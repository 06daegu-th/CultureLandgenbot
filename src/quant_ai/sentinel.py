"""자동 감시 (Sentinel) — 멈춘 것을 찾아 알린다. 5분마다 (스케줄러) + 감시견 프로세스에서도 (스케줄러가 죽어도 동작).

  데이터 수집 멈춤   수집 작업(news · disclosures · krx_data · investor_flow · macro)의 연속 실패 또는 오래 성공 없음
  AI 작업 멈춤       AI 판단(합의)이 2거래일 넘게 새로 안 생김 · AI 작업(decide · shadow) 연속 실패
  스케줄러 멈춤      심장박동 5분 넘게 없음 (감시견이 재시작도 한다)
  DB 오류           quick_check / SELECT 1 실패
  증권사 연결 끊김    KIS 호출 연속 실패 · 실시간(웹소켓) 끊김 (KIS 설정 시)
상태가 '문제'로 바뀌는 순간 알림(하루 1회), '정상'으로 돌아오면 복구 알림.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from . import ops
from .asof import label

DATA_JOBS = {"news": 3 * 3600, "disclosures": 2 * 3600, "krx_data": 30 * 3600, "investor_flow": 36 * 3600, "macro": 36 * 3600}
AI_JOBS = ("decide", "decide_and_trade", "ai_snapshot", "core_satellite")


def _job_state(s, job: str, now: datetime) -> dict:
    from .data.models import JobRun
    rows = s.execute(select(JobRun.ok, JobRun.started_at, JobRun.error).where(JobRun.job == job)
                     .order_by(JobRun.started_at.desc()).limit(5)).all()
    if not rows:
        return {"runs": 0}
    streak = 0
    for ok, _, _ in rows:
        if ok is False:
            streak += 1
        else:
            break
    last_ok = s.scalar(select(func.max(JobRun.started_at)).where(JobRun.job == job, JobRun.ok.is_(True)))
    return {"runs": len(rows), "fail_streak": streak, "last_ok": last_ok, "last_error": next((e for ok, _, e in rows if e), None)}


def check(app, now: datetime | None = None, store: bool = True) -> dict:
    from .data.db import session_scope
    from .data.models import ConsensusRecord
    from .recovery import db_check
    now = now or datetime.now(UTC)
    checks = []

    def add(key, title, status, detail):
        checks.append({"key": key, "title": title, "status": status, "detail": detail})

    db = db_check(app.engine)
    add("db", "DB", "ok" if db["ok"] else "bad", db["detail"])
    if not db["ok"]:
        return _finish(app, checks, now, store)
    with session_scope(app.engine) as s:
        # 데이터 수집
        bad, warn = [], []
        for job, max_age in DATA_JOBS.items():
            st = _job_state(s, job, now)
            if not st.get("runs"):
                continue
            last_ok = st.get("last_ok")
            age = (now - (last_ok if last_ok.tzinfo else last_ok.replace(tzinfo=UTC))).total_seconds() if last_ok else None
            if st["fail_streak"] >= 3:
                bad.append(f"{job} 연속 실패 {st['fail_streak']}회 ({(st.get('last_error') or '')[:60]})")
            elif age is None or age > max_age * 2:
                warn.append(f"{job} 마지막 성공 {label(last_ok) if last_ok else '없음'}")
        add("data", "데이터 수집", "bad" if bad else "warn" if warn else "ok", "; ".join(bad + warn)[:300] or "수집 작업 정상")
        # AI
        latest = s.scalar(select(func.max(ConsensusRecord.created_at)))
        ai_bad = [f"{j} 연속 실패 {st['fail_streak']}회" for j in AI_JOBS if (st := _job_state(s, j, now)).get("fail_streak", 0) >= 3]
    from .clock import KRX
    if latest is not None:
        latest = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
        lag = KRX.trading_days_between(latest.date(), now.date())
        if ai_bad or lag > 2:
            add("ai", "AI 작업", "bad", "; ".join(ai_bad + ([f"새 AI 판단 {lag}거래일째 없음 (마지막 {label(latest)})"] if lag > 2 else [])))
        else:
            add("ai", "AI 작업", "ok", f"마지막 AI 판단 {label(latest)}")
    else:
        add("ai", "AI 작업", "warn" if not ai_bad else "bad", "; ".join(ai_bad) or "AI 판단 기록 없음")
    hb = ops.get_state(app.engine, "heartbeat")
    if hb.get("at"):
        age = (now - datetime.fromisoformat(hb["at"])).total_seconds()
        add("scheduler", "스케줄러", "ok" if age < 300 else "bad", f"마지막 심장박동 {age / 60:.0f}분 전" + ("" if age < 300 else " — 멈춤 (감시견이 재시작)"))
    else:
        add("scheduler", "스케줄러", "warn", "심장박동 기록 없음 — ./run.sh 로 실행 중인지 확인")
    if app.settings.broker == "kis":
        h = ops.get_state(app.engine, "health:broker")
        ws = ops.get_state(app.engine, "kis_ws")
        fails = int(h.get("fails") or 0)
        ws_bad = ws.get("state") not in (None, "on", "off")
        add("broker", "증권사 연결", "bad" if fails >= 2 else "warn" if fails or ws_bad else "ok",
            f"연속 실패 {fails}회" + (f" · 실시간 {ws.get('state')}" if ws.get("state") else "") + (f" · {h.get('error')}" if h.get("error") else ""))
    else:
        add("broker", "증권사 연결", "na", "가상 체결 (KIS 미설정)")
    return _finish(app, checks, now, store)


def _finish(app, checks, now, store) -> dict:
    worst = max(({"ok": 0, "na": 0, "warn": 1, "bad": 2}[c["status"]] for c in checks), default=0)
    out = {"at": now.isoformat(), "as_of": label(now), "status": ["ok", "warn", "bad"][worst], "checks": checks}
    if store:
        from .alerts import push
        prev = {c["key"]: c["status"] for c in ops.get_state(app.engine, "sentinel").get("checks") or []}
        day = now.date().isoformat()
        for c in checks:
            was = prev.get(c["key"])
            if c["status"] == "bad" and was != "bad":
                push(app.engine, "ops", f"자동 감시: {c['title']} 문제", c["detail"][:200], level="bad",
                     link="#aihealth", dedupe=f"sentinel:{c['key']}:bad:{day}", now=now)
            elif was == "bad" and c["status"] == "ok":
                push(app.engine, "ops", f"자동 감시: {c['title']} 복구", c["detail"][:200], level="good",
                     link="#aihealth", dedupe=f"sentinel:{c['key']}:ok:{now.isoformat()[:13]}", now=now)
        ops.set_state(app.engine, "sentinel", out)
    return out


__all__ = ["check", "DATA_JOBS"]

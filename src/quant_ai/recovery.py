"""장애 복구 — 꺼졌다 켜져도, 작업이 멈춰도, 데이터 소스가 죽어도 스스로 원래 상태로.

1. 심장박동: 스케줄러가 1분마다 ops 'heartbeat' 기록 → 다음 시작 때 '얼마나 꺼져 있었나'를 안다
2. 시작 복구 (startup):
   · DB 무결성 빠른 점검 (SQLite quick_check)
   · 끝나지 않은 작업 기록(서버가 죽으며 남은 job_runs) → '중단됨'으로 정리
   · 꺼져 있던 동안 놓친 일 따라잡기: 결과 채점 → 장부 봉인 → (하루 이상이면) 평가·드리프트 → 매매 준비 점검
   · 가상 장부의 미완료 주문 정리는 매매 사이클이 먼저 한다 (pipeline.recover_orders)
   · 정전 이력 (최근 30회) 저장 · 5분 넘게 꺼졌으면 알림
3. 데이터 소스 상태: 소스별 최근 성공/실패 · 연속 실패 · 쿨다운 중인지 → 데이터 파이프라인 화면 · Readiness DATA
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from . import ops
from .data.db import session_scope
from .data.models import JobRun

log = logging.getLogger(__name__)
SOURCES = {  # 표시 이름 → (작업 이름들, health 키)
    "가격 (KRX/marcap)": (("krx_data", "prices", "collect"), None),
    "뉴스 (RSS)": (("news", "news_offhours"), None),
    "공시 (DART)": (("disclosures", "dart_summary"), None),
    "거시 (FRED)": (("macro",), None),
    "미국 주식": (("us_cycle", "us_sync"), None),
    "수급 (네이버)": (("investor_flow",), None),
    "증권사 (KIS)": ((), "broker"),
    "실시간 (KIS 웹소켓)": (("kis_ws",), None),
    "AI (LLM)": (("event_reanalyze", "ai_shadow", "news_agent"), None),
    "업종 (WICS/Yahoo)": (("sector_fill", "wics"), None),
}


def heartbeat(engine, now: datetime | None = None) -> None:
    ops.set_state(engine, "heartbeat", {"at": (now or datetime.now(UTC)).isoformat()})


def db_check(engine) -> dict:
    try:
        with engine.connect() as c:
            if engine.dialect.name == "sqlite":
                r = c.execute(text("PRAGMA quick_check")).scalar()
                return {"ok": r == "ok", "detail": str(r)}
            c.execute(text("SELECT 1"))
            return {"ok": True, "detail": "SELECT 1"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:120]}"}


def close_stale_jobs(engine, older_than: timedelta = timedelta(minutes=30), now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    n = 0
    with session_scope(engine) as s:
        for r in s.scalars(select(JobRun).where(JobRun.finished_at.is_(None))):
            st = r.started_at if r.started_at.tzinfo else r.started_at.replace(tzinfo=UTC)
            if now - st > older_than:
                r.finished_at, r.ok, r.error = now, False, "중단됨 (서버 재시작 · 끝나지 않은 작업 정리)"
                n += 1
    return n


def startup(app, now: datetime | None = None, catch_up: bool = True) -> dict:
    """서버·스케줄러 시작 때 한 번."""
    now = now or datetime.now(UTC)
    hb = ops.get_state(app.engine, "heartbeat")
    last = datetime.fromisoformat(hb["at"]) if hb.get("at") else None
    down = (now - last).total_seconds() if last else None
    out = {"at": now.isoformat(), "downtime_s": down, "db": db_check(app.engine),
           "stale_jobs": close_stale_jobs(app.engine, now=now), "catch_up": []}
    if catch_up and (down is None or down > 600):
        steps = [("결과 매칭", app.match_outcomes), ("복기 · 채점", lambda: app.review()), ("장부 봉인", app.ledger_anchor)]
        if down is None or down > 86400:
            steps += [("독립 평가", lambda: app.evaluation()), ("드리프트", lambda: app.drift())]
        for name, fn in steps:
            try:
                r = fn()
                out["catch_up"].append({"step": name, "ok": True, "result": str(r)[:80] if r is not None else None})
            except Exception as e:  # noqa: BLE001 - 따라잡기 실패가 시작을 막으면 안 됨
                out["catch_up"].append({"step": name, "ok": False, "error": f"{type(e).__name__}: {str(e)[:100]}"})
    hist = (ops.get_state(app.engine, "recovery").get("history") or [])[-29:]
    hist.append({"at": out["at"], "downtime_s": down, "db_ok": out["db"]["ok"], "stale_jobs": out["stale_jobs"]})
    ops.set_state(app.engine, "recovery", out | {"history": hist})
    heartbeat(app.engine, now)
    if (down or 0) > 300 or not out["db"]["ok"]:
        from .alerts import push
        push(app.engine, "ops", "재시작 복구" + (" · DB 점검 실패" if not out["db"]["ok"] else ""),
             f"꺼져 있던 시간 {down / 60:.0f}분 · 정리한 작업 {out['stale_jobs']}건 · 따라잡기 "
             + ", ".join(f"{c['step']}{'✓' if c['ok'] else '✗'}" for c in out["catch_up"]) if down else "첫 시작",
             level="warn" if out["db"]["ok"] else "critical", link="#server")
    return out


def source_health(app, now: datetime | None = None, days: int = 3) -> list[dict]:
    """소스별 최근 성공/실패 — 작업 기록(job_runs)과 health:* 상태에서."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    with session_scope(app.engine) as s:
        runs = s.execute(select(JobRun.job, JobRun.ok, JobRun.finished_at, JobRun.error)
                         .where(JobRun.started_at >= since).order_by(JobRun.id.desc())).all()
    by: dict[str, list] = {}
    for j, ok, fin, err in runs:
        by.setdefault(j, []).append((ok, fin, err))
    out = []
    for label, (jobs, hkey) in SOURCES.items():
        rs = [x for j in jobs for x in by.get(j, [])]
        rs.sort(key=lambda x: x[1] or now, reverse=True)
        streak = 0
        for ok, _, _ in rs:
            if ok is False:
                streak += 1
            else:
                break
        h = ops.get_state(app.engine, f"health:{hkey}") if hkey else {}
        if hkey and h:
            streak = int(h.get("fails") or 0)
        last_ok = next((f for ok, f, _ in rs if ok), None) or (datetime.fromisoformat(h["ok_at"]) if h.get("ok_at") else None)
        last_err = next((e for ok, _, e in rs if ok is False), None) or h.get("error")
        n = len(rs)
        status = ("none" if not rs and not h else "down" if streak >= 3 else "degraded" if streak >= 1 else "ok")
        out.append({"source": label, "status": status, "runs": n, "fails": sum(1 for x in rs if x[0] is False),
                    "streak": streak, "last_ok": last_ok.isoformat() if last_ok else None,
                    "last_error": (last_err or "")[:160] or None})
    return out


__all__ = ["heartbeat", "startup", "db_check", "close_stale_jobs", "source_health"]

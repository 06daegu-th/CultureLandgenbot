"""Trading Readiness Engine — 지금 새로 사도 되는 상태인가? 7개 관문을 한 번에 본다.

DATA · BROKER · MODEL · RISK · EVENT · DRIFT · CALIBRATION → 각각 green / yellow / red
  · red 가 하나라도 있으면 NOT READY → 실전·섀도 장부의 신규 매수를 막는다 (매도·위험 축소는 항상 허용)
  · yellow 는 CAUTION (매수 허용, 화면·알림으로 경고)
  · 전부 green 이면 READY
MODEL 은 '실제 시장 예측력' 증거를 본다: 실제 돈(KIS 실전)은 독립 평가기가 '검증됨' 이어야 green.
결과는 ops 상태 'readiness' 에 이력과 함께 저장되고, 상태가 바뀌면 알림이 간다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from . import ops
from .asof import freshness, label

ORDER = ("DATA", "BROKER", "MODEL", "RISK", "EVENT", "DRIFT", "CALIBRATION")
TITLE = {"DATA": "데이터", "BROKER": "증권사", "MODEL": "모델 · 예측력", "RISK": "리스크", "EVENT": "이벤트",
         "DRIFT": "드리프트", "CALIBRATION": "확률 보정"}
RANK = {"green": 0, "yellow": 1, "red": 2, "na": 0}


def _chk(key: str, status: str, detail: str, **extra) -> dict:
    return {"key": key, "title": TITLE[key], "status": status, "detail": detail, **extra}


def check_data(fr: dict) -> dict:
    it = fr["items"]
    bad, warn = [], []
    for k in ("bar_kr", "bar_index"):
        v = it.get(k)
        if not v or v["status"] == "none":
            bad.append(f"{(v or {}).get('name') or k} 없음")
        elif v["status"] == "old":
            bad.append(f"{v['name']} {v['age']}")
        elif v["status"] == "stale":
            warn.append(f"{v['name']} {v['age']}")
    q = it.get("quote") or {}
    if (fr.get("markets") or {}).get("KR") and q.get("status") in ("old", "stale"):
        warn.append(f"실시간 시세 {q.get('age')}")
    for k in ("news", "macro"):
        v = it.get(k) or {}
        if v.get("status") in ("old", "none"):
            warn.append(f"{v.get('name') or k} {v.get('age') or '없음'}")
    if bad:
        return _chk("DATA", "red", " · ".join(bad), items=bad + warn)
    if warn:
        return _chk("DATA", "yellow", " · ".join(warn[:3]), items=warn)
    b = it.get("bar_kr") or {}
    return _chk("DATA", "green", f"일봉 {b.get('label') or '-'} 기준 · 뉴스 {(it.get('news') or {}).get('age') or '-'}")


def check_broker(app, mode: str, now: datetime) -> dict:
    st = app.settings
    if not (mode == "live" and st.broker == "kis"):
        return _chk("BROKER", "green", "가상 체결 (증권사 연결 불필요)", na=True)
    h = ops.get_state(app.engine, "health:broker")
    v = ops.get_state(app.engine, "kis_validation")
    if int(h.get("fails") or 0) >= 2:
        return _chk("BROKER", "red", f"증권사 호출 연속 {h['fails']}회 실패: {h.get('error') or ''}"[:160])
    if not v:
        return _chk("BROKER", "yellow", "KIS 모의투자 검증 기록 없음 — 설정 → KIS 검증 실행")
    at = datetime.fromisoformat(v["at"]) if v.get("at") else None
    if not v.get("ok"):
        return _chk("BROKER", "red", f"KIS 검증 실패: {v.get('failed') or ''}"[:160])
    if at and now - at > timedelta(days=7):
        return _chk("BROKER", "yellow", f"KIS 검증이 {(now - at).days}일 전 — 다시 실행 권장")
    return _chk("BROKER", "green", f"KIS {v.get('env', '')} 검증 통과 ({label(at)})")


def check_model(app, mode: str, real_money: bool) -> dict:
    ev = ops.get_state(app.engine, "evaluation")
    rec, _ = app.active_model()
    if ev.get("status") in ("worse", "fail"):  # 예측이 기준선보다 나쁘거나 무결성이 깨졌으면 모델 유무와 상관없이 빨강
        return _chk("MODEL", "red", ev.get("verdict") or "", evidence=ev.get("status"))
    if rec is None:
        return _chk("MODEL", "red" if mode == "live" else "yellow", "챔피언 모델 없음 — 학습 필요 (후보가 검증 게이트를 통과하지 못함)")
    age = (datetime.now(UTC) - (rec.created_at if rec.created_at.tzinfo else rec.created_at.replace(tzinfo=UTC))).days
    decay = ops.get_state(app.engine, "model_decay")
    status = ev.get("status")
    evidence = ev.get("verdict") or "평가 기록 없음"
    if real_money and status != "pass":
        return _chk("MODEL", "red", f"실제 돈 매매는 예측력 검증 통과가 필요 — 현재: {evidence}", evidence=status)
    if status == "fail":
        return _chk("MODEL", "red", f"무결성 문제: {evidence}", evidence=status)
    if status == "worse":
        return _chk("MODEL", "red", evidence, evidence=status)
    if decay.get("status") == "decaying":
        return _chk("MODEL", "yellow", f"모델 노후 신호: {decay.get('message', '')}"[:160], evidence=status)
    if age > 90:
        return _chk("MODEL", "yellow", f"챔피언 {rec.version} 학습 {age}일 전 — 재학습 후보 확인", evidence=status)
    if status != "pass":
        return _chk("MODEL", "yellow", f"예측력 아직 증명 전 — {evidence}", evidence=status)
    return _chk("MODEL", "green", f"챔피언 {rec.version} · {evidence}", evidence=status)


def check_risk(app, mode: str, prisk: dict | None) -> dict:
    ks = ops.get_state(app.engine, "kill_switch")
    if ks.get("on"):
        return _chk("RISK", "red", f"{'HALTED' if ks.get('halt') else '킬스위치'}: {ks.get('reason') or ''}"[:160])
    if prisk is None:
        return _chk("RISK", "yellow", "포트폴리오 리스크 계산 불가")
    lim = (prisk.get("limits") or {}).get("max_var95", 0.04)
    if (prisk.get("var95") or 0) > lim:
        return _chk("RISK", "red", f"1일 VaR95 {prisk['var95']:.1%} > 한도 {lim:.0%}")
    w = prisk.get("warnings") or []
    if w:
        return _chk("RISK", "yellow", w[0][:160], items=w)
    if not prisk.get("n_positions"):
        return _chk("RISK", "green", "보유 없음")
    return _chk("RISK", "green", f"VaR95 {prisk.get('var95', 0):.1%} · 유효 종목 {(prisk.get('concentration') or {}).get('effective_n', '-')}")


def check_event(cal: dict | None, holdings: list[str]) -> dict:
    if not cal:
        return _chk("EVENT", "red", "이벤트 캘린더 없음 — 실적·FOMC 직전인지 알 수 없음 (fail-closed)")
    today = [e for e in cal.get("events", []) if e.get("d_day") == 0 and e["kind"] in ("fomc", "cpi", "nfp", "quad_witching", "bok")]
    near = [r for r in (cal.get("risk") or []) if r["symbol"] in holdings and r.get("buy_multiplier", 1) < 1]
    vk = cal.get("vkospi") or {}
    fear = [f"VKOSPI {vk.get('level')} — 최근 1년 상위 {100 - vk.get('percentile_1y', 0) * 100:.0f}% (공포 구간)"] if vk.get("fear") else []
    if today or near or fear:
        parts = [e["title"] for e in today] + [f"{r.get('name') or r['symbol']} {r.get('reason')}" for r in near] + fear
        return _chk("EVENT", "yellow", " · ".join(parts[:3]), items=parts)
    nxt = next((e for e in cal.get("events", []) if e.get("d_day", -1) >= 0 and e["importance"] >= 0.8), None)
    return _chk("EVENT", "green", "오늘 큰 이벤트 없음" + (f" · 다음: {nxt['title']} {nxt['d_label']}" if nxt else ""))


def check_drift(app) -> dict:
    d = ops.get_state(app.engine, "drift")
    if not d:
        return _chk("DRIFT", "yellow", "드리프트 점검 기록 없음")
    st = d.get("status")
    msg = d.get("message") or ""
    if st == "drift":
        return _chk("DRIFT", "red", msg[:160])
    if st in ("warn", "unknown"):
        return _chk("DRIFT", "yellow", msg[:160])
    return _chk("DRIFT", "green", msg[:160] or "학습 때와 비슷한 시장")


def check_calibration(app) -> dict:
    ev = ops.get_state(app.engine, "evaluation")
    b = ev.get("brier") or {}
    n = ev.get("n") or 0
    rel = b.get("reliability")
    if n < 50 or rel is None:
        return _chk("CALIBRATION", "yellow", f"채점된 예측 {n}건 — 보정 판단은 50건부터")
    if rel > 0.02:
        return _chk("CALIBRATION", "red", f"확률이 실제와 크게 어긋남 (신뢰도 항 {rel:.3f})")
    if rel > 0.01:
        return _chk("CALIBRATION", "yellow", f"확률 보정 오차 {rel:.3f} — 보정기 재학습 권장")
    return _chk("CALIBRATION", "green", f"확률 정직함 (신뢰도 항 {rel:.4f}, n={n})")


def evaluate(app, mode: str | None = None, now: datetime | None = None, prisk: dict | None = None,
             cal: dict | None = None, act: bool = True) -> dict:
    now = now or datetime.now(UTC)
    st = app.settings
    mode = mode or (st.mode.value if st.mode.value in ("paper", "shadow", "live") else "paper")
    real_money = mode == "live" and st.broker == "kis" and getattr(st, "kis_env", "demo") == "real"
    fr = freshness(app, now)
    if prisk is None:
        try:
            prisk = app.portfolio_risk(mode)
        except Exception as e:  # noqa: BLE001
            prisk = {"warnings": [f"계산 실패: {type(e).__name__}"]}
    cal = cal if cal is not None else ops.get_state(app.engine, "event_calendar")
    try:
        holdings = list(app.load_portfolio(mode).positions)
    except Exception:  # noqa: BLE001
        holdings = []
    checks = [check_data(fr), check_broker(app, mode, now), check_model(app, mode, real_money), check_risk(app, mode, prisk),
              check_event(cal, holdings), check_drift(app), check_calibration(app)]
    truth_bad = escalate_from_truth(app, mode, now, checks)
    worst = max(RANK[c["status"]] for c in checks)
    status = ["READY", "CAUTION", "NOT_READY"][worst]
    reds = [c for c in checks if c["status"] == "red"]
    out = {"status": status, "mode": mode, "real_money": real_money, "at": now.isoformat(), "as_of": label(now),
           "checks": checks, "blockers": [f"{c['title']}: {c['detail']}" for c in reds],
           "gate": gate_applies(st, mode), "freshness": fr, "truth_bad": truth_bad}
    if act:
        prev = ops.get_state(app.engine, "readiness")
        hist = (prev.get("history") or [])[-47:]
        hist.append({"at": now.isoformat(), "status": status, "reds": [c["key"] for c in reds]})
        ops.set_state(app.engine, "readiness", {k: v for k, v in out.items() if k != "freshness"} | {"history": hist})
        if prev.get("status") and prev.get("status") != status:
            from .alerts import push
            body = "; ".join(out["blockers"][:3]) or ("모든 관문 통과" if status == "READY" else "주의 항목 있음")
            push(app.engine, "readiness", f"매매 준비 상태: {prev['status']} → {status}", body,
                 level="critical" if status == "NOT_READY" else "info", link="#control")
    return out


TRUTH_TO_GATE = {"clock": "EVENT", "data": "DATA", "event": "EVENT", "broker": "BROKER", "risk": "RISK", "execution": "BROKER"}


def escalate_from_truth(app, mode: str, now: datetime, checks: list[dict]) -> list[str]:
    """Truth Center 의 bad 항목 → 해당 관문을 빨강으로 (예: 휴장일 캘린더 없음 → EVENT · 미확정 주문 → BROKER)."""
    from . import truth
    try:
        rep = truth.report(app, mode, now)
    except Exception as e:  # noqa: BLE001 - 사실 확인 자체가 실패하면 fail-closed
        c = next(x for x in checks if x["key"] == "DATA")
        c.update(status="red", detail=f"Truth Center 점검 실패 ({type(e).__name__}) — fail-closed")
        return [c["detail"]]
    bad = []
    by = {c["key"]: c for c in checks}
    for sec in rep["sections"]:
        for c in sec["checks"]:
            if c["status"] != "bad":
                continue
            g = by[TRUTH_TO_GATE[sec["key"]]]
            msg = f"{sec['title']} · {c['label']}: {c['detail']}"
            bad.append(msg)
            if g["status"] != "red":
                g.update(status="red", detail=msg[:200])
            else:
                g.setdefault("items", []).append(msg[:200])
    return bad


def gate_applies(settings, mode: str) -> bool:
    """QUANT_READINESS_GATE: live(기본 — live·shadow 장부) · all · off."""
    g = getattr(settings, "readiness_gate", "live")
    return g == "all" or (g == "live" and mode in ("live", "shadow"))


def buy_block(app, mode: str, max_age: timedelta = timedelta(hours=2)) -> str | None:
    """RiskEngine.buy_block 용 (fail-closed).

    게이트가 적용되는 장부(live·shadow 기본)에서: 최근 점검이 없거나 오래됐거나 다른 장부 기준이면 지금 다시 점검하고,
    점검 자체가 실패하면 매수를 막는다. NOT READY 면 사유를 돌려준다."""
    if not gate_applies(app.settings, mode):
        return None
    r = ops.get_state(app.engine, "readiness")
    fresh = r.get("at") and datetime.now(UTC) - datetime.fromisoformat(r["at"]) < max_age and r.get("mode") == mode
    if not fresh:
        try:
            r = evaluate(app, mode)
        except Exception as e:  # noqa: BLE001
            return f"매매 준비 점검 실패 ({type(e).__name__}) — fail-closed: 신규 매수 중단"
    if r.get("status") == "NOT_READY":
        return "NOT READY — " + "; ".join(r.get("blockers") or [])[:200]
    return None


__all__ = ["evaluate", "buy_block", "gate_applies", "ORDER", "TITLE"]

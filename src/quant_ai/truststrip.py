"""v40 홈 신뢰 상태줄 — 카드를 더 늘리지 않고, 매매 전에 꼭 알아야 할 다섯 가지를 한 줄로.

증권사 연결 · 시세 신선도 · 매수 막힘 이유 · 장부와 계좌 일치 · AI 신뢰. 자세한 내용은 접어 둔다.
네트워크를 부르지 않는다 (저장된 상태만 읽는다).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from . import ops

STALE_MIN = 15


def _age_min(ts, now) -> float | None:
    if not ts:
        return None
    try:
        t = pd.Timestamp(ts)
    except (ValueError, TypeError):
        return None
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return (pd.Timestamp(now) - t).total_seconds() / 60


def strip(app, now: datetime | None = None) -> dict:
    from .explain import _market_open
    from .pricenow import quote_ts
    now = now or datetime.now(UTC)
    st = app.settings
    items = []

    # 1 증권사
    broker = getattr(st, "broker", "none")
    if broker == "kis":
        v = ops.get_state(app.engine, "kis_validation") or {}
        lv = "ok" if v.get("ok") else "warn" if not v else "bad"
        items.append({"key": "broker", "label": "증권사", "level": lv,
                      "text": f"한국투자증권 · {'모의' if st.kis_env == 'demo' else '실전'}" + ("" if v.get("ok") else " · 검증 필요"),
                      "detail": v.get("message") or ("마지막 점검 통과" if v.get("ok") else "kis-check --suite 로 점검하세요"), "href": "#execution"})
    elif broker == "kb":
        v = ops.get_state(app.engine, "kb_validation") or {}
        items.append({"key": "broker", "label": "증권사", "level": "ok" if v.get("ok") else "warn",
                      "text": "KB증권 · 읽기 전용", "detail": "시세만 연결 — 잔고·주문 TR 은 공식 가이드 확인 전이라 막혀 있어요", "href": "#execution"})
    else:
        items.append({"key": "broker", "label": "증권사", "level": "warn", "text": "연결 안 됨 · 가상매매",
                      "detail": "실제 계좌 없이 모의 장부로만 움직여요", "href": "#execution"})

    # 2 시세 신선도 (장중에만 의미가 있다)
    lq = ops.get_state(app.engine, "live_quotes") or {}
    kr_open, us_open = _market_open("005930", now), _market_open("AAPL", now)
    if kr_open or us_open:
        ages = [a for sym, q in lq.items() if isinstance(q, dict) and (sym[:1].isdigit() == kr_open or (not sym[:1].isdigit()) == us_open)
                for a in [_age_min(quote_ts(q), now)] if a is not None]
        newest = min(ages) if ages else None
        lv = "bad" if newest is None or newest > STALE_MIN else "ok"
        items.append({"key": "quotes", "label": "시세", "level": lv,
                      "text": "실시간 없음" if newest is None else f"{newest:.0f}분 전",
                      "detail": f"장중 시세가 {STALE_MIN}분 넘게 늦으면 새 매수를 막아요" if lv == "bad" else "장중 시세가 들어오고 있어요", "href": "#datahealth"})
    else:
        items.append({"key": "quotes", "label": "시세", "level": "ok", "text": "장 마감 · 종가 기준", "detail": "지금은 정규장이 아니에요", "href": "#datahealth"})

    # 3 매수 막힘
    why = []
    if app.kill_switch_on():
        why.append("긴급 정지(킬스위치) 켜짐")
    rd = ops.get_state(app.engine, "readiness") or {}
    why += list(rd.get("blockers") or [])[:3]
    if broker == "kb" and st.mode.value == "live":
        why.append("KB 주문 경로 없음 (읽기 전용)")
    items.append({"key": "buy", "label": "새 매수", "level": "bad" if why else "ok", "text": "막힘" if why else "가능",
                  "detail": " · ".join(why) if why else ("매매 준비 점검 통과" if rd else "매매 준비 점검 기록 없음 — 가상매매 기준"), "href": "#readiness"})

    # 4 장부 ↔ 계좌
    rc = ops.get_state(app.engine, "reconcile") or {}
    if broker == "kis" and rc:
        age = _age_min(rc.get("at"), now)
        lv = "bad" if rc.get("drift_n") else "warn" if age is None or age > 60 * 26 else "ok"
        items.append({"key": "ledger", "label": "장부", "level": lv,
                      "text": f"불일치 {rc['drift_n']}종목" if rc.get("drift_n") else "계좌와 일치",
                      "detail": "증권사 잔고를 기준으로 맞춰요 — 연속 불일치면 매수를 멈춰요" + (f" · {age / 60:.0f}시간 전 확인" if age else ""), "href": "#pos"})
    elif broker == "kb":
        items.append({"key": "ledger", "label": "장부", "level": "warn", "text": "대조 불가",
                      "detail": "KB 잔고 조회 TR 이 공식 샘플에 없어 계좌 대조를 못 해요 — 실계좌 매수는 막혀 있어요", "href": "#pos"})
    else:
        items.append({"key": "ledger", "label": "장부", "level": "ok", "text": "가상 장부", "detail": "실계좌가 없어 대조할 것이 없어요", "href": "#pos"})

    # 5 AI 신뢰
    try:
        from .center import ai_state
        a = ai_state(app)
        lv = {"verified": "ok", "banned": "bad"}.get(a.get("key"), "warn")
        items.append({"key": "ai", "label": "AI", "level": lv, "text": a.get("label") or "-",
                      "detail": "검증 전에는 섀도 모드 — 판단은 기록·채점만 하고 주문에는 쓰지 않아요" if lv != "ok" else "성적 기준을 통과했어요",
                      "href": "#aitrust"})
    except Exception:  # noqa: BLE001
        items.append({"key": "ai", "label": "AI", "level": "warn", "text": "확인 못 함", "detail": "AI 성적을 읽지 못했어요", "href": "#aitrust"})
    worst = max(({"ok": 0, "warn": 1, "bad": 2}[i["level"]] for i in items), default=0)
    return {"at": now.isoformat(), "items": items, "level": ["ok", "warn", "bad"][worst],
            "headline": "새 매수 막힘 — " + next(i["detail"] for i in items if i["key"] == "buy") if any(i["key"] == "buy" and i["level"] == "bad" for i in items)
            else "매매 전 점검 이상 없음" if worst == 0 else "확인할 것이 있어요"}


__all__ = ["strip"]

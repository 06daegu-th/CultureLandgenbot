"""왜 BUY · 왜 SELL · 왜 NO TRADE — 판단 하나를 사람 말로 풀어 쓴다.

같은 기록(봉인된 합의 판단)만 쓴다 — 설명을 위해 새로 계산하지 않는다 (나중에 봐도 같은 설명).
  · 한 줄 결론: "BUY — 상승 확률 64%, 신뢰도 61 이 매수 기준(58% · 55)을 넘었다"
  · 찬성 근거 / 반대 근거: AI 별 확률·가중치에서 방향이 같은 것 / 반대인 것 (영향 큰 순)
  · 막은 것: Risk AI 거부권 · 의견 충돌 · 응답 AI 부족 · 확률·신뢰도 미달 · 매매 준비(NOT READY) · 이벤트(실적 D-1) · 포트폴리오 한도
  · 무엇이 바뀌면 판단이 바뀌나: 매수/매도 기준까지 남은 확률·신뢰도 · 무효화 가격
"""

from __future__ import annotations

from .ensemble.engine import EnsembleConfig

CODE_KO = {"veto": "Risk AI 거부권", "few_responders": "방향 의견을 낸 AI 부족", "high_conflict": "AI 간 의견 충돌 높음",
           "weak_signal": "확률·신뢰도가 매매 기준 미달"}
LABELS = {"primary": "뉴스 AI", "nvidia": "경제·시장 AI", "panel": "공시·실적 AI", "quant": "차트·Quant AI", "regime": "시장 국면",
          "risk": "Risk AI", "challenger": "도전자 모델"}


def explain(payload: dict, action: str, prob_up: float, confidence: float, gates: dict | None = None,
            cfg: EnsembleConfig | None = None) -> dict:
    cfg = cfg or EnsembleConfig()
    p = payload or {}
    contribs = p.get("contributions") or []
    up = prob_up >= 0.5
    for_, against = [], []
    for c in contribs:
        pu = c.get("prob_up")
        if pu is None:
            continue
        w = float(c.get("weight") or 0)
        infl = w * abs(pu - 0.5)
        item = {"ai": LABELS.get(c.get("analyst"), c.get("analyst")), "prob_up": round(pu, 3), "weight": round(w, 3),
                "influence": round(infl, 4), "summary": (c.get("summary") or "")[:140],
                "track": None if c.get("accuracy") is None else f"과거 적중 {c['accuracy']:.0%} (n={c.get('n_scored')})"}
        (for_ if (pu >= 0.5) == up else against).append(item)
    for_.sort(key=lambda x: -x["influence"])
    against.sort(key=lambda x: -x["influence"])
    blocks = [CODE_KO.get(k, k) for k in p.get("no_trade_codes") or []]
    blocks += [f"거부권 — {v}" for v in p.get("vetoes") or []]
    g = gates or {}
    if g.get("readiness"):
        blocks.append(f"매매 준비: {g['readiness']}")
    if g.get("event"):
        blocks.append(f"이벤트: {g['event']}")
    plan = p.get("plan") or {}
    to_buy = max(cfg.buy_prob - prob_up, 0.0)
    to_sell = max(prob_up - cfg.sell_prob, 0.0)
    conf_gap = max(cfg.min_confidence - confidence, 0.0)
    if action == "BUY":
        head = f"BUY — 상승 확률 {prob_up:.0%} · 신뢰도 {confidence:.0f} 가 매수 기준({cfg.buy_prob:.0%} · {cfg.min_confidence:.0f})을 넘었다"
    elif action == "SELL":
        head = f"SELL — 상승 확률 {prob_up:.0%} 가 매도 기준({cfg.sell_prob:.0%}) 아래 · 신뢰도 {confidence:.0f}"
    elif action == "NO_TRADE":
        head = "NO TRADE — " + (blocks[0] if blocks else "진입 보류")
    else:
        head = f"HOLD — 상승 확률 {prob_up:.0%} · 신뢰도 {confidence:.0f}: 매수({cfg.buy_prob:.0%})·매도({cfg.sell_prob:.0%}) 기준 사이"
    change = []

    def need(prob_part: str | None, extra: list[str]) -> str:
        parts = ([prob_part] if prob_part else []) + ([f"신뢰도 +{conf_gap:.0f}"] if conf_gap else []) + extra
        return " · ".join(parts) or "기준 충족 — 다른 차단 사유 해소"

    if action != "BUY":
        change.append("BUY 가 되려면: " + need(f"상승 확률 +{to_buy * 100:.1f}%p" if to_buy > 0 else None,
                                              (["거부권 해제"] if p.get("vetoes") else []) + (["의견 충돌 완화"] if p.get("conflict") == "high" else [])))
    if action != "SELL":
        change.append("SELL 이 되려면: " + need(f"상승 확률 −{to_sell * 100:.1f}%p" if to_sell > 0 else None, []))
    if plan.get("stop"):
        change.append(f"판단이 틀렸다고 인정하는 가격: {plan['stop']:,.2f} ({plan.get('stop_pct', 0):+.1%})")
    return {"action": action, "headline": head, "for": for_[:5], "against": against[:5], "blocks": blocks,
            "reasons": (p.get("reasons") or [])[:6], "risks": (p.get("risks") or [])[:6], "what_changes": change,
            "thresholds": {"buy_prob": cfg.buy_prob, "sell_prob": cfg.sell_prob, "min_confidence": cfg.min_confidence},
            "conflict": p.get("conflict"), "prob_raw": p.get("prob_raw")}


def for_symbol(app, symbol: str) -> dict | None:
    from . import ops
    from .asof import label
    from .pipeline import latest_consensus
    rec = latest_consensus(app.engine, symbol)
    if rec is None:
        return None
    gates = {}
    try:
        from .readiness import gate_applies
        rd = ops.get_state(app.engine, "readiness")
        if rd.get("status") == "NOT_READY" and gate_applies(app.settings, rd.get("mode") or "paper"):
            gates["readiness"] = "NOT READY (" + ", ".join(c["key"] for c in rd.get("checks") or [] if c["status"] == "red") + ")"
        r = next((x for x in ops.get_state(app.engine, "event_calendar").get("risk") or [] if x["symbol"] == symbol), None)
        if r and r.get("buy_multiplier", 1) < 1:
            gates["event"] = f"{r.get('reason')} → 매수 ×{r['buy_multiplier']}"
    except Exception:  # noqa: BLE001, S110 - 설명 보조 정보라 실패해도 본 설명은 보인다
        pass
    out = explain(rec.payload or {}, rec.action, rec.prob_up, rec.confidence, gates)
    return out | {"id": rec.id, "as_of": label(rec.as_of), "sealed": bool(rec.row_hash)}


__all__ = ["explain", "for_symbol"]

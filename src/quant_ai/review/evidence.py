"""Evidence Chain · AI Trading Journal.

Evidence Chain — 한 번의 판단을 끝까지 추적한다:
    뉴스 이벤트 → 공시 → 가격·지표 → 시장 상태 → AI 별 의견(근거·보정 전후 확률) → 앙상블(가중치·충돌)
    → 리스크 게이트(거부권·NO TRADE 사유) → 주문(멱등 키·거부 사유) → 체결(슬리피지) → 실제 결과 → 오답 원인

AI Journal — 판단 목록을 '왜 샀나 → 무엇을 예상했나 → 실제로 어떻게 됐나 → 왜 틀렸나' 로 보여준다.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.models import AnalystOpinionRecord, ConsensusRecord, FillRecord, Instrument, OrderRecord
from ..ensemble.tracker import CATEGORY_LABELS, provider_of
from .review import classify_miss

ROLE_LABELS = {"primary": "Primary AI", "nvidia": "Second AI", "panel": "Panel AI", "quant": "Quant Model",
               "regime": "Market Regime", "risk": "Risk AI", "challenger": "Challenger"}
NO_TRADE_LABELS = {"veto": "리스크 거부권", "few_responders": "의견 낸 AI 부족", "high_conflict": "AI 간 충돌 높음",
                   "weak_signal": "신호 약함 (확률·신뢰도 미달)"}


def _slip_bps(side: str, ref: float | None, avg: float | None) -> float | None:
    if not ref or not avg:
        return None
    return round((avg - ref) / ref * 1e4 * (1 if side == "buy" else -1), 2)


def evidence_chain(session: Session, consensus_id: int) -> dict | None:
    c = session.get(ConsensusRecord, consensus_id)
    if c is None:
        return None
    p = c.payload or {}
    ev = p.get("evidence") or {}
    inst = session.scalar(select(Instrument).where(Instrument.symbol == c.symbol))
    ops = session.scalars(select(AnalystOpinionRecord).where(AnalystOpinionRecord.consensus_id == c.id)
                          .order_by(AnalystOpinionRecord.id)).all()
    by_ai: dict[str, dict] = {}
    for o in ops:
        pl = o.payload or {}
        a = by_ai.setdefault(o.analyst, {
            "analyst": o.analyst, "label": ROLE_LABELS.get(o.analyst, o.analyst),
            "provider": provider_of(pl.get("backend"), pl.get("provider")), "model": pl.get("backend"),
            "summary": pl.get("summary"), "reasons": pl.get("reasons") or [], "risks": pl.get("risks") or [],
            "veto": o.veto, "veto_reason": pl.get("veto_reason"), "error": pl.get("error"), "scores": {}})
        a["scores"][o.category] = {"label": CATEGORY_LABELS.get(o.category, o.category),
                                   "prob_up": o.prob_up, "prob_cal": pl.get("prob_cal"),
                                   "correct": o.correct, "realized": o.realized_return}
    weights = {x["analyst"]: x for x in p.get("contributions", [])}
    for name, w in weights.items():  # 방향 의견 없이 규칙만 본 AI(예: 거부권 없는 Risk AI)도 사슬에 표시
        if name not in by_ai:
            by_ai[name] = {"analyst": name, "label": ROLE_LABELS.get(name, name),
                           "provider": provider_of(w.get("backend")), "model": w.get("backend"),
                           "summary": w.get("summary"), "reasons": [], "risks": [], "veto": w.get("veto"),
                           "veto_reason": None, "error": None, "scores": {}}
    for a in by_ai.values():
        w = weights.get(a["analyst"], {})
        a["weight"], a["track_accuracy"], a["n_scored"] = w.get("weight"), w.get("accuracy"), w.get("n_scored")
    orders = []
    for r in session.scalars(select(OrderRecord).where(OrderRecord.consensus_id == c.id).order_by(OrderRecord.id)):
        fills = session.scalars(select(FillRecord).where(FillRecord.order_id == r.id)).all()
        orders.append({"mode": r.mode, "ts": r.created_at.isoformat() if r.created_at else None, "side": r.side,
                       "qty": r.qty, "status": r.status, "reason": r.reason, "client_order_id": r.client_order_id,
                       "broker_order_id": r.broker_order_id, "ref_price": r.ref_price, "avg_price": r.avg_price,
                       "slippage_bps": _slip_bps(r.side, r.ref_price, r.avg_price),
                       "fills": [{"qty": f.qty, "price": f.price, "fee": f.fee} for f in fills]})
    outcome = {"realized_return": c.realized_return, "correct": c.correct, "horizon": p.get("horizon"),
               "cause": classify_miss(c) if c.correct is False else None}
    chain = [
        {"stage": "news", "title": "뉴스 이벤트", "items": ev.get("news", [])},
        {"stage": "disclosure", "title": "공시", "items": ev.get("disclosures", [])},
        {"stage": "price", "title": "가격 · 지표", "items": ev.get("price", {})},
        {"stage": "market", "title": "시장 상태 · 크로스에셋",
         "items": {"market": ev.get("market"), "regime": ev.get("regime"), "cross_asset": ev.get("cross_asset", []),
                   "macro": ev.get("macro"), "events": ev.get("events", [])}},
        {"stage": "ai", "title": "AI 의견 (서로 모른 채 독립 판단)", "items": list(by_ai.values())},
        {"stage": "ensemble", "title": "앙상블 합의",
         "items": {"action": c.action, "prob_up": c.prob_up, "confidence": c.confidence, "conflict": c.conflict,
                   "reasons": p.get("reasons", []), "risks": p.get("risks", [])}},
        {"stage": "risk", "title": "리스크 게이트",
         "items": {"vetoes": p.get("vetoes", []), "explanation": p.get("explanation", []),
                   "no_trade": [NO_TRADE_LABELS.get(x, x) for x in p.get("no_trade_codes", [])],
                   "data_quality": ev.get("data_quality", {})}},
        {"stage": "order", "title": "주문 · 체결", "items": orders},
        {"stage": "outcome", "title": "실제 결과", "items": outcome},
    ]
    return {"id": c.id, "symbol": c.symbol, "name": inst.name if inst else c.symbol, "as_of": c.as_of.isoformat(),
            "action": c.action, "prob_up": c.prob_up, "confidence": c.confidence, "conflict": c.conflict,
            "regime": p.get("regime"), "chain": chain}


def ai_journal(session: Session, limit: int = 50, symbol: str | None = None, actions: tuple[str, ...] | None = None,
               only_resolved: bool = False) -> list[dict]:
    """판단 저널: 왜 → 예상 → 실제 → 왜 틀렸나."""
    q = select(ConsensusRecord).order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()).limit(limit)
    if symbol:
        q = q.where(ConsensusRecord.symbol == symbol)
    if actions:
        q = q.where(ConsensusRecord.action.in_(actions))
    if only_resolved:
        q = q.where(ConsensusRecord.correct.is_not(None))
    names = {i.symbol: i.name for i in session.scalars(select(Instrument))}
    traded = {r.consensus_id: r.status for r in session.scalars(
        select(OrderRecord).where(OrderRecord.consensus_id.is_not(None)))}
    out = []
    for c in session.scalars(q):
        p = c.payload or {}
        top = sorted(p.get("contributions", []), key=lambda x: -(x.get("weight") or 0))[:3]
        why = [f"{ROLE_LABELS.get(x['analyst'], x['analyst'])} {x['prob_up']:.0%}" for x in top
               if x.get("prob_up") is not None]
        h = p.get("horizon", 5)
        expected = (f"{h}거래일 뒤 상승 확률 {c.prob_up:.0%}" if c.action in ("BUY", "HOLD")
                    else f"{h}거래일 뒤 하락 확률 {1 - c.prob_up:.0%}" if c.action == "SELL"
                    else f"진입 보류 ({', '.join(NO_TRADE_LABELS.get(x, x) for x in p.get('no_trade_codes', [])) or '사유 없음'})")
        out.append({
            "id": c.id, "symbol": c.symbol, "name": names.get(c.symbol, c.symbol), "as_of": c.as_of.isoformat(),
            "action": c.action, "prob_up": round(c.prob_up, 4), "confidence": c.confidence, "conflict": c.conflict,
            "why": why + (p.get("reasons") or [])[:2], "expected": expected,
            "actual": None if c.realized_return is None else round(c.realized_return, 4),
            "correct": c.correct, "cause": classify_miss(c) if c.correct is False else None,
            "order_status": traded.get(c.id),
            "no_trade": [NO_TRADE_LABELS.get(x, x) for x in p.get("no_trade_codes", [])],
        })
    return out


def no_trade_value(session: Session, since=None) -> list[dict]:
    """NO TRADE / HOLD 판정을 사유별로: 실제로 손실을 피했나 (진입했다면 평균 수익률)."""
    q = select(ConsensusRecord).where(ConsensusRecord.correct.is_not(None),
                                      ConsensusRecord.action.in_(("NO_TRADE", "HOLD")))
    if since is not None:
        q = q.where(ConsensusRecord.as_of >= since)
    agg: dict[str, list[float]] = {}
    for c in session.scalars(q):
        for code in (c.payload or {}).get("no_trade_codes", []) or ["unknown"]:
            agg.setdefault(code, []).append(c.realized_return or 0.0)
    return [{"code": k, "label": NO_TRADE_LABELS.get(k, k), "n": len(v),
             "avoided_loss_rate": round(sum(r < 0 for r in v) / len(v), 4),
             "avg_return_if_entered": round(sum(v) / len(v), 5)} for k, v in sorted(agg.items())]


__all__ = ["evidence_chain", "ai_journal", "no_trade_value"]

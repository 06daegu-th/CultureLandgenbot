"""내 투자 저널 vs AI — 내 판단도 AI 판단과 똑같이 봉인하고 채점한다.

기록: 종목 · BUY/SELL/HOLD · 확신(1~5) · 기간(거래일) · 이유 → 저장 순간 해시 봉인 (나중에 고쳐도 드러남)
채점: 기록 다음 거래일 시가 진입 → 기간 뒤 종가 (AI 와 같은 규칙, outcomes.forward)
비교 (같은 종목 · ±2일 안의 AI 합의 판단과 짝지어)
  · 적중률: 나 vs AI · 서로 의견이 다를 때 누가 맞았나
  · 확신도 보정: 확신 5 일 때 정말 더 잘 맞나 (아니면 과신)
  · 편향: 내가 자주 틀리는 패턴 (급등 후 추격 · 하락 중 물타기 …)
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select

from ..data.models import ConsensusRecord, UserJournal
from .outcomes import forward

ACTIONS = ("BUY", "SELL", "HOLD")


def _iso(t: datetime | None) -> str | None:
    """DB 왕복 뒤에도 같은 문자열 (SQLite 는 시간대를 버린다 → UTC 로 맞춘다)."""
    if t is None:
        return None
    t = t if t.tzinfo else t.replace(tzinfo=UTC)
    return t.astimezone(UTC).isoformat()


def _hash(r: UserJournal) -> str:
    c = {"symbol": r.symbol, "action": r.action, "conviction": r.conviction, "horizon": r.horizon,
         "created_at": _iso(r.created_at), "ref_price": r.ref_price, "reason": r.reason}
    return hashlib.sha256(json.dumps(c, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def add(session, symbol: str, action: str, conviction: int = 3, horizon: int = 5, reason: str | None = None,
        ref_price: float | None = None, tags: dict | None = None, now: datetime | None = None) -> dict:
    action = action.upper()
    if action not in ACTIONS:
        raise ValueError("action 은 BUY / SELL / HOLD")
    conviction = int(max(1, min(5, conviction)))
    horizon = int(max(1, min(60, horizon)))
    r = UserJournal(created_at=now or datetime.now(UTC), symbol=symbol, action=action, conviction=conviction,
                    horizon=horizon, ref_price=ref_price, reason=(reason or "")[:1000] or None, tags=tags)
    r.row_hash = _hash(r)
    session.add(r)
    session.flush()
    return {"id": r.id, "hash": r.row_hash}


def score(session, bars: dict) -> int:
    n = 0
    for r in session.scalars(select(UserJournal).where(UserJournal.realized_return.is_(None))):
        b = bars.get(r.symbol)
        if b is None:
            continue
        v = forward(b, r.created_at, r.horizon)
        if v is None:
            continue
        r.realized_return = float(v)
        r.correct = None if r.action == "HOLD" else (v > 0) == (r.action == "BUY")
        n += 1
    return n


def compare(session, days: int | None = None, limit: int = 2000) -> dict:
    q = select(UserJournal).order_by(UserJournal.id.desc()).limit(limit)
    if days:
        q = q.where(UserJournal.created_at >= datetime.now(UTC) - timedelta(days=days))
    mine = session.scalars(q).all()
    tampered = [r.id for r in mine if r.row_hash and r.row_hash != _hash(r)]
    scored = [r for r in mine if r.correct is not None]
    pairs = []
    for r in scored:
        t0 = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)
        ai = session.scalars(select(ConsensusRecord).where(
            ConsensusRecord.symbol == r.symbol, ConsensusRecord.as_of >= t0 - timedelta(days=2),
            ConsensusRecord.as_of <= t0 + timedelta(days=2), ConsensusRecord.realized_return.is_not(None))
            .order_by(ConsensusRecord.id.desc())).first()
        if ai is None:
            continue
        ai_dir = "BUY" if ai.prob_up >= 0.5 else "SELL"
        pairs.append({"id": r.id, "symbol": r.symbol, "me": r.action, "ai": ai_dir, "ai_prob": round(ai.prob_up, 3),
                      "agree": ai_dir == r.action, "me_hit": bool(r.correct),
                      "ai_hit": (ai.prob_up >= 0.5) == ((r.realized_return or 0) > 0), "ret": r.realized_return})
    dis = [p for p in pairs if not p["agree"]]
    conv = defaultdict(list)
    for r in scored:
        conv[r.conviction].append(bool(r.correct))
    conv_rows = [{"conviction": k, "n": len(v), "hit_rate": round(float(np.mean(v)), 3)} for k, v in sorted(conv.items())]
    hi = [x for r in scored if r.conviction >= 4 for x in [bool(r.correct)]]
    lo = [x for r in scored if r.conviction <= 2 for x in [bool(r.correct)]]
    bias = []
    if len(hi) >= 5 and len(lo) >= 5 and np.mean(hi) <= np.mean(lo):
        bias.append("과신: 확신이 높을 때 오히려 덜 맞음")
    buys = [r for r in scored if r.action == "BUY"]
    if len(buys) >= 8 and np.mean([bool(r.correct) for r in buys]) < 0.45:
        bias.append("매수 판단의 적중률이 45% 미만 — 진입 타이밍 점검")
    return {
        "n": len(mine), "scored": len(scored), "tampered": tampered,
        "me": {"hit_rate": round(float(np.mean([bool(r.correct) for r in scored])), 3) if scored else None,
               "avg_ret_signed": round(float(np.mean([(r.realized_return or 0) * (1 if r.action == "BUY" else -1) for r in scored])), 5) if scored else None},
        "pairs": {"n": len(pairs), "agree_rate": round(float(np.mean([p["agree"] for p in pairs])), 3) if pairs else None,
                  "me_hit": round(float(np.mean([p["me_hit"] for p in pairs])), 3) if pairs else None,
                  "ai_hit": round(float(np.mean([p["ai_hit"] for p in pairs])), 3) if pairs else None},
        "disagree": {"n": len(dis), "me_right": sum(p["me_hit"] for p in dis), "ai_right": sum(p["ai_hit"] for p in dis),
                     "examples": dis[:10]},
        "by_conviction": conv_rows, "biases": bias,
        "rows": [{"id": r.id, "at": r.created_at.isoformat(), "symbol": r.symbol, "action": r.action, "conviction": r.conviction,
                  "horizon": r.horizon, "reason": r.reason, "ref_price": r.ref_price, "ret": r.realized_return,
                  "correct": r.correct, "sealed": bool(r.row_hash)} for r in mine[:100]],
    }


__all__ = ["add", "score", "compare", "ACTIONS"]

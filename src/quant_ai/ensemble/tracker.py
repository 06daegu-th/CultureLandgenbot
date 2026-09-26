"""AI 성적표: 각 AI 의 의견을 저장하고, horizon 이 지나면 실제 결과로 채점한다.

카테고리별로 따로 채점한다.
- direction : prob_up 방향 vs 종목 실제 수익률
- news      : 뉴스 영향 부호 vs 종목 실제 수익률 (뉴스가 있던 경우만)
- macro     : 거시 영향 부호 vs 벤치마크(시장) 실제 수익률
- trend     : 추세 점수 부호 vs 종목 실제 수익률
- risk      : 경고(severity ≥ 0.3 또는 veto)를 낸 경우만, 실제로 하락/급변했는지
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysts.base import DIRECTION, MACRO, NEWS, RISK, TREND, Opinion
from ..data.models import AnalystOpinionRecord, ConsensusRecord
from ..engines.features import forward_return
from .engine import ConsensusSignal, TrackRecord

MIN_SIGNAL = 0.05  # 이보다 약한 점수는 '의견 없음'으로 보고 채점하지 않음
RISK_WARN_LEVEL = 0.3


def save_consensus(session: Session, sig: ConsensusSignal, opinions: list[Opinion], as_of: datetime,
                   horizon: int, regime: str | None) -> ConsensusRecord:
    rec = ConsensusRecord(symbol=sig.symbol, as_of=as_of, action=sig.action, prob_up=sig.prob_up,
                          confidence=sig.confidence, conflict=sig.conflict,
                          payload={**sig.to_dict(), "regime": regime, "horizon": horizon})
    session.add(rec)
    session.flush()
    for op in opinions:
        base = dict(consensus_id=rec.id, analyst=op.analyst, symbol=op.symbol, as_of=as_of, horizon_bars=horizon,
                    confidence=op.confidence, veto=op.veto)
        payload = {"reasons": op.reasons, "risks": op.risks, "summary": op.summary, "backend": op.backend,
                   "veto_reason": op.veto_reason, "error": op.error, "regime": regime, **op.meta}
        if op.prob_up is not None:
            session.add(AnalystOpinionRecord(category=DIRECTION, prob_up=op.prob_up, payload=payload, **base))
        for cat, score in op.sub_scores.items():
            # 위험 경고는 '경고를 냈을 때'만 채점 (조용히 있던 날까지 적중으로 세면 성적이 부풀려짐)
            if cat == RISK and not (op.veto or -score >= RISK_WARN_LEVEL):
                continue
            if cat == RISK or abs(score) >= MIN_SIGNAL:
                session.add(AnalystOpinionRecord(category=cat, prob_up=0.5 + 0.5 * score,
                                                 payload={**payload, "score": score}, **base))
    return rec


def resolve(session: Session, bars_by_symbol: dict[str, pd.DataFrame], benchmark: pd.DataFrame) -> int:
    """아직 채점 안 된 의견 중 결과가 확정된 것을 채점."""
    fwd_cache: dict[tuple[str, int], pd.Series] = {}

    def fwd(symbol: str, h: int) -> pd.Series | None:
        key = (symbol, h)
        if key not in fwd_cache:
            b = benchmark if symbol == "__benchmark__" else bars_by_symbol.get(symbol)
            if b is None:
                return None
            if "open" not in b:
                b = b.assign(open=b["close"])
            fwd_cache[key] = forward_return(b, h)
        return fwd_cache[key]

    def realized(symbol: str, as_of, h: int) -> float | None:
        s = fwd(symbol, h)
        if s is None:
            return None
        ts = pd.Timestamp(as_of)
        ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
        s = s[s.index <= ts]
        if s.empty or pd.isna(s.iloc[-1]):
            return None
        return float(s.iloc[-1])

    n = 0
    pending = session.scalars(select(AnalystOpinionRecord).where(AnalystOpinionRecord.correct.is_(None))).all()
    for op in pending:
        target = "__benchmark__" if op.category == MACRO else op.symbol
        r = realized(target, op.as_of, op.horizon_bars)
        if r is None:
            continue
        op.realized_return = r
        if op.category == RISK:
            # 경고 후 실제 하락했거나 5% 이상 급변했으면 적중
            op.correct = r < 0 or abs(r) >= 0.05
        else:
            op.correct = (op.prob_up >= 0.5) == (r > 0)
        n += 1
    for c in session.scalars(select(ConsensusRecord).where(ConsensusRecord.correct.is_(None))).all():
        h = (c.payload or {}).get("horizon", 5)
        r = realized(c.symbol, c.as_of, h)
        if r is None:
            continue
        c.realized_return = r
        c.correct = (c.prob_up >= 0.5) == (r > 0)
    return n


def scoreboard(session: Session, since: datetime | None = None,
               regime: str | None = None) -> dict[tuple[str, str], TrackRecord]:
    """(analyst, category) → TrackRecord. regime 을 주면 해당 국면에서의 성적만."""
    q = select(AnalystOpinionRecord).where(AnalystOpinionRecord.correct.is_not(None))
    if since is not None:
        q = q.where(AnalystOpinionRecord.as_of >= since)
    acc: dict[tuple[str, str], list] = defaultdict(lambda: [0, 0, 0.0])
    for op in session.scalars(q):
        if regime and (op.payload or {}).get("regime") != regime:
            continue
        a = acc[(op.analyst, op.category)]
        a[0] += 1
        a[1] += int(bool(op.correct))
        y = 1.0 if (op.realized_return or 0) > 0 else 0.0
        a[2] += ((op.prob_up or 0.5) - y) ** 2
    return {k: TrackRecord(n=v[0], hits=v[1], brier=v[2] / v[0] if v[0] else None) for k, v in acc.items()}


def scoreboard_table(board: dict[tuple[str, str], TrackRecord]) -> list[dict]:
    rows = []
    for (analyst, cat), r in sorted(board.items()):
        rows.append({"analyst": analyst, "category": cat, "n": r.n,
                     "accuracy": r.hits / r.n if r.n else None, "brier": r.brier})
    return rows


CATEGORY_LABELS = {DIRECTION: "단기 방향", NEWS: "뉴스 해석", MACRO: "거시경제", TREND: "추세", RISK: "위험 경고"}

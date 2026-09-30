"""예측 성적표 — "10개 중 몇 개 맞았나" 대신 돈의 관점으로 본다.

최근 N 번의 AI 합의 예측(종목·날짜별 마지막 판단 하나씩)을 실제 결과와 비교해:
  · 방향 적중률            (상승/하락 방향이 맞았나)
  · 평균 기대수익 vs 실제수익 (AI 가 말한 크기가 맞았나)
  · 비용 차감 후 신호 수익   (BUY/SELL 신호대로 했다면, 왕복 비용을 빼고)
  · 최대낙폭(MDD)           (신호를 매일 1/h 씩 나눠 따랐다고 보는 겹침 포트폴리오)
  · 확률 보정               (68% 라고 한 것이 실제로 68% 맞나: Brier Skill · ECE)
  · 국면별 적중률           (시장이 바뀌어도 무너지지 않나)
  · 틀린 예측마다 이유       (review.classify_miss)

기대수익은 합의 확률을 정규분포 가정으로 크기로 바꾼 값이다: E[r] = σ·√h·Φ⁻¹(p).
AI 가 따로 '몇 % 오른다' 를 말하지 않으므로 확률과 최근 변동성에서 나온다 — 화면에 그렇게 표시한다.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from statistics import NormalDist

import numpy as np
import pandas as pd
from sqlalchemy import select

from ..data.models import ConsensusRecord
from ..ensemble.calibration import metrics as cal_metrics

ROUND_TRIP_COST = 0.0025  # 국내 수수료+세금+슬리피지 대략 (미국은 global_market.COSTS 로 덮어쓴다)
_N = NormalDist()


def expected_move(prob_up: float | None, sigma_d: float | None, horizon: int) -> tuple[float | None, float | None]:
    """합의 확률 + 일간 변동성 → (horizon 기대수익, 하루 기대수익)."""
    if prob_up is None or not sigma_d or not math.isfinite(float(sigma_d)) or sigma_d <= 0:
        return None, None
    z = _N.inv_cdf(min(max(float(prob_up), 0.02), 0.98))
    h = max(int(horizon or 1), 1)
    return float(sigma_d) * math.sqrt(h) * z, float(sigma_d) * z / math.sqrt(h)


def sigma_of(payload: dict) -> float | None:
    p = ((payload or {}).get("evidence") or {}).get("price") or {}
    v = p.get("vol_20")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


def market_of(symbol: str) -> str:
    return "KR" if symbol.isdigit() else "US"


def _rows(session, market: str | None, since: datetime | None) -> list[ConsensusRecord]:
    q = select(ConsensusRecord).order_by(ConsensusRecord.as_of, ConsensusRecord.id)
    if since is not None:
        q = q.where(ConsensusRecord.as_of >= since)
    rows = [r for r in session.scalars(q) if market is None or market_of(r.symbol) == market]
    # 같은 종목·같은 일봉에 여러 번 판단했으면(이벤트 재분석 등) 마지막 것 하나만 센다 → 표본 부풀리기 방지
    last: dict[tuple[str, str], ConsensusRecord] = {}
    for r in rows:
        last[(r.symbol, str(pd.Timestamp(r.as_of).date()))] = r
    return sorted(last.values(), key=lambda r: (pd.Timestamp(r.as_of), r.id))


def _mdd(curve: list[float]) -> float:
    if not curve:
        return 0.0
    eq = np.asarray(curve, float)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def _stats(items: list[dict], cost: float) -> dict:
    """items: 채점된 예측 목록 → 요약 수치."""
    n = len(items)
    if not n:
        return {"n": 0}
    hits = [x["hit"] for x in items]
    exp = [x["expected"] for x in items if x["expected"] is not None]
    act = [x["actual"] for x in items]
    trades = [x for x in items if x["action"] in ("BUY", "SELL")]
    net = [x["signal_net"] for x in trades]
    # 겹침 포트폴리오: 매일의 신호에 자본의 1/h 를 h 일 동안 → 날짜별 평균 순수익 / h 를 누적
    curve, eq = [], 1.0
    by_day: dict[str, list[float]] = {}
    for x in trades:
        by_day.setdefault(x["date"], []).append(x["signal_net"] / max(x["horizon"], 1))
    for d in sorted(by_day):
        eq *= 1 + float(np.mean(by_day[d]))
        curve.append(eq)
    cal = cal_metrics([x["prob_up"] for x in items], [1.0 if x["actual"] > 0 else 0.0 for x in items])
    return {
        "n": n, "hit_rate": float(np.mean(hits)),
        "avg_expected": float(np.mean(exp)) if exp else None, "avg_actual": float(np.mean(act)),
        "n_trades": len(trades), "trade_hit_rate": float(np.mean([x["hit"] for x in trades])) if trades else None,
        "avg_signal_gross": float(np.mean([x["signal_gross"] for x in trades])) if trades else None,
        "avg_signal_net": float(np.mean(net)) if net else None, "cost": cost,
        "mdd": _mdd([1.0, *curve]), "curve_return": (curve[-1] - 1) if curve else 0.0,
        "curve": [[d, round(v, 5)] for d, v in zip(sorted(by_day), curve, strict=True)][-250:],
        "brier_skill": cal.get("brier_skill"), "ece": cal.get("ece"), "base_rate": cal.get("base_rate"),
        "calibration": cal.get("curve", []),
        # 크기 예측이 맞나: 기대수익과 실제수익의 상관 (방향만 맞히는 것보다 어려운 기준)
        "size_corr": (float(np.corrcoef([x["expected"] for x in items if x["expected"] is not None],
                                        [x["actual"] for x in items if x["expected"] is not None])[0, 1])
                      if len(exp) >= 10 and np.std(exp) > 0 and np.std(act) > 0 else None),
    }


def scorecard(session, market: str | None = None, last_n: int = 100, cost: float | None = None,
              now: datetime | None = None, names: dict[str, str] | None = None, recent: int = 25) -> dict:
    """최근 last_n 개 채점된 예측의 성적표 + 기간별(최근 30일) · 국면별 · 대기 중 예측."""
    from .review import classify_miss
    now = now or datetime.now(UTC)
    if cost is None:
        cost = 0.0012 if market == "US" else ROUND_TRIP_COST
    names = names or {}
    rows = _rows(session, market, None)
    done = [r for r in rows if r.correct is not None and r.realized_return is not None]
    pending = [r for r in rows if r.correct is None]
    items = []
    for r in done:
        p = r.payload or {}
        h = int(p.get("horizon") or 5)
        exp = p.get("expected_return")
        if exp is None:
            exp = expected_move(r.prob_up, sigma_of(p), h)[0]
        act = float(r.realized_return)
        sign = 1 if r.action == "BUY" else -1 if r.action == "SELL" else 0
        items.append({
            "id": r.id, "symbol": r.symbol, "name": names.get(r.symbol, r.symbol), "as_of": pd.Timestamp(r.as_of).isoformat(),
            "date": str(pd.Timestamp(r.as_of).date()), "action": r.action, "prob_up": float(r.prob_up),
            "confidence": float(r.confidence), "horizon": h, "expected": exp, "actual": act, "hit": bool(r.correct),
            "signal_gross": sign * act, "signal_net": sign * act - cost if sign else None,
            "regime": p.get("regime"), "miss_reason": None if r.correct else classify_miss(r),
        })
    last = items[-last_n:]
    cut = _utc(now) - timedelta(days=30)
    recent30 = [x for x in items if _utc(x["as_of"]) >= cut]
    regimes = {}
    for x in last:
        regimes.setdefault(x["regime"] or "unknown", []).append(x)
    by_regime = [{"regime": k, "n": len(v), "hit_rate": float(np.mean([y["hit"] for y in v])),
                  "avg_actual": float(np.mean([y["actual"] for y in v]))} for k, v in sorted(regimes.items())]
    first = min((_utc(r.as_of) for r in rows), default=None)
    return {
        "market": market or "ALL", "window": last_n, "total_scored": len(items), "pending": len(pending),
        "first_prediction": first.isoformat() if first is not None else None,
        "days_tracked": int((_utc(now) - first).days) if first is not None else 0,
        "summary": _stats(last, cost), "last_30d": _stats(recent30, cost), "by_regime": by_regime,
        "recent": list(reversed(items[-recent:])),
        "pending_list": [{"symbol": r.symbol, "name": names.get(r.symbol, r.symbol),
                          "as_of": pd.Timestamp(r.as_of).isoformat(), "action": r.action, "prob_up": float(r.prob_up),
                          "expected": (r.payload or {}).get("expected_return"),
                          "horizon": int((r.payload or {}).get("horizon") or 5)}
                         for r in reversed(pending[-15:])],
        "method": "기대수익 = 최근 20일 변동성 × √기간 × Φ⁻¹(상승확률). 신호 수익 = BUY 는 +실제, SELL 은 −실제(피한 손실), "
                  f"왕복 비용 {cost:.2%} 차감. MDD 는 매일 신호에 자본 1/기간씩 나눠 따른 겹침 포트폴리오 기준.",
    }


__all__ = ["scorecard", "expected_move", "sigma_of", "market_of", "ROUND_TRIP_COST"]

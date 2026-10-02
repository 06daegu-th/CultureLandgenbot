"""실측 슬리피지 → 비용 가정 자동 보정.

실제(또는 KIS 모의) 체결가와 주문 결정 시점 기준가를 비교해 슬리피지를 재고, 두 항으로 나눈다:
    슬리피지(분수) = 고정분 s0  +  충격 계수 k × 일간 변동성 σ × √(주문금액 / 20일 평균 거래대금)
최소제곱(음수 불가)으로 s0 · k 를 추정하고, 표본이 적을수록 기존 가정 쪽으로 당긴다 (가중 n/(n+50)).
50건 이상이면(QUANT_SLIPPAGE_AUTOCAL) 백테스트·가상 체결의 비용 모델이 이 값을 쓴다.
섀도 장부의 '체결 시뮬레이터 예측'과 실제 체결을 나란히 두면 시뮬레이터 자체의 오차(parity)도 보인다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

MIN_N = 50
PRIOR_N = 50


def observations(session, bars: dict[str, pd.DataFrame], mode: str = "live", days: int = 180) -> list[dict]:
    from sqlalchemy import select

    from ..data.models import OrderRecord
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(select(OrderRecord).where(
        OrderRecord.mode == mode, OrderRecord.created_at >= since, OrderRecord.ref_price.is_not(None),
        OrderRecord.avg_price.is_not(None), OrderRecord.status.in_(("filled", "partial")))).scalars().all()
    out = []
    for o in rows:
        if not o.ref_price or not o.avg_price or not o.filled_qty:
            continue
        sgn = 1 if o.side == "buy" else -1
        slip = sgn * (o.avg_price - o.ref_price) / o.ref_price
        b = bars.get(o.symbol)
        sig = adv = None
        if b is not None and len(b) > 25:
            t = pd.Timestamp(o.created_at)
            t = t.tz_localize("UTC") if t.tz is None else t
            h = b[b.index < t].iloc[-21:]
            if len(h) >= 10:
                sig = float(np.log(h["close"]).diff().std())
                adv = float((h["close"] * h["volume"]).mean()) if "volume" in h else None
        x = sig * np.sqrt(o.filled_qty * o.ref_price / adv) if sig and adv else None
        out.append({"id": o.id, "symbol": o.symbol, "side": o.side, "slip": float(slip), "x": None if x is None else float(x),
                    "notional": float(o.filled_qty * o.ref_price), "at": o.created_at.isoformat() if o.created_at else None})
    return out


def fit(obs: list[dict], prior_bps: float = 5.0, prior_coef: float = 0.7, seed: int = 3) -> dict:
    y_all = np.array([o["slip"] for o in obs], dtype=float)
    n = len(y_all)
    if n == 0:
        return {"n": 0, "applied": False, "fixed_bps": prior_bps, "impact_coef": prior_coef,
                "prior": {"fixed_bps": prior_bps, "impact_coef": prior_coef}, "verdict": "실측 체결 없음 — 기존 가정 유지"}
    with_x = [o for o in obs if o["x"] is not None]

    def est(sample):
        y = np.array([o["slip"] for o in sample], dtype=float)
        x = np.array([o["x"] for o in sample], dtype=float)
        if len(sample) >= 10 and x.var() > 0:
            A = np.column_stack([np.ones_like(x), x])
            (a, k), *_ = np.linalg.lstsq(A, y, rcond=None)
            if k < 0:  # 충격이 음수일 수는 없다 → 고정분만
                a, k = float(y.mean()), 0.0
            if a < 0:
                a = 0.0
                k = float(max((x @ y) / (x @ x), 0.0)) if x @ x > 0 else 0.0
            return float(a), float(k)
        return float(max(y.mean(), 0.0)), None
    a, k = est(with_x) if len(with_x) >= 10 else (float(max(y_all.mean(), 0.0)), None)
    rng = np.random.default_rng(seed)
    boots = []
    src = with_x if len(with_x) >= 10 else obs
    for _ in range(300):
        smp = [src[i] for i in rng.integers(0, len(src), len(src))]
        boots.append(est(smp) if src is with_x else (float(max(np.mean([o["slip"] for o in smp]), 0.0)), None))
    b0 = np.array([b[0] for b in boots]) * 1e4
    w = n / (n + PRIOR_N)
    fixed = w * a * 1e4 + (1 - w) * prior_bps
    coef = prior_coef if k is None else w * k + (1 - w) * prior_coef
    bps = y_all * 1e4
    return {"n": n, "n_with_liquidity": len(with_x), "weight": round(w, 3),
            "raw": {"fixed_bps": round(a * 1e4, 2), "impact_coef": None if k is None else round(k, 3)},
            "fixed_bps": round(float(fixed), 2), "impact_coef": round(float(coef), 3),
            "fixed_ci95": [round(float(np.quantile(b0, 0.025)), 2), round(float(np.quantile(b0, 0.975)), 2)],
            "mean_bps": round(float(bps.mean()), 2), "median_bps": round(float(np.median(bps)), 2),
            "p90_bps": round(float(np.quantile(bps, 0.9)), 2),
            "prior": {"fixed_bps": prior_bps, "impact_coef": prior_coef},
            "applied": n >= MIN_N,
            "verdict": ("표본 부족 — 기존 가정 유지" if n < MIN_N else
                        "가정보다 나쁨 → 비용 가정을 실측으로 올림" if fixed > prior_bps * 1.2 else
                        "가정보다 좋음 → 실측으로 낮춤 (보수적 가중)" if fixed < prior_bps * 0.8 else "가정과 비슷")}


def parity(sim_vs_real: list[dict]) -> dict:
    """[{sim_bps, real_bps}] → 시뮬레이터 오차 (실제 − 예측)."""
    d = np.array([x["real_bps"] - x["sim_bps"] for x in sim_vs_real if x.get("sim_bps") is not None and x.get("real_bps") is not None])
    if len(d) == 0:
        return {"n": 0}
    return {"n": int(len(d)), "bias_bps": round(float(d.mean()), 2), "mae_bps": round(float(np.abs(d).mean()), 2),
            "verdict": "시뮬레이터가 비용을 과소평가" if d.mean() > 2 else "과대평가" if d.mean() < -2 else "실제와 일치"}


def apply(costs, model: dict | None, enabled: bool = True):
    """보정 결과를 CostModelConfig 에 반영한 새 설정 (적용 조건을 못 채우면 그대로)."""
    from dataclasses import replace
    if not enabled or not model or not model.get("applied"):
        return costs
    return replace(costs, slippage_bps=float(model["fixed_bps"]), impact_coef=float(model["impact_coef"]))


__all__ = ["observations", "fit", "parity", "apply", "MIN_N"]

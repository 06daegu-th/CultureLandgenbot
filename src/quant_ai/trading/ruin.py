"""Risk-of-Ruin — 지금 방식으로 1년을 굴리면 계좌가 얼마나 깊이 빠질 수 있나.

일간 수익률을 블록 부트스트랩(연속 5일 묶음 → 변동성 군집 보존)으로 수천 번 다시 뽑아 1년 경로를 만든다.
  · P(최대 낙폭 ≥ 10/20/30/50%) — 50% 이상 = '파산'으로 본다 (복구에 +100% 필요)
  · 1년 뒤 계좌의 중앙값 · 하위 5%
  · 켈리 비율(평균/분산) 대비 지금 레버리지 — 1 을 넘으면 기대 성장률이 오히려 떨어지는 과잉 베팅
원천 수익률: 장부의 실제 일간 수익률(60일 이상일 때) → 없으면 현재 비중 × 과거 수익률(과거 시뮬레이션).
분포 가정이 없는 대신 '과거와 비슷한 1년'만 본다 — 과거에 없던 위기는 스트레스 테스트로 따로 본다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

THRESHOLDS = (0.10, 0.20, 0.30, 0.50)


def simulate(returns, horizon: int = 252, n_paths: int = 4000, block: int = 5, seed: int = 7,
             thresholds=THRESHOLDS) -> dict:
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    if len(r) < 30:
        return {"insufficient": True, "n_days": int(len(r)), "message": "일간 수익률이 30일 이상 필요합니다"}
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(horizon / block))
    starts = rng.integers(0, max(len(r) - block, 1), size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_paths, -1)[:, :horizon]
    idx = np.minimum(idx, len(r) - 1)
    paths = np.cumprod(1 + r[idx], axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.ones((n_paths, 1)), paths], axis=1), axis=1)[:, 1:]
    mdd = (1 - paths / peak).max(axis=1)
    term = paths[:, -1]
    mu, var = float(r.mean()), float(r.var())
    kelly = mu / var if var > 0 else None
    return {
        "n_days": int(len(r)), "horizon": horizon, "paths": n_paths, "block": block,
        "p_drawdown": {f"{int(t * 100)}": round(float((mdd >= t).mean()), 4) for t in thresholds},
        "p_ruin": round(float((mdd >= 0.5).mean()), 4),
        "p_loss": round(float((term < 1).mean()), 4),
        "terminal": {"p5": round(float(np.quantile(term, 0.05)) - 1, 4), "median": round(float(np.median(term)) - 1, 4),
                     "p95": round(float(np.quantile(term, 0.95)) - 1, 4)},
        "mdd": {"median": round(float(np.median(mdd)), 4), "p95": round(float(np.quantile(mdd, 0.95)), 4)},
        "daily_mean": round(mu, 6), "daily_vol": round(float(np.sqrt(var)), 6),
        "kelly_leverage": None if kelly is None else round(kelly, 2),
        "over_betting": bool(kelly is not None and kelly < 1.0 and mu > 0),
    }


def verdict(res: dict) -> str:
    if res.get("insufficient"):
        return "표본 부족"
    p20, pr = res["p_drawdown"].get("20", 0), res["p_ruin"]
    if pr >= 0.01:
        return f"위험: 1년 안에 반토막 날 확률 {pr:.1%}"
    if p20 >= 0.25:
        return f"주의: 1년 안에 -20% 낙폭 확률 {p20:.0%}"
    return f"양호: -20% 낙폭 확률 {p20:.0%} · 반토막 확률 {pr:.1%}"


__all__ = ["simulate", "verdict", "THRESHOLDS"]

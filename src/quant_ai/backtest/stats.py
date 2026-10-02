"""백테스트 통계적 유의성.

높은 Sharpe 가 '운'인지 '실력'인지 구분하기 위한 지표들.
- PSR (Probabilistic Sharpe Ratio, Bailey & López de Prado 2012):
  수익률의 왜도·첨도·표본 길이를 고려했을 때 '진짜 Sharpe > 기준' 일 확률.
- DSR (Deflated Sharpe Ratio, 2014): 여러 모델을 시도(다중검정)한 만큼 기준을 올려서 계산한 PSR.
  후보 모델을 많이 만들수록 우연히 좋은 백테스트가 나올 확률이 커지므로 반드시 봐야 한다.
- Bootstrap 신뢰구간: 블록 부트스트랩으로 Sharpe 의 95% 구간.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

EULER = 0.5772156649


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_ppf(p: float) -> float:
    lo, hi = -10.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _moments(r: np.ndarray) -> tuple[float, float, float]:
    sd = r.std(ddof=1)
    if sd == 0 or len(r) < 3:
        return 0.0, 0.0, 3.0
    sr = r.mean() / sd
    z = (r - r.mean()) / sd
    return float(sr), float(np.mean(z ** 3)), float(np.mean(z ** 4))


def probabilistic_sharpe(returns, sr_benchmark: float = 0.0) -> float:
    """기간 단위(비연율화) Sharpe 기준. 반환: P(진짜 SR > sr_benchmark)."""
    r = np.asarray(pd.Series(returns).dropna(), float)
    n = len(r)
    if n < 10:
        return 0.0
    sr, skew, kurt = _moments(r)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return 0.0
    return norm_cdf((sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom))


def deflated_sharpe(returns, n_trials: int, trial_sr_std: float | None = None) -> float:
    """n_trials 번 시도했을 때 기대되는 최대 Sharpe 를 기준으로 한 PSR."""
    r = np.asarray(pd.Series(returns).dropna(), float)
    n = len(r)
    if n < 10:
        return 0.0
    n_trials = max(int(n_trials), 1)
    if n_trials == 1:
        return probabilistic_sharpe(r, 0.0)
    sr, _, _ = _moments(r)
    sd = trial_sr_std if trial_sr_std else math.sqrt((1 + 0.5 * sr ** 2) / (n - 1))
    sr0 = sd * ((1 - EULER) * norm_ppf(1 - 1 / n_trials) + EULER * norm_ppf(1 - 1 / (n_trials * math.e)))
    return probabilistic_sharpe(r, sr0)


def bootstrap_sharpe_ci(returns, periods: int = 252, n_boot: int = 500, block: int = 10,
                        seed: int = 0) -> tuple[float, float]:
    """블록 부트스트랩 (자기상관 보존) 연율화 Sharpe 95% 구간."""
    r = np.asarray(pd.Series(returns).dropna(), float)
    n = len(r)
    if n < 2 * block:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    starts = np.arange(n - block + 1)
    k = int(math.ceil(n / block))
    out = np.empty(n_boot)
    for i in range(n_boot):
        idx = (rng.choice(starts, k)[:, None] + np.arange(block)).ravel()[:n]
        s = r[idx]
        sd = s.std(ddof=1)
        out[i] = s.mean() / sd * math.sqrt(periods) if sd > 0 else 0.0
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def calibration_table(prob, label, bins=(0, 0.4, 0.45, 0.5, 0.55, 0.6, 1.0)) -> list[dict]:
    """예측 확률 구간별 실제 상승 비율 — 확률이 '정직한지' 확인."""
    df = pd.DataFrame({"p": np.asarray(prob, float), "y": np.asarray(label, float)})
    df["bin"] = pd.cut(df["p"], bins, include_lowest=True)
    g = df.groupby("bin", observed=True).agg(n=("y", "size"), predicted=("p", "mean"), actual=("y", "mean"))
    return [{"bin": str(i), "n": int(r.n), "predicted": round(float(r.predicted), 4), "actual": round(float(r.actual), 4)}
            for i, r in g.iterrows()]

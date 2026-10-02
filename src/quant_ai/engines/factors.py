"""가격 기반 팩터 (연구소와 실시간 코어 전략이 같은 함수를 쓴다 — 검증한 것 = 실제로 도는 것)."""

from __future__ import annotations

import numpy as np
import pandas as pd

# 실제 KRX 데이터 연구(docs/RESEARCH_KRX.md)에서 dev 구간으로 선택된 코어 전략의 점수 가중치
CORE_FACTOR_WEIGHTS = {"mom_12_1": 1.0, "vol_60": -1.0, "dist_52w": 1.0}


def price_factors(close: pd.Series) -> pd.DataFrame:
    lr = np.log(close).diff()
    return pd.DataFrame({
        "mom_12_1": close.shift(21) / close.shift(252) - 1,  # 최근 1개월 제외 12개월 모멘텀
        "mom_6_1": close.shift(21) / close.shift(126) - 1,
        "dist_52w": close / close.rolling(252, min_periods=120).max() - 1,
        "vol_60": lr.rolling(60).std(),
    }, index=close.index)


def factor_score_cross_section(latest: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.Series:
    """종목 × 팩터 (한 시점) → 점수 = Σ w · (횡단면 백분위 - 0.5). 팩터 결측 종목은 제외."""
    w = weights or CORE_FACTOR_WEIGHTS
    x = latest[list(w)].dropna()
    ranks = x.rank(pct=True) - 0.5
    return sum(ranks[k] * v for k, v in w.items()).sort_values(ascending=False)

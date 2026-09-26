"""Market Regime Engine.

벤치마크(KOSPI, S&P500 등) 일봉으로 지금 시장이 어떤 국면인지 판정한다.
국면은 (1) 예측 모델의 피처, (2) 리스크 엔진의 노출 한도 배수로 쓰인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class Regime(str, Enum):
    BULL_QUIET = "bull_quiet"
    BULL_VOLATILE = "bull_volatile"
    SIDEWAYS = "sideways"
    BEAR_QUIET = "bear_quiet"
    BEAR_VOLATILE = "bear_volatile"
    CRISIS = "crisis"


# 국면별 최대 노출 배수 (RiskLimits.max_gross_exposure 에 곱해짐)
EXPOSURE_MULTIPLIER = {
    Regime.BULL_QUIET: 1.0,
    Regime.BULL_VOLATILE: 0.7,
    Regime.SIDEWAYS: 0.6,
    Regime.BEAR_QUIET: 0.4,
    Regime.BEAR_VOLATILE: 0.25,
    Regime.CRISIS: 0.0,
}

# 모델 피처용 수치 점수 (강세 +, 약세 -)
REGIME_SCORE = {
    Regime.BULL_QUIET: 1.0, Regime.BULL_VOLATILE: 0.5, Regime.SIDEWAYS: 0.0,
    Regime.BEAR_QUIET: -0.5, Regime.BEAR_VOLATILE: -1.0, Regime.CRISIS: -2.0,
}


@dataclass(frozen=True)
class RegimeState:
    regime: Regime
    trend: float  # MA50/MA200 - 1
    vol_pct: float  # 최근 변동성의 1년 분위 (0~1)
    drawdown: float  # 52주 고점 대비 낙폭 (<=0)

    @property
    def exposure_multiplier(self) -> float:
        return EXPOSURE_MULTIPLIER[self.regime]


def _classify(trend: float, slope: float, vol_pct: float, dd: float) -> Regime:
    if dd <= -0.20 and vol_pct >= 0.9:
        return Regime.CRISIS
    high_vol = vol_pct >= 0.7
    if trend > 0.02 and slope > 0:
        return Regime.BULL_VOLATILE if high_vol else Regime.BULL_QUIET
    if trend < -0.02 and slope < 0:
        return Regime.BEAR_VOLATILE if high_vol else Regime.BEAR_QUIET
    return Regime.SIDEWAYS


def regime_series(benchmark: pd.DataFrame, fast: int = 50, slow: int = 200, vol_win: int = 20,
                  rank_win: int = 252) -> pd.DataFrame:
    """각 시점의 국면 (그 시점까지의 데이터만 사용)."""
    c = benchmark["close"]
    ma_f, ma_s = c.rolling(fast).mean(), c.rolling(slow, min_periods=fast).mean()
    trend = ma_f / ma_s - 1
    slope = ma_f.pct_change(10)
    vol = np.log(c).diff().rolling(vol_win).std()
    vol_pct = vol.rolling(rank_win, min_periods=60).rank(pct=True)
    dd = c / c.rolling(rank_win, min_periods=1).max() - 1
    out = pd.DataFrame({"trend": trend, "slope": slope, "vol_pct": vol_pct, "drawdown": dd})
    valid = out.notna().all(axis=1)
    out["regime"] = None
    out.loc[valid, "regime"] = [
        _classify(r.trend, r.slope, r.vol_pct, r.drawdown).value for r in out[valid].itertuples()
    ]
    out["score"] = out["regime"].map(lambda r: REGIME_SCORE[Regime(r)] if r else np.nan)
    return out


def current_regime(benchmark: pd.DataFrame) -> RegimeState:
    s = regime_series(benchmark).dropna(subset=["regime"])
    if s.empty:
        return RegimeState(Regime.SIDEWAYS, 0.0, 0.5, 0.0)  # 데이터 부족 시 보수적 중립
    last = s.iloc[-1]
    return RegimeState(Regime(last["regime"]), float(last["trend"]), float(last["vol_pct"]), float(last["drawdown"]))


def equal_weight_index(bars_by_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """벤치마크 데이터가 없을 때 유니버스 동일가중 지수로 대체."""
    closes = pd.DataFrame({s: b["close"] for s, b in bars_by_symbol.items()}).sort_index()
    rets = closes.pct_change().mean(axis=1).fillna(0.0)
    return pd.DataFrame({"close": 100 * (1 + rets).cumprod()})

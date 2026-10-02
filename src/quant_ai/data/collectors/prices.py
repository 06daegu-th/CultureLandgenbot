"""국내 + 해외 시세 소스.

- ``SyntheticPriceSource``: 네트워크 없이 파이프라인 전체를 돌려보기 위한 가상 시세 (국면 전환 포함)
- ``CSVPriceSource``: 직접 받은 CSV 사용
- ``YahooPriceSource``: 해외/국내 일봉 (국내는 ``005930.KS`` 형식). ``pip install quant-ai[yahoo]``
- 장중 실시간 시세/호가/체결은 ``RealtimeFeed`` 인터페이스를 증권사 API(KIS 웹소켓 등)로 구현해 연결한다.
"""

from __future__ import annotations

import zlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

OHLCV = ["open", "high", "low", "close", "volume"]


def _utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


class PriceSource(ABC):
    name = "base"

    @abstractmethod
    def fetch_bars(self, symbol: str, start: datetime, end: datetime, interval: str = "1d") -> pd.DataFrame:
        """UTC DatetimeIndex, 컬럼 open/high/low/close/volume."""


class SyntheticPriceSource(PriceSource):
    """국면(상승/하락/횡보, 저변동/고변동)이 바뀌는 가상 시세.

    약한 모멘텀을 넣어두어 모델이 배울 '무언가'가 있게 만든다. 실제 시장의 예측력과는 무관하다.
    """

    name = "synthetic"

    def __init__(self, seed: int = 7, momentum: float = 0.08):
        self.seed = seed
        self.momentum = momentum

    def fetch_bars(self, symbol: str, start: datetime, end: datetime, interval: str = "1d") -> pd.DataFrame:
        idx = pd.bdate_range(start, end, tz="UTC")
        n = len(idx)
        rng = np.random.default_rng([self.seed, zlib.crc32(symbol.encode())])
        regimes = [(0.0008, 0.010), (-0.0010, 0.022), (0.0001, 0.008), (0.0012, 0.018)]
        rets = np.empty(n)
        i, prev = 0, 0.0
        while i < n:
            drift, vol = regimes[rng.integers(len(regimes))]
            length = int(rng.integers(40, 160))
            for j in range(i, min(n, i + length)):
                prev = drift + self.momentum * prev + rng.normal(0, vol)
                rets[j] = prev
            i += length
        close = 10_000 * np.exp(np.cumsum(rets))
        gap = rng.normal(0, 0.003, n)
        open_ = np.r_[close[0], close[:-1]] * np.exp(gap)
        spread = np.abs(rng.normal(0, 0.006, n))
        high = np.maximum(open_, close) * (1 + spread)
        low = np.minimum(open_, close) * (1 - spread)
        volume = rng.lognormal(12, 0.4, n) * (1 + 20 * np.abs(rets))
        return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


class CSVPriceSource(PriceSource):
    """``{root}/{symbol}.csv`` (date,open,high,low,close,volume)."""

    name = "csv"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def fetch_bars(self, symbol: str, start: datetime, end: datetime, interval: str = "1d") -> pd.DataFrame:
        df = pd.read_csv(self.root / f"{symbol}.csv")
        df.columns = [c.lower() for c in df.columns]
        ts_col = "date" if "date" in df.columns else "ts"
        df.index = pd.to_datetime(df.pop(ts_col), utc=True)
        df = df[OHLCV].sort_index()
        return df.loc[_utc(start):_utc(end)]


class YahooPriceSource(PriceSource):
    name = "yahoo"

    def fetch_bars(self, symbol: str, start: datetime, end: datetime, interval: str = "1d") -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - 선택 의존성
            raise RuntimeError("pip install 'quant-ai[yahoo]' 필요") from exc
        df = yf.download(symbol, start=start, end=end, interval=interval, auto_adjust=True, progress=False)
        if df.empty:
            return pd.DataFrame(columns=OHLCV)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        idx = pd.DatetimeIndex(df.index)
        df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        return df[OHLCV]


class RealtimeFeed(ABC):
    """장중 실시간 가격/호가/체결 스트림. 증권사 웹소켓으로 구현한다."""

    @abstractmethod
    def subscribe(
        self,
        symbols: list[str],
        on_tick: Callable[[str, datetime, float, float], None],
        on_quote: Callable[[str, datetime, list, list], None],
    ) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

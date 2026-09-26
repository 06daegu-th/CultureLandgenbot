"""Technical / Fundamental Feature Engine.

원칙: t 시점의 피처는 t 시점 종가까지의 정보만 쓴다. 라벨(미래 수익률)은 따로 만든다.
``tests/test_features.py`` 가 미래 데이터를 바꿔도 과거 피처가 변하지 않는지 검증한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "ret_1", "ret_5", "ret_20", "vol_20", "vol_ratio", "rsi_14", "macd_hist", "bb_pctb",
    "dist_ma20", "dist_ma60", "atr_14", "volume_z", "high_20_dist", "low_20_dist",
]


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0) / 100.0


def technical_features(bars: pd.DataFrame) -> pd.DataFrame:
    c, h, lo, v = bars["close"], bars["high"], bars["low"], bars["volume"]
    logret = np.log(c).diff()
    f = pd.DataFrame(index=bars.index)
    f["ret_1"] = c.pct_change(1)
    f["ret_5"] = c.pct_change(5)
    f["ret_20"] = c.pct_change(20)
    f["vol_20"] = logret.rolling(20).std()
    f["vol_ratio"] = logret.rolling(5).std() / f["vol_20"]
    f["rsi_14"] = _rsi(c)
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    f["macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / c
    ma20, sd20 = c.rolling(20).mean(), c.rolling(20).std()
    f["bb_pctb"] = (c - (ma20 - 2 * sd20)) / (4 * sd20)
    f["dist_ma20"] = c / ma20 - 1
    f["dist_ma60"] = c / c.rolling(60).mean() - 1
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    f["atr_14"] = tr.rolling(14).mean() / c
    lv = np.log1p(v)
    f["volume_z"] = (lv - lv.rolling(20).mean()) / lv.rolling(20).std()
    f["high_20_dist"] = c / h.rolling(20).max() - 1
    f["low_20_dist"] = c / lo.rolling(20).min() - 1
    return f.replace([np.inf, -np.inf], np.nan)


def forward_return(bars: pd.DataFrame, horizon: int) -> pd.Series:
    """라벨: 다음 봉 시가에 진입해 horizon 봉 뒤 종가에 청산했을 때의 수익률.

    t 시점 신호는 t 종가 이후에만 알 수 있으므로 진입은 t+1 시가로 가정한다.
    """
    entry = bars["open"].shift(-1)
    exit_ = bars["close"].shift(-horizon)
    return exit_ / entry - 1


def asof_merge(features: pd.DataFrame, external: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """재무/경제지표/뉴스 등 외부 데이터를 '공개 시점 기준'으로 붙인다.

    external 의 index 는 그 값이 *공개된* 시각이어야 한다 (예: 실적은 결산일이 아니라 공시일).
    """
    if external is None or external.empty:
        return features
    ext = external.sort_index().add_prefix(prefix)
    left = features.reset_index(names="_ts")
    right = ext.reset_index(names="_ext_ts")
    merged = pd.merge_asof(left.sort_values("_ts"), right.sort_values("_ext_ts"),
                           left_on="_ts", right_on="_ext_ts", direction="backward")
    return merged.drop(columns="_ext_ts").set_index("_ts").rename_axis(features.index.name)


def build_dataset(
    bars_by_symbol: dict[str, pd.DataFrame],
    horizon: int = 5,
    regime: pd.Series | None = None,
    sentiment: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """전 종목 패널 데이터셋. index=(ts, symbol), 컬럼=피처 + fwd_ret + label.

    fwd_ret 이 NaN 인 마지막 horizon 개 행은 추론용으로 남겨둔다 (학습 시 dropna).
    """
    frames = []
    for symbol, bars in bars_by_symbol.items():
        f = technical_features(bars)
        if regime is not None:
            f["regime_score"] = regime.reindex(f.index, method="ffill")
        if sentiment is not None and symbol in getattr(sentiment, "columns", []):
            # 하루 지연: d 일 봉에는 d-1 일까지의 뉴스만 반영 (장 마감 후 기사 누수 방지)
            lagged = sentiment[symbol].shift(1, freq="D")
            f["news_sent"] = lagged.reindex(f.index.normalize(), method="ffill").fillna(0.0).to_numpy()
        f["fwd_ret"] = forward_return(bars, horizon)
        f["symbol"] = symbol
        frames.append(f)
    df = pd.concat(frames).set_index("symbol", append=True).sort_index()
    df["label"] = (df["fwd_ret"] > 0).astype(float).where(df["fwd_ret"].notna())
    return df


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in ("fwd_ret", "label")]

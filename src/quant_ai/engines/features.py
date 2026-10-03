"""Technical / Fundamental Feature Engine.

원칙: t 시점의 피처는 t 시점 종가까지의 정보만 쓴다. 라벨(미래 수익률)은 따로 만든다.
``tests/test_features.py`` 가 미래 데이터를 바꿔도 과거 피처가 변하지 않는지 검증한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "ret_1", "ret_5", "ret_20", "vol_20", "vol_ratio", "jump_sigma", "rsi_14", "macd_hist", "bb_pctb",
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
    # 최근 5일 변동성 ÷ 그 '이전' 60일 변동성. (같은 구간으로 나누면 비율이 최대 ~2.2 로 묶여 급증을 못 잡는다)
    f["vol_ratio"] = logret.rolling(5).std() / logret.rolling(60, min_periods=20).std().shift(5)
    # 당일 움직임 ÷ 전일까지의 20일 변동성 (당일을 포함하면 최대 ~4.4σ 로 묶인다)
    f["jump_sigma"] = logret.abs() / logret.rolling(20).std().shift(1)
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


def long_features(bars: pd.DataFrame) -> pd.DataFrame:
    """v20 장기 팩터 — 16년 KRX 연구(docs/RESEARCH_QUANT_V2.md)에서 종목 선택력이 확인된 재료.
    12-1·6-1·3-1개월 모멘텀(최근 1개월 제외) · 52주 고점 근접도 · 60·250일 변동성 · 비유동성 · 최근 20일 최대 상승(복권형)."""
    c = bars["close"]
    lr = np.log(c).diff()
    f = pd.DataFrame(index=bars.index)
    f["mom_12_1"] = c.shift(21) / c.shift(252) - 1
    f["mom_6_1"] = c.shift(21) / c.shift(126) - 1
    f["mom_3_1"] = c.shift(21) / c.shift(63) - 1
    f["high_252"] = c / c.rolling(252, min_periods=120).max() - 1
    f["vol_60"] = lr.rolling(60).std()
    f["vol_250"] = lr.rolling(250, min_periods=120).std()
    amount = bars["amount"] if "amount" in bars else c * bars["volume"]
    f["illiq"] = (lr.abs() / amount.replace(0, np.nan)).rolling(60, min_periods=20).mean()
    f["max_ret_20"] = lr.rolling(20).max()
    return f.replace([np.inf, -np.inf], np.nan)


RANK_SKIP = ("fwd_ret", "label", "regime_score", "n_cross")  # 날짜마다 모든 종목이 같은 값 → 순위에 의미 없음
MIN_CROSS = 20  # 순위가 의미 있으려면 같은 날 비교할 종목이 이만큼은 있어야 한다


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
    target: str = "up",
) -> pd.DataFrame:
    """전 종목 패널 데이터셋. index=(ts, symbol), 컬럼=피처 + cs_피처(날짜별 순위) + fwd_ret + label.

    target="up"     label = horizon 뒤 오르면 1 (시장 방향이 섞인다 — 예전 방식)
    target="excess" label = 같은 날 전체 종목의 가운데(중앙값)보다 더 오르면 1 — '어떤 종목이 더 오를까' (v20)
    cs_* 컬럼: 날짜마다 종목들 사이 순위(0~1) − 0.5. 시장 전체가 오르내리는 영향을 지우고 '상대 위치'만 남긴다.
    fwd_ret 이 NaN 인 마지막 horizon 개 행은 추론용으로 남겨둔다 (학습 시 dropna).
    """
    if target not in ("up", "excess"):
        raise ValueError("target 은 up / excess")
    frames = []
    for symbol, bars in bars_by_symbol.items():
        f = pd.concat([technical_features(bars), long_features(bars)], axis=1)
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
    raw = [c for c in df.columns if c not in RANK_SKIP and c != "symbol"]
    ranks = df[raw].groupby(level=0).rank(pct=True) - 0.5
    df[[f"cs_{c}" for c in raw]] = ranks.to_numpy()
    df["n_cross"] = df.groupby(level=0)["fwd_ret"].transform("size").astype(float)  # 그날 비교한 종목 수
    if target == "excess":
        ex = df["fwd_ret"] - df["fwd_ret"].groupby(level=0).transform("median")
        df["label"] = (ex > 0).astype(float).where(df["fwd_ret"].notna())
    else:
        df["label"] = (df["fwd_ret"] > 0).astype(float).where(df["fwd_ret"].notna())
    return df


def feature_columns(df: pd.DataFrame, target: str = "up") -> list[str]:
    """모델이 쓰는 피처. excess(종목 선택) 모델은 날짜별 순위(cs_*)만 — 같은 날 모든 종목에 같은 값은 쓸모없다."""
    if target == "excess":
        return [c for c in df.columns if c.startswith("cs_")]
    return [c for c in df.columns if c not in ("fwd_ret", "label", "n_cross") and not c.startswith("cs_")]

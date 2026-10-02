"""시세 데이터 품질 검사. 잘못된 데이터 한 줄이 모델·리스크 판단 전체를 오염시킬 수 있다.

검사 항목
- 가격 ≤ 0, NaN/inf
- OHLC 모순 (high < max(open, close) 또는 low > min(open, close))
- 중복 타임스탬프, 정렬 안 됨
- 비정상 급변 (로그수익률이 최근 변동성의 N배 이상 — 상하한가 30% 초과 등)
- 긴 공백 (거래일 기준 N일 이상 데이터 누락)
- 거래량 음수
- 미래 시각 (수집 시스템 시계 오류·시간대 뒤틀림)
- 거래정지 의심 (거래량 0 + 가격 변화 없음이 이어짐)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class QualityReport:
    symbol: str
    rows_in: int
    rows_out: int
    dropped: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.dropped and not self.warnings


def validate_bars(bars: pd.DataFrame, symbol: str = "", max_jump: float = 0.35, jump_sigma: float = 12.0,
                  max_gap_days: int = 10) -> tuple[pd.DataFrame, QualityReport]:
    """문제가 확실한 행은 제거하고, 의심스러운 점은 경고로 남긴다."""
    rep = QualityReport(symbol, len(bars), len(bars))
    if bars.empty:
        return bars, rep
    df = bars.copy()

    def drop(mask: pd.Series, reason: str) -> None:
        nonlocal df
        n = int(mask.sum())
        if n:
            rep.dropped[reason] = rep.dropped.get(reason, 0) + n
            df = df[~mask]

    if not df.index.is_monotonic_increasing:
        df = df.sort_index()
        rep.warnings.append("타임스탬프 정렬 안 됨 → 정렬")
    drop(pd.Series(df.index.duplicated(keep="last"), index=df.index), "중복 타임스탬프")
    idx = pd.DatetimeIndex(df.index)
    now = pd.Timestamp.now(tz="UTC") if idx.tz is not None else pd.Timestamp.now()
    drop(pd.Series(idx > now + pd.Timedelta(days=1), index=df.index), "미래 시각")
    px = df[["open", "high", "low", "close"]]
    drop(~np.isfinite(px).all(axis=1) | (px <= 0).any(axis=1), "가격 0 이하/결측")
    drop(df["volume"].fillna(0) < 0, "거래량 음수")
    hi_bad = df["high"] < df[["open", "close"]].max(axis=1) * (1 - 1e-6)
    lo_bad = df["low"] > df[["open", "close"]].min(axis=1) * (1 + 1e-6)
    drop(hi_bad | lo_bad, "OHLC 모순")

    if len(df) > 30:
        lr = np.log(df["close"]).diff()
        vol = lr.rolling(60, min_periods=20).std().shift(1)
        jumps = (lr.abs() > max_jump) | (lr.abs() > jump_sigma * vol)
        for ts in df.index[jumps.fillna(False)]:
            rep.warnings.append(f"{ts.date()} 급변 {lr[ts]:+.1%} (액면분할·권리락·데이터 오류 확인 필요)")
        still = (df["volume"].fillna(0) == 0) & (df["close"].diff() == 0)
        runs = still.groupby((~still).cumsum()).cumsum()
        for ts in df.index[(runs == 3)]:  # 3거래일 이상 이어진 구간의 시작 무렵 한 번만 경고
            rep.warnings.append(f"{ts.date()} 전후 거래정지 의심 (거래량 0 · 가격 불변 3일+)")
        if bool(still.iloc[-1]):
            rep.warnings.append(f"최근 봉 {df.index[-1].date()} 거래량 0 · 가격 불변 → 신규 편입 금지 대상")
        gaps = df.index.to_series().diff().dt.days
        for ts, g in gaps[gaps > max_gap_days * 7 / 5].items():
            rep.warnings.append(f"{ts.date()} 이전 {int(g)}일 공백 (거래정지/수집 누락?)")
    rep.rows_out = len(df)
    return df, rep

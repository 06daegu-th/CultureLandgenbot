"""KRX 전 종목 일별 데이터 (FinanceData/marcap, 1995~현재, 상장폐지 종목 포함).

    git clone --depth 1 --filter=blob:none --sparse https://github.com/FinanceData/marcap.git
    cd marcap && git sparse-checkout set --no-cone /data/marcap-2010.parquet ...

핵심 처리
- **수정주가**: marcap 가격은 원시 가격이다(액면분할 시 -98% 처럼 보임). KRX 의 전일대비(Changes)는
  조정된 기준가 대비이므로 `ret = Changes / (Close - Changes)` 를 누적해 수정주가를 복원한다.
- **생존편향 제거**: 매월 말 시가총액 상위 N 종목을 그 시점 기준으로 선정(point-in-time).
  나중에 상장폐지된 종목도 당시 상위였다면 포함된다.
- 보통주만 (종목코드 끝자리 0), KONEX 제외.

데이터 출처: 한국거래소(KRX) 공개 데이터를 FinanceData 가 정리한 것. 상업적 재배포 전 KRX 이용약관 확인 필요.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

COLS = ["Date", "Code", "Name", "Open", "High", "Low", "Close", "Changes", "Volume", "Amount", "Marcap", "Market"]


def load_marcap(root: str | Path, start_year: int, end_year: int, columns: list[str] | None = None) -> pd.DataFrame:
    root = Path(root)
    frames = []
    for y in range(start_year, end_year + 1):
        p = root / f"marcap-{y}.parquet"
        if not p.exists():
            p = root / "data" / f"marcap-{y}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p, columns=columns or COLS))
    if not frames:
        raise FileNotFoundError(f"{root} 에 marcap-{start_year}~{end_year}.parquet 없음")
    df = pd.concat(frames, ignore_index=True)
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ"]) & df["Code"].str.endswith("0")]
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize("UTC")
    return df


def point_in_time_universe(df: pd.DataFrame, top_n: int = 100, min_amount: float = 1e9) -> pd.DataFrame:
    """월말 시가총액 상위 N (최근 20일 평균 거래대금 min_amount 이상) → 다음 달 매매 가능 종목.

    반환: index=일자, columns=종목코드, 값=bool (그 날 매매 대상인가)
    """
    d = df[["Date", "Code", "Marcap", "Amount"]].copy()
    d["month"] = d["Date"].dt.tz_convert(None).dt.to_period("M")
    amt = d.pivot_table(index="Date", columns="Code", values="Amount").rolling(20, min_periods=5).mean()
    last_days = d.groupby("month")["Date"].max()
    picks: dict[pd.Period, list[str]] = {}
    for m, day in last_days.items():
        snap = d[d["Date"] == day].set_index("Code")
        liquid = amt.loc[day].reindex(snap.index) >= min_amount
        picks[m] = snap[liquid]["Marcap"].nlargest(top_n).index.tolist()
    dates = pd.DatetimeIndex(sorted(d["Date"].unique()))
    codes = sorted({c for v in picks.values() for c in v})
    elig = pd.DataFrame(False, index=dates, columns=codes)
    months = dates.tz_convert(None).to_period("M")
    for m in sorted(picks):
        prev = picks.get(m - 1)
        if prev:
            elig.loc[months == m, prev] = True  # 전월 말 기준 선정 → 이번 달 매매 (미래 정보 없음)
    return elig


def adjusted_bars(df: pd.DataFrame, codes: list[str]) -> dict[str, pd.DataFrame]:
    """종목별 수정 OHLCV. 수정계수 = 수정종가 / 원시종가 를 시·고·저가에도 적용."""
    out = {}
    sub = df[df["Code"].isin(codes)].sort_values("Date")
    for code, g in sub.groupby("Code"):
        g = g.set_index("Date")
        g = g[~g.index.duplicated(keep="last")]
        g = g[g["Close"] > 0]
        if len(g) < 30:
            continue
        base = g["Close"] - g["Changes"]
        ret = (g["Changes"] / base).where(base > 0, 0.0).fillna(0.0).clip(-0.9, 3.0)
        ret.iloc[0] = 0.0
        adj_close = g["Close"].iloc[-1] * (1 + ret).cumprod() / (1 + ret).cumprod().iloc[-1]
        f = adj_close / g["Close"]
        bars = pd.DataFrame({
            "open": g["Open"].where(g["Open"] > 0, g["Close"]) * f,
            "high": g["High"].where(g["High"] > 0, g["Close"]) * f,
            "low": g["Low"].where(g["Low"] > 0, g["Close"]) * f,
            "close": adj_close,
            "volume": g["Volume"].astype(float) / f,  # 분할 전후 거래량도 같은 단위로
        })
        out[code] = bars
    return out


def cap_weighted_index(df: pd.DataFrame, market: str = "KOSPI") -> pd.DataFrame:
    """시장 전체 시가총액 가중 지수 (KOSPI/KOSDAQ 대용 벤치마크). 전일 시총 가중 × 당일 수익률."""
    d = df[df["Market"] == market][["Date", "Code", "Close", "Changes", "Marcap"]].copy()
    base = d["Close"] - d["Changes"]
    d["ret"] = (d["Changes"] / base).where(base > 0, 0.0)
    d = d.sort_values(["Code", "Date"])
    d["w"] = d.groupby("Code")["Marcap"].shift(1)
    d = d.dropna(subset=["w"])
    r = d.groupby("Date").apply(lambda x: np.average(x["ret"], weights=x["w"]), include_groups=False)
    idx = 100 * (1 + r).cumprod()
    return pd.DataFrame({"open": idx, "high": idx, "low": idx, "close": idx, "volume": 0.0})


@dataclass
class KRXDataset:
    bars: dict[str, pd.DataFrame]
    eligible: pd.DataFrame
    benchmark: pd.DataFrame
    names: dict[str, str]


def build_krx_dataset(root: str | Path, start_year: int, end_year: int, top_n: int = 100,
                      min_amount: float = 1e9) -> KRXDataset:
    df = load_marcap(root, start_year, end_year)
    elig = point_in_time_universe(df, top_n, min_amount)
    codes = list(elig.columns[elig.any()])
    bars = adjusted_bars(df, codes)
    elig = elig[[c for c in elig.columns if c in bars]]
    names = df.drop_duplicates("Code", keep="last").set_index("Code")["Name"].to_dict()
    return KRXDataset(bars, elig, cap_weighted_index(df, "KOSPI"), {c: names.get(c, c) for c in bars})

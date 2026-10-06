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

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

COLS = ["Date", "Code", "Name", "Open", "High", "Low", "Close", "Changes", "Volume", "Amount", "Marcap", "Market"]


def load_marcap(root: str | Path, start_year: int, end_year: int, columns: list[str] | None = None) -> pd.DataFrame:
    root = Path(root)
    frames = []
    for y in range(start_year, end_year + 1):
        p = root / f"marcap-{y}.parquet"
        if not p.exists():
            p = root / "data" / f"marcap-{y}.parquet"
        if p.exists():
            if columns:
                frames.append(pd.read_parquet(p, columns=columns))
                continue
            try:  # 상장주식수(Stocks)가 있으면 함께 — 시가총액 = 주식수 × 종가 대조용 (v29)
                frames.append(pd.read_parquet(p, columns=[*COLS, "Stocks"]))
            except Exception:  # noqa: BLE001 - 열이 없는 파일 (예전 형식·테스트용)
                frames.append(pd.read_parquet(p, columns=COLS))
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
            "marcap": g["Marcap"].astype(float),  # 시가총액(원) — 분할과 무관
            "amount": g["Amount"].astype(float),  # 거래대금(원)
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
    extra_indices: dict[str, pd.DataFrame] = field(default_factory=dict)  # 화면용 (KOSDAQ 대용)
    facts: dict[str, dict] = field(default_factory=dict)  # v29: 종목별 시가총액 · 상장주식수 · 수정주가 이벤트 (데이터 점검용)


ADJ_EVENT_MIN = 0.02  # 기준가가 전일 종가와 2% 넘게 다르면 '가격 조정 이벤트' (액면분할·병합·무상증자·유상증자 권리락 등)


def krx_facts(df: pd.DataFrame, codes) -> dict[str, dict]:
    """원천 자료 자체의 정합성과 수정주가 이벤트를 종목별로 정리 (화면 · 데이터 점검용).

    - 기준가(Close - Changes) ≠ 전일 종가 → 그날 가격 조정 이벤트 (배율 = 기준가 / 전일 종가, 예: 1/50 액면분할 = 0.02)
    - 시가총액 ≈ 상장주식수 × 종가 (1% 넘게 다르면 불일치로 센다)"""
    out: dict[str, dict] = {}
    sub = df[df["Code"].isin(list(codes))].sort_values("Date")
    for code, g in sub.groupby("Code"):
        g = g[g["Close"] > 0]
        g = g[~g["Date"].duplicated(keep="last")]
        if g.empty:
            continue
        base = g["Close"] - g["Changes"]
        prev = g["Close"].shift(1)
        ratio = (base / prev).where((prev > 0) & (base > 0))
        ev = g[(ratio - 1).abs() > ADJ_EVENT_MIN]
        events = [{"date": str(d.date()), "factor": round(float(r), 4)} for d, r in zip(ev["Date"], ratio[ev.index], strict=True)]
        last = g.iloc[-1]
        rec = {"date": str(last["Date"].date()), "close": float(last["Close"]), "marcap": float(last["Marcap"]),
               "market": str(last.get("Market", "")), "adj_events": events[-10:], "n_adj_events": len(events), "rows": int(len(g))}
        if "Stocks" in g and pd.notna(last.get("Stocks")):
            rec["shares"] = float(last["Stocks"])
            implied = g["Stocks"].astype(float) * g["Close"].astype(float)
            ok = implied > 0
            rec["marcap_mismatch"] = int(((g["Marcap"][ok] / implied[ok] - 1).abs() > 0.01).sum())
        out[code] = rec
    return out


def build_krx_dataset(root: str | Path, start_year: int, end_year: int, top_n: int = 100,
                      min_amount: float = 1e9) -> KRXDataset:
    df = load_marcap(root, start_year, end_year)
    elig = point_in_time_universe(df, top_n, min_amount)
    codes = list(elig.columns[elig.any()])
    bars = adjusted_bars(df, codes)
    elig = elig[[c for c in elig.columns if c in bars]]
    names = df.drop_duplicates("Code", keep="last").set_index("Code")["Name"].to_dict()
    extra = {}
    if (df["Market"] == "KOSDAQ").any():
        try:
            extra["KOSDAQ"] = cap_weighted_index(df, "KOSDAQ")
        except (ValueError, ZeroDivisionError) as e:  # 화면용이라 실패해도 적재는 계속
            log.warning("KOSDAQ 대용 지수 계산 실패: %s", e)
    return KRXDataset(bars, elig, cap_weighted_index(df, "KOSPI"), {c: names.get(c, c) for c in bars}, extra, krx_facts(df, list(bars)))


MARCAP_REPO = os.environ.get("QUANT_MARCAP_REPO", "https://github.com/FinanceData/marcap.git")


def default_dir() -> str | None:
    """일봉 자동 갱신에 쓰는 marcap 위치: QUANT_MARCAP_DIR → ./run.sh data 가 받아 둔 기본 위치 (없으면 None = 자동 갱신 불가)."""
    if os.environ.get("QUANT_MARCAP_DIR"):
        return os.environ["QUANT_MARCAP_DIR"]
    home = Path(os.environ.get("QUANT_HOME") or Path.home() / ".quant-ai")
    return next((str(p / "data") for p in (home / "data" / "marcap", Path("data/marcap")) if (p / ".git").exists()), None)


def sync_marcap(data_dir, years: int = 5, today=None, run=None) -> Path:
    """marcap 저장소를 최근 N년 파일만 받아(sparse) 두거나 갱신한다. data_dir = <저장소>/data.

    해가 바뀌면 새해 파일도 받도록 매번 sparse 목록을 다시 설정한다."""
    import subprocess
    from datetime import date as _date
    run = run or (lambda cmd: subprocess.run(cmd, check=True, timeout=1800, capture_output=True))  # noqa: S603
    repo = Path(data_dir).parent
    y = (today or _date.today()).year
    files = [f"/data/marcap-{i}.parquet" for i in range(y - years + 1, y + 1)]
    _clear_stale_lock(repo)
    if not (repo / ".git").exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", MARCAP_REPO, str(repo)])
    else:
        run(["git", "-C", str(repo), "pull", "--ff-only", "-q"])
    _clear_stale_lock(repo)
    run(["git", "-C", str(repo), "sparse-checkout", "set", "--no-cone", *files])
    return repo / "data"


def _clear_stale_lock(repo: Path, max_age_s: float = 600) -> bool:
    """중간에 끊긴 git 이 남긴 .git/index.lock (10분 넘게 된 것) 제거. 남아 있으면 이후 모든 갱신이 실패한다."""
    import time as _time
    lock = repo / ".git" / "index.lock"
    if lock.exists() and _time.time() - lock.stat().st_mtime > max_age_s:
        lock.unlink(missing_ok=True)
        return True
    return False


"""데이터 소스 — 1차(primary) · 2차(secondary) · 교차 검증 · 장애 시 대체.

| 데이터 | 1차 | 2차 (1차가 늦거나 실패할 때) | 교차 검증 |
|---|---|---|---|
| 국내 일봉 | KRX (FinanceData/marcap · 수정주가 · 상장폐지 포함) | Yahoo `.KS/.KQ` 일봉 — 1차 마지막 날 **이후 빈 날만** 채움 | 네이버 실시간 전일종가 · KIS 시세 |
| 미국 일봉 | yfinance | Yahoo chart → Nasdaq → Stooq (순서대로) | — |
| 실시간 시세 | KIS 웹소켓 (설정 시) | 네이버 폴링 → Yahoo | 증권사 ↔ DB 종가 ±30% (guardian) |
| 거시 | FRED | — (없으면 매크로 판단 제외) | — |

2차로 채운 봉은 `source='yahoo-secondary'` 로 표시하고, 1차 소스와 겹치는 날의 종가 비율로 수정주가 기준을 맞춘다.
1차가 다음에 갱신되면 같은 날짜를 덮어써 1차 값으로 돌아온다. 코어 전략의 순위 계산은 겹치는 날 비율이 ±2% 를 넘으면
2차 봉을 쓰지 않는다 (분할·데이터 오류 가능성).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd

from .. import ops

log = logging.getLogger(__name__)
SECONDARY = "yahoo-secondary"
MAX_RATIO_GAP = 0.02
REGISTRY = [
    {"data": "국내 일봉", "primary": "KRX (marcap)", "secondary": "Yahoo .KS/.KQ (빈 날만)", "check": "네이버 전일종가 · KIS 시세"},
    {"data": "미국 일봉", "primary": "yfinance", "secondary": "Yahoo chart → Nasdaq → Stooq", "check": "-"},
    {"data": "실시간 시세", "primary": "KIS 웹소켓", "secondary": "네이버 → Yahoo", "check": "증권사 ↔ DB 종가"},
    {"data": "공시", "primary": "DART", "secondary": "-", "check": "-"},
    {"data": "거시", "primary": "FRED", "secondary": "-", "check": "-"},
    {"data": "휴장일", "primary": "holidays (XKRX·XNYS)", "secondary": "artifacts/holidays.json", "check": "주말만이면 실전 차단 (fail-closed)"},
]


def fetch_yahoo_kr(symbol: str, period: str = "1mo") -> pd.DataFrame:
    import yfinance as yf
    for suf in (".KS", ".KQ"):
        df = yf.Ticker(symbol + suf).history(period=period, auto_adjust=False)
        if df is not None and len(df):
            df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
            idx = pd.DatetimeIndex(df.index)
            df.index = (idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")).normalize()
            return df
    raise RuntimeError(f"Yahoo 에 {symbol} 없음")


def align(primary: pd.DataFrame, secondary: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """1차 마지막 날 이후 2차 봉만, 겹치는 날 종가 비율로 스케일 → (채울 봉, 정보)."""
    if primary is None or primary.empty or secondary is None or secondary.empty:
        return pd.DataFrame(), {"reason": "데이터 없음"}
    p = primary.copy()
    p.index = pd.DatetimeIndex(p.index).normalize()
    s = secondary.copy()
    s.index = pd.DatetimeIndex(s.index).normalize()
    last = p.index.max()
    overlap = [d for d in s.index if d in p.index and d <= last]
    if not overlap:
        return pd.DataFrame(), {"reason": "겹치는 날 없음 — 기준을 맞출 수 없어 채우지 않음"}
    d0 = max(overlap)
    ratio = float(p.loc[d0, "close"]) / float(s.loc[d0, "close"])
    new = s[s.index > last].copy()
    info = {"overlap_day": str(d0.date()), "ratio": round(ratio, 5), "n": int(len(new))}
    # 겹치는 날들의 비율이 흔들리면(분할·오류) 채우지 않는다
    ratios = [float(p.loc[d, "close"]) / float(s.loc[d, "close"]) for d in overlap[-5:]]
    if max(ratios) / min(ratios) - 1 > MAX_RATIO_GAP:
        return pd.DataFrame(), info | {"reason": f"겹치는 날 비율이 {max(ratios) / min(ratios) - 1:.1%} 흔들림 — 분할·데이터 오류 의심"}
    if new.empty:
        return new, info | {"reason": "1차가 최신"}
    for c in ("open", "high", "low", "close"):
        new[c] = new[c] * ratio
    new["volume"] = new["volume"] / ratio
    return new, info | {"reason": "채움"}


def gap_fill(app, symbols: list[str], fetch=None, now: datetime | None = None, max_symbols: int = 60) -> dict:
    """1차(KRX) 일봉이 마지막 거래일보다 늦은 국내 종목만 2차로 빈 날을 채운다."""
    from ..asof import last_trading_close
    from .db import load_bars, session_scope, upsert_bars
    now = now or datetime.now(UTC)
    want = last_trading_close("KR", now)
    done, skipped, failed = [], [], []
    with session_scope(app.engine) as s:
        bars = load_bars(s, [x for x in symbols if x[:1].isdigit()][:max_symbols])
    for sym, b in bars.items():
        if b.empty or pd.Timestamp(b.index.max()).tz_convert("Asia/Seoul").date() >= want:
            continue
        try:
            sec = (fetch or fetch_yahoo_kr)(sym)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{sym}: {type(e).__name__}")
            continue
        new, info = align(b, sec)
        if new.empty:
            skipped.append({"symbol": sym, **info})
            continue
        with session_scope(app.engine) as s:
            upsert_bars(s, sym, new, "1d", SECONDARY)
        done.append({"symbol": sym, **info})
    out = {"at": now.isoformat(), "want": want.isoformat(), "filled": done, "skipped": skipped[:20], "failed": failed[:20]}
    if done or failed:
        ops.set_state(app.engine, "source_failover", out)
        app._md_cache = None
    return out


def cross_check(db_last: dict[str, tuple[str, float]], secondary_prev_close: dict[str, tuple[str, float]], tol: float = 0.01) -> list[dict]:
    """같은 날짜의 1차 종가 vs 2차 전일종가 → 차이가 tol 넘는 종목."""
    out = []
    for sym, (d, c) in db_last.items():
        sd = secondary_prev_close.get(sym)
        if sd and sd[0] == d and c and sd[1]:
            gap = sd[1] / c - 1
            if abs(gap) > tol:
                out.append({"symbol": sym, "date": d, "primary": c, "secondary": sd[1], "gap": round(gap, 4)})
    return out


__all__ = ["REGISTRY", "gap_fill", "align", "cross_check", "fetch_yahoo_kr", "SECONDARY"]

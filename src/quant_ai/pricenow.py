"""v37 '지금 가격'을 한 곳에서 — 모든 화면이 같은 규칙으로 같은 숫자를 보여 주게.

규칙
  1) 실시간(지연 가능) 시세는 '마지막 일봉보다 새것'일 때만 쓴다 (서버가 며칠 꺼졌다 켜진 뒤의 묵은 시세가 새 일봉을 덮지 않게).
  2) 등락 기준 = 그 가격 날짜의 '전 거래일 종가'. 시세 날짜가 마지막 일봉보다 뒤면 마지막 일봉 종가가 기준.
  3) 무엇을 보여 주는지 함께 준다: src(live/close) · label('실시간 10:32' · '09.23 종가') · at.
시세 저장 칸의 시각 이름이 경로마다 달랐다(무료 폴링 'ts' · 예전 코드 'at') → quote_ts 로 둘 다 읽는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import ops

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")


def quote_ts(q: dict | None) -> str | None:
    """시세 시각 (무료 폴링은 'ts', 일부 경로는 'at')."""
    q = q or {}
    return q.get("ts") or q.get("at")


def _tz(sym: str):
    return KST if sym[:1].isdigit() else NY


def _ts(x) -> pd.Timestamp | None:
    if not x:
        return None
    try:
        t = pd.Timestamp(x)
    except (TypeError, ValueError):
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t


def resolve(sym: str, bars: pd.DataFrame | None, q: dict | None, now: datetime | None = None) -> dict:
    """한 종목의 '지금 보여 줄 가격'. bars = 일봉(DataFrame, close 열) · q = live_quotes 의 이 종목 칸."""
    now = now or datetime.now(UTC)
    tz = _tz(sym)
    out: dict = {"symbol": sym, "price": None, "chg_pct": None, "chg": None, "prev_close": None, "src": None, "label": "가격 없음",
                 "at": None, "bar_date": None}
    last = prev = None
    bar_day = None
    if bars is not None and len(bars) and "close" in bars:
        c = bars["close"].astype(float)
        last = float(c.iloc[-1])
        prev = float(c.iloc[-2]) if len(c) > 1 else None
        bt = _ts(bars.index[-1])
        bar_day = bt.date() if bt is not None and bt.hour == 0 and bt.minute == 0 else (bt.tz_convert(tz).date() if bt is not None else None)
        out |= {"price": last, "chg_pct": (last / prev - 1) if prev else None, "chg": (last - prev) if prev else None,
                "prev_close": prev, "src": "close", "label": f"{bar_day:%m.%d} 종가" if bar_day else "종가", "at": str(bar_day) if bar_day else None,
                "bar_date": str(bar_day) if bar_day else None}
    q = q or {}
    qt = _ts(quote_ts(q))
    px = q.get("price")
    if px and qt is not None:
        qd = qt.tz_convert(tz).date()
        if bar_day is None or qd >= bar_day:
            ref = last if (bar_day is not None and qd > bar_day) else prev
            chg_pct = (float(px) / ref - 1) if ref else q.get("chg_pct")
            today = pd.Timestamp(now).tz_convert(tz).date() if pd.Timestamp(now).tzinfo else pd.Timestamp(now, tz="UTC").tz_convert(tz).date()
            hm = qt.tz_convert(tz).strftime("%H:%M")
            out |= {"price": float(px), "chg_pct": chg_pct, "chg": (float(px) - ref) if ref else q.get("chg"), "prev_close": ref,
                    "src": "live", "label": f"실시간 {hm}" if qd == today else f"{qd:%m.%d} {hm} 시세", "at": qt.isoformat(),
                    "quote_src": q.get("src")}
    return out


def for_symbols(app, syms: list[str], now: datetime | None = None, bars: dict | None = None) -> dict[str, dict]:
    """여러 종목 한 번에 (같은 규칙)."""
    if bars is None:
        bars, _ = app._all_bars()
    live = ops.get_state(app.engine, "live_quotes") or {}
    return {s: resolve(s, bars.get(s), live.get(s), now) for s in dict.fromkeys(syms) if s}


__all__ = ["quote_ts", "resolve", "for_symbols"]

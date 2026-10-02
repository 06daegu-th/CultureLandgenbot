"""그날 재현 — 날짜를 고르면 '그날 장 마감 시점까지 알 수 있었던 것'만 다시 보여준다 (복기·검증용).

  지수 · 시장 분위기(상승/하락 종목 수) · 많이 오른/내린 종목 (그날 일봉) — 한국 + 미국(그 날짜의 미국 장)
  그날 나온 뉴스 (톤 · 출처 · 시각 — 그날 23:59 KST 까지 공개된 것만)
  그날 AI 판단 (상승 확률 · 신호) + 그 뒤 실제 결과(나중에 붙은 값은 '결과'로 따로 표시 — 그날은 몰랐던 것)
  그날 가상 장부 평가 · 그날 일정(보관된 종목 일정 + 규칙으로 만든 시장 일정) · 그날 알림
미래 정보가 섞이지 않게: 판단·뉴스·가격은 모두 그 날짜 이전 기록만, 결과 칸만 이후 값.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select

from . import ops
from .asof import label

KST = ZoneInfo("Asia/Seoul")


def day(app, d: date, mode: str = "paper") -> dict:
    from .data.db import session_scope
    from .data.models import AlertRecord, ConsensusRecord, Instrument, NewsArticle, PortfolioSnapshot
    start = datetime.combine(d, datetime.min.time(), KST).astimezone(UTC)
    end = start + timedelta(days=1)
    bars, bench, _ = app.market_data()
    all_bars, benches = app._all_bars()
    rows = _day_rows(bars, d, kr=True)
    us_rows = _day_rows(all_bars, d, kr=False)
    trading = bool(rows)
    with session_scope(app.engine) as s:
        names = {x.symbol: x.name for x in s.scalars(select(Instrument))}
        news = [{"at": label(n.published_at), "title": n.title, "source": n.source, "url": n.url, "sent": round(n.sentiment or 0.0, 2),
                 "symbols": [names.get(x, x) for x in (n.symbols or [])][:3], "summary": (n.extract or {}).get("summary")}
                for n in s.scalars(select(NewsArticle).where(NewsArticle.published_at >= start, NewsArticle.published_at < end)
                                   .order_by(NewsArticle.importance.desc()).limit(30))]
        ai = s.scalars(select(ConsensusRecord).where(ConsensusRecord.as_of >= start - timedelta(hours=12), ConsensusRecord.as_of < end)
                       .order_by(ConsensusRecord.prob_up.desc()).limit(300)).all()
        ai = [{"symbol": r.symbol, "name": names.get(r.symbol, r.symbol), "action": r.action, "prob_up": round(r.prob_up, 3),
               "confidence": round(r.confidence), "made_at": label(r.created_at) if r.created_at else None,
               "later": None if r.realized_return is None else {"ret": round(r.realized_return, 4), "correct": r.correct}} for r in ai]
        snap = s.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode, PortfolioSnapshot.ts < end)
                        .order_by(PortfolioSnapshot.ts.desc()))
        snap = {"equity": round(snap.equity), "cash": round(snap.cash), "n": len(snap.positions or {}), "at": label(snap.ts)} if snap else None
        alerts = [{"at": label(a.ts), "title": a.title, "level": a.level} for a in s.scalars(
            select(AlertRecord).where(AlertRecord.ts >= start, AlertRecord.ts < end).order_by(AlertRecord.ts).limit(20))]
    idx = None
    if bench is not None and len(bench) > 1:
        bi = pd.DatetimeIndex(bench.index)
        t = pd.Timestamp(d)
        t = t.tz_localize(bi.tz) if bi.tz is not None else t
        j = int(bi.searchsorted(t, side="right")) - 1
        if j >= 1 and pd.Timestamp(bi[j]).date() == d:
            idx = {"close": round(float(bench["close"].iloc[j]), 2), "chg": round(float(bench["close"].iloc[j] / bench["close"].iloc[j - 1] - 1), 4)}
    evs = _events_on(app, d)
    rows.sort(key=lambda r: r["chg"])
    up = sum(1 for r in rows if r["chg"] > 0.0005)
    down = sum(1 for r in rows if r["chg"] < -0.0005)
    scored = [a for a in ai if a["later"]]
    hit = sum(1 for a in scored if a["later"]["correct"]) / len(scored) if scored else None
    from .clock import MARKETS
    return {"date": d.isoformat(), "weekday": "월화수목금토일"[d.weekday()], "trading": trading,
            "holiday": MARKETS["KRX"].holiday_name(d), "index": idx, "breadth": {"up": up, "down": down, "n": len(rows)},
            "gainers": [r | {"name": names.get(r["symbol"], r["symbol"])} for r in rows[::-1][:5]],
            "losers": [r | {"name": names.get(r["symbol"], r["symbol"])} for r in rows[:5]],
            "news": news, "ai": ai[:40], "ai_n": len(ai), "ai_hit_later": None if hit is None else round(hit, 3), "book": snap,
            "events": evs[:14], "alerts": alerts, "us": _us_block(us_rows, benches.get("US"), d, names),
            "prev": (d - timedelta(days=1)).isoformat(), "next": (d + timedelta(days=1)).isoformat(),
            "note": "그날 알 수 있던 것만 (가격·뉴스·판단) · '나중 결과' 칸만 그 뒤에 확정된 값"}


def _day_rows(bars: dict, d: date, kr: bool) -> list[dict]:
    rows = []
    for sym, b in bars.items():
        if sym[:1].isdigit() != kr or len(b) < 2 or sym in ("KOSPI", "KOSDAQ"):
            continue
        idx = pd.DatetimeIndex(b.index)
        t = pd.Timestamp(d)
        t = t.tz_localize(idx.tz) if idx.tz is not None else t
        i = int(idx.searchsorted(t, side="right")) - 1  # 그날(또는 그 전 마지막 거래일) 봉
        if i < 1 or pd.Timestamp(idx[i]).date() != d:
            continue
        c = b["close"]
        rows.append({"symbol": sym, "close": float(c.iloc[i]), "chg": float(c.iloc[i] / c.iloc[i - 1] - 1)})
    rows.sort(key=lambda r: r["chg"])
    return rows


def _us_block(rows: list[dict], bench: pd.DataFrame | None, d: date, names: dict) -> dict:
    """미국: 그 날짜(미국 날짜)의 일봉 — 한국 시간으로는 그날 밤~다음 날 새벽 장."""
    from .clock import MARKETS
    idx = None
    if bench is not None and len(bench) > 1:
        bi = pd.DatetimeIndex(bench.index)
        t = pd.Timestamp(d)
        t = t.tz_localize(bi.tz) if bi.tz is not None else t
        j = int(bi.searchsorted(t, side="right")) - 1
        if j >= 1 and pd.Timestamp(bi[j]).date() == d:
            idx = {"close": round(float(bench["close"].iloc[j]), 2), "chg": round(float(bench["close"].iloc[j] / bench["close"].iloc[j - 1] - 1), 4)}
    up = sum(1 for r in rows if r["chg"] > 0.0005)
    down = sum(1 for r in rows if r["chg"] < -0.0005)
    return {"trading": bool(rows) or idx is not None, "holiday": MARKETS["US"].holiday_name(d), "index": idx,
            "breadth": {"up": up, "down": down, "n": len(rows)},
            "gainers": [r | {"name": names.get(r["symbol"], r["symbol"])} for r in rows[::-1][:5]],
            "losers": [r | {"name": names.get(r["symbol"], r["symbol"])} for r in rows[:5]]}


def _events_on(app, d: date) -> list[dict]:
    """그날 일정 = 보관된 일정(종목 실적·공시 등) + 규칙으로 언제든 다시 만들 수 있는 시장 일정(휴장·만기·FOMC·지표)."""
    from .engines import events as E
    seen, out = set(), []
    arch = (ops.get_state(app.engine, "event_archive").get("by_date") or {}).get(d.isoformat()) or []
    cur = [e for e in ops.get_state(app.engine, "event_calendar").get("events") or [] if str(e.get("date"))[:10] == d.isoformat()]
    try:
        gen = E.market_events(d, d) + E.econ_events(d, d, ops.get_state(app.engine, "fred_releases").get("dates"))
    except Exception:  # noqa: BLE001 - 생성 실패해도 보관분은 보인다
        gen = []
    for e in arch + cur + gen:
        key = (e.get("title"), e.get("symbol"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": e.get("title"), "kind": e.get("kind"), "symbol": e.get("symbol"), "market": e.get("market"),
                    "estimated": bool(e.get("estimated"))})
    return out


__all__ = ["day"]

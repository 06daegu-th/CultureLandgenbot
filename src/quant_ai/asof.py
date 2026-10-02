"""AS-OF — 모든 데이터에 '언제 기준인가'를 붙인다. 형식: 2026-09-30 14:32 KST

- label(ts): 사람이 읽는 기준 시각 (KST)
- stamp(ts, kind): {at, label, age_s, age, status(fresh/stale/old/none), sla}
- freshness(app): 데이터 종류별 마지막 시각 · 기대 주기(SLA) 대비 상태 — 화면 상단 '데이터 기준' 과 Readiness DATA 가 쓴다
일봉은 '마지막 거래일'과 비교한다 (주말·휴장일에 어제 봉이 없다고 오래됐다고 하지 않는다).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo("Asia/Seoul")
# 종류 → (정상 기대 주기, 설명). 장중에만 의미 있는 것은 open_only
SLA = {
    "quote": (timedelta(minutes=10), "실시간 시세", True),
    "bar_kr": (None, "국내 일봉", False),
    "bar_us": (None, "미국 일봉", False),
    "bar_index": (None, "지수 일봉", False),
    "news": (timedelta(hours=3), "뉴스", False),
    "disclosure": (timedelta(days=2), "공시", False),
    "macro": (timedelta(days=4), "거시 (FRED)", False),
    "consensus": (timedelta(days=2), "AI 판단", False),
    "evaluation": (timedelta(hours=26), "독립 평가", False),
    "drift": (timedelta(hours=26), "드리프트", False),
    "sector_map": (timedelta(days=14), "업종 지도", False),
    "flow": (timedelta(days=3), "외국인·기관 수급", False),
    "calendar": (timedelta(hours=26), "이벤트 캘린더", False),
    "portfolio": (timedelta(days=3), "장부 스냅샷", False),
    "model": (timedelta(days=45), "모델 학습", False),
    "readiness": (timedelta(hours=2), "매매 준비 점검", False),
}


def _aware(ts) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if isinstance(ts, date) and not isinstance(ts, datetime):
        ts = datetime(ts.year, ts.month, ts.day, tzinfo=KST)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def label(ts, with_time: bool = True) -> str | None:
    t = _aware(ts)
    if t is None:
        return None
    k = t.astimezone(KST)
    return k.strftime("%Y-%m-%d %H:%M KST") if with_time else k.strftime("%Y-%m-%d")


def human_age(sec: float) -> str:
    if sec < 90:
        return "방금"
    if sec < 3600:
        return f"{sec / 60:.0f}분 전"
    if sec < 86400 * 2:
        return f"{sec / 3600:.0f}시간 전"
    return f"{sec / 86400:.0f}일 전"


def last_trading_close(market: str, now: datetime) -> date:
    """now 시점에 '있어야 할' 가장 최근 일봉 날짜 (장 마감 전이면 전 거래일)."""
    from .clock import KRX, US
    cal = KRX if market == "KR" else US
    t = now.astimezone(cal.tz)
    d = t.date()
    if cal.is_trading_day(d):
        _, c, _ = cal.session(d)
        if t.time() >= c:
            return d
    return cal.prev_trading_day(d)


def stamp(ts, kind: str | None = None, now: datetime | None = None, market_open: bool = True, source: str | None = None) -> dict:
    now = now or datetime.now(UTC)
    t = _aware(ts)
    sla, name, open_only = SLA.get(kind or "", (None, kind or "", False))
    if t is None:
        return {"kind": kind, "name": name, "at": None, "label": None, "status": "none", "age": "기록 없음", "source": source}
    age = (now - t).total_seconds()
    status = "fresh"
    if kind in ("bar_kr", "bar_us", "bar_index"):
        mk = "US" if kind == "bar_us" else "KR"
        want = last_trading_close(mk, now)
        have = t.astimezone(KST if mk == "KR" else ZoneInfo("America/New_York")).date()
        from .clock import KRX, US
        lag = (KRX if mk == "KR" else US).trading_days_between(have, want)
        status = "fresh" if lag <= 0 else "stale" if lag <= 2 else "old"
        out_label = label(t, with_time=False)
        return {"kind": kind, "name": name, "at": t.isoformat(), "label": out_label, "status": status,
                "age": "최신 거래일" if lag <= 0 else f"{lag}거래일 밀림", "lag_days": lag, "source": source}
    if sla is not None and not (open_only and not market_open):
        status = "fresh" if age <= sla.total_seconds() else "stale" if age <= 3 * sla.total_seconds() else "old"
    return {"kind": kind, "name": name, "at": t.isoformat(), "label": label(t), "status": status, "age": human_age(age),
            "source": source}


def freshness(app, now: datetime | None = None) -> dict:
    """데이터 종류별 마지막 시각. DB 를 가볍게만 읽는다 (max 집계)."""
    from sqlalchemy import func, select

    from .clock import MARKETS, Phase
    from .data.db import session_scope
    from .data.models import (
        ConsensusRecord,
        Disclosure,
        Instrument,
        MacroObservation,
        ModelRecord,
        NewsArticle,
        PortfolioSnapshot,
        PriceBar,
        QuoteSnapshot,
        SystemState,
    )
    now = now or datetime.now(UTC)
    kr_open = MARKETS["KRX"].phase(now) is Phase.OPEN
    us_open = MARKETS["US"].phase(now) is Phase.OPEN
    out = {}
    with session_scope(app.engine) as s:
        mk = dict(s.execute(select(Instrument.symbol, Instrument.market)).all())
        last = s.execute(select(PriceBar.symbol, func.max(PriceBar.ts)).group_by(PriceBar.symbol)).all()
        groups: dict[str, list] = {"bar_kr": [], "bar_us": [], "bar_index": []}
        for sym, ts in last:
            m = mk.get(sym, "")
            k = "bar_index" if m == "INDEX" else "bar_us" if (m == "GLOBAL" or not sym[:1].isdigit()) else "bar_kr"
            groups[k].append(_aware(ts))
        for k, ts in groups.items():
            if ts:
                # 대부분의 종목이 도달한 날짜 (몇 종목만 늦어도 전체가 최신이 아니게 보이지 않도록 중앙값)
                ts = sorted(ts)
                out[k] = stamp(ts[len(ts) // 2], k, now) | {"n_symbols": len(ts), "oldest": label(ts[0], False),
                                                            "newest": label(ts[-1], False)}
        q = s.scalar(select(func.max(QuoteSnapshot.ts)))
        out["quote"] = stamp(q, "quote", now, market_open=kr_open or us_open)
        out["news"] = stamp(s.scalar(select(func.max(NewsArticle.published_at))), "news", now)
        out["disclosure"] = stamp(s.scalar(select(func.max(Disclosure.filed_at))), "disclosure", now)
        out["macro"] = stamp(s.scalar(select(func.max(MacroObservation.ts))), "macro", now)
        out["consensus"] = stamp(s.scalar(select(func.max(ConsensusRecord.as_of))), "consensus", now)
        out["portfolio"] = stamp(s.scalar(select(func.max(PortfolioSnapshot.ts))), "portfolio", now)
        out["model"] = stamp(s.scalar(select(func.max(ModelRecord.created_at))), "model", now)
        for key, kind in (("evaluation", "evaluation"), ("drift", "drift"), ("sector_map", "sector_map"),
                          ("event_calendar", "calendar"), ("readiness", "readiness")):
            row = s.get(SystemState, key)
            out[kind] = stamp(row.updated_at if row else None, kind, now)
        rows = s.execute(select(SystemState.updated_at).where(SystemState.key.like("flow:%"))).all()
        out["flow"] = stamp(max((r[0] for r in rows), default=None), "flow", now)
    worst = [v for v in out.values() if v["status"] in ("old",) and v["kind"] in ("bar_kr", "bar_index", "quote", "bar_us")]
    return {"now": now.isoformat(), "now_label": label(now), "items": out,
            "status": "old" if worst else "stale" if any(v["status"] == "stale" for v in out.values()) else "fresh",
            "markets": {"KR": kr_open, "US": us_open}}


__all__ = ["label", "stamp", "freshness", "last_trading_close", "KST", "SLA"]

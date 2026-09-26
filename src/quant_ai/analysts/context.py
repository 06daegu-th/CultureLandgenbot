"""MarketContext 조립: DB 의 가격/뉴스/공시/거시/이벤트/RAG 를 모아 AI 입력 패킷을 만든다.

as_of 이후의 정보는 절대 넣지 않는다 (백테스트·복기에서도 같은 코드를 쓰기 위해).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.models import Disclosure, Instrument, MacroObservation, NewsArticle
from .base import MarketContext
from .memory import Memory

PRICE_KEYS = ["ret_1", "ret_5", "ret_20", "vol_20", "vol_ratio", "rsi_14", "dist_ma20", "dist_ma60",
              "volume_z", "high_20_dist", "atr_14"]
RISK_ON_SERIES = {"VIXCLS": -1.0, "DGS10": -0.5, "DEXKOUS": -0.5, "DCOILWTICO": 0.0}


def _clip(text: str | None, n: int) -> str:
    """외부 텍스트 길이 제한 (프롬프트 주입 표면·토큰 비용 축소)."""
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _r(x, nd=4):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def macro_snapshot(session: Session, as_of: datetime) -> dict:
    out: dict = {}
    appetite = []
    for sid in session.scalars(select(MacroObservation.series_id).distinct()):
        rows = session.scalars(
            select(MacroObservation).where(MacroObservation.series_id == sid, MacroObservation.ts <= as_of.date())
            .order_by(MacroObservation.ts.desc()).limit(6)
        ).all()
        if not rows:
            continue
        last, prev = rows[0].value, rows[-1].value
        chg = (last / prev - 1) if prev else 0.0
        out[sid] = {"last": _r(last), "chg_5d": _r(chg), "date": str(rows[0].ts)}
        if sid in RISK_ON_SERIES and RISK_ON_SERIES[sid]:
            appetite.append(np.tanh(20 * chg) * RISK_ON_SERIES[sid])
    if appetite:
        out["_risk_appetite"] = round(float(np.mean(appetite)), 3)  # + 위험선호 / - 위험회피
    return out


def build_context(
    session: Session,
    symbol: str,
    as_of: datetime,
    horizon_days: int,
    bars: pd.DataFrame,
    features_row: pd.Series | None,
    regime_row: pd.Series | None,
    memory: Memory | None = None,
    events: list[dict] | None = None,
    macro: dict | None = None,
    news_hours: int = 72,
    max_news: int = 30,
) -> MarketContext:
    inst = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    hist = bars[bars.index <= pd.Timestamp(as_of)]
    last = hist.iloc[-1] if len(hist) else None
    f = features_row if features_row is not None else pd.Series(dtype=float)

    price = {"last_close": _r(last["close"], 2) if last is not None else None,
             "last_bar": str(hist.index[-1]) if len(hist) else None}
    price.update({k: _r(f.get(k)) for k in PRICE_KEYS if k in f})

    regime = {}
    if regime_row is not None:
        regime = {"regime": regime_row.get("regime"), "trend": _r(regime_row.get("trend")),
                  "vol_pct": _r(regime_row.get("vol_pct")), "drawdown": _r(regime_row.get("drawdown"))}

    since = as_of - timedelta(hours=news_hours)
    news = [
        {"ts": str(n.published_at), "title": _clip(n.title, 200), "sentiment": _r(n.sentiment, 2),
         "importance": _r(n.importance, 2), "events": n.events or [], "source": n.source}
        for n in session.scalars(
            select(NewsArticle).where(NewsArticle.published_at <= as_of, NewsArticle.published_at >= since)
            .order_by(NewsArticle.published_at.desc()).limit(500)
        )
        if symbol in (n.symbols or [])
    ][:max_news]
    discl = [
        {"date": str(d.filed_at), "title": _clip(d.title, 200), "sentiment": _r(d.sentiment, 2), "events": d.events or []}
        for d in session.scalars(
            select(Disclosure).where(Disclosure.symbol == symbol, Disclosure.filed_at <= as_of.date(),
                                     Disclosure.filed_at >= (as_of - timedelta(days=7)).date())
        )
    ]

    # 데이터 품질: 급변(σ) / 정지
    dq = {}
    if f.get("ret_1") is not None and f.get("vol_20"):
        with np.errstate(all="ignore"):
            dq["jump_sigma"] = _r(abs(np.log1p(f["ret_1"])) / f["vol_20"], 2)
    if last is not None and (pd.Timestamp(as_of) - hist.index[-1]) > pd.Timedelta(days=5):
        dq["stale"] = f"마지막 봉 {hist.index[-1].date()}"

    similar = []
    if memory is not None:
        query = f"{symbol} {inst.name if inst else ''} " + " ".join(n["title"] for n in news[:5])
        similar = memory.search(session, query, k=5, before=as_of, symbol=symbol)

    upcoming = [e for e in (events or []) if e.get("symbol") in (None, symbol)]
    return MarketContext(
        symbol=symbol, name=inst.name if inst else None, market=inst.market if inst else "",
        as_of=as_of, horizon_days=horizon_days, price=price, regime=regime, news=news, disclosures=discl,
        macro=macro or {}, upcoming_events=upcoming, similar_past=similar,
        features={k: float(v) for k, v in f.items() if isinstance(v, (int, float, np.floating)) and not pd.isna(v)},
        data_quality=dq,
    )

"""'읽는' 화면 대신 '보는' 화면 — 뉴스 보드 · 증시 지도.

news_board()  같은 소식은 한 장으로 묶고 (매체 수·기사 수), 장마다:
              톤(색) · 확신도 · 이벤트 아이콘 · 한 줄 요약(LLM 있으면) · 왜 이 톤인지(키워드·부정어) · 루머 표시 · 출처 신뢰도 ·
              관련 종목 칩 = 이름 + '뉴스 이후 주가 변화' + 20일 미니 차트 + 지금 AI 판단 · 원문 링크·시각
market_map()  증시 지도: 종목 타일(크기 = 20일 평균 거래대금, 색 = 오늘 등락) 을 업종별로 · 업종 평균 · 상승/하락 종목 수 ·
              20일선 위 비율 · 지수 · 오늘 많이 오른/내린 종목 + 그 종목의 가장 가까운 뉴스('왜 움직였나' 후보)
저장된 기록만 읽는다 (외부 호출 없음).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import ops
from .asof import label
from .news_llm import EVENT_KO, tone_value

TONE = lambda s: "긍정" if (s or 0) > 0.2 else "부정" if (s or 0) < -0.2 else "중립"  # noqa: E731


def _aware(t):
    return t if t is None or t.tzinfo else t.replace(tzinfo=UTC)


def _since(b: pd.DataFrame, pub: datetime) -> float | None:
    """뉴스가 나온 날 직전 종가 → 지금(마지막 종가)."""
    idx = pd.DatetimeIndex(b.index)
    ts = pd.Timestamp(pub)
    ts = ts.tz_convert(idx.tz) if idx.tz is not None else ts.tz_localize(None)
    i = int(idx.searchsorted(ts))
    if i < 1 or i > len(b):
        return None
    ref = float(b["close"].iloc[i - 1])
    return float(b["close"].iloc[-1] / ref - 1) if ref else None


def news_board(app, days: int = 3, only: str | None = None, symbol: str | None = None, limit: int = 40, now: datetime | None = None) -> dict:
    from .data.db import session_scope
    from .data.models import Instrument, NewsArticle
    from .pipeline import latest_consensus
    now = now or datetime.now(UTC)
    bars, _ = app._all_bars()
    with session_scope(app.engine) as s:
        names = {i.symbol: i.name for i in s.scalars(select(Instrument))}
        rows = s.scalars(select(NewsArticle).where(NewsArticle.published_at >= now - timedelta(days=days))
                         .order_by(NewsArticle.published_at.desc()).limit(3000)).all()
        arts = [{"id": a.id, "title": a.title, "source": a.source, "url": a.url, "at": _aware(a.published_at), "sent": tone_value(a.sentiment, a.extract),
                 "imp": a.importance or 0.5, "symbols": a.symbols or [], "events": a.events or [], "ex": a.extract or {},
                 "cluster": a.cluster or f"a{a.id}", "body": (a.body or "")[:200]} for a in rows]
    if symbol:
        arts = [a for a in arts if symbol in a["symbols"]]
    mine = set()
    if only == "mine":
        from .alerts import focus_symbols
        mine = set(focus_symbols(app))
    groups: dict[str, list] = {}
    for a in arts:
        groups.setdefault(a["cluster"], []).append(a)
    cards = []
    for cid, g in groups.items():
        g.sort(key=lambda a: (-(a["ex"].get("source_weight") or 0.7), a["at"]))
        rep = max(g, key=lambda a: (a["ex"].get("by") == "llm", a["ex"].get("source_weight") or 0.7, a["imp"]))
        ws = np.array([a["ex"].get("source_weight") or 0.7 for a in g])
        tone = float(np.average([a["sent"] for a in g], weights=ws)) if len(g) else 0.0
        syms = list(dict.fromkeys(s_ for a in g for s_ in a["symbols"]))
        if only == "mine" and not (set(syms) & mine):
            continue
        ex = rep["ex"]
        if only in ("긍정", "부정", "중립") and TONE(tone) != only:
            continue
        chips = []
        for sym in syms[:4]:
            b = bars.get(sym)
            rec = latest_consensus(app.engine, sym)
            chips.append({"symbol": sym, "name": names.get(sym, sym),
                          "since": None if b is None else _since(b, min(a["at"] for a in g)),
                          "spark": [round(float(x), 2) for x in b["close"].iloc[-20:]] if b is not None and len(b) else [],
                          "ai": rec.action if rec else None})
        ev = ex.get("event") or (rep["events"] or ["other"])[0]
        cards.append({"cluster": cid, "title": rep["title"], "summary": ex.get("summary"), "by": ex.get("by", "rule"),
                      "tone": TONE(tone), "score": round(tone, 2), "confidence": ex.get("confidence"), "event": ev, "event_ko": EVENT_KO.get(ev, ev),
                      "rumor": bool(ex.get("rumor")), "why": ex.get("matched") or [], "n": len(g),
                      "sources": sorted({a["source"] for a in g if a["source"]}), "trust": round(float(ws.max()), 2),
                      "first": label(min(a["at"] for a in g)), "last_at": max(a["at"] for a in g).isoformat(),
                      "articles": [{"title": a["title"], "source": a["source"], "url": a["url"], "at": label(a["at"])} for a in g[:6]],
                      "symbols": chips, "importance": round(max(a["imp"] for a in g), 2)})
    cards.sort(key=lambda c: (c["last_at"]), reverse=True)
    cards = sorted(cards[:200], key=lambda c: -(c["importance"] + 0.15 * min(c["n"], 5) + 0.3 * abs(c["score"])))[:limit]
    cards.sort(key=lambda c: c["last_at"], reverse=True)
    cnt = {t: sum(1 for c in cards if c["tone"] == t) for t in ("긍정", "중립", "부정")}
    st = ops.get_state(app.engine, "news_extract")
    return {"cards": cards, "counts": cnt, "n_articles": len(arts), "days": days, "as_of": label(now),
            "extract": {"llm_on": st.get("llm_on"), "at": label(st.get("at")) if st.get("at") else None, "llm": st.get("llm")},
            "note": "톤 = 매체 신뢰도로 가중한 평균 · 같은 소식 여러 기사는 한 장 · '뉴스 이후' = 뉴스 전날 종가 → 마지막 종가 (인과 아님)"}


def market_map(app, now: datetime | None = None) -> dict:
    from .data.db import session_scope
    from .data.models import Instrument, NewsArticle
    from .engines.sector import sector_map
    now = now or datetime.now(UTC)
    bars, bench, _ = app.market_data()
    sectors = sector_map(app.engine)
    with session_scope(app.engine) as s:
        insts = list(s.scalars(select(Instrument)))
        names = {i.symbol: i.name for i in insts}
        for i in insts:  # WICS·Yahoo 업종이 없으면 종목 정보의 업종
            if i.sector and not sectors.get(i.symbol):
                sectors[i.symbol] = i.sector
        recent = [(a.published_at, a.title, a.symbols or [], tone_value(a.sentiment, a.extract)) for a in s.scalars(
            select(NewsArticle).where(NewsArticle.published_at >= now - timedelta(days=4)).order_by(NewsArticle.published_at.desc()).limit(3000))]
    tiles = []
    for sym, b in bars.items():
        if len(b) < 21 or not sym[:1].isdigit():
            continue
        c = b["close"].astype(float)
        val = float((c * b["volume"].astype(float)).iloc[-20:].mean())
        tiles.append({"symbol": sym, "name": names.get(sym, sym), "sector": sectors.get(sym) or "미분류", "last": float(c.iloc[-1]),
                      "chg": float(c.iloc[-1] / c.iloc[-2] - 1), "chg5": float(c.iloc[-1] / c.iloc[-6] - 1) if len(c) > 6 else None,
                      "above20": bool(c.iloc[-1] > c.iloc[-20:].mean()), "value": val, "date": str(pd.Timestamp(b.index[-1]).date())})
    if not tiles:
        return {"tiles": [], "sectors": [], "message": "국내 일봉이 없습니다 (./run.sh data)"}
    latest = max(t["date"] for t in tiles)
    n_stale = sum(1 for t in tiles if t["date"] != latest)
    tiles = [t for t in tiles if t["date"] == latest]  # 마지막 거래일 봉이 없는 종목(거래정지·상장폐지·수집 누락)은 빼고 따로 센다
    sector_note = None
    if all(t["sector"] == "미분류" for t in tiles):  # 업종 정보가 하나도 없으면 거래대금 순위로 묶는다 (정직하게 표시)
        ranked = sorted(tiles, key=lambda t: -t["value"])
        for k, t in enumerate(ranked):
            t["sector"] = "거래대금 상위 30" if k < 30 else "31~100위" if k < 100 else "그 외"
        sector_note = "업종 정보가 없어(WICS 미수집) 거래대금 순위로 묶었습니다 — 네트워크가 되면 업종으로 바뀝니다"
    tot = sum(t["value"] for t in tiles) or 1
    for t in tiles:
        t["weight"] = round(t["value"] / tot, 5)
    secs = {}
    for t in tiles:
        x = secs.setdefault(t["sector"], {"sector": t["sector"], "value": 0.0, "wchg": 0.0, "n": 0})
        x["value"] += t["value"]
        x["wchg"] += t["chg"] * t["value"]
        x["n"] += 1
    sec_rows = sorted(({"sector": k, "n": v["n"], "chg": round(v["wchg"] / v["value"], 4) if v["value"] else 0.0,
                        "weight": round(v["value"] / tot, 4)} for k, v in secs.items()), key=lambda r: -r["weight"])
    up = sum(1 for t in tiles if t["chg"] > 0.0005)
    down = sum(1 for t in tiles if t["chg"] < -0.0005)

    def why(sym):
        hit = next(((t, ti, sv) for t, ti, ss, sv in recent if sym in ss), None)
        return {"title": hit[1], "at": label(hit[0]), "tone": TONE(hit[2])} if hit else None
    movers = sorted(tiles, key=lambda t: t["chg"])
    gain = [t | {"news": why(t["symbol"])} for t in movers[::-1][:6]]
    lose = [t | {"news": why(t["symbol"])} for t in movers[:6]]
    idx = None
    if bench is not None and len(bench) > 21:
        bc = bench["close"].astype(float)
        idx = {"name": names.get("KOSPI") or "KOSPI", "last": round(float(bc.iloc[-1]), 2), "chg": round(float(bc.iloc[-1] / bc.iloc[-2] - 1), 4),
               "chg20": round(float(bc.iloc[-1] / bc.iloc[-21] - 1), 4), "spark": [round(float(x), 2) for x in bc.iloc[-60:]]}
    mood = ("상승 우세" if up > down * 1.5 else "하락 우세" if down > up * 1.5 else "혼조")
    return {"tiles": sorted(tiles, key=lambda t: -t["value"]), "sectors": sec_rows, "breadth": {"up": up, "down": down, "flat": len(tiles) - up - down,
            "above20": round(sum(t["above20"] for t in tiles) / len(tiles), 3), "mood": mood},
            "gainers": gain, "losers": lose, "index": idx, "date": latest, "as_of": label(now), "n_stale": n_stale, "sector_note": sector_note,
            "note": "타일 크기 = 20일 평균 거래대금 · 색 = 마지막 거래일 등락 · '왜 움직였나'는 가장 가까운 뉴스(인과 아님)"}


__all__ = ["news_board", "market_map"]

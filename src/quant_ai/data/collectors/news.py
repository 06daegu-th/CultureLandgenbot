"""뉴스(RSS) 수집. 피드 URL 은 QUANT_NEWS_FEEDS 로 지정한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import defusedxml.ElementTree as ET  # XML 폭탄(billion laughs)·외부 엔티티 공격 방어
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...engines.news_intel import NewsAnalyzer, tag_symbols
from ..models import Instrument, NewsArticle
from . import http

log = logging.getLogger("quant_ai.news")


@dataclass
class RawArticle:
    source: str
    url: str
    title: str
    body: str
    published_at: datetime


def _parse_date(text: str | None) -> datetime:
    if not text:
        return datetime.now(UTC)
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_feed(xml_bytes: bytes, source: str) -> list[RawArticle]:
    """RSS 2.0 / Atom 둘 다 처리."""
    root = ET.fromstring(xml_bytes)
    out: list[RawArticle] = []
    atom = "{http://www.w3.org/2005/Atom}"
    for item in root.iter("item"):
        out.append(RawArticle(
            source=source,
            url=(item.findtext("link") or "").strip(),
            title=(item.findtext("title") or "").strip(),
            body=(item.findtext("description") or "").strip(),
            published_at=_parse_date(item.findtext("pubDate")),
        ))
    for entry in root.iter(f"{atom}entry"):
        link = entry.find(f"{atom}link")
        out.append(RawArticle(
            source=source,
            url=(link.get("href") if link is not None else "") or "",
            title=(entry.findtext(f"{atom}title") or "").strip(),
            body=(entry.findtext(f"{atom}summary") or "").strip(),
            published_at=_parse_date(entry.findtext(f"{atom}updated") or entry.findtext(f"{atom}published")),
        ))
    # 링크는 http(s) 만 허용 (javascript:, data: 등 UI 삽입 방지)
    return [a for a in out if a.url.startswith(("http://", "https://")) and a.title]


class NewsCollector:
    def __init__(self, feeds: tuple[str, ...], analyzer: NewsAnalyzer | None = None, fetch=http.get):
        self.feeds = feeds
        self.analyzer = analyzer or NewsAnalyzer()
        self.fetch = fetch

    def collect(self, session: Session) -> int:
        aliases = {
            i.symbol: [i.symbol, *(i.keywords or []), *([i.name] if i.name else [])]
            for i in session.scalars(select(Instrument))
        }
        n = 0
        for feed in self.feeds:
            try:
                articles = parse_feed(self.fetch(feed), source=feed)
            except Exception as exc:  # noqa: BLE001 - 피드 하나가 실패해도 나머지는 계속
                log.warning("피드 수집 실패 %s: %s", feed, exc)
                continue
            for a in articles:
                if session.scalar(select(NewsArticle.id).where(NewsArticle.url == a.url)):
                    continue
                result = self.analyzer.analyze(a.title, a.body)
                session.add(NewsArticle(
                    source=a.source, url=a.url, published_at=a.published_at, title=a.title, body=a.body,
                    symbols=tag_symbols(f"{a.title} {a.body}", aliases),
                    sentiment=result.sentiment, events=result.events, importance=result.importance,
                    collected_at=datetime.now(UTC),
                ))
                session.flush()
                n += 1
        return n


# v26: 종목별 뉴스 — 일반 경제 RSS 만으로는 덜 알려진 종목에 뉴스가 거의 안 붙는다.
# 관심·보유 종목마다 구글뉴스 검색 RSS (키 불필요) 를 받아 그 종목으로 바로 태그한다.
GNEWS_KR = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
GNEWS_US = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def stock_feed_url(symbol: str, name: str | None) -> str:
    from urllib.parse import quote
    if symbol[:1].isdigit():
        q = f'"{name or symbol}" 주가 OR 실적 OR 공시'
        return GNEWS_KR.format(q=quote(q))
    q = f"{symbol} stock" + (f' OR "{name}"' if name and name != symbol and name.isascii() else "")
    return GNEWS_US.format(q=quote(q))


def collect_stock_news(session: Session, symbols: list[str], analyzer: NewsAnalyzer | None = None, fetch=http.get,
                       per_stock: int = 15) -> dict:
    """관심·보유 종목별 최신 기사 (종목당 최대 per_stock 건). 이미 있는 링크는 건너뛰고, 기사에 그 종목 태그를 더한다."""
    from ..global_stocks import GLOBAL_STOCKS
    analyzer = analyzer or NewsAnalyzer()
    names = {i.symbol: i.name for i in session.scalars(select(Instrument).where(Instrument.symbol.in_(symbols or [""])))}
    ko = {s: n for s, n, *_ in GLOBAL_STOCKS}
    added, failed = 0, []
    for sym in symbols[:25]:
        name = names.get(sym) if names.get(sym) and names.get(sym) != sym else None
        if not sym[:1].isdigit():
            from ...companies import identity
            name = identity(None, sym).get("name_en") or name
        try:
            arts = parse_feed(fetch(stock_feed_url(sym, name or ko.get(sym))), source="구글뉴스")[:per_stock]
        except Exception as exc:  # noqa: BLE001 - 한 종목 실패는 다음 회차에
            failed.append(f"{sym}: {type(exc).__name__}")
            continue
        for a in arts:
            old = session.scalar(select(NewsArticle).where(NewsArticle.url == a.url))
            if old:
                if sym not in (old.symbols or []):
                    old.symbols = [*(old.symbols or []), sym]
                continue
            src = a.title.rsplit(" - ", 1)[-1][:60] if " - " in a.title else "구글뉴스"  # 구글뉴스 제목 끝의 '- 매체명'
            title = a.title.rsplit(" - ", 1)[0] if " - " in a.title else a.title
            r = analyzer.analyze(title, "")
            session.add(NewsArticle(source=src, url=a.url, published_at=a.published_at, title=title, body="", symbols=[sym],
                                    sentiment=r.sentiment, events=r.events, importance=r.importance, collected_at=datetime.now(UTC)))
            session.flush()
            added += 1
    return {"added": added, "failed": failed[:10], "symbols": len(symbols[:25])}

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
                ))
                session.flush()
                n += 1
        return n

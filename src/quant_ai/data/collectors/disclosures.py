"""DART(전자공시) 공시 목록 수집. DART_API_KEY 필요 (https://opendart.fss.or.kr)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...engines.news_intel import NewsAnalyzer
from ..models import Disclosure
from . import http

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"


def parse_dart_list(payload: dict) -> list[dict]:
    status = payload.get("status")
    if status == "013":  # 조회된 데이터 없음
        return []
    if status != "000":
        raise RuntimeError(f"DART 오류 {status}: {payload.get('message')}")
    out = []
    for row in payload.get("list", []):
        out.append({
            "receipt_no": row["rcept_no"],
            "corp_code": row.get("corp_code"),
            "corp_name": row.get("corp_name"),
            "symbol": (row.get("stock_code") or "").strip() or None,
            "title": row.get("report_nm", "").strip(),
            "filed_at": datetime.strptime(row["rcept_dt"], "%Y%m%d").date(),
            "url": DART_VIEW_URL.format(row["rcept_no"]),
        })
    return out


class DartCollector:
    def __init__(self, api_key: str, analyzer: NewsAnalyzer | None = None, fetch_json=http.get_json):
        self.api_key = api_key
        self.analyzer = analyzer or NewsAnalyzer()
        self.fetch_json = fetch_json

    def collect(self, session: Session, start: date, end: date, max_pages: int = 10) -> int:
        n = 0
        for page in range(1, max_pages + 1):
            payload = self.fetch_json(DART_LIST_URL, {
                "crtfc_key": self.api_key,
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "page_no": page,
                "page_count": 100,
            })
            rows = parse_dart_list(payload)
            for row in rows:
                if session.scalar(select(Disclosure.id).where(Disclosure.receipt_no == row["receipt_no"])):
                    continue
                result = self.analyzer.analyze(row["title"], "")
                session.add(Disclosure(source="DART", sentiment=result.sentiment, events=result.events,
                                       collected_at=datetime.now(UTC), **row))
                session.flush()
                n += 1
            if page >= int(payload.get("total_page", 1)):
                break
        return n

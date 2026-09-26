"""경제지표 수집 (FRED). FRED_API_KEY 필요 (https://fred.stlouisfed.org).

기본 시리즈: 미 10년물, 2년물, 달러/원, VIX, 연방기금금리, WTI.
한국은행 ECOS 등 다른 소스도 같은 형태(``series_id, ts, value``)로 추가하면 된다.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MacroObservation
from . import http

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_SERIES = ("DGS10", "DGS2", "DEXKOUS", "VIXCLS", "DFF", "DCOILWTICO")


def parse_fred(payload: dict) -> list[tuple[date, float]]:
    out = []
    for obs in payload.get("observations", []):
        if obs.get("value") in (None, "", "."):  # FRED 는 결측을 "." 로 표시
            continue
        out.append((datetime.strptime(obs["date"], "%Y-%m-%d").date(), float(obs["value"])))
    return out


class FredCollector:
    def __init__(self, api_key: str, series: tuple[str, ...] = DEFAULT_SERIES, fetch_json=http.get_json):
        self.api_key = api_key
        self.series = series
        self.fetch_json = fetch_json

    def collect(self, session: Session, start: date) -> int:
        n = 0
        for sid in self.series:
            payload = self.fetch_json(FRED_URL, {
                "series_id": sid, "api_key": self.api_key, "file_type": "json",
                "observation_start": start.isoformat(),
            })
            have = set(session.scalars(
                select(MacroObservation.ts).where(MacroObservation.series_id == sid, MacroObservation.ts >= start)
            ))
            for ts, value in parse_fred(payload):
                if ts in have:
                    continue
                session.add(MacroObservation(series_id=sid, source="FRED", ts=ts, value=value))
                n += 1
        return n

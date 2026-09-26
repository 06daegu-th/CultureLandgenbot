"""시장 세션 판단 (장중 / 장외).

국내(KRX)와 미국(US) 정규장 시간을 기준으로 현재 시점이 어느 단계인지 알려준다.
휴장일은 ``holidays`` 로 주입한다 (거래소 휴장일 캘린더를 DB/파일에서 불러와 넣으면 된다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo


class Phase(str, Enum):
    PRE_OPEN = "pre_open"  # 장 시작 전 준비 구간
    OPEN = "open"  # 장중
    CLOSED = "closed"  # 장외


@dataclass(frozen=True)
class MarketCalendar:
    name: str
    tz: ZoneInfo
    open_time: time
    close_time: time
    pre_open_minutes: int = 30
    holidays: frozenset[date] = field(default_factory=frozenset)

    def local(self, now: datetime) -> datetime:
        if now.tzinfo is None:
            raise ValueError("timezone-aware datetime 이 필요합니다")
        return now.astimezone(self.tz)

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def phase(self, now: datetime) -> Phase:
        t = self.local(now)
        if not self.is_trading_day(t.date()):
            return Phase.CLOSED
        open_dt = datetime.combine(t.date(), self.open_time, self.tz)
        close_dt = datetime.combine(t.date(), self.close_time, self.tz)
        if open_dt <= t < close_dt:
            return Phase.OPEN
        if open_dt - timedelta(minutes=self.pre_open_minutes) <= t < open_dt:
            return Phase.PRE_OPEN
        return Phase.CLOSED

    def next_trading_day(self, d: date) -> date:
        d = d + timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d


KRX = MarketCalendar("KRX", ZoneInfo("Asia/Seoul"), time(9, 0), time(15, 30))
US = MarketCalendar("US", ZoneInfo("America/New_York"), time(9, 30), time(16, 0))

MARKETS = {"KRX": KRX, "US": US}


def any_market_open(now: datetime, markets: dict[str, MarketCalendar] = MARKETS) -> bool:
    return any(m.phase(now) is Phase.OPEN for m in markets.values())

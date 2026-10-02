"""시장 세션 판단 (장중 / 장외).

국내(KRX)와 미국(US) 정규장 시간을 기준으로 현재 시점이 어느 단계인지 알려준다.
휴장일: ``holidays`` 패키지의 거래소 캘린더(XKRX · XNYS, 대체공휴일·임시공휴일 규칙 포함)를 기본으로 쓰고,
``artifacts/holidays.json`` 으로 추가·보정한다 (선거일·임시휴장 등 공지로 바뀌는 날).
특수 시간: KRX 새해 첫 거래일 10:00 개장 · 미국 조기 폐장(추수감사절 다음 날 · 7/3 · 12/24 → 13:00).
패키지가 없으면 주말만 휴장으로 본다 (그 사실을 calendar_source 로 알린다).
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


HOLIDAY_NAMES: dict[str, dict[date, str]] = {"KRX": {}, "US": {}}


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

    def holiday_name(self, d: date) -> str | None:
        if d.weekday() >= 5:
            return None
        return HOLIDAY_NAMES.get(self.name, {}).get(d) or ("휴장" if d in self.holidays else None)

    def session(self, d: date) -> tuple[time, time, str | None]:
        """그날의 (개장, 폐장, 특이사항). 휴장일이어도 정규 시간을 돌려준다 (is_trading_day 로 먼저 확인)."""
        if self.name == "KRX":
            first = date(d.year, 1, 1)
            while not self.is_trading_day(first):
                first += timedelta(days=1)
            if d == first:
                return time(10, 0), self.close_time, "새해 첫 거래일 10:00 개장"
        if self.name == "US":
            thanks = _nth_weekday(d.year, 11, 3, 4)
            early = {thanks + timedelta(days=1): "추수감사절 다음 날 조기 폐장",
                     date(d.year, 7, 3): "독립기념일 전날 조기 폐장", date(d.year, 12, 24): "크리스마스 이브 조기 폐장"}
            if d in early and self.is_trading_day(d):
                return self.open_time, time(13, 0), early[d]
        return self.open_time, self.close_time, None

    def phase(self, now: datetime) -> Phase:
        t = self.local(now)
        if not self.is_trading_day(t.date()):
            return Phase.CLOSED
        o, c, _ = self.session(t.date())
        open_dt = datetime.combine(t.date(), o, self.tz)
        close_dt = datetime.combine(t.date(), c, self.tz)
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

    def prev_trading_day(self, d: date) -> date:
        d = d - timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def trading_days_between(self, a: date, b: date) -> int:
        """a 다음 날부터 b 까지의 거래일 수 (a<b). b<=a 면 0 이하."""
        if b <= a:
            return -self.trading_days_between(b, a) if b < a else 0
        n, d = 0, a
        while d < b:
            d += timedelta(days=1)
            n += self.is_trading_day(d)
        return n


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """month 의 n 번째 weekday (월=0 … 금=4)."""
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _exchange_holidays(code: str, years) -> dict[date, str]:
    try:
        import holidays as _h
        return dict(_h.financial_holidays(code, years=list(years), language="ko" if code == "XKRX" else "en_US"))
    except Exception:  # noqa: BLE001 - 패키지 없음/미지원 → 주말만
        try:
            import holidays as _h
            return dict(_h.financial_holidays(code, years=list(years)))
        except Exception:  # noqa: BLE001
            return {}


def _build(name, code, tz, o, c) -> MarketCalendar:
    y = date.today().year
    hs = _exchange_holidays(code, range(y - 16, y + 3))
    HOLIDAY_NAMES[name] = hs
    return MarketCalendar(name, ZoneInfo(tz), o, c, holidays=frozenset(hs))


KRX = _build("KRX", "XKRX", "Asia/Seoul", time(9, 0), time(15, 30))
US = _build("US", "XNYS", "America/New_York", time(9, 30), time(16, 0))
CALENDAR_SOURCE = "holidays 패키지 (XKRX · XNYS)" if HOLIDAY_NAMES["KRX"] else "주말만 (holidays 패키지 없음)"

MARKETS = {"KRX": KRX, "US": US}


def detail_session(cal: MarketCalendar, now: datetime) -> tuple[str, str]:
    """세부 세션 (코드, 한국어). 정규장 외 시간대까지 — 주문 가능 여부와 가격 신뢰도가 다르다.
    KRX: 장 시작 전 · 장전 동시호가(개장 30분 전) · 장중 · 장마감 동시호가(마지막 10분) · 시간외(~18:00) · 장마감 · 휴장
    미국: 프리마켓(04:00~) · 장중 · 애프터마켓(~20:00, 조기 폐장일은 폐장+4시간) · 장마감 · 휴장"""
    t = cal.local(now)
    if not cal.is_trading_day(t.date()):
        return "holiday", "휴장"
    o, c, _ = cal.session(t.date())
    od, cd = datetime.combine(t.date(), o, cal.tz), datetime.combine(t.date(), c, cal.tz)
    if cal.name == "KRX":
        if t < od - timedelta(minutes=30):
            return "before", "장 시작 전"
        if t < od:
            return "pre_auction", "장전 동시호가"
        if t < cd - timedelta(minutes=10):
            return "regular", "장중"
        if t < cd:
            return "close_auction", "장마감 동시호가"
        if t < datetime.combine(t.date(), time(18, 0), cal.tz):
            return "after_hours", "시간외"
        return "closed", "장마감"
    if t < datetime.combine(t.date(), time(4, 0), cal.tz):
        return "closed", "장마감"
    if t < od:
        return "pre_market", "프리마켓"
    if t < cd:
        return "regular", "장중"
    if t < cd + timedelta(hours=4):
        return "after_hours", "애프터마켓"
    return "closed", "장마감"


def next_open(cal: MarketCalendar, now: datetime) -> datetime:
    """지금 이후 처음 열리는 시각 (장중이면 다음 거래일 개장)."""
    t = cal.local(now)
    d = t.date()
    for _ in range(30):
        if cal.is_trading_day(d):
            o, _c, _n = cal.session(d)
            dt = datetime.combine(d, o, cal.tz)
            if dt > t:
                return dt
        d += timedelta(days=1)
    raise RuntimeError("30일 안에 개장일 없음 — 캘린더 확인")


def next_close(cal: MarketCalendar, now: datetime) -> datetime:
    """지금 이후 처음 닫히는 시각 (장중이면 오늘 폐장)."""
    t = cal.local(now)
    d = t.date()
    for _ in range(30):
        if cal.is_trading_day(d):
            _o, c, _n = cal.session(d)
            dt = datetime.combine(d, c, cal.tz)
            if dt > t:
                return dt
        d += timedelta(days=1)
    raise RuntimeError("30일 안에 폐장 없음 — 캘린더 확인")


def clock_status(now: datetime, markets: dict[str, MarketCalendar] | None = None) -> dict:
    """시장 시계: 시장별 단계 · 현지 시각 · 오늘 세션 · 다음 개장/폐장 · 남은 시간 · 휴장 이름 · 특이사항."""
    from zoneinfo import ZoneInfo
    kst = ZoneInfo("Asia/Seoul")
    out = {}
    for key, cal in (markets or MARKETS).items():
        t = cal.local(now)
        d = t.date()
        trading = cal.is_trading_day(d)
        o, c, note = cal.session(d)
        no, nc = next_open(cal, now), next_close(cal, now)
        ph = cal.phase(now)
        nxt = nc if ph is Phase.OPEN else no
        sc, sl = detail_session(cal, now)
        out[key] = {
            "name": "한국 (KRX)" if key == "KRX" else "미국 (NYSE·Nasdaq)", "tz": str(cal.tz), "phase": ph.value,
            "flag": "🇰🇷" if key == "KRX" else "🇺🇸", "short": "KRX" if key == "KRX" else "NASDAQ",
            "session_code": sc, "session_label": sl,
            "local_time": t.strftime("%Y-%m-%d %H:%M"), "trading_day": trading, "holiday": None if trading else cal.holiday_name(d) or "주말",
            "session": {"open": o.strftime("%H:%M"), "close": c.strftime("%H:%M"), "note": note} if trading else None,
            "next_open": no.isoformat(), "next_open_kst": no.astimezone(kst).strftime("%m-%d %H:%M KST"),
            "next_close": nc.isoformat(), "next_close_kst": nc.astimezone(kst).strftime("%m-%d %H:%M KST"),
            "next_event": "폐장" if ph is Phase.OPEN else "개장", "seconds_to_next": int((nxt - t).total_seconds()),
        }
    return {"now": now.isoformat(), "now_kst": now.astimezone(kst).strftime("%Y-%m-%d %H:%M:%S KST"), "markets": out,
            "calendar_source": CALENDAR_SOURCE, "calendar_ok": bool(HOLIDAY_NAMES.get("KRX")) and bool(HOLIDAY_NAMES.get("US"))}


def any_market_open(now: datetime, markets: dict[str, MarketCalendar] = MARKETS) -> bool:
    return any(m.phase(now) is Phase.OPEN for m in markets.values())


def load_holidays(path) -> dict[str, MarketCalendar]:
    """휴장일 파일로 캘린더 갱신. 형식: {"KRX": ["2026-01-01", ...], "US": [...]}

    거래소 휴장일은 매년 공지(대체공휴일·선거일 등)로 바뀌므로 코드에 하드코딩하지 않는다.
    """
    import json
    from dataclasses import replace
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return dict(MARKETS)
    data = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for k, cal in MARKETS.items():
        days = frozenset(date.fromisoformat(x) for x in data.get(k, []))
        out[k] = replace(cal, holidays=cal.holidays | days)
    return out

"""이벤트 캘린더 2.0 — 종목 · 시장 · 경제 · 실적 · 배당 · 공시 · 옵션 만기를 한 달력에.

출처와 확실성을 항상 같이 적는다 (estimated=True 는 규칙으로 추정한 날짜).
  · 시장: 거래소 휴장일(holidays 패키지) · 조기 폐장 · 새해 10시 개장
  · 파생: KOSPI200 옵션 만기(매월 둘째 목요일, 휴장이면 앞 거래일) · 3·6·9·12월 = 동시만기
          미국 월물 옵션 만기(셋째 금요일) · 3·6·9·12월 = 쿼드러플 위칭
  · 지수: KOSPI200 정기변경(6·12월 동시만기 다음 거래일) · MSCI 분기 리뷰(2·5·8·11월 마지막 거래일, 추정)
  · 경제: FOMC(연준 공표 일정) · 미국 고용(첫째 금요일, 추정) · FRED 발표 일정(키가 있으면 CPI·고용·GDP·PCE 공식 날짜)
          한국 수출입 동향(매월 1일, 추정)
  · 종목: 실적 발표 · 배당락 · 배당 지급 (종목 상세 캐시: Yahoo/Nasdaq/DART) · 최근 공시
  · 사용자: artifacts/events.json
이벤트 위험: 종목마다 '다가오는 이벤트의 크기 × 가까움'을 점수로 → 실적 직전 매수는 줄이거나 보류 (RiskEngine.event_caps)
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ..clock import KRX, US, _nth_weekday

log = logging.getLogger(__name__)

# 연준 공표 FOMC 일정 (결정 발표일 = 회의 둘째 날). 바뀌면 artifacts/events.json 으로 덮어쓴다.
FOMC_DATES = [
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]
# FRED 발표 일정 id → (이름, 중요도)
FRED_RELEASES = {10: ("미국 CPI (소비자물가)", 0.9), 50: ("미국 고용보고서 (비농업 고용)", 0.85),
                 53: ("미국 GDP", 0.7), 54: ("미국 PCE 물가 · 개인소득", 0.75), 9: ("미국 소매판매", 0.6)}
IMPORTANCE = {"fomc": 1.0, "cpi": 0.9, "nfp": 0.85, "earnings": 0.9, "quad_witching": 0.7, "options_expiry": 0.45,
              "index_rebalance": 0.6, "holiday": 0.3, "half_day": 0.3, "ex_div": 0.35, "div_pay": 0.1,
              "export": 0.5, "gdp": 0.7, "pce": 0.75, "retail": 0.6, "disclosure": 0.5, "custom": 0.6}
KIND_LABEL = {"fomc": "FOMC", "cpi": "CPI", "nfp": "고용", "earnings": "실적", "quad_witching": "동시만기",
              "options_expiry": "옵션만기", "index_rebalance": "지수 변경", "holiday": "휴장", "half_day": "조기폐장",
              "ex_div": "배당락", "div_pay": "배당지급", "export": "수출입", "gdp": "GDP", "pce": "PCE",
              "retail": "소매판매", "disclosure": "공시", "custom": "사용자", "econ": "경제지표"}
MARKET_WIDE = {"fomc", "cpi", "nfp", "quad_witching", "options_expiry", "index_rebalance", "holiday", "half_day",
               "export", "gdp", "pce", "retail", "econ"}


def _ev(d: date, kind: str, title: str, market: str, source: str, estimated: bool = False, symbol: str | None = None,
        importance: float | None = None, time_: str | None = None, **extra) -> dict:
    return {"date": d.isoformat(), "kind": kind, "label": KIND_LABEL.get(kind, kind), "title": title, "market": market,
            "symbol": symbol, "importance": IMPORTANCE.get(kind, 0.5) if importance is None else importance,
            "estimated": estimated, "source": source, "time": time_, **extra}


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _on_or_before_trading(cal, d: date) -> date:
    while not cal.is_trading_day(d):
        d -= timedelta(days=1)
    return d


def kr_options_expiry(y: int, m: int) -> date:
    return _on_or_before_trading(KRX, _nth_weekday(y, m, 3, 2))


def us_options_expiry(y: int, m: int) -> date:
    return _on_or_before_trading(US, _nth_weekday(y, m, 4, 3))


def market_events(start: date, end: date) -> list[dict]:
    out = []
    for cal, mk in ((KRX, "KR"), (US, "US")):
        d = start
        while d <= end:
            if d.weekday() < 5:
                if not cal.is_trading_day(d):
                    out.append(_ev(d, "holiday", f"{'한국' if mk == 'KR' else '미국'} 휴장 · {cal.holiday_name(d) or ''}".rstrip(" ·"),
                                   mk, "거래소 휴장일"))
                else:
                    _, c, note = cal.session(d)
                    if note:
                        out.append(_ev(d, "half_day", f"{'한국' if mk == 'KR' else '미국'} {note}", mk, "거래소 규칙",
                                       time_=c.strftime("%H:%M")))
            d += timedelta(days=1)
    for y, m in _months(start, end):
        q = m in (3, 6, 9, 12)
        kd = kr_options_expiry(y, m)
        if start <= kd <= end:
            out.append(_ev(kd, "quad_witching" if q else "options_expiry",
                           "KOSPI200 선물·옵션 동시만기" if q else "KOSPI200 옵션 만기", "KR", "KRX 규칙 (둘째 목요일)", True,
                           time_="15:20"))
            if m in (6, 12):
                nd = KRX.next_trading_day(kd)
                if start <= nd <= end:
                    out.append(_ev(nd, "index_rebalance", "KOSPI200 정기 변경 적용", "KR", "KRX 규칙 (6·12월 만기 다음 날)", True))
        ud = us_options_expiry(y, m)
        if start <= ud <= end:
            out.append(_ev(ud, "quad_witching" if q else "options_expiry",
                           "미국 쿼드러플 위칭 · S&P 분기 리밸런싱" if q else "미국 월물 옵션 만기", "US", "규칙 (셋째 금요일)", True))
        if m in (2, 5, 8, 11):
            last = _on_or_before_trading(US, (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)))
            if start <= last <= end:
                out.append(_ev(last, "index_rebalance", "MSCI 분기 리뷰 적용 (장 마감 리밸런싱)", "GLOBAL", "MSCI 관행 (추정)", True))
    return out


def econ_events(start: date, end: date, fred_dates: list[dict] | None = None) -> list[dict]:
    out = []
    for s in FOMC_DATES:
        d = date.fromisoformat(s)
        if start <= d <= end:
            out.append(_ev(d, "fomc", "FOMC 금리 결정", "US", "연준 공표 일정", time_="14:00 ET"))
    have = {e["kind"] for e in (fred_dates or [])}
    for e in fred_dates or []:
        d = date.fromisoformat(e["date"])
        if start <= d <= end:
            out.append(_ev(d, e["kind"], e["title"], "US", "FRED 발표 일정", importance=e.get("importance"), time_="08:30 ET"))
    for y, m in _months(start, end):
        if "nfp" not in have:
            d = _nth_weekday(y, m, 4, 1)
            if start <= d <= end:
                out.append(_ev(d, "nfp", "미국 고용보고서 (비농업 고용)", "US", "BLS 관행 (첫째 금요일, 추정)", True, time_="08:30 ET"))
        d = date(y, m, 1)
        if start <= d <= end:
            out.append(_ev(d, "export", "한국 수출입 동향 (전월)", "KR", "산업부 관행 (매월 1일, 추정)", True, time_="09:00"))
    return out


def fetch_fred_release_dates(api_key: str, start: date, end: date, fetch=None) -> list[dict]:
    """FRED 발표 일정 (공식 날짜). 키가 없거나 실패하면 빈 목록."""
    if not api_key:
        return []
    kinds = {10: "cpi", 50: "nfp", 53: "gdp", 54: "pce", 9: "retail"}
    out = []
    for rid, (title, imp) in FRED_RELEASES.items():
        q = urllib.parse.urlencode({"release_id": rid, "api_key": api_key, "file_type": "json",
                                    "include_release_dates_with_no_data": "true", "realtime_start": start.isoformat(),
                                    "realtime_end": end.isoformat(), "sort_order": "asc", "limit": 50})
        url = f"https://api.stlouisfed.org/fred/release/dates?{q}"
        try:
            data = fetch(url) if fetch else json.loads(urllib.request.urlopen(url, timeout=10).read())  # noqa: S310
        except Exception as e:  # noqa: BLE001
            log.warning("FRED 발표 일정 실패 %s: %s", rid, type(e).__name__)
            continue
        for r in data.get("release_dates") or []:
            out.append({"date": r["date"], "kind": kinds[rid], "title": title, "importance": imp})
    return out


def stock_events(profiles: dict[str, dict], names: dict[str, str], start: date, end: date) -> list[dict]:
    """종목 상세 캐시(실적·배당·공시 일정). 네트워크 호출 없음."""
    out = []
    for sym, p in profiles.items():
        for e in p.get("events") or []:
            k = e.get("kind")
            if k not in ("earnings", "ex_div", "div_pay"):
                continue
            try:
                d = date.fromisoformat(str(e.get("date"))[:10])
            except ValueError:
                continue
            if start <= d <= end:
                out.append(_ev(d, k, f"{names.get(sym) or sym} {e.get('label') or KIND_LABEL[k]}", "KR" if sym[:1].isdigit() else "US",
                               e.get("source") or "종목 정보", bool(e.get("estimated")), symbol=sym, time_=e.get("time")))
    return out


def disclosure_events(rows, names: dict[str, str], start: date, end: date) -> list[dict]:
    out = []
    for r in rows:
        d = r.filed_at.date() if hasattr(r.filed_at, "date") else r.filed_at
        if start <= d <= end:
            out.append(_ev(d, "disclosure", f"{names.get(r.symbol) or r.symbol} {r.title}", "KR", "DART", symbol=r.symbol,
                           importance=0.7 if any(k in r.title for k in ("실적", "잠정", "유상증자", "합병", "분할", "공급계약")) else 0.4))
    return out


def custom_events(items: list[dict], start: date, end: date) -> list[dict]:
    out = []
    for e in items or []:
        try:
            d = datetime.fromisoformat(str(e.get("ts") or e.get("date")).replace("Z", "+00:00")).date()
        except (ValueError, TypeError):
            continue
        if start <= d <= end:
            out.append(_ev(d, e.get("kind") or "custom", e.get("name") or e.get("title") or "이벤트", e.get("country") or "GLOBAL",
                           "사용자 (events.json)", symbol=e.get("symbol"), importance=e.get("importance")))
    return out


def build_calendar(start: date, end: date, profiles=None, names=None, disclosures=None, custom=None,
                   fred_dates=None) -> list[dict]:
    ev = market_events(start, end) + econ_events(start, end, fred_dates) + stock_events(profiles or {}, names or {}, start, end)
    ev += disclosure_events(disclosures or [], names or {}, start, end) + custom_events(custom or [], start, end)
    seen, out = set(), []
    for e in sorted(ev, key=lambda x: (x["date"], -x["importance"])):
        k = (e["date"], e["kind"], e["title"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def with_dday(events: list[dict], today: date) -> list[dict]:
    for e in events:
        d = date.fromisoformat(e["date"])
        cal = KRX if e["market"] == "KR" else US
        e["d_day"] = (d - today).days
        e["d_label"] = "오늘" if d == today else f"D-{(d - today).days}" if d > today else f"D+{(today - d).days}"
        e["trading_days"] = cal.trading_days_between(today, d) if d >= today else -cal.trading_days_between(d, today)
    return events


# ------------------------------------------------------------------ 이벤트 위험
def _decay(td: int) -> float:
    """거래일 기준 가까울수록 1 → 5거래일 넘으면 0."""
    if td < 0:
        return 0.0
    return max(0.0, 1.0 - td / 6.0)


def event_risk(symbol: str, events: list[dict], today: date, sigma: float | None = None, beta: float | None = None,
               implied_move: float | None = None) -> dict:
    """종목의 다가오는 이벤트 위험. score 0~1 · 예상 변동(±%) · 매수 배수 제안."""
    kr = symbol[:1].isdigit()
    mine, market = [], []
    for e in events:
        if e["d_day"] < 0 or e["d_day"] > 21:
            continue
        if e.get("symbol") == symbol:
            mine.append(e)
        elif e["kind"] in MARKET_WIDE and e["market"] in (("KR", "GLOBAL") if kr else ("US", "GLOBAL")) + (("US",) if e["kind"] == "fomc" else ()):
            market.append(e)
    b = 1.0 if beta is None else max(0.3, min(2.0, abs(beta)))
    parts = [(e, e["importance"] * _decay(e["trading_days"])) for e in mine]
    parts += [(e, e["importance"] * _decay(e["trading_days"]) * 0.5 * b) for e in market]
    score = min(1.0, sum(p for _, p in parts))
    nxt = min(mine, key=lambda e: e["d_day"], default=None)
    earn = next((e for e in sorted(mine, key=lambda e: e["d_day"]) if e["kind"] == "earnings"), None)
    move = None
    if earn:
        move = implied_move or (2.5 * sigma if sigma else None)  # 옵션 내재 변동 → 없으면 일간 σ × 2.5 (경험칙)
    mult, why = 1.0, None
    if earn and earn["trading_days"] <= 1:
        mult, why = 0.5, f"실적 발표 {earn['d_label']}"
    elif earn and earn["trading_days"] <= 3:
        mult, why = 0.75, f"실적 발표 {earn['d_label']}"
    big = [e for e in market if e["kind"] in ("fomc", "cpi") and e["trading_days"] <= 0]
    if big and b >= 1.2 and mult > 0.75:
        mult, why = 0.75, f"{big[0]['label']} 당일 · 고베타"
    level = "high" if score >= 0.7 else "medium" if score >= 0.35 else "low"
    return {"symbol": symbol, "score": round(score, 3), "level": level, "next": nxt, "earnings": earn,
            "expected_move": None if move is None else round(move, 4),
            "market_events": [{"date": e["date"], "title": e["title"], "d_label": e["d_label"]} for e in market[:5]],
            "buy_multiplier": mult, "reason": why,
            "drivers": [{"title": e["title"], "d_label": e["d_label"], "weight": round(p, 3)} for e, p in sorted(parts, key=lambda x: -x[1])[:5]]}


def event_caps(symbols, events: list[dict], today: date, mode: str = "reduce") -> dict[str, tuple[float, str]]:
    """RiskEngine.event_caps 용. mode: reduce(기본 — 실적 D-1 이내 ×0.5) · block(×0) · off."""
    if mode == "off":
        return {}
    out = {}
    for s in symbols:
        r = event_risk(s, events, today)
        if r["buy_multiplier"] < 1.0:
            out[s] = (0.0 if mode == "block" else r["buy_multiplier"], r["reason"])
    return out


def load_custom(artifacts_dir) -> list[dict]:
    p = Path(artifacts_dir) / "events.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (OSError, json.JSONDecodeError):
        return []


def today_kst(now: datetime | None = None) -> date:
    from zoneinfo import ZoneInfo
    return (now or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Seoul")).date()


__all__ = ["build_calendar", "with_dday", "event_risk", "event_caps", "market_events", "econ_events",
           "kr_options_expiry", "us_options_expiry", "fetch_fred_release_dates", "FOMC_DATES", "today_kst", "load_custom"]

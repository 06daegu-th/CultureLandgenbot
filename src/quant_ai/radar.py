"""v38 이벤트 레이더 — '다가오는 실적발표나 중대 발표 있어? 미국장 중에 뭐 있어?' 에 한 화면으로 답한다.

모으는 것
  · 내 종목(보유·관심·코어·미국 장부)의 실적·배당·락업 공시 + 같은 업종 실적
  · 시장 큰 일정: FOMC·CPI·고용·PCE·금통위·옵션 만기·휴장/조기폐장 (규칙 + FRED·한은 일정)
  · 시장을 움직이는 대형주 실적 (저장된 일정에 있을 때)
  · 웹에서 찾은 일정 (discover_web) — 기사 제목에서 읽은 날짜라 '미확인'으로 따로 표시
모든 시각은 한국시간으로 바꿔 보여 준다 (미국 ET → KST, 서머타임 반영 · 장 시작 전/마감 후 실적은 '경').
'이번 미국장' 묶음 = 다음(또는 지금) 미국 정규장 앞뒤 시간에 걸리는 일정 — 밤에 깨어 있어야 할 이유.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import ops

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
WEB_STATE = "radar_web"
WEEK = "월화수목금토일"
# 시장 전체를 움직이는 대형주 — 내 종목이 아니어도 실적일이 저장돼 있으면 보여 준다
MOVERS = ("NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "TSM", "NFLX", "AMD", "MU", "005930", "000660")
# 발표 시각을 모를 때 '경' 으로 잡는 대표 시각 (미국 동부)
SESSION_ET = {"장 시작 전": time(7, 30), "장중": time(12, 0), "장 마감 후": time(16, 10)}
KIND_LABEL_EXTRA = {"peer_earnings": "동종 실적", "lockup": "락업", "web": "웹 발견"}


# ------------------------------------------------------------------ 시각 변환
def when(e: dict, market: str | None = None) -> tuple[datetime | None, bool]:
    """일정 → (발표 시각 UTC, 대략인가). 시각을 모르면 (None, True)."""
    try:
        d = date.fromisoformat(str(e.get("date"))[:10])
    except (TypeError, ValueError):
        return None, True
    t = (e.get("time") or "").strip()
    us = (market or e.get("market")) == "US" or (e.get("symbol") and not str(e["symbol"])[:1].isdigit())
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(ET)?$", t)
    if m:
        tz = ET if m.group(3) else KST
        return datetime.combine(d, time(int(m.group(1)), int(m.group(2))), tz).astimezone(UTC), False
    if t in SESSION_ET:
        return datetime.combine(d, SESSION_ET[t], ET if us else KST).astimezone(UTC), True
    return None, True


def kst_text(at: datetime | None, now: datetime, approx: bool = False, d: str | None = None) -> str:
    """'오늘 밤 21:30' · '내일 새벽 05:10경' · '10/23(목) 시간 미정'."""
    nk = now.astimezone(KST).date()
    if at is None:
        if not d:
            return "날짜 미정"
        dd = date.fromisoformat(d[:10])
        day = "오늘" if dd == nk else "내일" if dd == nk + timedelta(days=1) else f"{dd.month}/{dd.day}({WEEK[dd.weekday()]})"
        return f"{day} · 시간 미정"
    k = at.astimezone(KST)
    day = "오늘" if k.date() == nk else "내일" if k.date() == nk + timedelta(days=1) else f"{k.month}/{k.day}({WEEK[k.weekday()]})"
    part = "새벽" if k.hour < 6 else "오전" if k.hour < 12 else "오후" if k.hour < 18 else "밤"
    return f"{day} {part} {k:%H:%M}{'경' if approx else ''}"


def us_window(now: datetime) -> dict:
    """지금(또는 다음) 미국 정규장 — 한국시간. '이번 미국장' 묶음의 기준."""
    from .clock import MARKETS, Phase, next_close, next_open
    cal = MARKETS["US"]
    ph = cal.phase(now)
    if ph is Phase.OPEN:
        close = next_close(cal, now)
        o, _, _ = cal.session(cal.local(now).date())
        open_ = datetime.combine(cal.local(now).date(), o, cal.tz)
    else:
        open_ = next_open(cal, now)
        close = next_close(cal, open_ + timedelta(minutes=1))
    ok, ck = open_.astimezone(KST), close.astimezone(KST)
    return {"open": open_.astimezone(UTC), "close": close.astimezone(UTC), "open_now": ph is Phase.OPEN,
            "label": f"{'지금 열려 있음 · ' if ph is Phase.OPEN else ''}한국시간 {ok:%m/%d %H:%M} ~ {ck:%m/%d %H:%M}",
            "dst": bool(cal.local(open_).dst()),
            # 장 전 지표(08:30 ET)·장 마감 후 실적(16:00~ ET)까지 '이번 미국장'으로 본다
            "from": (open_ - timedelta(hours=1, minutes=30)).astimezone(UTC), "to": (close + timedelta(hours=2)).astimezone(UTC)}


# ------------------------------------------------------------------ 모으기
def _names(app) -> dict[str, str]:
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.global_stocks import global_name
    from .data.models import Instrument
    with session_scope(app.engine) as s:
        out = {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument))}
    for m in MOVERS:
        out.setdefault(m, global_name(m) or m)
    return out


def _row(e: dict, now: datetime, today: date, scope: str, sym: str | None, names: dict, tags=None, win=None) -> dict | None:
    try:
        d = date.fromisoformat(str(e.get("date"))[:10])
    except (TypeError, ValueError):
        return None
    at, approx = when(e | ({"symbol": sym} if sym else {}))
    from .engines.events import KIND_LABEL
    dd = (d - today).days
    r = {"date": d.isoformat(), "d_day": dd, "d_label": "오늘" if dd == 0 else "내일" if dd == 1 else f"D-{dd}",
         "kind": e.get("kind"), "label": KIND_LABEL.get(e.get("kind"), KIND_LABEL_EXTRA.get(e.get("kind"), e.get("kind") or "일정")),
         "title": e.get("title") or "", "symbol": sym, "name": names.get(sym) if sym else None, "tags": sorted(tags or ()),
         "scope": scope, "estimated": bool(e.get("estimated")), "confirmed": not e.get("estimated") and scope != "웹 발견",
         "time": e.get("time"), "at": at.isoformat() if at else None, "approx": approx if at else None,
         "kst": kst_text(at, now, approx, d.isoformat()), "source": e.get("source"), "url": e.get("url"),
         "importance": e.get("importance"), "eps_estimate": e.get("eps_estimate")}
    if win is not None:
        if at is not None:
            r["us_session"] = win["from"] <= at <= win["to"]
        else:  # 시각 모르는 미국 종목 실적 — 날짜(ET)가 이번 장 날짜면 걸린다
            r["us_session"] = bool(sym and not sym[:1].isdigit() and d == win["open"].astimezone(ET).date())
    return r


def upcoming(app, days: int = 14, now: datetime | None = None, symbol: str | None = None, web: bool = True) -> dict:
    """{rows(가까운 순), groups{us_session, mine, market, movers, web}, us{…}, counts, note}."""
    from .alerts import focus_symbols
    from .engines import events as E
    from .stock import ALWAYS_KINDS, events
    now = now or datetime.now(UTC)
    days = max(1, min(int(days or 14), 60))
    today = now.astimezone(KST).date()
    names = _names(app)
    win = us_window(now)
    focus = focus_symbols(app)
    if symbol:
        focus = {symbol: focus.get(symbol, set()) or {"찾아본 종목"}}
    rows, seen = [], set()

    def add(r):
        if r is None or not 0 <= r["d_day"] <= days:
            return
        key = (r["title"], r["date"], r["symbol"] if r["scope"] != "시장" else None)
        if key not in seen:
            seen.add(key)
            rows.append(r)

    # 1) 내 종목
    for sym, tags in list(focus.items())[:40]:
        try:
            evs = events(app, sym, now, days=days)
        except Exception as e:  # noqa: BLE001 - 한 종목 실패가 레이더를 막지 않게
            log.info("레이더 %s 일정 실패: %s", sym, e)
            continue
        for e in evs:
            if e.get("kind") == "unknown" or e.get("scope") == "시장":
                continue
            add(_row(e, now, today, "내 종목" if e.get("scope") == "종목" else "동종 업종", sym, names, tags, win))
    # 2) 시장 큰 일정 (저장된 일정 + 규칙으로 만드는 일정)
    cal = ops.get_state(app.engine, "event_calendar")
    stored = list(cal.get("events") or [])
    mk = [e for e in stored if not e.get("symbol")]
    try:
        mk += E.market_events(today, today + timedelta(days=days)) + E.econ_events(
            today, today + timedelta(days=days), ops.get_state(app.engine, "fred_releases").get("dates"),
            ops.get_state(app.engine, "bok_schedule") or None)
    except Exception as e:  # noqa: BLE001
        log.info("레이더 시장 일정 실패: %s", e)
    for e in mk:
        if e.get("kind") in ALWAYS_KINDS | {"holiday", "half_day", "options_expiry", "pce", "gdp", "retail"} or (e.get("importance") or 0) >= 0.7:
            add(_row(e, now, today, "시장", None, names, None, win))
    # 3) 대형주 실적 (내 종목이 아니어도)
    if not symbol:
        for e in stored:
            s_ = e.get("symbol")
            if s_ in MOVERS and s_ not in focus and e.get("kind") == "earnings":
                add(_row(e, now, today, "주요 기업", s_, names, None, win))
    # 4) 웹에서 찾은 일정 (미확인)
    wst = ops.get_state(app.engine, WEB_STATE) or {}
    if web:
        have = {(r["symbol"], r["kind"]) for r in rows if r["kind"] == "earnings" and r["confirmed"]}
        for e in wst.get("rows") or []:
            if symbol and e.get("symbol") != symbol:
                continue
            if (e.get("symbol"), "earnings") in have and e.get("kind") == "earnings":
                continue  # 확정 일정이 있으면 웹 추정은 뺀다
            add(_row(e | {"estimated": True}, now, today, "웹 발견", e.get("symbol"), names, focus.get(e.get("symbol")), win))
    far = datetime(9999, 1, 1, tzinfo=UTC)
    rows.sort(key=lambda r: (r["date"], datetime.fromisoformat(r["at"]) if r["at"] else far, r["scope"] != "내 종목"))
    groups = {"us_session": [r for r in rows if r.get("us_session")],
              "mine": [r for r in rows if r["scope"] in ("내 종목", "동종 업종")],
              "market": [r for r in rows if r["scope"] == "시장"],
              "movers": [r for r in rows if r["scope"] == "주요 기업"],
              "web": [r for r in rows if r["scope"] == "웹 발견"]}
    nxt = next((r for r in rows if r["at"] and datetime.fromisoformat(r["at"]) >= now), None)
    us = {k: win[k] for k in ("label", "open_now", "dst")} | {
        "open_kst": win["open"].astimezone(KST).strftime("%m/%d %H:%M"), "close_kst": win["close"].astimezone(KST).strftime("%m/%d %H:%M"),
        "n": len(groups["us_session"])}
    head = (f"이번 미국장({us['open_kst']} 개장)에 걸린 일정 {us['n']}건" if us["n"] else "이번 미국장에 걸린 큰 일정은 없어요") + \
        (f" · 내 종목 일정 {len(groups['mine'])}건" if groups["mine"] else "")
    return {"as_of": now.isoformat(), "now_kst": now.astimezone(KST).strftime("%Y-%m-%d %H:%M"), "days": days, "symbol": symbol,
            "rows": rows, "groups": groups, "us": us, "headline": head,
            "next": nxt and {k: nxt[k] for k in ("title", "kst", "name", "kind")},
            "counts": {k: len(v) for k, v in groups.items()} | {"all": len(rows)},
            "web": {"at": wst.get("at"), "n": len(wst.get("rows") or []), "errors": wst.get("errors") or [], "queries": wst.get("queries") or []},
            "note": "시각은 모두 한국시간 · '경'은 장 시작 전/마감 후만 알려진 대략 시각 · '추정'·'웹 발견'은 확정 아님 (회사 IR·공시로 확인)"}


# ------------------------------------------------------------------ 웹에서 찾기
_MON = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_DATE_PATS = (
    (re.compile(r"(20\d\d)[-./년 ]\s*(\d{1,2})[-./월 ]\s*(\d{1,2})"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일"), "md"),
    (re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I), "Md"),
    (re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])"), "md"),
)
EARN_WORDS = ("실적", "어닝", "잠정", "earnings", "results", "report", "quarter", "분기")
EVENT_WORDS = {"earnings": EARN_WORDS, "event": ("발표회", "공개", "행사", "컨퍼런스", "investor day", "event", "keynote", "gtc", "unveil",
                                                 "launch", "신제품", "기조연설")}


def find_dates(text: str, today: date, ahead: int = 60) -> list[date]:
    """제목·요약에서 앞으로 ahead 일 안의 날짜만 (지난 날짜·너무 먼 날짜는 버림)."""
    out = []
    for pat, kind in _DATE_PATS:
        for m in pat.finditer(text or ""):
            try:
                if kind == "ymd":
                    d = date(int(m[1]), int(m[2]), int(m[3]))
                elif kind == "Md":
                    d = date(today.year, _MON[m[1].lower()[:3]], int(m[2]))
                else:
                    d = date(today.year, int(m[1]), int(m[2]))
                if kind != "ymd" and d < today - timedelta(days=200):
                    d = d.replace(year=today.year + 1)
            except (ValueError, KeyError):
                continue
            if 0 <= (d - today).days <= ahead and d not in out:
                out.append(d)
    return out


def _aliases(sym: str, name: str) -> list[str]:
    from .data.global_stocks import GLOBAL_STOCKS
    out = {sym.lower(), (name or "").lower()}
    for g, ko, al in GLOBAL_STOCKS:
        if g == sym:
            out |= {ko.lower(), *(a.lower() for a in al if a.isascii() or len(a) >= 3)}
    return [k for k in out if len(k) >= 2]


def _queries(sym: str, name: str) -> list[tuple[str, str]]:
    if sym[:1].isdigit():
        return [(f"{name} 실적 발표 일정", "earnings"), (f"{name} 잠정실적 발표", "earnings")]
    return [(f"{sym} earnings date", "earnings"), (f"{name or sym} 실적 발표일", "earnings")]


def discover_web(app, now: datetime | None = None, search=None, limit: int = 8, symbols: list[str] | None = None) -> dict:
    """내 종목(미국 먼저)을 웹에서 찾아 '기사에 적힌' 실적·발표 날짜를 모은다 → 상태 radar_web (미확인 표시)."""
    from . import websearch
    from .alerts import focus_symbols
    now = now or datetime.now(UTC)
    today = now.astimezone(KST).date()
    search = search or websearch.search
    names = _names(app)
    syms = symbols or sorted(focus_symbols(app), key=lambda s: (s[:1].isdigit(), s))
    rows, errors, queries = [], [], []
    for sym in syms[:limit]:
        name = names.get(sym) or sym
        found = 0
        for q, _kind in _queries(sym, name):
            queries.append(q)
            try:
                res = search(q, kind="news", days=21, n=8)
            except Exception as e:  # noqa: BLE001
                res = {"results": [], "error": f"{type(e).__name__}: {e}"}
            if res.get("error") and not res.get("results"):
                errors.append(f"{q}: {res['error']}")
                break  # 인터넷이 안 되면 같은 종목 다음 검색도 안 된다
            keys = _aliases(sym, name)
            for it in res.get("results") or []:
                txt = f"{it.get('title', '')} {it.get('snippet', '')}"
                low = txt.lower()
                if not any((re.search(rf"\b{re.escape(k)}\b", low) if k.isascii() else k in low) for k in keys):
                    continue  # 다른 회사 기사
                k = "earnings" if any(w in low for w in EARN_WORDS) else "event" if any(w in low for w in EVENT_WORDS["event"]) else None
                if not k:
                    continue
                for d in find_dates(txt, today)[:1]:
                    rows.append({"date": d.isoformat(), "kind": "earnings" if k == "earnings" else "web", "symbol": sym,
                                 "title": f"{name} {'실적 발표 (기사)' if k == 'earnings' else '발표·행사 (기사)'}: {it.get('title', '')[:80]}",
                                 "source": it.get("source"), "url": it.get("url"), "estimated": True, "importance": 0.6, "found": q})
                    found += 1
            if found:
                break
        if errors and not rows and len(errors) >= 2:
            break  # 오프라인 — 더 돌려도 실패
    # 같은 종목·같은 날짜는 하나만
    uniq, seen = [], set()
    for r in rows:
        if (r["symbol"], r["date"]) not in seen:
            seen.add((r["symbol"], r["date"]))
            uniq.append(r)
    st = {"at": now.isoformat(), "rows": uniq, "errors": errors[:5], "queries": queries[:20], "n_symbols": min(len(syms), limit)}
    ops.set_state(app.engine, WEB_STATE, st)
    return st


# ------------------------------------------------------------------ 알림
def alert_soon(app, now: datetime | None = None, hours: int = 12) -> int:
    """12시간 안에 시작하는 내 종목 일정 · 큰 시장 일정 → 알림 한 번 (같은 일정은 다시 안 보냄)."""
    from .alerts import push
    now = now or datetime.now(UTC)
    r = upcoming(app, days=2, now=now, web=False)
    sent = 0
    for e in r["rows"]:
        if not e["at"]:
            continue
        at = datetime.fromisoformat(e["at"])
        if not now <= at <= now + timedelta(hours=hours):
            continue
        if e["scope"] not in ("내 종목", "시장") or (e["scope"] == "시장" and (e.get("importance") or 0) < 0.8):
            continue
        rid = push(app.engine, "event", f"곧: {e['title']}", f"{e['kst']} (한국시간)" + (" · 추정" if e["estimated"] else ""),
                   level="info", symbol=e.get("symbol"), link="#radar", dedupe=f"radar:{e['title']}:{e['date']}", now=now)
        sent += rid is not None
    return sent


__all__ = ["upcoming", "discover_web", "alert_soon", "us_window", "when", "kst_text", "find_dates"]

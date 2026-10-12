"""v39 미국 실적 캘린더 — 키 없이 Nasdaq 공개 캘린더에서 앞으로 3주 실적 발표 일정을 받는다.

종목 화면 Yahoo 일정은 '내가 열어 본 종목'만 있고 발표 시각(장 전/장 후)이 비거나 자리표시값인 경우가 많다.
Nasdaq 캘린더는 날짜별로 모든 미국 상장사의 발표를 주고, 장 시작 전(time-pre-market)·장 마감 후(time-after-hours)를 알려 준다.
→ 레이더 '주요 기업'·'내 종목'과 종목 화면 일정에 쓴다. 시가총액이 작은 회사는 저장하지 않는다 (내 종목은 크기와 상관없이).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime, timedelta

from . import ops

log = logging.getLogger(__name__)
STATE = "earnings_cal_us"
URL = "https://api.nasdaq.com/api/calendar/earnings?date={d}"
HEADERS = {"Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
TIME_KO = {"time-pre-market": "장 시작 전", "time-after-hours": "장 마감 후"}
MIN_CAP = 10e9  # 시가총액 100억 달러 이상만 (내 종목은 예외)


def _num(s) -> float | None:
    if s is None:
        return None
    t = re.sub(r"[$,\s]", "", str(s)).replace("(", "-").replace(")", "")
    try:
        return float(t)
    except ValueError:
        return None


def parse(payload: dict, d: date) -> list[dict]:
    rows = (((payload or {}).get("data") or {}).get("rows")) or []
    out = []
    for r in rows:
        sym = str(r.get("symbol") or "").strip().upper()
        if not sym:
            continue
        out.append({"date": d.isoformat(), "symbol": sym, "name": (r.get("name") or "").strip(),
                    "time": TIME_KO.get(r.get("time")), "eps_estimate": _num(r.get("epsForecast")),
                    "last_year_eps": _num(r.get("lastYearEPS")), "n_est": int(_num(r.get("noOfEsts")) or 0) or None,
                    "market_cap": _num(r.get("marketCap")), "quarter": r.get("fiscalQuarterEnding") or None})
    return out


def collect(app, days: int = 21, get=None, now: datetime | None = None) -> dict:
    """앞으로 days 일의 미국 거래일마다 캘린더를 받아 상태(earnings_cal_us)에 저장. 인터넷이 안 되면 두 번 실패에서 멈춘다."""
    from .alerts import focus_symbols
    from .clock import US
    from .global_market import universe
    now = now or datetime.now(UTC)
    if get is None:
        from .data.global_stocks import _http

        def get(url):
            return _http(url, headers=HEADERS)
    focus = {s for s in focus_symbols(app) if not s[:1].isdigit()} | set(universe() or [])
    start = (now - timedelta(hours=5)).date()  # 미국 날짜 기준 (한국 아침이면 미국은 전날)
    rows, errors, n_days = [], [], 0
    for i in range(days + 1):
        d = start + timedelta(days=i)
        if not US.is_trading_day(d):
            continue
        try:
            raw = get(URL.format(d=d.isoformat()))
            payload = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
        except Exception as e:  # noqa: BLE001
            errors.append(f"{d}: {type(e).__name__}: {e}"[:160])
            if len(errors) >= 2 and not rows:
                break  # 오프라인 — 더 돌려도 같다
            continue
        n_days += 1
        for r in parse(payload, d):
            if r["symbol"] in focus or (r["market_cap"] or 0) >= MIN_CAP:
                rows.append(r)
    old = ops.get_state(app.engine, STATE) or {}
    if not rows and old.get("rows"):  # 이번엔 못 받았으면 지난 결과를 지킨다
        st = old | {"errors": errors[:5], "tried_at": now.isoformat()}
    else:
        st = {"at": now.isoformat(), "rows": rows, "days": n_days, "errors": errors[:5], "source": "Nasdaq 실적 캘린더 (공개)"}
    ops.set_state(app.engine, STATE, st)
    return {k: st.get(k) for k in ("at", "days", "errors")} | {"n": len(st.get("rows") or [])}


def rows(app, symbol: str | None = None) -> list[dict]:
    st = ops.get_state(app.engine, STATE) or {}
    rs = st.get("rows") or []
    return [r for r in rs if r["symbol"] == symbol] if symbol else rs


def as_events(app, symbol: str | None = None) -> list[dict]:
    """레이더·종목 일정 모양으로 (title·time·eps)."""
    from .data.global_stocks import global_name
    out = []
    for r in rows(app, symbol):
        name = global_name(r["symbol"]) or r["name"] or r["symbol"]
        det = []
        if r.get("eps_estimate") is not None:
            det.append(f"EPS 예상 {r['eps_estimate']:.2f}" + (f" (작년 {r['last_year_eps']:.2f})" if r.get("last_year_eps") is not None else ""))
        out.append({"date": r["date"], "kind": "earnings", "title": f"{name} 실적 발표", "market": "US", "symbol": r["symbol"],
                    "time": r.get("time"), "eps_estimate": r.get("eps_estimate"), "detail": " · ".join(det) or None,
                    "source": "Nasdaq", "importance": 0.8 if (r.get("market_cap") or 0) >= 200e9 else 0.6, "market_cap": r.get("market_cap")})
    return out


__all__ = ["collect", "parse", "rows", "as_events", "STATE"]

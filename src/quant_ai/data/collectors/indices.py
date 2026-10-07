"""v27 진짜 지수 (코스피 · 코스닥 · 나스닥 · S&P 500 · 다우) + 하루 안 움직임(분봉).

예전에는 코스피를 '국내 상위 종목 시총 가중 대용'으로 그렸다 — 숫자가 실제 코스피와 달랐다.
이제는 공개 시세에서 진짜 지수 일봉을 받아 시스템 상태(index_daily)에 저장하고, 못 받으면 대용으로 돌아간다.

소스 (먼저 되는 것, 키 불필요)
  1) Yahoo 차트 API  (^KS11 · ^KQ11 · ^IXIC · ^GSPC · ^DJI)
  2) 네이버 증권 지수 API (국내 지수만)
  3) stooq CSV (해외 지수)
분봉 (종목 화면 '1일')
  · Yahoo 5분봉 (국내 = 005930.KS / 코스닥 .KQ) → 실패하면 '분봉 없음' (일봉으로 대신)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from . import http

log = logging.getLogger(__name__)

# key, 화면 이름, Yahoo, 네이버, stooq
INDICES = (
    ("KOSPI", "코스피", "^KS11", "KOSPI", None),
    ("KOSDAQ", "코스닥", "^KQ11", "KOSDAQ", None),
    ("NASDAQ", "나스닥", "^IXIC", None, "^ndq"),
    ("SPX", "S&P 500", "^GSPC", None, "^spx"),
    ("DJI", "다우", "^DJI", None, "^dji"),
)
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept": "application/json,text/plain,*/*"}
STATE_KEY = "index_daily"


def _get(url: str, timeout: float = 10.0) -> bytes:
    return http.get(url, timeout=timeout, retries=1, headers=BROWSER)


# ------------------------------------------------------------------ 파서 (네트워크 없이 시험 가능)
def parse_yahoo(raw: dict) -> list[list]:
    """Yahoo chart → [[YYYY-MM-DD, 종가], ...] (빈 값은 건너뜀)."""
    res = (((raw or {}).get("chart") or {}).get("result") or [None])[0] or {}
    ts = res.get("timestamp") or []
    q = (((res.get("indicators") or {}).get("quote") or [{}])[0] or {}).get("close") or []
    off = int(((res.get("meta") or {}).get("gmtoffset")) or 0)
    out = []
    for t, c in zip(ts, q, strict=False):
        if c is None:
            continue
        out.append([datetime.fromtimestamp(t + off, UTC).strftime("%Y-%m-%d"), round(float(c), 2)])
    dedup: dict[str, float] = {}
    for d, c in out:
        dedup[d] = c
    return [[d, c] for d, c in sorted(dedup.items())]


def parse_naver_index(raw) -> list[list]:
    """네이버 m.stock 지수 일별 시세 (최신이 앞) → 오래된 순."""
    rows = raw if isinstance(raw, list) else (raw or {}).get("result") or (raw or {}).get("items") or []
    out = []
    for r in rows:
        d = str(r.get("localTradedAt") or r.get("localDate") or r.get("date") or "")[:10]
        v = r.get("closePrice") or r.get("closePriceRaw") or r.get("close")
        try:
            out.append([d.replace(".", "-"), round(float(str(v).replace(",", "")), 2)])
        except (TypeError, ValueError):
            continue
    return sorted([x for x in out if len(x[0]) == 10])


def parse_stooq(text: str) -> list[list]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            out.append([r["Date"], round(float(r["Close"]), 2)])
        except (KeyError, TypeError, ValueError):
            continue
    return out[-260:]


def parse_intraday(raw: dict) -> dict:
    """Yahoo 5분봉 → {points: [[유닉스초, 가격]], prev_close, tz, market_state}."""
    res = (((raw or {}).get("chart") or {}).get("result") or [None])[0] or {}
    meta = res.get("meta") or {}
    ts = res.get("timestamp") or []
    q = (((res.get("indicators") or {}).get("quote") or [{}])[0] or {})
    pts = [[int(t), round(float(c), 4), int(v or 0)] for t, c, v in zip(ts, q.get("close") or [], q.get("volume") or [0] * len(ts), strict=False) if c is not None]
    return {"points": pts, "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"), "tz": meta.get("exchangeTimezoneName"),
            "gmtoffset": meta.get("gmtoffset"), "market_state": meta.get("marketState"), "last": meta.get("regularMarketPrice")}


# ------------------------------------------------------------------ 받기
def fetch_index(key: str, get: Callable[[str], bytes] | None = None) -> dict:
    get = get or _get
    spec = next((x for x in INDICES if x[0] == key), None)
    if spec is None:
        raise KeyError(key)
    _, name, yh, nv, sq = spec
    errs = []
    try:
        s = parse_yahoo(json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{yh.replace('^', '%5E')}?range=1y&interval=1d")))
        if len(s) >= 5:
            return {"key": key, "name": name, "series": s, "source": "Yahoo"}
        errs.append("Yahoo: 빈 응답")
    except Exception as e:  # noqa: BLE001 - 다음 소스로
        errs.append(f"Yahoo: {str(e)[:80]}")
    if nv:
        try:
            s = parse_naver_index(json.loads(get(f"https://m.stock.naver.com/api/index/{nv}/price?pageSize=120&page=1")))
            if len(s) >= 5:
                return {"key": key, "name": name, "series": s, "source": "네이버 증권"}
            errs.append("네이버: 빈 응답")
        except Exception as e:  # noqa: BLE001
            errs.append(f"네이버: {str(e)[:80]}")
    if sq:
        try:
            s = parse_stooq(get(f"https://stooq.com/q/d/l/?s={sq}&i=d").decode("utf-8", "replace"))
            if len(s) >= 5:
                return {"key": key, "name": name, "series": s, "source": "stooq"}
            errs.append("stooq: 빈 응답")
        except Exception as e:  # noqa: BLE001
            errs.append(f"stooq: {str(e)[:80]}")
    return {"key": key, "name": name, "series": [], "error": " · ".join(errs)}


def collect_indices(engine, get: Callable[[str], bytes] | None = None) -> dict:
    """모든 지수를 받아 상태에 저장 — 실패한 지수는 이전 값을 그대로 둔다 (오래된 값 + 오류 표시)."""
    from ... import ops
    old = ops.get_state(engine, STATE_KEY, {})
    items = dict(old.get("items") or {})
    ok, fail = [], []
    for key, *_ in INDICES:
        r = fetch_index(key, get)
        if r.get("series"):
            items[key] = {**r, "series": r["series"][-260:], "at": datetime.now(UTC).isoformat()}
            ok.append(key)
        else:
            prev = items.get(key) or {"key": key, "name": r["name"], "series": []}
            items[key] = {**prev, "error": r.get("error"), "tried_at": datetime.now(UTC).isoformat()}
            fail.append(key)
    st = {"at": datetime.now(UTC).isoformat(), "items": items, "ok": ok, "failed": fail}
    ops.set_state(engine, STATE_KEY, st)
    return {"ok": ok, "failed": fail}


def load_indices(engine) -> dict:
    from ... import ops
    return (ops.get_state(engine, STATE_KEY, {}) or {}).get("items") or {}


_INTRA: dict[str, tuple[float, dict]] = {}


def yahoo_symbol(sym: str, market: str | None = None) -> str:
    if sym.isdigit():
        return f"{sym}.{'KQ' if market == 'KOSDAQ' else 'KS'}"
    return sym.replace(".", "-")


def intraday(sym: str, market: str | None = None, get: Callable[[str], bytes] | None = None, ttl: float = 60.0) -> dict:
    """오늘(또는 마지막 거래일) 5분봉. 1분 동안 같은 요청은 저장해 둔 값을 준다."""
    now = time.time()
    hit = _INTRA.get(sym)
    if hit and now - hit[0] < ttl:
        return hit[1]
    get = get or _get
    ys = yahoo_symbol(sym, market)
    out: dict
    try:
        out = parse_intraday(json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ys}?range=1d&interval=5m&includePrePost=false")))
        if not out["points"] and sym.isdigit() and market != "KOSDAQ":  # 코스피에 없으면 코스닥으로 한 번 더
            out = parse_intraday(json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.KQ?range=1d&interval=5m")))
        out |= {"symbol": sym, "source": "Yahoo 5분봉 (지연될 수 있음)"}
        if not out["points"]:
            out["error"] = "분봉 자료가 비어 있어요 (장 시작 전이거나 휴장)"
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        why = ("인터넷 연결이 안 되거나 시세 서버에 닿지 못했어요" if any(k in msg for k in ("URLError", "timed out", "Tunnel", "Connection", "Name or service"))
               else "시세 서버가 응답하지 않았어요 (잠시 뒤 다시)" if any(k in msg for k in ("429", "500", "502", "503")) else "분봉 자료를 읽지 못했어요")
        out = {"symbol": sym, "points": [], "error": f"{why} — 1주 이상 차트는 저장된 일봉으로 볼 수 있어요",
               "detail": msg[:160]}  # v36: 화면에는 쉬운 말, 기술 내용은 detail 로만
    _INTRA[sym] = (now, out)
    if len(_INTRA) > 300:
        for k in sorted(_INTRA, key=lambda k: _INTRA[k][0])[:100]:
            _INTRA.pop(k, None)
    return out


__all__ = ["INDICES", "collect_indices", "fetch_index", "intraday", "load_indices", "parse_intraday", "parse_naver_index", "parse_stooq", "parse_yahoo"]

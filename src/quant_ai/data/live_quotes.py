"""실시간(지연 가능) 시세 — 알림(급등·급락)과 화면의 가격 깜빡임용. 키 불필요.

국내: 네이버 증권 실시간 폴링 API (여러 종목 한 번에) → 실패하면 종목별 기본 API.
미국: Yahoo chart meta (현재가·전일 종가).
증권사(KIS) 시세가 있으면 그쪽이 더 정확하다 — 매매는 이 값을 쓰지 않는다 (표시·알림 전용).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from .global_stocks import _http

log = logging.getLogger(__name__)
NAVER_H = {"Referer": "https://m.stock.naver.com/"}


def _num(x) -> float | None:
    if x is None:
        return None
    s = re.sub(r"[,+%\s]", "", str(x))
    try:
        return float(s)
    except ValueError:
        return None


def _naver_row(d: dict) -> dict | None:
    price = _num(d.get("closePrice") or d.get("nv"))
    if not price:
        return None
    ratio = _num(d.get("fluctuationsRatio") if d.get("fluctuationsRatio") is not None else d.get("cr"))
    chg = _num(d.get("compareToPreviousClosePrice") if d.get("compareToPreviousClosePrice") is not None else d.get("cv"))
    direction = ((d.get("compareToPreviousPrice") or {}).get("name") or "") if isinstance(d.get("compareToPreviousPrice"), dict) else ""
    if direction in ("FALLING", "LOWER_LIMIT"):  # 일부 응답은 부호 없이 방향만 준다
        ratio = -abs(ratio) if ratio is not None else None
        chg = -abs(chg) if chg is not None else None
    return {"price": price, "chg_pct": ratio / 100 if ratio is not None else None, "chg": chg,
            "volume": _num(d.get("accumulatedTradingVolume") or d.get("aq")),
            "status": d.get("marketStatus"), "src": "naver"}


def fetch_kr(codes: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    codes = [c for c in codes if re.fullmatch(r"\d{6}", c)]
    for i in range(0, len(codes), 20):
        chunk = codes[i:i + 20]
        try:
            raw = json.loads(_http("https://polling.finance.naver.com/api/realtime/domestic/stock/" + ",".join(chunk),
                                   timeout=8, headers=NAVER_H))
            for d in raw.get("datas") or []:
                row = _naver_row(d)
                if row and d.get("itemCode"):
                    out[d["itemCode"]] = row
        except Exception as e:  # noqa: BLE001 - 종목별 API 로 다시
            log.debug("네이버 실시간 묶음 실패: %s", e)
    for c in [c for c in codes if c not in out][:10]:
        try:
            row = _naver_row(json.loads(_http(f"https://m.stock.naver.com/api/stock/{c}/basic", timeout=8, headers=NAVER_H)))
            if row:
                out[c] = row
        except Exception as e:  # noqa: BLE001
            log.debug("네이버 %s 실패: %s", c, e)
    return out


def fetch_us(symbols: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sym in symbols[:15]:
        try:
            raw = json.loads(_http(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m",
                                   timeout=8))
            meta = (((raw.get("chart") or {}).get("result") or [{}])[0] or {}).get("meta") or {}
            px, prev = meta.get("regularMarketPrice"), meta.get("chartPreviousClose") or meta.get("previousClose")
            if px:
                out[sym] = {"price": float(px), "chg_pct": (px / prev - 1) if prev else None,
                            "chg": (px - prev) if prev else None, "volume": meta.get("regularMarketVolume"),
                            "status": meta.get("marketState"), "src": "yahoo"}
        except Exception as e:  # noqa: BLE001
            log.debug("Yahoo 실시간 %s 실패: %s", sym, e)
    return out


def fetch_quotes(symbols: list[str], fetchers: dict | None = None) -> dict[str, dict]:
    f = {"kr": fetch_kr, "us": fetch_us, **(fetchers or {})}
    kr = [s for s in symbols if s.isdigit()]
    us = [s for s in symbols if not s.isdigit()]
    out = {}
    if kr:
        out.update(f["kr"](kr))
    if us:
        out.update(f["us"](us))
    now = datetime.now(UTC).isoformat()
    for v in out.values():
        v.setdefault("ts", now)
    return out


__all__ = ["fetch_quotes", "fetch_kr", "fetch_us"]

"""WICS 공식 업종 (와이즈인덱스 · 키 불필요) — 국내 종목의 업종을 Yahoo 번역이 아닌 공식 분류로.

WICS 대분류 10개(G10~G55)의 구성 종목을 받아 종목 → 업종 지도를 만든다 (주 1회).
응답 형식이 바뀌거나 막히면 그대로 두고 기존(Yahoo 기반) 지도를 쓴다. 날짜는 가장 최근 거래일 기준.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import UTC, datetime, timedelta

from ... import ops

log = logging.getLogger(__name__)
SECTORS = {"G10": "에너지", "G15": "소재", "G20": "산업재", "G25": "경기관련소비재", "G30": "필수소비재",
           "G35": "건강관리", "G40": "금융", "G45": "IT", "G50": "커뮤니케이션서비스", "G55": "유틸리티"}
TTL = timedelta(days=7)


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.wiseindex.com/"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - https 고정 주소
        return json.loads(r.read())


def parse(data: dict, code: str) -> dict[str, dict]:
    out = {}
    for r in (data or {}).get("list") or []:
        c = str(r.get("CMP_CD") or "").zfill(6)
        if len(c) == 6 and c.isdigit():
            out[c] = {"sector": r.get("SEC_NM_KOR") or SECTORS.get(code), "sector_code": code,
                      "industry": r.get("IDX_NM_KOR"), "weight": r.get("IDX_WGT"), "name": r.get("CMP_KOR")}
    return out


def fetch(day: str, fetcher=None, pause: float = 0.5) -> dict[str, dict]:
    out = {}
    for code in SECTORS:
        url = f"https://www.wiseindex.com/Index/GetIndexComponets?ceil_yn=0&dt={day}&sec_cd={code}"
        out.update(parse((fetcher or _get)(url), code))
        if pause:
            time.sleep(pause)
    return out


def refresh(engine, fetcher=None, now: datetime | None = None, force: bool = False) -> dict:
    now = now or datetime.now(UTC)
    st = ops.get_state(engine, "wics_map")
    if not force and st.get("at") and now - datetime.fromisoformat(st["at"]) < TTL and st.get("map"):
        return {"mapped": len(st["map"]), "cached": True}
    from ...asof import last_trading_close
    day = last_trading_close("KR", now).strftime("%Y%m%d")
    try:
        got = fetch(day, fetcher)
    except Exception as e:  # noqa: BLE001
        log.warning("WICS 실패: %s", e)
        ops.set_state(engine, "wics_map", {**st, "error": f"{type(e).__name__}: {str(e)[:100]}", "tried_at": now.isoformat()})
        return {"mapped": len(st.get("map") or {}), "error": type(e).__name__}
    if not got:
        return {"mapped": len(st.get("map") or {}), "error": "빈 응답"}
    mp = {c: v["sector"] for c, v in got.items() if v.get("sector")}
    ops.set_state(engine, "wics_map", {"map": mp, "detail": got, "at": now.isoformat(), "day": day, "source": "WICS (와이즈인덱스)"})
    return {"mapped": len(mp), "day": day}


__all__ = ["refresh", "fetch", "parse", "SECTORS"]

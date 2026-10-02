"""VKOSPI (KOSPI200 옵션 내재변동성) — 국내 시장이 매긴 '앞으로 30일 변동폭'.

소스 (먼저 되는 것)
  1) KRX 정보데이터시스템 파생지수 시세 (키 불필요) — 요청 형식은 QUANT_VKOSPI_KRX 로 바꿀 수 있다
  2) 없으면 대용: KOSPI 대용 지수의 20일 실현 변동성 (연율) — 화면에 '대용' 으로 표시
쓰임
  · 시장 예상 변동: 일간 = VKOSPI/100/√252 → N일 = × √N
  · 국내 종목 예상 변동 = √((베타 × 시장 변동)² + 고유 변동²) — 옵션이 없는 국내 종목의 '내재 변동' 근사
  · 백분위(최근 1년) → 공포 구간(상위 20%)이면 이벤트 위험 가중
"""

from __future__ import annotations

import json
import logging
import math
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from ... import ops

log = logging.getLogger(__name__)
KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
# 파생지수 시세 추이 (VKOSPI). 형식이 바뀌면 QUANT_VKOSPI_KRX='bld=...&indIdx=...&indIdx2=...' 로 덮어쓴다
DEFAULT_QUERY = "bld=dbms/MDC/STAT/standard/MDCSTAT01201&indIdx=1&indIdx2=300"
TTL = timedelta(hours=12)


def _post(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.krx.co.kr/"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - https 고정
        return json.loads(r.read())


def parse_krx(raw: dict) -> pd.Series:
    rows = (raw or {}).get("output") or (raw or {}).get("OutBlock_1") or []
    pts = {}
    for r in rows:
        d = str(r.get("TRD_DD") or r.get("trdDd") or "").replace("/", "-")
        v = r.get("CLSPRC_IDX") or r.get("clsprcIdx") or r.get("TDD_CLSPRC")
        try:
            pts[pd.Timestamp(d)] = float(str(v).replace(",", ""))
        except (ValueError, TypeError):
            continue
    return pd.Series(pts).sort_index()


def fetch_krx(start: str, end: str, query: str | None = None, post=None) -> pd.Series:
    form = dict(urllib.parse.parse_qsl(query or os.environ.get("QUANT_VKOSPI_KRX") or DEFAULT_QUERY))
    form |= {"strtDd": start, "endDd": end, "share": "1", "money": "1", "csvxls_isNo": "false"}
    return parse_krx((post or _post)(KRX_URL, form))


def realized_proxy(bench: pd.DataFrame | None) -> pd.Series:
    if bench is None or len(bench) < 30:
        return pd.Series(dtype=float)
    lr = np.log(bench["close"].astype(float)).diff()
    return (lr.rolling(20).std() * math.sqrt(252) * 100).dropna()


def get(engine, bench: pd.DataFrame | None = None, post=None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    st = ops.get_state(engine, "vkospi")
    if st.get("at") and now - datetime.fromisoformat(st["at"]) < TTL and st.get("level") is not None:
        return st
    src, s = "VKOSPI (KRX)", pd.Series(dtype=float)
    try:
        s = fetch_krx((now - timedelta(days=400)).strftime("%Y%m%d"), now.strftime("%Y%m%d"), post=post)
    except Exception as e:  # noqa: BLE001
        log.info("VKOSPI KRX 실패: %s", type(e).__name__)
    if len(s) < 20:
        src, s = "대용: KOSPI 20일 실현 변동성 (VKOSPI 를 받지 못함)", realized_proxy(bench)
    if s.empty:
        return {"available": False, "message": "VKOSPI · 지수 이력 모두 없음"}
    lvl = float(s.iloc[-1])
    last = s.iloc[-252:]
    pct = float((last <= lvl).mean())
    out = {"available": True, "at": now.isoformat(), "source": src, "proxy": src.startswith("대용"), "level": round(lvl, 2),
           "as_of": str(s.index[-1].date()), "percentile_1y": round(pct, 3),
           "daily_move": round(lvl / 100 / math.sqrt(252), 5), "move_5d": round(lvl / 100 * math.sqrt(5 / 252), 4),
           "move_20d": round(lvl / 100 * math.sqrt(20 / 252), 4), "fear": pct >= 0.8,
           "series": [round(float(x), 2) for x in s.iloc[-120:]]}
    ops.set_state(engine, "vkospi", out)
    return out


def stock_move(vk: dict, beta: float | None, idio_daily: float | None, days: int = 1) -> float | None:
    """옵션이 없는 국내 종목의 '내재 변동' 근사: √((β·시장)² + 고유²) × √days."""
    if not vk or not vk.get("available"):
        return None
    m = vk["daily_move"] * (abs(beta) if beta is not None else 1.0)
    i = idio_daily or 0.0
    return round(math.sqrt(m * m + i * i) * math.sqrt(days), 5)


__all__ = ["get", "fetch_krx", "parse_krx", "stock_move", "realized_proxy"]

"""한국은행 금융통화위원회 (기준금리 결정) — 일정 · 기준금리 이력.

일정 (셋 중 먼저 되는 것)
  1) .env QUANT_BOK_DATES=2026-10-22,2026-11-26  (한은 공지를 보고 직접 — 가장 확실)
  2) 한국은행 '통화정책방향 결정 회의 일정' 공개 페이지에서 '2026.10.22(목)' 형식 날짜를 읽음 (키 불필요)
  3) 없으면 캘린더에 넣지 않는다 (추측으로 날짜를 만들지 않는다)
기준금리 이력 (ECOS_API_KEY 가 있으면 · 무료 발급)
  ECOS 722Y001(한국은행 기준금리, 일별) → 현재 금리 · 변경일 목록 → 과거 결정일의 시장 영향 분석(event_impact)에 쓴다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import UTC, date, datetime, timedelta

from ... import ops

log = logging.getLogger(__name__)
SCHEDULE_URL = "https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755"
DATE_RX = re.compile(r"(20\d\d)\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?\s*\(\s*(월|화|수|목|금)\s*\)")
TTL = timedelta(days=7)


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - https 고정
        return r.read(2_000_000).decode("utf-8", "replace")


def parse_schedule(html: str) -> list[str]:
    """'2026.10.22(목)' 처럼 요일이 붙은 날짜만 (게시일 같은 다른 날짜를 피한다)."""
    out = set()
    for y, m, d, _ in DATE_RX.findall(html or ""):
        try:
            out.add(date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            continue
    return sorted(out)


def schedule(engine, fetch=None, now: datetime | None = None, env: dict | None = None) -> dict:
    now = now or datetime.now(UTC)
    e = os.environ if env is None else env
    manual = [x.strip() for x in (e.get("QUANT_BOK_DATES") or "").split(",") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x.strip())]
    if manual:
        return {"dates": sorted(manual), "source": "사용자 입력 (QUANT_BOK_DATES)"}
    st = ops.get_state(engine, "bok_schedule")
    if st.get("at") and now - datetime.fromisoformat(st["at"]) < (timedelta(hours=12) if st.get("error") else TTL):
        return st
    try:
        dates = parse_schedule((fetch or _get)(SCHEDULE_URL))
        out = {"at": now.isoformat(), "dates": dates, "source": "한국은행 공개 일정" if dates else "한국은행 페이지 (날짜 인식 실패)"}
    except Exception as e:  # noqa: BLE001
        out = {"at": now.isoformat(), "dates": st.get("dates") or [], "error": f"{type(e).__name__}", "source": "한국은행 페이지 (접속 실패)"}
    ops.set_state(engine, "bok_schedule", out)
    return out


def base_rate(engine, api_key: str | None, fetch=None, now: datetime | None = None, years: int = 6) -> dict:
    """ECOS 기준금리 (일별) → 현재 · 변경 이력."""
    if not api_key:
        return {"available": False, "message": "ECOS_API_KEY 가 있으면 기준금리 이력을 가져옵니다 (무료)"}
    now = now or datetime.now(UTC)
    st = ops.get_state(engine, "bok_rate")
    if st.get("at") and now - datetime.fromisoformat(st["at"]) < timedelta(hours=20) and st.get("changes") is not None:
        return st
    start = (now - timedelta(days=365 * years)).strftime("%Y%m%d")
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/5000/722Y001/D/{start}/{now:%Y%m%d}/0101000"
    try:
        raw = (fetch or (lambda u: json.loads(_get(u))))(url)
        rows = ((raw or {}).get("StatisticSearch") or {}).get("row") or []
        pts = [(r["TIME"], float(r["DATA_VALUE"])) for r in rows if r.get("TIME") and r.get("DATA_VALUE") not in (None, "")]
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": f"{type(e).__name__}", "message": "ECOS 조회 실패"}
    pts.sort()
    changes = []
    for (_t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if v1 != v0:
            changes.append({"date": f"{t1[:4]}-{t1[4:6]}-{t1[6:8]}", "from": v0, "to": v1, "bp": round((v1 - v0) * 100)})
    out = {"available": bool(pts), "at": now.isoformat(), "current": pts[-1][1] if pts else None,
           "as_of": f"{pts[-1][0][:4]}-{pts[-1][0][4:6]}-{pts[-1][0][6:8]}" if pts else None, "changes": changes[-20:]}
    ops.set_state(engine, "bok_rate", out)
    return out


__all__ = ["schedule", "parse_schedule", "base_rate", "SCHEDULE_URL"]

"""외국인 · 기관 수급 (국내) — 네이버 증권 투자자별 매매동향. 키 불필요.

보유·관심·코어 종목만, 장 마감 후 하루 한 번(최근 20거래일).
요약: 외국인·기관 5일/20일 순매수(주) · 연속 순매수(매도) 일수 · 외국인 보유율 변화.
AI 판단 재료(flow)와 종목 화면의 '수급' 카드, 체크리스트의 수급 항목에 쓰인다.
응답 형식이 바뀌면 조용히 쉬고(하루 뒤 재시도) 체크리스트는 거래량 대용으로 돌아간다.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta

from ... import ops
from ..global_stocks import _http

log = logging.getLogger(__name__)
FAIL_COOLDOWN = timedelta(hours=20)


def _num(x) -> float | None:
    if x is None:
        return None
    s = re.sub(r"[,+%\s]", "", str(x))
    try:
        return float(s)
    except ValueError:
        return None


def _pick(row: dict, *keys: str):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    low = {k.lower(): v for k, v in row.items()}
    for k in keys:
        for kk, v in low.items():
            if k.lower() in kk and v not in (None, ""):
                return v
    return None


def parse_trend(raw) -> list[dict]:
    rows = raw if isinstance(raw, list) else (raw or {}).get("trendList") or (raw or {}).get("list") or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = str(_pick(r, "bizdate", "localTradedAt", "date") or "")[:10].replace("-", "")
        if not re.fullmatch(r"\d{8}", d):
            continue
        out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                    "foreign": _num(_pick(r, "foreignerPureBuyQuant", "foreignPureBuyQuant", "foreigner")),
                    "inst": _num(_pick(r, "organPureBuyQuant", "institutionPureBuyQuant", "organ")),
                    "indiv": _num(_pick(r, "individualPureBuyQuant", "individual")),
                    "foreign_ratio": (_num(_pick(r, "foreignerHoldRatio", "foreignHoldRatio")) or 0) / 100 or None,
                    "close": _num(_pick(r, "closePrice", "close"))})
    return sorted(out, key=lambda x: x["date"])


def fetch_naver(code: str) -> list[dict]:
    raw = json.loads(_http(f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=20", timeout=10,
                           headers={"Referer": "https://m.stock.naver.com/"}))
    rows = parse_trend(raw)
    if not rows:
        raise RuntimeError("수급 데이터 없음 (응답 형식 변경 가능)")
    return rows


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}

    def s(key, n):
        v = [r[key] for r in rows[-n:] if r.get(key) is not None]
        return sum(v) if v else None

    def streak(key):
        k = 0
        sign = None
        for r in reversed(rows):
            v = r.get(key)
            if v is None or v == 0:
                break
            cur = v > 0
            if sign is None:
                sign = cur
            if cur != sign:
                break
            k += 1
        return (k if sign else -k) if sign is not None else 0
    fr = [r["foreign_ratio"] for r in rows if r.get("foreign_ratio")]
    return {"asof": rows[-1]["date"], "foreign_5d": s("foreign", 5), "foreign_20d": s("foreign", 20),
            "inst_5d": s("inst", 5), "inst_20d": s("inst", 20), "foreign_streak": streak("foreign"),
            "inst_streak": streak("inst"), "foreign_ratio": fr[-1] if fr else None,
            "foreign_ratio_chg_20d": (fr[-1] - fr[0]) if len(fr) >= 2 else None}


def collect(engine, codes: list[str], fetch=None, now: datetime | None = None, pause: float = 0.3) -> dict:
    now = now or datetime.now(UTC)
    fails = ops.get_state(engine, "flow_fail")
    done, failed = [], []
    for c in [x for x in codes if x.isdigit()][:40]:
        if fails.get(c) and now - datetime.fromisoformat(fails[c]) < FAIL_COOLDOWN:
            continue
        try:
            rows = (fetch or fetch_naver)(c)
        except Exception as e:  # noqa: BLE001
            fails[c] = now.isoformat()
            failed.append(f"{c}: {str(e)[:60]}")
            continue
        fails.pop(c, None)
        ops.set_state(engine, f"flow:{c}", {"at": now.isoformat(), "rows": rows[-20:], "summary": summarize(rows)})
        done.append(c)
        if pause:
            time.sleep(pause)
    ops.set_state(engine, "flow_fail", fails)
    return {"collected": len(done), "failed": failed[:10]}


def for_context(engine, code: str, max_age: timedelta = timedelta(days=4)) -> dict:
    st = ops.get_state(engine, f"flow:{code}")
    if not st.get("at") or datetime.now(UTC) - datetime.fromisoformat(st["at"]) > max_age:
        return {}
    return {"note": "순매수 단위: 주 · streak 양수 = 연속 순매수 일수", **(st.get("summary") or {})}


__all__ = ["collect", "parse_trend", "summarize", "for_context", "fetch_naver"]

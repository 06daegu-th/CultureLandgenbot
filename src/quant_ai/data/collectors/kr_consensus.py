"""국내 실적 컨센서스 (네이버 증권 분기 재무 · 키 불필요) → 실적 서프라이즈 이력.

네이버 분기 재무표는 아직 발표 안 된 분기를 '컨센서스(추정)' 열로 보여 준다.
  1) 매일 보유·관심 종목의 추정치를 스냅샷으로 남긴다 (발표 직전 값이 '시장 예상')
  2) 같은 분기가 추정 → 실적으로 바뀌면: 마지막 추정치 vs 실제 → 서프라이즈(%) 를 기록
  3) 이 이력이 실적 서프라이즈 모델(engines/earnings.py)의 국내 입력이 된다 (영업이익 기준, EPS 가 있으면 EPS)
이력은 시간이 지나며 쌓인다 — 처음 켠 날에는 과거 서프라이즈가 없다 (그 사실을 화면에 표시).
응답 형식이 바뀌면 조용히 쉬고(하루 뒤 재시도) 모델은 사전 분포만 쓴다.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta

from ... import ops

log = logging.getLogger(__name__)
METRICS = {"매출액": "revenue", "영업이익": "op_income", "당기순이익": "net_income", "EPS": "eps"}
FAIL_COOLDOWN = timedelta(hours=20)


def _num(v) -> float | None:
    if v is None:
        return None
    s = re.sub(r"[,\s%]", "", str(v))
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_naver(code: str) -> dict:
    from ..global_stocks import _http
    return json.loads(_http(f"https://m.stock.naver.com/api/stock/{code}/finance/quarter", timeout=10,
                            headers={"Referer": "https://m.stock.naver.com/"}))


def parse_quarter(raw: dict) -> dict:
    """{periods: [{key, label, consensus}], values: {metric: {key: float}}}. 모르는 형식이면 빈 값."""
    fi = (raw or {}).get("financeInfo") or raw or {}
    heads = fi.get("trTitleList") or fi.get("titleList") or []
    periods = []
    for h in heads:
        key = str(h.get("key") or h.get("title") or "").replace(".", "")[:6]
        if not re.fullmatch(r"\d{6}", key):
            continue
        periods.append({"key": key, "label": str(h.get("title") or key), "consensus": str(h.get("isConsensus", "N")).upper() == "Y"})
    values: dict[str, dict[str, float]] = {}
    for row in fi.get("rowList") or []:
        name = str(row.get("title") or "").strip()
        m = next((v for k, v in METRICS.items() if name.startswith(k)), None)
        if not m:
            continue
        cols = row.get("columns") or {}
        out = {}
        for p in periods:
            c = cols.get(p["key"]) or cols.get(p["label"])
            val = _num(c.get("value") if isinstance(c, dict) else c)
            if val is not None:
                out[p["key"]] = val
        values[m] = out
    return {"periods": periods, "values": values}


def update(engine, code: str, parsed: dict, now: datetime | None = None) -> dict:
    """스냅샷 저장 + 추정→실적으로 바뀐 분기의 서프라이즈 계산."""
    now = now or datetime.now(UTC)
    key = f"krcons:{code}"
    st = ops.get_state(engine, key)
    est = dict(st.get("estimates") or {})  # 분기 → {metric: 마지막 추정치, first_seen, last_seen}
    surprises = list(st.get("surprises") or [])
    done = {s["period"] for s in surprises}
    vals = parsed.get("values") or {}
    for p in parsed.get("periods") or []:
        k = p["key"]
        row = {m: vals.get(m, {}).get(k) for m in METRICS.values()}
        if p["consensus"]:
            prev = est.get(k) or {"first_seen": now.isoformat()}
            est[k] = {**prev, **{m: v for m, v in row.items() if v is not None}, "last_seen": now.isoformat(), "label": p["label"]}
        elif k in est and k not in done:
            e = est[k]
            base_m = "eps" if e.get("eps") and row.get("eps") is not None else "op_income"
            a, b = row.get(base_m), e.get(base_m)
            if a is not None and b not in (None, 0):
                surprises.append({"period": k, "label": p["label"], "date": now.date().isoformat(), "metric": base_m,
                                  "estimate": b, "actual": a, "surprise_pct": round((a - b) / abs(b) * 100, 2),
                                  "estimate_seen": e.get("last_seen")})
                done.add(k)
    nxt = next((p for p in parsed.get("periods") or [] if p["consensus"]), None)
    upcoming = None
    if nxt:
        k = nxt["key"]
        yoy = f"{int(k[:4]) - 1}{k[4:]}"
        cur = {m: vals.get(m, {}).get(k) for m in METRICS.values()}
        prev = {m: vals.get(m, {}).get(yoy) for m in METRICS.values()}
        upcoming = {"period": k, "label": nxt["label"], **cur,
                    "op_income_yoy": (cur["op_income"] / prev["op_income"] - 1) if cur.get("op_income") and prev.get("op_income") else None,
                    "revenue_yoy": (cur["revenue"] / prev["revenue"] - 1) if cur.get("revenue") and prev.get("revenue") else None}
    out = {"at": now.isoformat(), "estimates": est, "surprises": surprises[-24:], "upcoming": upcoming,
           "unit": "억원 (EPS 는 원)"}
    ops.set_state(engine, key, out)
    return out


def collect(engine, codes: list[str], fetch=None, now: datetime | None = None, pause: float = 0.3) -> dict:
    now = now or datetime.now(UTC)
    fails = ops.get_state(engine, "krcons_fail")
    done, failed = [], []
    for c in [x for x in codes if x.isdigit()][:40]:
        if fails.get(c) and now - datetime.fromisoformat(fails[c]) < FAIL_COOLDOWN:
            continue
        try:
            parsed = parse_quarter((fetch or fetch_naver)(c))
            if not parsed["periods"]:
                raise RuntimeError("컨센서스 형식 인식 실패")
        except Exception as e:  # noqa: BLE001
            fails[c] = now.isoformat()
            failed.append(f"{c}: {str(e)[:60]}")
            continue
        fails.pop(c, None)
        update(engine, c, parsed, now)
        done.append(c)
        if pause:
            time.sleep(pause)
    ops.set_state(engine, "krcons_fail", fails)
    return {"collected": len(done), "failed": failed[:10]}


def history_for_model(engine, code: str) -> list[dict]:
    """실적 서프라이즈 모델 입력 형식 (eps_est/eps 자리에 기준 지표)."""
    st = ops.get_state(engine, f"krcons:{code}")
    return [{"date": s["date"], "eps_est": s["estimate"], "eps": s["actual"], "surprise_pct": s["surprise_pct"],
             "metric": s["metric"]} for s in st.get("surprises") or []]


__all__ = ["parse_quarter", "update", "collect", "history_for_model", "fetch_naver"]

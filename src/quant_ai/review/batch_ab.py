"""묶음 호출 A/B — 여러 종목을 한 요청에 묶어 물어도(한도 절약) 판단 품질이 떨어지지 않나.

배정: 묶음이 켜진 AI 는 종목·날짜 해시로 약 20% 를 '하나씩' 묻는다 (무작위지만 재현 가능 → 선택 편향 없음).
비교: AI 별로 묶음 판단 vs 하나씩 판단의 적중률 · Brier (채점된 것만) · 두 비율 z 검정.
조정: 양쪽 200건 이상이고 하나씩이 유의하게(p<0.05) 나으면 → 그 AI 의 묶음 크기를 절반으로 (최소 1).
      400건 이상에서 차이가 없으면(p>0.5) 그대로. 결과는 ops 'batch_ab' · 'batch_override'.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from .. import ops

SINGLE_SHARE = 0.20
MIN_N = 200


def is_single_arm(symbol: str, t) -> bool:
    h = hashlib.sha256(f"{symbol}|{str(t)[:10]}".encode()).digest()
    return h[0] / 255 < SINGLE_SHARE


def batch_size_for(engine, analyst, default: int) -> int:
    ov = ops.get_state(engine, "batch_override").get(getattr(analyst, "name", ""), None)
    return int(ov) if ov else default


def _z(k1, n1, k2, n2) -> float | None:
    if min(n1, n2) == 0:
        return None
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (k2 / n2 - k1 / n1) / se if se > 0 else None


def _p(z: float | None) -> float | None:
    return None if z is None else math.erfc(abs(z) / math.sqrt(2))


def report(session, days: int = 180) -> dict:
    from sqlalchemy import select

    from ..data.models import AnalystOpinionRecord
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(select(AnalystOpinionRecord.analyst, AnalystOpinionRecord.prob_up, AnalystOpinionRecord.realized_return,
                                  AnalystOpinionRecord.payload).where(
        AnalystOpinionRecord.category == "direction", AnalystOpinionRecord.realized_return.is_not(None),
        AnalystOpinionRecord.as_of >= since)).all()
    by = defaultdict(lambda: {"batch": [], "single": []})
    batched_ai = set()
    for a, p, r, pl in rows:
        if p is None:
            continue
        b = int((pl or {}).get("batch") or 1)
        if b > 1:
            batched_ai.add(a)
        by[a]["batch" if b > 1 else "single"].append((p, 1.0 if r > 0 else 0.0))
    out = []
    for a in sorted(batched_ai):
        g = by[a]
        def st(xs):
            if not xs:
                return {"n": 0}
            k = sum((p >= 0.5) == (y > 0.5) for p, y in xs)
            return {"n": len(xs), "hits": k, "hit_rate": round(k / len(xs), 4),
                    "brier": round(sum((p - y) ** 2 for p, y in xs) / len(xs), 5)}
        b, s = st(g["batch"]), st(g["single"])
        z = _z(b.get("hits", 0), b["n"], s.get("hits", 0), s["n"]) if b["n"] and s["n"] else None
        pv = _p(z)
        verdict = ("표본 부족" if min(b["n"], s["n"]) < MIN_N else
                   "하나씩이 유의하게 나음 → 묶음 축소" if z is not None and z > 0 and pv < 0.05 else
                   "차이 없음 → 묶음 유지 (한도 절약)" if pv is not None and pv > 0.5 else "차이 불확실 — 계속 비교")
        out.append({"analyst": a, "batch": b, "single": s, "z": None if z is None else round(z, 2),
                    "p_value": None if pv is None else round(pv, 4), "verdict": verdict})
    return {"at": datetime.now(UTC).isoformat(), "single_share": SINGLE_SHARE, "min_n": MIN_N, "rows": out}


def adjust(engine, rep: dict, current: dict[str, int]) -> dict:
    """rep 판정에 따라 묶음 크기 조정 → batch_override 저장. current: {AI: 지금 묶음 크기}."""
    ov = dict(ops.get_state(engine, "batch_override"))
    changed = {}
    for r in rep.get("rows", []):
        a = r["analyst"]
        if r["verdict"].startswith("하나씩이 유의하게"):
            now = int(ov.get(a) or current.get(a) or 3)
            new = max(1, now // 2)
            if new != now:
                ov[a] = new
                changed[a] = (now, new)
    if changed:
        ops.set_state(engine, "batch_override", ov)
    return changed


__all__ = ["is_single_arm", "batch_size_for", "report", "adjust", "SINGLE_SHARE"]

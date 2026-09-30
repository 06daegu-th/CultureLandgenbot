"""독립 평가기 (Independent Evaluator) — 예측을 만든 코드를 믿지 않고 다시 채점한다.

  · 결과를 가격에서 직접 다시 계산해 저장된 채점과 대조 (불일치 = 데이터 수정 또는 채점 오류)
  · 기준선과 비교: 항상 '오른다'(기준율) · 20일 모멘텀 · 동전 던지기(50%)
  · 통계적 유의성: 적중률의 Wilson 95% 구간 · 이항 검정 p값(기준선 대비) · 신호 순수익 부트스트랩 95% 구간
  · Brier 분해 (Murphy): 신뢰도(작을수록 정직) · 분해능(클수록 구별) · 불확실성(시장 자체의 어려움)
  · 오차 분해: 어느 AI 가 틀렸나 · 어떤 요인이 반대로 갔나 · 확신도/충돌/국면/이벤트 여부별 · 틀린 이유 분포
  · 무결성: 예측 장부 해시 · 미래 정보 누수 감사
평가기는 기록을 읽기만 한다 (예측·채점을 고치지 않는다).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from ..control import factors as factor_levels
from .outcomes import forward
from .scorecard import _rows, expected_move, market_of, sigma_of

Z95 = 1.959964
SEALED_MIN = 0.5  # 평가 대상 중 해시 봉인된 기록 비율 하한 ("검증됨" 조건)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def binom_p(k: int, n: int, p0: float) -> float | None:
    """P(X ≥ k | n, p0) — 적중 k 개가 기준선 p0 로 우연히 나올 확률 (단측)."""
    if n <= 0:
        return None
    p0 = min(max(p0, 1e-9), 1 - 1e-9)
    if n > 400:  # 정규 근사 (연속성 보정)
        mu, sd = n * p0, math.sqrt(n * p0 * (1 - p0))
        z = (k - 0.5 - mu) / sd
        return 0.5 * math.erfc(z / math.sqrt(2))
    lp = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * math.log(p0) + (n - i) * math.log(1 - p0)
          for i in range(k, n + 1)]
    m = max(lp)
    return min(1.0, math.exp(m) * sum(math.exp(x - m) for x in lp))


def bootstrap_ci(x: list[float], n_boot: int = 2000, seed: int = 7) -> tuple[float, float] | None:
    if len(x) < 10:
        return None
    a = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    means = a[rng.integers(0, len(a), (n_boot, len(a)))].mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def murphy(probs: list[float], ys: list[int], bins: int = 10) -> dict:
    p, y = np.asarray(probs, float), np.asarray(ys, float)
    n = len(p)
    if n == 0:
        return {}
    ob = float(y.mean())
    idx = np.clip((p * bins).astype(int), 0, bins - 1)
    rel = res = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            k = int(m.sum())
            rel += k * (p[m].mean() - y[m].mean()) ** 2
            res += k * (y[m].mean() - ob) ** 2
    rel, res, unc = rel / n, res / n, ob * (1 - ob)
    return {"brier": float(np.mean((p - y) ** 2)), "reliability": round(rel, 5), "resolution": round(res, 5),
            "uncertainty": round(unc, 5), "skill": round((res - rel) / unc, 4) if unc > 0 else None}


def _corr(a: list[float], b: list[float]) -> float | None:
    """크기 예측력: 기대수익과 실제수익의 상관 (표본 20개 이상, 둘 다 변화가 있을 때만)."""
    if len(a) < 20 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _group(items, key, min_n=5) -> list[dict]:
    g = defaultdict(list)
    for x in items:
        g[key(x)].append(x)
    out = []
    for k, v in g.items():
        if len(v) >= min_n:
            out.append({"key": k, "n": len(v), "hit_rate": float(np.mean([x["hit"] for x in v])),
                        "avg_actual": float(np.mean([x["actual"] for x in v])),
                        "brier": float(np.mean([(x["prob_up"] - (1 if x["actual"] > 0 else 0)) ** 2 for x in v]))})
    return sorted(out, key=lambda r: -r["n"])


def _conf_bucket(c: float) -> str:
    return "낮음 (<50)" if c < 50 else "보통 (50~65)" if c < 65 else "높음 (65+)"


def evaluate(session, bars: dict[str, pd.DataFrame] | None = None, market: str | None = None, window: int = 500,
             cost: float = 0.0025, ledger: dict | None = None, leakage: dict | None = None,
             now: datetime | None = None) -> dict:
    """bars: 종목 → 일봉 (국내·미국 합쳐도 된다). ledger/leakage: 이미 계산한 무결성 결과."""
    bars = bars or {}
    now = now or datetime.now(UTC)
    rows = [r for r in _rows(session, market, None) if r.correct is not None and r.realized_return is not None][-window:]
    items, mismatches, recomputed = [], [], 0
    for r in rows:
        p = r.payload or {}
        h = int(p.get("horizon") or 5)
        stored = float(r.realized_return)
        mine = forward(bars.get(r.symbol), r.as_of, h)
        if mine is not None:
            recomputed += 1
            if abs(mine - stored) > 1e-4:
                mismatches.append({"id": r.id, "symbol": r.symbol, "stored": round(stored, 5), "recomputed": round(mine, 5)})
        actual = mine if mine is not None else stored  # 독립 계산 우선
        y = 1 if actual > 0 else 0
        price = ((p.get("evidence") or {}).get("price") or {})
        exp = p.get("expected_return")
        if exp is None:
            exp = expected_move(r.prob_up, sigma_of(p), h)[0]
        sign = 1 if r.action == "BUY" else -1 if r.action == "SELL" else 0
        items.append({
            "id": r.id, "symbol": r.symbol, "market": market_of(r.symbol), "prob_up": float(r.prob_up), "actual": actual,
            "sealed": getattr(r, "row_hash", None) is not None,
            "hit": (r.prob_up >= 0.5) == (actual > 0), "y": y, "action": r.action, "confidence": float(r.confidence),
            "conflict": r.conflict, "regime": p.get("regime") or "unknown", "trigger": bool(p.get("trigger")),
            "mom": price.get("ret_20"), "expected": exp, "net": sign * actual - cost if sign else None,
            "contribs": p.get("contributions") or [], "factors": factor_levels(p),
            "excess": (p.get("outcomes") or {}).get("excess"),
            "prompts": {k: (v or {}).get("prompt") for k, v in ((p.get("versions") or {}).get("roles") or {}).items()},
        })
    n = len(items)
    base = {"n": n, "recomputed": recomputed, "mismatches": mismatches[:20], "mismatch_rate": len(mismatches) / recomputed if recomputed else 0.0}
    if n == 0:
        return base | {"status": "insufficient", "message": "채점된 예측이 없습니다 — 예측 기간(보통 5거래일)이 지나면 평가합니다",
                       "ledger": ledger, "leakage": leakage, "evaluated_at": now.isoformat()}
    k = sum(x["hit"] for x in items)
    up_rate = float(np.mean([x["y"] for x in items]))
    mom = [x for x in items if x["mom"] is not None]
    baselines = {
        "coin": 0.5,
        "always_up": max(up_rate, 1 - up_rate),  # 항상 많이 나온 쪽을 찍는 전략
        "momentum": float(np.mean([(x["mom"] > 0) == (x["actual"] > 0) for x in mom])) if len(mom) >= 10 else None,
    }
    best_base = max(v for v in baselines.values() if v is not None)
    hit = k / n
    nets = [x["net"] for x in items if x["net"] is not None]
    exc = [x["excess"] for x in items if x["excess"] is not None]
    probs, ys = [x["prob_up"] for x in items], [x["y"] for x in items]
    # 오차 분해 ----------------------------------------------------------------
    per_ai = defaultdict(lambda: {"n": 0, "hits": 0, "brier": 0.0, "blame": 0.0, "right_when_consensus_wrong": 0})
    misses = [x for x in items if not x["hit"]]
    for x in items:
        for c in x["contribs"]:
            pa = c.get("prob_up")
            if pa is None:
                continue
            a = per_ai[c.get("analyst")]
            a["n"] += 1
            a["hits"] += int((pa >= 0.5) == (x["actual"] > 0))
            a["brier"] += (pa - x["y"]) ** 2
            if not x["hit"]:
                a["blame"] += float(c.get("weight") or 0) * (pa - x["y"]) ** 2
                a["right_when_consensus_wrong"] += int((pa >= 0.5) == (x["actual"] > 0))
    ai_rows = []
    for name, a in per_ai.items():
        if a["n"]:
            ai_rows.append({"analyst": name, "n": a["n"], "hit_rate": a["hits"] / a["n"], "brier": a["brier"] / a["n"],
                            "blame_share": 0.0, "saves": a["right_when_consensus_wrong"], "_blame": a["blame"]})
    tot_blame = sum(r["_blame"] for r in ai_rows) or 1.0
    for r in ai_rows:
        r["blame_share"] = r.pop("_blame") / tot_blame
    fac = defaultdict(lambda: [0, 0])
    for x in items:
        for f in x["factors"]:
            if abs(f["level"]) >= 2:  # 요인이 강하게 한쪽을 가리킨 경우만
                fac[f["key"]][0] += 1
                fac[f["key"]][1] += int((f["level"] > 0) == (x["actual"] > 0))
    factor_rows = [{"factor": k, "n": v[0], "hit_rate": v[1] / v[0]} for k, v in fac.items() if v[0] >= 5]
    from .review import classify_miss

    class _R:  # classify_miss 는 ConsensusRecord 모양을 받는다
        def __init__(self, r):
            self.payload, self.realized_return, self.confidence = r.payload, r.realized_return, r.confidence
    by_id = {r.id: r for r in rows}
    reasons = Counter(classify_miss(_R(by_id[x["id"]])).split(":")[0].split(" (")[0] for x in misses if x["id"] in by_id)
    prompt_rows = []
    pv = defaultdict(list)
    for x in items:
        for role, v in x["prompts"].items():
            if v:
                pv[(role, v)].append(x)
    for (role, v), xs in pv.items():
        if len(xs) >= 5:
            prompt_rows.append({"role": role, "prompt": v, "n": len(xs), "hit_rate": float(np.mean([y["hit"] for y in xs]))})
    ci = wilson(k, n)
    p_val = binom_p(k, n, best_base)
    sig = p_val is not None and p_val < 0.05 and hit > best_base
    integrity_ok = (ledger or {}).get("ok", True) and (leakage or {}).get("ok", True) and base["mismatch_rate"] < 0.01
    # 봉인 전(레거시) 기록은 사후 수정 여부를 증명할 수 없다 → 절반 이상이 봉인돼야 "검증됨"
    sealed_share = sum(x["sealed"] for x in items) / n
    proven = sealed_share >= SEALED_MIN
    verdict = ("검증됨: 기준선보다 유의하게 나음" if sig and integrity_ok and proven else
               "무결성 문제 — 성적을 믿기 전에 확인 필요" if not integrity_ok else
               f"기준선보다 나아 보임 — 단 봉인된 기록이 {sealed_share:.0%} 뿐이라 아직 증명 전" if sig else
               "아직 우연과 구분되지 않음" if n >= 100 else "표본 부족")
    return base | {
        "status": "pass" if sig and integrity_ok and proven else "fail" if not integrity_ok else "insufficient",
        "verdict": verdict, "sealed_share": sealed_share, "evaluated_at": now.isoformat(), "market": market or "ALL",
        "hit_rate": hit, "hits": k, "hit_ci95": ci, "p_value": p_val, "best_baseline": best_base, "baselines": baselines,
        "edge_vs_baseline": hit - best_base,
        "net_signal": {"n": len(nets), "mean": float(np.mean(nets)) if nets else None, "ci95": bootstrap_ci(nets)},
        "excess": {"n": len(exc), "mean": float(np.mean(exc)) if exc else None, "ci95": bootstrap_ci(exc)},
        "brier": murphy(probs, ys),
        "size_corr": _corr([x["expected"] for x in items if x["expected"] is not None],
                           [x["actual"] for x in items if x["expected"] is not None]),
        "errors": {
            "by_ai": sorted(ai_rows, key=lambda r: -r["blame_share"]), "by_factor": sorted(factor_rows, key=lambda r: -r["n"]),
            "by_confidence": _group(items, lambda x: _conf_bucket(x["confidence"])),
            "by_conflict": _group(items, lambda x: x["conflict"]), "by_regime": _group(items, lambda x: x["regime"]),
            "by_trigger": _group(items, lambda x: "이벤트 재분석" if x["trigger"] else "정기 판단"),
            "by_market": _group(items, lambda x: x["market"], 1), "by_prompt": prompt_rows,
            "miss_reasons": [{"reason": k2, "n": v} for k2, v in reasons.most_common(8)], "n_miss": len(misses),
        },
        "ledger": ledger, "leakage": leakage,
    }


__all__ = ["SEALED_MIN", "evaluate", "wilson", "binom_p", "bootstrap_ci", "murphy"]

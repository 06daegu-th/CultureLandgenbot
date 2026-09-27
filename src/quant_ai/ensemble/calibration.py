"""확률 보정 (Calibration) — "AI 가 72% 라고 하면 실제로 72% 맞는가?"

- 측정: 신뢰도 곡선(reliability curve), Brier, Log Loss, ECE(기대 보정 오차), 기저율 대비 Brier Skill
- 보정: AI 별 Platt 보정  p' = σ(a·logit(p) + b)
    · 채점된 방향 의견이 min_n 이상일 때만 적합 (그 전에는 원래 확률 그대로)
    · a 는 [0.05, 1.5] 로 제한 — 과신(a<1)은 줄이고, 소심함(a>1)은 조금만 늘린다
    · 항등(a=1, b=0) 쪽으로 L2 수축 → 표본이 적을 때 과적합 방지
- 적합은 과거(채점 끝난) 의견으로, 적용은 앞으로의 의견에 → 미래 정보 누설 없음
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysts.base import DIRECTION
from ..data.models import AnalystOpinionRecord, ConsensusRecord

EPS = 1e-4


def _logit(p):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _sigmoid(x):
    return 1 / (1 + np.exp(-np.asarray(x, float)))


def metrics(probs, outcomes, bins: int = 10) -> dict:
    """probs: 예측 상승확률, outcomes: 실제 상승(1)/하락(0)."""
    p = np.clip(np.asarray(probs, float), EPS, 1 - EPS)
    y = np.asarray(outcomes, float)
    n = len(p)
    if n == 0:
        return {"n": 0}
    base = float(y.mean())
    brier = float(np.mean((p - y) ** 2))
    brier_ref = float(np.mean((base - y) ** 2))
    logloss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    curve, ece = [], 0.0
    for b in range(bins):
        m = idx == b
        if not m.any():
            continue
        mp, fr, k = float(p[m].mean()), float(y[m].mean()), int(m.sum())
        curve.append({"lo": round(float(edges[b]), 2), "hi": round(float(edges[b + 1]), 2),
                      "pred": round(mp, 4), "actual": round(fr, 4), "n": k})
        ece += k / n * abs(mp - fr)
    return {"n": n, "base_rate": round(base, 4), "brier": round(brier, 5), "logloss": round(logloss, 5),
            "ece": round(ece, 4), "brier_skill": round(1 - brier / brier_ref, 4) if brier_ref > 0 else None,
            "accuracy": round(float(np.mean((p >= 0.5) == (y > 0.5))), 4), "curve": curve}


@dataclass
class Platt:
    a: float = 1.0
    b: float = 0.0
    n: int = 0

    def apply(self, p: float | None) -> float | None:
        if p is None:
            return None
        return float(_sigmoid(self.a * _logit(p) + self.b))

    @classmethod
    def fit(cls, probs, outcomes, l2: float = 2.0, iters: int = 200) -> Platt:
        """Newton 법으로 로그손실 + L2(항등 쪽) 최소화."""
        x = _logit(probs)
        y = np.asarray(outcomes, float)
        n = len(x)
        a, b = 1.0, 0.0
        for _ in range(iters):
            q = _sigmoid(a * x + b)
            r = q - y
            w = q * (1 - q)
            g = np.array([np.sum(r * x) + l2 * (a - 1), np.sum(r) + l2 * b])
            h = np.array([[np.sum(w * x * x) + l2, np.sum(w * x)], [np.sum(w * x), np.sum(w) + l2]])
            try:
                step = np.linalg.solve(h, g)
            except np.linalg.LinAlgError:
                break
            a, b = a - step[0], b - step[1]
            if np.max(np.abs(step)) < 1e-7:
                break
        return cls(a=float(np.clip(a, 0.05, 1.5)), b=float(np.clip(b, -1.0, 1.0)), n=n)


def _direction_rows(session: Session, since: datetime | None):
    q = select(AnalystOpinionRecord.analyst, AnalystOpinionRecord.prob_up, AnalystOpinionRecord.realized_return,
               AnalystOpinionRecord.payload, AnalystOpinionRecord.as_of).where(
        AnalystOpinionRecord.category == DIRECTION, AnalystOpinionRecord.correct.is_not(None),
        AnalystOpinionRecord.prob_up.is_not(None))
    if since is not None:
        q = q.where(AnalystOpinionRecord.as_of >= since)
    return session.execute(q).all()


def fit_calibrators(session: Session, window_days: int = 180, min_n: int = 50) -> dict[str, dict]:
    """AI 별 Platt 보정 적합. {analyst: {a, b, n, before, after}} — 표본 부족이면 제외."""
    since = datetime.now(UTC) - timedelta(days=window_days)
    by: dict[str, list] = defaultdict(list)
    for analyst, prob, rr, _payload, _ in _direction_rows(session, since):
        # prob_up 은 AI 가 말한 원래 확률 (보정값은 payload.prob_cal) → 이중 보정 없음
        by[analyst].append((prob, 1.0 if (rr or 0) > 0 else 0.0))
    out = {}
    for analyst, rows in by.items():
        if len(rows) < min_n:
            continue
        p, y = zip(*rows, strict=True)
        cal = Platt.fit(p, y)
        after = [cal.apply(x) for x in p]
        out[analyst] = {**asdict(cal), "before": _brief(metrics(p, y)), "after": _brief(metrics(after, y))}
    return out


def _brief(m: dict) -> dict:
    return {k: m.get(k) for k in ("n", "brier", "logloss", "ece", "accuracy")}


def load_calibrators(state: dict) -> dict[str, Platt]:
    return {k: Platt(v["a"], v["b"], v.get("n", 0)) for k, v in (state or {}).items()
            if isinstance(v, dict) and "a" in v}


def calibration_report(session: Session, window_days: int = 180, fitted: dict | None = None) -> dict:
    """대시보드용: AI 별 · 합의 신호의 신뢰도 곡선과 지표."""
    since = datetime.now(UTC) - timedelta(days=window_days)
    by: dict[str, list] = defaultdict(list)
    for analyst, prob, rr, payload, _ in _direction_rows(session, since):
        by[analyst].append((prob, (payload or {}).get("prob_cal", prob), 1.0 if (rr or 0) > 0 else 0.0))
    analysts = {}
    for analyst, rows in sorted(by.items()):
        raw, used, y = zip(*rows, strict=True)
        analysts[analyst] = {"raw": metrics(raw, y), "used": metrics(used, y),
                             "calibrator": (fitted or {}).get(analyst)}
    cons = session.execute(select(ConsensusRecord.prob_up, ConsensusRecord.realized_return).where(
        ConsensusRecord.correct.is_not(None), ConsensusRecord.as_of >= since)).all()
    consensus = metrics([c[0] for c in cons], [1.0 if (c[1] or 0) > 0 else 0.0 for c in cons]) if cons else {"n": 0}
    return {"window_days": window_days, "analysts": analysts, "consensus": consensus}


__all__ = ["Platt", "metrics", "fit_calibrators", "load_calibrators", "calibration_report"]

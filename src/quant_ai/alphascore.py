"""알파 기준 채점 · '조용한 장' 규칙 (사전 등록).

실패 연구 결과: AI 적중의 대부분이 '시장 방향 맞히기'였다 (시장이 조용한 구간 적중 39% vs 움직일 때 62%).
그래서 두 가지를 따로 잰다.

alpha_card()  같은 판단을 '시장 대비 초과수익(excess = 종목 − 지수, 같은 기간)'으로 다시 채점:
              초과 적중(상승확률≥50% ↔ 지수보다 더 오름) · 기준(지수보다 더 오른 비율) · 초과 Brier Skill · 초과 알파 ·
              '시장 방향 덕분' 비중 = 원래 적중 − 초과 적중
quiet_rule()  가설(사전 등록): "지수의 직전 20거래일 변동성이 1년 중 하위 1/3(조용한 장)일 때 AI 의 초과 적중은 기준 이하"
              · 조용한지 여부는 그 날까지의 과거 가격만으로 정한다 (미래 정보 없음)
              · 등록 시각 이후 판단만 '전진 검증'으로 판정, 이전 판단은 '참고(사후)'로 따로 표시
              · QUANT_AI_QUIET_THROTTLE=true 면 조용한 장에서 AI 위성 매수를 쉰다 (기본 꺼짐 — 전진 검증 통과 전)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import ops
from .asof import label
from .review.evaluator import wilson

PREREG_KEY = "prereg:quiet_market"
QUIET_PCT = 1 / 3


def _rows(app, n: int = 2000):
    from .data.db import session_scope
    from .data.models import ConsensusRecord
    with session_scope(app.engine) as s:
        out = []
        for r in s.scalars(select(ConsensusRecord).where(ConsensusRecord.realized_return.is_not(None))
                           .order_by(ConsensusRecord.as_of.desc()).limit(n)):
            ex = ((r.payload or {}).get("outcomes") or {}).get("excess")
            if ex is not None:
                out.append({"symbol": r.symbol, "as_of": r.as_of, "p": r.prob_up, "ret": r.realized_return, "excess": float(ex),
                            "created_at": r.created_at})
    return out


def _score(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    p = np.array([r["p"] for r in rows])
    ex = np.array([r["excess"] for r in rows])
    raw = np.array([r["ret"] for r in rows])
    y = (ex > 0).astype(float)
    hit = float(np.mean((p >= 0.5) == (y == 1)))
    hit_raw = float(np.mean((p >= 0.5) == (raw > 0)))
    base = float(y.mean())
    ref = float(np.mean((base - y) ** 2))
    brier = float(np.mean((p - y) ** 2))
    picked = p >= 0.5
    alpha = float(ex[picked].mean() - ex.mean()) if picked.any() and (~picked).any() else None
    ci = wilson(int(round(hit * n)), n)
    return {"n": n, "hit_excess": round(hit, 3), "ci95": [round(ci[0], 3), round(ci[1], 3)] if ci else None,
            "base_excess": round(base, 3), "hit_raw": round(hit_raw, 3), "market_share": round(hit_raw - hit, 3),
            "brier_skill_excess": round(1 - brier / ref, 3) if ref > 0 else None,
            "alpha_excess": None if alpha is None else round(alpha, 4), "mean_excess_picked": round(float(ex[picked].mean()), 4) if picked.any() else None}


def alpha_card(app, n: int = 1000) -> dict:
    rows = _rows(app, n)
    sc = _score(rows)
    if not sc.get("n"):
        return {"n": 0, "message": "시장 대비 결과(excess)가 붙은 판단이 아직 없음 — 결과 매칭 작업 후 채워짐"}
    beat = sc["ci95"] and sc["ci95"][0] > max(0.5, sc["base_excess"])
    worse = sc["ci95"] and sc["ci95"][1] < max(0.5, sc["base_excess"])
    sc["verdict"] = ("종목 선택력 있음 (시장 대비 적중이 유의하게 높음)" if beat else
                     "종목 선택력이 기준보다 유의하게 낮음" if worse else "종목 선택력은 우연과 구분 안 됨")
    sc["explain"] = (f"원래 적중 {sc['hit_raw']:.1%} 중 시장 방향 덕분 {sc['market_share'] * 100:+.1f}%p — "
                     f"시장 대비로 채점하면 {sc['hit_excess']:.1%} (기준 {sc['base_excess']:.1%})")
    return sc


# ------------------------------------------------------------------ 조용한 장 규칙
def quiet_series(bench: pd.DataFrame | None, window: int = 20, lookback: int = 250) -> pd.Series:
    """날짜 → 그날까지의 과거만 쓴 '조용함' (직전 20일 변동성이 직전 1년 중 하위 1/3)."""
    if bench is None or len(bench) < window + 30:
        return pd.Series(dtype=bool)
    r = bench["close"].astype(float).pct_change()
    vol = r.rolling(window).std()
    thr = vol.rolling(lookback, min_periods=60).quantile(QUIET_PCT)
    q = (vol <= thr).where(thr.notna())
    q.index = pd.DatetimeIndex(q.index).normalize().tz_localize(None) if pd.DatetimeIndex(q.index).tz is not None else pd.DatetimeIndex(q.index).normalize()
    return q.dropna().astype(bool)


def is_quiet(app, when: datetime | None = None) -> bool | None:
    bars, bench, _ = app.market_data()
    q = quiet_series(bench)
    if q.empty:
        return None
    if when is None:
        return bool(q.iloc[-1])
    t = pd.Timestamp(when).tz_convert(None).normalize() if pd.Timestamp(when).tzinfo else pd.Timestamp(when).normalize()
    prior = q[q.index <= t]
    return bool(prior.iloc[-1]) if len(prior) else None


def register(app, now: datetime | None = None) -> dict:
    st = ops.get_state(app.engine, PREREG_KEY)
    if st.get("registered_at"):
        return st
    st = {"registered_at": (now or datetime.now(UTC)).isoformat(),
          "hypothesis": "지수의 직전 20거래일 변동성이 1년 중 하위 1/3 인 '조용한 장'에서 AI 의 시장 대비 적중은 기준 이하다",
          "decision": "전진 판단 200건 이상 · 조용한 장 적중 95% 상한 < 기준 → 채택(조용한 장에서 AI 위성 매수 쉬기 권장) · 아니면 기각",
          "min_n": 200}
    ops.set_state(app.engine, PREREG_KEY, st)
    return st


def quiet_rule(app, now: datetime | None = None) -> dict:
    reg = register(app, now)
    t0 = pd.Timestamp(reg["registered_at"])
    _, bench, _ = app.market_data()
    q = quiet_series(bench)
    rows = _rows(app, 5000)

    def tag(r):
        t = pd.Timestamp(r["as_of"])
        t = (t.tz_convert(None) if t.tzinfo else t).normalize()
        prior = q[q.index <= t] if not q.empty else q
        return bool(prior.iloc[-1]) if len(prior) else None
    for r in rows:
        r["quiet"] = tag(r)
        ca = pd.Timestamp(r["created_at"]) if r["created_at"] is not None else None
        r["forward"] = ca is not None and (ca if ca.tzinfo else ca.tz_localize("UTC")) >= t0

    def part(rs):
        return {"quiet": _score([r for r in rs if r["quiet"] is True]), "active": _score([r for r in rs if r["quiet"] is False])}
    fwd = part([r for r in rows if r["forward"]])
    post = part([r for r in rows if not r["forward"]])
    fq = fwd["quiet"]
    if fq.get("n", 0) >= reg["min_n"]:
        decision = "채택" if fq["ci95"][1] < max(0.5, fq["base_excess"]) else "기각"
    else:
        decision = f"전진 검증 중 ({fq.get('n', 0)}/{reg['min_n']}건)"
    on = os.environ.get("QUANT_AI_QUIET_THROTTLE", "").lower() == "true"
    now_q = bool(q.iloc[-1]) if not q.empty else None
    return {"registration": reg | {"registered_label": label(reg["registered_at"])}, "forward": fwd, "in_sample": post,
            "decision": decision, "throttle_on": on, "quiet_now": now_q,
            "note": "사후(등록 전) 결과는 가설을 만든 데이터라 판정에 쓰지 않는다 · 조용함은 그날까지의 지수 가격만으로 계산"}


def throttle(app, buys: list[dict]) -> tuple[list[dict], str | None]:
    """QUANT_AI_QUIET_THROTTLE=true 이고 지금이 조용한 장이면 AI 위성 매수를 쉰다."""
    if not buys or os.environ.get("QUANT_AI_QUIET_THROTTLE", "").lower() != "true":
        return buys, None
    try:
        if is_quiet(app):
            return [], "조용한 장(지수 20일 변동성 하위 1/3) — AI 위성 매수 쉬기 (QUANT_AI_QUIET_THROTTLE)"
    except Exception:  # noqa: BLE001 - 판단 실패면 규칙을 적용하지 않는다
        return buys, None
    return buys, None


__all__ = ["alpha_card", "quiet_rule", "quiet_series", "is_quiet", "register", "throttle"]

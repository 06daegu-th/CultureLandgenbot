"""실제 시장 예측력 — "코드가 좋다"와 "돈을 벌 수 있다"를 구분하는 증거 체계.

증거는 세 층이고, 섞지 않는다.
 A. 사후 검증 (과거 실제 데이터 · 백테스트) — 실제 KRX 전 종목 일봉(상장폐지 포함)으로, 실시간 코어 전략과 '같은 점수 함수'의
    예측력을 잰다: 월별 순위 IC · 상위 5분위 적중 · 10분위 수익 스프레드 · 비용 후 초과수익 · 개발/검증 구간 분리 · 무작위 점수 대조군.
    한계: 과거에 통했다는 것뿐. 선택 편향을 줄이려고 개발 구간(≤2020)과 검증 구간(2021~)을 나눈다.
 B. 사전 등록 (pre-registration) — 앞으로의 성공 기준을 '결과를 보기 전에' 봉인한다: 지표 · 기준선 p0 · 목표 p1 · α · β · 시작 장부 번호.
 C. 전진 검증 (forward test) — 등록 이후 봉인된 예측만으로 순차 확률비 검정(SPRT)을 매일 갱신한다.
    SPRT 는 매일 들여다봐도 거짓 양성이 α 로 유지되는 검정이다 (p값을 매일 보는 것과 다름).
    판정: '예측력 있음(H1 채택)' · '예측력 없음(H0 채택)' · '계속 관찰' + 필요한 표본 수와 예상 판정일.
실제 돈을 쓰기 전에 필요한 것은 C 의 'H1 채택' 이다. A 는 참고, B 는 C 를 정직하게 만드는 장치다.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

Z = {0.05: 1.6449, 0.01: 2.3263, 0.10: 1.2816, 0.20: 0.8416}


# ------------------------------------------------------------------ 검정력 · 표본 수
def required_n(p0: float, p1: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """단측 이항 검정: 적중률 p1 이 p0 보다 높다는 것을 검출하는 데 필요한 예측 수 (정규 근사)."""
    za, zb = Z.get(alpha, 1.6449), Z.get(round(1 - power, 2), 0.8416)
    num = za * math.sqrt(p0 * (1 - p0)) + zb * math.sqrt(p1 * (1 - p1))
    return int(math.ceil((num / (p1 - p0)) ** 2))


def sprt(hits: list[bool], p0: float, p1: float, alpha: float = 0.05, beta: float = 0.2) -> dict:
    """Wald SPRT. LLR 이 상한 A 를 넘으면 H1(예측력 있음), 하한 B 아래면 H0(없음)."""
    a, b = math.log((1 - beta) / alpha), math.log(beta / (1 - alpha))
    w1, w0 = math.log(p1 / p0), math.log((1 - p1) / (1 - p0))
    llr, path, decided, at = 0.0, [], None, None
    for i, h in enumerate(hits):
        llr += w1 if h else w0
        path.append(round(llr, 4))
        if decided is None and llr >= a:
            decided, at = "H1", i + 1
        elif decided is None and llr <= b:
            decided, at = "H0", i + 1
    n = len(hits)
    k = sum(hits)
    # 앞으로의 기대 표본 (현재 적중률이 유지된다고 가정): 남은 거리 / 1건당 기대 LLR 증가
    p_hat = k / n if n else p0
    drift = p_hat * w1 + (1 - p_hat) * w0
    need = None
    if decided is None and n:
        if drift > 1e-9:
            need = int(math.ceil((a - llr) / drift))
        elif drift < -1e-9:
            need = int(math.ceil((llr - b) / -drift))
    return {"n": n, "hits": k, "hit_rate": round(p_hat, 4) if n else None, "llr": round(llr, 4),
            "upper": round(a, 4), "lower": round(b, 4), "decision": decided or "continue", "decided_at": at,
            "expected_more": need, "path": path[-300:],
            "label": {"H1": "예측력 있음 (사전 등록 기준 통과)", "H0": "예측력 없음 (기준 미달 확정)",
                      "continue": "계속 관찰 — 아직 판정할 만큼 증거가 쌓이지 않음"}[decided or "continue"]}


# ------------------------------------------------------------------ 사전 등록
def preregister(app, p0: float | None = None, p1: float | None = None, alpha: float = 0.05, beta: float = 0.2,
                metric: str = "합의 방향 적중률 (5거래일, 다음 날 시가 진입)", now: datetime | None = None,
                force: bool = False) -> dict:
    """결과를 보기 전에 성공 기준을 봉인한다. 이미 있으면 그대로 (force 로 새 등록 — 이력은 남는다)."""
    from sqlalchemy import func, select

    from .. import ops
    from ..data.db import session_scope
    from ..data.models import ConsensusRecord
    st = ops.get_state(app.engine, "prereg")
    if st.get("current") and not force:
        return st["current"]
    now = now or datetime.now(UTC)
    with session_scope(app.engine) as s:
        start_id = int(s.scalar(select(func.max(ConsensusRecord.id))) or 0)
        rows = s.execute(select(ConsensusRecord.realized_return).where(ConsensusRecord.realized_return.is_not(None))
                         .order_by(ConsensusRecord.id.desc()).limit(500)).all()
    up = [r[0] > 0 for r in rows]
    base = max(0.5, float(np.mean(up))) if len(up) >= 50 else 0.52
    p0 = round(p0 if p0 is not None else base, 4)
    p1 = round(p1 if p1 is not None else p0 + 0.04, 4)
    doc = {"version": len(st.get("history") or []) + 1, "registered_at": now.isoformat(), "metric": metric,
           "h0": f"적중률 = {p0:.1%} (기준선: 동전·많이 나온 쪽 중 높은 것)", "h1": f"적중률 ≥ {p1:.1%}",
           "p0": p0, "p1": p1, "alpha": alpha, "beta": beta, "start_after_id": start_id,
           "rule": "등록 이후 저장·봉인된 예측만 · 순차 확률비 검정 · 기준 변경 시 새 등록(이전 결과 유지)",
           "required_n_fixed": required_n(p0, p1, alpha, 1 - beta)}
    doc["hash"] = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    ops.set_state(app.engine, "prereg", {"current": doc, "history": (st.get("history") or []) + [doc]})
    return doc


def forward_test(session, prereg: dict, now: datetime | None = None) -> dict:
    from sqlalchemy import select

    from ..data.models import ConsensusRecord
    rows = session.execute(select(ConsensusRecord.id, ConsensusRecord.as_of, ConsensusRecord.prob_up,
                                  ConsensusRecord.realized_return, ConsensusRecord.row_hash, ConsensusRecord.created_at)
                           .where(ConsensusRecord.id > prereg["start_after_id"]).order_by(ConsensusRecord.id)).all()
    sealed = [r for r in rows if r.row_hash]
    scored = [r for r in sealed if r.realized_return is not None]
    hits = [(r.prob_up >= 0.5) == (r.realized_return > 0) for r in scored]
    res = sprt(hits, prereg["p0"], prereg["p1"], prereg["alpha"], prereg["beta"])
    now = now or datetime.now(UTC)
    # 하루에 채점되는 예측 수 → 예상 판정일
    rate = None
    if len(scored) >= 5:
        t0 = scored[0].as_of if scored[0].as_of.tzinfo else scored[0].as_of.replace(tzinfo=UTC)
        t1 = scored[-1].as_of if scored[-1].as_of.tzinfo else scored[-1].as_of.replace(tzinfo=UTC)
        days = max((t1 - t0).days, 1)
        rate = len(scored) / days
    eta = None
    if res["decision"] == "continue":
        more = res["expected_more"] if res["expected_more"] is not None else max(prereg["required_n_fixed"] - len(scored), 0)
        if rate and more is not None:
            eta = (now + timedelta(days=more / rate)).date().isoformat()
        res["more_needed"] = more
    return res | {"prereg": {k: prereg[k] for k in ("version", "registered_at", "p0", "p1", "alpha", "beta", "hash", "metric")},
                  "since_registration": len(rows), "sealed": len(sealed), "scored": len(scored),
                  "per_day": None if rate is None else round(rate, 2), "eta": eta}


# ------------------------------------------------------------------ A. 실제 KRX 데이터 사후 검증
def _t(x: np.ndarray) -> float | None:
    x = x[np.isfinite(x)]
    if len(x) < 3 or x.std(ddof=1) == 0:
        return None
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def signal_study(bars: dict[str, pd.DataFrame], eligible: pd.DataFrame, bench: pd.DataFrame | None = None,
                 horizon: int = 20, dev_end: str = "2020-12-31", top_k: int = 20, cost_bps: float = 45.0,
                 weights: dict | None = None, seed: int = 11) -> dict:
    """실시간 코어와 같은 점수 함수의 예측력 (20거래일 간격 · 그 시점 유니버스 · 미래 정보 없음).

    cost_bps: 한 번 갈아타는 왕복 비용 (수수료·세금·슬리피지) — 상위 20 초과수익에서 회전율만큼 뺀다."""
    from ..engines.factors import factor_score_cross_section, price_factors
    closes = pd.DataFrame({s: b["close"] for s, b in bars.items()}).sort_index()
    feats = {s: price_factors(closes[s].dropna()) for s in closes.columns}
    dates = closes.index
    rng = np.random.default_rng(seed)
    recs, prev_top = [], set()
    for i in range(260, len(dates) - horizon, horizon):
        t = dates[i]
        if t not in eligible.index:
            continue
        uni = [s for s in eligible.columns if eligible.at[t, s] and s in feats]
        latest = pd.DataFrame({s: feats[s].loc[:t].iloc[-1] for s in uni if len(feats[s].loc[:t])}).T
        if len(latest) < 30:
            continue
        score = factor_score_cross_section(latest, weights)
        fwd = (closes.iloc[i + horizon] / closes.iloc[i] - 1).reindex(score.index).dropna()
        sc = score.reindex(fwd.index)
        if len(fwd) < 30:
            continue
        ic = float(sc.rank().corr(fwd.rank()))
        placebo = float(pd.Series(rng.permutation(sc.to_numpy()), index=sc.index).rank().corr(fwd.rank()))
        q = pd.qcut(sc.rank(method="first"), 10, labels=False)
        dec = fwd.groupby(q).mean()
        top = set(sc.nlargest(top_k).index)
        turn = 1 - len(top & prev_top) / top_k if prev_top else 1.0
        prev_top = top
        med = float(fwd.median())
        top5 = sc.nlargest(max(len(sc) // 5, 1)).index
        recs.append({"date": t, "ic": ic, "placebo_ic": placebo, "spread": float(dec.iloc[-1] - dec.iloc[0]),
                     "top_hit": float((fwd.loc[top5] > med).mean()), "top_excess": float(fwd.loc[list(top)].mean() - fwd.mean()),
                     "turnover": turn, "n": len(fwd), "deciles": [float(x) for x in dec.to_numpy()]})
    if not recs:
        return {"insufficient": True}
    df = pd.DataFrame(recs).set_index("date")
    df["top_excess_net"] = df["top_excess"] - df["turnover"] * cost_bps / 1e4
    cut = pd.Timestamp(dev_end, tz=df.index.tz) if df.index.tz is not None else pd.Timestamp(dev_end)

    def part(d: pd.DataFrame) -> dict:
        if len(d) < 3:
            return {"n_periods": len(d)}
        return {"n_periods": int(len(d)), "start": str(d.index[0].date()), "end": str(d.index[-1].date()),
                "ic_mean": round(float(d["ic"].mean()), 4), "ic_t": _round(_t(d["ic"].to_numpy())),
                "ic_positive_share": round(float((d["ic"] > 0).mean()), 3),
                "placebo_ic_mean": round(float(d["placebo_ic"].mean()), 4), "placebo_ic_t": _round(_t(d["placebo_ic"].to_numpy())),
                "top_quintile_hit": round(float(d["top_hit"].mean()), 4), "top_hit_t": _round(_t(d["top_hit"].to_numpy() - 0.5)),
                "decile_spread": round(float(d["spread"].mean()), 5),
                "top20_excess": round(float(d["top_excess"].mean()), 5), "top20_excess_net": round(float(d["top_excess_net"].mean()), 5),
                "top20_excess_net_t": _round(_t(d["top_excess_net"].to_numpy())),
                "annualized_excess_net": round(float((1 + d["top_excess_net"].mean()) ** (252 / horizon) - 1), 4),
                "avg_turnover": round(float(d["turnover"].mean()), 3),
                "deciles": [round(float(x), 5) for x in np.mean(np.array(d["deciles"].tolist()), axis=0)]}
    dev, hold = df[df.index <= cut], df[df.index > cut]
    yearly = [{"year": int(y), "ic": round(float(g["ic"].mean()), 4), "top20_excess_net": round(float(g["top_excess_net"].mean()), 5),
               "n": int(len(g))} for y, g in df.groupby(df.index.year)]
    h, dv = part(hold), part(dev)
    return {"horizon": horizon, "top_k": top_k, "cost_bps": cost_bps, "dev_end": dev_end, "all": part(df), "dev": dv,
            "holdout": h, "yearly": yearly, "ic_series": [{"date": str(t.date()), "ic": round(float(v), 4)} for t, v in df["ic"].items()],
            "verdict": study_verdict(dv, h), "grade": study_grade(dv, h),
            "caveat": "과거 백테스트다. 앞으로도 통한다는 보장이 아니며, 실제 돈의 근거는 전진 검증(C)이어야 한다."}


def _sig(x, k="ic_t", lim=2.0) -> bool:
    return x.get(k) is not None and x[k] >= lim


def study_grade(dev: dict, hold: dict) -> str:
    """strong: 두 구간 모두 순위 예측력 + 검증 구간 비용 후 초과 유의 · partial: 검증 구간 순위만 유의 · none."""
    if _sig(dev) and _sig(hold) and _sig(hold, "top20_excess_net_t"):
        return "strong"
    if _sig(hold) or _sig(dev):
        return "partial"
    return "none"


def study_verdict(dev: dict, hold: dict) -> str:
    parts = [f"개발 구간(≤{dev.get('end', '')[:4]}) 순위 IC {dev.get('ic_mean', 0):+.3f} (t={dev.get('ic_t')})"
             + (" 유의" if _sig(dev) else " — 우연과 구분 안 됨"),
             f"검증 구간 IC {hold.get('ic_mean', 0):+.3f} (t={hold.get('ic_t')})" + (" 유의" if _sig(hold) else " — 유의하지 않음"),
             f"비용 후 상위20 초과 {hold.get('top20_excess_net', 0) * 100:+.2f}%/20일 (t={hold.get('top20_excess_net_t')})"
             + (" 유의" if _sig(hold, "top20_excess_net_t") else " — 유의하지 않음")]
    g = study_grade(dev, hold)
    head = {"strong": "과거 데이터에서 일관된 예측력", "partial": "혼재: 순위 예측력은 일부 구간에서만 보이고, 비용 후 돈으로는 아직 증명 안 됨",
            "none": "과거 데이터에서 예측력 없음"}[g]
    return head + " — " + " · ".join(parts)


def _round(x, nd: int = 2):
    return None if x is None else round(x, nd)


__all__ = ["required_n", "sprt", "preregister", "forward_test", "signal_study", "study_verdict", "study_grade"]

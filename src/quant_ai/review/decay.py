"""모델 노후(Model decay) — 모델이 배운 패턴이 시간이 지나며 약해지고 있나.

같은 모델·같은 AI 의 채점된 예측을 시간 순서로 놓고
  · 이동 적중률 (최근 50건 창) 과 기준(처음 절반) 비교
  · 추세: 적중(0/1)을 시간에 회귀 → 기울기와 t 값 (음수·유의하면 노후)
  · CUSUM(누적 합) 경보: 기준보다 계속 못 맞히는 구간이 쌓이면 울린다 (한두 번 틀림엔 반응 안 함)
  · 모델 나이별 적중률: 학습 후 0~30일 · 30~60 · 60~90 · 90일+ → 반감기 추정
결과: stable / watch / decaying — decaying 이면 재학습 후보 트리거 · Readiness MODEL 경고.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

WINDOW = 50
CUSUM_K = 0.05  # 허용 여유 (적중률 5%p 이내 하락은 무시) — 동전 수준 잡음에 울리지 않게
CUSUM_H = 5.0  # 경보 문턱 (누적 표준화 손실) · 모의실험: 안정 300건 오경보 2% · 62%→45% 하락 검출 83%


def _slope(y: np.ndarray) -> tuple[float, float | None]:
    n = len(y)
    if n < 20:
        return 0.0, None
    x = np.arange(n, dtype=float)
    x -= x.mean()
    b = float((x * (y - y.mean())).sum() / (x * x).sum())
    resid = y - y.mean() - b * x
    se = float(np.sqrt((resid ** 2).sum() / (n - 2) / (x * x).sum())) if n > 2 else 0.0
    return b, (b / se if se > 0 else None)


def cusum(hits: np.ndarray, ref: float, k: float = CUSUM_K, h: float = CUSUM_H) -> dict:
    """하방 CUSUM: S_t = max(0, S_{t-1} + (ref − k − hit_t)/σ). S > h 면 경보."""
    sd = float(np.sqrt(max(ref * (1 - ref), 1e-6)))
    s, alarm_at, path = 0.0, None, []
    for i, x in enumerate(hits):
        s = max(0.0, s + (ref - k - x) / sd)
        path.append(s)
        if alarm_at is None and s > h:
            alarm_at = i
    return {"stat": round(s, 3), "alarm": alarm_at is not None, "alarm_index": alarm_at, "threshold": h,
            "path": [round(v, 3) for v in path[-120:]]}


def analyze(rows: list[dict], trained_at: datetime | None = None, label: str = "합의") -> dict:
    """rows: [{as_of, hit(bool), prob}] 시간순이 아니어도 된다."""
    rows = sorted((r for r in rows if r.get("hit") is not None), key=lambda r: r["as_of"])
    n = len(rows)
    if n < 40:
        return {"label": label, "n": n, "status": "insufficient", "message": f"채점 {n}건 — 노후 판단은 40건부터"}
    days = {}
    for r in rows:
        days.setdefault(r["as_of"].date() if hasattr(r["as_of"], "date") else r["as_of"], []).append(1.0 if r["hit"] else 0.0)
    if n / len(days) > 1.5:
        return _analyze_daily(days, n, label)
    y = np.array([1.0 if r["hit"] else 0.0 for r in rows])
    half = n // 2
    ref = float(y[:half].mean())
    recent = float(y[-WINDOW:].mean())
    roll = [round(float(y[max(0, i - WINDOW + 1):i + 1].mean()), 4) for i in range(WINDOW - 1, n)]
    b, t = _slope(y)
    cs = cusum(y[half:], ref)
    ages = []
    if trained_at is not None:
        tz = trained_at if trained_at.tzinfo else trained_at.replace(tzinfo=UTC)
        buckets = {"0~30일": [], "30~60일": [], "60~90일": [], "90일+": []}
        for r, v in zip(rows, y):
            a = r["as_of"] if r["as_of"].tzinfo else r["as_of"].replace(tzinfo=UTC)
            d = (a - tz).days
            if d < 0:
                continue
            k = "0~30일" if d < 30 else "30~60일" if d < 60 else "60~90일" if d < 90 else "90일+"
            buckets[k].append(v)
        ages = [{"age": k, "n": len(v), "hit_rate": round(float(np.mean(v)), 4) if v else None} for k, v in buckets.items()]
    drop = ref - recent
    late = float(y[half:].mean())
    p = (y[:half].sum() + y[half:].sum()) / n
    se = np.sqrt(p * (1 - p) * (1 / half + 1 / (n - half)))
    z = (late - ref) / se if se > 0 else 0.0
    decaying = bool(z < -2.0 and (cs["alarm"] or (t is not None and t < -2.0)) and ref - late > 0.05)
    watch = not decaying and (z < -1.5 or cs["alarm"])
    status = "decaying" if decaying else "watch" if watch else "stable"
    msg = {"decaying": f"최근 {WINDOW}건 적중 {recent:.0%} < 초기 {ref:.0%} (−{drop * 100:.1f}%p) · 하락이 우연이 아님 → 재학습 후보",
           "watch": f"최근 적중 {recent:.0%} vs 초기 {ref:.0%} — 지켜보는 단계",
           "stable": f"최근 적중 {recent:.0%} · 초기 {ref:.0%} — 노후 신호 없음"}[status]
    # 반감기: 초과 적중(적중−50%)이 나이에 따라 지수적으로 준다고 보고 추정
    hl = None
    pts = [(i, a["hit_rate"] - 0.5) for i, a in enumerate(ages) if a["hit_rate"] is not None and a["n"] >= 15]
    if len(pts) >= 2 and pts[0][1] > 0 and pts[-1][1] > 0 and pts[-1][1] < pts[0][1]:
        k = np.log(pts[-1][1] / pts[0][1]) / ((pts[-1][0] - pts[0][0]) * 30)
        hl = round(float(np.log(0.5) / k), 0) if k < 0 else None
    return {"label": label, "n": n, "status": status, "message": msg, "reference": round(ref, 4), "recent": round(recent, 4),
            "drop": round(drop, 4), "z_halves": round(float(z), 2), "slope_per_100": round(b * 100, 4), "t": None if t is None else round(t, 2),
            "cusum": cs, "rolling": roll[-150:], "by_age": ages, "half_life_days": hl}


def _analyze_daily(days: dict, n: int, label: str, window_days: int = 20) -> dict:
    """하루에 여러 종목을 판단하면 같은 날 판단들은 시장 방향을 함께 타서 독립이 아니다 (나쁜 하루 = 100건 동시 오답).
    그래서 일별 적중률을 한 관측으로 보고, 표준편차도 일별 값에서 직접 추정해 검정한다."""
    keys = sorted(days)
    y = np.array([float(np.mean(days[k])) for k in keys])
    m = len(y)
    if m < 20:
        return {"label": label, "n": n, "days": m, "status": "insufficient",
                "message": f"채점 {n}건이지만 {m}거래일 — 같은 날 판단은 함께 움직여서 노후 판단은 20거래일부터"}
    half = m // 2
    ref, late = float(y[:half].mean()), float(y[half:].mean())
    recent = float(y[-window_days:].mean())
    sd = float(np.std(y, ddof=1)) or 1e-6
    cs = cusum(y[half:], ref, k=CUSUM_K)  # 아래에서 sd 로 다시 표준화
    s, alarm_at = 0.0, None
    k = max(CUSUM_K, 0.5 * sd)  # 표준 CUSUM 여유 0.5σ (일별 적중률은 하루 시장 방향에 따라 크게 흔들린다)
    for i, x in enumerate(y[half:]):
        s = max(0.0, s + (ref - k - x) / sd)
        if alarm_at is None and s > CUSUM_H:
            alarm_at = i
    cs.update(stat=round(s, 3), alarm=alarm_at is not None, alarm_index=alarm_at)
    se = sd * np.sqrt(1 / half + 1 / (m - half))
    z = (late - ref) / se if se > 0 else 0.0
    b, t = _slope(y)
    decaying = bool(z < -2.0 and (cs["alarm"] or (t is not None and t < -2.0)) and ref - late > 0.05)
    watch = not decaying and ref - late > 0.02 and (z < -1.645 or cs["alarm"])
    status = "decaying" if decaying else "watch" if watch else "stable"
    msg = {"decaying": f"최근 {window_days}거래일 적중 {recent:.0%} < 초기 {ref:.0%} (일별 검정 z={z:.1f}) → 재학습·가중치 점검",
           "watch": f"최근 {window_days}거래일 적중 {recent:.0%} vs 초기 {ref:.0%} — 지켜보는 단계",
           "stable": f"최근 {window_days}거래일 적중 {recent:.0%} · 초기 {ref:.0%} — 저하 신호 없음"}[status]
    return {"label": label, "n": n, "days": m, "status": status, "message": msg, "reference": round(ref, 4), "recent": round(recent, 4),
            "drop": round(ref - recent, 4), "z_halves": round(float(z), 2), "slope_per_100": round(b * 100, 4),
            "t": None if t is None else round(t, 2), "cusum": cs, "rolling": [round(float(v), 4) for v in y[-150:]], "by_age": [],
            "half_life_days": None, "daily": True}


def report(session, trained_at: datetime | None = None, window: int = 30000, days: int = 250) -> dict:
    from datetime import timedelta

    from sqlalchemy import select

    from ..data.models import AnalystOpinionRecord, ConsensusRecord
    since = datetime.now(UTC) - timedelta(days=days)  # 건수가 아니라 기간: 하루 수십~수백 건이라 건수 창은 며칠밖에 안 된다
    rows = session.execute(select(ConsensusRecord.as_of, ConsensusRecord.prob_up, ConsensusRecord.realized_return)
                           .where(ConsensusRecord.realized_return.is_not(None), ConsensusRecord.as_of >= since)
                           .order_by(ConsensusRecord.id.desc()).limit(window)).all()
    cons = [{"as_of": a, "prob": p, "hit": (p >= 0.5) == (r > 0)} for a, p, r in rows]
    q = session.execute(select(AnalystOpinionRecord.as_of, AnalystOpinionRecord.prob_up, AnalystOpinionRecord.realized_return)
                        .where(AnalystOpinionRecord.analyst == "quant", AnalystOpinionRecord.category == "direction",
                               AnalystOpinionRecord.realized_return.is_not(None), AnalystOpinionRecord.as_of >= since)
                        .order_by(AnalystOpinionRecord.id.desc()).limit(window)).all()
    quant = [{"as_of": a, "prob": p, "hit": (p >= 0.5) == (r > 0)} for a, p, r in q if p is not None]
    c = analyze(cons, trained_at, "AI 합의")
    m = analyze(quant, trained_at, "퀀트 모델")
    worst = max((c, m), key=lambda x: {"decaying": 2, "watch": 1}.get(x["status"], 0))
    # AI 별 (뉴스·경제·공시 AI 등): 합의가 버텨도 한 AI 가 무너지면 그 AI 의 가중치만 문제 — 따로 감지해 알린다
    by_ai = {}
    rows_ai = session.execute(select(AnalystOpinionRecord.analyst, AnalystOpinionRecord.as_of, AnalystOpinionRecord.prob_up,
                                     AnalystOpinionRecord.realized_return)
                              .where(AnalystOpinionRecord.analyst.notin_(("quant", "risk")), AnalystOpinionRecord.category == "direction",
                                     AnalystOpinionRecord.realized_return.is_not(None), AnalystOpinionRecord.prob_up.is_not(None),
                                     AnalystOpinionRecord.as_of >= since)
                              .order_by(AnalystOpinionRecord.id.desc()).limit(window * 4)).all()
    for an, a, p, r in rows_ai:
        by_ai.setdefault(an, []).append({"as_of": a, "prob": p, "hit": (p >= 0.5) == (r > 0)})
    analysts = {an: {k: v for k, v in analyze(rs, None, an).items() if k not in ("rolling", "cusum", "by_age")}
                for an, rs in by_ai.items()}
    return {"status": worst["status"] if worst["status"] != "insufficient" else
            ("insufficient" if c["status"] == m["status"] == "insufficient" else "stable"),
            "message": worst.get("message"), "consensus": c, "quant": m, "analysts": analysts,
            "decaying_ais": sorted(a for a, v in analysts.items() if v["status"] == "decaying"),
            "at": datetime.now(UTC).isoformat()}


__all__ = ["analyze", "cusum", "report"]

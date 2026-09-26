"""Review 모드: 오늘 AI 가 왜 틀렸는지 분석 (자동 복기).

1. 결과가 확정된 의견/합의 신호를 채점
2. 어떤 AI 가 맞았나 / 어떤 국면·확신 구간에서 틀렸나
3. 크게 틀린 사례(고확신 오답) 원인 분류
4. 교훈을 RAG 메모리에 저장 → 다음 판단 때 유사 상황에서 AI 가 참고
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysts.memory import Memory
from ..data.models import ConsensusRecord, ReviewReport
from ..ensemble.tracker import CATEGORY_LABELS, scoreboard, scoreboard_table


def classify_miss(c: ConsensusRecord) -> str:
    p = c.payload or {}
    r = c.realized_return or 0.0
    contribs = p.get("contributions", [])
    right = [x["analyst"] for x in contribs if x.get("prob_up") is not None and (x["prob_up"] >= 0.5) == (r > 0)]
    if abs(r) >= 0.07:
        return "이벤트/뉴스 쇼크: 예측 범위를 벗어난 급변 (" + f"{r:+.1%})"
    if p.get("conflict") in ("medium", "high") and right:
        return f"의견 충돌 중 소수 의견이 맞음 ({', '.join(right)}) → 해당 AI 가중치 상향 필요"
    if c.confidence >= 70:
        return "과신: 높은 신뢰도였으나 오답 → 확률 보정(calibration) 점검"
    if p.get("regime") in ("sideways", "bear_volatile", "bull_volatile"):
        return f"국면({p.get('regime')})에서의 노이즈성 움직임"
    return "약한 신호 구간의 일반 오차"


def daily_review(session: Session, review_date: date, lookback_days: int = 30,
                 memory: Memory | None = None) -> ReviewReport:
    end = datetime.combine(review_date, datetime.max.time(), timezone.utc)
    start = end - timedelta(days=lookback_days)
    cons = session.scalars(select(ConsensusRecord).where(
        ConsensusRecord.correct.is_not(None), ConsensusRecord.as_of >= start, ConsensusRecord.as_of <= end)).all()

    summary: dict = {"window_days": lookback_days, "n_resolved": len(cons)}
    lessons: list[str] = []
    if cons:
        df = pd.DataFrame([{
            "symbol": c.symbol, "action": c.action, "prob": c.prob_up, "conf": c.confidence,
            "conflict": c.conflict, "regime": (c.payload or {}).get("regime"), "ret": c.realized_return,
            "correct": bool(c.correct),
        } for c in cons])
        summary["consensus_accuracy"] = float(df["correct"].mean())
        traded = df[df["action"].isin(["BUY", "SELL"])]
        summary["traded_accuracy"] = float(traded["correct"].mean()) if len(traded) else None
        summary["by_regime"] = df.groupby("regime", dropna=False)["correct"].agg(["mean", "count"]) \
            .reset_index().rename(columns={"mean": "accuracy"}).to_dict("records")
        df["conf_bucket"] = pd.cut(df["conf"], [0, 55, 65, 75, 101], labels=["<55", "55-65", "65-75", "75+"])
        summary["by_confidence"] = df.groupby("conf_bucket", observed=True)["correct"].agg(["mean", "count"]) \
            .reset_index().rename(columns={"mean": "accuracy"}).astype({"conf_bucket": str}).to_dict("records")
        summary["by_conflict"] = df.groupby("conflict")["correct"].agg(["mean", "count"]) \
            .reset_index().rename(columns={"mean": "accuracy"}).to_dict("records")
        # NO_TRADE 가 실제로 손실을 피했는가
        nt = df[df["action"] == "NO_TRADE"]
        if len(nt):
            summary["no_trade_avoided_loss_rate"] = float((nt["ret"] < 0).mean())

        misses = sorted((c for c in cons if not c.correct), key=lambda c: -c.confidence)[:10]
        summary["worst_misses"] = [{
            "symbol": c.symbol, "as_of": str(c.as_of), "action": c.action, "prob_up": round(c.prob_up, 3),
            "confidence": c.confidence, "realized": round(c.realized_return or 0, 4), "cause": classify_miss(c),
        } for c in misses]

        # 교훈 도출
        cal = {r["conf_bucket"]: r for r in summary["by_confidence"]}
        hi = cal.get("75+")
        if hi and hi["count"] >= 5 and hi["accuracy"] < 0.55:
            lessons.append(f"신뢰도 75+ 신호의 정확도가 {hi['accuracy']:.0%} 로 낮음 → 과신. 신뢰도 산식/임계값 상향 검토")
        for r in summary["by_regime"]:
            if r["count"] >= 10 and r["accuracy"] < 0.48:
                lessons.append(f"'{r['regime']}' 국면 정확도 {r['accuracy']:.0%} → 이 국면에서는 노출 축소")
        hc = next((r for r in summary["by_conflict"] if r["conflict"] == "high"), None)
        if hc and hc["count"] >= 5 and hc["accuracy"] < 0.5:
            lessons.append("의견 충돌 '높음' 구간은 실제로도 적중률이 낮음 → NO_TRADE 규칙 유지 타당")
        if summary.get("no_trade_avoided_loss_rate", 0) >= 0.55:
            lessons.append(f"NO_TRADE 판정의 {summary['no_trade_avoided_loss_rate']:.0%} 가 실제 하락을 피함 → Risk AI 유효")

    names = {"primary": "Primary AI", "nvidia": "NVIDIA AI", "quant": "Quant Model", "regime": "Market Regime",
             "risk": "Risk AI", "challenger": "Challenger"}
    board = scoreboard(session, since=start)
    summary["scoreboard"] = scoreboard_table(board)
    for row in summary["scoreboard"]:
        if row["n"] >= 20 and row["accuracy"] is not None:
            label = CATEGORY_LABELS.get(row["category"], row["category"])
            if row["accuracy"] >= 0.6:
                lessons.append(f"{names.get(row['analyst'], row['analyst'])} {label} 적중률 {row['accuracy']:.0%} (n={row['n']}) → 가중치 상향")
            elif row["accuracy"] < 0.45:
                lessons.append(f"{names.get(row['analyst'], row['analyst'])} {label} 적중률 {row['accuracy']:.0%} (n={row['n']}) → 가중치 자동 하향 중")

    report = ReviewReport(review_date=review_date, mode="review", created_at=datetime.now(timezone.utc),
                          summary=_clean(summary), lessons=lessons)
    session.add(report)
    if memory is not None:
        for m in summary.get("worst_misses", [])[:5]:
            memory.add(session, "review", f"{m['symbol']} {m['action']} 예측 실패 ({m['realized']:+.1%}): {m['cause']}",
                       ts=end, symbol=m["symbol"], meta={"outcome": m["realized"], "cause": m["cause"]})
        for lesson in lessons:
            memory.add(session, "review", f"복기 교훈: {lesson}", ts=end)
    return report


def _clean(obj):
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, float) and obj != obj:
        return None
    return obj


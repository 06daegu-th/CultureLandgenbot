"""24시간 관제실 — "컴퓨터만 켜 두면 AI 가 무엇을 하고 있나" 를 한 화면에.

수집 → 정리 → 독립 분석 → 최종 판단 → 예측 저장 → 실제 결과 비교 → 성적 누적 → 승격
각 단계의 마지막 실행 · 다음 실행 · 상태와, 방금 저장된 예측(요인 +++ 표시), 검증 사다리, 성적표 요약.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from . import ops
from .data.db import session_scope
from .data.models import AlertRecord, ConsensusRecord, Instrument, JobRun

# 작업 → (단계, 한글 이름, 주기 설명, 주기 초)
JOBS = {
    "krx_bootstrap": ("collect", "국내 주가 첫 적재", "켤 때 1회", 86400),
    "krx_data": ("collect", "국내 주가·거래량 갱신", "장 마감 후 6시간마다", 6 * 3600),
    "news": ("collect", "국내·해외 뉴스", "장중 5분", 300),
    "news_offhours": ("collect", "뉴스 (장외)", "장외 30분", 1800),
    "disclosures": ("collect", "기업 공시 (DART)", "10분", 600),
    "macro": ("collect", "경제지표·금리·환율 (FRED)", "장외 3시간", 3 * 3600),
    "community": ("collect", "커뮤니티·SNS", "30분", 1800),
    "price_watch": ("collect", "실시간 시세 · 급등락 감시", "장중 2.5분", 150),
    "market_pulse": ("organize", "시장 상황 재분석", "매시간", 3600),
    "event_reactions": ("organize", "공시 반응 통계", "하루 1회", 86400),
    "alert_scan": ("organize", "알림 정리 (신호·공시·뉴스·채점)", "2분", 120),
    "core_satellite": ("analyze", "국내 판단 → 매매 (코어+AI)", "장중 매시간", 3600),
    "decide_and_trade": ("analyze", "AI 합의 매매", "장중 15분", 900),
    "decide": ("analyze", "AI 판단", "30분", 1800),
    "ai_snapshot": ("analyze", "AI 판단 스냅샷 (장외)", "장외 6시간", 6 * 3600),
    "event_reanalyze": ("analyze", "새 뉴스·공시 → 즉시 재분석", "10분", 600),
    "us_cycle": ("analyze", "미국 시장 예측 · 가상 장부", "3시간", 3 * 3600),
    "review": ("compare", "실제 결과 채점 · 복기", "장외 6시간", 6 * 3600),
    "strategy_health": ("compare", "전략 건강검진", "장외 12시간", 12 * 3600),
    "ai_verdict": ("score", "AI 섀도 판정", "장외 12시간", 12 * 3600),
    "guardian": ("promote", "자동 킬스위치 감시", "5분", 300),
    "ladder": ("promote", "검증 사다리 (승격·강등)", "야간 6시간", 6 * 3600),
    "shadow_eval": ("promote", "후보 모델 Shadow 평가", "야간 12시간", 12 * 3600),
    "retrain_candidate": ("promote", "재학습 후보 (드리프트·성과 하락·7일)", "야간 하루 1회", 86400),
    "kis_ws": ("collect", "KIS 실시간 체결 (웹소켓)", "장중 상시", 60),
    "investor_flow": ("collect", "외국인·기관 수급", "장 마감 후", 6 * 3600),
    "dart_summary": ("organize", "공시 원문 AI 요약", "30분", 1800),
    "news_agent": ("organize", "뉴스 에이전트 (시장 뉴스 정리)", "장중 매시간", 3600),
    "news_agent_offhours": ("organize", "뉴스 에이전트 (장외)", "장외 3시간", 3 * 3600),
    "macro_agent": ("organize", "매크로 에이전트", "12시간", 12 * 3600),
    "sector_agent": ("organize", "섹터 에이전트", "12시간", 12 * 3600),
    "sector_fill": ("organize", "업종 지도 채우기", "장외 2시간", 2 * 3600),
    "knowledge_graph": ("organize", "지식 그래프 (동시언급·상관·업종)", "장외 6시간", 6 * 3600),
    "ledger_anchor": ("predict", "예측 장부 봉인 (해시 사슬)", "매시간", 3600),
    "match_outcomes": ("compare", "결과 매칭 (1·5·20일 · 초과수익)", "장외 3시간", 3 * 3600),
    "evaluation": ("score", "독립 평가 · 누수 감사", "야간 12시간", 12 * 3600),
    "drift": ("score", "데이터 드리프트 (PSI)", "야간 12시간", 12 * 3600),
    "morning_brief": ("score", "아침 브리핑 (평일 08:30)", "5분마다 시간 확인", 300),
    "daily_report": ("score", "일일 리포트 (16:10)", "5분마다 시간 확인", 300),
    "backup": ("promote", "DB 자동 백업", "하루 1회", 86400),
}
STAGES = [("collect", "24H 데이터 수집"), ("organize", "AI 자동 정리"), ("analyze", "AI 독립 분석"),
          ("decide", "최종 판단"), ("predict", "예측 저장"), ("compare", "실제 결과 비교"),
          ("score", "AI 성적 누적"), ("promote", "검증된 모델만 승격")]


def _lvl(x: float | None, scale: float) -> int:
    if x is None or not np.isfinite(x):
        return 0
    return int(max(-3, min(3, round(float(x) / scale))))


def factors(payload: dict) -> list[dict]:
    """판단에 쓴 재료를 −3 ~ +3 으로 (화면: 뉴스 +++ · 시장 ++ …)."""
    ev = (payload or {}).get("evidence") or {}
    px = ev.get("price") or {}
    news = [n.get("sentiment") for n in ev.get("news") or [] if n.get("sentiment") is not None]
    mk = ev.get("market") or {}
    out = [
        {"key": "뉴스", "level": _lvl(float(np.mean(news)) if news else None, 0.15), "n": len(news)},
        {"key": "추세", "level": _lvl(px.get("ret_20"), 0.04)},
        {"key": "시장", "level": _lvl((mk.get("score") - 50) if mk.get("score") is not None else None, 12)},
        {"key": "거래량", "level": _lvl((px.get("vol_ratio") - 1) if px.get("vol_ratio") is not None else None, 0.25)},
    ]
    cross = ev.get("cross_asset") or []
    if cross:
        c = cross[0]
        out.append({"key": str(c.get("name") or "해외"), "level": _lvl(c.get("corr"), 0.3)})
    return out


def _utc(t):
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


def control(app, now: datetime | None = None) -> dict:
    from .review.scorecard import scorecard
    now = now or datetime.now(UTC)
    eng = app.engine
    day0 = now - timedelta(hours=24)
    with session_scope(eng) as s:
        names = {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument))}
        runs: dict[str, dict] = {}
        for r in s.scalars(select(JobRun).where(JobRun.started_at >= now - timedelta(days=3))
                           .order_by(JobRun.started_at.desc()).limit(2000)):
            j = runs.setdefault(r.job, {"last": r.started_at, "ok": r.ok, "error": r.error, "runs": 0, "fails": 0})
            j["runs"] += 1
            j["fails"] += int(r.ok is False)
        cons = s.scalars(select(ConsensusRecord).order_by(ConsensusRecord.id.desc()).limit(24)).all()
        today = s.execute(select(ConsensusRecord.action, func.count()).where(ConsensusRecord.as_of >= day0 - timedelta(days=3))
                          .group_by(ConsensusRecord.action)).all()
        n_24h = s.scalar(select(func.count()).select_from(ConsensusRecord).where(ConsensusRecord.id > 0)) or 0
        pending = s.scalar(select(func.count()).select_from(ConsensusRecord).where(ConsensusRecord.correct.is_(None))) or 0
        alerts_24h = s.scalar(select(func.count()).select_from(AlertRecord).where(AlertRecord.ts >= day0)) or 0
        sc_kr = scorecard(s, "KR", 100, names=names, recent=8)
        sc_us = scorecard(s, "US", 100, names=names, recent=8)
    jobs = []
    for name, (stage, label, cadence, every) in JOBS.items():
        r = runs.get(name)
        last = _utc(r["last"]) if r else None
        jobs.append({"job": name, "stage": stage, "label": label, "cadence": cadence,
                     "last": last.isoformat() if last is not None else None,
                     "next": (last + timedelta(seconds=every)).isoformat() if last is not None else None,
                     "ok": r["ok"] if r else None, "error": (r or {}).get("error"), "runs": (r or {}).get("runs", 0),
                     "fails": (r or {}).get("fails", 0)})
    by_stage: dict[str, list[dict]] = {}
    for j in jobs:
        by_stage.setdefault(j["stage"], []).append(j)
    acts = {a: n for a, n in today}
    ladder = ops.get_state(eng, "ladder")
    stages = []
    for key, label in STAGES:
        js = [j for j in by_stage.get(key, []) if j["runs"]]
        last = max((j["last"] for j in js), default=None)
        status = "fail" if any(j["ok"] is False for j in js) else "ok" if js else "wait"
        detail = {
            "collect": f"작업 {len(js)}개 동작",
            "organize": "시장 흐름 · 알림 · 이벤트 반응",
            "analyze": f"최근 판단 {sum(acts.values())}건",
            "decide": " · ".join(f"{k} {v}" for k, v in sorted(acts.items())) or "판단 대기",
            "predict": f"결과 대기 {pending}건",
            "compare": f"채점 누적 {sc_kr['total_scored'] + sc_us['total_scored']}건",
            "score": (f"적중 {sc_kr['summary']['hit_rate']:.0%} (국내 최근 {sc_kr['summary']['n']}회)"
                      if sc_kr["summary"].get("n") else "채점 대기"),
            "promote": f"현재 단계: {ladder.get('stage') or 'backtest'}",
        }[key]
        if key in ("decide", "predict", "score"):
            status = "ok" if (acts if key == "decide" else pending if key == "predict" else sc_kr["summary"].get("n")) else "wait"
            last = _utc(cons[0].as_of).isoformat() if cons and key != "score" else last
        stages.append({"key": key, "label": label, "status": status, "last": last, "detail": detail})
    feed = []
    for c in cons:
        p = c.payload or {}
        feed.append({"id": c.id, "symbol": c.symbol, "name": names.get(c.symbol, c.symbol), "as_of": _utc(c.as_of).isoformat(),
                     "action": c.action, "prob_up": c.prob_up, "confidence": c.confidence,
                     "expected": p.get("expected_return"), "expected_1d": p.get("expected_1d"),
                     "horizon": p.get("horizon", 5), "last_close": ((p.get("evidence") or {}).get("price") or {}).get("last_close"),
                     "factors": factors(p), "trigger": p.get("trigger"), "correct": c.correct,
                     "realized": c.realized_return, "market": "KR" if c.symbol.isdigit() else "US"})
    return {
        "now": now.isoformat(), "stages": stages, "jobs": jobs, "feed": feed,
        "counts": {"predictions": n_24h, "pending": pending, "alerts_24h": alerts_24h},
        "scorecard": {"KR": {k: sc_kr[k] for k in ("summary", "last_30d", "total_scored", "days_tracked", "pending")},
                      "US": {k: sc_us[k] for k in ("summary", "last_30d", "total_scored", "days_tracked", "pending")}},
        "pulse": ops.get_state(eng, "market_pulse").get("hist", [])[-72:],
        "progress": {m: ops.get_state(eng, f"ai_progress:{m}") for m in ("KR", "US")},
        "scheduler_alive": any(j["last"] and _utc(j["last"]) >= now - timedelta(minutes=30) for j in jobs),
    }


__all__ = ["control", "factors", "JOBS", "STAGES"]

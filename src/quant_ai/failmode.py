"""장애가 나도 돈은 안전 — 부품별 장애 → 매매 동작 (모두 Fail-Closed).

| 장애 | 기존 포지션 | 신규 매수 | 매도·위험 축소 | 어디서 |
|---|---|---|---|---|
| AI 서버·LLM 다운 | 유지 | 코어는 계속 (AI 오버레이만 기권) | 가능 | analysts → 기권, 합의에서 제외 |
| 뉴스 수집 다운 | 유지 | **절반으로 제한** (새 악재를 모르는 상태) | 가능 | news_down → RiskEngine.event_caps |
| 증권사(KIS) 다운 | 증권사 기준 유지 | **차단** | 차단(주문 불가) · 자동 감시 HALTED | guardian · BROKER 관문 |
| DB 다운 | 유지 | **차단** (기록 못 하면 주문 안 함) | 차단 | 모든 사이클이 DB 기록 후 주문 |
| 시세·일봉 다운 | 유지 | **차단** | 가능(장부 기준) | stale guard · 장중 가격 15분 지연 → DATA 관문 |
| 휴장일·이벤트 캘린더 없음 | 유지 | **차단** | 가능 | Truth Center → EVENT 관문 |
| 매매 준비 점검 실패 | 유지 | **차단** | 가능 | readiness.buy_block |
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

MATRIX = [
    {"part": "AI 서버·LLM", "positions": "유지", "new_buys": "코어 계속 · AI 기권", "sells": "가능"},
    {"part": "뉴스 수집", "positions": "유지", "new_buys": "절반 제한", "sells": "가능"},
    {"part": "증권사(KIS)", "positions": "증권사 기준", "new_buys": "차단", "sells": "차단 (HALTED)"},
    {"part": "DB", "positions": "유지", "new_buys": "차단", "sells": "차단"},
    {"part": "시세·일봉", "positions": "유지", "new_buys": "차단", "sells": "가능"},
    {"part": "캘린더", "positions": "유지", "new_buys": "차단", "sells": "가능"},
    {"part": "매매 준비 점검", "positions": "유지", "new_buys": "차단", "sells": "가능"},
]
NEWS_DOWN_S = 6 * 3600


def news_down(app, now: datetime | None = None) -> str | None:
    """뉴스 수집이 돌던 시스템에서 연속 실패 3회 또는 6시간 넘게 성공 없음 → 사유 (처음부터 안 돌던 곳은 판단 안 함)."""
    from .data.db import session_scope
    from .data.models import JobRun
    now = now or datetime.now(UTC)
    with session_scope(app.engine) as s:
        rows = s.execute(select(JobRun.ok).where(JobRun.job.in_(("news", "news_offhours"))).order_by(JobRun.started_at.desc()).limit(3)).all()
        if not rows:
            return None
        last_ok = s.scalar(select(func.max(JobRun.started_at)).where(JobRun.job.in_(("news", "news_offhours")), JobRun.ok.is_(True)))
    if len(rows) >= 3 and all(r.ok is False for r in rows):
        return "뉴스 수집 연속 실패 — 새 악재를 모르는 상태라 신규 매수 절반 제한"
    if last_ok is not None:
        age = (now - (last_ok if last_ok.tzinfo else last_ok.replace(tzinfo=UTC))).total_seconds()
        if age > NEWS_DOWN_S:
            return f"뉴스 수집 {age / 3600:.0f}시간 멈춤 — 신규 매수 절반 제한"
    return None


def apply_caps(app, caps: dict, symbols, now: datetime | None = None) -> dict:
    """RiskEngine.event_caps 에 장애 제한을 더한다 (이미 더 작은 배수가 있으면 그대로)."""
    why = news_down(app, now)
    if not why:
        return caps
    out = dict(caps)
    for s in symbols:
        m, r = out.get(s, (1.0, None))
        if m > 0.5:
            out[s] = (0.5, why if r is None else f"{r} · {why}")
    return out


__all__ = ["MATRIX", "news_down", "apply_caps"]

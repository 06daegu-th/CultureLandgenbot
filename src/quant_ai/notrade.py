"""왜 거래하지 않았나 — 사려고 했는데 막히거나 줄어든 모든 경우를 이유별로.

출처 (모두 실제 기록)
  · 주문 저널: 리스크 엔진이 거부(rejected)·축소한 주문과 그 사유 · 미체결·취소 · 중복 방지
  · 매매 건너뜀: 데이터가 오래돼 사이클 전체를 건너뛴 날 · HALTED
  · AI 매수 신호인데 주문이 없던 날: 코어 전용 모드(AI 오버레이 꺼짐) · 위성 한도 · 신뢰도 등 — 계획 기록으로 설명
분류: 매매 준비 · 긴급 정지 · 이벤트 · 포트폴리오 한도 · 유동성 · 현금 · 일 한도 · 확률·신뢰도 · 데이터 · 중복 방지 · 증권사
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from . import ops
from .asof import label

CATS = [
    ("readiness", "매매 준비 NOT READY", ("매매 준비 상태", "NOT READY", "fail-closed")),
    ("kill", "긴급 정지 · HALTED", ("킬스위치", "HALTED")),
    ("event", "이벤트 위험 (실적 발표 등)", ("이벤트 위험", "실적 발표", "당일 · 고베타")),
    ("portfolio", "포트폴리오 한도 (종목·업종·묶음·VaR)", ("포트폴리오 한도", "업종 한도", "업종 ", "묶음", "VaR", "종목 비중 한도", "총노출 한도")),
    ("liquidity", "유동성 부족", ("유동성", "거래대금")),
    ("cash", "현금 · 1회 주문 한도", ("가용 현금", "1회 주문 한도")),
    ("daily", "일 주문·손실 한도", ("일 주문 한도", "일 손실 한도")),
    ("confidence", "확률 · 신뢰도 미달", ("확률 ", "최소 ")),
    ("data", "데이터 부족 · 오래됨", ("데이터", "갱신되지", "일봉")),
    ("dedupe", "중복 주문 방지", ("중복",)),
    ("broker", "증권사 미체결 · 취소", ("unfilled", "cancelled", "미체결", "잔량 취소")),
    ("ai_off", "AI 오버레이 꺼짐 (코어 전용)", ("코어 전용",)),
    ("ai_demoted", "AI 자동 강등 (성적 불량 → SHADOW)", ("SHADOW 강등", "자동 강등")),
    ("ai_quiet", "조용한 장 — AI 위성 매수 쉼 (사전 등록 규칙)", ("조용한 장",)),
]
LABEL = {k: v for k, v, _ in CATS}


def categorize(reasons: list[str] | str) -> str:
    text = " ".join(reasons) if isinstance(reasons, list) else str(reasons)
    for key, _, kws in CATS:
        if any(k in text for k in kws):
            return key
    return "other"


def report(app, mode: str = "paper", days: int = 30, symbol: str | None = None, now: datetime | None = None) -> dict:
    from .data.db import session_scope
    from .data.models import ConsensusRecord, Instrument, JournalEntry
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    rows = []
    with session_scope(app.engine) as s:
        names = {i.symbol: i.name for i in s.scalars(select(Instrument))}
        q = select(JournalEntry).where(JournalEntry.mode == mode, JournalEntry.ts >= since,
                                       JournalEntry.kind.in_(("order", "risk_block", "dedupe", "skip")))
        if symbol:
            q = q.where((JournalEntry.symbol == symbol) | (JournalEntry.symbol.is_(None)))
        entries = s.scalars(q.order_by(JournalEntry.ts.desc()).limit(3000)).all()
        buys_by_day = {}
        for e in entries:
            d = e.data or {}
            if e.kind == "order":
                st, reasons = d.get("status"), d.get("reasons") or []
                if d.get("side") == "buy" and st in ("filled", "partial"):
                    buys_by_day.setdefault(str(e.ts.date()), set()).add(e.symbol)
                shrunk = [r for r in reasons if "축소" in r or "→" in r]
                if st in ("filled", "partial") and not shrunk:
                    continue
                if d.get("side") != "buy" and st in ("filled", "partial"):
                    continue
                blocked = st not in ("filled", "partial")
                why = reasons or [st or ""]
                rows.append({"ts": label(e.ts), "symbol": e.symbol, "name": names.get(e.symbol, e.symbol), "blocked": blocked,
                             "outcome": "차단" if blocked else "축소", "category": categorize(why if blocked else shrunk),
                             "reasons": why[:4], "plan": d.get("reason"), "qty": d.get("qty")})
            else:
                rows.append({"ts": label(e.ts), "symbol": e.symbol, "name": names.get(e.symbol, e.symbol) if e.symbol else "전체",
                             "blocked": True, "outcome": "건너뜀" if e.kind == "skip" else "차단",
                             "category": (d.get("category") or categorize(e.message)), "reasons": [e.message[:200]], "plan": None, "qty": None})
        # AI 매수 신호인데 그날 매수 주문이 없던 경우
        cq = select(ConsensusRecord.symbol, ConsensusRecord.as_of, ConsensusRecord.prob_up, ConsensusRecord.confidence, ConsensusRecord.payload) \
            .where(ConsensusRecord.action == "BUY", ConsensusRecord.as_of >= since)
        if symbol:
            cq = cq.where(ConsensusRecord.symbol == symbol)
        ai_buys = s.execute(cq.order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()).limit(500)).all()
    plan = ops.get_state(app.engine, f"cs-plan:{mode}")
    cfg = plan.get("config") or {}
    ai_on = cfg.get("use_ai", not app.settings.core_only) if cfg else not getattr(app.settings, "core_only", False)
    sat = {x.get("symbol") for x in plan.get("satellite") or []}
    missed = []
    for sym, at, p, conf, _payload in ai_buys:
        day = str(at.date())
        if sym in buys_by_day.get(day, set()):
            continue
        if not ai_on:
            why, cat = "코어 전용 모드 — AI 매수 신호는 채점만 하고 주문하지 않음 (AI 켜기 판정 통과 전)", "ai_off"
        elif sym not in sat:
            why, cat = f"위성 후보 {cfg.get('satellite_k', '-')}자리 안에 못 듦 (신뢰도 {conf:.0f} · 기준 {cfg.get('satellite_min_confidence', '-')})", "confidence"
        else:
            why, cat = "위성 편입됐지만 주문 기록 없음 — 리스크 게이트 기록 확인", "other"
        missed.append({"ts": label(at), "symbol": sym, "name": names.get(sym, sym), "blocked": True, "outcome": "AI BUY 미실행",
                       "category": cat, "reasons": [why], "plan": f"AI BUY {p:.0%} · 신뢰도 {conf:.0f}", "qty": None})
    allrows = rows + missed
    cnt = Counter(r["category"] for r in allrows)
    return {"mode": mode, "days": days, "symbol": symbol, "n": len(allrows),
            "by_category": [{"key": k, "label": LABEL.get(k, "기타"), "n": v} for k, v in cnt.most_common()],
            "rows": sorted(allrows, key=lambda r: r["ts"] or "", reverse=True)[:200], "ai_on": ai_on,
            "headline": (f"최근 {days}일 막히거나 줄어든 매수 {len(rows)}건 · 실행되지 않은 AI BUY {len(missed)}건"
                         + (f" — 가장 많은 이유: {LABEL.get(cnt.most_common(1)[0][0], '기타')}" if cnt else ""))}


__all__ = ["report", "categorize", "CATS"]

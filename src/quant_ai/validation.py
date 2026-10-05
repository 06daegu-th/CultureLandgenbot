"""검증 진행표 — 기능보다 중요한 것: 실제 계좌·실시간·실측 비용·전진 예측 기록이 충분히 쌓였나.

  ① KIS 모의계좌 검증    시세·잔고·주문·취소 경로 + 1주 실제 체결 (kis-check --suite --fill)
  ② 실시간(WebSocket)    체결·호가 메시지 수신 · 가격 출처 중 실시간 비중 (REST 폴링 의존도)
  ③ 실측 슬리피지        실전/모의 실체결 50건(보정 적용) · 100건(체결 시뮬레이터 parity)
  ④ AI 전진(Forward) 기록 사전 등록 이후 봉인된 예측 · 판정까지 필요한 건수
  ⑤ 장기 실데이터 검증    봉인된 예측이 쌓인 거래일 수 (목표 250거래일 = 1년)
진행률 % 와 '다음에 할 일'을 준다. 숫자는 저장된 기록에서만 — 추정하지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from . import ops
from .asof import label


def _pct(x, target):
    return round(min(1.0, (x or 0) / target), 3) if target else None


def progress(app, now: datetime | None = None) -> dict:
    from .clock import KRX
    from .data.db import session_scope
    from .data.models import ConsensusRecord, OrderRecord
    now = now or datetime.now(UTC)
    st = app.settings
    kis = st.broker == "kis"
    items = []
    import os
    kis_keys = {k: bool(os.environ.get(k)) for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT")}
    v = ops.get_state(app.engine, "kis_validation")
    e2e_ok = any(h.get("e2e") for h in (v.get("history") or [])) or bool(v.get("e2e_verified"))
    steps = [bool(v.get("ok")), bool(v.get("order_path_verified")), bool(v.get("fill_path_verified") or v.get("fill_verified")), e2e_ok]
    items.append({"key": "kis", "title": "KIS 모의계좌 검증", "progress": round(sum(steps) / 4, 3) if kis else 0.0,
                  "status": "ok" if all(steps) else "warn" if kis else "setup",
                  "detail": (f"스위트 {'통과' if steps[0] else '미통과'} · 주문 경로 {'확인' if steps[1] else '미확인'} · 실체결 {'확인' if steps[2] else '미확인'}"
                             f" · 전 과정(주문→체결→장부→잔고) {'확인' if steps[3] else '미확인'}"
                             + (f" · {label(v.get('at'))}" if v.get("at") else "")) if kis else
                            "KIS 미설정 — 가짜 서버 테스트만 통과한 상태 · 키: " + " · ".join(f"{k.replace('KIS_', '')} {'✓' if ok else '✗'}" for k, ok in kis_keys.items()),
                  "steps": ["모의투자 신청 (한국투자증권 앱·홈페이지)", ".env 에 KIS_APP_KEY · KIS_APP_SECRET · KIS_ACCOUNT · KIS_ENV=demo",
                            "./run.sh kis-check --suite (시세·잔고·주문·취소·정정 경로)", "장중 ./run.sh kis-check --suite --fill (1주 실제 체결)",
                            "장중 ./run.sh kis-check --suite --e2e (주문→체결→장부→잔고 대조→중복 방지→되팔기→재시작 복구)",
                            "하루 1회 자동 재검증"],
                  "next": ("./run.sh kis-check --suite --fill (장중, 모의투자)" if not all(steps[:3]) else
                           "./run.sh kis-check --suite --e2e (장중, 모의투자)" if not steps[3] else "하루 1회 자동 재검증 중")})
    ws = ops.get_state(app.engine, "kis_ws")
    lq = ops.get_state(app.engine, "live_quotes")
    srcs = [q.get("source") for k, q in lq.items() if not k.startswith("_") and isinstance(q, dict)]
    ws_share = sum(1 for x in srcs if x and "kis" in str(x)) / len(srcs) if srcs else 0.0
    items.append({"key": "ws", "title": "실시간 WebSocket", "progress": round(min(1.0, (ws.get("ticks") or 0) / 1000) * 0.5 + ws_share * 0.5, 3) if kis else 0.0,
                  "status": "ok" if ws.get("state") == "on" and ws_share > 0.5 else "warn" if kis else "setup",
                  "detail": (f"상태 {ws.get('state', '기록 없음')} · 체결 {ws.get('ticks', 0)} · 호가 {ws.get('books', 0)} · 가격 중 실시간 비중 {ws_share:.0%}"
                             if kis else "KIS 미설정 — 가격은 네이버/Yahoo 폴링(REST)과 일봉"),
                  "next": "KIS 키 + QUANT_KIS_WS=true 로 장중 실행 → 체결 1,000건 · 실시간 비중 50%+" if ws_share < 0.5 else "유지"})
    with session_scope(app.engine) as s:
        live_fills = s.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.mode == "live", OrderRecord.status.in_(("filled", "partial")),
                                                                                  OrderRecord.ref_price.is_not(None))) or 0
        from datetime import timedelta
        recent = s.scalar(select(func.count()).select_from(OrderRecord).where(
            OrderRecord.mode == "live", OrderRecord.status.in_(("filled", "partial")), OrderRecord.ref_price.is_not(None),
            OrderRecord.created_at >= now - timedelta(days=14))) or 0
        n_sealed = s.scalar(select(func.count()).select_from(ConsensusRecord).where(ConsensusRecord.row_hash.is_not(None))) or 0
        first = s.scalar(select(func.min(ConsensusRecord.created_at)).where(ConsensusRecord.row_hash.is_not(None)))
        n_scored_sealed = s.scalar(select(func.count()).select_from(ConsensusRecord).where(ConsensusRecord.row_hash.is_not(None),
                                                                                         ConsensusRecord.correct.is_not(None))) or 0
    par = ops.get_state(app.engine, "execution_parity").get("rows") or []
    items.append({"key": "slippage", "title": "실측 슬리피지", "progress": _pct(live_fills, 100),
                  "status": "ok" if live_fills >= 100 else "warn" if live_fills else "setup" if not kis else "warn",
                  "detail": f"실체결 {live_fills}건 (50건: 비용 보정 적용 · 100건: 체결 시뮬레이터 검증) · parity 기록 {len(par)}건"
                            + (f" · 최근 14일 하루 {recent / 14:.1f}건 → 50건까지 약 {max(0, 50 - live_fills) / (recent / 14):.0f}일 · 100건까지 약 {max(0, 100 - live_fills) / (recent / 14):.0f}일"
                               if recent else " · 최근 14일 체결 없음 → 예상 완료일 계산 불가"),
                  "next": "모의투자로 소액 코어 매매를 운영해 체결을 쌓기" if live_fills < 100 else "보정 자동 적용 중"})
    fw = ops.get_state(app.engine, "prediction_power").get("forward") or {}
    need = (fw.get("n") or 0) + (fw.get("more_needed") or 0)
    items.append({"key": "forward", "title": "AI 전진(Forward) 기록", "progress": _pct(fw.get("n"), need) if need else 0.0,
                  "status": "ok" if fw.get("decision") in ("H1",) else "bad" if fw.get("decision") == "H0" else "warn",
                  "detail": f"사전 등록 후 채점 {fw.get('n', 0)}건 · 판정까지 약 {fw.get('more_needed') or '-'}건 · {fw.get('label') or '진행 중'}",
                  "next": "LLM 키로 매일 AI 판단이 쌓이게 두기 (조작 불가 — 봉인 후 채점)"})
    first_d = (first if first is None or first.tzinfo else first.replace(tzinfo=UTC))
    days = KRX.trading_days_between(first_d.date(), now.date()) if first_d else 0
    items.append({"key": "longterm", "title": "장기 실데이터 검증", "progress": _pct(days, 250),
                  "status": "ok" if days >= 250 else "warn",
                  "detail": f"봉인된 예측 {n_sealed:,}건 · 채점 {n_scored_sealed:,}건 · 기록 기간 {days}거래일 (목표 250 = 1년)"
                            + (f" · 시작 {label(first_d, with_time=False)}" if first_d else ""),
                  "next": "코드가 아니라 시간이 필요 — 스케줄러를 계속 켜 두기"})
    total = sum(i["progress"] or 0 for i in items) / len(items)
    return {"at": now.isoformat(), "as_of": label(now), "items": items, "overall": round(total, 3),
            "headline": f"실전 검증 진행률 {total:.0%} — 병목은 코드가 아니라 실제 계좌·실시간·시간(예측 기록)"}


__all__ = ["progress"]

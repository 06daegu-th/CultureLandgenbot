"""자동 킬스위치 (Guardian) — 버튼이 아니라 조건. 하나라도 critical 이면 LIVE TRADING → HALTED.

    □ Broker disconnect       증권사 API 연속 실패
    □ Quote stale             시세·주가 데이터가 오래됨 / 0·음수 시세
    □ Position mismatch       DB 장부 ≠ 증권사 잔고 가 연속으로 발생
    □ Duplicate order         같은 목표의 미완료 주문이 둘 이상 (멱등성 위반)
    □ Daily loss limit        당일 손실 한도의 1.5배
    □ Abnormal volatility     KOSPI 일간 ±5% (서킷브레이커급) / VIX 40+
    □ Data source conflict    증권사 시세 vs DB 종가가 가격제한폭(±30%) 밖으로 어긋남
    □ Model anomaly           champion 확률 붕괴 · 전진 적중률 붕괴 → 자동 롤백
    □ Risk engine unavailable 리스크 계산 연속 실패
    □ Database unavailable    DB 응답 없음

HALTED 는 매도까지 포함해 새 주문을 내지 않는다 (증권사·장부 상태를 모를 때 주문은 위험을 키운다).
자동 해제는 없다: 사람이 원인을 확인하고 대시보드/CLI 에서 해제한다. 해제해도 조건이 여전히 critical 이면 다시 멈춘다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from .. import ops
from ..data.db import session_scope
from ..data.models import OrderRecord, PortfolioSnapshot

log = logging.getLogger(__name__)

CONDITIONS = [
    ("broker", "Broker disconnect", "증권사 연결"),
    ("quote_stale", "Quote stale", "시세 신선도"),
    ("position", "Position mismatch", "장부 ↔ 증권사 잔고"),
    ("duplicate", "Duplicate order", "중복 주문"),
    ("daily_loss", "Daily loss limit", "일 손실 한도"),
    ("volatility", "Abnormal volatility", "시장 급변"),
    ("data_conflict", "Data source conflict", "데이터 소스 충돌"),
    ("model", "Model anomaly", "모델 이상"),
    ("risk_engine", "Risk engine unavailable", "리스크 엔진"),
    ("database", "Database unavailable", "데이터베이스"),
    ("total_loss", "Total loss limit", "원금 대비 최대 손실"),
]
OPEN = ("pending", "submitted", "partial")


def _c(key: str, status: str, detail: str, **extra) -> dict:
    meta = next(x for x in CONDITIONS if x[0] == key)
    return {"key": key, "name": meta[1], "label": meta[2], "status": status, "detail": detail, **extra}


def _health(app, key: str, crit_fails: int = 3) -> tuple[str, str]:
    st = ops.get_state(app.engine, f"health:{key}")
    fails = int(st.get("fails") or 0)
    if not st:
        return "ok", "기록 없음 (아직 호출 전)"
    if fails >= crit_fails:
        return "critical", f"연속 {fails}회 실패: {st.get('error') or ''}"[:200]
    if fails:
        return "warn", f"최근 실패 {fails}회: {st.get('error') or ''}"[:200]
    return "ok", f"정상 (마지막 성공 {str(st.get('ok_at', ''))[:16]})"


def check_database(app) -> dict:
    try:
        with app.engine.connect() as c:
            c.execute(text("SELECT 1"))
        return _c("database", "ok", "응답 정상")
    except Exception as e:  # noqa: BLE001
        return _c("database", "critical", f"DB 응답 없음: {e}"[:200])


def check_broker(app, mode: str) -> dict:
    if not (mode == "live" and app.settings.broker == "kis"):
        return _c("broker", "na", "증권사 미연결 (가상매매)")
    status, detail = _health(app, "broker")
    return _c("broker", status, detail)


def check_quotes(app, mode: str, bars_last=None, now: datetime | None = None, market_open: bool = False) -> dict:
    now = now or datetime.now(UTC)
    limit = app.settings.max_data_age_days
    if bars_last is not None and limit:
        age = app.data_age_days(bars_last, now)
        if age > limit:
            return _c("quote_stale", "critical", f"주가 데이터가 {age}영업일 전 것 ({bars_last.date()}) > 한도 {limit}")
    if mode == "live" and app.settings.broker == "kis":
        q = ops.get_state(app.engine, "live_quotes")
        if q.get("bad"):
            return _c("quote_stale", "critical", f"0 또는 음수 시세 {q['bad']}종목")
        if market_open and q.get("ts"):
            age_min = (now - datetime.fromisoformat(q["ts"])).total_seconds() / 60
            if age_min > 90:
                return _c("quote_stale", "warn", f"마지막 실시간 시세 {age_min:.0f}분 전")
    return _c("quote_stale", "ok", "정상" + (f" (마지막 일봉 {bars_last.date()})" if bars_last is not None else ""))


def check_position(app, mode: str) -> dict:
    if mode != "live":
        return _c("position", "na", "증권사 잔고 비교 대상 아님 (가상매매)")
    st = ops.get_state(app.engine, "reconcile")
    streak = int(st.get("streak") or 0)
    if streak >= 2:
        return _c("position", "critical", f"장부 불일치 {streak}회 연속 ({st.get('drift_n')}종목) — 증권사 앱 확인 필요")
    if streak == 1:
        return _c("position", "warn", f"직전 동기화에서 {st.get('drift_n')}종목 불일치 → 증권사 기준으로 수정함")
    return _c("position", "ok", "일치" + (f" (확인 {str(st.get('at', ''))[:16]})" if st.get("at") else ""))


def check_duplicates(app, mode: str) -> dict:
    since = datetime.now(UTC) - timedelta(days=3)
    with session_scope(app.engine) as s:
        rows = s.execute(select(OrderRecord.client_order_id, OrderRecord.status, OrderRecord.symbol).where(
            OrderRecord.mode == mode, OrderRecord.created_at >= since, OrderRecord.client_order_id.is_not(None),
            OrderRecord.status.in_(OPEN))).all()
    seen: dict[str, int] = {}
    for coid, _, _ in rows:
        base = coid.rsplit("#", 1)[0]
        seen[base] = seen.get(base, 0) + 1
    dup = [k for k, n in seen.items() if n > 1]
    if dup:
        return _c("duplicate", "critical", f"같은 목표의 미완료 주문 {len(dup)}건: {dup[0]}")
    blocked = int(ops.get_state(app.engine, "health:duplicate_blocked").get("count") or 0)
    return _c("duplicate", "ok", "없음" + (f" (중복 시도 {blocked}건 자동 차단)" if blocked else ""))


def check_daily_loss(app, mode: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    start = datetime.combine(now.date(), datetime.min.time(), UTC)
    with session_scope(app.engine) as s:
        prev = s.scalar(select(PortfolioSnapshot.equity).where(PortfolioSnapshot.mode == mode, PortfolioSnapshot.ts < start)
                        .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.id.desc()))
        last = s.scalar(select(PortfolioSnapshot.equity).where(PortfolioSnapshot.mode == mode)
                        .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.id.desc()))
    if not prev or not last:
        return _c("daily_loss", "ok", "기록 없음")
    pnl = last / prev - 1
    lim = app.settings.risk.max_daily_loss_pct
    from ..budget import KILL_MULT
    if pnl <= -KILL_MULT * lim:
        return _c("daily_loss", "critical", f"당일 {pnl:+.2%} ≤ 한도의 {KILL_MULT}배 (-{KILL_MULT * lim:.1%})", value=round(pnl, 5))
    if pnl <= -lim:
        return _c("daily_loss", "warn", f"당일 {pnl:+.2%} ≤ 한도 -{lim:.1%} → 신규 매수 중단", value=round(pnl, 5))
    return _c("daily_loss", "ok", f"당일 {pnl:+.2%} (한도 -{lim:.1%})", value=round(pnl, 5))


def check_total_loss(app, mode: str) -> dict:
    """사용자가 정한 '최대로 감당할 손실'(내 투자 한도)에 닿으면 전체 정지 — 80% 부터 경고."""
    from ..budget import total_loss
    t = total_loss(app, mode)
    if t is None:
        return _c("total_loss", "ok", "투자 한도 미설정 — '내 투자 한도'에서 원금·최대 손실을 정하세요")
    if t["equity"] is None:
        return _c("total_loss", "ok", f"평가 기록 없음 (한도 {t['limit']:,}원)")
    from ..budget import STOP_POLICIES
    detail = f"손실 {t['loss']:,}원 / 한도 {t['limit']:,}원 ({t['used']:.0%})" + (f" · 입출금 {t['net_flows']:+,}원 반영" if t.get("n_flows") else "")
    if t["used"] >= 1:
        return _c("total_loss", "critical", detail + " → 최대 손실 도달 · 정지 후: " + STOP_POLICIES[t.get("on_stop", "hold")].split(" —")[0],
                  value=t["used"])
    if t["used"] >= 0.8:
        return _c("total_loss", "warn", detail + " → 한도의 80% 이상", value=t["used"])
    return _c("total_loss", "ok", detail, value=t["used"])


def check_volatility(bench, vix=None) -> dict:
    if bench is None or len(bench) < 30:
        return _c("volatility", "na", "지수 데이터 부족")
    r = bench["close"].pct_change().dropna()
    last = float(r.iloc[-1])
    sd = float(r.iloc[-121:-1].std()) if len(r) > 30 else 0.0
    z = last / sd if sd > 0 else 0.0
    v = float(vix.iloc[-1]) if vix is not None and len(vix) else None
    if abs(last) >= 0.05:
        return _c("volatility", "critical", f"KOSPI 일간 {last:+.1%} (서킷브레이커급)", value=round(last, 4))
    if abs(z) >= 4 or abs(last) >= 0.03 or (v is not None and v >= 40):
        return _c("volatility", "warn", f"KOSPI {last:+.1%} ({z:+.1f}σ)" + (f" · VIX {v:.0f}" if v else ""),
                  value=round(last, 4))
    return _c("volatility", "ok", f"KOSPI {last:+.1%} ({z:+.1f}σ)" + (f" · VIX {v:.0f}" if v else ""), value=round(last, 4))


def check_data_conflict(app, mode: str) -> dict:
    if not (mode == "live" and app.settings.broker == "kis"):
        return _c("data_conflict", "na", "비교할 실시간 시세 없음 (가상매매)")
    conf = ops.get_state(app.engine, "live_quotes").get("conflicts") or []
    if conf:
        return _c("data_conflict", "critical",
                  f"증권사 시세와 DB 종가가 ±30% 넘게 다름: {', '.join(conf[:5])} (액면분할·데이터 오류 확인)")
    return _c("data_conflict", "ok", "증권사 시세 ↔ DB 종가 일치")


def check_model(app) -> dict:
    try:
        res = app.check_champion(rollback=False)
    except Exception as e:  # noqa: BLE001
        return _c("model", "warn", f"모델 점검 실패: {e}"[:200])
    if res.get("status") == "none":
        return _c("model", "ok", "champion 없음 (코어 팩터는 모델과 무관)")
    if not res.get("passed", True):
        return _c("model", "critical", "; ".join(res.get("failures", [])), version=res.get("version"))
    return _c("model", "ok", f"champion {res.get('version')} 전진 {res.get('n', 0)}건"
              + (f" 적중 {res['accuracy']:.0%}" if res.get("accuracy") is not None else ""))


def check_risk_engine(app) -> dict:
    status, detail = _health(app, "risk_engine", crit_fails=2)
    return _c("risk_engine", status, detail)


def evaluate(app, mode: str | None = None, act: bool = True, now: datetime | None = None,
             market_open: bool = False) -> dict:
    """11개 조건 점검 → 하나라도 critical 이면 HALTED (+ 모델 이상이면 자동 롤백)."""
    mode = mode or (app.settings.mode.value if app.settings.mode.value in ("paper", "shadow", "live") else "paper")
    now = now or datetime.now(UTC)
    db = check_database(app)
    if db["status"] == "critical":
        conds = [db] + [_c(k, "unknown", "DB 가 없어 확인 불가") for k, *_ in CONDITIONS if k != "database"]
        return {"state": "HALTED", "mode": mode, "conditions": conds, "checked_at": now.isoformat(), "acted": False}
    bench, vix, bars_last = None, None, None
    try:
        bars, bench, _ = app.market_data()
        bars_last = max(b.index.max() for b in bars.values()) if bars else None
        from ..engines.market_intel import load_macro
        with session_scope(app.engine) as s:
            vix = load_macro(s, ["VIXCLS"]).get("VIXCLS")
    except Exception as e:  # noqa: BLE001
        log.warning("guardian: 시장 데이터 읽기 실패: %s", e)
    conds = [check_broker(app, mode), check_quotes(app, mode, bars_last, now, market_open), check_position(app, mode),
             check_duplicates(app, mode), check_daily_loss(app, mode, now), check_volatility(bench, vix),
             check_data_conflict(app, mode), check_model(app), check_risk_engine(app), db, check_total_loss(app, mode)]
    critical = [c for c in conds if c["status"] == "critical"]
    ks = ops.get_state(app.engine, "kill_switch")
    state = "HALTED" if critical or ks.get("halt") else "DEGRADED" if any(c["status"] == "warn" for c in conds) \
        else "BUY_BLOCKED" if ks.get("on") else "TRADING"
    acted = False
    if act and critical:
        for c in [c for c in critical if c["key"] == "model"]:
            # champion 이상 → 자동 롤백. 코어 매매는 모델과 무관하므로 모델 이상만으로는 멈추지 않는다
            rb = app.check_champion(rollback=True).get("rollback") or {}
            c["status"] = "warn"
            c["detail"] += (f" → 자동 롤백 ({rb.get('rolled_back')} → {rb.get('restored') or 'champion 없음'})"
                            if rb.get("rolled_back") else "")
        critical = [c for c in critical if c["status"] == "critical"]
        state = "HALTED" if critical or ks.get("halt") else "DEGRADED"
        if critical and not ks.get("halt"):
            reason = " · ".join(f"{c['name']}: {c['detail']}" for c in critical)[:500]
            ops.set_kill_switch(app.engine, True, reason, by="guardian", halt=True,
                                conditions=[c["key"] for c in critical])
            app.notifier.send(f"[{mode}] ⛔ 자동 매매 정지 (HALTED)\n{reason}\n원인 확인 후 대시보드에서 해제하세요.",
                              "critical")
            acted = True
            if any(c["key"] == "total_loss" for c in critical):  # 미리 정한 '정지 후 처리' — 매도 주문표만 만들고 자동으로 팔지 않는다
                from ..budget import stop_sheet
                sh = stop_sheet(app, mode)
                if sh and sh["rows"]:
                    app.notifier.send(f"[{mode}] 최대 손실 정지 후 처리: {sh['text']}\n매도 주문표 {len(sh['rows'])}종목 — '내 투자 한도' 화면에서 확인",
                                      "critical")
    out = {"state": state, "mode": mode, "conditions": conds, "checked_at": now.isoformat(), "acted": acted,
           "kill_switch": ops.get_state(app.engine, "kill_switch")}
    ops.set_state(app.engine, "guardian", {k: v for k, v in out.items() if k != "kill_switch"})
    return out


__all__ = ["CONDITIONS", "evaluate"]

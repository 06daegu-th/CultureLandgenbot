"""v29 증명 프로젝트 — 100만원 실계좌로 "AI 가 비용·세금 뒤에도 지수를 이기는가"를 고칠 수 없는 기록으로 증명한다.

시작할 때 (사전 등록 · 봉인)
  - 규칙: 원금 · 최대 손실(넘으면 전체 정지) · 종목당 상한 · 보유 종목 수 · 하루 손실 한도(넘으면 그날 신규 매수 중단, 1.5배면 자동 정지)
          · 1주 가격이 종목당 상한보다 비싼 종목은 살 수 없음(100만원의 현실) · 레버리지·신용·공매도 없음
  - 성공 기준: 실주문 수 · 코스피 대비 연 초과수익 · 최대 낙폭 · 예측력 검정 — 시작 후에는 바꿀 수 없다 (바꾸려면 종료 후 새 프로젝트)
  - 규칙 · 기준 · 시작일을 해시로 봉인
매일 (장 마감 뒤 한 번)
  - 평가금액 · 현금 · 보유 종목 수 · 그날 체결 수 · 입출금 · 코스피 종가 → 하루 수익률(입출금 제외) · 누적 · 코스피 대비 · 최고점 대비 낙폭
  - 앞 기록의 해시를 이어 붙인 체인 — 지난 기록을 한 글자만 고쳐도 검증에서 드러난다
공개 페이지 (/proof · QUANT_PROOF_PUBLIC=true 또는 화면에서 '공개' 를 켤 때만, 로그인 없이)
  - 수익률 곡선(시작 = 100) vs 코스피 · 규칙 · 성공 기준 진행 · 체인 검증 결과 · 원본 JSON(/proof.json)
  - 계좌번호 · 키는 절대 넣지 않는다 · 금액은 '금액 공개'를 켤 때만
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta

from . import ops

STATE = "proof_project"
LOG = "proof_log"
PRESET = {"principal": 1_000_000, "max_loss": 150_000, "max_position_weight": 0.20, "max_positions": 5,
          "max_daily_loss_pct": 0.03, "days": 365, "min_turnover": 1e9}
CRITERIA = [
    {"key": "orders", "title": "실주문 300건 이상", "target": 300},
    {"key": "excess", "title": "코스피 대비 연 +5%p 이상 (비용·세금 뒤)", "target": 0.05},
    {"key": "mdd", "title": "최대 낙폭 -15% 이내", "target": -0.15},
    {"key": "sprt", "title": "예측력 검정(순차 검정) 통과", "target": "H1"},
]


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def rules(p: dict) -> list[dict]:
    w, pr = p["max_position_weight"], p["principal"]
    kill = p["max_daily_loss_pct"] * 1.5
    return [
        {"key": "principal", "title": "원금", "value": f"{pr:,.0f}원", "why": "이 돈 안에서만 운용 — 추가 입금은 기록하고 수익률에서 뺀다"},
        {"key": "max_loss", "title": "전체 정지 손실", "value": f"-{p['max_loss']:,.0f}원 ({p['max_loss'] / pr:.0%})",
         "why": "누적 손실이 여기에 닿으면 자동 매매 전체 정지 → 전량 정리 주문표(사람이 확인 후 실행)"},
        {"key": "position", "title": "종목당 상한", "value": f"{w:.0%} ({pr * w:,.0f}원)", "why": f"한 종목이 반토막 나도 손실 {w * 0.5:.0%}"},
        {"key": "positions", "title": "보유 종목 수", "value": f"최대 {p['max_positions']}종목", "why": "100만원을 너무 잘게 나누면 수수료·1주 단위 때문에 계획대로 못 산다"},
        {"key": "share_price", "title": "살 수 있는 종목", "value": f"1주 {pr * w:,.0f}원 이하", "why": "1주 가격이 종목당 상한보다 비싸면 살 수 없음 (예: 1주 28만원 종목은 제외)"},
        {"key": "daily", "title": "하루 손실 한도", "value": f"-{p['max_daily_loss_pct']:.0%} → 그날 신규 매수 중단 · -{kill:.1%} → 자동 정지",
         "why": "나쁜 날 연속 매수로 손실을 키우지 않게"},
        {"key": "liquidity", "title": "거래대금", "value": f"20일 평균 {p['min_turnover'] / 1e8:,.0f}억원 이상", "why": "팔고 싶을 때 팔 수 있는 종목만"},
        {"key": "no_leverage", "title": "금지", "value": "신용 · 미수 · 공매도 · 레버리지/인버스 ETF", "why": "원금 이상 잃을 수 있는 방법은 쓰지 않음"},
    ]


def get(app) -> dict:
    return ops.get_state(app.engine, STATE)


def active(app) -> dict | None:
    return get(app).get("active")


def start(app, mode: str = "live", principal: float | None = None, max_loss: float | None = None, public: bool = False,
          show_amounts: bool = False, now: datetime | None = None) -> dict:
    """규칙 · 성공 기준 · 시작일을 봉인하고 투자 한도에 반영한다. 진행 중이면 거절."""
    from . import budget
    now = now or datetime.now(UTC)
    st = get(app)
    if st.get("active"):
        raise ValueError("이미 진행 중인 증명 프로젝트가 있어요 — 끝낸 뒤 새로 시작하세요 (규칙은 시작 후 바꿀 수 없음)")
    if mode not in ("live", "paper", "shadow"):
        raise ValueError("mode 는 live / paper / shadow")
    p = dict(PRESET)
    if principal:
        p["principal"] = float(principal)
    p["max_loss"] = float(max_loss) if max_loss else round(p["principal"] * 0.15)
    if not 0 < p["max_loss"] < p["principal"]:
        raise ValueError("최대 손실은 0 보다 크고 원금보다 작아야 해요")
    # 투자 한도에 반영 (원금 · 최대 손실 · 정지 후 전량 정리) → 증명 프로젝트용 값으로 덮기
    b = budget.save(app, {"principal": p["principal"], "max_loss": p["max_loss"], "first_stage": 0.5, "on_stop": "liquidate"})
    lim = dict(b["limits"])
    lim.update({"max_position_weight": p["max_position_weight"], "max_order_value": round(p["principal"] * p["max_position_weight"]),
                "max_daily_loss_pct": p["max_daily_loss_pct"], "live_small_capital": round(p["principal"]), "live_max_capital": round(p["principal"])})
    ops.set_state(app.engine, budget.KEY, {**b, "limits": lim, "proof": True})
    budget.apply(app)
    day = _kst(now).date()
    start_eq = budget._last_equity(app, mode)  # 수익률 기준 = 시작 시점 장부 평가금액 (없으면 원금)
    rec = {"id": f"proof-{day.isoformat()}", "mode": mode, "start_equity": round(start_eq if start_eq else p["principal"], 2), "start": day.isoformat(), "end": (day + timedelta(days=p["days"])).isoformat(),
           "params": p, "rules": rules(p), "criteria": CRITERIA, "at": now.isoformat(), "public": bool(public), "show_amounts": bool(show_amounts)}
    rec["hash"] = _h({k: v for k, v in rec.items() if k not in ("public", "show_amounts")})
    ops.set_state(app.engine, STATE, {"active": rec, "history": st.get("history") or []})
    ops.set_state(app.engine, LOG, {"project": rec["id"], "days": []})
    return rec


def end(app, reason: str = "", now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    st = get(app)
    a = st.get("active")
    if not a:
        raise ValueError("진행 중인 증명 프로젝트가 없어요")
    s = status(app, now=now)
    done = {**a, "ended_at": now.isoformat(), "end_reason": reason[:200], "final": {k: s.get(k) for k in ("cum", "bench_cum", "excess_ann", "mdd", "orders", "verdict")},
            "log_head": s["chain"].get("head"), "log": ops.get_state(app.engine, LOG).get("days") or []}
    ops.set_state(app.engine, STATE, {"active": None, "history": [*(st.get("history") or []), done][-10:]})
    return done


def set_public(app, public: bool, show_amounts: bool | None = None) -> dict:
    st = get(app)
    a = st.get("active")
    if not a:
        raise ValueError("진행 중인 증명 프로젝트가 없어요")
    a = {**a, "public": bool(public)}
    if show_amounts is not None:
        a["show_amounts"] = bool(show_amounts)
    ops.set_state(app.engine, STATE, {**st, "active": a})
    return a


def is_public(app) -> bool:
    if os.environ.get("QUANT_PROOF_PUBLIC", "").lower() in ("1", "true", "yes", "on"):
        return True
    a = active(app)
    return bool(a and a.get("public"))


def apply_risk(app, book: str, risk) -> None:
    """매매 사이클의 리스크 엔진에 증명 프로젝트 규칙(보유 종목 수)을 건다 — 그 장부일 때만."""
    a = active(app)
    if a and a.get("mode") == book:
        risk.max_positions = int(a["params"]["max_positions"])


# ------------------------------------------------------------------ 매일 기록 (해시 체인)
def _kst(t: datetime) -> datetime:
    from .asof import KST
    return t.astimezone(KST)


def _bench_close(app, day: date) -> tuple[float | None, str]:
    """코스피 종가: 진짜 지수(받아 둔 것) → 없으면 대용 지수(일봉). (값, 출처)"""
    from .data.collectors.indices import load_indices
    it = load_indices(app.engine).get("KOSPI") or {}
    ser = [r for r in it.get("series") or [] if str(r[0]) <= day.isoformat()]
    if ser:
        return float(ser[-1][1]), f"코스피 ({it.get('source') or '지수'})"
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import PriceBar
    with session_scope(app.engine) as s:
        row = s.execute(select(PriceBar.close).where(PriceBar.symbol == "KOSPI", PriceBar.ts <= datetime.combine(day, datetime.max.time(), UTC))
                        .order_by(PriceBar.ts.desc()).limit(1)).first()
    return (float(row[0]), "코스피 대용(시총가중)") if row else (None, "없음")


def _day_numbers(app, mode: str, day: date, since: date | None = None) -> dict:
    from sqlalchemy import func, select

    from .data.db import session_scope
    from .data.models import OrderRecord, PortfolioSnapshot
    end_ = datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC) - timedelta(hours=9)  # KST 자정
    start_ = end_ - timedelta(days=1)
    with session_scope(app.engine) as s:
        snap = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode, PortfolioSnapshot.ts < end_)
                         .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.id.desc()).limit(1)).first()
        n_day = s.scalar(select(func.count()).select_from(OrderRecord).where(
            OrderRecord.mode == mode, OrderRecord.status.in_(("filled", "partial")), OrderRecord.created_at >= start_, OrderRecord.created_at < end_)) or 0
        since_ = datetime.combine(since, datetime.min.time(), UTC) - timedelta(hours=9) if since else start_  # 프로젝트 시작(KST) 이후만
        n_all = s.scalar(select(func.count()).select_from(OrderRecord).where(
            OrderRecord.mode == mode, OrderRecord.status.in_(("filled", "partial")), OrderRecord.created_at >= since_,
            OrderRecord.created_at < end_)) or 0
        out = {"equity": float(snap.equity) if snap else None, "cash": float(snap.cash) if snap else None,
               "n_positions": len(snap.positions or {}) if snap else 0, "snap_at": snap.ts.isoformat() if snap else None,
               "orders_day": int(n_day), "orders_total": int(n_all)}
    flows = [f for f in (app.cashflows(mode) if hasattr(app, "cashflows") else []) if str(f.get("date")) == day.isoformat()]
    out["flow"] = round(sum(float(f["amount"]) for f in flows), 2)
    return out


def record_day(app, now: datetime | None = None, force: bool = False) -> dict | None:
    """장 마감 뒤 하루 한 번 봉인 기록. 이미 그날 기록이 있으면 None (먼저 남긴 것을 바꾸지 않는다)."""
    from .clock import KRX
    now = now or datetime.now(UTC)
    a = active(app)
    if not a:
        return None
    k = _kst(now)
    day = k.date()
    if not KRX.is_trading_day(day):
        return None
    if not force and (k.hour, k.minute) < (15, 40):
        return None
    if day.isoformat() < a["start"]:
        return None
    lg = ops.get_state(app.engine, LOG)
    days = list(lg.get("days") or []) if lg.get("project") == a["id"] else []
    if any(d["date"] == day.isoformat() for d in days):
        return None
    n = _day_numbers(app, a["mode"], day, date.fromisoformat(a["start"]))
    bench, bsrc = _bench_close(app, day)
    prev = days[-1] if days else None
    start_eq = float(a.get("start_equity") or a["params"]["principal"])
    base_eq = prev["equity"] if prev and prev.get("equity") else start_eq
    eq = n["equity"] if n["equity"] is not None else (prev["equity"] if prev else start_eq)
    r = (eq - n["flow"]) / base_eq - 1 if base_eq else 0.0  # 입출금은 수익이 아니다 (시간가중)
    cum = (1 + (prev["cum"] if prev else 0.0)) * (1 + r) - 1
    b0 = days[0]["bench"] if days and days[0].get("bench") else bench
    bcum = (bench / b0 - 1) if bench and b0 else None
    peak = max(prev["peak"] if prev else 0.0, cum)
    dd = (1 + cum) / (1 + peak) - 1
    rec = {"date": day.isoformat(), "equity": round(eq, 2), "cash": n["cash"], "flow": n["flow"], "n_positions": n["n_positions"],
           "orders_day": n["orders_day"], "orders_total": n["orders_total"], "ret": round(r, 6), "cum": round(cum, 6),
           "bench": bench, "bench_src": bsrc, "bench_cum": None if bcum is None else round(bcum, 6),
           "excess": None if bcum is None else round(cum - bcum, 6), "peak": round(peak, 6), "dd": round(dd, 6),
           "no_snapshot": n["equity"] is None, "at": now.isoformat(), "prev": prev["hash"] if prev else a["hash"]}
    rec["hash"] = _h(rec)
    days.append(rec)
    ops.set_state(app.engine, LOG, {"project": a["id"], "days": days})
    return rec


def verify(days: list[dict], genesis: str) -> dict:
    prev = genesis
    for i, d in enumerate(days):
        body = {k: v for k, v in d.items() if k != "hash"}
        if d.get("prev") != prev or _h(body) != d.get("hash"):
            return {"ok": False, "n": len(days), "broken_at": d.get("date"), "index": i,
                    "text": f"{d.get('date')} 기록이 봉인 뒤에 바뀌었어요 (체인 끊김)"}
        prev = d["hash"]
    return {"ok": True, "n": len(days), "head": prev, "text": f"{len(days)}일 기록 모두 봉인 그대로" if days else "아직 기록 없음"}


def status(app, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    a = active(app)
    st = get(app)
    if not a:
        return {"active": None, "preset": PRESET, "rules": rules({**PRESET, "max_loss": PRESET["max_loss"]}), "criteria": CRITERIA,
                "history": [{k: v for k, v in h.items() if k != "log"} for h in st.get("history") or []]}
    lg = ops.get_state(app.engine, LOG)
    days = list(lg.get("days") or []) if lg.get("project") == a["id"] else []
    chain = verify(days, a["hash"])
    last = days[-1] if days else {}
    n_days = len(days)
    elapsed = (_kst(now).date() - date.fromisoformat(a["start"])).days
    years = max(elapsed, 1) / 365
    cum, bcum = last.get("cum"), last.get("bench_cum")
    excess_ann = ((1 + cum) ** (1 / years) - (1 + bcum) ** (1 / years)) if cum is not None and bcum is not None and elapsed >= 20 else None
    mdd = min((d["dd"] for d in days), default=None)
    sprt = (ops.get_state(app.engine, "prediction_power").get("forward") or {}).get("decision")
    orders = last.get("orders_total") or 0
    prog = []
    for c in CRITERIA:
        if c["key"] == "orders":
            v, ok, frac = orders, orders >= c["target"], min(1.0, orders / c["target"])
        elif c["key"] == "excess":
            v, ok, frac = excess_ann, excess_ann is not None and excess_ann >= c["target"], None
        elif c["key"] == "mdd":
            v, ok, frac = mdd, mdd is not None and mdd >= c["target"], None
        else:
            v, ok, frac = sprt, sprt == "H1", None
        prog.append({**c, "value": v, "ok": bool(ok), "frac": frac})
    finished = _kst(now).date().isoformat() >= a["end"]
    if not days:
        verdict = "기록 시작 전 — 장 마감 뒤 첫 기록이 남아요"
    elif not finished:
        verdict = f"진행 중 · {n_days}거래일 기록 · 종료일 {a['end']} · 지금 판정하지 않음 (기간을 다 채워야 판정)"
    else:
        verdict = "성공 — 기준 모두 충족" if all(p["ok"] for p in prog) else "실패 — " + ", ".join(p["title"] for p in prog if not p["ok"])
    try:
        halted = bool(app.kill_switch_on())
    except Exception:  # noqa: BLE001
        halted = False
    warn = []
    se, pr = float(a.get("start_equity") or 0), float(a["params"]["principal"])
    if se and abs(se / pr - 1) > 0.1:
        warn.append(f"시작할 때 장부 평가금액 {se:,.0f}원이 원금 {pr:,.0f}원과 달라요 — 수익률은 장부 전체 기준이라, 증명용 계좌에는 원금만 넣어 두세요")
    buyable = None
    try:  # 100만원의 현실: 1주 가격이 종목당 상한 이하인 종목만 살 수 있다
        bars, _, _ = app.market_data()
        cap = pr * float(a["params"]["max_position_weight"])
        kr = {s: float(b["close"].iloc[-1]) for s, b in bars.items() if s[:1].isdigit() and b is not None and len(b)}
        buyable = {"n": sum(1 for v in kr.values() if v <= cap), "total": len(kr), "cap": cap}
        if kr and buyable["n"] < len(kr) * 0.5:
            warn.append(f"지금 대상 {len(kr)}종목 중 1주를 살 수 있는 종목은 {buyable['n']}개뿐이에요 (1주 {cap:,.0f}원 이하) — 원금이 작으면 고를 수 있는 종목이 줄어요")
    except Exception:  # noqa: BLE001
        buyable = None
    if days and days[-1].get("no_snapshot"):
        warn.append("마지막 기록일에 장부 평가 기록이 없어 전날 값으로 남겼어요 — 서버(24시간 운영)가 켜져 있는지 확인하세요")
    return {"active": a, "rules": a["rules"], "criteria": prog, "warnings": warn, "buyable": buyable, "days": days[-400:], "n_days": n_days, "elapsed_days": elapsed,
            "cum": cum, "bench_cum": bcum, "excess": last.get("excess"), "excess_ann": excess_ann, "mdd": mdd, "orders": orders,
            "equity": last.get("equity"), "chain": chain, "verdict": verdict, "finished": finished, "halted": halted,
            "public": is_public(app), "history": [{k: v for k, v in h.items() if k != "log"} for h in st.get("history") or []]}


def public_view(app, now: datetime | None = None) -> dict:
    """공개용: 계좌번호 · 키 없음 · 금액은 '금액 공개'일 때만."""
    s = status(app, now)
    a = s.get("active")
    if not a:
        return {"active": False, "text": "진행 중인 증명 프로젝트가 없어요"}
    amounts = bool(a.get("show_amounts"))
    keep = ("date", "ret", "cum", "bench_cum", "excess", "dd", "n_positions", "orders_day", "orders_total", "bench_src", "prev", "hash", "at", "no_snapshot")
    days = [{k: d.get(k) for k in keep} | ({"equity": d.get("equity"), "flow": d.get("flow"), "cash": d.get("cash"), "bench": d.get("bench"), "peak": d.get("peak")} if amounts else {})
            for d in s["days"]]
    return {"active": True, "id": a["id"], "start": a["start"], "end": a["end"], "mode": {"live": "실제 계좌", "paper": "가상 장부", "shadow": "그림자 장부"}.get(a["mode"], a["mode"]),
            "registered_hash": a["hash"], "rules": a["rules"] if amounts else _rules_pct(a),
            "criteria": s["criteria"], "verdict": s["verdict"], "chain": s["chain"], "cum": s["cum"], "bench_cum": s["bench_cum"],
            "excess_ann": s["excess_ann"], "mdd": s["mdd"], "orders": s["orders"], "n_days": s["n_days"], "amounts": amounts,
            "days": days, "note": "매일 장 마감 뒤 한 번 기록하고, 각 기록은 앞 기록의 해시를 이어 봉인합니다 — 원본 JSON 으로 누구나 다시 계산해 확인할 수 있어요. "
                                  "과거 성과가 미래 수익을 보장하지 않으며, 이 페이지는 투자 권유가 아닙니다."}


def public_html(view: dict) -> str:
    from html import escape as E
    if not view.get("active"):
        return "<!doctype html><meta charset='utf-8'><title>증명 프로젝트</title><p>진행 중인 증명 프로젝트가 없어요.</p>"
    days = view["days"]
    pts = [(d["date"], 100 * (1 + (d.get("cum") or 0)), None if d.get("bench_cum") is None else 100 * (1 + d["bench_cum"])) for d in days]
    W, H, P = 720, 260, 34

    allv = [v for p in pts for v in (p[1], p[2]) if v is not None]
    lo, hi = (min(allv), max(allv)) if allv else (99.0, 101.0)
    hi = hi if hi > lo else lo + 1

    def path(i):
        if len(pts) < 2 or not any(p[i] is not None for p in pts):
            return ""
        xy = [(P + k * (W - 2 * P) / (len(pts) - 1), H - P - (p[i] - lo) / (hi - lo) * (H - 2 * P)) for k, p in enumerate(pts) if p[i] is not None]
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy)

    pct = lambda v: "-" if v is None else f"{(0.0 if abs(v) < 5e-5 else v) * 100:+.2f}%"  # noqa: E731 (-0.00% 대신 +0.00%)
    crit = "".join(f"<li class='{'ok' if c['ok'] else ''}'><b>{E(c['title'])}</b><span>{E(_crit_value(c))}</span></li>" for c in view["criteria"])
    rule = "".join(f"<li><b>{E(r['title'])}</b><span>{E(r['value'])}</span></li>" for r in view["rules"])
    rows = "".join(f"<tr><td>{E(d['date'])}</td><td>{pct(d.get('ret'))}</td><td>{pct(d.get('cum'))}</td><td>{pct(d.get('bench_cum'))}</td>"
                   f"<td>{pct(d.get('dd'))}</td><td>{d.get('orders_day') or 0}</td><td class='h'>{E(str(d.get('hash', ''))[:12])}</td></tr>" for d in reversed(days[-60:]))
    ch = view["chain"]
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>증명 프로젝트 기록</title><style>
:root{{--bg:#fff;--fg:#191f28;--sub:#6b7684;--line:#e5e8eb;--up:#f04452;--down:#3182f6;--card:#f9fafb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#101215;--fg:#e8eaed;--sub:#9aa0a6;--line:#2a2e33;--card:#17191c}}}}
html,body{{overflow-x:hidden}} body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}}
main{{max-width:760px;margin:0 auto;padding:24px 16px 48px}} h1{{font-size:24px;margin:0 0 4px}} .sub{{color:var(--sub);font-size:13px}}
.kv{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}} .kv div{{background:var(--card);border-radius:14px;padding:12px 14px}}
.kv b{{display:block;font-size:22px;font-variant-numeric:tabular-nums}} .up{{color:var(--up)}} .down{{color:var(--down)}}
svg{{width:100%;height:auto;background:var(--card);border-radius:14px}} ul{{list-style:none;padding:0;margin:0}}
li{{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}} li b{{flex:0 0 auto;max-width:45%}}
li span{{color:var(--sub);text-align:right;min-width:0;overflow-wrap:anywhere}} main{{box-sizing:border-box;width:100%}}
li.ok b::before{{content:"✓ ";color:#12b886}} h2{{font-size:17px;margin:28px 0 8px}}
table{{width:100%;min-width:520px;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}} td{{white-space:nowrap}} td,th{{padding:6px 4px;border-bottom:1px solid var(--line);text-align:right}}
td:first-child,th:first-child{{text-align:left}} .h{{font-family:ui-monospace,monospace;color:var(--sub)}} .wrap{{overflow-x:auto}}
.badge{{display:inline-block;padding:2px 10px;border-radius:99px;background:var(--card);font-size:13px}}
</style></head><body><main>
<h1>증명 프로젝트</h1>
<div class="sub">{E(view['mode'])} · {E(view['start'])} ~ {E(view['end'])} · 사전 등록 해시 <span class="h">{E(view['registered_hash'][:16])}…</span></div>
<p><span class="badge">{E(view['verdict'])}</span></p>
<div class="kv"><div>누적 수익률<b class="{'up' if (view['cum'] or 0) > 0 else 'down'}">{pct(view['cum'])}</b></div>
<div>코스피<b>{pct(view['bench_cum'])}</b></div><div>최대 낙폭<b class="down">{pct(view['mdd'])}</b></div>
<div>체결 주문<b>{view['orders']}건</b></div></div>
<svg viewBox="0 0 {W} {H}" role="img" aria-label="수익률 곡선 (시작=100) vs 코스피"><path d="{path(2)}" fill="none" stroke="#9aa0a6" stroke-width="2" stroke-dasharray="5 4"/>
<path d="{path(1)}" fill="none" stroke="#3182f6" stroke-width="2.5"/><text x="{P}" y="20" font-size="12" fill="#3182f6">이 계좌 (시작=100)</text>
<text x="{P + 130}" y="20" font-size="12" fill="#9aa0a6">코스피</text></svg>
<h2>성공 기준 (시작 때 봉인 · 바꿀 수 없음)</h2><ul>{crit}</ul>
<h2>규칙</h2><ul>{rule}</ul>
<h2>매일 기록 · 봉인 {'정상' if ch['ok'] else '끊김'}</h2><div class="sub">{E(ch['text'])} · <a href="/proof.json">원본 JSON</a></div>
<div class="wrap"><table><thead><tr><th>날짜</th><th>그날</th><th>누적</th><th>코스피</th><th>낙폭</th><th>체결</th><th>해시</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="sub">{E(view['note'])}</p></main></body></html>"""


def _rules_pct(a: dict) -> list[dict]:
    """금액 비공개일 때: 원금·손실·상한을 비율로만."""
    p = a["params"]
    hide = {"principal": "비공개", "max_loss": f"원금의 -{p['max_loss'] / p['principal']:.0%}",
            "position": f"원금의 {p['max_position_weight']:.0%}", "share_price": "1주 가격 ≤ 종목당 상한"}
    return [{**r, "value": hide.get(r["key"], r["value"])} for r in a["rules"]]


def _crit_value(c: dict) -> str:
    v = c.get("value")
    if c["key"] == "orders":
        return f"{v or 0} / {c['target']}건"
    if c["key"] in ("excess", "mdd"):
        return "-" if v is None else f"{v * 100:+.1f}%" + (" (연환산)" if c["key"] == "excess" else "")
    return {"H1": "통과", "H0": "실패"}.get(v or "", "진행 중")


__all__ = ["start", "end", "status", "record_day", "verify", "public_view", "public_html", "apply_risk", "is_public", "set_public", "PRESET", "CRITERIA"]

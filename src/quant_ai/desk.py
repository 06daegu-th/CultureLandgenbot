"""v13 운영 데스크 — 새 엔진들을 앱(QuantAI)에 연결하는 얇은 층.

스케줄러 · 대시보드 API · 행동(actions) 이 여기의 함수를 부른다. 결과는 ops 상태에 저장되어 화면이 바로 읽는다.
  event_calendar · event_impact · readiness · kis_validate · slippage_calibrate · model_decay · prediction_power
  wics · rotation · alt_collect · extract_events · batch_ab · fx · notarize · stock_desk · my_journal_*
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import select

from . import ops
from .asof import label
from .data.db import session_scope
from .data.models import Disclosure, Instrument, SystemState

log = logging.getLogger(__name__)


def _json(obj):
    from .pipeline import _json_ready
    return _json_ready(obj)


def _names(app) -> dict[str, str]:
    with session_scope(app.engine) as s:
        return {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument))}


def _profiles(app) -> dict[str, dict]:
    with session_scope(app.engine) as s:
        rows = s.scalars(select(SystemState).where(SystemState.key.like("profile:%"))).all()
        return {r.key.split(":", 1)[1]: (r.value or {}).get("data") or {} for r in rows}


def _focus(app) -> list[str]:
    try:
        from .alerts import focus_symbols
        return list(focus_symbols(app))
    except Exception:  # noqa: BLE001
        return []


# ------------------------------------------------------------------ 이벤트 캘린더 2.0
def fred_release_dates(app, start, end, fetch=None) -> list[dict]:
    key = app.settings.fred_api_key
    if not key:
        return []
    st = ops.get_state(app.engine, "fred_releases")
    if st.get("at") and datetime.now(UTC) - datetime.fromisoformat(st["at"]) < timedelta(hours=24):
        return st.get("dates") or []
    from .engines.events import fetch_fred_release_dates
    dates = fetch_fred_release_dates(key, start, end, fetch)
    if dates:
        ops.set_state(app.engine, "fred_releases", {"at": datetime.now(UTC).isoformat(), "dates": dates})
    return dates


def event_calendar(app, days_ahead: int = 60, days_back: int = 14, now: datetime | None = None, store: bool = True) -> dict:
    from .engines import events as E
    now = now or datetime.now(UTC)
    today = E.today_kst(now)
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    names = _names(app)
    from .data.collectors import bok as BOK
    from .data.collectors import vkospi as VK
    bok_sched = BOK.schedule(app.engine)
    with session_scope(app.engine) as s:
        disc = s.scalars(select(Disclosure).where(Disclosure.filed_at >= now - timedelta(days=days_back))).all()
        ev = E.build_calendar(start, end, _profiles(app), names, disc, E.load_custom(app.settings.artifacts_dir),
                              fred_release_dates(app, start, end), bok_sched)
    ev = E.with_dday(ev, today)
    bars, bench, _ = app.market_data()
    vk = VK.get(app.engine, bench)
    risk = []
    focus = _focus(app)
    for sym in focus:
        b = bars.get(sym)
        sig = beta = None
        if b is not None and len(b) > 60:
            r = b["close"].pct_change().dropna()
            sig = float(r.iloc[-20:].std())
            if bench is not None and len(bench) > 60:
                m = bench["close"].pct_change().reindex(r.index).dropna()
                rr = r.reindex(m.index)
                if m.var() > 0:
                    beta = float(np.cov(rr.iloc[-120:], m.iloc[-120:])[0, 1] / m.iloc[-120:].var())
        opt = ops.get_state(app.engine, f"options:{sym}")
        implied = opt.get("earnings_implied_move")
        if implied is None and sym[:1].isdigit() and sig:
            # 국내: 옵션이 없어 VKOSPI 로 시장 몫을 재고 고유 변동을 더한 일간 변동 × 2.5 (실적일 경험칙)
            sys_hist = abs(beta or 1.0) * float(bench["close"].pct_change().iloc[-20:].std()) if bench is not None and len(bench) > 21 else 0.0
            idio = math.sqrt(max(sig * sig - sys_hist * sys_hist, 0.0))  # 과거 변동 중 시장 몫을 뺀 고유 변동
            m1 = VK.stock_move(vk, beta, idio, 1)
            implied = None if m1 is None else round(2.5 * m1, 4)
        er = E.event_risk(sym, ev, today, sig, beta, implied)
        er["name"] = names.get(sym, sym)
        risk.append(er)
    risk.sort(key=lambda x: -x["score"])
    out = {"at": now.isoformat(), "as_of": label(now), "today": today.isoformat(), "events": ev, "risk": risk,
           "vkospi": {k: v for k, v in vk.items() if k != "series"} | {"series": vk.get("series")},
           "bok": {"schedule": bok_sched, "rate": BOK.base_rate(app.engine, app.settings.ecos_api_key)},
           "counts": {k: sum(1 for e in ev if e["kind"] == k and e["d_day"] >= 0) for k in {e["kind"] for e in ev}},
           "gate": app.settings.event_gate}
    if store:
        ops.set_state(app.engine, "event_calendar", _json(out))
        archive_events(app, ev, today)
    return out


def archive_events(app, events: list[dict], today) -> int:
    """지나간 일정은 버리지 않고 날짜별로 보관 — '그날 재현' 이 오래된 날짜의 종목 일정(실적·공시)도 보여주게. 2년치."""
    st = ops.get_state(app.engine, "event_archive")
    arch: dict[str, list] = dict(st.get("by_date") or {})
    n = 0
    for e in events:
        d = str(e.get("date"))[:10]
        if not d or d > today.isoformat():
            continue  # 미래 일정은 바뀔 수 있어 확정된(지난) 것만
        keep = {k: e.get(k) for k in ("date", "kind", "title", "symbol", "market", "source", "estimated")}
        lst = arch.setdefault(d, [])
        if not any(x.get("title") == keep["title"] and x.get("symbol") == keep["symbol"] for x in lst):
            lst.append(keep)
            n += 1
    cutoff = (today - timedelta(days=730)).isoformat()
    arch = {k: v for k, v in arch.items() if k >= cutoff}
    ops.set_state(app.engine, "event_archive", {"by_date": arch})
    return n


def event_caps_for(app, symbols) -> dict:
    from .engines.events import event_caps, today_kst
    cal = ops.get_state(app.engine, "event_calendar")
    if not cal.get("events") or datetime.now(UTC) - datetime.fromisoformat(cal["at"]) > timedelta(hours=26):
        return {}
    return event_caps(symbols, cal["events"], today_kst(), app.settings.event_gate)


def event_impact(app, store: bool = True) -> dict:
    from datetime import date as _date

    from .engines.event_extract import extract
    from .review.event_impact import market_impact, past_market_events, prediction_attribution, stock_impact
    bars, benches = app._all_bars()
    today = datetime.now(UTC).date()
    pm = past_market_events(today - timedelta(days=900), today)
    mk = market_impact(benches.get("KR"), pm, "KR")
    mk_us = market_impact(benches.get("US"), pm, "US") if benches.get("US") is not None else []
    names = _names(app)
    stock_ev = []
    with session_scope(app.engine) as s:
        for d in s.scalars(select(Disclosure).where(Disclosure.filed_at >= datetime.now(UTC) - timedelta(days=720))):
            for e in extract(d.title, names, d.symbol):
                stock_ev.append({"symbol": d.symbol, "date": d.filed_at.date() if hasattr(d.filed_at, "date") else d.filed_at,
                                 "kind": e["label"], "title": d.title})
    for sym, p in _profiles(app).items():
        for e in p.get("events") or []:
            if e.get("kind") == "earnings" and e.get("date"):
                try:
                    dd = _date.fromisoformat(str(e["date"])[:10])
                except ValueError:
                    continue
                if dd < today:
                    stock_ev.append({"symbol": sym, "date": dd, "kind": "실적 발표", "title": e.get("label")})
    rate = ops.get_state(app.engine, "bok_rate")
    bok_days = [{"date": _date.fromisoformat(c["date"]), "kind": "bok_change", "market": "KR"} for c in rate.get("changes") or []]
    if bok_days:
        mk = sorted(mk + market_impact(benches.get("KR"), bok_days, "KR"), key=lambda x: -(x["vs_normal"] or 0))
    si = stock_impact(bars, benches.get("KR"), stock_ev)
    # 예측 오차 귀속
    from .data.models import ConsensusRecord
    with session_scope(app.engine) as s:
        rows = s.execute(select(ConsensusRecord.symbol, ConsensusRecord.as_of, ConsensusRecord.prob_up, ConsensusRecord.realized_return)
                         .where(ConsensusRecord.realized_return.is_not(None)).order_by(ConsensusRecord.id.desc()).limit(1500)).all()
    items = [{"symbol": sy, "as_of": a.date(), "hit": (p >= 0.5) == (r > 0)} for sy, a, p, r in rows]
    big = [{"date": e["date"], "kind": e["kind"], "market": e["market"]} for e in pm if e["kind"] in ("fomc", "quad_witching", "nfp")]
    big += [{"date": e["date"], "kind": e["kind"], "symbol": e["symbol"], "market": "KR"} for e in stock_ev]
    att = prediction_attribution(items, big, app.horizon)
    out = {"at": datetime.now(UTC).isoformat(), "market_kr": mk, "market_us": mk_us, "stock": si, "prediction": att,
           "n_stock_events": len(stock_ev)}
    if store:
        ops.set_state(app.engine, "event_impact", _json(out))
    return out


# ------------------------------------------------------------------ 매매 준비 · 리스크
def readiness(app, mode: str | None = None, act: bool = True) -> dict:
    from .readiness import evaluate
    cal = ops.get_state(app.engine, "event_calendar")
    if not cal.get("at") or datetime.now(UTC) - datetime.fromisoformat(cal["at"]) > timedelta(hours=6):
        try:
            cal = event_calendar(app)
        except Exception as e:  # noqa: BLE001
            log.warning("캘린더 실패: %s", e)
    return _json(evaluate(app, mode, cal=cal, act=act))


def portfolio_gate(app, bars: dict, limits=None):
    """RiskEngine.portfolio_gate — 업종 · 묶음 · VaR 한도 안에서 넣을 수 있는 최대 수량."""
    from .engines.sector import sector_map
    from .trading.portfolio_risk import PortfolioRiskLimits, pretrade_check
    sectors = sector_map(app.engine)
    lim = limits or PortfolioRiskLimits(max_var95=app.settings.risk.max_var95,
                                        max_sector_weight=app.settings.risk.max_sector_weight)

    def gate(symbol, qty, price, portfolio, prices):
        eq = portfolio.equity(prices)
        if eq <= 0:
            return qty, None
        w = portfolio.weights(prices)
        r = pretrade_check(w, symbol, qty * price / eq, bars, sectors, lim)
        if r["ok"]:
            return qty, None
        return int(r["allowed"] * eq // price), "; ".join(r["reasons"])[:160]
    return gate


def risk_of_ruin(app, mode: str) -> dict:
    """장부의 실제 일간 수익률(60일 이상) → 없으면 현재 비중 × 과거 수익률."""
    import pandas as pd

    from .data.models import PortfolioSnapshot
    from .trading.ruin import simulate, verdict
    with session_scope(app.engine) as s:
        rows = s.execute(select(PortfolioSnapshot.ts, PortfolioSnapshot.equity).where(PortfolioSnapshot.mode == mode)
                         .order_by(PortfolioSnapshot.ts)).all()
    src = "장부 실제 일간 수익률"
    rets = None
    if rows:
        eq = pd.Series([e for _, e in rows], index=pd.DatetimeIndex([t for t, _ in rows]))
        eq = eq.groupby(eq.index.normalize()).last()
        r = eq.pct_change().dropna()
        if len(r) >= 60:
            rets = r
    if rets is None:
        pf = app.load_portfolio(mode)
        bars, _, _ = app.market_data()
        prices = {s: float(b["close"].iloc[-1]) for s, b in bars.items() if len(b)}
        for s, p in pf.positions.items():
            prices.setdefault(s, p.avg_price)
        w = pf.weights(prices)
        closes = pd.DataFrame({s: bars[s]["close"] for s in w if s in bars}).pct_change().iloc[-500:]
        if closes.empty:
            return {"insufficient": True, "message": "보유 종목 또는 장부 기록이 없습니다"}
        rets = closes.fillna(0) @ pd.Series({s: w[s] for s in closes.columns})
        src = "현재 비중 × 과거 2년 수익률 (장부 기록 60일 미만)"
    res = simulate(rets.to_numpy())
    return res | {"source": src, "verdict": verdict(res)}


# ------------------------------------------------------------------ KIS 검증 · 슬리피지
def kis_validate(app, fill: bool = False, client=None, market_open: bool | None = None, e2e: bool = False) -> dict:
    from .clock import MARKETS, Phase
    from .trading.kis import KISClient
    from .trading.kis_check import run
    if client is None:
        try:
            client = KISClient.from_env(Path(app.settings.artifacts_dir))
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "at": datetime.now(UTC).isoformat(), "failed": str(e)[:200], "steps": [],
                   "env": app.settings.kis_env, "message": "KIS 키가 .env 에 없습니다 (KIS_APP_KEY · KIS_APP_SECRET · KIS_ACCOUNT)"}
            ops.set_state(app.engine, "kis_validation", res)
            return res
    if market_open is None:
        market_open = MARKETS["KRX"].phase(datetime.now(UTC)) is Phase.OPEN
    try:
        pos = {s: p.qty for s, p in app.load_portfolio("live").positions.items()}
    except Exception:  # noqa: BLE001
        pos = None
    res = run(client, market_open=market_open, fill_test=fill, db_positions=pos, e2e_engine=app.engine if e2e else None)
    prev = ops.get_state(app.engine, "kis_validation")
    hist = (prev.get("history") or [])[-29:] + [{"at": res["at"], "ok": res["ok"], "env": res["env"],
                                                  "order": res["order_path_verified"], "fill": res["fill_path_verified"],
                                                  "e2e": res.get("e2e_verified")}]
    ops.set_state(app.engine, "kis_validation", res | {"history": hist})
    if res.get("parity"):
        par = ops.get_state(app.engine, "execution_parity")
        rows = (par.get("rows") or [])[-199:] + [res["parity"] | {"at": res["at"]}]
        ops.set_state(app.engine, "execution_parity", {"rows": rows})
    ops.record_health(app.engine, "broker", res["ok"], res.get("failed"))
    return res


def record_parity(app, fills, quotes: dict, book_fn=None) -> int:
    """실제(KIS) 체결마다: 주문 순간의 호가로 체결 시뮬레이터가 예측한 슬리피지 vs 실제 체결가 → parity 기록.

    중간가 기준(불리한 방향 +, bp). 100건이 쌓이면 시뮬레이터 편향이 보정 판단의 근거가 된다."""
    from .trading.exec_sim import simulate
    from .trading.kis_ws import latest_book
    rows = []
    for f in fills or []:
        o = f.order
        q = quotes.get(o.symbol)
        if q is None or not f.price:
            continue
        book = (book_fn or latest_book)(o.symbol)
        sim = simulate(o.side.value, int(f.qty), q.last, q.bid, q.ask, q.bid_qty, q.ask_qty, book=book,
                       adv=q.adv, sigma=q.sigma, impact_coef=app.settings.costs.impact_coef)
        mid = sim.mid or q.last
        sgn = 1 if o.side.value == "buy" else -1
        real = sgn * (f.price - mid) / mid * 1e4
        rows.append({"at": f.ts.isoformat() if hasattr(f.ts, "isoformat") else str(f.ts), "symbol": o.symbol, "side": o.side.value,
                     "qty": int(f.qty), "real_bps": round(real, 2), "sim_bps": None if sim.slippage_bps is None else round(sim.slippage_bps, 2),
                     "method": sim.method, "source": "live"})
    if rows:
        par = ops.get_state(app.engine, "execution_parity")
        ops.set_state(app.engine, "execution_parity", {"rows": ((par.get("rows") or []) + rows)[-500:]})
    return len(rows)


PARITY_MIN = 100


def sim_bias(app) -> float:
    """실제 체결 − 시뮬레이터 예측의 평균 (bp). 100건 미만이면 0 (보정 안 함)."""
    from .trading.slippage import parity
    p = parity(ops.get_state(app.engine, "execution_parity").get("rows") or [])
    return float(p["bias_bps"]) if p.get("n", 0) >= PARITY_MIN else 0.0


def slippage_calibrate(app, store: bool = True) -> dict:
    from .trading.slippage import fit, observations, parity
    bars, _ = app._all_bars()
    c = app.settings.costs
    with session_scope(app.engine) as s:
        live = observations(s, bars, "live")
        shadow = observations(s, bars, "shadow")
    out = {"at": datetime.now(UTC).isoformat(), "live": fit(live, c.slippage_bps, c.impact_coef),
           "shadow_reference": fit(shadow, c.slippage_bps, c.impact_coef) | {"note": "섀도는 호가 기준 가상 체결 — 참고용 (보정에는 실제 체결만)"},
           "parity": parity(ops.get_state(app.engine, "execution_parity").get("rows") or [])
           | {"applied_bias_bps": sim_bias(app), "min_n": PARITY_MIN},
           "autocal": app.settings.slippage_autocal}
    out["model"] = out["live"]
    if store:
        ops.set_state(app.engine, "slippage_model", _json(out))
    return out


def cost_config(app, base):
    """가상 체결·백테스트에 쓰는 비용 설정 — 실측 보정이 적용 조건을 채우면 반영."""
    from .trading.slippage import apply
    m = ops.get_state(app.engine, "slippage_model").get("model")
    return apply(base, m, app.settings.slippage_autocal)


# ------------------------------------------------------------------ 모델 노후 · 예측력
def model_decay(app, store: bool = True) -> dict:
    from .review.decay import report
    rec, _ = app.active_model()
    with session_scope(app.engine) as s:
        r = report(s, rec.created_at if rec else None)
    if store:
        prev = ops.get_state(app.engine, "model_decay")
        ops.set_state(app.engine, "model_decay", _json(r))
        from .alerts import push
        if r["status"] == "decaying" and prev.get("status") != "decaying":
            push(app.engine, "market", "모델 노후 신호", r.get("message") or "", level="warn", link="#aihealth")
        for an in set(r.get("decaying_ais") or []) - set(prev.get("decaying_ais") or []):
            from .explain import LABELS
            push(app.engine, "market", f"AI 성능 저하: {LABELS.get(an, an)}", (r["analysts"][an].get("message") or "")[:200],
                 level="warn", link="#aihealth", dedupe=f"ai-decay:{an}:{r['at'][:10]}")
    return r


def signal_study_path(app) -> Path:
    return Path(app.settings.artifacts_dir) / "signal_study.json"


def prediction_power(app, store: bool = True) -> dict:
    from .review.power import forward_test, preregister, required_n
    reg = preregister(app)
    with session_scope(app.engine) as s:
        fw = forward_test(s, reg)
    p = signal_study_path(app)
    study = None
    if p.exists():
        try:
            study = json.loads(p.read_text(encoding="utf-8"))
            study.pop("ic_series", None) if len(json.dumps(study)) > 400_000 else None
        except (OSError, json.JSONDecodeError):
            study = None
    ev = ops.get_state(app.engine, "evaluation")
    table = [{"edge_pp": e, "n": required_n(reg["p0"], reg["p0"] + e / 100)} for e in (2, 3, 4, 5, 8)]
    out = {"at": datetime.now(UTC).isoformat(), "prereg": reg, "forward": fw, "study": study, "power_table": table,
           "evaluation": {k: ev.get(k) for k in ("status", "verdict", "n", "hit_rate", "p_value", "sealed_share", "best_baseline")},
           "layers": [
               {"key": "A", "title": "사후 검증 (실제 KRX 과거 데이터)", "status": "done" if study and not study.get("insufficient") else "none",
                "detail": (study or {}).get("verdict") or "연구 결과 없음 — quant-ai power-study 로 생성"},
               {"key": "B", "title": "사전 등록 (성공 기준 봉인)", "status": "done",
                "detail": f"{reg['h1']} · α={reg['alpha']} · 등록 {reg['registered_at'][:10]} · 해시 {reg['hash'][:12]}…"},
               {"key": "C", "title": "전진 검증 (등록 이후 봉인된 예측)",
                "status": {"H1": "pass", "H0": "fail"}.get(fw["decision"], "running"), "detail": fw["label"]}],
           "bottom_line": ("실제 시장 예측력: 전진 검증 통과" if fw["decision"] == "H1" else
                           "실제 시장 예측력: 없음으로 판정 — 실제 돈 금지" if fw["decision"] == "H0" else
                           "실제 시장 예측력: 아직 검증 전 — 실제 돈은 소액 한도 안에서만")}
    if store:
        prev = ops.get_state(app.engine, "prediction_power")
        ops.set_state(app.engine, "prediction_power", _json({k: v for k, v in out.items() if k != "study"}))
        power_alerts(app, prev.get("forward") or {}, fw)
    return _json(out)


def power_alerts(app, prev: dict, fw: dict) -> list[str]:
    """전진 검증 판정이 바뀌면(H1/H0) · 채점 100건마다 알림 (텔레그램·푸시로도)."""
    from .alerts import push
    sent = []
    if fw.get("decision") != prev.get("decision") and fw.get("decision") in ("H1", "H0"):
        good = fw["decision"] == "H1"
        t = "실제 시장 예측력: 사전 등록 기준 통과" if good else "실제 시장 예측력: 없음으로 판정"
        body = (f"등록 이후 봉인된 예측 {fw.get('scored')}건 · 적중 {(fw.get('hit_rate') or 0):.1%} · 순차 검정 판정. "
                + ("소액 실전 검토 가능 (다른 관문도 확인)" if good else "실제 돈 사용 금지 — 모델·프롬프트를 바꾸면 새로 등록"))
        push(app.engine, "power", t, body, level="good" if good else "critical", link="#power", dedupe=f"power:{fw['decision']}")
        app.notifier.send(f"{t}\n{body}", "info" if good else "critical")
        sent.append(t)
    n0, n1 = int(prev.get("scored") or 0), int(fw.get("scored") or 0)
    if n1 // 100 > n0 // 100 and fw.get("decision") == "continue":
        t = f"예측력 전진 검증 {n1 // 100 * 100}건 돌파"
        push(app.engine, "power", t, f"적중 {(fw.get('hit_rate') or 0):.1%} · {fw.get('label')} · 예상 판정일 {fw.get('eta') or '-'}",
             link="#power", dedupe=f"power:n{n1 // 100}")
        sent.append(t)
    return sent


def run_signal_study(app, marcap_dir: str, start: int = 2010, end: int | None = None, top: int = 100) -> dict:
    from .data.collectors.marcap import build_krx_dataset
    from .review.power import signal_study
    end = end or datetime.now(UTC).year
    ds = build_krx_dataset(marcap_dir, start, end, top)
    res = signal_study(ds.bars, ds.eligible, ds.benchmark)
    res["source"] = {"data": "FinanceData/marcap (KRX 전 종목, 상장폐지 포함)", "start": start, "end": end, "top_n": top,
                     "symbols": len(ds.bars)}
    signal_study_path(app).parent.mkdir(parents=True, exist_ok=True)
    signal_study_path(app).write_text(json.dumps(_json(res), ensure_ascii=False), encoding="utf-8")
    return res


# ------------------------------------------------------------------ 업종 · 대체데이터 · 이벤트 추출 · A/B · 환율 · 공증
def wics(app, fetcher=None, force: bool = False) -> dict:
    from .data.collectors.wics import refresh
    return refresh(app.engine, fetcher, force=force)


def rotation(app) -> list[dict]:
    from .engines.sector import rotation as rrg
    from .engines.sector import sector_map
    bars, bench, _ = app.market_data()
    return _json(rrg(bars, sector_map(app.engine), bench))


def alt_collect(app, fetch=None) -> dict:
    from .data.collectors.altdata import collect
    names = _names(app)
    return collect(app.engine, [(s, names.get(s)) for s in _focus(app)], fetch)


def extract_events(app, days: int = 30) -> dict:
    from .engines.event_extract import annotate
    names = {s: n for s, n in _names(app).items() if n and n != s}
    caps = {}
    bars, _, _ = app.market_data()
    for s, b in bars.items():
        if "marcap" in b and len(b):
            caps[s] = float(b["marcap"].iloc[-1])
    with session_scope(app.engine) as s:
        res = annotate(s, names, caps, datetime.now(UTC) - timedelta(days=days))
    ops.set_state(app.engine, "event_extract", _json(res | {"at": datetime.now(UTC).isoformat()}))
    return res


def batch_ab(app, adjust_sizes: bool = True) -> dict:
    from .review.batch_ab import adjust, report
    with session_scope(app.engine) as s:
        rep = report(s)
    if adjust_sizes:
        cur = {}
        for prov, cfg in app.settings.llm_providers.items():
            cur[prov] = int(cfg.get("batch") or 1)
        rep["changed"] = adjust(app.engine, rep, cur)
    ops.set_state(app.engine, "batch_ab", _json(rep))
    return rep


def fx(app) -> dict:
    from .review.fx_attrib import for_app
    return _json(for_app(app))


def notarize(app, post=None) -> dict:
    from .review.ledger import latest_anchor
    from .review.notary import notarize as _n
    with session_scope(app.engine) as s:
        a = latest_anchor(s)
    if a is None:
        a = app.ledger_anchor()
        if a is None:
            return {"skipped": "봉인할 예측이 없습니다"}
    return _n(app, a, post)


# ------------------------------------------------------------------ 종목 데스크 (종목 화면 v13 패널)
def stock_desk(app, symbol: str) -> dict:
    from .data.collectors import altdata, options
    from .engines import events as E
    from .engines.earnings import get as earnings_get
    from .engines.event_extract import extract
    from .engines.knowledge import REL_LABEL, neighbors, two_hop
    from .pipeline import latest_consensus
    now = datetime.now(UTC)
    try:
        bars, bench, _ = app.market_data() if symbol[:1].isdigit() else _us_data(app)
    except Exception:  # noqa: BLE001 - 미국 데이터가 없을 수 있다
        bars, bench = {}, None
    b = bars.get(symbol)
    rec = latest_consensus(app.engine, symbol)
    plan = (rec.payload or {}).get("plan") if rec else None
    if plan is None and b is not None and rec is not None:
        plan = app._trade_plan(symbol, b, b.index[-1], rec.prob_up, action=rec.action)
    cal = ops.get_state(app.engine, "event_calendar")
    today = E.today_kst(now)
    evs = [e for e in cal.get("events", []) if e.get("symbol") == symbol and e.get("d_day", -1) >= -7]
    risk = next((r for r in cal.get("risk", []) if r["symbol"] == symbol), None)
    if risk is None and cal.get("events"):
        risk = E.event_risk(symbol, cal["events"], today)
    earn_date = None
    if risk and risk.get("earnings"):
        from datetime import date as _d
        earn_date = _d.fromisoformat(risk["earnings"]["date"])
    try:
        opt = options.get(app.engine, symbol, earn_date)
    except Exception as e:  # noqa: BLE001
        opt = {"available": False, "error": type(e).__name__}
    try:
        em = earnings_get(app.engine, symbol, b, bench, earn_date, opt.get("earnings_implied_move"))
    except Exception as e:  # noqa: BLE001
        em = {"error": type(e).__name__}
    kr_implied = None
    if symbol[:1].isdigit() and b is not None and len(b) > 60:
        from .data.collectors import vkospi as VK
        vk = VK.get(app.engine, bench)
        r = b["close"].pct_change().dropna()
        sig = float(r.iloc[-20:].std())
        beta = None
        if bench is not None and len(bench) > 60:
            m = bench["close"].pct_change().reindex(r.index).dropna()
            if m.iloc[-120:].var() > 0:
                beta = float(np.cov(r.reindex(m.index).iloc[-120:], m.iloc[-120:])[0, 1] / m.iloc[-120:].var())
        sys_hist = abs(beta or 1.0) * float(bench["close"].pct_change().iloc[-20:].std()) if bench is not None and len(bench) > 21 else 0.0
        idio = math.sqrt(max(sig * sig - sys_hist * sys_hist, 0.0))
        if vk.get("available"):
            kr_implied = {"vkospi": vk.get("level"), "source": vk.get("source"), "proxy": vk.get("proxy"), "percentile_1y": vk.get("percentile_1y"),
                          "beta": None if beta is None else round(beta, 2), "move_1d": VK.stock_move(vk, beta, idio, 1),
                          "move_5d": VK.stock_move(vk, beta, idio, 5), "move_20d": VK.stock_move(vk, beta, idio, 20), "as_of": vk.get("as_of")}
    flow = ops.get_state(app.engine, f"flow:{symbol}")
    g = ops.get_state(app.engine, "kgraph")
    nb = neighbors(g, symbol, 8) if g else []
    for n in nb:
        n["relation_label"] = REL_LABEL.get(n.get("relation") or "")
    names = _names(app)
    with session_scope(app.engine) as s:
        disc = s.scalars(select(Disclosure).where(Disclosure.symbol == symbol).order_by(Disclosure.filed_at.desc()).limit(15)).all()
        from .data.models import NewsArticle
        news = s.scalars(select(NewsArticle).where(NewsArticle.published_at >= now - timedelta(days=30))
                         .order_by(NewsArticle.published_at.desc()).limit(400)).all()
        items = [(d.title, d.filed_at, "공시") for d in disc] + [(n.title, n.published_at, "뉴스") for n in news if symbol in (n.symbols or [])][:20]
    extracted = []
    for t, at, src in items:
        for e in extract(t, names, symbol):
            extracted.append({**e, "title": t[:120], "date": at.date().isoformat() if hasattr(at, "date") else str(at)[:10], "source": src})
    return _json({
        "symbol": symbol, "name": names.get(symbol, symbol), "as_of": label(now),
        "bar_as_of": label(b.index[-1], with_time=False) if b is not None and len(b) else None,
        "plan": plan, "action": rec.action if rec else None, "plan_sealed": bool(rec and rec.row_hash and (rec.payload or {}).get("plan")),
        "plan_at": label(rec.as_of) if rec else None,
        "events": evs[:12], "event_risk": risk, "options": opt, "kr_implied": kr_implied, "earnings_model": em,
        "flow": (flow.get("summary") or {}) | ({"rows": flow.get("rows")[-60:]} if flow.get("rows") else {}) if flow else {},
        "flow_at": label(flow.get("at")) if flow else None,
        "alt": altdata.for_context(app.engine, symbol) | {"raw": ops.get_state(app.engine, f"alt:{symbol}")},
        "graph": {"neighbors": nb, "two_hop": two_hop(g, symbol) if g else []},
        "extracted": extracted[:15],
    })


def _us_data(app):
    from . import global_market
    bars, bench, _ = global_market.market_data(app)
    return bars, bench, None


# ------------------------------------------------------------------ 내 저널
def my_journal_add(app, body: dict) -> dict:
    from .review.my_journal import add
    sym = str(body.get("symbol") or "").strip().upper()
    if not sym or len(sym) > 12:
        raise ValueError("종목을 입력하세요")
    bars, _ = app._all_bars()
    ref = float(bars[sym]["close"].iloc[-1]) if sym in bars and len(bars[sym]) else None
    from .pipeline import latest_consensus
    c = latest_consensus(app.engine, sym)  # 그때 AI 가 뭐라고 했는지 자동으로 함께 봉인
    tags = {"view": str(body.get("view") or "")[:500] or None, "exit_reason": str(body.get("exit_reason") or "")[:500] or None,
            "ai": {"action": c.action, "prob_up": round(c.prob_up, 3), "as_of": c.as_of.isoformat()} if c else None,
            "run5": round(float(bars[sym]["close"].iloc[-1] / bars[sym]["close"].iloc[-6] - 1), 4) if sym in bars and len(bars[sym]) > 6 else None}
    with session_scope(app.engine) as s:
        return add(s, sym, body.get("action", "BUY"), int(body.get("conviction") or 3), int(body.get("horizon") or 5),
                   body.get("reason"), ref, tags={k: v for k, v in tags.items() if v is not None})


def my_journal(app) -> dict:
    from .review.my_journal import compare, score
    bars, _ = app._all_bars()
    with session_scope(app.engine) as s:
        score(s, bars)
    with session_scope(app.engine) as s:
        out = compare(s)
    names = _names(app)
    for r in out["rows"]:
        r["name"] = names.get(r["symbol"], r["symbol"])
    return _json(out)


__all__ = ["event_calendar", "event_impact", "readiness", "kis_validate", "slippage_calibrate", "model_decay",
           "prediction_power", "run_signal_study", "wics", "rotation", "alt_collect", "extract_events", "batch_ab", "fx",
           "notarize", "stock_desk", "my_journal", "my_journal_add", "portfolio_gate", "risk_of_ruin", "cost_config",
           "event_caps_for", "power_alerts", "record_parity", "sim_bias", "PARITY_MIN"]

"""v13 P0: 포트폴리오 리스크 2.0 · Risk-of-Ruin · 리스크 게이트 · 시장 캘린더 · 이벤트 캘린더/위험/영향 · AS-OF · Readiness."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops


def _bars(n=320, seed=1, start="2019-06-03", drift=0.0004, vol=0.018, common=None, beta=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n, tz="UTC")
    r = rng.normal(drift, vol, n) + (beta * common if common is not None else 0)
    c = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
    return pd.DataFrame({"open": c * 0.999, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 2e6})


@pytest.fixture(scope="module")
def market():
    rng = np.random.default_rng(0)
    common = rng.normal(0, 0.012, 320)
    bars = {f"S{i}": _bars(seed=i + 1, common=common, beta=1.0 if i < 3 else 0.1) for i in range(6)}
    bench = _bars(seed=99, common=common, beta=1.0, vol=0.002)
    return bars, bench


# ------------------------------------------------------------------ 포트폴리오 리스크 2.0
def test_portfolio_risk_v2_has_every_lens(market):
    from quant_ai.trading.portfolio_risk import PortfolioRiskLimits, portfolio_risk
    bars, bench = market
    w = {"S0": 0.25, "S1": 0.2, "S2": 0.2, "S3": 0.1, "S4": 0.05}
    sectors = {"S0": "반도체", "S1": "반도체", "S2": "반도체", "S3": "은행"}
    r = portfolio_risk(w, bars, bench, values={s: x * 1e8 for s, x in w.items()}, sectors=sectors,
                       limits=PortfolioRiskLimits(max_sector_weight=0.4, max_name_weight=0.2))
    assert r["concentration"]["top1"] == 0.25 and 3 < r["concentration"]["effective_n"] < 5
    assert r["sectors"][0]["sector"] == "반도체" and r["sectors"][0]["weight"] == pytest.approx(0.65)
    assert any("업종 반도체" in x for x in r["warnings"]) and any("한 종목" in x for x in r["warnings"])
    v = r["var"]
    assert {"hist95", "hist99", "es95", "normal95", "cornish_fisher95", "ewma95", "hist95_10d"} <= set(v)
    assert v["hist99"] >= v["hist95"] and v["es95"] >= v["hist95"]
    # 성분 VaR 합 = 정규 근사 포트폴리오 VaR (평균 0 근사)
    assert sum(c["share"] for c in r["component_var"]) == pytest.approx(1.0, abs=1e-3)
    assert r["diversification_benefit"] > 0 and r["undiversified_var95"] >= r["var95"]
    assert r["lvar95"] >= r["var95"] and r["corr_matrix"]["symbols"][0] == "S0"
    names = {x["name"] for x in r["stress"]}
    assert "2020 코로나 급락" in names and "시장 -10%" in names and any("업종" in n for n in names)
    covid = next(x for x in r["stress"] if x["name"] == "2020 코로나 급락")
    assert covid["basis"] == "종목 실제 수익률"
    assert r["worst_stress"]["loss"] == max(x["loss"] for x in r["stress"] if x["loss"] is not None)


def test_pretrade_gate_caps_sector_cluster_and_var(market):
    from quant_ai.trading.portfolio_risk import PortfolioRiskLimits, pretrade_check
    bars, _ = market
    base = {"S0": 0.2, "S1": 0.15}
    lim = PortfolioRiskLimits(max_sector_weight=0.4, max_cluster_weight=0.45, max_var95=0.5)
    r = pretrade_check(base, "S2", 0.2, bars, {"S0": "반도체", "S1": "반도체", "S2": "반도체"}, lim)
    assert not r["ok"] and r["allowed"] == pytest.approx(0.05) and "업종" in r["reasons"][0]
    ok = pretrade_check(base, "S4", 0.05, bars, {}, lim)
    assert ok["ok"] and ok["allowed"] == pytest.approx(0.05)
    tight = pretrade_check(base, "S3", 0.3, bars, {}, PortfolioRiskLimits(max_var95=0.012, max_cluster_weight=1))
    assert tight["allowed"] < 0.3 and any("VaR" in x for x in tight["reasons"])


def test_risk_of_ruin_monte_carlo():
    from quant_ai.trading.ruin import simulate, verdict
    rng = np.random.default_rng(1)
    safe = simulate(rng.normal(0.0008, 0.006, 500))
    risky = simulate(rng.normal(-0.002, 0.04, 500))
    assert safe["p_ruin"] == 0 and safe["p_drawdown"]["10"] < risky["p_drawdown"]["10"]
    assert risky["p_ruin"] > 0.5 and verdict(risky).startswith("위험")
    assert simulate([0.01] * 10)["insufficient"]


def test_risk_engine_gates_block_and_shrink_buys_only():
    from quant_ai.config import RiskLimits
    from quant_ai.trading.portfolio import Order, Portfolio, Position, Side
    from quant_ai.trading.risk import RiskEngine
    pf = Portfolio(cash=10_000_000, positions={"A": Position(10, 1000)})
    px = {"A": 1000.0, "B": 1000.0}
    rk = RiskEngine(RiskLimits(max_position_weight=1, max_order_value=1e9, max_adv_participation=0))
    rk.start_day(date(2026, 9, 30), pf.equity(px))
    rk.buy_block = "NOT READY — DATA"
    assert not rk.check(Order("B", Side.BUY, 100), pf, px).approved
    assert rk.check(Order("A", Side.SELL, 5), pf, px).approved  # 매도는 항상 허용
    rk.buy_block = None
    rk.event_caps = {"B": (0.5, "실적 발표 D-1")}
    d = rk.check(Order("B", Side.BUY, 100), pf, px)
    assert d.order.qty == 50 and "실적" in d.reasons[-1]
    rk.event_caps = {}
    rk.portfolio_gate = lambda sym, q, p, portfolio, prices: (30, "업종 한도")
    d = rk.check(Order("B", Side.BUY, 100), pf, px)
    assert d.order.qty == 30 and "포트폴리오 한도" in d.reasons[-1]


# ------------------------------------------------------------------ 시장 캘린더
def test_exchange_calendar_holidays_and_special_sessions():
    from quant_ai.clock import KRX, US, Phase
    assert not KRX.is_trading_day(date(2026, 9, 25)) and "추석" in KRX.holiday_name(date(2026, 9, 25))
    assert not KRX.is_trading_day(date(2026, 12, 31))  # 연말 휴장
    assert KRX.phase(datetime(2026, 9, 25, 1, 0, tzinfo=UTC)) is Phase.CLOSED
    o, c, note = KRX.session(date(2026, 1, 2))
    assert o.hour == 10 and "새해" in note
    o, c, note = US.session(date(2026, 11, 27))
    assert c.hour == 13 and "조기" in note
    assert KRX.trading_days_between(date(2026, 9, 23), date(2026, 9, 29)) == 2  # 24·25 추석 → 28·29
    assert KRX.prev_trading_day(date(2026, 9, 28)) == date(2026, 9, 23)


# ------------------------------------------------------------------ 이벤트 캘린더 · 위험
def test_event_calendar_builds_every_kind_with_sources():
    from quant_ai.engines import events as E
    ev = E.build_calendar(date(2026, 9, 28), date(2026, 12, 31),
                          profiles={"005930": {"events": [{"kind": "earnings", "label": "실적 발표", "date": "2026-10-08", "estimated": True},
                                                          {"kind": "ex_div", "label": "배당락", "date": "2026-12-29"}]}},
                          names={"005930": "삼성전자"}, custom=[{"ts": "2026-10-15T00:00:00Z", "name": "한은 금통위", "country": "KR"}])
    kinds = {e["kind"] for e in ev}
    assert {"earnings", "ex_div", "holiday", "half_day", "options_expiry", "quad_witching", "index_rebalance", "fomc",
            "nfp", "export", "custom"} <= kinds
    assert E.kr_options_expiry(2026, 10) == date(2026, 10, 8) and E.kr_options_expiry(2026, 12) == date(2026, 12, 10)
    assert E.us_options_expiry(2026, 12) == date(2026, 12, 18)
    reb = [e for e in ev if e["kind"] == "index_rebalance" and "KOSPI200" in e["title"]]
    assert reb and reb[0]["date"] == "2026-12-11"
    assert all(e["source"] for e in ev)
    ev = E.with_dday(ev, date(2026, 10, 7))
    risk = E.event_risk("005930", ev, date(2026, 10, 7), sigma=0.02)
    assert risk["buy_multiplier"] == 0.5 and "D-1" in risk["reason"] and risk["expected_move"] == pytest.approx(0.05)
    assert E.event_caps(["005930"], ev, date(2026, 10, 7), "block")["005930"][0] == 0.0
    assert E.event_caps(["005930"], ev, date(2026, 10, 7), "off") == {}
    far = E.event_risk("005930", E.with_dday(ev, date(2026, 9, 28)), date(2026, 9, 28))
    assert far["buy_multiplier"] == 1.0


def test_fred_release_dates_parse():
    from quant_ai.engines.events import fetch_fred_release_dates
    fake = lambda url: {"release_dates": [{"release_id": 10, "date": "2026-10-14"}]}  # noqa: E731
    out = fetch_fred_release_dates("k", date(2026, 10, 1), date(2026, 10, 31), fake)
    assert {"date": "2026-10-14", "kind": "cpi", "title": "미국 CPI (소비자물가)", "importance": 0.9} in out
    assert fetch_fred_release_dates("", date(2026, 10, 1), date(2026, 10, 31)) == []


def test_event_impact_finds_real_abnormal_returns():
    from quant_ai.review.event_impact import abnormal, market_impact, prediction_attribution, stock_impact
    b = _bars(400, seed=3, start="2024-01-01")
    bench = _bars(400, seed=4, start="2024-01-01", vol=0.01)
    days = [b.index[i].date() for i in (200, 250, 300, 350)]
    for d in days:  # 이벤트 날 +6% 점프
        i = b.index.get_loc(pd.Timestamp(d, tz="UTC"))
        b.iloc[i:, b.columns.get_loc("close")] *= 1.06
    a = abnormal(b, bench, days[0])
    assert a["ar0"] > 0.04 and a["z0"] > 2
    si = stock_impact({"X": b}, bench, [{"symbol": "X", "date": d, "kind": "공급계약·수주"} for d in days])
    assert si[0]["n"] == 4 and si[0]["mean"] > 0.04 and si[0]["pos_share"] == 1.0
    ev_days = [{"date": bench.index[i].date(), "kind": "fomc", "market": "KR"} for i in range(150, 390, 20)]
    for e in ev_days:
        i = bench.index.get_loc(pd.Timestamp(e["date"], tz="UTC"))
        bench.iloc[i:, bench.columns.get_loc("close")] *= 0.97
    mk = market_impact(bench, ev_days, "KR")
    assert mk[0]["kind"] == "fomc" and mk[0]["vs_normal"] > 2
    items = [{"symbol": "X", "as_of": d - timedelta(days=2), "hit": False} for d in days] + \
            [{"symbol": "X", "as_of": date(2024, 3, 1), "hit": True}]
    att = prediction_attribution(items, [{"symbol": "X", "date": d, "kind": "공급계약·수주", "market": "KR"} for d in days])
    assert att["with_event"]["n"] == 4 and att["with_event"]["hit_rate"] == 0 and att["miss_share_with_event"] == 1.0


# ------------------------------------------------------------------ AS-OF
def test_asof_labels_and_bar_staleness_respect_calendar():
    from quant_ai.asof import label, last_trading_close, stamp
    assert label(datetime(2026, 9, 30, 5, 32, tzinfo=UTC)) == "2026-09-30 14:32 KST"
    sat = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)  # 토요일 12:00 KST
    assert last_trading_close("KR", sat) == date(2026, 10, 2)
    assert stamp(datetime(2026, 10, 2, 6, 30, tzinfo=UTC), "bar_kr", sat)["status"] == "fresh"
    # 추석 연휴 뒤 월요일 오전: 마지막 거래일은 9/23
    assert last_trading_close("KR", datetime(2026, 9, 28, 0, 30, tzinfo=UTC)) == date(2026, 9, 23)
    old = stamp(datetime(2026, 9, 1, 6, 30, tzinfo=UTC), "bar_kr", sat)
    assert old["status"] == "old" and "거래일 밀림" in old["age"]
    q = stamp(sat - timedelta(minutes=20), "quote", sat, market_open=True)
    assert q["status"] == "stale" and stamp(sat - timedelta(minutes=40), "quote", sat, market_open=False)["status"] == "fresh"
    assert stamp(None, "news")["status"] == "none"


# ------------------------------------------------------------------ Readiness (앱)
@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Mode, Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v13")
    fake_marcap(d, n_codes=6, days=300)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a",
                        max_data_age_days=0, mode=Mode.SHADOW))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def test_readiness_seven_gates_and_buy_block(app):
    from quant_ai import desk, readiness
    r = desk.readiness(app, "shadow")
    assert [c["key"] for c in r["checks"]] == list(readiness.ORDER)
    assert r["status"] in ("READY", "CAUTION", "NOT_READY") and r["as_of"].endswith("KST")
    data = next(c for c in r["checks"] if c["key"] == "DATA")
    assert data["status"] == "red"  # 2020년 가짜 데이터 → 일봉이 수년 밀림
    assert r["status"] == "NOT_READY" and readiness.buy_block(app, "shadow")
    assert readiness.buy_block(app, "paper") is None  # 기본 게이트: live·shadow 만
    app.set_kill_switch(True, "test")
    try:
        r2 = desk.readiness(app, "shadow")
        assert next(c for c in r2["checks"] if c["key"] == "RISK")["status"] == "red"
    finally:
        app.set_kill_switch(False)
    hist = ops.get_state(app.engine, "readiness")["history"]
    assert len(hist) >= 2


def test_model_gate_requires_proof_for_real_money(app):
    from quant_ai.readiness import check_model
    ops.set_state(app.engine, "evaluation", {"status": "insufficient", "verdict": "표본 부족"})
    if app.active_model()[0] is None:
        assert check_model(app, "live", True)["status"] == "red"
    else:
        c = check_model(app, "live", True)
        assert c["status"] == "red" and "검증 통과" in c["detail"]


def test_desk_calendar_and_impact_run_on_app(app):
    from quant_ai import desk
    cal = desk.event_calendar(app)
    assert cal["events"] and cal["as_of"].endswith("KST") and ops.get_state(app.engine, "event_calendar")["at"]
    imp = desk.event_impact(app)
    assert "market_kr" in imp and "prediction" in imp
    pr = app.portfolio_risk("paper", with_ruin=True)
    assert "n_positions" in pr

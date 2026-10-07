"""v31: 추천 목표 계획 (200만원 + 매달 100만원 → 1억) · 해마다 점검표 · 진행 범위 판정 · 실제 계좌 AI 범위(ai_cap · 적립 ETF 보호)."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from quant_ai import ops

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v31")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


@pytest.fixture(autouse=True)
def offline_etf(monkeypatch):
    """ETF 가격은 인터넷 없이 고정 (Yahoo 호출 막기)."""
    from quant_ai import goal
    monkeypatch.setattr(goal, "last_close", lambda app, sym: (40_000.0, "2026-10-05"))


# ------------------------------------------------------------------ 1. 추천 계획 한 번에 적용
def test_apply_recommended_paper_seeds_etf_book_and_saves_checkpoints(app):
    from quant_ai import goal
    r = goal.apply_recommended(app, "m100", "paper")
    g = r["saved"]
    assert (g["principal"], g["monthly"], g["goal"], g["target_years"], g["strategy"]) == (2_000_000, 1_000_000, 100_000_000, 7, "kospi")
    assert g["ai_cap"] == 0.15 and g["preset"] == "m100" and g["start"] == datetime.now(goal.KST).date().isoformat()
    assert g["dca"] == {**g["dca"], "on": True, "day": 25, "mode": "paper", "target": "etf", "etf": "069500", "amount": 1_000_000}
    assert 0.6 < g["p_target"] < 0.8  # 7년 안 1억: 약 68% (KOSPI 가정) — 보장이 아니라 확률
    cps = g["checkpoints"]
    assert len(cps) == 10 and cps[0]["paid"] == 14_000_000 and cps[6]["paid"] == 86_000_000
    assert all(c["p10"] <= c["p50"] <= c["p90"] for c in cps)
    # 원금 200만원으로 ETF 49주 (4만원 · 비용 포함) · 입출금 기록 1건
    assert g["seeded"]["bought"] == 49 and len(app.cashflows(goal.ETF_BOOK)) == 1
    pr = goal.progress(app)
    assert pr["band_level"] == "start" and pr["next_check"]["year"] == 1 and pr["ai_cap"] == 0.15
    assert "200만원으로 시작" in r["next"]


def test_apply_recommended_live_gives_start_sheet_and_validates(app):
    from quant_ai import goal
    r = goal.apply_recommended(app, "m100", "live", monthly=1_500_000)
    assert r["saved"]["monthly"] == 1_500_000 and r["saved"]["dca"]["amount"] == 1_500_000 and r["saved"]["dca"]["mode"] == "live"
    assert "49주" in r["start_sheet"] and "KODEX 200" in r["start_sheet"] and "ISA" in r["next"]
    with pytest.raises(ValueError):
        goal.apply_recommended(app, "lottery")
    with pytest.raises(ValueError):
        goal.save(app, {"principal": 1, "monthly": 1, "goal": 10, "ai_cap": 0.5})
    # 화면에서 숫자만 바꿔 저장해도 AI 비중 · 계획 이름은 남는다
    g = goal.save(app, {"principal": 2_000_000, "monthly": 1_000_000, "goal": 100_000_000, "target_years": 7, "strategy": "kospi"})
    assert g["ai_cap"] == 0.15 and g["preset"] == "m100"


# ------------------------------------------------------------------ 2. 진행 범위 판정
def test_band_interpolates_and_judges():
    from quant_ai import goal
    g = {"principal": 2_000_000}
    cps = [{"year": 1, "p10": 12e6, "p50": 14e6, "p90": 16e6, "paid": 14e6}, {"year": 2, "p10": 24e6, "p50": 28e6, "p90": 32e6, "paid": 26e6}]
    b = goal.band_at(g, cps, 6)
    assert b == {"p10": 7_000_000, "p50": 8_000_000, "p90": 9_000_000, "paid": 8_000_000}
    assert goal.band_at(g, cps, 99)["p50"] == 28e6  # 끝을 넘으면 마지막 값
    st = date(2026, 10, 6)
    assert goal.band_check(g, cps, 12, 11e6, st)["band_level"] == "low"
    assert goal.band_check(g, cps, 12, 13e6, st)["band_level"] == "ok"
    assert goal.band_check(g, cps, 12, 15e6, st)["band_level"] == "good"
    assert goal.band_check(g, cps, 12, 17e6, st)["band_level"] == "great"
    nc = goal.band_check(g, cps, 12, 15e6, st)["next_check"]
    assert nc["year"] == 2 and nc["date"] == "2028-10" and goal.band_check(g, cps, 30, 1, st)["next_check"] is None


# ------------------------------------------------------------------ 3. 실제 계좌: AI 범위 (적립 ETF 는 절대 팔지 않음)
def test_live_scope_protects_human_holdings_and_caps_budget(app):
    from quant_ai import autopilot as AP
    from quant_ai.trading.portfolio import Portfolio, Position
    pf = Portfolio(cash=1_000_000.0)
    pf.positions["069500"] = Position(qty=100, avg_price=40_000.0)   # 사람이 산 적립 ETF
    pf.positions["005930"] = Position(qty=2, avg_price=70_000.0)     # AI 가 산 종목
    st = ops.get_state(app.engine, AP.STATE)
    ops.set_state(app.engine, AP.STATE, {**st, "meta": {**(st.get("meta") or {}), "live": {"005930": {"entry_date": "2026-10-01"}}}})
    prices = {"069500": 40_000.0, "005930": 70_000.0}
    hold = {"069500": {"qty": 100, "avg_price": 40_000.0}, "005930": {"qty": 2, "avg_price": 70_000.0}}
    sc = AP.live_scope(app, pf, prices, hold)
    eq = 1_000_000 + 4_000_000 + 140_000
    assert sc["protect"] == {"069500"} and sc["equity"] == eq and sc["ai_value"] == 140_000 and sc["ai_cap"] == 0.15
    budget = min(eq * 0.15, app.live_cap())
    assert sc["budget"] == round(budget)
    br = min(1.0, app.live_cap() / eq)
    assert abs(sc["scale"] - budget / (br * eq)) < 1e-9


def test_live_run_trades_only_ai_symbols_within_budget(app, monkeypatch):
    from quant_ai import autopilot as AP
    from quant_ai.config import Mode
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import PortfolioSnapshot
    bars, _, _ = app.market_data()
    syms = [s for s in bars if s[:1].isdigit()][:3]
    px = {s: float(bars[s]["close"].iloc[-1]) for s in syms}
    with session_scope(app.engine) as s:  # 실제 계좌: 사람이 산 ETF + 현금
        s.add(PortfolioSnapshot(mode="live", ts=datetime(2026, 10, 7, 0, tzinfo=UTC), cash=3_000_000.0, equity=7_000_000.0,
                                positions={"069500": {"qty": 100, "avg_price": 40_000.0}}))
    st = ops.get_state(app.engine, AP.STATE)
    ops.set_state(app.engine, AP.STATE, {**st, "meta": {**(st.get("meta") or {}), "live": {}}})
    calls = []
    real = app.trade

    def fake_trade(decisions, mode, ts=None, quotes=None, signals=None, book=None, protect=None):
        if mode is Mode.LIVE:
            calls.append({"signals": signals, "protect": protect})
            return []
        return real(decisions, mode, ts=ts, quotes=quotes, signals=signals, book=book, protect=protect)
    monkeypatch.setattr(app, "trade", fake_trade)
    monkeypatch.setattr(AP, "live_enabled", lambda a: True)
    rows = {s: {"symbol": s, "name": s, "score": 2.0, "last": px[s], "stop": px[s] * 0.9, "signals": [], "evidence": {}} for s in syms}
    full = {"_rows": rows, "as_of": "2026-10-07", "regime": {"above_200": True}, "calibration": {}}
    r = AP.run(app, now=datetime(2026, 10, 8, 2, tzinfo=UTC), force=True, full=full, allow_stale=True)
    pl = r["books"]["live"]
    assert calls and calls[0]["protect"] == {"069500"} and pl["scope"]["protect"] == ["069500"]
    assert all(a["symbol"] != "069500" for a in pl["actions"])  # ETF 는 '계산 대상에서 빠짐 → 팔기' 가 되지 않는다
    budget = pl["scope"]["budget"]
    assert budget == round(min(7_000_000 * 0.15, app.live_cap()))  # ETF 가격은 장부 평균가로 평가
    # 신호 비중 × 계좌 × 소액 상한 비율 = AI 예산 기준 금액 (종목당 18%)
    br = min(1.0, app.live_cap() / 7_000_000)
    assert calls[0]["signals"], pl.get("skipped")
    for sig in calls[0]["signals"]:
        assert abs(sig.target_weight * br * 7_000_000 - 0.18 * budget) < 1


def test_pipeline_protect_never_orders_protected(app):
    from quant_ai.config import Mode
    from quant_ai.trading.execution import Signal
    bars, _, _ = app.market_data()
    a, b = [s for s in bars if s[:1].isdigit()][:2]
    t0 = datetime(2026, 10, 9, 2, tzinfo=UTC)
    app.trade([], Mode.PAPER, ts=t0, signals=[Signal(a, 0.2), Signal(b, 0.2)], book="protect-test")
    pf = app.load_portfolio("protect-test")
    assert pf.qty(a) > 0 and pf.qty(b) > 0
    fills = app.trade([], Mode.PAPER, ts=datetime(2026, 10, 10, 2, tzinfo=UTC), signals=[], book="protect-test", protect={a})
    assert {f.order.symbol for f in fills} == {b}  # 목표에 없지만 보호된 a 는 그대로
    assert app.load_portfolio("protect-test").qty(a) == pf.qty(a)


# ------------------------------------------------------------------ 4. 화면 · CLI
def test_goal_screen_and_cli_wired():
    js = (STATIC / "toss2.js").read_text(encoding="utf-8")
    assert "function goalRecCard" in js and "data-gapply" in js and "해마다 점검표" in js and "function goalBandLine" in js
    assert "s.live_rule" in js
    from quant_ai.cli import main
    with pytest.raises(SystemExit):
        main(["goal-plan", "--help"])


def test_api_apply_and_cli_show(app, capsys):
    from quant_ai.cli import cmd_goal_plan
    from quant_ai.web.api import DashboardAPI
    api = DashboardAPI(app)
    r = api.goal_save({"apply": "m100", "mode": "paper"})
    assert r["ok"] and r["saved"]["preset"] == "m100"
    d = api.goal({})
    assert "m100" in d["recommended"] and d["saved"]["ai_cap"] == 0.15 and d["progress"]["band_level"] == "start"
    import argparse
    cmd_goal_plan(argparse.Namespace(action="show", db=app.settings.database_url))
    out = capsys.readouterr().out
    assert "매달 1,000,000원" in out and "7년 뒤" in out and "AI 비중 상한 15%" in out

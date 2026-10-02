"""실전 운용 보조 도구: 리밸런싱 주문표 · 전략 건강검진 · 추세 필터 · 소액 계좌 대체 · 연도별 세율."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from quant_ai.config import CostModelConfig, Settings
from quant_ai.strategy.core_satellite import CoreSatelliteConfig, build_plan
from quant_ai.strategy.health import REFERENCE, evaluate, reference_from_curve
from quant_ai.strategy.order_sheet import make_order_sheet, parse_holdings

NO_COST = CostModelConfig(commission_bps=0.0, slippage_bps=0.0, sell_tax_bps=0.0)


# ------------------------------------------------------------------ 주문표 산수
def test_order_sheet_sells_first_and_integer_quantities():
    sheet = make_order_sheet({"A": 0.5, "B": 0.5}, {"C": 10}, cash=0, prices={"A": 1000, "B": 3000, "C": 1000},
                             costs=NO_COST)
    sides = [ln.side for ln in sheet.lines]
    assert sides == ["SELL", "BUY", "BUY"]  # 매도 먼저
    by = {ln.symbol: ln for ln in sheet.lines}
    assert by["C"].qty == 10 and by["C"].target_qty == 0
    assert by["A"].qty == 5 and by["B"].qty == 1  # 5000원씩: A 5주, B 1주 (정수 내림)
    assert sheet.cash_after == pytest.approx(10_000 - 5_000 - 3_000)


def test_order_sheet_limit_prices_on_tick_grid():
    sheet = make_order_sheet({"A": 1.0}, {"B": 3}, cash=1_000_000, prices={"A": 52_340, "B": 9_870})
    by = {ln.symbol: ln for ln in sheet.lines}
    assert by["A"].limit_price == 52_900  # 52,340×1.01=52,863.4 → 100원 단위 올림
    assert by["B"].limit_price == 9_770  # 9,870×0.99=9,771.3 → 10원 단위 내림
    assert by["B"].est_cost == pytest.approx(3 * 9_870 * (1.5 + 20) / 1e4)  # 수수료 + 매도세


def test_order_sheet_skips_tiny_adjustments_but_not_entries():
    # 목표 50% 에 거의 맞음 → 생략, 신규 종목은 작아도 매수
    # 평가금액 10만원, A 목표 500주 vs 보유 497주 → 차이 300원 < 0.5%(500원)
    sheet = make_order_sheet({"A": 0.5, "B": 0.004}, {"A": 497}, cash=50_300, prices={"A": 100, "B": 100},
                             costs=NO_COST)
    by = {ln.symbol: ln for ln in sheet.lines}
    assert by["A"].side == "HOLD" and "생략" in by["A"].reason
    assert by["B"].side == "BUY" and by["B"].qty == 4


def test_order_sheet_never_spends_more_than_available_cash():
    # 5주씩 목표지만 지정가 상한(10,050원) 기준으로는 100,500원 필요 → 비례 축소
    sheet = make_order_sheet({"A": 0.5, "B": 0.5}, {}, cash=100_000, prices={"A": 9_950, "B": 9_950})
    need = sum(ln.qty * ln.limit_price for ln in sheet.lines if ln.side == "BUY")
    assert need <= 100_000 and any("현금 부족" in n for n in sheet.notes)
    assert sheet.cash_after >= 0


def test_order_sheet_unknown_and_unaffordable():
    sheet = make_order_sheet({"A": 0.5}, {"ZZZ": 5}, cash=100_000, prices={"A": 900_000})
    assert any("ZZZ" in n and "가격 데이터 없음" in n for n in sheet.notes)
    assert all(ln.symbol != "ZZZ" for ln in sheet.lines)  # 모르는 종목은 팔지 않음
    a = sheet.lines[0]
    assert a.side == "HOLD" and "매수 불가" in a.reason
    csv = sheet.to_csv()
    assert csv.splitlines()[0].startswith("순서,구분,종목코드") and "매수 불가" in csv


def test_parse_holdings_formats():
    text = '종목코드,수량\n005930,10\n5930 5\n000660\t"1,000"\t70000\n035720.KQ,3,52000\n# 주석\n123456,10,70000\n\n'
    assert parse_holdings(text) == {"005930": 15, "000660": 1000, "035720": 3, "123456": 10}


# ------------------------------------------------------------------ 코어: 추세 축소 · 소액 계좌 대체
SCORES = pd.Series([0.9, 0.8, 0.7, 0.6, 0.5], index=list("ABCDE"))
CFG = CoreSatelliteConfig(core_top_k=3, core_buffer_k=4, satellite_k=2)


def test_trend_scale_shrinks_core_weights():
    p = build_plan(SCORES, [], True, CFG, core_scale=0.5)
    assert sum(p.weights.values()) == pytest.approx(0.4)
    assert any("추세 필터" in n for n in p.notes)


def test_unaffordable_names_are_replaced_by_next_rank():
    p = build_plan(SCORES, [], True, CFG, unaffordable={"A"})
    assert p.core == ["B", "C", "D"] and any("A 1주 가격" in n for n in p.notes)


def test_trend_helper_detects_index_below_ma():
    from quant_ai.pipeline import QuantAI
    idx = pd.bdate_range("2024-01-01", periods=300)
    up = pd.DataFrame({"close": np.linspace(100, 200, 300)}, index=idx)
    down = pd.DataFrame({"close": np.r_[np.linspace(100, 200, 250), np.linspace(200, 120, 50)]}, index=idx)
    assert QuantAI._trend(up, CoreSatelliteConfig())["scale"] == 1.0
    t = QuantAI._trend(down, CoreSatelliteConfig())
    assert t["below"] and t["scale"] == 0.5 and t["index"] < t["ma"]
    assert QuantAI._trend(down, CoreSatelliteConfig(trend_ma=None))["scale"] == 1.0


# ------------------------------------------------------------------ 건강검진
def _curve(daily: np.ndarray, start="2020-01-01") -> pd.Series:
    return pd.Series(1e7 * np.cumprod(1 + daily), index=pd.bdate_range(start, periods=len(daily)))


def test_health_ok_warn_critical():
    rng = np.random.default_rng(1)
    calm = rng.normal(0.0004, 0.007, 400)  # 연 변동성 ~11%
    assert evaluate(_curve(calm))["status"] == "ok"
    rising = rng.normal(0.0015, 0.007, 400)  # 1년 수익률은 정상 범위로 유지하고 하락폭만 본다
    warn = np.r_[rising, np.full(30, -0.011)]  # 고점 대비 약 -28%
    r = evaluate(_curve(warn))
    assert r["status"] == "warn" and next(c for c in r["checks"] if c["name"] == "drawdown")["status"] == "warn"
    crash = np.r_[calm, np.full(40, -0.016)]  # 약 -47% (과거 최악 -43% 초과)
    r = evaluate(_curve(crash))
    assert r["status"] == "critical" and "킬스위치" in r["action"]


def test_health_short_history_is_insufficient():
    assert evaluate(_curve(np.full(5, 0.001)))["checks"][2]["status"] == "insufficient"
    assert evaluate(pd.Series(dtype=float))["status"] == "insufficient"


def test_health_relative_to_kospi_and_factor_ic():
    rng = np.random.default_rng(2)
    eq = _curve(rng.normal(0.0002, 0.007, 300))
    kospi = _curve(np.full(300, 0.0025))  # KOSPI 1년 약 +88% → 초과수익 과거 하위 5% 밖, 최악보다는 위
    r = evaluate(eq, kospi, factor_ic={"n": 12, "mean": -0.08, "t": -2.5})
    assert evaluate(eq, factor_ic={"n": 12, "mean": -0.03, "t": -0.6})["checks"][-1]["status"] == "ok"
    st = {c["name"]: c["status"] for c in r["checks"]}
    assert st["excess_1y"] == "warn" and st["factor_ic"] == "critical"


def test_reference_from_curve_keys_match_reference():
    rng = np.random.default_rng(3)
    eq = _curve(rng.normal(0.0003, 0.01, 800))
    ref = reference_from_curve(eq, _curve(rng.normal(0.0003, 0.012, 800)))
    assert set(ref) == set(REFERENCE)
    assert ref["worst_drawdown"] <= ref["dd_p95"] <= 0


# ------------------------------------------------------------------ 연구: 연도별 매도세
def test_historical_sell_tax_schedule():
    from quant_ai.research_lab import Costs
    d = pd.DatetimeIndex(["2015-06-01", "2019-06-03", "2022-01-03", "2025-03-01", "2026-02-02"], tz="UTC")
    assert list(Costs.historical().sell_tax_series(d)) == [30.0, 25.0, 23.0, 15.0, 20.0]
    assert list(Costs().sell_tax_series(d)) == [18.0] * 5


def test_cost_settings_from_env():
    st = Settings.from_env({"QUANT_SELL_TAX_BPS": "15", "QUANT_COMMISSION_BPS": "0.5"})
    assert st.costs.sell_tax_bps == 15 and st.costs.commission_bps == 0.5


# ------------------------------------------------------------------ 통합: 실제 형태의 KRX 데이터
@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("money")
    fake_marcap(d, n_codes=14, days=500)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a"))
    a.ingest_krx(d, years=0, top_n=10, end_year=2020)
    return a


def test_pipeline_order_sheet_from_cash(app):
    cfg = CoreSatelliteConfig(core_top_k=4, core_buffer_k=6)
    sheet = app.order_sheet({}, 50_000_000, use_ai=False, cfg=cfg)
    buys = [ln for ln in sheet.lines if ln.side == "BUY"]
    assert 1 <= len(buys) <= 4 and not any(ln.side == "SELL" for ln in sheet.lines)
    assert sum(ln.target_weight for ln in sheet.lines) <= 1.0 + 1e-9
    assert sheet.cash_after >= 0 and sheet.trend and sheet.as_of
    assert all(ln.role == "core" and "코어 #" in ln.reason for ln in buys)


def test_pipeline_order_sheet_sells_names_outside_buffer(app):
    cfg = CoreSatelliteConfig(core_top_k=4, core_buffer_k=6)
    first = app.order_sheet({}, 50_000_000, use_ai=False, cfg=cfg)
    held = {ln.symbol: ln.qty for ln in first.lines if ln.side == "BUY"}
    again = app.order_sheet(held, first.cash_after, use_ai=False, cfg=cfg)
    assert not [ln for ln in again.lines if ln.side == "SELL"]  # 방금 산 종목은 유지 (버퍼)
    bars, _, _ = app.market_data()
    loser = next(iter(sorted(set(bars) - set(held) - {"KOSPI"})))
    r = app.order_sheet({**held, loser: 1}, 0, use_ai=True, cfg=cfg)
    ln = next(x for x in r.lines if x.symbol == loser)
    assert ln.side in ("SELL", "HOLD")
    if ln.side == "SELL":
        assert ln.qty == 1 and ln.reason


def test_cli_orders_and_checkup(app, tmp_path, capsys, monkeypatch):
    from quant_ai.cli import main
    h = tmp_path / "h.csv"
    h.write_text("종목코드,수량\n", encoding="utf-8")
    out = tmp_path / "o.csv"
    main(["--db", app.settings.database_url, "orders", "--cash", "30000000", "--holdings", str(h), "--no-ai",
          "--out", str(out)])
    txt = capsys.readouterr().out
    assert "리밸런싱 주문표" in txt and "매수" in txt and out.read_text(encoding="utf-8-sig").startswith("순서")
    with pytest.raises(SystemExit) as e:
        main(["--db", app.settings.database_url, "checkup", "--mode", "paper", "--no-ic"])
    assert e.value.code == 0 and "전략 건강검진" in capsys.readouterr().out


def test_strategy_health_state_and_alert_on_change(app):
    from datetime import UTC, datetime, timedelta

    from quant_ai.data.db import session_scope
    from quant_ai.data.models import PortfolioSnapshot
    t0 = datetime(2024, 1, 2, 7, tzinfo=UTC)
    with session_scope(app.engine) as s:
        for i, v in enumerate(np.r_[np.linspace(1e7, 1.1e7, 60), np.linspace(1.1e7, 0.55e7, 30)]):
            s.add(PortfolioSnapshot(mode="hc-test", ts=t0 + timedelta(days=i), cash=float(v), equity=float(v),
                                    positions={}))
    n0 = len(app.notifier.sent)
    r = app.strategy_health("hc-test", with_ic=True)
    assert r["status"] == "critical" and r["mode"] == "hc-test"
    assert len(app.notifier.sent) == n0 + 1 and "critical" in app.notifier.sent[-1][1]
    app.strategy_health("hc-test", with_ic=False)  # 같은 상태 → 중복 알림 없음
    assert len(app.notifier.sent) == n0 + 1


def test_dashboard_order_sheet_api(app):
    from quant_ai.web.api import DashboardAPI
    api = DashboardAPI(app)
    r = api.order_sheet({"cash": "20000000", "holdings": "", "use_ai": False})
    assert r["lines"] and r["csv"].startswith("순서")
    with pytest.raises(ValueError):
        api.order_sheet({"cash": "abc"})
    h = api.strategy_health("paper")
    assert "status" in h

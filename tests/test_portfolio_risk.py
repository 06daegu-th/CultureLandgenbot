"""포트폴리오 리스크: VaR/ES · 베타 · 상관 군집 · 유동성 · 통화 · VaR 예산 · 주문 유동성 한도."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from quant_ai.trading.portfolio_risk import PortfolioRiskLimits, clusters, portfolio_risk, var_budget_scale


def _bars(seed=0, n=300):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n, tz="UTC")
    mkt = rng.normal(0.0003, 0.01, n)
    out = {}
    for s, (beta, idio, vol) in {"A": (1.0, 0.004, 1e5), "B": (1.0, 0.004, 1e5), "C": (0.2, 0.02, 1e5),
                                 "T": (0.5, 0.01, 50)}.items():
        r = beta * mkt + rng.normal(0, idio, n)
        c = 10000 * np.exp(np.cumsum(r))
        out[s] = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": vol}, index=idx)
    bench = pd.DataFrame({"close": 2500 * np.exp(np.cumsum(mkt))}, index=idx)
    return out, bench


def test_var_es_beta_and_clusters():
    bars, bench = _bars()
    r = portfolio_risk({"A": 0.4, "B": 0.4, "C": 0.1}, bars, bench, names={"A": "에이", "B": "비"})
    assert 0 < r["var95"] < r["es95"] < 0.1
    assert 0.6 < r["beta"] < 1.1 and r["stress_market_-10pct"] == pytest.approx(-0.1 * r["beta"], abs=1e-3)
    g = r["clusters"][0]
    assert set(g["symbols"]) == {"A", "B"} and g["weight"] == pytest.approx(0.8)
    assert any("같이 움직이는 묶음" in w for w in r["warnings"])  # 80% > 40%
    assert r["risk_contrib"][0]["symbol"] in ("A", "B")
    assert r["currency"] == {"USD": pytest.approx(0.9)}  # 숫자 코드가 아니면 해외로 간주


def test_liquidity_days_to_liquidate():
    bars, bench = _bars()
    r = portfolio_risk({"T": 0.1}, bars, bench, values={"T": 5_000_000})
    t = r["liquidity"][0]
    assert t["days_to_liquidate"] > 3 and any("청산에" in w for w in r["warnings"])


def test_empty_and_short_history():
    bars, _ = _bars(n=20)
    assert portfolio_risk({}, bars)["var95"] == 0.0
    assert portfolio_risk({"A": 0.5}, bars).get("insufficient")


def test_var_budget_scale():
    bars, _ = _bars()
    scale, var = var_budget_scale({"A": 1.0}, bars, PortfolioRiskLimits(max_var95=0.005))
    assert var > 0.005 and scale == pytest.approx(0.005 / var)
    assert var_budget_scale({"A": 0.1}, bars)[0] == 1.0


def test_clusters_union_find():
    corr = pd.DataFrame([[1, .9, .1], [.9, 1, .7], [.1, .7, 1]], index=list("xyz"), columns=list("xyz"))
    assert clusters(corr, 0.6) == [["x", "y", "z"]]
    assert sorted(map(len, clusters(corr, 0.8))) == [1, 2]


def test_risk_engine_caps_order_by_liquidity():
    from quant_ai.config import RiskLimits
    from quant_ai.trading.portfolio import Order, Portfolio, Side
    from quant_ai.trading.risk import RiskEngine
    eng = RiskEngine(RiskLimits(max_position_weight=1.0, max_order_value=1e12, max_adv_participation=0.05))
    eng.adv = {"A": 1_000_000}  # 하루 거래대금 100만원 → 5% = 5만원
    d = eng.check(Order("A", Side.BUY, 100), Portfolio(cash=10_000_000), {"A": 1000.0})
    assert d.approved and d.order.qty == 50 and any("유동성 한도" in r for r in d.reasons)


def test_pipeline_portfolio_risk_on_real_shaped_data(tmp_path):
    from datetime import timedelta

    from quant_ai.config import Mode, Settings
    from quant_ai.pipeline import QuantAI
    from quant_ai.strategy.core_satellite import CoreSatelliteConfig
    from quant_ai.trading.broker import MarketQuote
    from tests.test_marcap import fake_marcap
    fake_marcap(tmp_path, n_codes=12, days=420)
    app = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/r.db", artifacts_dir=tmp_path))
    app.ingest_krx(tmp_path, years=0, top_n=10, end_year=2020)
    bars, _, _ = app.market_data()
    t = max(bars.values(), key=len).index[-1]
    q = {s: MarketQuote(last=float(b["close"].iloc[-1])) for s, b in bars.items()}
    app.run_core_satellite(Mode.PAPER, as_of=t.to_pydatetime(), ts=t.to_pydatetime() + timedelta(hours=6), quotes=q,
                           cfg=CoreSatelliteConfig(core_top_k=4, core_buffer_k=6, use_ai=False))
    r = app.portfolio_risk("paper")
    assert r["n_positions"] >= 3 and r["var95"] > 0 and r["var95_krw"] > 0 and "beta" in r
    assert r["currency"] == {"KRW": pytest.approx(r["gross"], abs=1e-3)}

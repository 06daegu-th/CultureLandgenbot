"""코어-위성: 계획 로직, AI 거부권/긴급청산/위성, 가상 장부, Risk AI 규칙 발동 회귀 테스트."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_ai.analysts.analysts import RiskAnalyst
from quant_ai.analysts.base import MarketContext
from quant_ai.engines.features import technical_features
from quant_ai.strategy.core_satellite import CoreSatelliteConfig, build_plan

CFG = CoreSatelliteConfig(core_top_k=3, core_buffer_k=5, satellite_k=2, core_weight=0.8)
SCORES = pd.Series([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3], index=list("ABCDEFG"))


def test_first_rebalance_picks_top_k():
    p = build_plan(SCORES, [], True, CFG)
    assert p.core == ["A", "B", "C"] and p.core_rebalanced
    assert p.weights == pytest.approx({"A": 0.8 / 3, "B": 0.8 / 3, "C": 0.8 / 3})
    assert "유지 0 · 신규 3" in p.notes[0]


def test_buffer_keeps_existing_holdings():
    p = build_plan(SCORES, ["E", "A"], True, CFG)  # E 는 5위 → buffer(5) 안이라 유지
    assert p.core[:2] == ["E", "A"] and len(p.core) == 3


def test_veto_skips_new_entry_but_not_existing():
    p = build_plan(SCORES, [], True, CFG, vetoes={"A": "risk: 이벤트 임박"})
    assert "A" not in p.core and p.core == ["B", "C", "D"] and "A" in p.vetoed
    held = build_plan(SCORES, ["A"], True, CFG, vetoes={"A": "risk: 변동성"})
    assert "A" in held.core  # 이미 보유 → 거부권은 신규 편입만 막는다


def test_exit_removes_holding_between_rebalances():
    p = build_plan(SCORES, ["A", "B", "C"], False, CFG, exits={"B": "상장폐지"})
    assert "B" not in p.core and p.exits == {"B": "상장폐지"} and len(p.core) == 3


def test_satellite_takes_confident_ai_buys_outside_core():
    buys = [{"symbol": "A", "confidence": 90, "prob_up": 0.7},  # 코어와 중복 → 제외
            {"symbol": "F", "confidence": 70, "prob_up": 0.62},
            {"symbol": "G", "confidence": 50, "prob_up": 0.6}]  # 신뢰도 미달
    p = build_plan(SCORES, [], True, CFG, ai_buys=buys)
    assert [x["symbol"] for x in p.satellite] == ["F"]
    assert p.weights["F"] == pytest.approx(0.1) and sum(p.weights.values()) == pytest.approx(0.9)


def test_attribution_variants():
    core_only = build_plan(SCORES, [], True, CFG, vetoes={"A": "x"}, use_veto=False, use_satellite=False)
    assert core_only.core == ["A", "B", "C"] and sum(core_only.weights.values()) == pytest.approx(1.0)


# ------------------------------------------------------------------ 회귀: Risk AI 하드 규칙이 실제로 발동하는가
def test_risk_rules_are_reachable_on_real_shaped_data():
    """예전 정의(같은 구간으로 나누기)에서는 vol_ratio ≤ ~2.2, jump ≤ ~4.4 로 묶여 규칙이 영원히 발동하지 않았다."""
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2024-01-01", periods=200, tz="UTC")
    r = rng.normal(0, 0.01, 200)
    r[150] = -0.12  # 12σ 급락
    r[180:185] = [0.08, -0.09, 0.1, -0.07, 0.09]  # 변동성 급증
    c = 100 * np.exp(np.cumsum(r))
    bars = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e5}, index=idx)
    f = technical_features(bars)
    assert f["jump_sigma"].iloc[150] >= 6
    assert f["vol_ratio"].iloc[184] >= 3
    ra = RiskAnalyst()
    for i, why in ((150, "급변"), (184, "변동성")):
        ctx = MarketContext("X", "X", "KRX", idx[i].to_pydatetime(), 5,
                            price={k: float(v) for k, v in f.iloc[i].items() if pd.notna(v)},
                            regime={"regime": "bull_quiet"}, data_quality={"jump_sigma": float(f["jump_sigma"].iloc[i])})
        op = ra.analyze(ctx)
        assert op.veto and why in op.veto_reason


# ------------------------------------------------------------------ 통합: 실제 형태의 KRX 데이터로 사이클
@pytest.fixture(scope="module")
def krx_app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("cs")
    fake_marcap(d, n_codes=14, days=500)
    app = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a"))
    info = app.ingest_krx(d, years=0, top_n=10, end_year=2020)
    assert info["symbols"] >= 6 and info["months"] > 10  # 거래대금 미달 종목은 유니버스에서 제외됨
    return app


def test_core_satellite_cycle_and_attribution_books(krx_app):
    from quant_ai import ops
    from quant_ai.config import Mode
    from quant_ai.trading.broker import MarketQuote
    cfg = CoreSatelliteConfig(core_top_k=4, core_buffer_k=6, satellite_k=2, shortlist_k=8)
    bars, _, _ = krx_app.market_data()
    cal = max(bars.values(), key=len).index
    days = cal[-25:]
    for t in days:
        q = {s: MarketQuote(last=float(b.loc[t, "close"])) for s, b in bars.items() if t in b.index}
        r = krx_app.run_core_satellite(Mode.PAPER, as_of=t.to_pydatetime(), ts=t.to_pydatetime() + timedelta(hours=6),
                                       quotes=q, cfg=cfg)
    plan = ops.get_state(krx_app.engine, "cs-plan:paper")
    assert len(plan["core"]) == 4 and plan["rebalances"] >= 2  # 25거래일 → 20일 주기로 2회
    pf = krx_app.load_portfolio("paper")
    held = {s for s, p in pf.positions.items() if p.qty}
    assert set(plan["core"]) - held == set() or len(held) >= 3
    for book in ("attr-core", "attr-veto", "attr-full"):
        assert krx_app.load_portfolio(book).positions, book
    # 코어 매수는 AI 확률 최소치로 거부되지 않아야 함
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import OrderRecord
    with session_scope(krx_app.engine) as s:
        rej = [o.reason for o in s.query(OrderRecord).filter(OrderRecord.status == "rejected")]
    assert not any("CORE" in x and "최소" in x for x in rej)
    assert r["plan"].core == plan["core"]


def test_dashboard_core_satellite_api(krx_app):
    from quant_ai.web.api import DashboardAPI
    d = DashboardAPI(krx_app).core_satellite()
    assert d["plan"]["core"] and {"attr-core", "attr-veto", "attr-full"} <= set(d["books"])
    assert all(c in d["plan"]["names"] for c in d["plan"]["core"])


def test_live_capital_not_affected_by_books(krx_app):
    from quant_ai.config import Mode
    with pytest.raises(ValueError):
        krx_app.trade([], Mode.SHADOW, book="attr-core")  # 가상 장부는 PAPER 전용
    assert datetime.now(UTC)


def test_market_wide_risk_does_not_trigger_stock_exits():
    """위기 국면으로 전 종목을 청산하던 문제(실데이터 재생에서 발견) 회귀 방지."""
    ra = RiskAnalyst()
    now = datetime(2026, 7, 13, 6, tzinfo=UTC)
    crisis = ra.analyze(MarketContext("X", "X", "KRX", now, 5, price={}, regime={"regime": "crisis"}))
    assert crisis.veto and crisis.meta.get("veto_scope") == "market" and not crisis.meta.get("exit")
    fomc = {"ts": (now + timedelta(hours=10)).isoformat(), "name": "FOMC", "importance": 0.9, "symbol": None}
    assert ra.analyze(MarketContext("X", "X", "KRX", now, 5, price={}, regime={},
                                    upcoming_events=[fomc])).meta["veto_scope"] == "market"
    earn = {**fomc, "name": "X 실적", "symbol": "X"}
    assert ra.analyze(MarketContext("X", "X", "KRX", now, 5, price={}, regime={},
                                    upcoming_events=[earn])).meta["veto_scope"] == "stock"
    delist = ra.analyze(MarketContext("X", "X", "KRX", now, 5, price={}, regime={},
                                      disclosures=[{"title": "상장폐지 결정", "events": ["delisting"]}]))
    assert delist.meta["exit"] and delist.meta["veto_scope"] == "stock"

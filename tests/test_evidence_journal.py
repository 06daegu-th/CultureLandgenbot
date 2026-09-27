"""Evidence Chain · AI Journal · NO TRADE 사유 · 장후 리뷰 확장 · 데이터 품질(미래 시각·거래정지)."""

from dataclasses import replace
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from quant_ai.config import Mode, Settings


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    from quant_ai.pipeline import QuantAI
    from quant_ai.strategy.core_satellite import CoreSatelliteConfig
    from quant_ai.trading.broker import MarketQuote
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("ev")
    fake_marcap(d, n_codes=12, days=420)
    app = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/e.db", artifacts_dir=d / "a"))
    app.ingest_krx(d, years=0, top_n=10, end_year=2020)
    bars, _, _ = app.market_data()
    cal = max(bars.values(), key=len).index
    t = cal[-30]  # 결과가 확정될 수 있도록 과거 시점에서 판단
    q = {s: MarketQuote(last=float(b.loc[:t, "close"].iloc[-1])) for s, b in bars.items() if len(b.loc[:t])}
    r = app.run_core_satellite(Mode.PAPER, as_of=t.to_pydatetime(), ts=t.to_pydatetime() + timedelta(hours=6),
                               quotes=q, cfg=CoreSatelliteConfig(core_top_k=4, core_buffer_k=6, shortlist_k=8))
    rep = app.review((t + timedelta(days=14)).date())
    return app, r, rep


def test_evidence_chain_traces_decision_to_order_and_outcome(run):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import OrderRecord
    from quant_ai.review.evidence import evidence_chain
    app, r, _ = run
    with session_scope(app.engine) as s:
        o = s.query(OrderRecord).filter(OrderRecord.consensus_id.is_not(None), OrderRecord.mode == "paper").first()
        assert o is not None, "코어 주문에도 합의 ID 가 연결돼야 한다"
        ev = evidence_chain(s, o.consensus_id)
        assert evidence_chain(s, 10**9) is None
    stages = [x["stage"] for x in ev["chain"]]
    assert stages == ["news", "disclosure", "price", "market", "ai", "ensemble", "risk", "order", "outcome"]
    ai = next(x for x in ev["chain"] if x["stage"] == "ai")["items"]
    assert {a["analyst"] for a in ai} >= {"primary", "nvidia", "risk"} and all("provider" in a for a in ai)
    price = next(x for x in ev["chain"] if x["stage"] == "price")["items"]
    assert price.get("last_close") and price.get("last_bar")
    market = next(x for x in ev["chain"] if x["stage"] == "market")["items"]
    assert market["market"]["label"] in ("RISK ON", "RISK OFF", "NEUTRAL")
    orders = next(x for x in ev["chain"] if x["stage"] == "order")["items"]
    assert orders and orders[0]["client_order_id"] and orders[0]["ref_price"]
    assert next(x for x in ev["chain"] if x["stage"] == "outcome")["items"]["correct"] is not None


def test_ai_journal_why_expected_actual(run):
    from quant_ai.data.db import session_scope
    from quant_ai.review.evidence import ai_journal
    app, _, _ = run
    with session_scope(app.engine) as s:
        rows = ai_journal(s, limit=100)
        resolved = ai_journal(s, only_resolved=True)
    assert rows and all({"why", "expected", "actual", "correct", "cause"} <= set(x) for x in rows)
    assert resolved and all(x["actual"] is not None for x in resolved)
    wrong = [x for x in resolved if x["correct"] is False]
    assert all(x["cause"] for x in wrong)
    nt = [x for x in rows if x["action"] in ("NO_TRADE", "HOLD")]
    assert all(x["no_trade"] for x in nt)


def test_review_has_provider_calibration_and_no_trade_sections(run):
    _, _, rep = run
    s = rep.summary
    assert "providers" in s and "calibration" in s and "no_trade_by_reason" in s
    assert s["n_resolved"] > 0 and isinstance(rep.lessons, list)


def test_no_trade_codes():
    from quant_ai.analysts.base import Opinion
    from quant_ai.ensemble.engine import EnsembleEngine
    e = EnsembleEngine()
    veto = e.combine("X", [Opinion("primary", "X", 0.7, 0.8), Opinion("nvidia", "X", 0.7, 0.8),
                           Opinion("risk", "X", 0.4, 0.8, veto=True, veto_reason="이벤트")])
    assert veto.action == "NO_TRADE" and veto.no_trade_codes == ["veto"]
    few = e.combine("X", [Opinion("primary", "X", 0.7, 0.8)])
    assert few.no_trade_codes == ["few_responders"]
    weak = e.combine("X", [Opinion("primary", "X", 0.52, 0.5), Opinion("nvidia", "X", 0.51, 0.5)])
    assert weak.action == "HOLD" and weak.no_trade_codes == ["weak_signal"]


def test_quality_drops_future_rows_and_flags_halts():
    from quant_ai.data.quality import validate_bars
    idx = pd.bdate_range(end=pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1), periods=62)
    c = np.linspace(100, 110, len(idx))
    c[-4:] = c[-5]
    v = np.full(len(idx), 1000.0)
    v[-4:] = 0
    df = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": v}, index=idx)
    future = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
                          index=[pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=30)])
    out, rep = validate_bars(pd.concat([df, future]), "X")
    assert rep.dropped.get("미래 시각") == 1 and len(out) == len(df)
    assert any("거래정지 의심" in w for w in rep.warnings) and any("신규 편입 금지" in w for w in rep.warnings)


def test_risk_ai_vetoes_halted_stock():
    from datetime import UTC, datetime

    from quant_ai.analysts.analysts import RiskAnalyst
    from quant_ai.analysts.base import MarketContext
    ctx = MarketContext("X", "X", "KRX", datetime(2026, 9, 28, tzinfo=UTC), 5, price={}, regime={},
                        data_quality={"halt": "2026-09-25 거래량 0 · 가격 변화 없음"},
                        news=[{"title": "t", "sentiment": None, "importance": None}])
    op = RiskAnalyst().analyze(ctx)
    assert op.veto and "거래정지" in op.veto_reason


def test_dashboard_apis_for_new_views(run):
    from quant_ai.web.api import DashboardAPI
    app, _, _ = run
    api = DashboardAPI(app)
    d = api.dashboard()
    assert d["market"]["risk_label"] in ("RISK ON", "RISK OFF", "NEUTRAL") and d["market"]["components"]
    assert d["ai_feed"] and {"text", "tone", "id"} <= set(d["ai_feed"][0])
    assert "news_events" in d and "events" in d and "lessons" in d
    cid = d["ai_feed"][0]["id"]
    assert api.evidence(cid)["chain"] and api.evidence(10**9) == {"error": "not found"}
    j = api.journal(limit=20)
    assert j["rows"] and isinstance(j["no_trade"], list)
    assert "analysts" in api.calibration() and "rows" in api.ai_scoreboard()
    r = api.risk("paper")
    assert r.get("n_positions", 0) >= 1
    o = api.orders()
    assert o["rows"] and o["rows"][0]["client_order_id"] and "paper" in o["slippage"]
    sym = d["watchlist"][0]["symbol"]
    a = api.analysis(sym)
    assert len(a["checklist"]) == 4 and a["range"]["lower"] < a["range"]["last"] < a["range"]["upper"]

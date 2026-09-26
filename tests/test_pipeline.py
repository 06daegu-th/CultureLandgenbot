"""통합 테스트: 가상 데이터로 수집→학습→멀티AI 판단→Paper/Shadow→채점→복기→대시보드 API."""

from dataclasses import replace

import pytest

from quant_ai.backtest.backtester import Backtester, BacktestConfig
from quant_ai.config import Mode, Settings
from quant_ai.data.collectors.prices import SyntheticPriceSource


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.demo import run_demo
    from quant_ai.pipeline import QuantAI

    d = tmp_path_factory.mktemp("qa")
    settings = replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "artifacts")
    app = QuantAI(settings)
    run_demo(app, replay_days=8, verbose=False, years=3)
    return app


def test_backtest_runs_and_trains_only_on_past():
    src = SyntheticPriceSource(seed=5)
    bars = {s: src.fetch_bars(s, "2019-01-01", "2021-12-31") for s in ("A", "B", "C")}
    res = Backtester(BacktestConfig(min_train_dates=200, retrain_every=40)).run(bars)
    assert res.metrics["n_days"] > 100
    assert res.metrics["prediction"]["n"] > 0
    # 첫 예측일은 워밍업(학습 + horizon + embargo) 이후
    first = res.predictions["ts"].min()
    assert first >= sorted(next(iter(bars.values())).index)[200 + 5 + 1]


def test_decide_produces_consensus_for_all_symbols(app):
    decisions = app.decide(persist=False)
    assert len(decisions) == 9
    for d in decisions:
        assert d.signal.action in ("BUY", "SELL", "HOLD", "NO_TRADE")
        names = {c.analyst for c in d.signal.contributions}
        assert {"primary", "nvidia", "quant", "regime", "risk"} <= names


def test_ai_never_touches_broker_directly(app):
    # 애널리스트 객체는 브로커/주문 API 에 대한 참조가 없어야 한다
    from quant_ai.analysts.analysts import build_analysts
    for a in build_analysts(app.settings, None):
        assert not any("broker" in k.lower() or "execution" in k.lower() for k in vars(a))


def test_kill_switch_blocks_new_buys(app):
    app.set_kill_switch(True)
    try:
        decisions = app.decide(persist=False)
        for d in decisions:
            d.signal.action = "BUY"
            d.signal.confidence = 90
        fills = app.trade(decisions, Mode.PAPER)
        assert all(f.order.side.value == "sell" for f in fills)
        from sqlalchemy import select
        from quant_ai.data.db import session_scope
        from quant_ai.data.models import JournalEntry
        with session_scope(app.engine) as s:
            blocked = [j for j in s.scalars(select(JournalEntry).where(JournalEntry.kind == "order"))
                       if "킬스위치 ON" in (j.data or {}).get("reasons", [])]
        assert blocked, "킬스위치가 매수 주문을 거부한 기록이 있어야 함"
    finally:
        app.set_kill_switch(False)


def test_research_mode_cannot_trade(app):
    with pytest.raises(ValueError):
        app.trade([], Mode.RESEARCH)


def test_live_is_blocked_by_default(app):
    with pytest.raises(PermissionError):
        app.trade([], Mode.LIVE)


def test_review_and_scoreboard(app):
    report = app.review()
    assert "scoreboard" in report.summary
    assert any(r["analyst"] == "quant" for r in report.summary["scoreboard"])


def test_dashboard_api(app):
    from quant_ai.web.api import DashboardAPI

    api = DashboardAPI(app)
    d = api.dashboard()
    assert d["demo"] is True
    assert len(d["watchlist"]) == 9 and d["indices"]
    assert d["portfolios"]["paper"]["equity"] > 0
    sym = d["watchlist"][0]["symbol"]
    assert api.chart(sym)["bars"]
    a = api.analysis(sym)
    assert a["consensus"]["contributions"]
    assert api.reviews()

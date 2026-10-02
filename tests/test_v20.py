"""v20: 목표 달성 계획(확률) · 월 적립식 · (이후) Quant 모델 v2 · 상시 운영 · 알파 채점."""

import json
import threading
from dataclasses import replace
from datetime import UTC, date, datetime
from http.client import HTTPConnection

import pytest

from quant_ai import ops


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v20")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


@pytest.fixture(scope="module")
def server(app):
    from http.server import ThreadingHTTPServer

    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), None, {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _req(port, method, path, body=None):
    c = HTTPConnection("127.0.0.1", port, timeout=60)
    h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"} if body is not None else {}
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


# ------------------------------------------------------------------ 목표 확률
def test_goal_simulation_is_sane_and_monotonic():
    from quant_ai import goal
    dep = goal.simulate(5e6, 0, 1e8, 0.03, 0.0, years=30, n=200)
    assert dep["p_reach"] == 0.0  # 예금 3% · 적립 0 → 30년 안에 20배 불가
    sure = goal.simulate(5e6, 1e6, 1e8, 0.0, 0.0, years=10, n=50)
    assert sure["by_year"][7] == 1.0 and sure["by_year"][6] == 0.0  # 0% · 월 100만 → 95개월째 도달 (8년차)
    lo = goal.simulate(5e6, 3e5, 1e8, 0.076, 0.157, years=15, n=1500)["by_year"][9]
    hi = goal.simulate(5e6, 1e6, 1e8, 0.076, 0.157, years=15, n=1500)["by_year"][9]
    assert 0 <= lo < hi <= 1  # 적립이 많을수록 확률이 높다
    risky = goal.simulate(5e6, 5e5, 1e8, 0.076, 0.30, years=10, n=1500)
    assert risky["p_mdd30"] > goal.simulate(5e6, 5e5, 1e8, 0.076, 0.10, years=10, n=1500)["p_mdd30"]
    need = goal.required_monthly(5e6, 1e8, 10, 0.076, 0.157, 0.5)
    assert 400_000 <= need <= 650_000 and need % 10_000 == 0


def test_goal_plan_honesty_and_compare():
    from quant_ai import goal
    p = goal.plan(5_000_000, 500_000, 100_000_000, 10, "core")
    assert 0.3 < p["p_target"] < 0.7 and "10년 안에 목표에 닿을 확률" in p["headline"]
    assert any("35%" in h and "파산" in h for h in p["honest"])  # 적립 없이 20배는 연 35% — 정직하게
    assert {c["key"] for c in p["compare"]} == set(goal.PRESETS) and next(c for c in p["compare"] if c["key"] == "core")["p_target"] == p["p_target"]
    assert next(w for w in p["what_if"] if w["monthly"] == 500_000)["p_target"] == p["p_target"]
    assert p["need_monthly"]["p80"] >= p["need_monthly"]["p50"]
    with pytest.raises(ValueError):
        goal.plan(5e6, 0, 1e8, 10, "lottery")


# ------------------------------------------------------------------ 저장 · 진행률 · 월 적립
def test_goal_save_progress_and_dca_deposit(app):
    from quant_ai import goal
    g = goal.save(app, {"principal": "5,000,000", "monthly": 500000, "goal": 100000000, "target_years": 10,
                        "dca": {"on": True, "day": 5, "mode": "paper", "amount": 500000}})
    assert g["dca"]["on"] and g["start"]
    pr = goal.progress(app)
    assert pr["set"] and pr["goal"] == 100_000_000 and 0 <= pr["pct"] and "목표 1.00억 중" in pr["text"]
    with pytest.raises(ValueError):
        goal.save(app, {"dca": {"on": True, "day": 31}})
    # 적립일 전이면 안 하고, 적립일(거래일) 이후 처음 한 번만
    assert not goal.dca_due(goal.get(app), date(2026, 10, 2), True)
    assert not goal.dca_due(goal.get(app), date(2026, 10, 3), False)  # 휴장일(개천절)이면 다음 거래일로 미룸
    cash0 = app.load_portfolio("paper").cash
    r = goal.dca_run(app, datetime(2026, 10, 6, 1, tzinfo=UTC))  # 10/5(적립일)이 대체휴일이면 10/6 거래일에
    assert r["done"] and r["mode"] == "paper" and app.load_portfolio("paper").cash == cash0 + 500_000
    assert app.cashflows("paper")[-1]["amount"] == 500_000 and app.cashflows("paper")[-1]["memo"] == "월 적립"
    assert not goal.dca_run(app, datetime(2026, 10, 20, 1, tzinfo=UTC))["done"]  # 같은 달 두 번 안 함
    assert goal.dca_run(app, datetime(2026, 11, 5, 1, tzinfo=UTC))["done"]  # 다음 달은 다시
    with pytest.raises(ValueError, match="실계좌"):
        goal.deposit(app, "live", 1000)
    alerts = ops.get_state(app.engine, "goal_plan")
    assert alerts["dca"]["last"] == "2026-11-05"


def test_goal_routes_and_home(app, server):
    st, g = _req(server, "GET", "/api/goal?monthly=1000000")
    assert st == 200 and g["inputs"]["monthly"] == 1_000_000 and g["p_target"] > 0.9 and g["progress"]["set"]
    st, r = _req(server, "POST", "/api/goal", {"principal": 5000000, "monthly": 700000, "goal": 100000000, "target_years": 8})
    assert st == 200 and r["saved"]["monthly"] == 700000
    st, h = _req(server, "GET", "/api/home5")
    assert st == 200 and h["goal"]["set"]


# ------------------------------------------------------------------ 상시 운영
def test_ops_status_flags_stopped_scheduler_and_stale_data(app):
    from datetime import timedelta

    from quant_ai import center
    from quant_ai.recovery import heartbeat
    now = datetime.now(UTC)
    st = center.ops_status(app, now)
    keys = {x["key"] for x in st["issues"]}
    assert not st["running"] and {"scheduler", "data"} <= keys  # 심장박동 없음 · 시험 데이터는 2021년
    assert "install-service" in next(x for x in st["issues"] if x["key"] == "scheduler")["fix"]
    heartbeat(app.engine, now - timedelta(minutes=3))
    st = center.ops_status(app, now)
    assert st["running"] and "scheduler" not in {x["key"] for x in st["issues"]}
    t = center.today3(app, now=now)
    assert t["items"][0]["kind"] == "ops_data" and "거래일 밀렸습니다" in t["items"][0]["text"]  # 오늘 할 일 맨 위


def test_run_sh_service_status_update_commands():
    from pathlib import Path
    run = (Path(__file__).resolve().parents[1] / "run.sh").read_text()
    for cmd in ("install-service)", "uninstall-service)", "status)", "update)"):
        assert cmd in run
    assert "KeepAlive" in run and "Restart=always" in run and "git -C \"$ROOT\" pull --ff-only" in run
    assert "qa ops-status" in run


# ------------------------------------------------------------------ Quant v2 (순위 · 시장 대비 라벨)
def _panel(n_sym=30, days=320, seed=3):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=days, tz="UTC")
    mkt = rng.normal(0, 0.01, days)
    bars = {}
    for i in range(n_sym):
        r = mkt + rng.normal(0, 0.015, days)
        c = 10_000 * np.exp(np.cumsum(r))
        bars[f"{i:06d}"] = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e5 + i}, index=idx)
    return bars


def test_excess_dataset_ranks_and_label():
    from quant_ai.engines.features import MIN_CROSS, build_dataset, feature_columns
    df = build_dataset(_panel(), 5, target="excess")
    day = df.index.get_level_values(0).unique()[300]
    one = df.xs(day, level=0)
    assert abs(one["cs_ret_1"].mean()) < 0.02 and one["cs_ret_1"].between(-0.5, 0.5).all()  # 날짜마다 순위 −0.5~0.5
    assert one["label"].mean() == 0.5  # 절반은 그날 가운데보다 더 오른다
    assert (one["n_cross"] == 30).all() and MIN_CROSS <= 30
    fx = feature_columns(df, "excess")
    assert fx and all(c.startswith("cs_") for c in fx) and "cs_mom_12_1" in fx and "cs_regime_score" not in fx
    up = feature_columns(df, "up")
    assert "n_cross" not in up and not any(c.startswith("cs_") for c in up) and "mom_12_1" in up
    with pytest.raises(ValueError):
        build_dataset(_panel(3), 5, target="lottery")


def test_gbm_survives_empty_feature_columns():
    import numpy as np
    import pandas as pd

    from quant_ai.engines.prediction import Predictor
    idx = pd.MultiIndex.from_product([pd.date_range("2020", periods=300, tz="UTC"), list("abcdefghij")])
    X = pd.DataFrame({"a": np.random.default_rng(1).normal(size=3000), "b": np.nan}, index=idx)
    y = pd.Series((np.random.default_rng(2).random(3000) > 0.5).astype(float), index=idx)
    p = Predictor("gbm", target="excess").fit(X, y)
    assert p.target == "excess" and len(p.predict_proba(X)) == 3000


def test_quant_analyst_excess_wording_and_small_universe():
    from datetime import UTC, datetime

    import numpy as np
    import pandas as pd

    from quant_ai.analysts.analysts import QuantAnalyst
    from quant_ai.analysts.base import MarketContext
    from quant_ai.engines.features import build_dataset, feature_columns
    from quant_ai.engines.prediction import Predictor
    df = build_dataset(_panel(), 5, target="excess")
    tr = df.dropna(subset=["label"])
    model = Predictor("logistic", 5, target="excess").fit(tr[feature_columns(df, "excess")], tr["label"])
    row = df.xs("000001", level=1).iloc[-1]
    feats = {k: float(v) for k, v in row.items() if isinstance(v, (int, float, np.floating)) and not pd.isna(v)}
    ctx = MarketContext(symbol="000001", name="t", market="KOSPI", as_of=datetime(2020, 3, 1, tzinfo=UTC), horizon_days=5,
                        price=1.0, regime={}, news=[], disclosures=[], macro={}, upcoming_events=[], similar_past=[], features=feats)
    op = QuantAnalyst(model, "v1").analyze(ctx)
    assert "시장 대비 더 오를 확률" in op.summary and op.meta["target"] == "excess" and 0 < op.prob_up < 1
    ctx.features = {**feats, "n_cross": 3.0}
    assert QuantAnalyst(model, "v1").analyze(ctx).abstained  # 비교할 종목이 적으면 순위 모델은 기권


def test_backtest_excess_rebalances_every_horizon():
    from quant_ai.backtest.backtester import BacktestConfig, Backtester
    bars = _panel(n_sym=25, days=400)
    r = Backtester(BacktestConfig(min_train_dates=150, retrain_every=40, rank_k=5, use_regime=False)).run(bars)
    assert r.model is not None and r.model.target == "excess"
    buys = [t for t in r.journal.trades if getattr(t, "side", "") in ("BUY", "buy")] if hasattr(r.journal, "trades") else []
    assert r.metrics["prediction"]["n"] > 0 and len(r.equity) > 100
    held = {t.symbol for t in buys} if buys else set()
    assert len(held) <= 25


# ------------------------------------------------------------------ 기준선 (코어 vs 지수 ETF)
def test_etf_shadow_same_money_same_flows():
    import pandas as pd

    from quant_ai.baseline import ETF_TRADE, etf_shadow
    idx = pd.bdate_range("2026-01-05", periods=60)
    bench = pd.Series([100.0] * 30 + [110.0] * 30, index=idx)
    eq = pd.Series(1_000_000.0, index=idx)
    out = etf_shadow(eq, [{"date": str(idx[30].date()), "amount": 500_000}], bench)
    assert abs(out.iloc[0] - 1_000_000 * (1 - ETF_TRADE)) < 1
    assert abs(out.iloc[29] / out.iloc[0] - 1) < 0.001  # 가격이 그대로면 (보수 외) 그대로
    assert 1_598_000 < out.iloc[30] < 1_600_000  # 100만 × 1.1 + 입금 50만 − 수수료·보수


def test_baseline_compare_and_route(app, server):
    from quant_ai import baseline
    b = baseline.compare(app)
    assert b["research"]["core"]["cagr"] < b["research"]["index"]["cagr"] and "증거는 없다" in b["research"]["verdict"]
    assert b["live"]["status"] in ("insufficient", "ahead", "behind") and b["recommend"]["text"]
    st, r = _req(server, "GET", "/api/baseline")
    assert st == 200 and r["research"]["period"].startswith("2012")


def test_recommend_rule_is_fixed():
    from quant_ai.baseline import _recommend
    assert _recommend({"status": "behind", "t": -2.5})["level"] == "bad"
    assert _recommend({"status": "behind", "t": -1.0})["level"] == "warn"
    assert _recommend({"status": "ahead", "t": 0.5})["level"] == "good"
    assert _recommend(None)["level"] == "info"


# ------------------------------------------------------------------ 알파 채점 (AI 상태)
def test_ai_state_needs_alpha_for_green(app, monkeypatch):
    from quant_ai import aitrack, alphascore, center
    monkeypatch.setattr(aitrack, "report", lambda app_: {"trust": {"level": "CANDIDATE"}, "demotion": {}, "last": {}})
    monkeypatch.setattr(alphascore, "alpha_card", lambda app_, n=1000: {"n": 300, "hit_excess": 0.5, "base_excess": 0.5,
                                                                        "market_share": 0.08, "verdict": "종목 선택력은 우연과 구분 안 됨"})
    s = center.ai_state(app)
    assert s["key"] == "checking" and "시장 대비" in s["why"] and s["easy"]["alpha"]["text"].startswith("시장 대비로 채점하면 50%")
    monkeypatch.setattr(alphascore, "alpha_card", lambda app_, n=1000: {"n": 300, "hit_excess": 0.58, "base_excess": 0.5,
                                                                        "market_share": 0.0, "verdict": "종목 선택력 있음 (시장 대비 적중이 유의하게 높음)"})
    assert center.ai_state(app)["key"] == "verified"


def test_ops_status_flags_missing_news_and_sectors(app, monkeypatch):
    from datetime import timedelta

    from quant_ai import center
    from quant_ai.engines import sector
    from quant_ai.recovery import heartbeat
    now = datetime.now(UTC)
    heartbeat(app.engine, now - timedelta(minutes=1))
    st = center.ops_status(app, now)
    assert st["running"] and "news" in {x["key"] for x in st["issues"]}  # 돌고 있는데 뉴스 0건이면 경고
    assert "qa collect news" in next(x for x in st["issues"] if x["key"] == "news")["fix"]
    assert st["sector_coverage"] == 0.0
    monkeypatch.setattr(sector, "sector_map", lambda engine: {})
    assert "sector" not in {x["key"] for x in st["issues"]}  # 종목이 10개 미만이면 업종 경고는 생략 (시험 DB 5종목)


def test_home_assets_count_deposits_as_principal(app):
    from quant_ai import center
    h = center.home5(app)
    a = h["assets"]
    paid = sum(x["amount"] for x in app.cashflows("paper"))
    assert a["paid_in"] == round(paid) and paid > 0  # 위 월 적립 시험에서 입금됨
    assert abs(a["pnl"] - (a["equity"] - app.settings.initial_cash - paid)) <= 1  # 입금은 수익이 아니다
    assert isinstance(a["spark"], list)


def test_collect_sectors_command_and_run_sh_loads_5_years():
    from pathlib import Path

    cli = (Path(__file__).resolve().parents[1] / "src/quant_ai/cli.py").read_text()
    assert '"macro", "sectors"]' in cli and 'args.what == "sectors"' in cli
    run = (Path(__file__).resolve().parents[1] / "run.sh").read_text()
    assert "--years 5 --top 100" in run and "qa collect sectors" in run


def test_stock_tabs_and_chart_declutter_static():
    from pathlib import Path
    st = Path(__file__).resolve().parents[1] / "src/quant_ai/web/static"
    os_js, app_js, truth_js = ((st / f).read_text() for f in ("os.js", "app.js", "truth.js"))
    assert "const OS_TABS" in os_js and "function osTab(" in os_js and "osShow(t.dataset.jump)" in os_js
    assert "function declutterLines(" in app_js and "function declutterMarkers(" in app_js and "el._chart = chart" in app_js
    assert "chartLine(el," in truth_js and "createPriceLine" not in truth_js  # 모든 가격선은 정리 함수를 거친다
    assert "chartLine(el," in os_js
    hub = (st / "hub.js").read_text()
    assert "data-star=" in hub and "wl-recent" in hub  # 별표 안 한 최근 종목은 ☆

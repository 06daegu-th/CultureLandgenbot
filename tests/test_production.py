"""서비스 운영 수준 기능 테스트: 운영 상태, LLM 가드, 데이터 품질, 통계, KIS 브로커, 웹 보안."""

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops
from quant_ai.analysts.guard import GuardedLLM, estimate_cost, llm_usage_summary
from quant_ai.analysts.llm_clients import LLMClient, LLMError
from quant_ai.backtest.stats import bootstrap_sharpe_ci, deflated_sharpe, probabilistic_sharpe
from quant_ai.config import Settings
from quant_ai.data.db import init_db, make_engine
from quant_ai.data.quality import validate_bars
from quant_ai.engines.prediction import Predictor
from quant_ai.trading.broker import MarketQuote
from quant_ai.trading.kis import KISBroker, KISClient, KISError, krx_code, krx_tick, round_to_tick
from quant_ai.trading.portfolio import Order, Portfolio, Side


@pytest.fixture()
def engine(tmp_path):
    e = make_engine(f"sqlite:///{tmp_path}/t.db")
    init_db(e)
    return e


# ------------------------------------------------------------------ 운영 상태 / 잠금 / 알림
def test_kill_switch_is_shared_via_db(engine):
    assert not ops.kill_switch_on(engine)
    ops.set_kill_switch(engine, True, "테스트", by="pytest")
    assert ops.kill_switch_on(engine)
    assert ops.get_state(engine, "kill_switch")["by"] == "pytest"
    ops.set_kill_switch(engine, False)
    assert not ops.kill_switch_on(engine)


def test_trading_lock_prevents_concurrent_cycles(engine, tmp_path):
    with ops.trading_lock(engine, "trade-paper", tmp_path), pytest.raises(ops.LockBusy):
        with ops.trading_lock(engine, "trade-paper", tmp_path):
            pass
    with ops.trading_lock(engine, "trade-paper", tmp_path):  # 해제 후 다시 획득 가능
        pass


def test_record_job_logs_success_and_failure(engine):
    from sqlalchemy import select

    from quant_ai.data.db import session_scope
    from quant_ai.data.models import JobRun
    with ops.record_job(engine, "ok-job"):
        pass
    with pytest.raises(ValueError), ops.record_job(engine, "bad-job"):
        raise ValueError("boom")
    with session_scope(engine) as s:
        runs = {r.job: r for r in s.scalars(select(JobRun))}
    assert runs["ok-job"].ok and runs["bad-job"].ok is False and "boom" in runs["bad-job"].error


def test_notifier_levels_and_https_only(monkeypatch):
    n = ops.Notifier(discord="http://insecure", min_level="warn")
    posted = []
    monkeypatch.setattr(n, "_post", lambda url, payload: posted.append(url))
    n.send("info 는 무시", "info")
    n.send("경고", "warn")
    assert len(n.sent) == 1 and posted == []  # http 웹훅은 전송하지 않음
    n.discord = "https://discord.example/webhook"
    n.send("치명", "critical")
    assert posted == ["https://discord.example/webhook"]


# ------------------------------------------------------------------ LLM 가드
class CountingLLM(LLMClient):
    model = "claude-opus-5"
    provider = "anthropic"

    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def complete_json(self, system, user, schema):
        self.calls += 1
        self.last_usage = (10_000, 2_000)
        if self.fail:
            raise LLMError("API down")
        return {"prob_up": 0.6}


def test_guard_caches_identical_prompts(engine):
    inner = CountingLLM()
    g = GuardedLLM(inner, engine, "primary", daily_budget_usd=100)
    assert g.complete_json("s", "u", {}) == {"prob_up": 0.6}
    assert g.complete_json("s", "u", {}) == {"prob_up": 0.6}
    assert inner.calls == 1
    g.complete_json("s", "다른 입력", {})
    assert inner.calls == 2


def test_guard_enforces_daily_budget(engine):
    inner = CountingLLM()
    cost = estimate_cost("anthropic", "claude-opus-5", (10_000, 2_000))
    assert cost == pytest.approx(0.1)
    g = GuardedLLM(inner, engine, "primary", daily_budget_usd=0.15)
    g.complete_json("s", "1", {})
    g.complete_json("s", "2", {})  # 누적 0.2 > 0.15
    with pytest.raises(LLMError, match="예산"):
        g.complete_json("s", "3", {})
    usage = llm_usage_summary(engine)
    assert usage["today_cost"] == pytest.approx(0.2)
    assert usage["by_model"][0]["errors"] == 1


def test_guard_logs_failures_without_caching(engine):
    inner = CountingLLM(fail=True)
    g = GuardedLLM(inner, engine, "nvidia")
    for _ in range(2):
        with pytest.raises(LLMError):
            g.complete_json("s", "u", {})
    assert inner.calls == 2  # 실패는 캐시하지 않음


def test_prompts_warn_about_untrusted_text():
    from quant_ai.analysts.analysts import ROLE_PROMPTS
    for role in ROLE_PROMPTS.values():
        assert "신뢰할 수 없는 데이터" in role and "as_of 이후" in role


# ------------------------------------------------------------------ 데이터 품질
def test_validate_bars_drops_bad_rows_and_warns():
    idx = pd.bdate_range("2024-01-01", periods=80, tz="UTC")
    c = 100 * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, 80)))
    df = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e5}, index=idx)
    df.iloc[10, df.columns.get_loc("close")] = -5  # 음수 가격
    df.iloc[20, df.columns.get_loc("high")] = df.iloc[20]["low"] * 0.5  # OHLC 모순
    df.iloc[60:, :4] *= 2.0  # 액면병합 같은 급변
    df = pd.concat([df, df.iloc[[30]]])  # 중복
    clean, rep = validate_bars(df, "X")
    assert rep.dropped == {"중복 타임스탬프": 1, "가격 0 이하/결측": 1, "OHLC 모순": 1}
    assert any("급변" in w for w in rep.warnings)
    assert clean.index.is_unique and (clean[["open", "close"]] > 0).all().all()


# ------------------------------------------------------------------ 통계적 유의성
def test_psr_dsr_distinguish_luck_from_skill():
    rng = np.random.default_rng(1)
    skill = rng.normal(0.0015, 0.01, 1000)  # 연 Sharpe ~2.4
    noise = rng.normal(0.0, 0.01, 1000)
    assert probabilistic_sharpe(skill) > 0.95
    assert probabilistic_sharpe(noise) < 0.9
    # 시도 횟수가 많을수록 DSR 은 낮아진다
    assert deflated_sharpe(skill, 100) < deflated_sharpe(skill, 2) <= probabilistic_sharpe(skill) + 1e-9
    lo, hi = bootstrap_sharpe_ci(skill)
    assert lo < 2.4 < hi and lo > 0


def test_gate_rejects_unlucky_and_fragile_models():
    from quant_ai.registry.model_registry import PromotionGates, backtest_gate
    m = {"prediction": {"n": 1000, "accuracy": 0.55, "brier": 0.245, "base_rate": 0.5, "ic": 0.05, "ic_t": 3.0},
         "strategy": {"sharpe": 1.2, "max_drawdown": -0.1, "psr": 0.99}, "stress": {"sharpe": 0.5}, "dsr": 0.9}
    assert backtest_gate(m, None, PromotionGates()).passed
    bad = {**m, "strategy": {**m["strategy"], "psr": 0.6}, "stress": {"sharpe": -0.3}, "dsr": 0.2,
           "prediction": {**m["prediction"], "ic_t": 1.0}}
    fails = backtest_gate(bad, None, PromotionGates()).failures
    assert len(fails) == 4


# ------------------------------------------------------------------ 확률 보정
def test_calibration_shrinks_overconfident_model():
    rng = np.random.default_rng(3)
    n = 4000
    idx = pd.MultiIndex.from_arrays([pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC"), ["A"] * n])
    x = rng.normal(size=n)
    y = pd.Series((rng.random(n) < 0.5 + 0.05 * np.tanh(x)).astype(float), index=idx)  # 약한 신호
    X = pd.DataFrame({"f": x * 5, "noise": rng.normal(size=n)}, index=idx)
    raw = Predictor("gbm").fit(X, y, calibrate=False).predict_proba(X)
    cal = Predictor("gbm").fit(X, y, calibrate=True).predict_proba(X)
    assert np.std(cal) < np.std(raw)  # 과신이 줄어듦
    assert pd.Series(cal).corr(pd.Series(raw), method="spearman") > 0.99  # 순위는 보존


# ------------------------------------------------------------------ KIS 브로커 (가짜 서버)
class FakeKIS:
    def __init__(self, fill_ratio=1.0):
        self.calls = []
        self.fill_ratio = fill_ratio
        self.orders = {}

    def __call__(self, method, url, headers, params, body):
        path = url.split(":29443")[-1]
        self.calls.append((method, path, headers.get("tr_id"), body or params))
        if path == "/oauth2/tokenP":
            return {"access_token": "TOKEN", "expires_in": 86400}
        if path.endswith("inquire-price"):
            return {"rt_cd": "0", "output": {"stck_prpr": "71000"}}
        if path.endswith("inquire-asking-price-exp-ccn"):
            return {"rt_cd": "0", "output1": {"askp1": "71100", "bidp1": "71000", "askp_rsqn1": "500", "bidp_rsqn1": "700"}}
        if path.endswith("order-cash"):
            odno = f"{len(self.orders) + 1:010d}"
            self.orders[odno] = body
            return {"rt_cd": "0", "output": {"ODNO": odno, "KRX_FWDG_ORD_ORGNO": "91252", "ORD_TMD": "090000"}}
        if path.endswith("inquire-daily-ccld"):
            odno = params["ODNO"]
            q = int(self.orders[odno]["ORD_QTY"])
            filled = int(q * self.fill_ratio)
            cancelled = any(c[1].endswith("order-rvsecncl") for c in self.calls)
            return {"rt_cd": "0", "output1": [{"odno": odno, "tot_ccld_qty": str(filled), "avg_prvs": "71050",
                                              "rmn_qty": str(0 if cancelled else q - filled),
                                              "cncl_yn": "Y" if cancelled and filled < q else "N"}]}
        if path.endswith("order-rvsecncl"):
            return {"rt_cd": "0", "output": {}}
        if path.endswith("inquire-balance"):
            return {"rt_cd": "0", "output1": [{"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "70000"}],
                    "output2": [{"dnca_tot_amt": "900000", "prvs_rcdl_excc_amt": "850000"}],
                    "ctx_area_fk100": "", "ctx_area_nk100": ""}
        return {"rt_cd": "1", "msg_cd": "X", "msg1": "unknown"}


def kis(tmp_path, fake):
    return KISClient("APPKEY1234", "SECRET", "12345678-01", "demo", tmp_path / "tok.json", fake)


def test_kis_tick_and_code_helpers():
    assert krx_tick(1_500) == 1 and krx_tick(71_000) == 100 and krx_tick(600_000) == 1_000
    assert round_to_tick(71_049, Side.BUY) == 71_100 and round_to_tick(71_049, Side.SELL) == 71_000
    assert krx_code("005930.KS") == "005930" and krx_code("NVDA") is None


def test_kis_token_cached_with_private_permissions(tmp_path):
    fake = FakeKIS()
    c = kis(tmp_path, fake)
    c.token()
    c2 = kis(tmp_path, fake)  # 새 프로세스라고 가정
    c2.token()
    assert sum(1 for x in fake.calls if x[1] == "/oauth2/tokenP") == 1
    assert oct((tmp_path / "tok.json").stat().st_mode & 0o777) == "0o600"


def test_kis_buy_uses_protective_limit_and_demo_tr(tmp_path):
    fake = FakeKIS()
    b = KISBroker(Portfolio(cash=10_000_000), kis(tmp_path, fake), poll_s=0)
    q = b.live_quotes(["005930.KS"])["005930.KS"]
    assert (q.bid, q.ask, q.ask_qty) == (71000, 71100, 500)
    fill = b.submit(Order("005930.KS", Side.BUY, 5), q, datetime.now(UTC))
    order = next(c for c in fake.calls if c[1].endswith("order-cash"))
    assert order[2] == "VTTC0012U"  # 모의투자 매수 TR
    assert order[3]["ORD_DVSN"] == "00" and int(order[3]["ORD_UNPR"]) == 71_500  # 71100*1.005 → 호가단위 올림
    assert fill.qty == 5 and fill.price == 71050
    assert b.portfolio.qty("005930.KS") == 5


def test_kis_cancels_unfilled_remainder(tmp_path):
    fake = FakeKIS(fill_ratio=0.4)
    b = KISBroker(Portfolio(cash=10_000_000), kis(tmp_path, fake), fill_timeout_s=0.01, poll_s=0)
    fill = b.submit(Order("005930", Side.BUY, 10), MarketQuote(71000, 71000, 71100), datetime.now(UTC))
    assert fill.qty == 4
    cancel = next(c for c in fake.calls if c[1].endswith("order-rvsecncl"))
    assert cancel[2] == "VTTC0013U" and cancel[3]["RVSE_CNCL_DVSN_CD"] == "02" and cancel[3]["QTY_ALL_ORD_YN"] == "Y"


def test_kis_sync_uses_broker_as_source_of_truth(tmp_path):
    b = KISBroker(Portfolio(cash=1), kis(tmp_path, FakeKIS()))
    pf = b.sync_portfolio(["005930.KS", "NVDA"])
    assert pf.cash == 850_000 and pf.qty("005930.KS") == 10


def test_kis_rejects_foreign_symbols_and_errors(tmp_path):
    b = KISBroker(Portfolio(cash=1e7), kis(tmp_path, FakeKIS()))
    with pytest.raises(KISError):
        b.submit(Order("NVDA", Side.BUY, 1), MarketQuote(100), datetime.now(UTC))
    with pytest.raises(ValueError):
        KISClient("k", "s", "1234", "demo")


# ------------------------------------------------------------------ 매매 사이클: 영속 한도 · 강제 청산 · 소액 상한
@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.demo import run_demo
    from quant_ai.pipeline import QuantAI
    d = tmp_path_factory.mktemp("prod")
    st = replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a")
    a = QuantAI(st)
    run_demo(a, replay_days=6, verbose=False, years=3)
    return a


def test_order_count_persists_across_cycles(app):
    from quant_ai.config import Mode, RiskLimits
    app.settings = replace(app.settings, risk=replace(app.settings.risk, max_orders_per_day=1))
    try:
        ds = app.decide(persist=False)
        for d in ds:
            d.signal.action, d.signal.confidence = "BUY", 90
        now = datetime.now(UTC)
        app.trade(ds, Mode.SHADOW, ts=now)
        second = app.trade(ds, Mode.SHADOW, ts=now + timedelta(minutes=15))
        assert not [f for f in second if f.order.side is Side.BUY]  # 두 번째 사이클 매수는 한도로 거부
    finally:
        app.settings = replace(app.settings, risk=RiskLimits())


def _hold(app, mode, qty=100, at=None):
    """테스트용: 해당 모드 포트폴리오가 첫 종목을 qty 주 보유한 상태로 만든다."""
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import PortfolioSnapshot
    bars, _, _ = app.market_data()
    sym = sorted(bars)[0]
    px = float(bars[sym]["close"].iloc[-1])
    with session_scope(app.engine) as s:
        s.add(PortfolioSnapshot(mode=mode, ts=at or datetime.now(UTC) - timedelta(days=2), cash=5_000_000,
                                equity=5_000_000 + qty * px, positions={sym: {"qty": qty, "avg_price": px}}))
    return sym, px


def test_severe_risk_exits_held_positions(app):
    from quant_ai.analysts.base import Opinion
    from quant_ai.config import Mode
    sym, _ = _hold(app, "paper", at=datetime.now(UTC) + timedelta(days=30))
    held = [sym]
    ds = [d for d in app.decide(persist=False) if d.symbol == held[0]]
    ds[0].opinions.append(Opinion("risk", held[0], None, 0.5, veto=True, veto_reason="상장폐지 공시",
                                  meta={"exit": True, "exit_reason": "상장폐지 공시"}))
    fills = app.trade(ds, Mode.PAPER, ts=datetime.now(UTC) + timedelta(days=31))
    assert any(f.order.symbol == held[0] and f.order.side is Side.SELL for f in fills)
    assert app.load_portfolio("paper").qty(held[0]) == 0


def test_auto_kill_switch_on_large_daily_loss(app):
    from quant_ai.config import Mode
    ops.set_kill_switch(app.engine, False)
    ds = app.decide(persist=False)
    day = datetime.now(UTC) + timedelta(days=60)
    sym, px = _hold(app, "shadow", qty=1000, at=day - timedelta(days=1))  # 전일 스냅샷 = 일 시작 자산
    bars, _, _ = app.market_data()
    crash = {s: MarketQuote(last=float(b["close"].iloc[-1])) for s, b in bars.items()}
    crash[sym] = MarketQuote(last=px * 0.5)  # 보유 종목 -50% 폭락
    app.trade(ds, Mode.SHADOW, ts=day, quotes=crash)
    assert ops.kill_switch_on(app.engine)
    assert ops.get_state(app.engine, "kill_switch")["by"] == "auto"
    ops.set_kill_switch(app.engine, False)


def test_budget_ratio_caps_live_capital(app):
    pf = app.load_portfolio("paper")
    bars, _, _ = app.market_data()
    prices = {s: float(b["close"].iloc[-1]) for s, b in bars.items()}
    ds = app.decide(persist=False)
    for d in ds:
        d.signal.action, d.signal.confidence = "BUY", 90
    full = app.signals_from_decisions(ds, Portfolio(cash=1e7), prices, 1.0)
    capped = app.signals_from_decisions(ds, Portfolio(cash=1e7), prices, 0.1)
    assert max(s.target_weight for s in capped) == pytest.approx(0.1 * max(s.target_weight for s in full))
    assert pf  # fixture 사용


# ------------------------------------------------------------------ 웹 보안
@pytest.fixture(scope="module")
def server(app):
    from http.server import ThreadingHTTPServer

    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), None, {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def req(port, method, path, body=None, headers=None):
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    return r.status, r.read(), dict(r.getheaders())


def test_web_security_headers_and_health(server):
    status, body, h = req(server, "GET", "/api/health")
    assert status == 200 and json.loads(body)["ok"]
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["X-Frame-Options"] == "DENY"


def test_web_blocks_dns_rebinding(server):
    status, _, _ = req(server, "GET", "/api/dashboard", headers={"Host": "evil.example:8050"})
    assert status == 421


def test_web_killswitch_requires_json_and_same_origin(server, app):
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert req(server, "POST", "/api/killswitch", '{"on": true}', form)[0] == 415
    cross = {"Content-Type": "application/json", "Origin": "https://evil.example"}
    assert req(server, "POST", "/api/killswitch", '{"on": true}', cross)[0] == 403
    assert not app.kill_switch_on()
    ok = {"Content-Type": "application/json", "Origin": "http://127.0.0.1"}
    status, body, _ = req(server, "POST", "/api/killswitch", '{"on": true, "reason": "t"}', ok)
    assert status == 200 and json.loads(body)["kill_switch"] is True
    req(server, "POST", "/api/killswitch", '{"on": false}', ok)


def test_web_ops_endpoint(server):
    status, body, _ = req(server, "GET", "/api/ops")
    d = json.loads(body)
    assert status == 200 and {"jobs", "llm", "data_quality", "kill_switch"} <= set(d)


def test_static_path_traversal_blocked(server):
    assert req(server, "GET", "/../../pyproject.toml")[0] == 404
    assert req(server, "GET", "/%2e%2e/%2e%2e/pyproject.toml")[0] == 404

"""v13 체결·검증: KIS 호가 스트림 · 체결 시뮬레이터 · 실측 슬리피지 보정 · KIS 모의투자 검증 스위트 ·
매매 계획(사이징·진입 구간·무효화) 봉인 · 모델 노후 · 드리프트 KS."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

BOOK_MSG = "0|H0STASP0|001|" + "^".join(
    ["005930", "093000", "0"] + [str(70100 + 100 * i) for i in range(10)] + [str(70000 - 100 * i) for i in range(10)]
    + [str(100 * (i + 1)) for i in range(10)] + [str(120 * (i + 1)) for i in range(10)] + ["5500", "6600"] + ["0"] * 14)


def test_parse_orderbook_and_latest_book():
    import time

    from quant_ai.trading import kis_ws
    b = kis_ws.parse_book(BOOK_MSG)
    assert b["symbol"] == "005930" and b["asks"][0] == (70100.0, 100.0) and b["bids"][0] == (70000.0, 120.0)
    assert len(b["asks"]) == 10 and b["ask_total"] == 5500
    assert kis_ws.parse_book("0|H0STCNT0|001|x") is None
    kis_ws.BOOKS["005930"] = b | {"_t": time.time()}
    assert kis_ws.latest_book("005930.KS")["symbol"] == "005930"
    assert kis_ws.latest_book("005930", now=time.time() + 60) is None
    kis_ws.BOOKS.clear()


def test_exec_sim_walks_book_respects_limit_and_models_without_book():
    from quant_ai.trading import kis_ws
    from quant_ai.trading.exec_sim import simulate, walk_book
    b = kis_ws.parse_book(BOOK_MSG)
    q, avg, lv = walk_book("buy", 250, b)
    assert q == 250 and lv == 2 and avg == pytest.approx((100 * 70100 + 150 * 70200) / 250)
    q, _, _ = walk_book("buy", 1000, b, limit=70200)
    assert q == 300  # 70100·70200 두 단계만
    s = simulate("buy", 250, 70050, book=b, sigma=0.02)
    assert s.method == "book10" and s.qty == 250 and s.slippage_bps > s.parts["half_spread"] > 0
    m = simulate("buy", 100, 50000, adv=5e9, sigma=0.02)
    assert m.method == "model" and m.parts["half_spread"] >= 5  # 5만원대 호가단위 100원 = 20bp → 절반 10bp
    one = simulate("sell", 500, 50000, bid=49950, ask=50050, bid_qty=200, ask_qty=300, adv=5e9, sigma=0.02)
    assert one.method == "book1" and one.slippage_bps > one.parts["half_spread"]


def test_shadow_broker_fills_against_depth():
    from quant_ai.trading import kis_ws
    from quant_ai.trading.broker import MarketQuote, ShadowBroker
    from quant_ai.trading.portfolio import Order, Portfolio, Side
    b = kis_ws.parse_book(BOOK_MSG)
    br = ShadowBroker(Portfolio(cash=1e9), book_fn=lambda s: b)
    f = br.submit(Order("005930", Side.BUY, 250), MarketQuote(last=70050, sigma=0.02), datetime.now(UTC))
    assert f.qty == 250 and f.price > 70100 and br.last_sim["levels"] == 2
    part = br.submit(Order("005930", Side.BUY, 100_000, order_type="limit", limit_price=70200),
                     MarketQuote(last=70050), datetime.now(UTC))
    assert part.qty == 300 and "부분체결" in part.order.reason


def test_slippage_calibration_recovers_and_shrinks():
    from quant_ai.config import CostModelConfig
    from quant_ai.trading.slippage import apply, fit, parity
    rng = np.random.default_rng(2)
    obs = []
    for _ in range(300):
        x = rng.uniform(0.0, 0.01)
        obs.append({"slip": 0.0008 + 1.2 * x + rng.normal(0, 0.0003), "x": x})
    r = fit(obs, prior_bps=5, prior_coef=0.7)
    assert r["applied"] and r["raw"]["fixed_bps"] == pytest.approx(8, abs=1.5) and r["raw"]["impact_coef"] == pytest.approx(1.2, abs=0.15)
    assert 5 < r["fixed_bps"] < r["raw"]["fixed_bps"]  # 사전값 쪽으로 당겨짐
    assert r["fixed_ci95"][0] < r["raw"]["fixed_bps"] < r["fixed_ci95"][1]
    small = fit(obs[:20], 5, 0.7)
    assert not small["applied"] and small["verdict"].startswith("표본 부족")
    c = apply(CostModelConfig(), r)
    assert c.slippage_bps == r["fixed_bps"] and c.impact_coef == r["impact_coef"]
    assert apply(CostModelConfig(), small).slippage_bps == CostModelConfig().slippage_bps
    assert parity([{"real_bps": 12, "sim_bps": 6}, {"real_bps": 10, "sim_bps": 6}])["bias_bps"] == 5


class FakeKIS:
    def __init__(self, env="demo"):
        self.env, self.cano, self.prdt, self.app_key, self.app_secret, self.base = env, "12345678", "01", "k" * 20, "s" * 20, "http://x"
        self.orders, self.cancelled = [], []

    def token(self):
        return "t"

    def balance(self):
        from quant_ai.trading.portfolio import Position
        return 1_000_000.0, {"005930": Position(3, 70000)}

    def quote(self, code):
        from quant_ai.trading.broker import MarketQuote
        return MarketQuote(last=70000, bid=69900, ask=70000, bid_qty=500, ask_qty=400)

    def price(self, code):
        return 70000.0

    def order(self, code, side, qty, px):
        self.orders.append((code, side.value, qty, px))
        return {"odno": str(len(self.orders)), "orgno": "0001"}

    def order_status(self, odno, day=None):
        if odno in self.cancelled or self.orders[int(odno) - 1][3] < 69000:
            return {"filled": 0, "avg_price": 0.0, "remaining": 0 if odno in self.cancelled else 1, "cancelled": odno in self.cancelled}
        return {"filled": 1, "avg_price": 70010.0, "remaining": 0, "cancelled": False}

    def cancel(self, odno, orgno):
        self.cancelled.append(odno)


def test_kis_validation_suite_steps():
    from quant_ai.trading.kis_check import run
    c = FakeKIS()
    closed = run(c, market_open=False, db_positions={"005930": 3}, ws_key_fn=lambda: "k")
    st = {s["key"]: s for s in closed["steps"]}
    assert closed["ok"] and st["order"]["ok"] is None and "장외" in st["order"]["detail"]
    assert st["balance"]["mismatch"] == {} and st["quote"]["spread_bps"] > 0 and st["realtime"]["ok"]
    open_ = run(c, market_open=True, fill_test=True, db_positions={"005930": 5}, ws_key_fn=lambda: "k", sleep=lambda s: None)
    st = {s["key"]: s for s in open_["steps"]}
    assert open_["order_path_verified"] and open_["fill_path_verified"] and st["balance"]["mismatch"]["005930"]["system"] == 5
    assert c.cancelled and open_["parity"]["real_bps"] > 0
    real = run(FakeKIS("real"), market_open=True, ws_key_fn=lambda: "k")
    assert {s["key"]: s for s in real["steps"]}["order"]["ok"] is None
    bad = FakeKIS()
    bad.token = lambda: (_ for _ in ()).throw(RuntimeError("EGW00123 토큰 만료"))
    b = run(bad, ws_key_fn=lambda: "k")
    assert not b["ok"] and "토큰" in b["failed"] and b["steps"][-1]["detail"] == "앞 단계 실패로 건너뜀"


def _bars(n=120, seed=1, drift=0.001):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-03-02", periods=n, tz="UTC")
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, 0.02, n))), index=idx)
    return pd.DataFrame({"open": c * 0.999, "high": c * 1.012, "low": c * 0.988, "close": c, "volume": 1e6})


def test_trade_plan_sizing_entry_and_invalidation():
    from quant_ai.trading.trade_plan import check_invalidation, plan
    b = _bars()
    p = plan("X", b, 0.62, 5, cost_bps=30, max_weight=0.1)
    c = float(b["close"].iloc[-1])
    assert p["tradeable"] and 0 < p["sizing"]["weight"] <= 0.1 and p["net_expected"] > 0
    assert p["entry"]["low"] < c <= p["entry"]["high"] + 1e-9 <= p["entry"]["no_chase_above"] + 1e-9
    assert p["stop"] < c and p["stop_pct"] < 0 and {i["kind"] for i in p["invalidation"]} == {"price", "time", "thesis"}
    half = plan("X", b, 0.62, 5, 30, 0.1, event_mult=0.5, event_reason="실적 D-1", earnings_in_horizon=True)
    assert half["sizing"]["weight"] == pytest.approx(p["sizing"]["weight"] * 0.5) and any(i["kind"] == "event" for i in half["invalidation"])
    hold = plan("X", b, 0.62, 5, 30, 0.1, action="HOLD")
    assert not hold["tradeable"] and "참고용" in hold["note"] and hold["sizing"]["weight"] > 0
    flat = plan("X", b, 0.51, 5, 30)
    assert not flat["tradeable"] and flat["sizing"]["binding"] == "비용 후 기대수익 ≤ 0"
    after = b.iloc[-3:].copy()
    after["close"] = p["stop"] * 0.98
    assert check_invalidation(p, after)["status"] == "stopped"


def test_plan_is_sealed_in_ledger(tmp_path):
    from quant_ai.data.db import init_db, make_engine, session_scope
    from quant_ai.data.models import ConsensusRecord
    from quant_ai.review import ledger as LG
    e = make_engine(f"sqlite:///{tmp_path}/l.db")
    init_db(e)
    with session_scope(e) as s:
        r = ConsensusRecord(symbol="X", as_of=datetime(2026, 9, 1, tzinfo=UTC), action="BUY", prob_up=0.6, confidence=60,
                            conflict="low", payload={"horizon": 5, "plan": {"stop": 95.0, "entry": {"low": 99, "high": 101}}})
        LG.seal(r)
        s.add(r)
        s.flush()
        LG.anchor(s)
    with session_scope(e) as s:
        assert LG.verify(s)["ok"]
        r = s.scalars(__import__("sqlalchemy").select(ConsensusRecord)).first()
        r.payload = {**r.payload, "plan": {**r.payload["plan"], "stop": 90.0}}  # 결과를 보고 손절선을 고침
    with session_scope(e) as s:
        v = LG.verify(s)
        assert not v["ok"] and v["tampered"]


def test_model_decay_detects_fading_edge():
    from quant_ai.review.decay import analyze, cusum
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    rng = np.random.default_rng(4)
    good = [{"as_of": t0 + timedelta(days=i), "hit": bool(rng.random() < 0.62)} for i in range(150)]
    bad = [{"as_of": t0 + timedelta(days=150 + i), "hit": bool(rng.random() < 0.42)} for i in range(150)]
    r = analyze(good + bad, t0 - timedelta(days=5))
    assert r["status"] == "decaying" and r["cusum"]["alarm"] and r["drop"] > 0.1 and r["t"] < -2
    assert r["by_age"][0]["hit_rate"] > r["by_age"][-1]["hit_rate"]
    steady = [{"as_of": t0 + timedelta(days=i), "hit": bool(rng.random() < 0.6)} for i in range(300)]
    assert analyze(steady)["status"] in ("stable", "watch")
    assert analyze(steady[:10])["status"] == "insufficient"
    assert not cusum(np.ones(100), 0.6)["alarm"]


def test_drift_ks_confirms_psi():
    from quant_ai.engines.drift import ks
    rng = np.random.default_rng(0)
    d, p = ks(rng.normal(0, 1, 2000), rng.normal(0, 1, 300))
    assert p > 0.05
    d2, p2 = ks(rng.normal(0, 1, 2000), rng.normal(0.8, 1, 300))
    assert p2 < 1e-6 and d2 > d
    assert ks([1, 2], [3]) == (None, None)

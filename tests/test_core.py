from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from quant_ai.clock import KRX, US, Phase
from quant_ai.config import LIVE_CONFIRM_PHRASE, RiskLimits, Settings
from quant_ai.data.collectors.disclosures import parse_dart_list
from quant_ai.data.collectors.macro import parse_fred
from quant_ai.data.collectors.news import parse_feed
from quant_ai.data.collectors.prices import SyntheticPriceSource
from quant_ai.engines.features import build_dataset, forward_return, technical_features
from quant_ai.engines.news_intel import NewsAnalyzer, tag_symbols
from quant_ai.engines.regime import Regime, current_regime
from quant_ai.registry.model_registry import PromotionGates, backtest_gate, shadow_gate
from quant_ai.trading.broker import MarketQuote, PaperBroker, ShadowBroker
from quant_ai.trading.portfolio import CostModel, Order, Portfolio, Side
from quant_ai.trading.risk import RiskEngine


def bars(symbol="A", n=400):
    return SyntheticPriceSource(seed=3).fetch_bars(symbol, datetime(2020, 1, 1), datetime(2020, 1, 1) + pd.Timedelta(days=int(n * 1.45)))


# ------------------------------------------------------------------ 피처: 미래 정보 누수 없음
def test_features_do_not_look_ahead():
    b = bars()
    f1 = technical_features(b)
    b2 = b.copy()
    b2.iloc[300:, :] *= 3.0  # 미래를 크게 바꿔도
    f2 = technical_features(b2)
    pd.testing.assert_frame_equal(f1.iloc[:300], f2.iloc[:300])  # 과거 피처는 그대로


def test_forward_return_enters_next_open():
    b = bars()
    fr = forward_return(b, 5)
    t = 100
    expected = b["close"].iloc[t + 5] / b["open"].iloc[t + 1] - 1
    assert fr.iloc[t] == pytest.approx(expected)
    assert fr.iloc[-5:].isna().all()


def test_build_dataset_labels_and_panel():
    data = build_dataset({"A": bars("A"), "B": bars("B")}, horizon=5)
    assert set(data.index.get_level_values(1)) == {"A", "B"}
    lab = data.dropna(subset=["label"])
    assert ((lab["fwd_ret"] > 0) == (lab["label"] == 1)).all()


# ------------------------------------------------------------------ 국면
def test_regime_detects_crash():
    idx = pd.bdate_range("2020-01-01", periods=400, tz="UTC")
    up = np.linspace(100, 200, 300)
    crash = np.linspace(200, 120, 100) * (1 + 0.04 * np.sin(np.arange(100)))
    c = np.r_[up, crash]
    state = current_regime(pd.DataFrame({"close": c}, index=idx))
    assert state.regime in (Regime.CRISIS, Regime.BEAR_VOLATILE, Regime.BEAR_QUIET)
    assert state.exposure_multiplier < 0.5


# ------------------------------------------------------------------ 뉴스
def test_news_sentiment_and_tagging():
    a = NewsAnalyzer()
    assert a.analyze("삼성전자, 어닝서프라이즈에 급등").sentiment > 0.3
    bad = a.analyze("OO기업 횡령 혐의, 거래정지")
    assert bad.sentiment < -0.3 and "delisting" in bad.events
    assert tag_symbols("NVIDIA beats estimates", {"NVDA": ["NVDA", "NVIDIA"], "A": ["A"]}) == ["NVDA"]


# ------------------------------------------------------------------ 수집기 파서
def test_parse_rss_and_atom():
    rss = b"""<rss><channel><item><title>Hello</title><link>http://x/1</link><pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate></item></channel></rss>"""
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title><link href="http://x/2"/><updated>2024-01-01T00:00:00Z</updated></entry></feed>"""
    assert parse_feed(rss, "s")[0].url == "http://x/1"
    assert parse_feed(atom, "s")[0].published_at.year == 2024


def test_parse_dart_and_fred():
    rows = parse_dart_list({"status": "000", "list": [{"rcept_no": "2024", "corp_code": "1", "corp_name": "삼성전자",
                                                       "stock_code": "005930", "report_nm": "공급계약 체결 ", "rcept_dt": "20240102"}]})
    assert rows[0]["symbol"] == "005930" and rows[0]["filed_at"] == date(2024, 1, 2)
    assert parse_dart_list({"status": "013"}) == []
    obs = parse_fred({"observations": [{"date": "2024-01-02", "value": "4.1"}, {"date": "2024-01-03", "value": "."}]})
    assert obs == [(date(2024, 1, 2), 4.1)]


# ------------------------------------------------------------------ 시장 시간
def test_market_phases():
    kst_open = datetime(2024, 3, 4, 1, 0, tzinfo=UTC)  # 월 10:00 KST
    assert KRX.phase(kst_open) is Phase.OPEN
    assert US.phase(kst_open) is Phase.CLOSED
    saturday = datetime(2024, 3, 2, 3, 0, tzinfo=UTC)
    assert KRX.phase(saturday) is Phase.CLOSED


# ------------------------------------------------------------------ 브로커 / 비용
def test_paper_broker_costs_and_tax():
    pf = Portfolio(cash=1_000_000)
    br = PaperBroker(pf, CostModel())
    ts = datetime.now(UTC)
    br.submit(Order("A", Side.BUY, 10), MarketQuote(last=10_000), ts)
    assert pf.qty("A") == 10 and pf.cash < 900_000
    br.submit(Order("A", Side.SELL, 10), MarketQuote(last=10_000), ts)
    assert pf.qty("A") == 0
    assert pf.cash < 1_000_000  # 수수료 + 슬리피지 + 매도세로 손실
    with pytest.raises(ValueError):
        br.submit(Order("A", Side.SELL, 1), MarketQuote(last=10_000), ts)


def test_shadow_uses_quotes_and_partial_fill():
    pf = Portfolio(cash=10_000_000)
    br = ShadowBroker(pf)
    fill = br.submit(Order("A", Side.BUY, 100), MarketQuote(last=100, bid=99, ask=101, ask_qty=30),
                     datetime.now(UTC))
    assert fill.qty == 30 and fill.price > 101  # 매도1호가 + 슬리피지
    assert len(br.would_have_sent) == 1


# ------------------------------------------------------------------ 리스크 엔진
def _pf_with(qty=0, cash=10_000_000):
    pf = Portfolio(cash=cash)
    if qty:
        PaperBroker(pf).submit(Order("A", Side.BUY, qty), MarketQuote(last=1000), datetime.now(UTC))
    return pf


def test_risk_caps_position_weight():
    risk = RiskEngine(RiskLimits(max_position_weight=0.1))
    risk.start_day(date.today(), 10_000_000)
    d = risk.check(Order("A", Side.BUY, 5000, prob_up=0.7), _pf_with(), {"A": 1000})
    assert d.approved and d.order.qty == 1000  # 10% of 10M / 1000


def test_risk_blocks_buys_after_daily_loss_but_allows_sells():
    risk = RiskEngine(RiskLimits(max_daily_loss_pct=0.02))
    pf = _pf_with(qty=1000)
    risk.start_day(date.today(), 10_000_000)
    prices = {"A": 500}  # 50만원 손실 = -5%
    assert not risk.check(Order("A", Side.BUY, 10, prob_up=0.9), pf, prices).approved
    assert risk.check(Order("A", Side.SELL, 10), pf, prices).approved


def test_kill_switch_and_no_short_and_min_confidence():
    risk = RiskEngine(RiskLimits(min_confidence=0.55))
    risk.start_day(date.today(), 10_000_000)
    pf = _pf_with()
    assert not risk.check(Order("A", Side.SELL, 1), pf, {"A": 1000}).approved
    assert not risk.check(Order("A", Side.BUY, 1, prob_up=0.5), pf, {"A": 1000}).approved
    risk.kill_switch = True
    assert not risk.check(Order("A", Side.BUY, 1, prob_up=0.9), pf, {"A": 1000}).approved


def test_regime_multiplier_limits_gross_exposure():
    risk = RiskEngine(RiskLimits(max_position_weight=1.0, max_order_value=1e12))
    risk.start_day(date.today(), 10_000_000)
    d = risk.check(Order("A", Side.BUY, 10_000, prob_up=0.9), _pf_with(), {"A": 1000}, exposure_multiplier=0.25)
    assert d.order.qty == 2500


# ------------------------------------------------------------------ 설정 / Live 안전장치
def test_live_requires_all_gates():
    with pytest.raises(PermissionError):
        Settings.from_env({}).assert_live_allowed(champion_ready=True)
    env = {"QUANT_LIVE_ENABLED": "true", "QUANT_LIVE_CONFIRM": LIVE_CONFIRM_PHRASE}
    with pytest.raises(PermissionError):
        Settings.from_env(env).assert_live_allowed(champion_ready=False)
    Settings.from_env(env).assert_live_allowed(champion_ready=True)


# ------------------------------------------------------------------ 모델 게이트
def test_backtest_gate():
    good = {"prediction": {"n": 1000, "accuracy": 0.55, "brier": 0.245, "base_rate": 0.5, "ic": 0.05},
            "strategy": {"sharpe": 1.2, "max_drawdown": -0.1}}
    assert backtest_gate(good, None, PromotionGates()).passed
    bad = {**good, "prediction": {**good["prediction"], "ic": 0.0, "brier": 0.26}}
    res = backtest_gate(bad, None, PromotionGates())
    assert not res.passed and len(res.failures) == 2
    assert not backtest_gate(good, good, PromotionGates()).passed  # champion 대비 개선 없음


def test_shadow_gate():
    assert not shadow_gate({"days": 5, "accuracy": 0.6, "max_drawdown": -0.01}, PromotionGates()).passed
    assert shadow_gate({"days": 25, "accuracy": 0.55, "max_drawdown": -0.03}, PromotionGates()).passed


def test_holidays_loaded_from_file(tmp_path):
    import json

    from quant_ai.clock import load_holidays
    (tmp_path / "h.json").write_text(json.dumps({"KRX": ["2024-03-04"]}))
    cals = load_holidays(tmp_path / "h.json")
    monday_10am_kst = datetime(2024, 3, 4, 1, 0, tzinfo=UTC)
    assert cals["KRX"].phase(monday_10am_kst) is Phase.CLOSED
    assert KRX.phase(monday_10am_kst) is Phase.OPEN  # 원본은 변경되지 않음

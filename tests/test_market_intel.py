"""뉴스 이벤트 클러스터링 · 시장 상태 · 크로스에셋 · 일별 감성 중복 제거."""

import numpy as np
import pandas as pd
import pytest

from quant_ai.engines.market_intel import cluster_news, cross_asset, market_state, news_category


def test_same_story_many_articles_becomes_one_event():
    arts = [{"ts": f"2026-09-25T0{i}:00:00+00:00", "title": t, "sentiment": 0.8, "importance": 0.5, "source": src}
            for i, (t, src) in enumerate([
                ("삼성전자, 3분기 영업이익 컨센서스 상회 전망", "A"),
                ("[속보] 삼성전자 3분기 영업이익 컨센서스 상회 전망", "B"),
                ("삼성전자 3분기 영업이익, 컨센서스 상회 전망 (종합)", "C"),
                ("미 연준, 기준금리 동결…시장 예상 부합", "D")])]
    ev = cluster_news(arts)
    assert len(ev) == 2
    sam = next(e for e in ev if "삼성" in e["title"])
    assert sam["n_articles"] == 3 and sam["sources"] == ["A", "B", "C"] and sam["sentiment"] == pytest.approx(0.8)
    assert sam["category"] == "실적" and next(e for e in ev if "연준" in e["title"])["category"] == "정책"


def test_old_articles_are_separate_events():
    a = {"title": "카카오 신규 서비스 출시", "sentiment": 0.3}
    ev = cluster_news([{**a, "ts": "2026-09-01T00:00:00+00:00"}, {**a, "ts": "2026-09-10T00:00:00+00:00"}])
    assert len(ev) == 2 and news_category("국제유가 상승세 지속") == "원자재"


def _index(trend, vol, n=300, seed=0):
    rng = np.random.default_rng(seed)
    r = rng.normal(trend, vol, n)
    return pd.DataFrame({"close": 2500 * np.exp(np.cumsum(r))}, index=pd.bdate_range("2025-01-01", periods=n))


def test_market_state_risk_on_off():
    on = market_state(_index(0.002, 0.006))
    off = market_state(_index(-0.003, 0.02, seed=1))
    assert on["label"] == "RISK ON" and on["score"] >= 60 and on["type"].startswith("TREND")
    assert off["label"] == "RISK OFF" and off["score"] <= 40
    vix = pd.Series([35.0] * 10, index=pd.bdate_range("2026-01-01", periods=10))
    assert market_state(_index(0.002, 0.006), vix=vix)["score"] < on["score"]
    assert market_state(None)["insufficient"]


def test_cross_asset_finds_nasdaq_link_with_one_day_lag():
    idx = pd.bdate_range("2026-01-01", periods=120)
    rng = np.random.default_rng(3)
    nq_ret = rng.normal(0, 0.01, 120)
    nasdaq = pd.Series(15000 * np.exp(np.cumsum(nq_ret)), index=idx)
    stock_ret = np.r_[0, 0.8 * nq_ret[:-1]] + rng.normal(0, 0.003, 120)  # 다음 날 따라감
    stock = pd.Series(50000 * np.exp(np.cumsum(stock_ret)), index=idx)
    oil = pd.Series(70 * np.exp(np.cumsum(rng.normal(0, 0.01, 120))), index=idx)
    rows = cross_asset(stock, {"NASDAQCOM": nasdaq, "DCOILWTICO": oil})
    assert rows[0]["name"] == "NASDAQ" and rows[0]["corr"] > 0.8 and 0.6 < rows[0]["beta"] < 1.0
    assert abs(next(r for r in rows if r["name"] == "WTI 유가")["corr"]) < 0.3


def test_daily_sentiment_not_inflated_by_duplicates():
    from quant_ai.engines.news_intel import daily_sentiment
    base = {"published_at": pd.Timestamp("2026-09-25 01:00", tz="UTC"), "symbol": "X", "sentiment": 0.8,
            "importance": 1.0}
    one = pd.DataFrame([{**base, "title": "X사 대규모 수주 공시"}])
    dup = pd.DataFrame([{**base, "title": t} for t in ("X사 대규모 수주 공시", "[속보] X사 대규모 수주 공시",
                                                        "X사, 대규모 수주 공시 (종합)")])
    assert daily_sentiment(dup).iloc[-1]["X"] == pytest.approx(daily_sentiment(one).iloc[-1]["X"])

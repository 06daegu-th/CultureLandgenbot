"""종목 상세 (토스식): 다가오는 일정 D-day · 핵심 지표 · 애널리스트 · 실적 · 기업 정보 · 뉴스 — 네트워크 없이 가짜 소스로."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from quant_ai.data import fundamentals as fu

NOW = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)  # KST 12:00


def yf_nvda(_sym):
    ed = pd.DataFrame({"EPS Estimate": [0.95, 0.75, 0.60, 0.52], "Reported EPS": [float("nan"), 0.81, 0.68, 0.60],
                       "Surprise(%)": [float("nan"), 8.0, 13.3, 15.4]},
                      index=pd.DatetimeIndex(["2026-11-18 16:20", "2026-08-26 16:20", "2026-05-27 16:20",
                                              "2026-02-25 16:20"]).tz_localize("America/New_York"))
    q = pd.DataFrame({pd.Timestamp("2026-07-31"): [46.7e9, 28.4e9, 26.4e9, 1.08],
                      pd.Timestamp("2026-04-30"): [44.1e9, 21.6e9, 18.8e9, 0.76]},
                     index=["Total Revenue", "Operating Income", "Net Income", "Diluted EPS"])
    return {
        "info": {"longName": "NVIDIA Corporation", "sector": "Technology", "industry": "Semiconductors",
                 "fullTimeEmployees": 36000, "website": "https://www.nvidia.com", "longBusinessSummary": "GPUs.",
                 "currency": "USD", "currentPrice": 180.0, "marketCap": 4.4e12, "trailingPE": 51.2, "forwardPE": 30.1,
                 "priceToBook": 45.0, "trailingEps": 3.5, "dividendRate": 0.04, "dividendYield": 0.02, "beta": 2.1,
                 "fiftyTwoWeekHigh": 195.0, "fiftyTwoWeekLow": 95.0, "debtToEquity": 10.5, "returnOnEquity": 1.09,
                 "targetMeanPrice": 210.0, "targetHighPrice": 280.0, "targetLowPrice": 120.0,
                 "numberOfAnalystOpinions": 55, "recommendationKey": "strong_buy", "recommendationMean": 1.3,
                 "exDividendDate": int(datetime(2026, 9, 11, tzinfo=UTC).timestamp())},
        "calendar": {"Earnings Date": [date(2026, 11, 18)], "Earnings Average": 0.95, "Earnings Low": 0.9,
                     "Earnings High": 1.01, "Revenue Average": 54.6e9, "Ex-Dividend Date": date(2026, 9, 11),
                     "Dividend Date": date(2026, 10, 2)},
        "earnings_dates": ed, "quarterly": q,
        "recommendations": pd.DataFrame({"period": ["0m", "-1m"], "strongBuy": [12, 11], "buy": [40, 39],
                                         "hold": [6, 7], "sell": [1, 1], "strongSell": [0, 0]}),
        "news": [{"id": "x", "content": {"title": "Nvidia unveils new chip", "pubDate": "2026-09-27T12:00:00Z",
                                         "provider": {"displayName": "Reuters"},
                                         "canonicalUrl": {"url": "https://example.com/a"}}},
                 {"title": "Old-shape item", "publisher": "Yahoo", "link": "https://example.com/b",
                  "providerPublishTime": 1790000000}],
        "errors": [],
    }


def boom(*_a, **_k):
    raise RuntimeError("429 Too Many Requests")


def test_build_profile_normalizes_everything_for_a_us_stock(monkeypatch):
    monkeypatch.setattr(fu, "today_kst", lambda now=None: date(2026, 9, 28))
    p = fu.build_profile("NVDA", {"yfinance": yf_nvda, "nasdaq": boom})
    assert p["sources"] == ["Yahoo Finance"] and p["failed"] == ["nasdaq"] and "nasdaq" in p["errors"][0]
    st = p["stats"]
    assert st["market_cap"] == 4.4e12 and st["per"] == 51.2 and st["fwd_per"] == 30.1
    assert st["div_yield"] == pytest.approx(0.04 / 180)  # 버전마다 단위가 다른 dividendYield 대신 직접 계산
    assert st["debt_to_equity"] == pytest.approx(0.105)
    ev = {e["kind"]: e for e in p["events"]}
    assert ev["earnings"]["date"] == "2026-11-18" and "EPS 예상 0.95" in ev["earnings"]["detail"]
    assert "$54.60B" in ev["earnings"]["detail"] and ev["earnings"]["time"] == "장 마감 후"
    assert ev["div_pay"]["date"] == "2026-10-02" and ev["ex_div"]["date"] == "2026-09-11"
    assert [h["eps_actual"] for h in p["earnings_history"]] == [0.60, 0.68, 0.81]
    assert all(h["beat"] for h in p["earnings_history"])
    assert [q["period"] for q in p["quarterly"]] == ["2026-04", "2026-07"] and p["quarterly"][-1]["revenue"] == 46.7e9
    a = p["analyst"]
    assert a["target_mean"] == 210 and a["rating"] == "강력 매수" and a["distribution"]["buy"] == 40
    assert [n["title"] for n in p["news"]] == ["Nvidia unveils new chip", "Old-shape item"]
    assert p["news"][0]["source"] == "Reuters" and p["news"][0]["url"] == "https://example.com/a"
    assert p["company"]["employees"] == 36000


def test_d_day_is_recomputed_per_request_and_old_events_drop():
    evs = [{"kind": "earnings", "label": "실적 발표", "date": "2026-11-18"},
           {"kind": "ex_div", "label": "배당락일", "date": "2026-09-25"},
           {"kind": "div_pay", "label": "배당 지급일", "date": "2026-08-01"}]
    out = fu.with_d_day(evs, date(2026, 9, 28))
    assert [(e["kind"], e["d_label"], e["past"]) for e in out] == [("earnings", "D-51", False), ("ex_div", "D+3", True)]
    assert fu.with_d_day(evs, date(2026, 11, 18))[0]["d_label"] == "오늘"


def test_nasdaq_fallback_gives_earnings_date_when_yahoo_is_blocked(monkeypatch):
    monkeypatch.setattr(fu, "today_kst", lambda now=None: date(2026, 9, 28))
    nas = {"summary": {"MarketCap": {"label": "Market Cap", "value": "4,400,000,000,000"},
                       "PERatio": {"value": 51.2}, "FiftTwoWeekHighLow": {"value": "$195.00/$95.00"},
                       "Yield": {"value": "0.02%"}, "OneYrTarget": {"value": "$210.00"},
                       "Sector": {"value": "Technology"}},
           "earnings": {"announcement": "Earnings announcement* for NVDA: Nov 18, 2026",
                        "reportText": "NVIDIA is estimated to report earnings on 11/18/2026 after market close. "
                                      "The report will be for the fiscal Quarter ending Oct 2026."}}
    p = fu.build_profile("NVDA", {"yfinance": boom, "nasdaq": lambda s: nas})
    e = p["events"][0]
    assert (e["date"], e["time"], e["estimated"], e["detail"]) == ("2026-11-18", "장 마감 후", True, "Oct 2026 분기")
    assert p["stats"]["market_cap"] == 4.4e12 and p["stats"]["high52"] == 195 and p["stats"]["low52"] == 95
    assert p["stats"]["div_yield"] == pytest.approx(0.0002) and p["analyst"]["target_mean"] == 210
    assert p["sources"] == ["Nasdaq"] and "yfinance" in p["failed"]


def test_korean_stock_uses_naver_consensus_and_tries_kosdaq_suffix(monkeypatch):
    tried = []

    def yf_kr(sym):
        tried.append(sym)
        if sym.endswith(".KS"):
            raise RuntimeError("Yahoo: 결과 없음")
        return {"info": {"longName": "Alteogen", "sector": "Healthcare", "currency": "KRW"}, "errors": []}
    naver = {"stockName": "알테오젠", "totalInfos": [
        {"code": "lastClosePrice", "key": "전일", "value": "400,000"},
        {"code": "marketValue", "key": "시총", "value": "21조 3,456억"}, {"code": "per", "key": "PER", "value": "95.12배"},
        {"code": "pbr", "key": "PBR", "value": "30.1배"}, {"code": "dividendYieldRatio", "key": "배당수익률", "value": "0.10%"},
        {"code": "foreignRate", "key": "외인소진율", "value": "18.5%"}],
        "consensusInfo": {"recommMean": "4.10", "priceTargetMean": "520,000", "createDate": "2026-09-26"},
        "researches": [{"tit": "플랫폼 가치 재평가", "bnm": "OO증권", "wdt": "2026-09-20"}]}
    p = fu.build_profile("196170", {"yfinance": yf_kr, "naver": lambda c: naver})
    assert tried == ["196170.KS", "196170.KQ"] and p["yf_symbol"] == "196170.KQ"
    assert p["stats"]["market_cap"] == pytest.approx(21.3456e12) and p["stats"]["per"] == 95.12
    assert p["stats"]["div_yield"] == pytest.approx(0.001) and p["stats"]["foreign_rate"] == pytest.approx(0.185)
    assert [r["label"] for r in p["stats_text"]] == ["시총", "PER", "PBR", "배당수익률", "외인소진율"]  # 가격 행 제외
    a = p["analyst"]
    assert a["target_mean"] == 520000 and a["rating"] == "매수" and a["reports"][0]["broker"] == "OO증권"
    assert p["company"]["name"] == "알테오젠" and p["currency"] == "KRW"


def test_kr_legal_deadline_covers_every_quarter():
    assert fu.kr_report_deadline(date(2026, 9, 28))["date"] == date(2026, 11, 14)
    assert fu.kr_report_deadline(date(2026, 11, 20))["date"] == date(2027, 3, 31)  # 사업보고서 90일
    assert fu.kr_report_deadline(date(2026, 2, 1))["date"] == date(2026, 3, 31)
    assert fu.kr_report_deadline(date(2026, 4, 2))["date"] == date(2026, 5, 15)
    assert fu.kr_amount("423조 8,469억") == pytest.approx(423.8469e12) and fu.kr_amount("1,234억") == 1.234e11


@pytest.fixture()
def engine(tmp_path):
    from quant_ai.data.db import init_db, make_engine
    e = make_engine(f"sqlite:///{tmp_path}/p.db")
    init_db(e)
    return e


def test_stock_profile_caches_and_cools_down_failed_sources(engine):
    calls = {"yf": 0, "nas": 0}

    def yf(sym):
        calls["yf"] += 1
        return yf_nvda(sym)

    def nas(sym):
        calls["nas"] += 1
        raise RuntimeError("503")
    f = {"yfinance": yf, "nasdaq": nas}
    p = fu.stock_profile(engine, "NVDA", fetchers=f, now=NOW)
    assert [e["d_label"] for e in p["events"]] == ["D-4", "D-51"]  # 가까운 순, 14일 넘게 지난 배당락은 뺀다
    assert calls == {"yf": 1, "nas": 1}
    fu.stock_profile(engine, "NVDA", fetchers=f, now=NOW + timedelta(minutes=5))
    assert calls == {"yf": 1, "nas": 1}  # 캐시
    later = fu.stock_profile(engine, "NVDA", fetchers=f, now=NOW + timedelta(days=1))
    assert later["events"][1]["d_label"] == "D-50"  # D-day 는 매번 다시
    assert calls == {"yf": 2, "nas": 2}  # 실패가 섞인 결과는 30분 TTL


def test_stock_profile_keeps_last_good_result_when_everything_fails(engine):
    fu.stock_profile(engine, "NVDA", fetchers={"yfinance": yf_nvda, "nasdaq": yf_nvda}, now=NOW)
    p = fu.stock_profile(engine, "NVDA", fetchers={"yfinance": boom, "nasdaq": boom}, now=NOW + timedelta(hours=7))
    assert p.get("stale") and p["stats"]["per"] == 51.2 and p["errors"]
    none = fu.stock_profile(engine, "AAPL", fetchers={"yfinance": boom, "nasdaq": boom}, now=NOW)
    assert none["sources"] == [] and none["errors"] and none["events"] == []


def test_kr_profile_adds_disclosure_events_and_deadline(engine):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Disclosure
    with session_scope(engine) as s:
        s.add(Disclosure(source="DART", receipt_no="1", symbol="005930", title="기업설명회(IR) 개최(안내공시)",
                         filed_at=date(2026, 9, 24), url="https://dart.example/1"))
    naver = {"totalInfos": [{"code": "per", "key": "PER", "value": "12.1배"}]}
    p = fu.stock_profile(engine, "005930", fetchers={"yfinance": boom, "naver": lambda c: naver}, now=NOW)
    kinds = {e["kind"]: e for e in p["events"]}
    assert kinds["ir"]["past"] and kinds["ir"]["detail"].startswith("기업설명회")
    assert kinds["earnings"]["date"] == "2026-11-14" and kinds["earnings"]["estimated"]
    assert fu.next_events_text(p)[0].startswith("3분기 실적 공시 기한 2026-11-14 (D-47, 추정)")


# ------------------------------------------------------------------ API · 채팅 연결
@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("prof")
    fake_marcap(d, n_codes=6, days=300)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a",
                        max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def test_profile_api_uses_chart_price_for_upside_and_52w(app, monkeypatch):
    from quant_ai.web.api import DashboardAPI
    sym = next(iter(app.market_data()[0]))
    naver = {"totalInfos": [{"code": "per", "key": "PER", "value": "10.0배"}],
             "consensusInfo": {"recommMean": "3.9", "priceTargetMean": "1"}}
    monkeypatch.setattr(fu, "DEFAULT_FETCHERS", {"yfinance": boom, "naver": lambda c: naver, "nasdaq": boom})
    api = DashboardAPI(app)
    p = api.profile(sym)
    st = p["stats"]
    assert st["price"] > 0 and st["high52"] >= st["price"] >= st["low52"] and 0 <= st["pos52"] <= 1
    assert p["analyst"]["upside"] == pytest.approx(1 / st["price"] - 1) and p["analyst"]["rating"] == "매수"
    assert any(e["kind"] == "earnings" for e in p["events"])  # 국내: 법정 기한은 항상
    assert api.profile("../../etc")["error"] and api.profile("KOSPI").get("skipped") in (None, "지수")
    a = api.analysis(sym)
    assert a["last"] and a["chg_pct"] is not None and a["last_ts"]


def test_chat_stock_answer_leads_with_upcoming_events(app, monkeypatch):
    from quant_ai.assistant import reply
    monkeypatch.setattr(fu, "DEFAULT_FETCHERS", {"yfinance": yf_nvda, "nasdaq": boom, "naver": boom})
    from quant_ai.data import global_stocks as gs
    monkeypatch.setattr(gs, "DEFAULT_FETCHERS", (("fake", lambda s: _bars_df()),))
    r = reply(app, "엔비디아 어때?", "p1", client_factory=lambda st: (None, None))
    assert r["mode"] == "rules" and "📅 실적 발표" in r["answer"] and "EPS 예상 0.95" in r["answer"]
    assert "목표가" in r["answer"] and "PER 51.2배" in r["answer"]
    tool = r["tools_used"][0]
    assert tool["tool"] == "stock_overview"


def _bars_df():
    idx = pd.bdate_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=120)
    c = pd.Series(range(100, 220), index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1e6})


def test_home_orders_watchlist_and_lists_cached_earnings(app, monkeypatch):
    from quant_ai import ops
    from quant_ai.web.api import DashboardAPI
    syms = list(app.market_data()[0])
    ops.set_state(app.engine, "watch_symbols", {"symbols": [syms[-1]]})
    soon = (fu.today_kst() + timedelta(days=5)).isoformat()
    ops.set_state(app.engine, f"profile:{syms[0]}", {"at": datetime.now(UTC).isoformat(), "data": {
        "events": [{"kind": "earnings", "label": "실적 발표", "date": soon, "estimated": True}]}})
    d = DashboardAPI(app)._dashboard()
    first = d["watchlist"][0]
    assert first["tier"] in ("held", "core", "watched") and "_liq" not in first
    tiers = [w["tier"] for w in d["watchlist"]]
    order = {"held": 0, "core": 1, "watched": 2, "signal": 3, None: 4}
    assert [order[t] for t in tiers] == sorted(order[t] for t in tiers)  # 보유 → 코어 → 관심 → 신호 → 나머지
    ev = [e for e in d["events"] if e.get("symbol") == syms[0]]
    assert ev and ev[0]["d_label"] == "D-5" and "(예상)" in ev[0]["name"]
    assert d["indices"][0]["symbol"] == "KOSPI"

"""v17 (2): 내 투자 한도 · 부정어 뉴스 분석 · 뉴스 구조화(LLM JSON)·묶기 · 뉴스 보드/증시 지도 · 실적 이벤트(PEAD) 전진 기록 ·
미국 주문표(환전·세금) · 그날 재현 · 주간 일정 · 락업 공시 띠 · 화면 경로."""

import json
import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from http.client import HTTPConnection

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v17e")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def _syms(app):
    return sorted(s for s in app.market_data()[0] if s[:1].isdigit())


def _last_day(app):
    return pd.Timestamp(app.market_data()[0][_syms(app)[0]].index[-1]).to_pydatetime().replace(tzinfo=UTC)


def _news(app, rows):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import NewsArticle
    with session_scope(app.engine) as s:
        for i, (title, at, syms, src) in enumerate(rows):
            s.add(NewsArticle(source=src, url=f"https://example.com/{abs(hash(title))}/{i}", published_at=at, title=title, body="",
                              symbols=syms, sentiment=None, events=None, importance=0.6))


# ------------------------------------------------------------------ 내 투자 한도
def test_budget_plan_apply_and_total_loss_guard(app):
    from quant_ai import budget
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import PortfolioSnapshot
    from quant_ai.trading.guardian import check_total_loss
    p = budget.plan(10_000_000, 1_000_000)
    lim = p["limits"]
    assert lim["max_daily_loss_pct"] == 0.0125 and lim["max_position_weight"] == 0.10 and lim["max_var95"] == 0.02
    assert lim["live_small_capital"] == 1_000_000 and lim["max_order_value"] == 1_000_000
    assert lim["max_position_weight"] * 0.5 <= p["loss_pct"]  # 한 종목 반토막이 최대 손실 안
    assert any(x.startswith("QUANT_MAX_DAILY_LOSS_PCT=") for x in p["env_lines"])
    small = budget.plan(10_000_000, 200_000)["limits"]
    assert small["max_position_weight"] == 0.04 and small["max_daily_loss_pct"] == 0.005
    for bad in ((50_000, 1000), (1_000_000, 0), (1_000_000, 2_000_000)):
        with pytest.raises(ValueError):
            budget.plan(*bad)
    assert check_total_loss(app, "paper")["status"] == "ok"  # 미설정
    budget.save(app, {"principal": 10_000_000, "max_loss": 1_000_000})
    assert app.settings.risk.max_position_weight == 0.10 and app.settings.live_max_capital == 10_000_000
    base = app.settings.initial_cash
    with session_scope(app.engine) as s:
        s.add(PortfolioSnapshot(mode="paper", ts=datetime.now(UTC), equity=base * 0.91, cash=base * 0.91, positions={}))
    c = check_total_loss(app, "paper")
    assert c["status"] == "warn" and "한도" in c["detail"]  # 손실 9% / 한도 10% → 90%
    with session_scope(app.engine) as s:
        s.add(PortfolioSnapshot(mode="paper", ts=datetime.now(UTC) + timedelta(seconds=1), equity=base * 0.85, cash=base * 0.85, positions={}))
    assert check_total_loss(app, "paper")["status"] == "critical"
    ops.set_state(app.engine, budget.KEY, {})


# ------------------------------------------------------------------ 부정어 · 출처 신뢰도
def test_news_negation_and_source_weight():
    from quant_ai.engines.news_intel import NewsAnalyzer, source_weight
    na = NewsAnalyzer()
    plain = na.analyze("삼성전자 어닝 쇼크", "")
    neg = na.analyze("삼성전자, 사실상 어닝 쇼크는 아니다", "")
    assert plain.sentiment < 0 < neg.sentiment
    assert any("(부정)" in m for m in neg.matched)
    assert na.analyze("Apple did not miss estimates", "").sentiment > 0
    assert na.analyze("Apple misses estimates", "").sentiment < 0
    assert source_weight("연합뉴스", "https://www.yna.co.kr/x") > source_weight("커뮤니티", "https://gall.dcinside.com/x")


# ------------------------------------------------------------------ 뉴스 구조화 · 묶기 · 보드
def test_news_extract_llm_json_clusters_and_board(app):
    from quant_ai import board, news_llm
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import NewsArticle
    sym = _syms(app)[0]
    now = _last_day(app) + timedelta(hours=10)
    t0 = now - timedelta(hours=5)
    _news(app, [("A전자 3분기 영업이익 사상 최대 어닝 서프라이즈", t0, [sym], "연합뉴스"),
                ("A전자 3분기 영업이익 사상 최대 어닝 서프라이즈 기록", t0 + timedelta(minutes=30), [sym], "한국경제"),
                ("B화학 대규모 유상증자 결정 소식", t0 + timedelta(hours=1), [sym], "블로그")])

    class Fake:
        calls = 0

        def complete_json(self, system, text, schema):
            Fake.calls += 1
            assert "외부 데이터" in system and "[" in text
            ids = [int(x[1:x.index("]")]) for x in text.splitlines() if x.startswith("[")]
            return {"items": [{"id": i, "event": "earnings", "direction": 1, "confidence": 0.8, "summary": "실적이 예상보다 좋음",
                               "companies": ["A전자"], "rumor": False} for i in ids[:1]] + [{"id": "x"}]}

    r = news_llm.extract_pending(app, client=Fake(), now=now)
    assert r["rule"] == 3 and r["llm"] == 1 and r["llm_on"] and Fake.calls == 1
    with session_scope(app.engine) as s:
        arts = s.query(NewsArticle).order_by(NewsArticle.id).all()
        clusters = [a.cluster for a in arts]
        llm = [a for a in arts if a.extract.get("by") == "llm"]
        assert len(llm) == 1 and llm[0].sentiment == 0.8 and llm[0].extract["summary"] == "실적이 예상보다 좋음"
        assert all("source_weight" in a.extract for a in arts)
    assert clusters[0] == clusters[1] != clusters[2]  # 같은 소식 두 기사는 한 묶음
    b = board.news_board(app, days=3, now=now)
    assert b["n_articles"] == 3 and len(b["cards"]) == 2
    big = next(c for c in b["cards"] if c["n"] == 2)
    assert big["tone"] == "긍정" and big["event_ko"] == "실적" and set(big["sources"]) == {"연합뉴스", "한국경제"}
    assert big["symbols"][0]["symbol"] == sym and len(big["symbols"][0]["spark"]) == 20
    assert board.news_board(app, days=3, only="부정", now=now)["cards"] == [c for c in board.news_board(app, days=3, only="부정", now=now)["cards"] if c["tone"] == "부정"]
    assert news_llm.rule_extract("인수설 관측", "", 0.0, ["mna"])["rumor"]


def test_market_map_tiles_sectors_and_movers(app):
    from quant_ai import board
    m = board.market_map(app, now=_last_day(app))
    assert len(m["tiles"]) == len(_syms(app)) - 1 and m["n_stale"] == 1  # 먼저 거래가 끝난 종목은 지도에서 빠지고 따로 센다
    assert abs(sum(t["weight"] for t in m["tiles"]) - 1) < 0.01 and {t["date"] for t in m["tiles"]} == {m["date"]}
    assert m["breadth"]["up"] + m["breadth"]["down"] + m["breadth"]["flat"] == len(m["tiles"])
    assert m["gainers"][0]["chg"] >= m["losers"][0]["chg"] and m["sectors"]
    assert m["index"] is None or len(m["index"]["spark"]) == 60


# ------------------------------------------------------------------ 실적 이벤트 (PEAD) 전진 기록
def test_pead_forward_ledger_seal_and_scoring(app):
    from quant_ai import pead
    syms = _syms(app)
    idx = app.market_data()[0][syms[0]].index
    old = pd.Timestamp(idx[-80]).date()   # 결과(20거래일)가 이미 나온 발표
    new = pd.Timestamp(idx[-3]).date()    # 아직 결과 전
    ops.set_state(app.engine, f"krcons:{syms[0]}", {"surprises": [{"date": old.isoformat(), "eps_surprise_pct": 12.0},
                                                                  {"date": new.isoformat(), "surprise_pct": -9.0, "metric": "영업이익"}]})
    ops.set_state(app.engine, f"krcons:{syms[1]}", {"surprises": [{"date": old.isoformat(), "eps_surprise_pct": 1.0}]})  # 신호 없음
    early = datetime.combine(old - timedelta(days=1), datetime.min.time(), UTC)
    assert pead.scan(app, early)["added"] == 3
    assert pead.scan(app, early)["added"] == 0  # 중복 없음
    led = ops.get_state(app.engine, pead.KEY)["rows"]
    assert all(r["forward"] for r in led if r["date"] == old.isoformat())
    late = datetime.combine(new + timedelta(days=30), datetime.min.time(), UTC)
    ops.set_state(app.engine, f"krcons:{syms[3]}", {"surprises": [{"date": old.isoformat(), "eps_surprise_pct": -20.0}]})
    pead.scan(app, late)
    led = ops.get_state(app.engine, pead.KEY)["rows"]
    assert not next(r for r in led if r["symbol"] == syms[3])["forward"]  # 나중에 채운 것은 사후
    r = pead.report(app, late)
    assert r["forward"]["n"] == 1 and r["backfill"]["n"] == 1 and r["tampered"] == 0
    rec = next(x for x in r["recent"] if x["symbol"] == syms[0])
    assert rec["signed"] == round(rec["excess"], 4) and rec["intact"]
    assert "전진 기록 중 (1/30건)" == r["decision"] and len(r["pending"]) == 1
    # 봉인 뒤 수치를 고치면 드러난다
    led[0]["surprise"] = 99.0
    ops.set_state(app.engine, pead.KEY, {"rows": led})
    assert pead.report(app, late)["tampered"] == 1


# ------------------------------------------------------------------ 미국 주문표
def test_us_order_sheet_tax_fx_and_csv(app, monkeypatch):
    from quant_ai import usorder
    ix = pd.date_range("2026-01-01", periods=30, freq="B")
    fake = {s: pd.DataFrame({"close": np.full(30, px), "volume": 1e6}, index=ix) for s, px in (("AAPL", 200.0), ("MSFT", 400.0), ("NVDA", 100.0))}
    monkeypatch.setattr(app, "_all_bars", lambda: (fake, {}))
    t = usorder.after_tax(3_000_000, 0)
    assert t["tax_krw"] == round(500_000 * 0.22) and t["deduction_left"] == 2_500_000
    assert usorder.after_tax(1_000_000, 3_000_000)["tax_krw"] == 220_000  # 공제 이미 다 씀
    assert usorder.after_tax(-500_000, 3_000_000)["tax_krw"] == -110_000  # 손실은 상계
    r = usorder.sheet(app, holdings={"aapl": 10, "msft": 5}, cash_usd=1000, targets={"AAPL": 1, "NVDA": 1},
                      avg_cost={"MSFT": 300.0}, ytd_gain_krw=0)
    assert r["fx"] == 1350.0 and "기본값" in r["fx_source"]
    rows = {x["symbol"]: x for x in r["rows"]}
    assert rows["MSFT"]["side"] == "매도" and rows["MSFT"]["order_qty"] == -5 and rows["MSFT"]["gain_krw"] == 500 * 1350
    assert rows["NVDA"]["side"] == "매수" and rows["AAPL"]["target_qty"] * 200 <= 2000 + 2000 + 1000
    assert r["rows"][0]["symbol"] == "MSFT"  # 매도 먼저
    assert r["summary"]["tax"]["tax_krw"] == 0  # 67.5만원 이익 < 250만원 공제
    assert r["csv"].splitlines()[0].startswith("순서,종목") and "MSFT,매도,5" in r["csv"]
    r2 = usorder.sheet(app, holdings={}, cash_krw=2_700_000, targets={"NVDA": 1})
    assert r2["summary"]["need_usd"] > 0 and r2["summary"]["fx_cost_krw"] == round(r2["summary"]["need_usd"] * 1350 * 0.0025)
    assert "error" in usorder.sheet(app, targets={})
    r3 = usorder.sheet(app, holdings={"TSLA": 2}, cash_usd=500, targets={"TSLA": 1}, prices={"tsla": 250.0})  # 시세 없는 종목은 직접 입력 가격으로
    assert not r3["missing"] and r3["rows"][0]["price"] == 250.0 and r3["price_source"] == "직접 입력"
    assert usorder.sheet(app, holdings={"TSLA": 2}, targets={"TSLA": 1})["missing_hint"]


# ------------------------------------------------------------------ 그날 재현 · 주간 일정 · 락업
def test_replay_day_only_knows_that_day(app):
    from quant_ai import replay
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    sym = _syms(app)[0]
    d = pd.Timestamp(app.market_data()[0][sym].index[-10]).date()
    at = datetime.combine(d, datetime.min.time(), UTC) + timedelta(hours=8)
    _news(app, [("그날 뉴스 하나", at, [sym], "연합뉴스"), ("다음날 뉴스", at + timedelta(days=1), [sym], "연합뉴스")])
    with session_scope(app.engine) as s:
        s.add(ConsensusRecord(symbol=sym, as_of=at, action="BUY", prob_up=0.62, confidence=55.0, conflict="low", payload={},
                              realized_return=0.03, correct=True, created_at=at))
    r = replay.day(app, d)
    assert r["trading"] and r["breadth"]["n"] == len(_syms(app)) - 1  # 먼저 거래가 끝난 종목은 그날 봉이 없어 빠진다
    titles = [n["title"] for n in r["news"]]
    assert "그날 뉴스 하나" in titles and "다음날 뉴스" not in titles
    assert r["ai"][0]["later"]["correct"] and r["ai_hit_later"] == 1.0
    assert r["prev"] == (d - timedelta(days=1)).isoformat()
    sat = d + timedelta(days=(5 - d.weekday()) % 7 or 7)
    assert not replay.day(app, sat)["trading"]


def test_weekly_schedule_alert_and_lockup_strip(app, monkeypatch):
    from quant_ai import center, stock
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import AlertRecord, Disclosure
    sym = _syms(app)[0]
    mon = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)  # 월요일 10시 KST
    ops.set_state(app.engine, "event_calendar", {"events": [
        {"date": "2026-10-07", "kind": "earnings", "symbol": sym, "title": "3분기 실적", "market": "KR"},
        {"date": "2026-10-08", "kind": "fomc", "title": "FOMC", "market": "US", "importance": 0.9},
        {"date": "2026-11-30", "kind": "earnings", "symbol": sym, "title": "먼 일정", "market": "KR"}]})
    monkeypatch.setattr("quant_ai.alerts.focus_symbols", lambda a: {sym: {"보유"}})
    w = center.weekly_schedule(app, mon)
    titles = [e["title"] for e in w["rows"]]
    assert "3분기 실적" in titles and "FOMC" in titles and "먼 일정" not in titles
    assert center.weekly_alert(app, mon) == 1 and center.weekly_alert(app, mon + timedelta(hours=3)) == 0  # 주 1회
    assert center.weekly_alert(app, mon + timedelta(days=1)) == 0  # 월요일만
    with session_scope(app.engine) as s:
        assert s.query(AlertRecord).filter(AlertRecord.title.like("이번 주 일정%")).count() == 1
        s.add(Disclosure(source="DART", receipt_no="L1", symbol=sym, title="최대주주등소유주식변동신고서(의무보유 해제)",
                         filed_at=date(2026, 10, 1), url="https://dart.example/L1"))
    evs = stock.events(app, sym, mon)
    lk = [e for e in evs if e["kind"] == "lockup"]
    assert lk and "원문 확인" in lk[0]["title"] and lk[0]["d_day"] == -4


# ------------------------------------------------------------------ 화면 경로
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
    c = HTTPConnection("127.0.0.1", port, timeout=30)
    h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", "Host": f"127.0.0.1:{port}"} if body is not None else {}
    c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def test_v17_event_routes(server, app):
    for path in ("/api/pead", "/api/weekly", "/api/news-board?days=3", "/api/market-map", "/api/budget?principal=10000000&max_loss=1000000"):
        st, body = _req(server, "GET", path)
        assert st == 200, (path, body)
    d = pd.Timestamp(app.market_data()[0][_syms(app)[0]].index[-5]).date().isoformat()
    st, body = _req(server, "GET", f"/api/replay?date={d}")
    assert st == 200 and body["date"] == d
    assert _req(server, "GET", "/api/replay?date=2026-13-40")[0] == 400
    st, body = _req(server, "POST", "/api/us-sheet", {"holdings": "AAPL,1", "targets": "AAPL,1"})
    assert st == 200 and ("rows" in body)
    assert _req(server, "POST", "/api/us-sheet", {"holdings": {"AAPL": "x"}})[0] == 400


def test_v17_views_are_wired():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src/quant_ai/web/static"
    app_js, board_js = (root / "app.js").read_text(), (root / "board.js").read_text()
    for v, fn in (("pead", "viewPead"), ("usorder", "viewUSOrder"), ("replay", "viewReplay"), ("map", "viewMarketMap"), ("news", "viewNewsBoard")):
        assert f'S.view === "{v}"' in app_js and f'["{v}",' in app_js and f"function {fn}(" in board_js
    assert "weeklyCard(" in app_js and "board.js" in (root / "index.html").read_text() and "board.js" in (root / "sw.js").read_text()


# ------------------------------------------------------------------ AI 어시스턴트 v17 도구 · 링크 · 이어 묻기
def test_assistant_v17_routes_tools_links_and_rule_answers(app, monkeypatch):
    from datetime import date as _date

    from quant_ai import assistant
    from quant_ai.assistant import find_date, reply, route
    no_llm = lambda st: (None, None)  # noqa: E731
    today = _date(2026, 10, 2)
    assert find_date("2026-09-03 에 무슨 일?", today) == _date(2026, 9, 3)
    assert find_date("9월 3일 장 어땠어", today) == _date(2026, 9, 3)
    assert find_date("12월 25일", today) == _date(2025, 12, 25)  # 미래 → 작년
    assert find_date("어제 시장", today) == _date(2026, 10, 1)
    assert find_date("2026-13-01", today) is None and find_date("내일 일정", today) is None
    names = [n for n, _ in route(app, "DART 키 왜 안돼? 공시가 안 채워져")]
    assert names[0] == "keys_status" or "keys_status" in names
    assert "ai_trust" in [n for n, _ in route(app, "AI 믿어도 돼?")]
    assert {"why_no_trade", "budget", "weekly_schedule", "market_map"} <= {n for n, _ in route(app, "왜 안 샀어? 내 투자 한도랑 이번 주 일정, 많이 오른 종목")}
    r = route(app, "2020-12-01 에 무슨 일 있었어? 뉴스도")
    assert ("replay_day", {"date": "2020-12-01"}) in r and "news_board" not in [n for n, _ in r]

    monkeypatch.setenv("DART_API_KEY", "")
    r = reply(app, "DART 키 왜 안돼?", "k1", client_factory=no_llm)
    assert "데이터 키 진단" in r["answer"] and "붙여넣지 마세요" in r["answer"]
    assert {"label": "데이터 건강 · 키 진단", "href": "#datahealth"} in r["links"]
    r = reply(app, "AI 믿어도 돼?", "k2", client_factory=no_llm)
    assert "AI 믿어도 되나" in r["answer"] and "실수 방지" in r["answer"] and any(x["href"] == "#scorecard" for x in r["links"])
    assert r["followups"]
    r = reply(app, "오늘 많이 오른 종목은?", "k3", client_factory=no_llm)
    assert "증시 지도" in r["answer"] and "▲" in r["answer"]
    r = reply(app, "내 투자 한도 알려줘", "k4", client_factory=no_llm)
    assert "내 투자 한도" in r["answer"] and "#budget" in [x["href"] for x in r["links"]]
    r = reply(app, "실적 이벤트 전략 결과", "k5", client_factory=no_llm)
    assert "PEAD" in r["answer"]
    d = pd.Timestamp(app.market_data()[0][_syms(app)[0]].index[-10]).date()
    r = reply(app, f"{d.isoformat()} 그날 시장 어땠어?", "k6", client_factory=no_llm)
    assert f"{d.isoformat()}" in r["answer"] and "재현" in r["answer"] and {"label": "그날 재현", "href": f"#replay/{d.isoformat()}"} in r["links"]
    # 종목을 물으면 AI 성적도 함께 조회되고, 종목 페이지 링크와 '그 종목 뉴스/왜 안 샀나' 이어 묻기가 붙는다
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Instrument
    sym = _syms(app)[0]
    with session_scope(app.engine) as s:
        name = s.query(Instrument).filter_by(symbol=sym).one().name
    r = reply(app, f"{name} 뉴스 어때?", "k7", client_factory=no_llm)
    tools = [u["tool"] for u in r["tools_used"]]
    assert tools[0] == "stock_overview" and "ai_trust" in tools and any(x["href"] == f"#analysis/{sym}" for x in r["links"])
    assert next(u for u in r["tools_used"] if u["tool"] == "news_board")["args"] == {"symbol": sym}
    assert r["answer"].index(f"### {name}") < r["answer"].index("AI 믿어도 되나")
    assert not any("뉴스" in q for q in r["followups"])
    hist = assistant.history(app.engine, "k2")
    assert hist[-1]["links"] and hist[-1]["followups"]


def test_assistant_llm_can_call_v17_tools(app):
    from quant_ai.assistant import reply

    class Fake:
        model = "fake-model"
        n = 0

        def chat(self, messages, tools=None, **kw):
            Fake.n += 1
            if Fake.n == 1:
                names = {t["function"]["name"] for t in tools}
                assert {"ai_trust", "keys_status", "news_board", "market_map", "budget", "event_strategy", "why_no_trade",
                        "weekly_schedule", "replay_day"} <= names
                return {"choices": [{"message": {"content": "", "tool_calls": [
                    {"id": "c1", "function": {"name": "weekly_schedule", "arguments": "{}"}},
                    {"id": "c2", "function": {"name": "replay_day", "arguments": '{"date": "nope"}'}}]}}]}
            tool_msgs = [m for m in messages if m["role"] == "tool"]
            assert "rows" in tool_msgs[0]["content"] and "YYYY-MM-DD" in tool_msgs[1]["content"]
            return {"choices": [{"message": {"content": "이번 주 일정 정리 (자세히: #calendar)"}}]}

    import quant_ai.assistant as A
    orig = A._chat_call
    A._chat_call = lambda client, msgs, tools: client.chat(msgs, tools)
    try:
        r = reply(app, "음 뭐가 있지", "l1", client_factory=lambda st: (Fake(), "claude"))
    finally:
        A._chat_call = orig
    assert r["mode"] == "llm" and "#calendar" in r["answer"]
    assert {"label": "일정 (D-Day)", "href": "#calendar"} in r["links"]
    assert [u["ok"] for u in r["tools_used"]] == [True, False]


# ------------------------------------------------------------------ 차트 급등락·AI 신호 변화 · 상황별 슬리피지 · 홈 한 줄
def test_chart_big_moves_and_ai_signal_changes(app):
    from quant_ai import stockplus
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    ix = pd.date_range("2026-01-01", periods=120, freq="B", tz="UTC")
    rng = np.random.default_rng(1)
    c = pd.Series(10000 * np.cumprod(1 + rng.normal(0, 0.01, 120)), index=ix)
    c.iloc[80:] *= 1.12   # 하루 +12%
    c.iloc[100:] *= 0.94  # 하루 −6%
    mv = stockplus.big_moves(c)
    assert [m["date"] for m in mv] == [str(ix[80].date()), str(ix[100].date())] and mv[0]["chg"] > 0.1 and "급락" in mv[1]["title"]
    sym = _syms(app)[1]
    t0 = datetime(2026, 3, 2, tzinfo=UTC)
    with session_scope(app.engine) as s:
        for i, act in enumerate(["HOLD", "HOLD", "BUY", "BUY", "SELL"]):
            s.add(ConsensusRecord(symbol=sym, as_of=t0 + timedelta(days=i), action=act, prob_up=0.5, confidence=50.0, conflict="low", payload={}))
    ch = stockplus.ai_changes(app, sym, t0 - timedelta(days=1))
    assert [(x["from"], x["to"]) for x in ch] == [("HOLD", "BUY"), ("BUY", "SELL")] and ch[0]["date"] == "2026-03-04"
    ov = stockplus.overlay(app, sym)  # 차트 표시 목록에 AI 신호 변화가 들어간다 (일봉 기간 밖이면 빠짐)
    assert all({"date", "kind", "title"} <= set(m) for m in ov["marks"])


def test_slippage_segments_low_liquidity_big_move_vi(app):
    from quant_ai import execreport
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import OrderRecord
    ix = pd.date_range("2026-01-01", periods=40, freq="B", tz="UTC")
    close = np.full(40, 1000.0)
    close[30] = 1070.0   # +7% 급등 날
    close[35] = close[34]
    hi, lo = close * 1.01, close * 0.99
    hi[35] = close[34] * 1.12  # 장중 +12% (VI 근사)
    vol = np.full(40, 1e7)  # 1000원 × 1천만주 = 100억 (충분)
    liq = pd.DataFrame({"close": close, "high": hi, "low": lo, "volume": vol}, index=ix)
    thin = liq.assign(volume=1e5)  # 1억 → 저유동
    assert execreport._segment(liq, ix[30] + timedelta(hours=1)) == "big_move"
    assert execreport._segment(liq, ix[35] + timedelta(hours=1)) == "vi"
    assert execreport._segment(liq, ix[25] + timedelta(hours=1)) == "normal"
    assert execreport._segment(thin, ix[25] + timedelta(hours=1)) == "low_liq"
    assert execreport._segment(None, ix[25]) == "normal"
    sym = _syms(app)[0]
    with session_scope(app.engine) as s:
        for i in range(3):
            s.add(OrderRecord(mode="live", created_at=datetime.now(UTC) - timedelta(days=1, minutes=i), symbol=sym, side="buy", qty=10,
                              order_type="limit", status="filled", ref_price=1000.0, avg_price=1000.0 + 2 * (i + 1), filled_qty=10))
    r = execreport.costs(app)
    live = next(x for x in r["rows"] if x["mode"] == "live")
    segs = live["segments"]
    assert sum(g["n"] for g in segs) == 3 and all({"key", "label", "mean_bps", "enough"} <= set(g) for g in segs)
    assert not any(g["enough"] for g in segs)


def test_home_oneline_and_route(app, server):
    from quant_ai import center
    o = center.oneline(app)
    keys = [x["key"] for x in o["items"]]
    assert keys == ["market", "event", "risk", "ai"] and all(x["link"].startswith("#") for x in o["items"])
    assert o["items"][3]["level"] in ("bad", "warn", "ok") and o["level"] in ("ok", "warn", "bad")
    ops.set_state(app.engine, "ai_demotion", {"on": True, "since": datetime.now(UTC).isoformat()})
    assert "SHADOW" in center.oneline(app)["items"][3]["text"] and center.oneline(app)["level"] == "bad"
    ops.set_state(app.engine, "ai_demotion", {})
    st, body = _req(server, "GET", "/api/oneline")
    assert st == 200 and len(body["items"]) == 4


def test_frontend_has_no_duplicate_globals():
    """화면 스크립트는 전역 이름을 공유한다 — 같은 이름이 두 번 있으면 나중에 로드된 파일이 조용히 덮어쓴다
    (예: 증시 지도 색 함수가 AI 확률 색 함수에 덮여 모든 타일이 빨강, '실험·승격' 화면이 연구 결과 함수에 덮여 오류)."""
    import re
    from collections import Counter
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src/quant_ai/web/static"
    names = Counter()
    for f in root.glob("*.js"):
        if f.name == "sw.js":
            continue
        names.update(re.findall(r"^(?:async )?function ([A-Za-z0-9_]+)|^(?:const|let) ([A-Za-z0-9_]+) =", f.read_text(), re.M))
    flat = Counter()
    for (a, b), n in names.items():
        flat[a or b] += n
    assert [k for k, n in flat.items() if n > 1] == []


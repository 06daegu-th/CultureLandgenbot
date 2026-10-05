"""v25: 토스식 화면 — 화면 하나 = API 하나(/api/t/*). 홈 · 종목 · 통합 피드 · 포트폴리오 · 시장 · 시세 묶음 · AI 리포트 · 알림 설정 + 화면 연결 정적 검사."""

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v25")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


@pytest.fixture(scope="module")
def syms(app):
    bars, _ = app._all_bars()
    return sorted(s for s in bars if s[:1].isdigit())


@pytest.fixture(scope="module")
def news(app, syms):
    """뉴스 2건(한국어·영어 AI) + 공시 1건 — 피드 필터·기사 화면용."""
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Disclosure, NewsArticle
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        s.add_all([NewsArticle(source="연합뉴스", url="https://example.com/v25/1", published_at=now - timedelta(hours=3), title="반도체 업황 회복 기대",
                               body="본문", symbols=[syms[0]], sentiment=0.5, importance=0.7, collected_at=now),
                   NewsArticle(source="Reuters", url="https://example.com/v25/2", published_at=now - timedelta(hours=1), title="Nvidia AI data center demand",
                               body="body", symbols=["NVDA"], sentiment=-0.5, importance=0.8, collected_at=now),
                   Disclosure(source="DART", receipt_no="V25TEST0001", symbol=syms[1], title="영업(잠정)실적(공정공시)", filed_at=now.date(),
                              url="https://dart.fss.or.kr/", sentiment=0.3, collected_at=now)])
    return True


@pytest.fixture(scope="module")
def server(app):
    from http.server import ThreadingHTTPServer

    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), None, {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _get(port, path):
    c = HTTPConnection("127.0.0.1", port, timeout=60)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def _post(port, path, body):
    c = HTTPConnection("127.0.0.1", port, timeout=60)
    c.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json"})
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


# ------------------------------------------------------------------ 홈
def test_home_has_every_block_and_honest_index_labels(app):
    from quant_ai import toss
    h = toss.home(app, now=datetime(2026, 10, 5, 6, 0, tzinfo=UTC))
    for k in ("greeting", "date", "indices", "picks", "events", "holdings", "trust", "markets"):
        assert k in h, k
    assert "좋은 오후예요" in h["greeting"]  # 15시 KST
    idx = {x["key"]: x for x in h["indices"]}
    assert idx["KOSPI"]["proxy"] is True and "대용" in idx["KOSPI"]["proxy_note"]  # 대용값을 진짜 코스피처럼 보이지 않게
    for k in ("NASDAQCOM", "SP500"):  # 자료가 없으면 빈칸이 아니라 '왜 없는지'
        assert idx[k].get("missing") and "자료 없음" in idx[k]["why"]
    assert [m["key"] for m in h["markets"]] == ["KRX", "US"]
    assert {"equity", "pnl", "pnl_pct", "spark"} <= set(h["holdings"])


# ------------------------------------------------------------------ 종목
def test_stock_stats_and_market_state(app, syms):
    from quant_ai import toss
    st = toss.stock(app, syms[0], now=datetime(2026, 10, 3, 3, 0, tzinfo=UTC))  # 토요일
    assert st["last"] and st["bar_date"] and st["identity"]["symbol"] == syms[0]
    s = st["stats"]
    assert s["low52"] <= s["low"] <= s["high"] <= s["high52"]
    assert st["market"]["state"] == "휴장"
    nv = toss.stock(app, "ZZZZ", now=datetime(2026, 10, 3, 3, 0, tzinfo=UTC))  # 시세 없는 해외 종목도 오류 없이
    assert "last" not in nv and nv["market"]["state"]


# ------------------------------------------------------------------ 통합 피드
def test_feed_tabs_and_filters(app, news, syms):
    from quant_ai import toss
    f = toss.feed(app)
    kinds = [x["kind"] for x in f["items"]]
    assert "news" in kinds and "disc" in kinds
    assert all(x["ago"] for x in f["items"])
    d = next(x for x in f["items"] if x["kind"] == "disc")
    assert d["ago"] in ("오늘", "어제") or "." in d["ago"]  # 공시는 날짜만 있다 — '몇 시간 전' 이 아님
    assert [x["kind"] for x in toss.feed(app, tab="disc")["items"]] == ["disc"]
    us = toss.feed(app, tab="news", region="us")["items"]
    assert [x["symbols"] for x in us] == [["NVDA"]]
    ai = toss.feed(app, tab="news", topic="ai")["items"]
    assert ai and all("AI" in x["title"] or "Nvidia" in x["title"] for x in ai)
    semi = toss.feed(app, tab="news", topic="semi")["items"]
    assert any("반도체" in x["title"] for x in semi)
    ev = toss.feed(app, tab="event")["items"]
    assert ev and all(x["kind"] == "event" and x["d_label"] and x.get("date") for x in ev)


# ------------------------------------------------------------------ 포트폴리오 · 시장 · 시세 · 리포트
def test_portfolio_shape(app):
    from quant_ai import toss
    p = toss.portfolio(app)
    for k in ("equity", "cash", "stock", "pnl", "positions", "alloc", "trades", "spark"):
        assert k in p, k
    assert p["alloc"][-1]["name"] == "현금"
    assert abs(sum(a["weight"] for a in p["alloc"]) - 1) < 1e-6 or p["equity"] == 0


def test_market_movers_from_liquid_names(app):
    from quant_ai import toss
    m = toss.market(app)
    kr = m["movers"]["kr"]
    assert kr["n"] >= 1 and kr["up"] and kr["down"]
    assert kr["up"][0]["chg_pct"] >= kr["down"][0]["chg_pct"]
    assert all(r["name"] for r in kr["up"])


def test_quotes_and_report(app, syms):
    from quant_ai import toss
    q = toss.quotes(app, [syms[0], "NVDA", syms[0]])["rows"]
    assert [r["symbol"] for r in q] == [syms[0], "NVDA"]  # 중복 제거 · 순서 유지
    assert q[0]["last"] and q[1]["last"] is None  # 시세 없는 종목은 None (오류 아님)
    r = toss.report(app, syms[0])
    assert r["symbol"] == syms[0] and isinstance(r["history"], list)
    assert r["delta"] is None or abs(r["delta"]) <= 1


# ------------------------------------------------------------------ 알림 설정 (가격 ±% · 종류별 켜고 끄기)
def test_alerts_write_roundtrip(app, syms):
    from quant_ai import toss, ux
    ux.set_star(app, syms[0], True)
    a = toss.alerts(app)
    row = next(r for r in a["price"] if r["symbol"] == syms[0])
    assert row["on"] is False
    rid = toss.alerts_write(app, {"symbol": syms[0], "price_on": True, "value": 7})["rule_id"]
    row = next(r for r in toss.alerts(app)["price"] if r["symbol"] == syms[0])
    assert row["on"] and row["value"] == 7 and row["rule_id"] == rid
    toss.alerts_write(app, {"symbol": syms[0], "rule_id": rid})
    assert not next(r for r in toss.alerts(app)["price"] if r["symbol"] == syms[0])["on"]
    toss.alerts_write(app, {"kind": "signal", "on": False})
    assert not next(x for x in toss.alerts(app)["event"] if x["kind"] == "signal")["on"]
    toss.alerts_write(app, {"kind": "signal", "on": True})
    assert next(x for x in toss.alerts(app)["event"] if x["kind"] == "signal")["on"]
    with pytest.raises(ValueError):
        toss.alerts_write(app, {})


# ------------------------------------------------------------------ HTTP
def test_t_endpoints(server, syms, news):
    for path in ("/api/t/home", f"/api/t/stock?symbol={syms[0]}", "/api/t/feed?tab=news&region=kr", "/api/t/portfolio?mode=paper",
                 "/api/t/market", f"/api/t/quotes?symbols={syms[0]},NVDA", f"/api/t/report?symbol={syms[0]}", "/api/t/alerts"):
        st, body = _get(server, path)
        assert st == 200 and "error" not in body, (path, body)
    st, body = _get(server, "/api/t/feed?tab=<script>&region=zz&topic=x")  # 이상한 값은 기본값으로
    assert st == 200 and body["filters"] == {"tab": "all", "region": "all", "topic": "all"}
    st, body = _get(server, "/api/t/stock?symbol=")
    assert st == 400
    st, body = _post(server, "/api/t/alerts", {"kind": "news", "on": True})
    assert st == 200 and body["ok"]


# ------------------------------------------------------------------ 화면 연결 정적 검사
def test_static_v25_wiring():
    html = (STATIC / "index.html").read_text()
    assert '<script src="/toss.js"></script>' in html and '<link rel="stylesheet" href="/toss.css">' in html
    assert html.index("/toss.js") < html.index("/app.js")  # app.js 가 render() 에서 tossRender 를 부른다
    sw = (STATIC / "sw.js").read_text()
    import re
    assert re.search(r"qa-shell-v(2[5-9]|[3-9]\d)", sw) and '"/toss.js"' in sw and '"/toss.css"' in sw
    app_js = (STATIC / "app.js").read_text()
    assert "tossRender(el)" in app_js and "S.sub = sub" in app_js
    easy = (STATIC / "easy.js").read_text()
    for v in ('["watch", "star", "관심종목"]', '["pos", "portfolio", "포트폴리오"]', '["market", "market", "시장"]', '["report", "ai", "AI 분석"]',
              '["alerts", "bell", "알림 설정"]', '["more", "grid", "더보기"]'):
        assert v in easy, v
    verify = (STATIC / "verify.js").read_text()
    assert '["more", "grid", "더보기"]' in verify  # 휴대폰 하단 탭: 홈 · 관심종목 · 포트폴리오 · 시장 · 더보기
    t = (STATIC / "toss.js").read_text()
    for fn in ("async function tHome(", "async function tStock(", "async function tFeed(", "async function tArticle(", "async function tPortfolio(",
               "async function tMarket(", "async function tReport(", "async function tAlerts(", "async function tMore(", "async function tWatch(",
               "function tOrderSheet(", "async function tossRender("):
        assert fn in t, fn
    assert "e.stopImmediatePropagation()" in t and '}, true);' in t  # 쉬운 화면: 뉴스 클릭 → 전체 화면 기사 (캡처 단계)
    assert "Date.UTC(" in t  # 날짜만 있는 일정이 브라우저 시간대 때문에 하루 밀리지 않게
    assert "T_NAV" in t  # 뒤로 단추가 사이트 밖으로 나가지 않게
    css = (STATIC / "toss.css").read_text()
    assert ".ts { min-width: 0;" in css  # 그리드 안에서 휴대폰 가로 넘침 방지
    assert "@media (max-width: 760px)" in css and ":root[data-theme=\"light\"]" in css


def test_static_toss_escapes_server_text():
    """서버에서 온 글(제목·이름·출처)은 esc() 를 거쳐서만 화면에 들어간다 — 대표 지점 확인."""
    t = (STATIC / "toss.js").read_text()
    for frag in ("esc(x.title)", "esc(d.title)", "esc(a.title)", "esc(name)", "esc(r.name || r.symbol)" if "esc(r.name || r.symbol)" in t else "esc(name || sym)",
                 "esc(ex.translation)", "esc(d.body || d.summary"):
        assert frag in t, frag
    assert "innerHTML = d." not in t and "innerHTML = x." not in t

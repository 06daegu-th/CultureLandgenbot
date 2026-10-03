"""v23: 기업 마스터 · 내장 로고 · 검색/종목/로고 API E2E · 홈(확인할 것·주의할 것·결론) · 뉴스 So What · 실적 숫자 ·
상황별 성적 · 휴장/서머타임 · 통신 끊김 주문 · LiveBroker 역할 · 화면 정적 검사."""

import json
import re
import threading
from dataclasses import replace
from datetime import UTC, datetime
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import quote

import pytest

from tests.test_kis_live import cfg, live  # noqa: F401 - 가짜 KIS 서버 fixture

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Instrument
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v23")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    with session_scope(a.engine) as s:  # 실제 이름의 국내 종목 하나 (검색 E2E)
        s.add(Instrument(symbol="005930", market="KOSPI", name="삼성전자", currency="KRW"))
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


def _get(port, path, raw=False):
    c = HTTPConnection("127.0.0.1", port, timeout=60)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    return (r.status, body, dict(r.getheaders())) if raw else (r.status, json.loads(body or b"{}"))


# ------------------------------------------------------------------ 기업 마스터 · 로고
def test_company_master_isin_and_identity():
    from quant_ai.companies import identity, isin_ok, kr_isin, master
    m = master()
    assert kr_isin("005930") == "KR7005930003" and kr_isin("000660") == "KR7000660001"
    assert all(isin_ok(c.isin) for c in m.values() if c.isin)
    for sym in ("005930", "000660", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "TSM"):
        c = m[sym]
        assert c.name and c.exchange and c.currency and c.sector and c.website
    n = identity(None, "NVDA")
    assert (n["name"], n["exchange"], n["currency"], n["logo"]) == ("엔비디아", "NASDAQ", "USD", "bundled")
    unknown = identity(None, "123450")
    assert unknown["country"] == "KR" and unknown["logo"] == "fetch" and unknown["isin"] == kr_isin("123450")
    assert identity(None, "SPY")["etf"]


def test_bundled_logos_are_safe_and_licensed():
    from quant_ai.companies import LOGO_DIR, bundled
    idx = bundled()
    for sym in ("005930", "000660", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "TSM", "035420", "035720"):
        assert sym in idx, sym
    for f in LOGO_DIR.glob("*.svg"):
        t = f.read_text()
        assert t.startswith("<svg") and not re.search(r"<script|on[a-z]+\s*=|javascript:|foreignObject", t, re.I), f.name
    notice = (LOGO_DIR / "NOTICE.md").read_text()
    assert "CC0" in notice and "MIT" in notice and "상표" in notice
    assert "assets/logos/*" in (ROOT / "pyproject.toml").read_text()  # 설치본에도 포함


def test_logo_order_bundled_then_default_icon(app, monkeypatch):
    from quant_ai import logos
    calls = []
    data, ctype, src = logos.get(app, "NVDA", fetch=lambda u: calls.append(u) or b"")
    assert src == "bundled" and ctype == "image/svg+xml" and not calls  # 네트워크를 쓰지 않는다
    monkeypatch.setenv("QUANT_LOGOS", "off")
    d2, _, src2 = logos.get(app, "ZZZQ")
    assert src2 == "default" and b"<svg" in d2 and b"ETF" not in d2
    assert b"ETF" in logos.default_icon(app, "QQQ")


# ------------------------------------------------------------------ API E2E (서버 → 검색 → 종목 → 로고)
def test_e2e_search_samsung_and_nvidia(server):
    st, r = _get(server, "/api/search?q=" + quote("삼성전자"))
    assert st == 200
    top = r["results"][0]
    assert top["symbol"] == "005930" and top["logo"] == "bundled" and top["name_en"] == "Samsung Electronics"
    st, r = _get(server, "/api/search?q=" + quote("엔비디아"))
    nv = r["results"][0]
    assert nv["symbol"] == "NVDA" and nv["exchange"] == "NASDAQ" and nv["logo"] == "bundled"
    st, r = _get(server, "/api/search?q=nvidia")
    assert r["results"][0]["symbol"] == "NVDA"


def test_e2e_logo_and_company_api(server):
    for sym in ("005930", "NVDA"):
        st, body, h = _get(server, f"/api/logo/{sym}", raw=True)
        assert st == 200 and h["Content-Type"].startswith("image/svg") and body.startswith(b"<svg")
        assert "86400" in h.get("Cache-Control", "")  # 진짜 로고는 오래 캐시
    st, c = _get(server, "/api/company?symbol=NVDA")
    assert c["name_en"] == "NVIDIA" and c["isin"] == "US67066G1040" and c["logo_url"] == "/api/logo/NVDA"


def test_e2e_stock_without_news_or_disclosures(server, app):
    sym = sorted(s for s in app.market_data()[0] if s[:1].isdigit())[0]
    st, v = _get(server, f"/api/verdict?symbol={sym}")
    assert st == 200 and v["identity"]["symbol"] == sym and "market" in v and v.get("earnings") is None
    st, a = _get(server, f"/api/analysis?symbol={sym}")
    assert st == 200 and "error" not in a


# ------------------------------------------------------------------ 홈 (오래된 데이터 → 결론)
def test_home_sections_and_stale_data_conclusion(app):
    from quant_ai.center import conclusion, home5
    h = home5(app)
    for k in ("market", "todo", "caution", "core", "watch", "system", "data", "conclusion"):
        assert k in h, k
    assert all(not (it.get("kind") or "").startswith("ops_") for it in h["todo"]["items"])  # 시스템 점검은 할 일과 분리
    assert any("밀림" in c["text"] for c in h["caution"])  # 테스트 데이터는 몇 년 전 것 → 주의
    assert h["conclusion"]["level"] == "bad" and "데이터" in h["conclusion"]["text"]
    assert "core" in h and (h["core"]["lines"] or h["core"]["hint"])
    assert h["data"]["overall"] is not None and h["data"]["items"]
    calm = conclusion({"caution": [], "todo": {"items": []}, "ai": {"key": "checking"}, "core": {"lines": []}})
    assert calm["do_nothing"] and "아무것도 하지 않아도" in calm["text"]


# ------------------------------------------------------------------ 시장 시간: 휴장 이유 · 서머타임
def test_market_holiday_reason_and_dst():
    from quant_ai.clock import clock_status
    m = clock_status(datetime(2026, 7, 3, 15, 0, tzinfo=UTC))["markets"]
    assert not m["US"]["trading_day"] and "독립기념일" in m["US"]["notice"] and m["US"]["dst"] is True
    assert m["US"]["next_open_kst"].endswith("22:30 KST")  # 서머타임: 한국시간 22:30 개장
    w = clock_status(datetime(2026, 1, 13, 15, 0, tzinfo=UTC))["markets"]["US"]
    assert w["dst"] is False and "23:30" in (w["next_open_kst"] or "")  # 표준시: 23:30
    kr = clock_status(datetime(2026, 10, 3, 3, 0, tzinfo=UTC))["markets"]["KRX"]
    assert not kr["trading_day"] and kr["holiday"]


# ------------------------------------------------------------------ 뉴스 So What · 실적 숫자 · 상황별 성적
def test_so_what_rules(app, monkeypatch):
    from quant_ai import newsdetail
    monkeypatch.setattr(newsdetail, "_held", lambda app: {"000020": {"qty": 10, "book": "paper"}})
    chain = [{"symbol": "000020", "name": "종목A", "why": "기사에 직접 나옴"}, {"symbol": "000030", "name": "종목B", "why": "같은 업종"}]
    rel = [{"symbol": "000020", "name": "종목A", "ai_change": {"before": {"action": "BUY", "score": 72}, "after": {"action": "HOLD", "score": 58},
                                                                "delta": -14}}]
    w = newsdetail.so_what(app, "중국 반도체 규제", -0.6, "소송·제재", None, rel, chain)
    assert w["mine"][0]["symbol"] == "000020" and w["level"] == "bad" and "신규 매수는 보류" in w["conclusion"]
    assert w["ai_changes"][0]["text"] == "BUY 72% → HOLD 58%" and w["companies"][0]["held"]
    calm = newsdetail.so_what(app, "x", 0.0, "기타", None, [], [])
    assert calm["level"] == "ok" and calm["mine"] == []


def test_fin_numbers_from_disclosure_text():
    from quant_ai.newsdetail import fin_numbers
    r = {x["key"]: x for x in fin_numbers("매출액 79조 1,000억원(전년 동기 대비 12.3% 증가), 영업이익 10조원 (20.1% 감소)")}
    assert r["revenue"]["value"] == "79조 1,000억원" and r["revenue"]["change"] == "+12.3%"
    assert r["op_income"]["change"] == "-20.1%"
    e = {x["key"]: x for x in fin_numbers("Revenue of $35.1 billion, up 94%. Earnings per diluted share was $0.78, up 111%")}
    assert e["revenue"]["change"] == "+94%" and e["eps"]["value"] == "$0.78"


def test_ai_context_scorecard_groups(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    from quant_ai.scorecard import by_context
    with session_scope(app.engine) as s:
        for i in range(12):
            s.add(ConsensusRecord(symbol="000020", as_of=datetime(2026, 1, 1 + i, tzinfo=UTC), action="BUY", prob_up=0.6, confidence=0.5,
                                  conflict="low", correct=i % 3 != 0, realized_return=0.01,
                                  payload={"evidence": {"news": [{"events": ["earnings"]}] if i < 6 else [], "disclosures": []}}))
    r = by_context(app)
    news = {x["key"]: x for x in r["news"]}
    assert news["실적"]["n"] == 6 and news["관련 뉴스 없음"]["n"] >= 6
    ern = {x["key"]: x for x in r["earnings"]}
    assert ern["실적 발표 전후"]["n"] == 6 and not ern["실적 발표 전후"]["enough"]  # 표본 10건 미만은 '부족'


# ------------------------------------------------------------------ 주문: 통신 끊김 · LiveBroker 역할
def test_network_error_classification():
    import urllib.error

    from quant_ai.trading.execution import is_network_error
    from quant_ai.trading.kis import KISError
    assert is_network_error(ConnectionResetError()) and is_network_error(TimeoutError())
    assert is_network_error(urllib.error.URLError("down"))
    assert not is_network_error(KISError("APBK0001", "종목코드 오류")) and not is_network_error(ValueError("x"))


def test_lost_order_response_stops_new_buys_and_never_duplicates(live):  # noqa: F811
    from quant_ai import ops
    from quant_ai.config import Mode
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import OrderRecord
    app, mock = live
    mock.drop_order_response = True  # 증권사는 주문을 받았지만 응답이 끊김
    app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    placed = len(mock.orders)
    assert placed > 0
    with session_scope(app.engine) as s:
        st = {o.status for o in s.query(OrderRecord).filter(OrderRecord.mode == "live")}
    assert st == {"unknown"}  # '실패'로 단정하지 않는다
    assert ops.kill_switch_on(app.engine)  # 신규 매수 중단 → 사람이 증권사 앱에서 확인
    mock.drop_order_response = False
    app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    assert len(mock.orders) == placed  # 킬스위치 동안 같은 주문을 다시 내지 않는다
    app.set_kill_switch(False, "확인 완료", by="test")
    app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    pf = app.load_portfolio("live")
    broker_pos = {c: q for c, (q, _) in mock.positions.items() if q}
    assert {s_: p.qty for s_, p in pf.positions.items() if p.qty} == broker_pos  # 해제 뒤: 증권사 잔고로 맞춘 뒤 판단 → 중복 없음


def test_live_broker_without_real_broker_refuses(tmp_path):
    from quant_ai.config import Settings
    from quant_ai.trading.broker import BrokerNotConfigured, LiveBroker
    from quant_ai.trading.portfolio import Portfolio
    st = replace(Settings.from_env({}), artifacts_dir=tmp_path)
    try:
        b = LiveBroker(Portfolio(cash=100_000), st, champion_ready=True)
    except PermissionError:
        return  # 실전 동의가 없으면 만들 수조차 없다 (먼저 막힘)
    with pytest.raises(BrokerNotConfigured):
        b.submit(None, None, datetime.now(UTC))


# ------------------------------------------------------------------ 화면 정적 검사
def test_static_v23_wiring():
    idx = (STATIC / "index.html").read_text()
    assert idx.index("/style.css") < idx.index("/calm.css")
    calm = (STATIC / "calm.css").read_text()
    assert "--up: #f04452" in calm and "--down: #3182f6" in calm and ".b-BUY" in calm and ".act-SELL" in calm
    words = (STATIC / "words.js").read_text()
    assert 'BUY: "buy", SELL: "sell"' in words
    app_js = (STATIC / "app.js").read_text()
    assert "const coId = " in app_js and "coId(sym, a.name || sym, { big: true" in app_js
    easy = (STATIC / "easy.js").read_text()
    for s in ("오늘 확인할 것", "오늘 주의할 것", "시장 핵심", "관심종목 — AI 판단", "오늘의 결론", "function stockMeta(", "xs.slice(0, 3)"):
        assert s in easy, s
    assert "function soWhat(" in (STATIC / "board.js").read_text()
    assert "function contextScore(" in (STATIC / "hub.js").read_text()
    sw = (STATIC / "sw.js").read_text()
    assert '"/calm.css"' in sw

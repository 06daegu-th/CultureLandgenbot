"""v19: 쉬운 화면 — 오늘 할 일 3개 · 처음 안내 · 쉬운 AI 성적표 · AI 최종 판단(NO TRADE 우선) · 미국 종목 AI ·
종목 첫 화면(52주·장 상태) · 영문 검색 · 포트폴리오 테마/보유 정보 · 로고(국내 출처·재시도·미리 받기) · 보안·오프라인."""

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from quant_ai import ops

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v19")
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


def _get(port, path):
    c = HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.read(), dict(r.getheaders())


def _syms(app):
    return sorted(s for s in app.market_data()[0] if s[:1].isdigit())


def _scored(app, sym, n=40, now=None, prob=0.62, up_every=3, action="BUY"):
    """채점된 판단 n 개 (up_every 번째마다 하락) — 성적표·판단 테스트용."""
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    b = app.market_data()[0][sym]
    t0 = pd.Timestamp(b.index[-n - 10]).to_pydatetime()
    with session_scope(app.engine) as s:
        for i in range(n):
            up = i % up_every != 0
            s.add(ConsensusRecord(symbol=sym, as_of=t0 + timedelta(days=i), action=action, prob_up=prob, confidence=60, conflict="low",
                                  payload={"horizon": 5, "regime": "bull_quiet", "expected_return": 0.02},
                                  realized_return=0.03 if up else -0.04, correct=up))


# ------------------------------------------------------------------ 홈: 오늘 할 일 3개 · 처음 안내 · 화면 설정
def test_today3_priorities_and_start_guide(app, monkeypatch):
    from quant_ai import center, prefs
    # 시험 데이터는 2021년이라 '운영 꺼짐·데이터 밀림'이 늘 맨 위로 온다 — 이 시험은 그 아래 순서를 본다 (운영 알림은 test_v20)
    monkeypatch.setattr(center, "ops_status", lambda a, now=None: {"issues": []})
    from quant_ai.ux import set_star
    a, b = _syms(app)[:2]
    g = center.start_guide(app)
    assert [x["key"] for x in g["steps"]] == ["data", "goal", "watch", "today"]  # v33: ② 투자 한도 → 내 목표 계획 (한도는 선택)
    assert g["steps"][0]["done"] and not g["steps"][1]["done"] and g["next"] == "goal" and not g["done"]
    assert any("투자 한도" in o["title"] for o in g["optional"])
    t = center.today3(app)
    assert t["items"] == [] or len(t["items"]) <= 3
    # 관심종목 실적 D-1 · 시장 CPI 내일 · 자동 정지 → 정지가 맨 위, 3개까지
    now = datetime.now(UTC)
    set_star(app, a, True)
    ops.set_state(app.engine, "event_calendar", {"events": [
        {"date": (now.astimezone(ZoneInfo("Asia/Seoul")) + timedelta(days=1)).date().isoformat(), "kind": "earnings", "symbol": a, "title": "3분기 실적", "market": "KR"}]})
    ops.set_kill_switch(app.engine, True, "테스트", halt=True)
    try:
        t = center.today3(app, now=now)
        assert t["items"][0]["kind"] == "halt" and t["items"][0]["link"] == "#safety"
        ev = next(x for x in t["items"] if x["kind"] == "event")
        assert "내일" in ev["text"] and ev["symbol"] == a and len(t["items"]) <= 3
    finally:
        ops.set_kill_switch(app.engine, False)
        ops.set_state(app.engine, "event_calendar", {})
    for s_ in (a, b, _syms(app)[2]):
        set_star(app, s_, True)
    assert center.start_guide(app)["steps"][2]["done"]
    p = prefs.save(app.engine, {"ui": {"mode": "pro"}})
    assert p["ui"] == {"mode": "pro", "guide_hidden": False}
    assert prefs.save(app.engine, {"ui": {"guide_hidden": True}})["ui"] == {"mode": "pro", "guide_hidden": True}
    with pytest.raises(ValueError):
        prefs.save(app.engine, {"ui": {"mode": "hacker"}})
    prefs.save(app.engine, {"ui": {"mode": "easy", "guide_hidden": False}})
    for s_ in (a, b, _syms(app)[2]):
        set_star(app, s_, False)


# ------------------------------------------------------------------ 쉬운 AI 성적표 · AI 상태
def test_plain_scorecard_counts_money_vs_index_failures_and_regimes(app):
    from quant_ai import center, scorecard
    sym = _syms(app)[3]
    assert scorecard.plain(app, symbol="999999")["n"] == 0
    _scored(app, sym, n=40)
    r = scorecard.plain(app, 100, sym)
    assert r["n"] == 40 and r["hits"] == 26 and r["headline"].startswith("최근 40회 중 26회 방향 적중")
    m = r["money"]
    assert m["n"] == 40 and m["kind"] == "BUY 라고 한" and abs(m["avg_raw"] - (26 * 0.03 - 14 * 0.04) / 40) < 1e-4
    assert m["avg_net"] < m["avg_raw"] and m["avg_bench"] is not None and "지수보다" in m["text"]
    assert len(r["failures"]) == 3 and all(f["actual"] == -0.04 for f in r["failures"]) and "예상 +2.0% → 실제 -4.0%" in r["failures"][0]["text"]
    assert r["regimes"] == [{"regime": "상승장", "n": 40, "hit": 0.65}] and r["strong"].startswith("상승장에서 잘함")
    st = center.ai_state(app)
    assert st["easy"]["headline"] and "label" in st and center.AI_STATE["verified"][1] == "실전 가능"


# ------------------------------------------------------------------ AI 최종 판단: 하나로 · NO TRADE 우선
def test_verdict_final_votes_no_trade_gates_and_data_used(app, monkeypatch):
    from quant_ai import center, explain
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    sym = _syms(app)[4]
    assert explain.verdict(app, sym)["final"] is None and explain.verdict(app, "NVDA")["can_analyze"]
    b = app.market_data()[0][sym]
    last = pd.Timestamp(b.index[-1]).to_pydatetime()
    contribs = [{"analyst": "primary", "prob_up": 0.7, "weight": 0.5, "summary": "실적 기대"},
                {"analyst": "nvidia", "prob_up": 0.5, "weight": 0.3, "summary": "거시 중립"},
                {"analyst": "quant", "prob_up": None, "weight": 0, "summary": "기권: 학습된 모델 없음"},
                {"analyst": "risk", "prob_up": None, "weight": 0, "summary": "위험 요인 없음"}]
    with session_scope(app.engine) as s:
        s.add(ConsensusRecord(symbol=sym, as_of=last, action="BUY", prob_up=0.66, confidence=70, conflict="low",
                              payload={"horizon": 5, "contributions": contribs, "reasons": ["[primary] 20일 모멘텀 상승"],
                                       "evidence": {"price": {"last_bar": str(last), "vol_20": 0.02}, "news": [{"at": "2026-09-01T00:00:00"}],
                                                    "disclosures": [], "macro": {}, "regime": {"regime": "bull_quiet"}}}))
    from quant_ai import stock
    monkeypatch.setattr(stock, "trust", lambda a, s_: {"status": "ok", "checks": []})  # 시험 데이터는 2021년이라 '오래됨' — 여기선 정상으로
    monkeypatch.setattr(center, "ai_state", lambda a: {"key": "checking", "icon": "🟡", "label": "검증 중", "demoted": False})
    now = last + timedelta(hours=10)  # 장 마감 뒤 · 하루 안
    monkeypatch.setattr(explain, "_market_open", lambda s_, n: False)
    v = explain.verdict(app, sym, now=now)
    assert v["final"] == "BUY" and v["big"] == "🟢 BUY 66%" and not v["changed_by_gate"], v["no_trade"]
    views = {x["role"]: x["view"] for x in v["votes"]}
    assert views == {"News": "BUY", "Macro": "HOLD", "Quant": "기권", "Risk": "통과"}
    assert v["why_buy"][0] == "뉴스 AI: 20일 모멘텀 상승" and v["used"]["news"]["n"] == 1 and v["used"]["sealed"] is False
    # 실적 D-1 → BUY 를 막는다 (판단 기록은 그대로)
    ops.set_state(app.engine, "event_calendar", {"risk": [{"symbol": sym, "earnings": {"trading_days": 1, "d_label": "D-1"}, "buy_multiplier": 0.5}]})
    v = explain.verdict(app, sym, now=now)
    assert v["final"] == "NO_TRADE" and v["raw"] == "BUY" and v["changed_by_gate"] and any("실적 발표 임박" in x for x in v["no_trade"])
    ops.set_state(app.engine, "event_calendar", {})
    # 장중인데 실시간 가격이 20분 넘게 안 바뀜 → NO TRADE
    monkeypatch.setattr(explain, "_market_open", lambda s_, n: True)
    ops.set_state(app.engine, "live_quotes", {sym: {"price": 1.0, "at": (now - timedelta(minutes=45)).isoformat()}})
    v = explain.verdict(app, sym, now=now)
    assert v["final"] == "NO_TRADE" and any("가격 지연" in x and "45분" in x for x in v["no_trade"])
    ops.set_state(app.engine, "live_quotes", {})
    # 오래된 판단 · AI 사용 금지 상태도 막는다
    monkeypatch.setattr(explain, "_market_open", lambda s_, n: False)
    assert any("판단이 오래됨" in x for x in explain.verdict(app, sym, now=last + timedelta(days=10))["no_trade"])
    monkeypatch.setattr(center, "ai_state", lambda a: {"key": "banned", "icon": "🔴", "label": "사용 금지", "demoted": False})
    v = explain.verdict(app, sym, now=now)
    assert v["final"] == "NO_TRADE" and any("사용 금지" in x for x in v["no_trade"])


def test_verdict_route_has_52w_and_market(app, server):
    sym = _syms(app)[0]
    st, body, _ = _get(server, f"/api/verdict?symbol={sym}")
    v = json.loads(body)
    assert st == 200 and v["market"]["name"] == "KRX" and v["market"]["light"] in ("🟢", "🟡", "🔴")
    r = v["range52"]
    assert r["low"] <= r["last"] <= r["high"] and 0 <= r["pos"] <= 1 and r["from_high"] <= 0
    st, body, _ = _get(server, "/api/ai-plain")
    assert st == 200 and "headline" in json.loads(body)
    st, body, _ = _get(server, "/api/start-guide")
    assert st == 200 and len(json.loads(body)["steps"]) == 4


def test_us_ticker_can_be_analyzed(app, monkeypatch):
    """문제: 미국 종목은 'AI 합의 대상이 아닙니다'로 막혀 있었다 → 일봉을 받아 같은 AI 합의로 판단."""
    from quant_ai import actions
    from quant_ai.data import global_stocks
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Instrument, PriceBar
    from quant_ai.global_market import BENCH
    k = app.market_data()[0][_syms(app)[0]]
    called = []
    monkeypatch.setattr(global_stocks, "ensure_many", lambda eng, syms, **kw: called.append(syms) or {"fetched": 0})
    with pytest.raises(ValueError, match="일봉을 받지 못했습니다"):  # 네트워크가 막혀 못 받으면 쉬운 문장으로
        actions.analyze_symbol(app, "ZZZT")
    with session_scope(app.engine) as s:
        s.add(Instrument(symbol="ZZZT", market="US", name="Test Corp", currency="USD"))
        s.add(Instrument(symbol=BENCH, market="INDEX", name=BENCH, currency="USD"))
        for ts, row in k.iterrows():
            s.add(PriceBar(symbol=BENCH, ts=pd.Timestamp(ts).to_pydatetime(), open=float(row["open"]), high=float(row["high"]),
                           low=float(row["low"]), close=float(row["close"]), volume=float(row["volume"]), interval="1d", source="yahoo"))
            s.add(PriceBar(symbol="ZZZT", ts=pd.Timestamp(ts).to_pydatetime(), open=float(row["open"]) / 1000, high=float(row["high"]) / 1000,
                           low=float(row["low"]) / 1000, close=float(row["close"]) / 1000, volume=float(row["volume"]), interval="1d", source="yahoo"))
    r = actions.analyze_symbol(app, "ZZZT")
    assert called[-1] == ["ZZZT", BENCH] and r["consensus_id"] and r["action"] in ("BUY", "SELL", "HOLD", "NO_TRADE")
    with pytest.raises(ValueError):
        actions.analyze_symbol(app, "../x")


# ------------------------------------------------------------------ 검색 · 실적 배너 · 이벤트 게이트
def test_english_name_search_and_earnings_estimates(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.global_stocks import KR_ENGLISH, search
    from quant_ai.data.models import Instrument
    from quant_ai.stockplus import earnings_banner
    with session_scope(app.engine) as s:
        s.add(Instrument(symbol="005930", market="KRX", name="삼성전자", currency="KRW"))
    with session_scope(app.engine) as s:
        assert search(s, "Samsung Electronics", 3)[0]["symbol"] == "005930"
        assert search(s, "samsung", 3)[0]["symbol"] == "005930"
    assert KR_ENGLISH["sk hynix"] == "000660"
    e = {"d_label": "D-3", "date": "2026-11-18", "time": "장 마감 후", "eps_estimate": 0.98, "revenue_estimate": 32.5e9}
    assert earnings_banner(app, "NVDA", e).endswith("· 예상 EPS $0.98 · 예상 매출 $32.5B")
    k = {"d_label": "D-1", "date": "2026-10-29", "eps_estimate": 1520.0, "revenue_estimate": 79e12}
    assert "예상 EPS 1,520.00원 · 예상 매출 79.0조원" in earnings_banner(app, "005930", k)


# ------------------------------------------------------------------ 포트폴리오: 테마 · 보유 종목 정보
def test_portfolio_themes_and_holding_extras(app):
    from quant_ai import portfolio_os
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import NewsArticle
    th = portfolio_os.themes({"NVDA": 0.2, "AMD": 0.13, "TSM": 0.1, "AAPL": 0.1, "005380": 0.05}, {"005380": "자동차"},
                             {"NVDA": "엔비디아", "AMD": "AMD", "TSM": "TSMC"})
    top = th[0]
    assert top["theme"] == "반도체·AI" and top["weight"] == 0.43 and top["level"] == "HIGH" and "사실상 같은 베팅" in top["text"]
    assert top["text"].startswith("반도체·AI 집중 43% (엔비디아·AMD·TSMC)")
    sym = _syms(app)[1]
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        s.add(NewsArticle(source="t", url="https://x/n19", title="대형 수주", body="", published_at=now - timedelta(hours=5),
                          symbols=[sym], sentiment=0.5, importance=0.9))
    ops.set_state(app.engine, "event_calendar", {"events": [{"date": (now + timedelta(days=4)).date().isoformat(), "kind": "earnings",
                                                             "symbol": sym, "title": "실적", "estimated": True}]})
    ex = portfolio_os.holding_extras(app, [sym], now)[sym]
    assert ex["news"]["n"] == 1 and ex["news"]["top"]["title"] == "대형 수주" and ex["earn"]["d_label"] == "D-4" and ex["earn"]["estimated"]
    ops.set_state(app.engine, "event_calendar", {})


# ------------------------------------------------------------------ 로고: 국내 출처 · 재시도 · 대소문자 · 미리 받기
def test_logo_kr_sources_retry_case_and_prefetch(app, monkeypatch):
    import urllib.error

    from quant_ai import logos
    syms = _syms(app)
    k1, k2 = syms[0], syms[1]
    urls = []

    def fetch_toss(url):
        urls.append(url)
        if "static.toss.im" in url:
            return PNG
        raise urllib.error.URLError("down")
    assert logos.get(app, k1, fetch=fetch_toss)[2] == "toss" and "icn-sec-fill-" + k1 in urls[0]
    # 네트워크 오류만 → 1시간 뒤 다시 · '없음'(404)이면 7일
    t0 = 1_000_000.0
    assert logos.get(app, k2, fetch=lambda u: (_ for _ in ()).throw(urllib.error.URLError("x")), now=t0)[2] == "default"
    meta = json.loads((logos._dir(app) / f"{k2}.json").read_text())
    assert meta["net"] is True
    n = []
    assert logos.get(app, k2, fetch=lambda u: n.append(u) or PNG, now=t0 + 1800)[2] == "default" and not n  # 30분 뒤: 아직 안 함
    assert logos.get(app, k2, fetch=lambda u: n.append(u) or PNG, now=t0 + 3700)[2] == "toss"  # 1시간 지나면 다시 받음

    def not_found(url):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    k3 = syms[2]
    logos.get(app, k3, fetch=not_found, now=t0)
    assert json.loads((logos._dir(app) / f"{k3}.json").read_text())["net"] is False
    assert logos.get(app, k3, fetch=lambda u: PNG, now=t0 + 7200)[2] == "default"  # 없음 → 7일
    assert logos.get(app, k3, fetch=lambda u: PNG, now=t0 + 7200, force=True)[2] == "toss"  # --retry
    # 소문자 파일 이름도 인식 · 이름 없이 불러도 종목 이름으로 이니셜
    (logos._dir(app) / "custom" / "tsla.png").write_bytes(PNG)
    assert logos.get(app, "TSLA", fetch=not_found)[2] == "custom"
    monkeypatch.setenv("QUANT_LOGO_SOURCES", "favicon")
    assert [c[0] for c in logos.candidates(app, "005930")] == ["favicon"]
    monkeypatch.delenv("QUANT_LOGO_SOURCES")
    monkeypatch.setenv("QUANT_LOGOS", "off")
    mono, _, src = logos.get(app, syms[3])
    assert src == "default" and b"<svg" in mono
    r = logos.prefetch(app, [k1, syms[3]])
    assert r["n"] == 2 and r["by_source"].get("default") == 1 and syms[3] in r["missing"]


# ------------------------------------------------------------------ 보안 · 오프라인 · 화면 파일
def test_totp_without_password_is_warned_and_assets_are_local(app, monkeypatch, server):
    from quant_ai import governance
    from quant_ai.auth import Auth
    monkeypatch.setenv("QUANT_WEB_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    monkeypatch.delenv("QUANT_WEB_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("QUANT_WEB_PASSWORD", raising=False)
    sec = {x["item"]: x for x in governance.security(app)["items"]}["로그인 · 2단계 인증(MFA)"]
    assert sec["status"] == "missing" and "비밀번호가 없음" in sec["detail"]
    assert Auth().config_errors()
    run = (ROOT / "run.sh").read_text()
    assert "시작하자마자 멈췄습니다" in run and "tail -n 5" in run  # 서버가 설정 오류로 바로 끝나면 이유를 화면에
    # 차트 라이브러리는 서버가 직접 (CDN 이 막혀도 차트가 그려지게) · CSP 에 외부 스크립트 없음
    st, body, h = _get(server, "/vendor/lightweight-charts.standalone.production.js")
    assert st == 200 and b"Lightweight Charts" in body[:400] and "script-src 'self';" in h["Content-Security-Policy"]
    html = (ROOT / "src/quant_ai/web/static/index.html").read_text()
    assert "cdn.jsdelivr.net/npm/lightweight-charts" not in html and "/easy.js" in html
    sw = (ROOT / "src/quant_ai/web/static/sw.js").read_text()
    assert "/easy.js" in sw and "/vendor/lightweight-charts.standalone.production.js" in sw

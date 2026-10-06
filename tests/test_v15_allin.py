"""v15: 올인원 종목 페이지(데이터 신뢰도 · 사전 리스크 게이트 · 왜 샀나/안 샀나 · 뉴스/공시 요약 · D-day) ·
AI 별 성능 저하(일별 검정) · AI Health · 사용자 설정(홈 구성 · 외부 알림 종류별 채널 · 조용한 시간) · 관심종목 홈 반영."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from quant_ai import ops


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v15")
    fake_marcap(d, n_codes=6, days=300)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def _syms(app):
    return [s for s in app.market_data()[0] if s[:1].isdigit()]


# ------------------------------------------------------------------ 데이터 신뢰도
def test_trust_flags_stale_secondary_and_halt(app):
    from quant_ai import stock
    from quant_ai.data.db import load_bars, session_scope, upsert_bars
    from quant_ai.data.sources import SECONDARY
    sym = _syms(app)[0]
    t = stock.trust(app, sym)
    keys = {c["key"]: c for c in t["checks"]}
    assert keys["fresh"]["status"] == "bad" and "거래일 밀림" in keys["fresh"]["detail"]  # 2020년 데이터
    assert keys["source"]["status"] == "ok" and keys["ai"]["status"] == "warn" and 0 <= t["score"] < 100
    # 2차 소스 봉 + 거래정지(가격 고정·거래량 0) 흉내
    with session_scope(app.engine) as s:
        b = load_bars(s, [sym])[sym]
        tail = b.iloc[-3:].copy()
        tail["close"] = tail["open"] = tail["high"] = tail["low"] = float(b["close"].iloc[-4])
        tail["volume"] = 0.0
        upsert_bars(s, sym, tail, "1d", SECONDARY)
    app._md_cache = None
    keys = {c["key"]: c for c in stock.trust(app, sym)["checks"]}
    assert keys["source"]["status"] == "warn" and SECONDARY in keys["source"]["detail"]
    assert keys["halt"]["status"] == "bad"
    assert stock.trust(app, "NOPE")["checks"][0]["status"] == "bad"


# ------------------------------------------------------------------ 사전 리스크 게이트
def test_pretrade_mirrors_trading_gates(app):
    from quant_ai import stock
    sym = _syms(app)[1]
    r = stock.pretrade(app, sym, 0.05, "paper")
    gates = [x["gate"] for x in r["steps"]]
    assert gates[:4] == ["HALTED", "긴급 정지", "매매 준비", "이벤트"]
    assert r["verdict"] == "통과" and r["allowed_qty"] == r["want_qty"] > 0  # 빈 paper 장부 · 5%
    assert next(x for x in r["steps"] if x["gate"] == "매매 준비")["status"] == "na"  # paper 는 게이트 미적용
    big = stock.pretrade(app, sym, 0.5, "paper")  # 종목 한도 10% 로 축소
    assert big["verdict"] == "축소" and big["allowed_weight"] <= app.settings.risk.max_position_weight + 0.01
    assert any("종목 비중 한도" in x["detail"] for x in big["steps"])
    sh = stock.pretrade(app, sym, 0.05, "shadow")  # shadow 는 게이트 적용 → 2020년 데이터라 NOT READY
    assert sh["verdict"] == "차단" and sh["allowed_qty"] == 0
    assert next(x for x in sh["steps"] if x["gate"] == "매매 준비")["status"] == "bad"
    assert sum(1 for x in sh["steps"] if "NOT READY" in x["detail"]) == 1  # 같은 사유를 두 번 쓰지 않음
    app.set_kill_switch(True, "test")
    try:
        k = stock.pretrade(app, sym, 0.05, "paper")
        assert k["verdict"] == "차단" and next(x for x in k["steps"] if x["gate"] == "긴급 정지")["status"] == "bad"
    finally:
        app.set_kill_switch(False)
    before = app._orders_today("paper", datetime.now(UTC))
    stock.pretrade(app, sym, 0.05, "paper")
    assert app._orders_today("paper", datetime.now(UTC)) == before  # 주문은 나가지 않는다
    assert "error" in stock.pretrade(app, "AAPL")


# ------------------------------------------------------------------ 왜 샀나 / 왜 안 샀나
def test_story_explains_bought_and_not_bought(app):
    from quant_ai import stock
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import JournalEntry
    a, b, c = _syms(app)[:3]
    now = datetime.now(UTC)
    ops.set_state(app.engine, "cs-plan:paper", {
        "core": [a], "ranks": {a: 0, b: 7}, "scores": {a: 1.2, b: 0.3}, "vetoed": {}, "exits": {}, "unaffordable": [c],
        "satellite": [], "universe_size": 5, "ts": now.isoformat(),
        "config": {"core_top_k": 4, "core_buffer_k": 6, "core_rebalance_days": 20, "use_ai": False}})
    with session_scope(app.engine) as s:
        s.add(JournalEntry(ts=now, mode="paper", kind="order", symbol=b, message=f"buy 10 {b} → rejected",
                           data={"status": "rejected", "reasons": ["업종 한도 IT 40% → 0주로 축소"], "reason": "CORE #8"}))
    sa = stock.story(app, a)
    assert "코어 편입" in sa["facts"][0] and "순위 #1" in sa["facts"][0] and sa["headline"].startswith("코어 대상")
    sb = stock.story(app, b)
    assert sb["headline"].startswith("사려고 했지만 막힘") and "업종 한도" in sb["headline"]
    assert any("#8" in f and "상위 4 밖" in f for f in sb["facts"]) and sb["timeline"][0]["status"] == "rejected"
    sc = stock.story(app, c)
    assert any("살 수 없음" in f for f in sc["facts"]) and any("코어 전용" in f for f in sc["facts"])
    assert "유니버스" in stock.story(app, "999999")["headline"]


# ------------------------------------------------------------------ 뉴스·공시 요약 · D-day
def test_digest_and_events(app):
    from quant_ai import stock
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Disclosure, NewsArticle
    sym = _syms(app)[2]
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        for i, (t, sent) in enumerate([("수주 대박", 0.8), ("신제품 호평", 0.5), ("실적 우려", -0.6), ("증권가 목표가 상향", 0.4)]):
            s.add(NewsArticle(source="rss", url=f"http://x/{i}", published_at=now - timedelta(days=i), title=t, symbols=[sym],
                              sentiment=sent, importance=0.7, events=["earnings"] if i == 2 else []))
        s.add(Disclosure(source="DART", receipt_no="r1", symbol=sym, title="자기주식취득 결정 (50억원)", filed_at=now.date(),
                         url="http://d/1", summary="자사주 50억원 매입"))
    g = stock.digest(app, sym)
    assert g["news_7d"] == 4 and g["pos"] == 3 and g["neg"] == 1 and g["tone"] == "긍정 우세"
    assert g["headlines"][0]["title"] == "수주 대박" and g["disclosures"][0]["summary"] == "자사주 50억원 매입"
    assert any("자기주식" in e["label"] or "자사주" in e["label"] for e in g["extracted"])
    assert any(x.startswith("최근 7일 뉴스 4건") for x in g["summary"])
    assert "저장된 뉴스" in stock.digest(app, "999999")["summary"][0]
    today = now.date()
    ops.set_state(app.engine, "event_calendar", {"at": now.isoformat(), "events": [
        {"date": (today + timedelta(days=2)).isoformat(), "kind": "earnings", "title": "실적 발표", "symbol": sym, "estimated": True, "importance": 0.9},
        {"date": (today + timedelta(days=1)).isoformat(), "kind": "fomc", "title": "FOMC", "market": "US", "importance": 1.0},
        {"date": (today + timedelta(days=5)).isoformat(), "kind": "options_expiry", "title": "옵션 만기", "market": "KR", "importance": 0.5},
        {"date": (today + timedelta(days=3)).isoformat(), "kind": "earnings", "title": "남의 실적", "symbol": "000000", "importance": 0.9}]})
    ev = stock.events(app, sym)
    # 가까운 순 · 다른 종목 제외 · v16: 옵션 만기·FOMC 같은 시장 일정은 중요도와 무관하게 항상 표시 (D-n 로)
    assert [e["title"] for e in ev] == ["FOMC", "실적 발표", "옵션 만기"]
    assert ev[2]["d_day"] - ev[0]["d_day"] == 4  # D-day 는 한국 날짜 기준 (UTC 자정~KST 9시 사이 실행에도 안전)
    assert all(e["d_label"] == ("D-Day" if e["d_day"] == 0 else f"D-{e['d_day']}") for e in ev)
    assert ev[1]["scope"] == "종목" and ev[1]["d_label"].startswith("D-") and ev[1]["estimated"]
    p = stock.page(app, sym)
    assert {"symbol", "trust", "story", "digest", "events"} <= set(p)
    assert {"header", "situation", "freshness", "position", "thesis", "verify"} <= set(p)  # v16 Stock OS


# ------------------------------------------------------------------ AI 별 성능 저하 (일별 검정)
def test_decay_uses_daily_groups_for_same_day_predictions():
    from quant_ai.review.decay import analyze
    rng = np.random.default_rng(7)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    def rows(p1, p2, ndays=100, per=80):
        out = []
        for d in range(ndays):
            dp = np.clip((p1 if d < ndays // 2 else p2) + rng.normal(0, 0.15), 0.02, 0.98)
            out += [{"as_of": t0 + timedelta(days=d), "hit": rng.random() < dp, "prob": 0.6} for _ in range(per)]
        return out
    stable = [analyze(rows(0.52, 0.52), None, "x")["status"] for _ in range(30)]
    assert stable.count("decaying") <= 1 and stable.count("stable") >= 24  # 건별 검정은 이런 데이터에서 거의 항상 경보
    r = analyze(rows(0.6, 0.44), None, "x")
    assert r["status"] == "decaying" and r["daily"] and r["days"] == 100
    few = analyze(rows(0.5, 0.5, ndays=10), None, "x")
    assert few["status"] == "insufficient" and "20거래일" in few["message"]


def test_per_ai_decay_alert_and_health(app):
    from quant_ai import desk
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import AlertRecord, AnalystOpinionRecord, ConsensusRecord
    from quant_ai.health import ai_health
    rng = np.random.default_rng(3)
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        for d in range(60):
            day = now - timedelta(days=60 - d)
            for an, p in (("primary", 0.6 if d < 30 else 0.4), ("nvidia", 0.55)):
                dp = np.clip(p + rng.normal(0, 0.05), 0, 1)
                for k in range(30):
                    hit = rng.random() < dp
                    s.add(AnalystOpinionRecord(consensus_id=None, analyst=an, symbol=f"{k:06d}", as_of=day, horizon_bars=5,
                                               category="direction", prob_up=0.6, confidence=0.5, veto=False,
                                               realized_return=0.01 if hit else -0.01, correct=hit))
        s.add(ConsensusRecord(symbol="000001", as_of=now - timedelta(days=1), action="HOLD", prob_up=0.5, confidence=50,
                              conflict="low", payload={}))
    r = desk.model_decay(app)
    assert r["analysts"]["primary"]["status"] == "decaying" and r["decaying_ais"] == ["primary"]
    assert r["analysts"]["nvidia"]["status"] != "decaying"
    with session_scope(app.engine) as s:
        assert s.query(AlertRecord).filter(AlertRecord.title == "AI 성능 저하: 뉴스 AI").count() == 1
    desk.model_decay(app)  # 이미 알린 저하는 다시 알리지 않음
    with session_scope(app.engine) as s:
        assert s.query(AlertRecord).filter(AlertRecord.title.like("AI 성능 저하%")).count() == 1
    h = ai_health(app)
    by = {a["analyst"]: a for a in h["analysts"]}
    assert h["status"] == "bad" and h["analysts"][0]["analyst"] == "primary"  # 저하가 맨 위
    assert by["primary"]["decay"] == "decaying" and by["primary"]["n_scored"] == 1800 and not by["primary"]["stale"]
    assert by["primary"]["hit_recent"] < by["primary"]["hit_ref"] - 0.1 and by["primary"]["days"] == 60
    assert any("뉴스 AI: 성능 저하" in p for p in h["problems"])
    assert [c["key"] for c in h["model"]["checks"]] == ["MODEL", "DRIFT", "CALIBRATION"]


# ------------------------------------------------------------------ 사용자 설정 · 알림 경로
def test_prefs_validate_and_route_notifications(app, monkeypatch):
    from quant_ai import alerts, prefs
    with pytest.raises(ValueError):
        prefs.save(app.engine, {"notify": {"quiet": {"on": True, "start": "25:00"}}})
    p = prefs.save(app.engine, {"home": {"hidden": ["system", "bogus"], "order": ["watch", "today", "watch"]}})
    assert p["home"] == {"hidden": ["system"], "order": ["watch", "today"]}
    sent, pushed = [], []

    class N:
        def send(self, text, level):
            sent.append((text, level))

    monkeypatch.setitem(alerts.ROUTE, "notifier", N())
    monkeypatch.setitem(alerts.ROUTE, "push", lambda t, b, link: pushed.append(t))
    monkeypatch.setitem(alerts.ROUTE, "kinds", {"rule"})
    monkeypatch.setitem(alerts.ROUTE, "push_kinds", {"price"})
    alerts.push(app.engine, "signal", "신호 A")  # 기본값: signal 은 외부로 안 보냄
    assert sent == [] and pushed == []
    prefs.save(app.engine, {"notify": {"external": {"signal": True}, "push": {"price": False}}})
    alerts.push(app.engine, "signal", "신호 B")
    alerts.push(app.engine, "price", "급등 C")
    assert [x[0] for x in sent] == ["신호 B"] and pushed == []  # 사용자가 켠 것만 · 끈 것은 안 보냄
    # 조용한 시간: 외부 보류, 긴급은 예외 · 사이트 알림은 그대로 저장
    now_kst = datetime.now(prefs.KST)
    start, end = (now_kst - timedelta(minutes=5)).strftime("%H:%M"), (now_kst + timedelta(minutes=5)).strftime("%H:%M")
    prefs.save(app.engine, {"notify": {"external": {"signal": True, "guardian": True}, "quiet": {"on": True, "start": start, "end": end}}})
    assert alerts.push(app.engine, "signal", "밤 신호") is not None
    alerts.push(app.engine, "guardian", "긴급 정지", level="bad")
    assert [x[0] for x in sent] == ["신호 B", "긴급 정지"] and pushed == ["긴급 정지"]  # 웹 푸시는 긴급이면 항상
    assert prefs.in_quiet({"on": True, "start": "23:00", "end": "07:00"}, datetime(2026, 1, 1, 2, 0, tzinfo=prefs.KST))
    assert not prefs.in_quiet({"on": True, "start": "23:00", "end": "07:00"}, datetime(2026, 1, 1, 12, 0, tzinfo=prefs.KST))


def test_dashboard_shows_starred_first(app):
    from quant_ai import ux
    from quant_ai.web.api import DashboardAPI
    syms = _syms(app)
    last = syms[-1]
    ux.set_star(app, last, True)
    d = DashboardAPI(app).dashboard()
    first = d["watchlist"][0]
    assert first["symbol"] == last and first["starred"] and not any(w["starred"] for w in d["watchlist"][1:])


def test_v15_routes(app):
    import json
    import threading
    from http.client import HTTPConnection
    from http.server import ThreadingHTTPServer

    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), None, {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def call(method, path, body=None):
        c = HTTPConnection("127.0.0.1", port, timeout=60)
        h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", "Host": f"127.0.0.1:{port}"}
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        return r.status, json.loads(r.read())

    try:
        sym = _syms(app)[0]
        st, p = call("GET", f"/api/stock?symbol={sym}")
        assert st == 200 and p["trust"]["checks"]
        pt = call("GET", f"/api/pretrade?symbol={sym}&weight=5")
        assert pt[0] == 200 and pt[1].get("verdict") in ("통과", "축소", "차단"), pt
        assert call("GET", f"/api/pretrade?symbol={sym}&weight=500")[0] == 400
        assert call("GET", f"/api/stock?symbol={sym}&mode=hack")[0] == 400
        st, h = call("GET", "/api/health/ai")
        assert st == 200 and "analysts" in h
        st, pr = call("POST", "/api/prefs", {"home": {"hidden": ["live"], "order": []}})
        assert st == 200 and pr["home"]["hidden"] == ["live"]
        st, g = call("GET", "/api/prefs")
        assert g["home"]["hidden"] == ["live"] and "price" in g["kinds"] and "channels" in g
        assert call("POST", "/api/prefs", {"notify": {"quiet": {"on": True, "start": "99:99"}}})[0] == 400
    finally:
        httpd.shutdown()


def test_event_caps_use_todays_dday_not_stored_one():
    """캘린더를 어제 만들었으면 저장된 d_day 는 하루 어긋난다 — 주문하는 날 기준으로 다시 계산해야 실적 D-1 축소가 걸린다."""
    from datetime import date

    from quant_ai.engines.events import event_caps
    today = date(2026, 10, 6)  # 화요일
    stale = [{"date": "2026-10-07", "kind": "earnings", "title": "실적", "symbol": "005930", "market": "KR", "importance": 0.9,
              "d_day": 2, "d_label": "D-2", "trading_days": 2, "label": "실적"}]  # 어제(10/5) 만든 기록
    caps = event_caps(["005930"], stale, today, "reduce")
    assert caps["005930"][0] == 0.5 and "D-1" in caps["005930"][1]
    smart = event_caps(["005930"], stale, today)  # v19 기본: 실적 D-1 이내는 신규 매수 보류
    assert smart["005930"][0] == 0.0 and "신규 매수 보류" in smart["005930"][1]
    assert stale[0]["d_day"] == 2  # 원본은 건드리지 않음

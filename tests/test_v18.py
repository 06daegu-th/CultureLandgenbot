"""v18: 종목 로고 · 뉴스/공시 상세(원문·번역·쉬운 설명·중요도·영향 종목·AI 판단 변화) · 종목 '오늘 중요한 것' · 홈 5칸."""

import json
import threading
from dataclasses import replace
from datetime import timedelta
from http.client import HTTPConnection

import pandas as pd
import pytest

from quant_ai import ops

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v18")
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


# ------------------------------------------------------------------ 로고
def test_logo_sources_cache_fallback_and_safety(app):
    from quant_ai import logos
    calls = []

    def fetch(url):
        calls.append(url)
        if "financialmodelingprep" in url and "NVDA" in url:
            return PNG
        if "samsung.com" in url:
            return PNG
        if "evil" in url:
            return b"<svg onload=alert(1)>" + b" " * 400  # 외부 SVG 는 받지 않는다
        raise OSError("blocked")

    d, t, src = logos.get(app, "NVDA", "NVIDIA", fetch=fetch)
    assert t == "image/png" and src == "fmp" and d == PNG
    assert logos.get(app, "NVDA", fetch=fetch)[2] == "fmp" and len(calls) == 1  # 저장된 것 재사용
    d, t, src = logos.get(app, "005930", "삼성전자", fetch=fetch)
    assert src == "favicon" and "samsung.com" in calls[-1]
    ops.set_state(app.engine, "profile:EVIL", {"data": {"company": {"website": "https://evil.example"}}})
    d, t, src = logos.get(app, "EVIL", "Evil Corp", fetch=fetch)
    assert src == "monogram" and t == "image/svg+xml" and b"onload" not in d and b"EV" in d
    n = len(calls)
    assert logos.get(app, "EVIL", fetch=fetch)[2] == "monogram" and len(calls) == n  # 실패는 7일간 다시 시도 안 함
    mono = logos.monogram("000020", "종목1")
    assert b"\xec\xa2\x85" in mono  # '종'
    (logos._dir(app) / "custom" / "000030.png").write_bytes(PNG)
    assert logos.get(app, "000030", fetch=fetch)[2] == "custom"
    with pytest.raises(ValueError):
        logos.get(app, "../../etc", fetch=fetch)


def test_logo_route_is_public_and_cached(app, server, monkeypatch):
    monkeypatch.setenv("QUANT_LOGOS", "off")
    st, body, h = _get(server, "/api/logo/ZZZZ?n=Zeta%20Corp")
    assert st == 200 and h["Content-Type"] == "image/svg+xml" and body.startswith(b"<svg") and "max-age" in h["Cache-Control"]
    assert _get(server, "/api/logo/..%2F..")[0] in (200, 400)


# ------------------------------------------------------------------ 뉴스·공시 상세
def _add_news(app, rows):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import NewsArticle
    ids = []
    with session_scope(app.engine) as s:
        for i, (title, body, at, syms, src, cl) in enumerate(rows):
            a = NewsArticle(source=src, url=f"https://n.example/{i}/{abs(hash(title))}", published_at=at, title=title, body=body,
                            symbols=syms, sentiment=0.5, importance=0.8, cluster=cl, events=["earnings"])
            s.add(a)
            s.flush()
            ids.append(a.id)
    return ids


def test_news_detail_times_terms_level_chain_and_explain(app):
    from quant_ai import newsdetail
    sym = _syms(app)[0]
    at = pd.Timestamp(app.market_data()[0][sym].index[-15]).to_pydatetime().replace(hour=19, minute=32)
    ids = _add_news(app, [("Nvidia beats EPS estimates, raises guidance on HBM demand",
                           "EPS $1.12 vs $0.98 expected. Revenue $35.1 billion, up 94%. Export control risk remains.", at, [sym], "Reuters", "cx1"),
                          ("엔비디아 실적 예상 상회", "", at + timedelta(minutes=20), [sym], "연합뉴스", "cx1")])
    d = newsdetail.news_detail(app, ids[0])
    assert d["lang"] == "en" and d["time"]["text"] == f"{d['time']['et'][:-3]} ET · 한국시간 {d['time']['kst'][5:16].replace('-', '/')}"
    assert {"EPS", "guidance", "HBM", "export control"} <= {t["term"] for t in d["terms"]}
    assert "$1.12" in " ".join(n["value"] for n in d["numbers"]) and "94%" in " ".join(n["value"] for n in d["numbers"])
    assert d["level"]["icon"] in ("🔴", "🟠") and d["chain"][0] == {"symbol": sym, "name": d["chain"][0]["name"], "level": "🔴", "why": "기사에 직접 나옴"}
    assert d["siblings"][0]["source"] == "연합뉴스" and d["explain"]["by"] == "rule" and "AI 설명" in d["explain"]["note"]
    assert d["related"][0]["symbol"] == sym and "before_5d" in d["related"][0]

    class Fake:
        def complete_json(self, system, text, schema):
            assert "외부 데이터" in system and "Nvidia" in text
            return {"ko_title": "엔비디아, EPS 예상 상회·가이던스 상향", "ko_text": "EPS 1.12달러로 예상 0.98달러를 넘었다.",
                    "easy": "돈을 예상보다 많이 벌었고 앞으로도 더 벌 거라고 했어요.", "impact": "단기 긍정적일 수 있음",
                    "numbers": [{"label": "EPS", "value": "$1.12"}, "bad"], "tone": 0.7}
    r = newsdetail.explain_news(app, ids[0], client=Fake())
    assert r["by"] == "llm" and r["translation"].startswith("EPS") and r["numbers"] == [{"label": "EPS", "value": "$1.12"}]
    d2 = newsdetail.news_detail(app, ids[0])
    assert d2["explain"]["ko_title"].startswith("엔비디아") and d2["numbers"][0]["label"] == "EPS"  # 저장된 설명 재사용
    assert newsdetail.explain(app, "x", "", client=None)["error"].startswith("LLM 키")
    assert newsdetail.lang("トヨタ決算") == "ja" and newsdetail.lang("삼성전자 실적") == "ko"
    lo = newsdetail.level(0.2, "other", 0.0)
    hi = newsdetail.level(0.9, "earnings", 0.8, 4, 1.0, True)
    assert lo["icon"] == "⚪" and hi["icon"] == "🔴"
    found = newsdetail.search(app, "nvidia HBM", days=3650)
    assert [x["id"] for x in found] == [ids[0]] and newsdetail.search(app, "없는단어zz", 3650) == []


def test_disclosure_detail_and_routes(app, server):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Disclosure
    sym = _syms(app)[0]
    with session_scope(app.engine) as s:
        d = Disclosure(source="DART", receipt_no="R18", symbol=sym, title="단일판매·공급계약체결 (계약금액 1,234억원, 매출액 대비 15.2%)",
                       filed_at=pd.Timestamp(app.market_data()[0][sym].index[-5]).date(), url="https://dart.example/R18", sentiment=0.4,
                       events=["contract"], summary="공급계약 1,234억원")
        s.add(d)
        s.flush()
        did = d.id
    c = HTTPConnection("127.0.0.1", server, timeout=30)
    c.request("GET", f"/api/disclosure/{did}")
    body = json.loads(c.getresponse().read())
    assert body["kind"] == "disclosure" and body["important"] and "1,234억원" in " ".join(n["value"] for n in body["numbers"])
    assert {"공급계약"} <= {t["term"] for t in body["terms"]} and "한국 공시일" in body["time"]["text"]
    c.request("GET", "/api/news-search?q=nvidia&days=3650")
    assert json.loads(c.getresponse().read())["results"]
    c.request("GET", "/api/news/999999")
    assert json.loads(c.getresponse().read())["error"] == "뉴스 없음"


# ------------------------------------------------------------------ 종목 페이지: 오늘 중요한 것 · 실적 배너 · 기간별 확률 · 차트
def test_stock_today_banner_horizons_and_chart_extras(app, monkeypatch):
    import numpy as np

    from quant_ai import stockplus
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord, Disclosure
    sym = _syms(app)[0]
    b = app.market_data()[0][sym]
    now = pd.Timestamp(b.index[-1]).to_pydatetime() + timedelta(hours=12)
    ops.set_state(app.engine, "event_calendar", {"at": now.isoformat(), "events": [
        {"date": (now + timedelta(days=3)).date().isoformat(), "kind": "earnings", "symbol": sym, "title": "3분기 실적", "market": "KR",
         "time": "장 마감 후"}]})
    with session_scope(app.engine) as s:
        s.add(Disclosure(source="DART", receipt_no="T18a", symbol=sym, title="유상증자 결정", filed_at=now.date(), url="https://x/1"))
        rng = np.random.default_rng(3)
        for i in range(60):  # 확률 0.60 근처 과거 판단 60개 · 1일 뒤 오른 비율 2/3
            up = i % 3 != 0
            s.add(ConsensusRecord(symbol=sym, as_of=now - timedelta(days=200 - i), action="BUY", prob_up=0.6 + rng.uniform(-0.02, 0.02),
                                  confidence=60, conflict="low", payload={"outcomes": {"1": 0.01 if up else -0.01, "5": 0.02, "20": -0.03}},
                                  realized_return=0.01, correct=up))
        s.add(ConsensusRecord(symbol=sym, as_of=now - timedelta(days=1), action="HOLD", prob_up=0.5, confidence=50, conflict="low", payload={}))
        s.add(ConsensusRecord(symbol=sym, as_of=now, action="BUY", prob_up=0.6, confidence=60, conflict="low", payload={}))
    h = stockplus.header(app, sym, now)
    assert h["earnings_banner"] == f"{sym} 실적 발표 D-3 · {(now + timedelta(days=3)).month}월 {(now + timedelta(days=3)).day}일 · 장 마감 후 예정"
    texts = [x["text"] for x in h["today"]]
    assert "실적 D-3 · 장 마감 후" in texts and texts[0] == "중요 공시 1건 (3일)" and "AI 신호 변화 HOLD → BUY" in texts
    hz = {x["h"]: x for x in h["horizons"]}
    assert hz[1]["n"] >= 60 and abs(hz[1]["p"] - 0.667) < 0.02 and hz[5]["p"] == 1.0 and hz[20]["p"] == 0.0
    assert stockplus.horizon_probs(app, 0.95)[0]["p"] is None  # 표본 부족 → 표시 안 함
    ov = stockplus.overlay(app, sym)
    kinds = {x["kind"] for x in ov["lines"]}
    assert {"high52", "low52", "range_hi", "range_lo"} <= kinds
    hi = next(x for x in ov["lines"] if x["kind"] == "high52")["price"]
    assert hi == round(float(b["close"].iloc[-252:].max()), 2)
    v = b.copy()
    v.loc[v.index[-3], "volume"] = float(v["volume"].iloc[-30:-3].mean()) * 5
    assert stockplus.volume_spikes(v)[-1]["date"] == str(pd.Timestamp(v.index[-3]).date())
    marks = stockplus.macro_marks(app, "AAPL", pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-03-31", tz="UTC"))
    assert {"fomc", "nfp"} <= {m["event"] for m in marks}
    assert "nfp" not in {m["event"] for m in stockplus.macro_marks(app, sym, pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-03-31", tz="UTC"))}
    ops.set_state(app.engine, "event_calendar", {})


# ------------------------------------------------------------------ 홈 5칸 · AI 상태 · 거래소 상태
def test_clock_light_holiday_notice_and_dst():
    from datetime import UTC, datetime

    from quant_ai.clock import clock_status, holiday_ko
    m = clock_status(datetime(2026, 7, 3, 15, tzinfo=UTC))["markets"]  # 미국 독립기념일 대체 휴장 · 한국은 토요일
    assert m["US"]["light"] == "🔴" and m["US"]["notice"].startswith("오늘 미국 증시는 휴장입니다 (독립기념일 대체 휴장)")
    assert "다음 개장 07/06 22:30 한국시간" in m["US"]["notice"] and m["US"]["dst"] is True
    assert m["KRX"]["notice"].startswith("오늘 한국 증시는 휴장입니다 (주말)") and m["KRX"]["dst"] is None
    m = clock_status(datetime(2026, 1, 15, 15, tzinfo=UTC))["markets"]  # 미국 정규장 · 겨울(서머타임 아님)
    assert m["US"]["light"] == "🟢" and m["US"]["notice"] is None and m["US"]["dst"] is False
    assert holiday_ko("Christmas Day") == "성탄절" and holiday_ko("Hurricane Sandy") == "Hurricane Sandy"


def test_ai_state_mapping(app, monkeypatch):
    from quant_ai import aitrack, center

    def fake(level, dem=False):
        return lambda a: {"trust": {"level": level, "reasons": ["표본 부족"]}, "demotion": {"on": dem}, "last": {"accuracy": 0.55, "base": 0.5, "n": 80}}
    for level, dem, key in [("CANDIDATE", False, "verified"), ("CANDIDATE", True, "banned"), ("UNTRUSTED", False, "banned"),
                            ("NO_DATA", False, "checking"), ("WATCH", False, "checking")]:
        monkeypatch.setattr(aitrack, "report", fake(level, dem))
        s = center.ai_state(app)
        assert s["key"] == key and s["icon"] == center.AI_STATE[key][0] and s["accuracy"] == 0.55


def test_home5_sections_and_route(app, server, monkeypatch):
    from quant_ai import center
    h = center.home5(app, "paper")
    assert {"market", "assets", "ai", "news", "todo", "as_of"} <= set(h)
    assert {m["name"] for m in h["market"]["markets"]} and all(m["light"] in ("🟢", "🟡", "🔴") for m in h["market"]["markets"])
    assert h["assets"]["mode"] == "paper" and h["assets"]["equity"] > 0
    assert h["ai"]["key"] in center.AI_STATE and isinstance(h["news"]["items"], list) and isinstance(h["todo"]["items"], list)
    # 한 칸이 실패해도 나머지는 보인다
    monkeypatch.setattr(center, "action_center", lambda *a, **k: 1 / 0)
    h = center.home5(app, "paper")
    assert "ZeroDivisionError" in h["todo"]["error"] and "error" not in h["assets"]
    monkeypatch.undo()
    st, body, _ = _get(server, "/api/home5?mode=paper")
    assert st == 200 and set(json.loads(body)) >= {"market", "assets", "ai", "news", "todo"}


def test_portfolio_rows_show_ai_badge(app):
    from datetime import UTC, datetime

    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord, Instrument, PortfolioSnapshot
    from quant_ai.web.api import DashboardAPI
    a, b = _syms(app)[:2]
    bars = app.market_data()[0]
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        s.add(PortfolioSnapshot(mode="shadow", ts=now, cash=1e6, equity=3e6,
                                positions={a: {"qty": 10, "avg_price": 1000.0}, b: {"qty": 5, "avg_price": 2000.0}}))
        s.add(ConsensusRecord(symbol=a, as_of=now - timedelta(days=2), action="HOLD", prob_up=0.5, confidence=50, conflict="low", payload={}))
        s.add(ConsensusRecord(symbol=a, as_of=now, action="BUY", prob_up=0.64, confidence=60, conflict="low", payload={}))
    with session_scope(app.engine) as s:
        inst = {i.symbol: i for i in s.query(Instrument).all()}
        pf = DashboardAPI(app)._portfolio(s, "shadow", bars, inst)
    rows = {p["symbol"]: p for p in pf["positions"]}
    assert rows[a]["ai"]["action"] == "BUY" and rows[a]["ai"]["icon"] == "🟢" and rows[a]["ai"]["prob_up"] == 0.64
    assert rows[b]["ai"] is None

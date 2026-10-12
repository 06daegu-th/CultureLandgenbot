"""v39 미국 실적 캘린더 · 오늘 밤 미국장 브리핑 · 판단 시각 · AI 연결 시험 · 웹 검색 캐시/한도 · 채팅 UX.

인터넷이 없는 환경이라 Nasdaq·웹 검색·모델 호출은 모두 가짜 응답으로 시험한다.
"""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from quant_ai import ops

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


def cal_payload(rows):
    return json.dumps({"data": {"rows": rows}}).encode()


NVDA = {"symbol": "NVDA", "name": "NVIDIA Corporation", "time": "time-after-hours", "epsForecast": "$0.95", "lastYearEPS": "$0.81",
        "noOfEsts": "18", "marketCap": "$4,400,000,000,000", "fiscalQuarterEnding": "Oct/2026"}
SMALL = {"symbol": "TINY", "name": "Tiny Co", "time": "time-pre-market", "epsForecast": "($0.10)", "marketCap": "$120,000,000"}
JPM = {"symbol": "JPM", "name": "JPMorgan Chase", "time": "time-pre-market", "epsForecast": "$4.10", "marketCap": "$600,000,000,000"}


@pytest.fixture(scope="module")
def mapp(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v39")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0,
                        news_feeds=[], dart_api_key=None, fred_api_key=None, llm_providers={}, anthropic_enabled=False))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


# ------------------------------------------------------------------ 1. 미국 실적 캘린더
def test_parse_nasdaq_calendar():
    from quant_ai.earncal import parse
    rs = parse(json.loads(cal_payload([NVDA, SMALL])), date(2026, 10, 12))
    n = rs[0]
    assert n["symbol"] == "NVDA" and n["time"] == "장 마감 후" and n["eps_estimate"] == 0.95 and n["market_cap"] == 4.4e12 and n["n_est"] == 18
    assert rs[1]["eps_estimate"] == -0.10 and rs[1]["time"] == "장 시작 전"


def test_collect_keeps_big_and_mine_and_survives_offline(mapp):
    from quant_ai import earncal
    from quant_ai.actions import add_watch
    add_watch(mapp, "NVDA")
    now = datetime(2026, 10, 12, 2, tzinfo=UTC)  # 미국 일요일 밤
    seen = []

    def get(url):
        seen.append(url)
        d = url.rsplit("=", 1)[1]
        return cal_payload([NVDA, SMALL, JPM] if d == "2026-10-14" else [])
    r = earncal.collect(mapp, days=7, get=get, now=now)
    assert r["n"] == 2 and r["days"] == 5 and all("2026-10-1" in u for u in seen)  # 거래일만 (토·일 건너뜀)
    syms = {x["symbol"] for x in earncal.rows(mapp)}
    assert syms == {"NVDA", "JPM"}  # 작은 회사는 저장 안 함
    off = earncal.collect(mapp, days=7, get=lambda u: (_ for _ in ()).throw(OSError("URLError: offline")), now=now)
    assert off["n"] == 2 and off["errors"]  # 못 받으면 지난 결과 유지


def test_radar_and_stock_events_use_calendar(mapp):
    from quant_ai import radar
    from quant_ai.stock import events
    now = datetime(2026, 10, 12, 2, tzinfo=UTC)
    ops.set_state(mapp.engine, "earnings_cal_us", {"at": now.isoformat(), "rows": [
        {"date": "2026-10-14", "symbol": "NVDA", "name": "NVIDIA", "time": "장 마감 후", "eps_estimate": 0.95, "market_cap": 4.4e12},
        {"date": "2026-10-14", "symbol": "JPM", "name": "JPMorgan", "time": "장 시작 전", "eps_estimate": 4.1, "market_cap": 6e11}]})
    r = radar.upcoming(mapp, days=7, now=now)
    nv = next(e for e in r["rows"] if e["symbol"] == "NVDA")
    assert nv["scope"] == "내 종목" and nv["kst"] == "10/15(목) 새벽 05:10경" and "EPS 예상 0.95" in nv["detail"]
    jp = next(e for e in r["rows"] if e["symbol"] == "JPM")
    assert jp["scope"] == "주요 기업" and "밤 20:30경" in jp["kst"]  # 장 시작 전 = 07:30 ET
    assert r["earncal"]["n"] == 2
    ev = [e for e in events(mapp, "NVDA", now) if e["kind"] == "earnings" and e["scope"] == "종목"]
    assert ev and ev[0]["date"] == "2026-10-14" and ev[0]["time"] == "장 마감 후"


# ------------------------------------------------------------------ 2. 오늘 밤 미국장 브리핑
def test_us_brief_and_push_once(mapp):
    from quant_ai import radar
    now = datetime(2026, 10, 12, 11, 45, tzinfo=UTC)  # 월 20:45 KST · 오늘 밤 22:30 개장
    b = radar.us_brief(mapp, now)
    assert b["open_kst"] == "10/12 22:30" and b["lines"][0].startswith("개장 오늘 밤 22:30")
    assert radar.us_brief_push(mapp, now) == 1 and radar.us_brief_push(mapp, now) == 0  # 하루 한 번
    assert radar.us_brief_push(mapp, datetime(2026, 10, 12, 6, tzinfo=UTC)) == 0  # 15:00 KST — 시간 아님
    assert radar.us_brief_push(mapp, datetime(2026, 10, 10, 11, 45, tzinfo=UTC)) == 0  # 토요일 밤 — 미국장 없음


def test_chat_routes_us_brief(mapp):
    from quant_ai.assistant import reply, route
    names = [n for n, _ in route(mapp, "오늘 밤 미국장 브리핑 해줘")]
    assert names == ["us_brief"]
    r = reply(mapp, "오늘 밤 미국장 어때?", "b1", client_factory=lambda st, plan=None: (None, None))
    assert "미국장 브리핑" in r["answer"] and r["links"][0]["href"] == "#radar"


# ------------------------------------------------------------------ 3. 판단 시각
def test_verdict_shows_judged_time_separately(mapp):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    from quant_ai.explain import verdict
    sym = next(s for s in mapp._all_bars()[0] if s[:1].isdigit())
    bar = mapp._all_bars()[0][sym].index[-1]
    with session_scope(mapp.engine) as s:
        s.add(ConsensusRecord(symbol=sym, as_of=bar.to_pydatetime(), action="HOLD", prob_up=0.54, confidence=46, conflict="low",
                              payload={"horizon": 5}, created_at=bar.to_pydatetime() + timedelta(days=3, hours=1, minutes=53)))
    v = verdict(mapp, sym)
    assert v["judged_at"].endswith("10:53 KST") and v["basis"].endswith("종가 기준") and v["judged_at"] != v["as_of"]


# ------------------------------------------------------------------ 4. 웹 검색 캐시 · 회원 한도 · 연결 시험
def test_search_cache_and_member_quota(mapp, monkeypatch):
    from quant_ai import tenancy, websearch
    from quant_ai.assistant import Tools
    calls = []

    def fake(url, *a, **k):
        calls.append(url)
        return b"<rss><item><title>X - Y</title><link>https://a/1</link><source>Y</source></item></rss>"
    monkeypatch.setattr(websearch, "_get", fake)
    websearch._CACHE.clear()
    a = websearch.search("캐시 시험 v39")
    n_before = len(calls)
    b = websearch.search("캐시 시험 v39")
    assert a["results"] and b.get("cached") and len(calls) == n_before
    websearch.search("캐시 시험 v39")
    assert len(calls) == n_before  # 세 번째도 캐시
    monkeypatch.setattr(tenancy, "is_member", lambda: True)
    monkeypatch.setattr(Tools, "WEB_DAILY_MEMBER", 2)
    monkeypatch.setattr(Tools, "_blocked", lambda self: frozenset())
    t = Tools(mapp, web=True)
    assert t.t_web_search("캐시 시험 v39")["results"] and t.t_web_search("캐시 시험 v39")["results"]
    over = t.t_web_search("캐시 시험 v39")
    assert over["results"] == [] and "다 썼어요" in over["error"]
    websearch._CACHE.clear()


def test_selftest_reports_each_provider_without_keys_shown(mapp):
    from quant_ai import aimodels
    mapp.settings = replace(mapp.settings, llm_providers={"gemini": {"key": "SECRET-KEY-1234", "models": ("m",), "daily": 10},
                                                          "groq": {"key": "x", "models": ("m",), "daily": 10}})
    try:
        class Ok:
            models = ("gemini-flash-latest",)

            def _post(self, path, body):
                assert body["max_tokens"] == 8
                return {"choices": [{"message": {"content": "OK"}}]}

        def factory(settings, plan):
            if plan["provider"] == "groq":
                raise OSError("HTTP Error 401: Unauthorized")
            return Ok(), plan["provider"]
        post = lambda url, body: {"candidates": [{"content": {"parts": [{"text": "뉴스"}]}, "groundingMetadata": {"groundingChunks": [{"web": {"uri": "u", "title": "t"}}]}}]}  # noqa: E731
        st = aimodels.selftest(mapp, client_factory=factory, post=post,
                               search=lambda *a, **k: {"results": [{"title": "NVIDIA earnings on Nov 18"}]})
        rows = {r["name"]: r for r in st["rows"]}
        assert rows["Google Gemini"]["ok"] and "응답함" in rows["Google Gemini"]["detail"]
        assert not rows["Groq"]["ok"] and "키" in rows["Groq"]["detail"]
        assert rows["Gemini Google 검색 근거"]["ok"] and "출처 1개" in rows["Gemini Google 검색 근거"]["detail"]
        assert st["headline"].startswith("1개 안 됨") and "SECRET" not in json.dumps(st)
        assert aimodels.catalog(mapp.settings, mapp.engine)["selftest"]["n"] == st["n"]
    finally:
        mapp.settings = replace(mapp.settings, llm_providers={})


# ------------------------------------------------------------------ 5. 화면
def test_static_v39():
    pro = (STATIC / "pro.js").read_text()
    t2 = (STATIC / "toss2.js").read_text()
    toss = (STATIC / "toss.js").read_text()
    assert "chat-el" in pro and "chat-copy" in pro and "chat-retry" in pro
    assert 'runAction("earnings_cal_us"' in t2 and "/api/us-brief" in t2 and 'runAction("ai_selftest"' in t2
    assert "v.judged_at" in toss and "s.signal.judged_at" in (STATIC / "allin.js").read_text()


def test_api_us_brief(mapp):
    from quant_ai.web.api import DashboardAPI
    b = DashboardAPI(mapp).us_brief()
    assert b["title"].startswith("오늘 밤 미국장 브리핑") and b["lines"]
    from quant_ai import service
    assert "/api/us-brief" in service.MEMBER_GET

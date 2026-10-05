"""v26: 나머지 화면 토스식 공통 스킨 · 영어 꼬리표 우리말 · 로고(미국 묶음·첫 글자 아이콘) · 커뮤니티 수집 보강 · 종목별 뉴스 · 대시보드 즉시 응답."""

import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v26")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


# ------------------------------------------------------------------ 로고
def test_us_logo_bundle_and_cdn_source(app):
    from quant_ai import logos
    from quant_ai.companies import bundled_logo
    data, ctype, src = logos.get(app, "ROKU", fetch=lambda u: (_ for _ in ()).throw(OSError("network")))
    assert src == "bundled" and ctype == "image/webp" and data[:4] == b"RIFF"  # 네트워크 없이도 미국 인기 종목은 진짜 로고
    assert bundled_logo("BRK.B")  # 클래스 주식: 점 → 하이픈
    assert bundled_logo("005930")  # 국내 내장 SVG 는 그대로
    first = logos.candidates(app, "ZYXI")[0]
    assert first[0] == "usl" and "cdn.jsdelivr.net/npm/us-stock-logos" in first[1] and first[1].endswith("/ZYXI.webp")
    assert not any(s == "usl" for s, _ in logos.candidates(app, "123450"))  # 국내 종목엔 미국 묶음을 쓰지 않음
    assert (ROOT / "src/quant_ai/assets/logos/us/LICENSE-us-stock-logos.txt").exists()


def test_default_icon_is_first_letter_not_building(app):
    from quant_ai import logos
    kr = logos.default_icon(app, "999990", "가나다전자").decode()
    assert ">가</text>" in kr and logos.BUILDING not in kr
    us = logos.default_icon(app, "ZZZQ", "Zeta Quant").decode()
    assert ">ZE</text>" in us
    unknown = logos.default_icon(app, "QQQQ9", None).decode()  # 이름을 전혀 모르면 건물 그림 (코드 글자를 회사 이름처럼 쓰지 않음)
    assert logos.BUILDING in unknown


# ------------------------------------------------------------------ 커뮤니티
def test_slang_sentiment():
    from quant_ai.data.collectors.community import slang_score
    assert slang_score("오늘 떡상 가즈아") > 0.5
    assert slang_score("손절하고 한강 간다") < -0.5
    assert slang_score("그냥 그렇네") is None
    assert slang_score("to the moon, calls printing") > 0


def test_naver_mobile_json_shape_tolerant_and_fallback(monkeypatch):
    from quant_ai.data.collectors import community as C
    nested = {"result": {"posts": [{"id": 11, "title": "떡상 각", "writtenAt": "2026-10-05T10:00:00"},
                                   {"postId": 12, "title": "물렸다 손절", "date": "x"}]}}
    found = C._walk_posts(nested)
    assert len(found) == 2 and {str(p.get("id") or p.get("postId")) for p in found} == {"11", "12"}
    monkeypatch.setattr(C, "fetch_naver_mobile", lambda code: (_ for _ in ()).throw(RuntimeError("형식 변경")))
    monkeypatch.setattr(C, "fetch_naver_board", lambda code: [{"title": "가즈아", "sentiment": 0.8, "url": "u", "id": "1", "ts": None}])
    assert C.fetch_naver("005930")[0]["title"] == "가즈아"  # 모바일 실패 → PC 게시판
    monkeypatch.setattr(C, "fetch_naver_board", lambda code: (_ for _ in ()).throw(RuntimeError("구조 변경")))
    with pytest.raises(RuntimeError) as e:
        C.fetch_naver("005930")
    assert "모바일 토론실" in str(e.value) and "PC 게시판" in str(e.value)  # 두 출처의 실패 이유를 함께


def test_community_status_recorded(app):
    from quant_ai import ops, toss
    from quant_ai.data.collectors import community as C
    now = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
    posts = [{"title": "떡상", "sentiment": 0.9, "url": "u1"}, {"title": "한강", "sentiment": -0.8, "url": "u2"}]
    r = C.collect(app.engine, ["005930", "NVDA"], fetchers={"kr": lambda s: posts, "us": lambda s: (_ for _ in ()).throw(OSError("403"))}, now=now)
    assert r["collected"] == ["005930"] and r["failed"]
    st = ops.get_state(app.engine, "community_status")
    assert st["ok"] == 1 and st["tried"] == 2 and st["last_ok"]
    c = toss.community(app, "005930")
    assert c["n"] == 2 and c["bull"] == 1 and c["bear"] == 1 and c["posts"]
    cs = toss.collect_status(app)
    assert cs["community"]["ok"] == 1 and cs["logos"]["bundled"] >= 150 and "stock_news" in cs


# ------------------------------------------------------------------ 종목별 뉴스
RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Samsung beats estimates - Reuters</title><link>https://example.com/a</link><pubDate>Mon, 05 Oct 2026 01:00:00 GMT</pubDate></item>
<item><title>Chip demand rises - Bloomberg</title><link>https://example.com/b</link><pubDate>Mon, 05 Oct 2026 02:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_stock_news_tags_symbol_and_source(app):
    from sqlalchemy import select

    from quant_ai.data.collectors.news import collect_stock_news, stock_feed_url
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import NewsArticle
    assert "hl=ko" in stock_feed_url("005930", "삼성전자") and "hl=en-US" in stock_feed_url("NVDA", "NVIDIA")
    syms = []
    with session_scope(app.engine) as s:
        from quant_ai.data.models import Instrument
        syms = [i.symbol for i in s.scalars(select(Instrument).where(Instrument.market != "INDEX")).all()][:2]
        r = collect_stock_news(s, syms, fetch=lambda u: RSS)
    assert r["added"] == 2  # 같은 링크는 한 번만 저장, 두 번째 종목은 태그만 더한다
    with session_scope(app.engine) as s:
        a = s.scalar(select(NewsArticle).where(NewsArticle.url == "https://example.com/a"))
        assert a.source == "Reuters" and a.title == "Samsung beats estimates" and set(a.symbols) == set(syms)


# ------------------------------------------------------------------ 대시보드: 지난 값을 바로 · 뒤에서 새로
def test_dashboard_stale_while_revalidate(app):
    from quant_ai.web.api import DashboardAPI
    api = DashboardAPI(app)
    first = api.dashboard()
    api._cache = (time.monotonic() - 60, first)  # 1분 전 값
    t = time.monotonic()
    again = api.dashboard()
    assert again is first and time.monotonic() - t < 0.5  # 기다리지 않고 바로
    for _ in range(100):  # 뒤에서 새로 계산된다
        if api._cache[1] is not first:
            break
        time.sleep(0.1)
    assert api._cache[1] is not first


# ------------------------------------------------------------------ 화면 정적 검사
def test_static_v26_skin_header_and_jargon():
    t = (STATIC / "toss.js").read_text()
    assert "function tossOwns(" in t and "function tlHead(" in t
    app_js = (STATIC / "app.js").read_text()
    assert 'classList.toggle("tl", legacy)' in app_js and "tlHead()" in app_js
    css = (STATIC / "toss.css").read_text()
    for frag in (".tl .view-inner .card", ".tl .tabs button.on", ".tl th", ".tl-head h1", ".ui-easy #market-notice { display: none; }",
                 "font-variant-numeric: tabular-nums", ".tl table:has(tr > :nth-child(4))", ".ui-easy .dev-only"):
        assert frag in css, frag
    assert "Pretendard" in css.split(".num, .mono")[1].split("}")[0]  # 숫자도 본문 글꼴 (코딩 글꼴 X)
    w = (STATIC / "words.js").read_text()
    assert "function deJargonNode(" in w and '"모의"' in w and '"자동 정지"' in w and '"횡보"' in w
    assert "quant-ai research krx --marcap-dir" not in app_js  # 개발자 명령 대신 ./run.sh
    assert 'class="dev-only"' in (STATIC / "truth.js").read_text()  # 쉬운 화면에선 소스 파일 경로를 숨김
    hub = (STATIC / "hub.js").read_text()
    assert "async function collectCard(" in hub and "/api/t/collect" in hub

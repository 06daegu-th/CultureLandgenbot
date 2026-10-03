"""v24: 전 종목 로고(출처 확대·SVG 정화·전 종목 미리 받기·로고 응답 격리) · 홈 '다가오는 일정' · AI 신뢰 센터 · 토스식 화면 정적 검사."""

import threading
from dataclasses import replace
from datetime import UTC, datetime
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
    d = tmp_path_factory.mktemp("v24")
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


# ------------------------------------------------------------------ 로고: 출처 · SVG 정화 · 응답 격리
def test_logo_sources_cover_unknown_kr_and_us(app, monkeypatch):
    from quant_ai import logos
    monkeypatch.delenv("QUANT_LOGO_SOURCES", raising=False)
    kr = [s for s, _ in logos.candidates(app, "123450")]
    us = [s for s, _ in logos.candidates(app, "ROKU")]
    assert kr[:3] == ["toss", "alpha", "naver"]
    assert us[:3] == ["fmp", "cmc", "eodhd"]
    assert any("Stock123450.svg" in u for _, u in logos.candidates(app, "123450"))
    assert any(u.endswith("/roku.png") for _, u in logos.candidates(app, "ROKU"))


def test_sanitize_svg_rejects_anything_active():
    from quant_ai.logos import sanitize_svg
    ok = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><defs><linearGradient id="g"/></defs><rect fill="url(#g)" width="10" height="10"/></svg>'
    assert sanitize_svg(ok) == ok
    for bad in (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"/>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><a href="https://evil"><rect/></a></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><image xlink:href="https://evil/x.png"/></svg>',
                b'<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div/></foreignObject></svg>',
                b'<svg', b'<html><body/></html>'):
        assert sanitize_svg(bad) is None, bad[:40]


def test_external_svg_logo_is_sanitized_then_cached(app):
    from quant_ai import logos
    good = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#0a0"/>' + b" " * 300 + b"</svg>"
    evil = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">' + b" " * 300 + b"</svg>"
    d, ctype, src = logos.get(app, "234560", fetch=lambda u: good if "pstatic" in u else (_ for _ in ()).throw(OSError("x")))
    assert src == "naver" and ctype == "image/svg+xml" and d == good
    d2, _, src2 = logos.get(app, "345670", fetch=lambda u: evil if "pstatic" in u else (_ for _ in ()).throw(OSError("x")))
    assert src2 == "default" and b"onload" not in d2


def test_logo_response_is_sandboxed(server):
    c = HTTPConnection("127.0.0.1", server, timeout=30)
    c.request("GET", "/api/logo/NVDA")
    r = c.getresponse()
    r.read()
    csp = r.getheader("Content-Security-Policy") or ""
    assert "sandbox" in csp and "default-src 'none'" in csp and r.getheader("X-Content-Type-Options") == "nosniff"


def test_logos_cli_all_option():
    cli = (ROOT / "src/quant_ai/cli.py").read_text()
    assert '"--all"' in cli and "Instrument.market != \"INDEX\"" in cli
    assert "qa logos --all" in (ROOT / "run.sh").read_text()


# ------------------------------------------------------------------ 홈 '다가오는 일정' · 장 상태
def test_upcoming_has_market_events_without_watchlist(app):
    from quant_ai.center import home5, upcoming
    u = upcoming(app, datetime(2026, 10, 3, 3, 0, tzinfo=UTC))
    titles = [r["title"] for r in u["rows"]]
    assert any("개천절" in t for t in titles) and any("한글날" in t for t in titles)  # 관심종목이 없어도 시장 일정은 보인다
    assert u["rows"] == sorted(u["rows"], key=lambda r: (r["d_day"], r["scope"] != "종목"))
    assert all(r["d_label"] in ("오늘", "내일") or r["d_label"].startswith("D-") for r in u["rows"])
    h = home5(app)
    assert "upcoming" in h and "last" in (h["watch"][0] if h["watch"] else {"last": 0})


def test_market_sessions_extended_hours():
    from quant_ai.clock import clock_status
    us = clock_status(datetime(2026, 10, 6, 12, 0, tzinfo=UTC))["markets"]["US"]
    assert us["session_label"] == "프리마켓"
    us2 = clock_status(datetime(2026, 10, 6, 20, 30, tzinfo=UTC))["markets"]["US"]
    assert us2["session_label"] == "애프터마켓"
    kr = clock_status(datetime(2026, 10, 7, 7, 0, tzinfo=UTC))["markets"]["KRX"]
    assert kr["session_label"] == "시간외"


# ------------------------------------------------------------------ AI 신뢰 센터
def test_ai_trust_center_endpoint(server):
    import json
    c = HTTPConnection("127.0.0.1", server, timeout=60)
    c.request("GET", "/api/ai-trust")
    r = json.loads(c.getresponse().read())
    for k in ("state", "ladder", "ledger", "context"):
        assert k in r, k
    assert r["state"]["key"] in ("verified", "checking", "banned")
    assert r["ladder"]["stage"]
    assert "ok" in r["ledger"]


# ------------------------------------------------------------------ 화면 정적 검사
def test_static_v24_toss_layout_and_no_flag_emoji():
    easy = (STATIC / "easy.js").read_text()
    for s_ in ("다가오는 일정", "t-row", "t-px", "#aitrust"):
        assert s_ in easy, s_
    words = (STATIC / "words.js").read_text()
    assert "function logoLinks(" in words and "\\u{1F1E6}" in words  # 종목 링크 자동 로고 · 국기 이모지 정리
    icons = (STATIC / "icons.js").read_text()
    assert 'class="flag cc"' in icons
    truth = (STATIC / "truth.js").read_text()
    assert "m.flag" not in truth
    app_js = (STATIC / "app.js").read_text()
    assert "prefers-color-scheme: light" in app_js and '"aitrust"' in app_js and "on-home" in app_js
    assert "function viewAITrust(" in (STATIC / "hub.js").read_text()
    pro = (STATIC / "pro.js").read_text()
    assert "Enter = 첫 결과의 올인원 종목 화면" in pro
    calm = (STATIC / "calm.css").read_text()
    assert ".up-strip" in calm and ".t-row" in calm and ".tc-hero" in calm
    assert "qa-shell-v24" in (STATIC / "sw.js").read_text()

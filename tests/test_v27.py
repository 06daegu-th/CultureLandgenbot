"""v27: 나머지 화면 토스식 개별 재작성 · 진짜 지수 · 1일(분봉) · 로컬 LLM · 미국 모의 매수 · 중복 제목 · 용어 풀이 ·
로고 직접 넣기 · 커뮤니티 확대(많이 움직인 종목) · AI 버튼 위치 · 이벤트 반응 날짜 오류."""

import base64
import json
import re
import struct
import threading
import zlib
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v27")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def _png(w=8, h=8) -> bytes:
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def ch(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")


def _yahoo(closes, start=1_727_740_800, step=86400, off=32400):
    return json.dumps({"chart": {"result": [{"meta": {"gmtoffset": off, "chartPreviousClose": closes[0] * 0.99, "regularMarketPrice": closes[-1],
                                                      "exchangeTimezoneName": "Asia/Seoul", "marketState": "CLOSED"},
                                             "timestamp": [start + i * step for i in range(len(closes))],
                                             "indicators": {"quote": [{"close": closes, "volume": [100] * len(closes)}]}}]}}).encode()


# ------------------------------------------------------------------ 5. 진짜 지수
def test_index_parsers():
    from quant_ai.data.collectors import indices as I
    s = I.parse_yahoo(json.loads(_yahoo([2500.0, None, 2510.5, 2490.2])))
    assert len(s) == 3 and s[-1][1] == 2490.2 and re.match(r"\d{4}-\d\d-\d\d", s[0][0])
    nv = I.parse_naver_index([{"localTradedAt": "2026-10-02T15:30:00+09:00", "closePrice": "2,610.11"},
                              {"localTradedAt": "2026-10-01T15:30:00+09:00", "closePrice": "2,600.00"}])
    assert nv == [["2026-10-01", 2600.0], ["2026-10-02", 2610.11]]  # 오래된 순
    sq = I.parse_stooq("Date,Open,High,Low,Close,Volume\n2026-10-01,1,1,1,5700.5,0\n2026-10-02,1,1,1,5720.1,0\n")
    assert sq[-1] == ["2026-10-02", 5720.1]


def test_collect_indices_falls_back_and_toss_uses_real(app):
    from quant_ai import toss
    from quant_ai.data.collectors import indices as I
    seen = []

    def get(url):
        seen.append(url)
        if "yahoo" in url and "KQ11" in url:
            raise OSError("blocked")  # 코스닥은 야후가 막히면 네이버로
        if "naver" in url:
            return json.dumps([{"localTradedAt": f"2026-09-{d:02d}", "closePrice": 800 + d} for d in range(30, 20, -1)]).encode()
        if "stooq" in url:
            raise OSError("blocked")
        return _yahoo([2500 + i for i in range(30)])
    r = I.collect_indices(app.engine, get)
    assert set(r["ok"]) == {"KOSPI", "KOSDAQ", "NASDAQ", "SPX", "DJI"} and not r["failed"]
    items = I.load_indices(app.engine)
    assert items["KOSDAQ"]["source"] == "네이버 증권" and items["KOSPI"]["source"] == "Yahoo"
    idx = toss._indices(app)
    k = next(x for x in idx if x["key"] == "KOSPI")
    assert k["proxy"] is False and k["last"] == 2529.0 and k["source"] == "Yahoo"  # 대용이 아닌 진짜 지수
    assert any(x["key"] == "KOSDAQ" for x in idx) and any(x["key"] == "DJI" for x in idx)
    # 모두 실패하면 이전 값을 지우지 않고 오류만 남긴다
    r2 = I.collect_indices(app.engine, lambda u: (_ for _ in ()).throw(OSError("down")))
    assert len(r2["failed"]) == 5
    assert I.load_indices(app.engine)["KOSPI"]["series"] and "down" in I.load_indices(app.engine)["KOSPI"]["error"]


# ------------------------------------------------------------------ 6. 1일 (분봉)
def test_intraday_points_and_failure_message():
    from quant_ai.data.collectors import indices as I
    I._INTRA.clear()
    r = I.intraday("005930", "KOSPI", get=lambda u: _yahoo([71000, 71200, 70900], step=300), ttl=0)
    assert len(r["points"]) == 3 and r["prev_close"] and r["gmtoffset"] == 32400 and "Yahoo" in r["source"]
    assert I.yahoo_symbol("005930") == "005930.KS" and I.yahoo_symbol("247540", "KOSDAQ") == "247540.KQ" and I.yahoo_symbol("BRK.B") == "BRK-B"
    bad = I.intraday("NVDA", get=lambda u: (_ for _ in ()).throw(OSError("blocked")), ttl=0)
    assert bad["points"] == [] and "분봉을 받지 못했어요" in bad["error"]
    js = (STATIC / "toss.js").read_text()
    assert '[[1, "1일"]' in js and "/api/t/intraday" in js and "tStockIntraday" in js


# ------------------------------------------------------------------ 7. 로컬 LLM (키 없이)
def test_local_llm_provider_only_loopback(monkeypatch):
    from quant_ai.analysts.llm_clients import local_url_ok
    from quant_ai.config import Settings
    assert local_url_ok("http://127.0.0.1:11434/v1") and local_url_ok("http://localhost:1234/v1") and local_url_ok("https://my.llm/v1")
    assert not local_url_ok("http://192.168.0.5:11434/v1") and not local_url_ok("ftp://127.0.0.1/")
    st = Settings.from_env({"QUANT_LOCAL_LLM_URL": "http://127.0.0.1:11434/v1", "QUANT_LOCAL_MODELS": "qwen2.5:7b-instruct"})
    assert st.llm_providers["local"]["account"] == "http://127.0.0.1:11434/v1" and st.llm_providers["local"]["models"] == ("qwen2.5:7b-instruct",)
    assert st.has_llm
    assert "local" not in Settings.from_env({"QUANT_LOCAL_LLM_URL": "http://10.0.0.2/v1"}).llm_providers  # 다른 컴퓨터 http 는 거부


def test_local_llm_chat_roundtrip():
    from quant_ai.analysts.analysts import assign_roles
    from quant_ai.assistant import chat_client
    from quant_ai.config import Settings
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = json.dumps({"choices": [{"message": {"role": "assistant", "content": "안녕하세요 (로컬)"}, "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 3, "completion_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        st = Settings.from_env({"QUANT_LOCAL_LLM_URL": f"http://127.0.0.1:{srv.server_port}/v1", "QUANT_LOCAL_MODELS": "tiny"})
        client, prov = chat_client(st)
        assert prov == "local"
        r = client._post("/chat/completions", {"model": "tiny", "messages": [{"role": "user", "content": "hi"}]})
        assert r["choices"][0]["message"]["content"] == "안녕하세요 (로컬)" and got["body"]["model"] == "tiny"
        assert set(assign_roles(st).values()) == {"local"}  # 클라우드 키가 없으면 AI 역할도 로컬이 맡는다
    finally:
        srv.shutdown()


# ------------------------------------------------------------------ 8. 미국 모의 매수
def test_us_manual_ticket_books_in_usd(app, monkeypatch):
    from quant_ai import ticket
    from quant_ai.data import global_stocks as gs
    idx = pd.bdate_range("2026-01-01", periods=60, tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9, "close": np.linspace(100, 120, 60), "volume": 5e6}, index=idx)
    monkeypatch.setattr(gs, "DEFAULT_FETCHERS", (("test", lambda s: df),))
    assert gs.ensure_global(app.engine, "ZZUS", force=True)["ok"]
    v = ticket.preview(app, "ZZUS", "buy", 3)
    assert v["mode"] == "us-manual" and v["currency"] == "USD" and v["price"] == pytest.approx(120.0)
    assert v["fx"] and v["notional_krw"] and v["allowed_qty"] == 3
    r = ticket.place(app, {"symbol": "ZZUS", "side": "buy", "qty": 3, "confirm": True})
    assert r["placed"] and "$" in r["message"]
    b = ticket.book(app, ticket.US_MODE)
    assert b["currency"] == "USD" and b["positions"][0]["symbol"] == "ZZUS" and b["cash"] < 100_000 and b["equity_krw"]
    assert not ticket.book(app)["positions"] or all(p["symbol"] != "ZZUS" for p in ticket.book(app)["positions"])  # 국내 장부와 섞이지 않음
    js = (STATIC / "toss.js").read_text()
    assert "미국 주식 모의 주문 장부는 아직 국내 주식만 지원" not in js and "us-manual" in (STATIC / "toss2.js").read_text()


# ------------------------------------------------------------------ 12. 로고 직접 넣기
def test_logo_upload_saves_custom_first(app):
    from quant_ai import logos
    from quant_ai.web.api import DashboardAPI
    api = DashboardAPI(app)
    out = api.logo_upload({"symbol": "000020", "data": "data:image/png;base64," + base64.b64encode(_png()).decode()})
    assert out["saved"] and out["type"] == "image/png"
    data, ctype, src = logos.get(app, "000020")
    assert src == "custom" and ctype == "image/png"
    with pytest.raises(ValueError, match="이미지만"):
        api.logo_upload({"symbol": "000020", "data": base64.b64encode(b"hello world, not an image").decode()})
    with pytest.raises(ValueError, match="너무 커요"):
        logos.save_custom(app, "000020", b"\x89PNG" + b"0" * 200_000)
    with pytest.raises(ValueError):  # 스크립트가 든 SVG 는 거부 — 그리고 기존 로고는 그대로
        logos.save_custom(app, "000020", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle r="4"/></svg>')
    assert logos.get(app, "000020")[2] == "custom"
    assert api.logo_upload({"symbol": "000020", "data": ""})["removed"] and logos.get(app, "000020")[2] != "custom"
    srv = (ROOT / "src/quant_ai/web/server.py").read_text()
    assert "/api/logo-upload" in srv and '"custom": "no-cache"' in srv
    assert "data-logo-up" in (STATIC / "toss.js").read_text() and "tLogoSheet" in (STATIC / "toss2.js").read_text()


# ------------------------------------------------------------------ 13. 커뮤니티: 많이 움직인 종목까지
def test_community_symbols_include_movers(app, monkeypatch):
    from quant_ai.data.collectors import community as C
    idx = pd.bdate_range("2026-01-01", periods=3, tz="UTC")
    bars = {"111111": pd.DataFrame({"close": [100, 100, 130], "volume": [1e8] * 3}, index=idx),   # +30% · 거래대금 130억
            "222222": pd.DataFrame({"close": [100, 100, 101], "volume": [1e8] * 3}, index=idx),
            "333333": pd.DataFrame({"close": [100, 100, 200], "volume": [10] * 3}, index=idx),    # 거래가 거의 없음 → 뺀다
            "NVDA": pd.DataFrame({"close": [1, 1, 5], "volume": [1e9] * 3}, index=idx)}           # 해외는 국내 토론방 대상 아님
    assert C.movers(bars, 5) == ["111111", "222222"]
    monkeypatch.setattr(app, "market_data", lambda *a, **k: (bars, None, None))
    syms = C.symbols_for(app, cap=25)
    assert "111111" in syms and "333333" not in syms and len(syms) <= 25
    sch = (ROOT / "src/quant_ai/scheduler.py").read_text()
    assert "community_syms(app)" in sch and "collect_indices" in sch


# ------------------------------------------------------------------ 이벤트 반응: 시간대 비교 오류
def test_abnormal_handles_tz_aware_bars():
    from datetime import date

    from quant_ai.analytics import _abnormal
    idx = pd.bdate_range("2026-01-01", periods=20, tz="UTC")
    c = pd.Series(np.linspace(100, 119, 20), index=idx)
    b = pd.Series(100.0, index=idx)
    r = _abnormal(c, b, date(2026, 1, 8), 1)
    assert r is not None and r > 0


# ------------------------------------------------------------------ 9·10·11·14. 화면
def test_every_menu_screen_has_toss_renderer():
    app_js = (STATIC / "app.js").read_text()
    t2 = (STATIC / "toss2.js").read_text()
    nav = re.findall(r'\["(\w+)", "\w+", "[^"]+"\]', app_js[app_js.find("const NAV = ["):app_js.find("function buildNav")])
    owned_by_toss1 = {"dashboard", "analysis", "watch", "market", "news", "pos"}  # V25 토스 화면 (쉬운 화면)
    tv = set(re.findall(r"^TV\.(\w+) = ", t2, re.M))
    missing = [v for v in nav if v not in tv and v not in owned_by_toss1]
    assert not missing, missing
    assert {"goal", "ledger", "evidence"} <= tv and len(tv) >= 50
    toss = (STATIC / "toss.js").read_text()
    assert "TV[v] && S.sub !== \"full\"" in toss  # 두 화면 모드 모두 · #화면//full 은 예전 전문가 화면
    assert "tFull(" in t2 and "//full" in t2
    assert 'if (param === "full" && !sub' in app_js  # #pos/full 이 다시 토스 화면으로 돌던 문제


def test_glossary_duplicate_title_and_ai_button():
    t2 = (STATIC / "toss2.js").read_text()
    for k in ("Sharpe", "VaR", "MDD", "Brier Skill", "ECE", "PSI", "IC", "DSR", "베타", "슬리피지"):
        assert f'"{k}": [' in t2, k
    assert "function glossify" in t2 and "data-gl" in t2
    app_js = (STATIC / "app.js").read_text()
    assert "function dedupeTitle" in app_js and "glossify(el)" in app_js
    pro = (STATIC / "pro.js").read_text()
    css = (STATIC / "toss.css").read_text()
    assert "ai-top-btn" in pro and ".chat-fab { display: none !important; }" in css
    # v27 클래스가 예전 주문 시트(.t-field · .t-check · .t-steps)와 겹치지 않게
    v27 = css[css.find("v27 — 나머지 화면"):]
    assert ".t-field " not in v27 and ".t-check " not in v27 and ".t-steps " not in v27


def test_static_shell_lists_toss2():
    idx = (STATIC / "index.html").read_text()
    sw = (STATIC / "sw.js").read_text()
    assert idx.index("/toss.js") < idx.index("/toss2.js") < idx.index("/app.js")
    assert '"/toss2.js"' in sw and re.search(r'qa-shell-v(2[7-9]|3\d)', sw)

"""v21: 차분한 화면(우리말·색 점) · 운영 상태 표시 · 내 자산 장부 선택 · 세금 계좌 비교 · ETF 월 적립 · 점검 보고서."""

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime
from http.client import HTTPConnection
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src/quant_ai/web/static"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v21")
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
    c = HTTPConnection("127.0.0.1", port, timeout=60)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


# ------------------------------------------------------------------ 세금 계좌
def test_tax_compare_accounts_rules():
    from quant_ai import goal
    core = {x["key"]: x for x in goal.tax_compare(5e6, 5e5, 1e8, 10, "core")}
    assert not core["pension"]["ok"] and "개별 종목" in core["pension"]["why_not"]  # 연금계좌는 코어(개별 종목) 불가
    assert core["isa"]["p_target"] >= core["general"]["p_target"] - 0.01
    sp = {x["key"]: x for x in goal.tax_compare(5e6, 5e5, 1e8, 10, "sp500")}
    assert sp["isa"]["p_target"] > sp["general"]["p_target"]  # 해외지수 ETF 는 ISA 가 유리 (15.4% → 9.9%)
    assert sp["pension"]["refund_year"] == round(6_000_000 * 0.132) and sp["pension"].get("best")  # 연 600만 납입 × 13.2% 돌려받음
    assert sum(1 for x in sp.values() if x.get("best")) == 1
    p = goal.plan(5e6, 5e5, 1e8, 10, "kospi")
    assert {t["key"] for t in p["tax"]} == {"general", "isa", "pension"}


# ------------------------------------------------------------------ ETF 월 적립
def test_etf_dca_book_buys_and_waits_for_price(app, monkeypatch):
    from quant_ai import goal
    from quant_ai.data.collectors import prices

    class NoNet:  # 인터넷이 되는 CI 에서도 Yahoo 를 부르지 않게 (가격이 없을 때의 동작을 시험)
        def __init__(self, *a, **k):
            raise ConnectionError("시험: 네트워크 없음")
    monkeypatch.setattr(prices, "YahooPriceSource", NoNet)
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Instrument, PriceBar
    g = goal.save(app, {"principal": 5_000_000, "monthly": 500_000, "goal": 100_000_000, "target_years": 10,
                        "dca": {"on": True, "day": 5, "mode": "paper", "target": "etf", "etf": "069500", "amount": 500_000}})
    assert g["dca"]["target"] == "etf" and g["seeded"]["bought"] == 0  # 가격이 없으면 원금은 현금으로 기다린다
    assert goal._load_book(app, goal.ETF_BOOK).cash == 5_000_000 and app.load_portfolio("paper").cash == app.settings.initial_cash  # 코어 장부와 분리
    with pytest.raises(ValueError):
        goal.save(app, {"dca": {"on": True, "day": 5, "target": "bitcoin"}})
    with session_scope(app.engine) as s:
        s.add(Instrument(symbol="069500", market="KRX", name="KODEX 200"))
        s.add(PriceBar(symbol="069500", interval="1d", ts=datetime(2026, 10, 2, tzinfo=UTC), open=35000, high=35500, low=34800,
                       close=35200, volume=1000, source="test"))
    r = goal.dca_run(app, datetime(2026, 10, 6, 1, tzinfo=UTC))  # 10/6 거래일: 밀린 원금 매수 + 이번 달 적립 매수
    assert r["done"] and r["buy"]["bought"] >= 14 and "주 매수" in r["message"]
    pf = goal._load_book(app, goal.ETF_BOOK)
    assert pf.positions["069500"].qty >= 150 and 0 <= pf.cash < 35_300
    total, src = goal.current_assets(app)
    assert "ETF 적립" in src and 5_300_000 < total < 5_600_000
    assert [f["memo"] for f in app.cashflows(goal.ETF_BOOK)] == ["시작 원금", "월 적립"]


def test_etf_dca_live_sends_order_sheet(app, monkeypatch):
    from quant_ai import goal, ops
    from quant_ai.data.collectors import prices
    monkeypatch.setattr(prices, "YahooPriceSource", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("시험")))
    g = goal.get(app)
    ops.set_state(app.engine, goal.KEY, g | {"dca": {**g["dca"], "mode": "live", "last": None}})
    sent = []
    monkeypatch.setattr("quant_ai.alerts.push", lambda *a, **k: sent.append(a))
    r = goal.dca_run(app, datetime(2026, 11, 5, 1, tzinfo=UTC))
    assert r["done"] and r["mode"] == "live" and "069500" in r["sheet"] and "주" in r["sheet"] and sent


# ------------------------------------------------------------------ 점검 보고서
def test_report_has_facts_but_no_secrets(app, monkeypatch, tmp_path):
    from quant_ai import report
    monkeypatch.setenv("DART_API_KEY", "dartsecretvalue123456")
    monkeypatch.setenv("QUANT_TELEGRAM_TOKEN", "123456789:AAH-secretTOKENvalue_abcdefghijklmnop")
    out, text = report.write(app, tmp_path / "r.txt", net=False)
    assert out.exists() and "## 24시간 운영 · 데이터" in text and "DART_API_KEY" in text and "## DB 크기" in text
    assert "dartsecretvalue123456" not in text and "secretTOKEN" not in text
    s = report.scrub("url?crtfc_key=abcdef12345 계좌 12345678-01 a@b.com postgresql://u:p@h/db 0123456789abcdef0123456789abcdef01")
    assert "abcdef12345" not in s and "12345678-01" not in s and "a@b.com" not in s and "u:p@h" not in s and "0123456789abcdef0123" not in s


def test_report_command_wired():
    cli = (Path(__file__).resolve().parents[1] / "src/quant_ai/cli.py").read_text()
    run = (Path(__file__).resolve().parents[1] / "run.sh").read_text()
    assert 'add_parser("report"' in cli and "report)     ensure_db; qa report" in run


# ------------------------------------------------------------------ 운영 상태 · 홈 · 내 자산
def test_ops_status_route_and_home_numbers(app, server):
    st, o = _get(server, "/api/ops-status")
    assert st == 200 and "issues" in o and "running" in o
    from quant_ai import center
    ez = center.ai_state(app)["easy"]
    assert {"hits", "base_hits", "excess"} <= set(ez)


def test_portfolio_overview_source_toggle(app):
    from quant_ai import accounts, portfolio_os
    acct = accounts.upsert(app.engine, {"name": "내 ISA", "type": "isa", "cash": 1_000_000, "holdings": []})
    try:
        auto = portfolio_os.overview(app, "paper")
        sysb = portfolio_os.overview(app, "paper", "system")
        assert auto["source_key"] == "accounts" and auto["n_accounts"] == 1
        assert sysb["source_key"] == "system" and sysb["source"] == "모의투자 장부" and sysb["total"] != auto["total"]
    finally:
        accounts.delete(app.engine, acct["id"])


# ------------------------------------------------------------------ 화면 말투 (정적 검사)
def test_ui_words_and_calm_layout_static():
    idx = (STATIC / "index.html").read_text()
    sw = (STATIC / "sw.js").read_text()
    assert "/words.js" in idx and idx.index("/words.js") < idx.index("/allin.js") and '"/words.js"' in sw
    assert "AI-Powered" not in idx and "거짓말" not in idx
    words = (STATIC / "words.js").read_text()
    for fn in ("function koAct(", "function lvDot(", "function dotText(", "function koText(", "function calDot(", "function foldPro("):
        assert fn in words
    app_js = (STATIC / "app.js").read_text()
    assert "Paper Trading" not in app_js and "/api/ops-status" in app_js and "nav-filter" in app_js
    easy = (STATIC / "easy.js").read_text()
    assert "koAct(v.final)" in easy and "home-hero" in easy and "FINAL" not in easy and "🎯 내 목표" not in easy
    os_js = (STATIC / "os.js").read_text()
    assert '["sum", "요약"' in os_js and 'const OS_ST = { ok: lvDot("good")' in os_js
    for f in ("allin.js", "desk.js", "app.js"):  # 상태 표시 사전에 이모지가 남지 않았나
        assert not any("🟢" in ln for ln in (STATIC / f).read_text().splitlines() if "_ICON = " in ln)
    css = (STATIC / "style.css").read_text()
    assert ".ui-easy .pro-only" in css and ".lv-dot" in css and ".home-hero" in css


# ------------------------------------------------------------------ 로고 (v21 마지막 점검)
def test_logo_never_blocks_screen_and_retries_blocked(app, monkeypatch, tmp_path):
    import json as _json
    import time as _time
    import urllib.error

    from quant_ai import logos
    monkeypatch.delenv("QUANT_LOGOS", raising=False)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 400
    calls = []

    def blocked(url):
        calls.append(url)
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    data, ctype, src = logos.get(app, "AAA111", "테스트", fetch=blocked)
    assert src == "default" and calls
    meta = _json.loads((logos._dir(app) / "AAA111.json").read_text())
    assert meta["net"] is True and meta["v"] == logos.META_V  # 403 은 '없음'이 아니라 차단 → 1시간 뒤 다시
    # 옛 버전의 실패 기록(7일 막힘)은 무시하고 다시 받는다
    (logos._dir(app) / "BBB222.json").write_text(_json.dumps({"failed_at": _time.time(), "net": False, "tried": ["toss: HTTPError"]}))
    _, _, src2 = logos.get(app, "BBB222", fetch=lambda u: png)
    assert src2 in ("toss", "alpha", "fmp", "favicon", "logodev")
    # 화면용(block=False): 기다리지 않고 이니셜 → 뒤에서 받아 두면 다음엔 진짜 로고
    _, _, src3 = logos.get(app, "CCC333", fetch=lambda u: png, block=False)
    assert src3 == "pending"
    for _ in range(50):
        if "CCC333" not in logos._inflight:
            break
        _time.sleep(0.05)
    assert logos.get(app, "CCC333", block=False)[2] != "pending"


def test_logodev_source_only_with_token(app, monkeypatch):
    from quant_ai import logos
    monkeypatch.delenv("QUANT_LOGO_SOURCES", raising=False)
    monkeypatch.delenv("QUANT_LOGO_DEV_TOKEN", raising=False)
    assert all(s != "logodev" for s, _ in logos.candidates(app, "NVDA"))
    monkeypatch.setenv("QUANT_LOGO_DEV_TOKEN", "tok123")
    c = logos.candidates(app, "005930")
    assert c[0][0] == "logodev" and "005930.KS" in c[0][1]
    assert logos.candidates(app, "NVDA")[0][1].startswith("https://img.logo.dev/ticker/NVDA")


def test_tls_context_adds_certifi():
    import ssl

    from quant_ai import tls
    assert tls.install() and isinstance(tls.context(), ssl.SSLContext)
    assert ssl._create_default_https_context is tls.context  # noqa: SLF001

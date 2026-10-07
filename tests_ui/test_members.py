"""v34 여러 사용자 모드 화면 테스트: 실제 브라우저로 가입 → 회원 화면 전부 → 운영자 콘솔.

실패 조건: 화면 JS 예외 · /api 5xx · 회원 화면이 부른 API 가 403(운영 전용·AI 판단)·401 · 휴대폰 가로 넘침 ·
           회원 화면에 운영 정보(긴급 정지·서버) 노출 · 투자 정보 범위 none 인데 'AI 추천' 메뉴.
"""

import os
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer

import pytest

pw = pytest.importorskip("playwright.sync_api")

MEMBER_SCREENS = ["dashboard", "watch", "goal", "pos", "accounts", "market", "news", "calendar", "alerts", "more", "map",
                  "compare", "myjournal", "manual", "chat", "account", "analysis/000001"]
PW = "calm-ocean-4821"


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    from quant_ai import members, service
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("mui")
    fake_marcap(d, n_codes=8, days=320)
    old = {k: os.environ.get(k) for k in ("QUANT_SERVICE_MODE", "QUANT_DATA_KEY", "QUANT_WARM_LOOP")}
    os.environ.update(QUANT_SERVICE_MODE="multi", QUANT_DATA_KEY="ui-test-key", QUANT_WARM_LOOP="0")
    app = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/m.db", artifacts_dir=d / "a", max_data_age_days=0))
    app.ingest_krx(d, years=0, top_n=6, end_year=2020)
    members.create(app, "boss@example.com", "steady-river-2026", "운영자", role="owner", verified=True,
                   consent={"terms": members.TERMS_VERSION})
    service.set_policy(app, {"signup": "open"})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), None, {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        exe = os.environ.get("QUANT_UI_CHROMIUM")
        b = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        yield b
        b.close()


def _ctx(browser, width):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.route("**/fonts.googleapis.com/**", lambda r: r.fulfill(body="", content_type="text/css"))
    ctx.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    ctx.route("**/cdn.jsdelivr.net/**", lambda r: r.fulfill(body="", content_type="text/css"))
    return ctx


@pytest.mark.parametrize("width", [1400, 390])
def test_member_signup_and_every_member_screen(browser, base_url, width):
    ctx = _ctx(browser, width)
    page = ctx.new_page()
    errors, bad = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if "/api/" in r.url and (r.status >= 500 or r.status in (401, 403))
            and "/api/auth" not in r.url else None)
    page.goto(f"{base_url}/")
    page.wait_for_selector(".ma-card", timeout=15000)
    page.click("text=처음이에요 — 가입하기")
    page.fill("input[name=email]", f"ui{width}@example.com")
    page.fill("input[name=password]", PW)
    page.fill("input[name=password2]", PW)
    page.check("[data-all]")
    page.click(".ma-go")
    page.wait_for_selector("#view .view-inner", timeout=20000)
    page.wait_for_timeout(1500)
    nav = page.inner_text("#nav")
    assert "AI 자동매매" not in nav and "AI 추천" not in nav and "내 계정" in nav
    problems = []
    for v in MEMBER_SCREENS:
        page.goto(f"{base_url}/#{v}")
        page.wait_for_timeout(1300)
        txt = page.inner_text("#view")
        if "운영자만 쓸 수 있는" in txt or not txt.strip():
            problems.append(f"{v}: 막힘/빈 화면")
        if "긴급 정지" in txt or "서버 · DB" in txt:
            problems.append(f"{v}: 운영 정보 노출")
        if width < 500 and page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") > 2:
            problems.append(f"{v}: 가로 넘침")
    # 운영 화면 주소로 오면 안내만
    page.goto(f"{base_url}/#server")
    page.wait_for_timeout(800)
    assert "운영자만 쓸 수 있는" in page.inner_text("#view")
    ctx.close()
    assert not errors, errors
    assert not bad, bad
    assert not problems, problems


def test_owner_admin_console(browser, base_url):
    ctx = _ctx(browser, 1400)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/")
    page.wait_for_selector(".ma-card", timeout=15000)
    page.fill("input[name=email]", "boss@example.com")
    page.fill("input[name=password]", "steady-river-2026")
    page.click(".ma-go")
    page.wait_for_selector("#view .view-inner", timeout=20000)
    assert "운영 콘솔" in page.inner_text("#nav")
    page.goto(f"{base_url}/#admin")
    page.wait_for_selector(".adm-users", timeout=15000)
    page.click("#inv-go")
    page.wait_for_selector("#inv-out code", timeout=10000)
    assert "#signup/" in page.inner_text("#inv-out")
    ctx.close()
    assert not errors, errors

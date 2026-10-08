"""v32 화면 회귀 테스트: 사용자 메뉴와 핵심 화면을 실제 브라우저로 하나씩 연다.

실패 조건 (사람이 스크린샷으로 보던 것을 기계가 대신):
  · 화면 JS 예외(pageerror)
  · 화면이 부른 /api/ 응답이 5xx
  · 화면이 비었거나 '화면이 없습니다'
PC(1400px)·휴대폰(390px) 두 크기 · 휴대폰에서는 가로로 넘치는 화면도 실패.
"""

import os
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer

import pytest

pw = pytest.importorskip("playwright.sync_api")

SCREENS = ["dashboard", "watch", "pos", "goal", "picks", "autopilot", "news", "market", "calendar", "alerts", "more",
           "report", "proof", "datacheck", "budget", "settings", "logoq", "ops"]


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    from quant_ai.auth import Auth
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("ui")
    fake_marcap(d, n_codes=8, days=320)
    app = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/ui.db", artifacts_dir=d / "a", max_data_age_days=0))
    app.ingest_krx(d, years=0, top_n=6, end_year=2020)
    os.environ.setdefault("QUANT_WARM_LOOP", "0")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), None, {"127.0.0.1", "localhost"}, Auth()))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        exe = os.environ.get("QUANT_UI_CHROMIUM")  # 내려받은 브라우저 대신 쓸 실행 파일 (예: 미리 깔린 chromium)
        b = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        yield b
        b.close()


def _open(browser, base_url, view, width):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.route("**/fonts.googleapis.com/**", lambda r: r.fulfill(body="", content_type="text/css"))
    ctx.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    page = ctx.new_page()
    errors, bad = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if "/api/" in r.url and r.status >= 500 else None)
    page.goto(f"{base_url}/#{view}", wait_until="networkidle")
    page.wait_for_timeout(1200)
    text = page.inner_text("#view")
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    ctx.close()
    return errors, bad, text, overflow


@pytest.mark.parametrize("view", SCREENS)
def test_screen_opens_clean_on_pc(browser, base_url, view):
    errors, bad, text, _ = _open(browser, base_url, view, 1400)
    assert not errors, f"{view}: JS 오류 {errors[:3]}"
    assert not bad, f"{view}: 서버 오류 {bad[:3]}"
    assert len(text.strip()) > 20 and "화면이 없습니다" not in text, f"{view}: 빈 화면"


@pytest.mark.parametrize("view", ["dashboard", "goal", "picks", "autopilot", "pos", "more"])
def test_screen_fits_phone(browser, base_url, view):
    errors, bad, text, overflow = _open(browser, base_url, view, 390)
    assert not errors and not bad, f"{view}: {errors[:2]} {bad[:2]}"
    assert overflow <= 2, f"{view}: 휴대폰에서 가로로 {overflow}px 넘침"


def test_stock_page_opens(browser, base_url):
    errors, bad, text, _ = _open(browser, base_url, "analysis/000010", 1400)
    assert not errors and not bad, f"{errors[:2]} {bad[:2]}"
    assert len(text.strip()) > 20


def test_stock_page_shows_inputs_staleness_and_clean_axis(browser, base_url):
    """v36: 오래된 가격 경고 · 'AI 가 확인한 자료' · 1년 차트 축에 '0.000' 같은 이상한 눈금 없음 · 1일 차트 실패 문구에 주소 없음."""
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
    ctx.route("**/fonts.googleapis.com/**", lambda r: r.fulfill(body="", content_type="text/css"))
    ctx.route("**/query1.finance.yahoo.com/**", lambda r: r.abort())
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/#analysis/000020", wait_until="networkidle")
    page.wait_for_selector(".tsk-stale", timeout=15000)
    assert "실제 주가와 다를 수 있어요" in page.inner_text(".tsk-stale")
    page.wait_for_selector("#analyze-btn, .ai-inputs", timeout=20000)
    if page.locator("#analyze-btn").count():  # 판단 기록이 없으면 화면의 '지금 AI 분석'으로 만든다 (주문 없음)
        page.click("#analyze-btn")
    page.wait_for_selector(".ai-inputs", timeout=90000)
    box = page.inner_text(".ai-inputs")
    assert "AI 가 확인한 자료" in box and "뉴스" in box and "차트" in box
    page.click('#tsk-per button[data-k="252"]')
    page.wait_for_timeout(1200)
    axis = page.inner_text(".tsk-chart-w")
    assert "0.000" not in axis
    page.click('#tsk-per button[data-k="1"]')
    page.wait_for_timeout(2500)
    assert "http" not in page.inner_text(".tsk-chart-w")
    ctx.close()
    assert not errors, errors


def test_refresh_button_shows_step_results(browser, base_url):
    """v37: 오래된 가격 경고의 '지금 최신으로' → 단계별 결과 (받음/건너뜀/실패 + 이유) · 닫으면 화면 새로."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.route("**/fonts.googleapis.com/**", lambda r: r.fulfill(body="", content_type="text/css"))
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/#analysis/000020", wait_until="networkidle")
    page.wait_for_selector(".tsk-stale [data-refresh]", timeout=15000)
    page.click(".tsk-stale [data-refresh]")
    page.wait_for_selector(".rf-list", timeout=120000)
    txt = page.inner_text(".t-sheet")
    assert "국내 일봉" in txt and "AI 판단" in txt and ("받음" in txt or "건너뜀" in txt)
    page.click("#rf-close")
    page.wait_for_timeout(800)
    assert not page.query_selector(".t-sheet-ov")
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 2
    ctx.close()
    assert not errors, errors

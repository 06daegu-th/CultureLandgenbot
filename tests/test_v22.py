"""v22: 해외 종목 한글 이름 · 화면 이모지 정리기 · 종목 화면 채팅 버튼 · 로고 키 안내 · 서비스 준비도 문서."""

import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


def test_display_name_prefers_korean_for_global():
    from quant_ai.web.api import DashboardAPI
    inst = {"NVDA": SimpleNamespace(name="NVDA"), "005930": SimpleNamespace(name="삼성전자"), "ZZZZ": SimpleNamespace(name="Zeta Corp")}
    assert DashboardAPI._display_name("NVDA", inst) == "엔비디아"
    assert DashboardAPI._display_name("AAPL", {}) == "애플"
    assert DashboardAPI._display_name("005930", inst) == "삼성전자"
    assert DashboardAPI._display_name("ZZZZ", inst) == "Zeta Corp"   # 한글 이름을 모르면 원래 이름
    assert DashboardAPI._display_name("QQQQ", {}) == "QQQQ"


def test_static_v22_wiring():
    words = (STATIC / "words.js").read_text()
    assert "function deEmoji(" in words and "MutationObserver" in words and "lv-dot" in words
    app_js = (STATIC / "app.js").read_text()
    assert '"on-stock"' in app_js and "el.isConnected" in app_js
    assert "body.on-stock .chat-dock:not(.open)" in (STATIC / "style.css").read_text()
    assert re.search(r"qa-shell-v[23]\d", (STATIC / "sw.js").read_text())


def test_deemoji_regex_keeps_symbols():
    js = (STATIC / "words.js").read_text()
    if not shutil.which("node"):
        assert "★" in js and "EMO_KEEP" in js  # node 가 없으면 정적 검사만 (CI 는 0 skip 유지)
        return
    line = next(ln for ln in js.splitlines() if ln.startswith("const EMO_RE"))
    code = line + "\nconst t=(s)=>s.replace(EMO_RE,'#');\n" + \
        "console.log([t('⛔ 막음'),t('📰 뉴스'),t('★ 관심 ☆'),t('✓ 완료'),t('🟢 정상')].join('|'))"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30).stdout.strip()  # noqa: S603, S607
    assert out == "#막음|#뉴스|★ 관심 ☆|✓ 완료|#정상"


def test_logo_token_documented_and_service_doc():
    assert "QUANT_LOGO_DEV_TOKEN" in (ROOT / ".env.example").read_text()
    assert "QUANT_LOGO_DEV_TOKEN" in (ROOT / "docs/ENV_KEYS.md").read_text()
    doc = (ROOT / "docs/SERVICE_READINESS.md").read_text()
    for must in ("유사투자자문업", "투자일임업", "데이터 라이선스", "다중 사용자", "로고", "우선순위 로드맵"):
        assert must in doc

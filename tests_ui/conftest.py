"""화면 자동 테스트 (Playwright) — 메인 테스트(tests/)와 따로 돈다: CI 의 ui-smoke 작업 · 로컬은 `pytest tests_ui`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests.test_marcap 의 가짜 KRX 자료를 같이 쓴다

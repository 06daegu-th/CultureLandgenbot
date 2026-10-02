"""README·문서의 '테스트 N개 통과'가 실제 테스트 수와 항상 같아야 한다 (전체 실행일 때만 검사)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "docs" / "PLATFORM_STATUS.md"]


def test_documented_test_count_matches_suite():
    from tests.conftest import COLLECTED
    if not COLLECTED.get("full"):
        return  # 일부만 실행할 때는 비교할 수 없다 (건너뜀으로 표시하지 않음 — 0 skip 유지)
    n = COLLECTED["n"]
    claims = []
    for p in DOCS:
        claims += [(p.name, int(m)) for m in re.findall(r"테스트 (\d+)개", p.read_text(encoding="utf-8"))]
    assert claims, "README/PLATFORM_STATUS 에 '테스트 N개' 표기가 없음"
    wrong = [(f, c) for f, c in claims if c != n]
    assert not wrong, f"문서의 테스트 수 {wrong} ≠ 실제 {n}개 — README·PLATFORM_STATUS 를 고치세요"

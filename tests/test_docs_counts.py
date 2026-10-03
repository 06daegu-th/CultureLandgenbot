"""문서에 테스트 개수를 손으로 적지 않는다 — 금방 틀린 숫자가 된다. 개수는 CI(GitHub Actions) 결과가 기준."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
SKIP = {"FINAL_CHECKLIST.md"}  # 자동 생성 문서 (항목 표 — 개수 문구 없음)


def test_docs_do_not_hardcode_test_counts():
    bad = []
    for p in DOCS:
        if p.name in SKIP:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"테스트 \d+개|\b\d{2,} passed\b", line):
                bad.append(f"{p.name}:{i}: {line.strip()[:80]}")
    assert not bad, "문서에 테스트 개수를 적지 마세요 (CI 결과가 기준):\n" + "\n".join(bad)


def test_readme_points_to_ci_as_source_of_truth():
    s = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "CI" in s and 'pip install -e ".[dev]"' in s

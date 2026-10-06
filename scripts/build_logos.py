"""주요 종목 로고를 프로젝트 안에 미리 만들어 둔다 → src/quant_ai/assets/logos/<종목>.svg

네트워크가 막혀도 삼성전자·엔비디아 같은 주요 종목은 항상 진짜 로고가 보이게 하려는 것.
원본 (모두 재배포 허용 라이선스 — 출처·라이선스는 assets/logos/NOTICE.md):
  si  Simple Icons (CC0-1.0)          — 단색 브랜드 마크 → 브랜드색 원 + 흰 마크
  lg  iconify-json/logos (CC0-1.0)    — 컬러 로고 → 흰 원 + 컬러 마크
  st  super-tiny-icons (MIT)          — 정사각 앱 아이콘 → 원 모양으로 자름
로고 자체는 각 회사의 상표다 — 종목을 알아보게 하는 표시로만 쓴다.

쓰는 법 (npm 이 되는 곳에서 한 번):
  cd /tmp && npm pack simple-icons @iconify-json/logos super-tiny-icons
  mkdir si lg st && tar xzf simple-icons-*.tgz -C si && tar xzf iconify-json-logos-*.tgz -C lg && tar xzf super-tiny-icons-*.tgz -C st
  python scripts/build_logos.py /tmp   (압축을 푼 폴더)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src/quant_ai/assets/logos"

SAMSUNG = ["005930", "005935", "006400", "207940", "028260", "032830", "009150", "018260", "000810", "010140", "016360"]
LG = ["066570", "051910", "373220", "034220", "011070", "003550", "051900", "032640"]
PICK: dict[str, str] = {
    "NVDA": "si:nvidia", "AAPL": "si:apple", "MSFT": "lg:microsoft-icon", "GOOGL": "lg:google-icon", "GOOG": "lg:google-icon",
    "AMZN": "st:amazon", "META": "lg:meta-icon", "TSLA": "si:tesla", "AVGO": "lg:broadcom-icon", "TSM": "lg:tsmc",
    "AMD": "si:amd", "NFLX": "si:netflix", "PLTR": "si:palantir", "INTC": "si:intel", "QCOM": "si:qualcomm",
    "MU": "lg:micron-icon", "ARM": "si:arm", "SMCI": "si:supermicro", "ORCL": "lg:oracle", "CRM": "lg:salesforce",
    "ADBE": "lg:adobe-icon", "V": "si:visa", "MA": "lg:mastercard", "KO": "si:cocacola", "NKE": "si:nike",
    "COIN": "si:coinbase", "MSTR": "si:microstrategy", "UBER": "si:uber", "BABA": "si:alibabadotcom",
    "000660": "lg:sk-hynix", "005380": "si:hyundai", "000270": "si:kia", "035420": "si:naver", "035720": "si:kakao",
    **{s: "si:samsung" for s in SAMSUNG}, **{s: "si:lg" for s in LG},
}
SAFE = re.compile(r"<script|on[a-z]+\s*=|javascript:|<foreignObject|xlink:href\s*=\s*\"(?!#)|href\s*=\s*\"(?!#)", re.I)


def _lum(hexc: str) -> float:
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


WIDE = {"samsung": 2.0, "intel": 1.85, "coinbase": 2.0, "supermicro": 1.9, "visa": 1.9, "amd": 1.9, "kakao": 1.9, "uber": 1.8,
        "cocacola": 1.9, "kia": 1.9, "lg": 1.7, "hyundai": 1.7}  # 가로로 긴 글자형 로고는 더 크게


def si(root: Path, slug: str) -> str:
    meta = {x["slug"]: x for x in json.loads((root / "si/package/data/simple-icons.json").read_text())}[slug]
    path = re.search(r'<path d="([^"]+)"', (root / f"si/package/icons/{slug}.svg").read_text()).group(1)
    bg = "#" + meta["hex"]
    fg = "#111827" if _lum(meta["hex"]) > 0.72 else "#ffffff"
    k = WIDE.get(slug, 1.5)
    return (f'<circle cx="32" cy="32" r="32" fill="{bg}"/>'
            f'<g transform="translate({32 - 12 * k:.2f} {32 - 12 * k:.2f}) scale({k})"><path fill="{fg}" d="{path}"/></g>')


def lg(root: Path, name: str) -> str:
    d = json.loads((root / "lg/package/icons.json").read_text())
    ic = d["icons"][name]
    w, h = ic.get("width", d.get("width", 256)), ic.get("height", d.get("height", 256))
    box = 40.0
    s = min(box / w, box / h)
    tx, ty = 32 - w * s / 2, 32 - h * s / 2
    body = re.sub(r'id="([^"]+)"', lambda m: f'id="q{m.group(1)}"', ic["body"])
    body = re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#q{m.group(1)})", body)
    return (f'<circle cx="32" cy="32" r="31.5" fill="#ffffff" stroke="#e5e7eb"/>'
            f'<g transform="translate({tx:.2f} {ty:.2f}) scale({s:.5f})">{body}</g>')


def st(root: Path, name: str) -> str:
    raw = (root / f"st/package/images/svg/{name}.svg").read_text()
    vb = re.search(r'viewBox="([^"]+)"', raw).group(1)
    inner = re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", raw.strip(), flags=re.S)
    return (f'<clipPath id="c"><circle cx="32" cy="32" r="32"/></clipPath>'
            f'<g clip-path="url(#c)"><svg x="0" y="0" width="64" height="64" viewBox="{vb}">{inner}</svg></g>')


def build(root: Path) -> dict[str, str]:
    OUT.mkdir(parents=True, exist_ok=True)
    made = {}
    for sym, src in PICK.items():
        kind, name = src.split(":", 1)
        body = {"si": si, "lg": lg, "st": st}[kind](root, name)
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">{body}</svg>'
        if SAFE.search(svg):
            raise SystemExit(f"{sym}: 안전하지 않은 SVG 요소")
        (OUT / f"{sym}.svg").write_text(svg)
        made[sym] = src
    (OUT / "index.json").write_text(json.dumps(made, ensure_ascii=False, indent=1, sort_keys=True))
    return made


if __name__ == "__main__":
    print(len(build(Path(sys.argv[1] if len(sys.argv) > 1 else "."))), "개 로고")

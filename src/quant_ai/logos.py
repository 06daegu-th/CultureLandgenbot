"""종목 로고 — 검색·관심종목·포트폴리오·종목 상세·뉴스 칩에 회사 아이콘.

순서 (처음 성공한 것을 artifacts/logos 에 저장해 다음부터 바로):
  1. 직접 넣은 파일   artifacts/logos/custom/<종목>.png|.svg|.jpg  (원하는 로고로 바꾸고 싶을 때)
  2. 미국 종목        Financial Modeling Prep 공개 로고 이미지 (키 없음)
  3. 회사 홈페이지     종목 정보(프로필)의 website 또는 아래 국내 주요 기업 도메인 → 파비콘(구글 s2, 128px)
  4. 이니셜 아이콘     회사 이름 첫 글자 + 종목별 고정 색 (네트워크가 막혀도 항상 무언가 보인다)
가져오기 실패는 7일 동안 다시 시도하지 않는다 (화면이 느려지지 않게). QUANT_LOGOS=off 면 네트워크를 쓰지 않고 이니셜만.
로고는 각 회사의 상표다 — 종목 식별용 표시로만 쓴다 (docs/DATA_LICENSES.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

FMP_URL = "https://financialmodelingprep.com/image-stock/{sym}.png"
FAVICON_URL = "https://www.google.com/s2/favicons?domain={domain}&sz=128"
RETRY_AFTER = 7 * 86400
MAX_BYTES = 300_000
TYPES = {b"\x89PNG": ("png", "image/png"), b"\xff\xd8\xff": ("jpg", "image/jpeg"), b"GIF8": ("gif", "image/gif"),
         b"RIFF": ("webp", "image/webp"), b"<svg": ("svg", "image/svg+xml"), b"<?xm": ("svg", "image/svg+xml")}

# 국내 주요 기업 홈페이지 (프로필에 website 가 없을 때) — 종목코드 → 도메인
KR_DOMAINS = {
    "005930": "samsung.com", "005935": "samsung.com", "000660": "skhynix.com", "373220": "lgensol.com",
    "207940": "samsungbiologics.com", "005380": "hyundai.com", "000270": "kia.com", "068270": "celltrion.com",
    "005490": "posco-holdings.com", "035420": "navercorp.com", "035720": "kakaocorp.com", "051910": "lgchem.com",
    "006400": "samsungsdi.com", "105560": "kbfg.com", "055550": "shinhangroup.com", "012330": "mobis.co.kr",
    "028260": "samsungcnt.com", "066570": "lg.com", "003550": "lgcorp.com", "096770": "skinnovation.com",
    "034730": "sk.co.kr", "017670": "sktelecom.com", "030200": "kt.com", "015760": "kepco.co.kr",
    "032830": "samsunglife.com", "086790": "hanafn.com", "316140": "woorifg.com", "033780": "ktng.com",
    "009150": "samsungsem.com", "018260": "samsungsds.com", "010130": "koreazinc.co.kr", "011200": "hmm21.com",
    "259960": "krafton.com", "323410": "kakaobank.com", "377300": "kakaopay.com", "251270": "netmarble.com",
    "036570": "ncsoft.com", "010950": "s-oil.com", "024110": "ibk.co.kr", "012450": "hanwhaaerospace.com",
    "042660": "hanwhaocean.com", "000810": "samsungfire.com", "090430": "apgroup.com", "051900": "lghnh.com",
    "011170": "lottechem.com", "004020": "hyundai-steel.com", "000720": "hdec.kr", "047050": "poscointl.com",
    "003490": "koreanair.com", "352820": "hybecorp.com", "041510": "smentertainment.com", "035900": "jype.com",
    "247540": "ecopro.co.kr", "086520": "ecopro.co.kr", "028300": "hlbpharma.com", "196170": "alteogen.com",
    "263750": "pearlabyss.com", "293490": "kakaogames.com", "011070": "lginnotek.com", "034220": "lgdisplay.com",
    "009830": "hanwhasolutions.com", "000880": "hanwha.co.kr", "001040": "cj.net", "097950": "cj.co.kr",
    "139480": "emart.com", "004170": "shinsegae.com", "023530": "lotteshopping.com", "021240": "coway.com",
    "161390": "hankooktire.com", "078930": "gsholdings.com", "006800": "miraeasset.com", "016360": "samsungpop.com",
    "039490": "kiwoom.com", "071050": "koreainvestment.com", "005830": "idbins.com", "010140": "samsungshi.com",
    "329180": "hd.com", "009540": "hd.com", "267250": "hd.com", "042700": "hanmisemi.com", "064350": "hyundai-rotem.co.kr",
    "047810": "koreaaero.com", "012510": "douzone.com", "000100": "yuhan.co.kr", "128940": "hanmi.co.kr",
}
PALETTE = ("#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#6366f1", "#84cc16")


def _dir(app) -> Path:
    d = Path(app.settings.artifacts_dir) / "logos"
    (d / "custom").mkdir(parents=True, exist_ok=True)
    return d


def _safe(sym: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]", "", sym.upper())[:16]
    if not s or s.startswith("."):
        raise ValueError("종목 코드가 이상합니다")
    return s


def kind_of(data: bytes) -> tuple[str, str] | None:
    head = data[:5].lstrip()
    for magic, t in TYPES.items():
        if head.startswith(magic):
            return t
    return None


def domain_for(app, sym: str) -> str | None:
    from . import ops
    data = ops.get_state(app.engine, f"profile:{sym}").get("data") or {}
    web = (data.get("company") or {}).get("website") or data.get("website")
    if web:
        host = urlparse(web if "://" in web else f"https://{web}").hostname or ""
        return host.removeprefix("www.") or None
    return KR_DOMAINS.get(sym)


def candidates(app, sym: str) -> list[tuple[str, str]]:
    out = []
    if not sym[:1].isdigit():
        out.append(("fmp", FMP_URL.format(sym=sym.replace(".", "-"))))
    dom = domain_for(app, sym)
    if dom:
        out.append(("favicon", FAVICON_URL.format(domain=dom)))
    return out


def _fetch(url: str, timeout: float = 4.0) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("https 만")
    req = urllib.request.Request(url, headers={"User-Agent": "quant-ai/0.18 (+logo)"})  # noqa: S310 - https 확인됨
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("너무 큼")
    return data


def monogram(sym: str, name: str | None = None) -> bytes:
    """이니셜 아이콘 (SVG) — 이름 첫 글자 (한글이면 한 글자, 영문이면 두 글자), 종목별 고정 색."""
    label = (name or sym).strip()
    label = re.sub(r"^(주식회사|\(주\)|㈜)\s*", "", label)
    first = label[:1]
    text = first if re.match(r"[가-힣]", first) else re.sub(r"[^A-Za-z0-9]", "", label)[:2].upper() or sym[:2].upper()
    color = PALETTE[int(hashlib.sha256(sym.encode()).hexdigest(), 16) % len(PALETTE)]
    esc = text.replace("&", "&amp;").replace("<", "&lt;")
    size = 30 if len(text) == 1 else 24
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
            f'<circle cx="32" cy="32" r="32" fill="{color}"/>'
            f'<text x="32" y="33" dy=".35em" text-anchor="middle" font-family="Pretendard,Apple SD Gothic Neo,Malgun Gothic,sans-serif" '
            f'font-size="{size}" font-weight="700" fill="#fff">{esc}</text></svg>').encode()


def get(app, sym: str, name: str | None = None, fetch=None, now: float | None = None) -> tuple[bytes, str, str]:
    """(이미지, content-type, 출처). 항상 무언가 돌려준다."""
    sym = _safe(sym)
    d = _dir(app)
    now = now or time.time()
    for f in sorted((d / "custom").glob(f"{sym}.*")):
        data = f.read_bytes()
        t = kind_of(data)
        if t:
            return data, t[1], "custom"
    meta_p = d / f"{sym}.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    if meta.get("file") and (d / meta["file"]).exists():
        return (d / meta["file"]).read_bytes(), meta["type"], meta.get("source", "cache")
    off = (os.environ.get("QUANT_LOGOS") or "").lower() in ("off", "0", "false")
    if not off and now - float(meta.get("failed_at") or 0) > RETRY_AFTER:
        for src, url in candidates(app, sym):
            try:
                data = (fetch or _fetch)(url)
                t = kind_of(data)
                if not t or t[0] == "svg" or len(data) < 200:  # 외부 SVG 는 받지 않음(스크립트 위험) · 빈 기본 파비콘은 거른다
                    continue
                fn = f"{sym}.{t[0]}"
                (d / fn).write_bytes(data)
                meta_p.write_text(json.dumps({"file": fn, "type": t[1], "source": src, "url": url, "at": now}))
                return data, t[1], src
            except Exception as e:  # noqa: BLE001 - 다음 후보로
                log.debug("로고 %s %s 실패: %s", sym, src, e)
        meta_p.write_text(json.dumps({"failed_at": now}))
    return monogram(sym, name), "image/svg+xml", "monogram"


__all__ = ["get", "monogram", "candidates", "domain_for", "kind_of", "KR_DOMAINS"]

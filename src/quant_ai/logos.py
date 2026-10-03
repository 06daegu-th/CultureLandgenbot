"""종목 로고 — 검색·관심종목·포트폴리오·종목 상세·뉴스·홈 어디서나 같은 회사 아이콘.

순서 (처음 성공한 것을 artifacts/logos 에 저장해 다음부터 바로):
  1. 직접 넣은 파일   artifacts/logos/custom/<종목>.png|.svg|.jpg  (대소문자 무관 — 원하는 로고로 바꾸고 싶을 때)
  1b. 내장 로고 (v23) 프로젝트에 들어 있는 주요 종목 로고 (assets/logos — 삼성전자·SK하이닉스·엔비디아·애플 등 53개)
                     네트워크가 막혀도 항상 같은 진짜 로고. 목록·출처는 assets/logos/NOTICE.md
  2. 국내 종목        토스증권 공개 아이콘 → 알파스퀘어 공개 아이콘 (실제 회사 로고 · 키 없음)
     미국 종목        Financial Modeling Prep 공개 로고 이미지 (키 없음)
  3. 회사 홈페이지     종목 정보(프로필)의 website 또는 아래 국내 주요 기업 도메인 → 파비콘(구글 s2, 128px)
  4. 기본 기업 아이콘 (v23) 건물 모양 + 업종 색 (ETF 는 'ETF') — 못 받았거나 받는 중일 때 (이니셜 대신)
실패 기억: 네트워크 오류·차단(401/403/429)은 1시간 뒤, '그 종목 로고가 없음'(404)은 7일 뒤 다시 시도 (화면이 느려지지 않게).
v21: 화면은 기다리지 않는다 — 처음 보는 종목은 이니셜을 바로 보내고 진짜 로고는 뒤에서 받아 둔다 (다음 화면부터 로고).
     브라우저와 같은 User-Agent 로 받는다 (프로그램 이름이면 막는 CDN 이 있다) · 예전 버전의 실패 기록은 무시하고 다시 받는다.
상용 서비스: QUANT_LOGO_DEV_TOKEN 이 있으면 logo.dev(라이선스 로고 API)를 가장 먼저 쓴다 (국내 .KS · 미국 티커).
QUANT_LOGOS=off 면 네트워크를 쓰지 않고 이니셜만. QUANT_LOGO_SOURCES=logodev,toss,alpha,fmp,favicon 으로 쓸 출처·순서를 바꿀 수 있다.
미리 받기: ./run.sh logos (관심·보유·주요 종목) · 관심종목 ★ 를 누르면 그 종목은 바로 받아 둔다.
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
TOSS_URL = "https://static.toss.im/png-icons/securities/icn-sec-fill-{sym}.png"
ALPHA_URL = "https://file.alphasquare.co.kr/media/images/stock_logo/kr/{sym}.png"
FAVICON_URL = "https://www.google.com/s2/favicons?domain={domain}&sz=128"
LOGODEV_URL = "https://img.logo.dev/ticker/{sym}?token={token}&size=128&format=png&fallback=404"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
META_V = 2  # 실패 기록 형식 — 이보다 옛 기록(v19~v20: 403 도 '없음'으로 7일 막던 것)은 무시
RETRY_AFTER = 7 * 86400  # 로고가 없다고 확인된 종목
RETRY_NET = 3600  # 네트워크 오류 — 연결이 돌아오면 금방 다시
SOURCES = ("logodev", "toss", "alpha", "fmp", "favicon")
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
    from .companies import master
    c = master().get(sym)
    return (c.website if c else None) or KR_DOMAINS.get(sym)


def _sources() -> tuple[str, ...]:
    v = (os.environ.get("QUANT_LOGO_SOURCES") or "").strip()
    if not v:
        return SOURCES
    return tuple(x for x in (y.strip().lower() for y in v.split(",")) if x in SOURCES)


def candidates(app, sym: str) -> list[tuple[str, str]]:
    kr = sym[:1].isdigit()
    out = []
    token = os.environ.get("QUANT_LOGO_DEV_TOKEN", "").strip()
    for src in _sources():
        if src == "logodev":
            if token:
                out.append(("logodev", LOGODEV_URL.format(sym=f"{sym}.KS" if kr else sym, token=token)))
        elif src == "toss" and kr:
            out.append(("toss", TOSS_URL.format(sym=sym)))
        elif src == "alpha" and kr:
            out.append(("alpha", ALPHA_URL.format(sym=sym)))
        elif src == "fmp" and not kr:
            out.append(("fmp", FMP_URL.format(sym=sym.replace(".", "-"))))
        elif src == "favicon":
            dom = domain_for(app, sym)
            if dom:
                out.append(("favicon", FAVICON_URL.format(domain=dom)))
    return out


def _fetch(url: str, timeout: float = 4.0) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("https 만")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/avif,image/webp,image/png,image/*;q=0.8,*/*;q=0.5"})  # noqa: S310 - https 확인됨
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("너무 큼")
    return data


def monogram(sym: str, name: str | None = None) -> bytes:
    """이니셜 아이콘 (SVG) — 이름 첫 글자 (한글이면 한 글자, 영문이면 두 글자), 종목별 고정 색 + 은은한 그라데이션."""
    label = (name or sym).strip()
    label = re.sub(r"^(주식회사|\(주\)|㈜)\s*", "", label)
    first = label[:1]
    text = first if re.match(r"[가-힣]", first) else re.sub(r"[^A-Za-z0-9]", "", label)[:2].upper() or sym[:2].upper()
    h = int(hashlib.sha256(sym.encode()).hexdigest(), 16)
    color = PALETTE[h % len(PALETTE)]
    esc = text.replace("&", "&amp;").replace("<", "&lt;")
    size = 30 if len(text) == 1 else 24
    gid = f"g{h % 100000}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{color}"/>'
            f'<stop offset="1" stop-color="{color}" stop-opacity=".72"/></linearGradient></defs>'
            f'<circle cx="32" cy="32" r="32" fill="url(#{gid})"/>'
            f'<text x="32" y="33" dy=".35em" text-anchor="middle" font-family="Pretendard,Apple SD Gothic Neo,Malgun Gothic,sans-serif" '
            f'font-size="{size}" font-weight="700" fill="#fff">{esc}</text></svg>').encode()


SECTOR_TINT = {"IT": "#3b5b8c", "반도체": "#3b5b8c", "커뮤니케이션": "#5b4b8a", "경기소비재": "#8a5a3b", "필수소비재": "#5a7a3b",
               "헬스케어": "#2f7a6d", "금융": "#3b6b8a", "산업재": "#6b6b6b", "소재": "#7a6a3b", "에너지": "#8a4b3b",
               "유틸리티": "#4b6b7a", "부동산": "#6b5b4b", "ETF": "#475569"}
BUILDING = ("M20 46V22l12-6 12 6v24h-6V38h-12v12h-6z M25 26h4v4h-4z M35 26h4v4h-4z M25 32h4v3h-4z M35 32h4v3h-4z")


def default_icon(app, sym: str, name: str | None = None) -> bytes:
    """기본 기업 아이콘 (SVG) — 진짜 로고가 없을 때. 건물 모양 + 업종 색 (회색 계열로 차분하게) · ETF 는 'ETF' 글자."""
    sector, etf = None, False
    try:
        from .companies import identity
        idt = identity(app, sym, name)
        sector, etf = idt.get("sector"), idt.get("etf")
    except Exception:  # noqa: BLE001, S110 - 업종을 몰라도 회색으로
        pass
    color = SECTOR_TINT.get(sector or "", "#64748b")
    inner = ('<text x="32" y="33" dy=".35em" text-anchor="middle" font-family="Pretendard,Apple SD Gothic Neo,sans-serif" '
             'font-size="17" font-weight="700" fill="#fff" letter-spacing=".5">ETF</text>') if etf else \
        f'<path fill="#fff" fill-opacity=".92" d="{BUILDING}"/><rect x="18" y="46" width="28" height="2.5" rx="1" fill="#fff" fill-opacity=".92"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
            f'<circle cx="32" cy="32" r="32" fill="{color}"/>{inner}</svg>').encode()


def _name(app, sym: str) -> str | None:
    """이름 없이 불렀을 때 (홈·알림 등) 종목 이름을 찾아 이니셜에 쓴다."""
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import Instrument
    try:
        with session_scope(app.engine) as s:
            n = s.scalar(select(Instrument.name).where(Instrument.symbol == sym).limit(1))
    except Exception:  # noqa: BLE001 - 이름이 없어도 코드로 그린다
        return None
    return n if n and n != sym else None


def _is_net_error(e: Exception) -> bool:
    import socket
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500 or e.code in (401, 403, 429)  # 차단·한도는 잠시 뒤 다시 · 404 등은 '없음'
    return isinstance(e, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError))


def get(app, sym: str, name: str | None = None, fetch=None, now: float | None = None, force: bool = False,
        block: bool = True) -> tuple[bytes, str, str]:
    """(이미지, content-type, 출처). 항상 무언가 돌려준다. force=True 면 실패 기억을 무시하고 다시 받는다.
    block=False (화면용): 받아 둔 로고가 없으면 이니셜을 바로 돌려주고 뒤에서 받는다 → 출처 'pending'."""
    sym = _safe(sym)
    d = _dir(app)
    now = now or time.time()
    for f in sorted((d / "custom").iterdir()):
        if f.is_file() and f.stem.upper() == sym:
            data = f.read_bytes()
            t = kind_of(data)
            if t:
                return data, t[1], "custom"
    from .companies import bundled_logo
    data = bundled_logo(sym)
    if data:
        return data, "image/svg+xml", "bundled"
    meta_p = d / f"{sym}.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    if meta.get("file") and (d / meta["file"]).exists() and not force:
        return (d / meta["file"]).read_bytes(), meta["type"], meta.get("source", "cache")
    if meta.get("failed_at") and meta.get("v") != META_V:
        meta = {}  # 옛 버전의 실패 기록 → 다시 받아 본다
    off = (os.environ.get("QUANT_LOGOS") or "").lower() in ("off", "0", "false")
    wait = RETRY_NET if meta.get("net") else RETRY_AFTER
    due = not off and (force or now - float(meta.get("failed_at") or 0) > wait)
    if due and not block:
        _background(app, sym, name, fetch)  # 화면은 기다리지 않는다 — 이니셜을 먼저, 진짜 로고는 다음 화면부터
        return default_icon(app, sym, name), "image/svg+xml", "pending"
    if due:
        net_only = True
        tried = []
        for src, url in candidates(app, sym):
            try:
                data = (fetch or _fetch)(url)
                t = kind_of(data)
                if not t or t[0] == "svg" or len(data) < 200:  # 외부 SVG 는 받지 않음(스크립트 위험) · 빈 기본 파비콘은 거른다
                    net_only = False
                    tried.append(f"{src}: 이미지 아님")
                    continue
                fn = f"{sym}.{t[0]}"
                for old in d.glob(f"{sym}.*"):
                    if old.suffix != ".json" and old.name != fn:
                        old.unlink()
                (d / fn).write_bytes(data)
                meta_p.write_text(json.dumps({"file": fn, "type": t[1], "source": src, "url": url, "at": now}))
                return data, t[1], src
            except Exception as e:  # noqa: BLE001 - 다음 후보로
                net_only = net_only and _is_net_error(e)
                tried.append(f"{src}: {type(e).__name__}")
                log.debug("로고 %s %s 실패: %s", sym, src, e)
        if tried:
            meta_p.write_text(json.dumps({"failed_at": now, "net": net_only, "tried": tried, "v": META_V}))
    return default_icon(app, sym, name), "image/svg+xml", "default"


_inflight: set[str] = set()


def _background(app, sym: str, name: str | None, fetch=None) -> None:
    import threading
    if sym in _inflight or len(_inflight) > 16:  # 같은 종목은 한 번만 · 한꺼번에 너무 많이 받지 않는다
        return
    _inflight.add(sym)

    def run():
        try:
            get(app, sym, name, fetch=fetch, block=True)
        except Exception as e:  # noqa: BLE001
            log.debug("로고 뒤에서 받기 실패 %s: %s", sym, e)
        finally:
            _inflight.discard(sym)
    threading.Thread(target=run, name=f"logo-{sym}", daemon=True).start()


def prefetch(app, symbols: list[str], force: bool = False, fetch=None) -> dict:
    """여러 종목 로고를 미리 받아 둔다 → {출처: 개수} · 실패 종목."""
    out: dict[str, int] = {}
    missing = []
    for sym in dict.fromkeys(symbols):
        try:
            _, _, src = get(app, sym, fetch=fetch, force=force)
        except ValueError:
            continue
        out[src] = out.get(src, 0) + 1
        if src in ("monogram", "default"):
            missing.append(sym)
    return {"by_source": out, "missing": missing[:50], "n": sum(out.values())}


__all__ = ["get", "prefetch", "monogram", "default_icon", "candidates", "domain_for", "kind_of", "KR_DOMAINS"]

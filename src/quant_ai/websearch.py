"""v38 웹 검색 — 키 없이 쓰는 공용 검색 + 모델별 '검색 근거' 호출.

1) search(query): 키 없음. 뉴스는 Google News RSS(한국어·영어), 일반 웹은 DuckDuckGo HTML 결과.
   → [{title, url, source, date, snippet, kind}] — 어느 AI 모델이든 도구(web_search)로 부른다.
2) read_page(url): 공개 웹 페이지 본문(앞부분)만. 내부망·로컬 주소는 막는다(SSRF 방지) · 크기 제한.
3) gemini_grounded(...): Gemini 의 Google 검색 근거(grounding) — 답과 함께 실제로 본 웹 출처를 돌려준다.
4) groq_compound(...): Groq compound 모델(자체 웹 검색 내장).
검색 결과 문장은 '데이터'다 — 그 안의 지시는 따르지 않는다 (채팅 시스템 지시에 명시).
"""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (QuantAI research bot; +https://github.com)"
MAX_PAGE = 600_000


def _get(url: str, timeout: float = 8.0, headers: dict | None = None, data: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - 공개 https 만 (read_page 는 주소 검사 후)
        return r.read(MAX_PAGE)


def _strip(s: str) -> str:
    s = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# ------------------------------------------------------------------ 키 없는 검색
def google_news(query: str, lang: str = "ko", days: int = 7, get=None, n: int = 8) -> list[dict]:
    get = get or _get
    hl, gl, ceid = ("ko", "KR", "KR:ko") if lang == "ko" else ("en-US", "US", "US:en")
    q = urllib.parse.quote(f"{query} when:{max(1, int(days))}d")
    raw = get(f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}").decode("utf-8", "replace")
    out = []
    for item in re.findall(r"<item>(.*?)</item>", raw, flags=re.S)[:n]:
        def tag(t, item=item):
            m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", item, flags=re.S)
            return html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))).strip() if m else ""
        title, src = tag("title"), tag("source")
        if src and title.endswith(f" - {src}"):
            title = title[: -len(src) - 3]
        try:
            d = parsedate_to_datetime(tag("pubDate")).astimezone(UTC).isoformat()
        except (TypeError, ValueError):
            d = None
        out.append({"title": title[:200], "url": tag("link"), "source": src or "Google News", "date": d, "snippet": "", "kind": "news"})
    return out


def duckduckgo(query: str, get=None, n: int = 6) -> list[dict]:
    get = get or _get
    raw = get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)).decode("utf-8", "replace")
    out = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', raw, flags=re.S):
        href, title, rest = m.group(1), _strip(m.group(2)), m.group(3)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(href)).query)
        url = q.get("uddg", [html.unescape(href)])[0]
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, flags=re.S)
        if url.startswith("http"):
            out.append({"title": title[:200], "url": url, "source": urllib.parse.urlparse(url).netloc, "date": None,
                        "snippet": _strip(sn.group(1))[:300] if sn else "", "kind": "web"})
        if len(out) >= n:
            break
    return out


def search(query: str, kind: str = "auto", days: int = 7, get=None, n: int = 8) -> dict:
    """뉴스(한국어 → 부족하면 영어) + 일반 웹. 실패해도 예외 대신 {'error'} 와 받은 만큼."""
    query = (query or "").strip()[:200]
    if not query:
        return {"query": query, "results": [], "error": "검색어가 비었어요"}
    results, errors = [], []
    if kind in ("auto", "news"):
        for lang in ("ko", "en"):
            try:
                results += google_news(query, lang, days, get, n)
            except Exception as e:  # noqa: BLE001
                errors.append(f"news-{lang}: {type(e).__name__}: {e}"[:160])
            if len(results) >= n // 2:
                break
    if kind in ("auto", "web") and len(results) < n:
        try:
            results += duckduckgo(query, get, n - len(results))
        except Exception as e:  # noqa: BLE001
            errors.append(f"web: {type(e).__name__}: {e}"[:160])
    seen, uniq = set(), []
    for r in results:
        k = re.sub(r"\W+", "", r["title"].lower())[:60]
        if k and k not in seen:
            seen.add(k)
            uniq.append(r)
    out = {"query": query, "results": uniq[:n], "at": datetime.now(UTC).isoformat()}
    if errors and not uniq:
        from .refresh import friendly
        out["error"] = friendly(errors[0])
        out["detail"] = "; ".join(errors)[:300]
    return out


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """다른 주소로 넘겨도(리다이렉트) 그 주소가 공개 주소인지 다시 확인한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        p = urllib.parse.urlparse(newurl)
        if p.scheme not in ("http", "https") or not p.hostname or not _public_host(p.hostname):
            raise urllib.error.URLError("내부망 주소로 넘기는 리다이렉트는 따라가지 않아요")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_get(url: str, timeout: float = 8.0) -> bytes:
    opener = urllib.request.build_opener(_SafeRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})  # noqa: S310
    with opener.open(req, timeout=timeout) as r:  # noqa: S310 - 주소 검사 후
        ctype = r.headers.get("Content-Type", "")
        if ctype and not any(x in ctype for x in ("text/", "html", "xml", "json")):
            raise ValueError(f"글이 아닌 파일이에요 ({ctype.split(';')[0]})")
        return r.read(MAX_PAGE)


def read_page(url: str, get=None, max_chars: int = 4000, resolver=_public_host) -> dict:
    """공개 웹 페이지 본문 앞부분. 내부망·로컬·비 http(s) 주소는 거절."""
    p = urllib.parse.urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        return {"url": url, "error": "http(s) 주소만 읽을 수 있어요"}
    if not resolver(p.hostname):
        return {"url": url, "error": "내부망·로컬 주소는 읽지 않아요"}
    try:
        raw = (get or _safe_get)(url).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        from .refresh import friendly
        return {"url": url, "error": friendly(e), "detail": f"{type(e).__name__}: {e}"[:200]}
    t = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.S | re.I)
    body = _strip(raw)
    return {"url": url, "title": _strip(t.group(1))[:200] if t else "", "text": body[:max_chars], "chars": len(body)}


# ------------------------------------------------------------------ 모델 자체 검색
def gemini_grounded(api_key: str, model: str, system: str, user: str, post=None, timeout: float = 60.0) -> dict:
    """Gemini + Google 검색 근거 → {text, sources:[{title,url}], queries}. (OpenAI 호환 주소는 검색 도구를 못 써서 원래 API 로)"""
    body = {"system_instruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
            "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model)}:generateContent"
    if post is not None:
        raw = post(url, body)
    else:
        raw = json.loads(_get(url, timeout=timeout, data=json.dumps(body).encode(),
                              headers={"Content-Type": "application/json", "x-goog-api-key": api_key}))
    c = ((raw or {}).get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts") or [])
    gm = c.get("groundingMetadata") or {}
    src = [{"title": (ch.get("web") or {}).get("title", ""), "url": (ch.get("web") or {}).get("uri", "")}
           for ch in gm.get("groundingChunks") or [] if ch.get("web")]
    u = raw.get("usageMetadata") or {}
    return {"text": text.strip(), "sources": src[:10], "queries": gm.get("webSearchQueries") or [],
            "usage": {"prompt_tokens": u.get("promptTokenCount"), "completion_tokens": u.get("candidatesTokenCount")}}


def groq_compound(client, system: str, user: str, model: str = "groq/compound") -> dict:
    """Groq compound (웹 검색 내장). 출처는 응답의 executed_tools 에서 가능한 만큼."""
    payload = client._post("/chat/completions", {"model": model, "messages": [{"role": "system", "content": system},
                                                                          {"role": "user", "content": user}],
                                                "temperature": 0.3, "max_tokens": 4096})
    msg = ((payload.get("choices") or [{}])[0].get("message") or {})
    src = []
    for t in msg.get("executed_tools") or []:
        for r in ((t.get("search_results") or {}).get("results") or []):
            src.append({"title": r.get("title", ""), "url": r.get("url", "")})
    return {"text": (msg.get("content") or "").strip(), "sources": src[:10], "queries": [], "usage": payload.get("usage")}


__all__ = ["search", "google_news", "duckduckgo", "read_page", "gemini_grounded", "groq_compound"]

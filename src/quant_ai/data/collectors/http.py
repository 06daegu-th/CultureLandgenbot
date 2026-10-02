"""표준 라이브러리 기반 HTTP 헬퍼 (외부 의존성 없음)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "quant-ai/0.1 (+research)"


MAX_BYTES = 10 * 1024 * 1024


def get(url: str, params: dict | None = None, timeout: float = 15.0, retries: int = 3, headers: dict | None = None) -> bytes:
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"허용되지 않는 URL 스킴: {url[:40]}")
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})  # noqa: S310 - 스킴 검사됨
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 스킴 검사됨
                data = resp.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError("응답이 너무 큼")
                return data
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read(2000).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001, S110
                pass
            if 400 <= exc.code < 500 and exc.code != 429:  # 키 오류 등 — 다시 해도 같다 (본문에 이유가 있음)
                raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc
            last = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
        except Exception as exc:  # noqa: BLE001 - 네트워크 오류는 재시도
            last = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    safe = re.sub(r"(crtfc_key|api_key|serviceKey)=[^&]+", r"\1=***", url)  # 키가 오류 메시지·로그에 남지 않게
    raise RuntimeError(f"GET 실패: {safe[:200]} ({type(last).__name__}: {last})") from last


def get_json(url: str, params: dict | None = None, **kw) -> dict:
    return json.loads(get(url, params, **kw))

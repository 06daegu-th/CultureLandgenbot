"""표준 라이브러리 기반 HTTP 헬퍼 (외부 의존성 없음)."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

USER_AGENT = "quant-ai/0.1 (+research)"


def get(url: str, params: dict | None = None, timeout: float = 15.0, retries: int = 3) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - 네트워크 오류는 재시도
            last = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"GET 실패: {url}") from last


def get_json(url: str, params: dict | None = None, **kw) -> dict:
    return json.loads(get(url, params, **kw))

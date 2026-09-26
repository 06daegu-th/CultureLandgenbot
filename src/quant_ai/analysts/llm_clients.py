"""LLM 백엔드.

- ``ClaudeClient``: Primary AI (Anthropic SDK, 구조화 출력으로 JSON 스키마 보장)
- ``OpenAICompatClient``: NVIDIA NIM (https://integrate.api.nvidia.com/v1, OpenAI 호환 REST)
- ``NvidiaEmbeddings`` / ``HashingEmbeddings``: RAG 용 임베딩 (키가 없으면 로컬 해싱으로 대체)

주의: NVIDIA Developer Program 무료 엔드포인트는 프로토타이핑/연구/테스트 용도다.
실제 돈이 걸린 Live 운용 전에 해당 모델·계정의 최신 약관을 확인할 것.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

import numpy as np


class LLMError(RuntimeError):
    pass


class LLMClient(ABC):
    model: str
    provider: str = "unknown"
    last_usage: tuple[int, int] | None = None  # (input_tokens, output_tokens) — 비용 추적용

    @abstractmethod
    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...


def extract_json(text: str) -> dict:
    """추론 모델 출력에서 JSON 객체만 추출 (<think> 블록, 코드펜스 제거)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"JSON 없음: {text[:200]!r}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON 파싱 실패: {exc}") from exc


class ClaudeClient(LLMClient):
    """Primary AI. 기본 모델 claude-opus-5, adaptive thinking, 서버측 refusal fallback 사용."""

    provider = "anthropic"

    def __init__(self, model: str = "claude-opus-5", effort: str = "high", api_key: str | None = None,
                 timeout: float = 180.0, max_retries: int = 2):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - 선택 의존성
            raise LLMError("pip install 'quant-ai[ai]' 필요 (anthropic)") from exc
        self._anthropic = anthropic
        kw = {"timeout": timeout, "max_retries": max_retries}
        self.client = anthropic.Anthropic(api_key=api_key, **kw) if api_key else anthropic.Anthropic(**kw)
        self.model = model
        self.effort = effort

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        a = self._anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.RateLimitError as exc:
            raise LLMError("Claude rate limit") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"Claude API 오류 {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("Claude 연결 실패") from exc
        u = getattr(response, "usage", None)
        self.last_usage = (getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0) if u else None
        if response.stop_reason == "refusal":
            raise LLMError("Claude refusal")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude max_tokens 도달")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)


class OpenAICompatClient(LLMClient):
    """NVIDIA NIM 등 OpenAI 호환 /chat/completions 엔드포인트."""

    provider = "nvidia"

    def __init__(self, api_key: str, model: str = "nvidia/nemotron-3-super-120b-a12b",
                 base_url: str = "https://integrate.api.nvidia.com/v1", timeout: float = 120.0,
                 max_tokens: int = 8192):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def _post(self, path: str, body: dict, retries: int = 3) -> dict:
        if not self.base_url.startswith("https://"):
            raise LLMError("https 엔드포인트만 허용")
        last: Exception | None = None
        for attempt in range(retries):
            req = urllib.request.Request(  # noqa: S310 - https 검사됨
                f"{self.base_url}{path}", data=json.dumps(body).encode(), method="POST",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                         "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - https 확인됨
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in (429, 500, 502, 503, 504):  # 4xx 는 재시도해도 소용없음
                    break
            except Exception as exc:  # noqa: BLE001 - 네트워크 오류 재시도
                last = exc
            time.sleep(min(2 ** attempt, 8))
        raise LLMError(f"{self.base_url}{path} 호출 실패: {last}") from last

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        sys_prompt = (f"{system}\n\n반드시 아래 JSON 스키마를 따르는 JSON 객체 하나만 출력하라. 다른 텍스트 금지.\n"
                      f"{json.dumps(schema, ensure_ascii=False)}")
        payload = self._post("/chat/completions", {
            "model": self.model,
            "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
        })
        u = payload.get("usage") or {}
        self.last_usage = (int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)) if u else None
        try:
            content = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"예상치 못한 응답: {str(payload)[:200]}") from exc
        return extract_json(content)


# ------------------------------------------------------------------ 임베딩
class Embeddings(ABC):
    model: str

    @abstractmethod
    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray: ...


class NvidiaEmbeddings(Embeddings):
    def __init__(self, api_key: str, model: str = "nvidia/nemotron-3-embed-1b",
                 base_url: str = "https://integrate.api.nvidia.com/v1"):
        self._client = OpenAICompatClient(api_key, model, base_url, timeout=60)
        self.model = model

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        payload = self._client._post("/embeddings", {
            "model": self.model, "input": texts, "input_type": kind, "encoding_format": "float",
        })
        vecs = np.array([d["embedding"] for d in sorted(payload["data"], key=lambda d: d["index"])], float)
        return vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)


class HashingEmbeddings(Embeddings):
    """오프라인 대체: 문자 2~3-gram 해싱 벡터. 의미 검색 품질은 낮지만 키워드 유사도는 잡는다."""

    model = "local-hashing-512"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        out = np.zeros((len(texts), self.dim))
        for i, t in enumerate(texts):
            t = re.sub(r"\s+", " ", t.lower())
            for n in (2, 3):
                for j in range(len(t) - n + 1):
                    h = int.from_bytes(hashlib.blake2b(t[j:j + n].encode(), digest_size=4).digest(), "little")
                    out[i, h % self.dim] += 1.0
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)

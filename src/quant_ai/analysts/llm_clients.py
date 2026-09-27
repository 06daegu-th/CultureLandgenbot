"""LLM 백엔드.

- ``ClaudeClient``: Primary AI (Anthropic SDK, 구조화 출력으로 JSON 스키마 보장)
- ``OpenAICompatClient``: OpenAI 호환 REST — NVIDIA NIM · Google Gemini · Groq · Cloudflare Workers AI (``FREE_PROVIDERS``)
- ``NvidiaEmbeddings`` / ``HashingEmbeddings``: RAG 용 임베딩 (키가 없으면 로컬 해싱으로 대체)

주의: 무료 엔드포인트(NVIDIA Developer Program, Gemini 무료 등급 등)는 대부분 프로토타이핑/연구/테스트 용도이며
무료 등급 입력은 서비스 개선에 쓰일 수 있다. 실제 돈이 걸린 Live 운용 전에 각 공급자의 최신 약관을 확인할 것.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

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


@dataclass(frozen=True)
class ProviderSpec:
    """무료(한도 있는) OpenAI 호환 LLM 공급자. 한도는 보수적 기본값이며 계정·시기마다 다르다 → 환경변수로 조정."""

    name: str
    label: str
    base_url: str  # {account} 자리표시자 가능 (Cloudflare)
    models: tuple[str, ...]  # 앞에서부터 시도, 한도 초과(429)·모델 없음(404)이면 다음 모델로
    key_env: str
    daily_requests: int  # 이 시스템이 하루에 스스로 멈추는 호출 수 (공급자 한도보다 낮게)
    rpm: int  # 분당 호출 상한 → 호출 간 최소 간격
    signup: str


FREE_PROVIDERS: dict[str, ProviderSpec] = {
    "gemini": ProviderSpec("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                           ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"), "GEMINI_API_KEY",
                           daily_requests=300, rpm=8, signup="aistudio.google.com → Get API key"),
    "nvidia": ProviderSpec("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1",
                           ("nvidia/nemotron-3-super-120b-a12b",), "NVIDIA_API_KEY",
                           daily_requests=1000, rpm=30, signup="build.nvidia.com → Get API Key"),
    "groq": ProviderSpec("groq", "Groq", "https://api.groq.com/openai/v1",
                         ("openai/gpt-oss-120b", "llama-3.3-70b-versatile"), "GROQ_API_KEY",
                         daily_requests=800, rpm=20, signup="console.groq.com → API Keys"),
    "cloudflare": ProviderSpec("cloudflare", "Cloudflare Workers AI",
                               "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
                               ("@cf/google/gemma-3-12b-it",), "CLOUDFLARE_API_TOKEN",
                               daily_requests=150, rpm=30,
                               signup="dash.cloudflare.com → AI → Workers AI → REST API 토큰 + Account ID"),
}

_LAST_CALL: dict[str, float] = {}
_RATE_LOCK = threading.Lock()


class OpenAICompatClient(LLMClient):
    """OpenAI 호환 /chat/completions (NVIDIA NIM · Gemini · Groq · Cloudflare Workers AI 등).

    models 를 여러 개 주면 한도 초과(429)·모델 없음(404) 시 다음 모델로 넘어가고, 그 모델은 그날 다시 쓰지 않는다.
    rpm 을 주면 같은 공급자 호출 사이에 최소 간격을 둔다 (무료 분당 한도 보호).
    """

    provider = "nvidia"

    def __init__(self, api_key: str, model: str = "nvidia/nemotron-3-super-120b-a12b",
                 base_url: str = "https://integrate.api.nvidia.com/v1", timeout: float = 60.0,
                 max_tokens: int = 8192, provider: str | None = None, fallback_models: tuple[str, ...] = (),
                 rpm: int | None = None):
        self.api_key = api_key
        self.model = model
        self.models = (model, *[m for m in fallback_models if m != model])
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.retries = 2  # 네트워크 오류·5xx 재시도 (응답 없는 공급자가 사이클 전체를 붙잡지 않도록 짧게)
        self.call_lock = threading.Lock()  # 같은 클라이언트의 동시 호출 직렬화 (사용량 기록이 섞이지 않게)
        self.provider = provider or self.provider
        self.min_interval = 60.0 / rpm if rpm else 0.0
        self.exhausted: dict[str, str] = {}  # 모델 → 한도 초과한 날짜 (UTC)

    @classmethod
    def from_spec(cls, spec: ProviderSpec, api_key: str, models: tuple[str, ...] | None = None,
                  account: str | None = None, rpm: int | None = None) -> OpenAICompatClient:
        models = tuple(models or spec.models)
        base = spec.base_url.format(account=account or "")
        if "{account}" in spec.base_url and not account:
            raise LLMError(f"{spec.label}: 계정 ID 가 필요합니다 (CLOUDFLARE_ACCOUNT_ID)")
        return cls(api_key, models[0], base, provider=spec.name, fallback_models=models[1:],
                   rpm=rpm if rpm is not None else spec.rpm)

    @property
    def backend_id(self) -> str:
        return self.models[0]

    def _throttle(self) -> None:
        if not self.min_interval:
            return
        with _RATE_LOCK:
            wait = _LAST_CALL.get(self.provider, 0.0) + self.min_interval - time.monotonic()
            _LAST_CALL[self.provider] = time.monotonic() + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def _send(self, url: str, body: dict) -> dict:
        req = urllib.request.Request(  # noqa: S310 - https 검사됨
            url, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                     "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - https 확인됨
            return json.loads(resp.read())

    def _post(self, path: str, body: dict, retries: int | None = None) -> dict:
        if not self.base_url.startswith("https://"):
            raise LLMError("https 엔드포인트만 허용")
        last: Exception | None = None
        for attempt in range(retries or self.retries):
            self._throttle()
            try:
                return self._send(f"{self.base_url}{path}", body)
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 429) and len(self.models) > 1:
                    raise  # 모델 폴백은 호출자가 처리
                try:  # 공급자가 준 이유(예: "API key not valid")를 오류에 담는다
                    detail = exc.read()[:300].decode("utf-8", "replace").replace("\n", " ")
                except Exception:  # noqa: BLE001
                    detail = ""
                last = RuntimeError(f"HTTP {exc.code} {detail}".strip())
                if exc.code not in (429, 500, 502, 503, 504):  # 4xx 는 재시도해도 소용없음
                    break
            except Exception as exc:  # noqa: BLE001 - 네트워크 오류 재시도
                last = exc
            time.sleep(min(2 ** attempt, 8))
        raise LLMError(f"{self.base_url}{path} 호출 실패: {last}") from last

    def _chat(self, messages: list[dict]) -> dict:
        today = datetime.now(UTC).date().isoformat()
        errors = []
        for m in self.models:
            if self.exhausted.get(m) == today:
                continue
            try:
                payload = self._post("/chat/completions", {"model": m, "messages": messages, "temperature": 0.2,
                                                          "max_tokens": self.max_tokens})
                self.model = m  # 실제로 답한 모델 (감사 로그·성적표용)
                return payload
            except urllib.error.HTTPError as exc:
                errors.append(f"{m}: HTTP {exc.code}")
                self.exhausted[m] = today  # 한도 초과·없는 모델 → 오늘은 다음 모델로
        raise LLMError(f"{self.provider}: 사용 가능한 모델 없음 ({'; '.join(errors) or '오늘 한도 모두 소진'})")

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        sys_prompt = (f"{system}\n\n반드시 아래 JSON 스키마를 따르는 JSON 객체 하나만 출력하라. 다른 텍스트 금지.\n"
                      f"{json.dumps(schema, ensure_ascii=False)}")
        self.last_usage = None
        payload = self._chat([{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}])
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

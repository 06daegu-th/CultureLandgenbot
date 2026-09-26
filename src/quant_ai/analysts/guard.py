"""LLM Guard: 모든 LLM 호출을 감싸는 캐시 · 예산 · 감사 로그.

- 캐시: 같은 모델·같은 입력이면 TTL 동안 재호출하지 않는다 (장중 15분 주기 × 종목 수 비용 절감).
- 예산: 하루 비용(USD) 한도를 넘으면 호출하지 않고 기권 → 시스템은 나머지 AI 로 계속 동작.
- 감사: 모든 호출의 모델·지연·토큰·비용·응답을 `llm_calls` 에 남긴다 (사후 설명·분쟁 대응).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from ..data.db import session_scope
from ..data.models import LLMCall
from .llm_clients import LLMClient, LLMError

UTC = UTC

# USD / 1M tokens (input, output). 모르는 모델은 보수적으로 가장 비싼 값 적용.
PRICES = {
    "claude-opus-5": (5.0, 25.0), "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0), "claude-fable-5-1": (10.0, 50.0),
}
UNKNOWN_PRICE = (10.0, 50.0)


def estimate_cost(provider: str, model: str, usage: tuple[int, int] | None) -> float:
    if usage is None:
        return 0.0
    if provider == "nvidia":  # 무료 엔드포인트 (Production 전환 시 라이선스 비용 별도)
        return 0.0
    pin, pout = PRICES.get(model, UNKNOWN_PRICE)
    return (usage[0] * pin + usage[1] * pout) / 1e6


class GuardedLLM(LLMClient):
    def __init__(self, inner: LLMClient, engine: Engine, analyst: str, daily_budget_usd: float = 20.0,
                 cache_ttl_minutes: float = 60.0):
        self.inner = inner
        self.engine = engine
        self.analyst = analyst
        self.daily_budget_usd = daily_budget_usd
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.model = inner.model
        self.provider = inner.provider

    def spent_today(self) -> float:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with session_scope(self.engine) as s:
            return float(s.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
                                  .where(LLMCall.ts >= start)) or 0.0)

    def _log(self, h: str, status: str, **kw) -> None:
        with session_scope(self.engine) as s:
            s.add(LLMCall(ts=datetime.now(UTC), provider=self.provider, model=self.model, analyst=self.analyst,
                          prompt_hash=h, status=status, **kw))

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        h = hashlib.sha256(json.dumps([self.model, system, user, schema], ensure_ascii=False,
                                      sort_keys=True).encode()).hexdigest()
        with session_scope(self.engine) as s:
            hit = s.scalar(select(LLMCall).where(LLMCall.prompt_hash == h, LLMCall.status == "ok",
                                                 LLMCall.ts >= datetime.now(UTC) - self.cache_ttl)
                           .order_by(LLMCall.ts.desc()))
            cached = dict(hit.response) if hit and hit.response else None
        if cached is not None:
            self._log(h, "cached", latency_ms=0, cost_usd=0.0)
            return cached
        if self.daily_budget_usd >= 0 and self.spent_today() >= self.daily_budget_usd:
            self._log(h, "budget", error=f"일 예산 ${self.daily_budget_usd} 소진")
            raise LLMError(f"LLM 일 예산 ${self.daily_budget_usd} 소진")
        t0 = time.monotonic()
        try:
            out = self.inner.complete_json(system, user, schema)
        except Exception as exc:
            usage = self.inner.last_usage
            self._log(h, "error", latency_ms=int((time.monotonic() - t0) * 1000), error=str(exc)[:2000],
                      input_tokens=usage[0] if usage else None, output_tokens=usage[1] if usage else None,
                      cost_usd=estimate_cost(self.provider, self.model, usage))
            raise
        usage = self.inner.last_usage
        self._log(h, "ok", latency_ms=int((time.monotonic() - t0) * 1000), response=out,
                  input_tokens=usage[0] if usage else None, output_tokens=usage[1] if usage else None,
                  cost_usd=estimate_cost(self.provider, self.model, usage))
        return out


def llm_usage_summary(engine: Engine, days: int = 7) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    out: dict = {"by_model": [], "today_cost": 0.0}
    with session_scope(engine) as s:
        rows = s.execute(select(LLMCall.model, LLMCall.status, func.count(), func.coalesce(func.sum(LLMCall.cost_usd), 0.0),
                                func.avg(LLMCall.latency_ms))
                         .where(LLMCall.ts >= since).group_by(LLMCall.model, LLMCall.status)).all()
        out["today_cost"] = float(s.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
                                           .where(LLMCall.ts >= today)) or 0.0)
    agg: dict[str, dict] = {}
    for model, status, n, cost, lat in rows:
        a = agg.setdefault(model, {"model": model, "calls": 0, "errors": 0, "cached": 0, "cost": 0.0, "latency_ms": None})
        a["calls"] += n
        a["cost"] += float(cost)
        if status in ("error", "budget"):
            a["errors"] += n
        if status == "cached":
            a["cached"] += n
        if status == "ok" and lat is not None:
            a["latency_ms"] = int(lat)
    out["by_model"] = list(agg.values())
    return out

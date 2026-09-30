"""환경변수 기반 설정.

모든 운영 모드와 리스크 한도는 여기서 정의한다. Live 모드는 여러 개의 안전장치를
모두 통과해야만 켜진다 (``Settings.assert_live_allowed``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Mode(str, Enum):
    RESEARCH = "research"  # AI가 시장을 분석
    PREDICT = "predict"  # 내일/다음 시간대 시나리오 생성
    PAPER = "paper"  # 가상매매
    SHADOW = "shadow"  # 실제 주문이었다면 어떻게 됐는지 기록
    LIVE = "live"  # 실제 계좌 자동매매
    REVIEW = "review"  # 오늘 AI가 왜 틀렸는지 분석


LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_REAL_MONEY"


DEFAULT_NEWS_FEEDS = (
    "https://www.yna.co.kr/rss/economy.xml",  # 연합뉴스 경제
    "https://www.hankyung.com/feed/finance",  # 한국경제 증권
    "https://www.mk.co.kr/rss/50200011/",  # 매일경제 증권
    "https://news.google.com/rss/search?q=%EC%BD%94%EC%8A%A4%ED%94%BC&hl=ko&gl=KR&ceid=KR:ko",  # 구글뉴스 '코스피'
)


@dataclass(frozen=True)
class RiskLimits:
    max_position_weight: float = 0.10  # 종목당 최대 비중
    max_gross_exposure: float = 1.0  # 총 노출 (1.0 = 레버리지 없음)
    max_daily_loss_pct: float = 0.02  # 일 손실 한도, 넘으면 당일 신규 매수 중단
    max_order_value: float = 5_000_000  # 1회 주문 최대 금액
    max_orders_per_day: int = 100
    min_confidence: float = 0.55  # 이 확률 미만의 매수 신호는 무시
    max_adv_participation: float = 0.05  # 1회 매수 금액 ≤ 20일 평균 거래대금 × 5% (시장 충격·유동성)
    max_var95: float = 0.04  # 계획 포트폴리오 1일 VaR95 한도 → 넘으면 전체 비중 축소


@dataclass(frozen=True)
class CostModelConfig:
    commission_bps: float = 1.5  # 증권사 수수료 (편도)
    slippage_bps: float = 5.0  # 체결 미끄러짐 가정
    sell_tax_bps: float = 20.0  # 국내 매도 거래세+농특세. 2026년 0.20% (2025년 0.15%) — 매년 세법 확인, QUANT_SELL_TAX_BPS


def _llm_providers(e) -> dict:
    """환경변수 → 키가 있는 무료 LLM 공급자 설정. 모델·일 한도는 QUANT_<이름>_MODELS / _DAILY_LIMIT 로 조정."""
    from .analysts.llm_clients import FREE_PROVIDERS
    out = {}
    for name, spec in FREE_PROVIDERS.items():
        key = e.get(spec.key_env)
        if not key:
            continue
        models = [m.strip() for m in e.get(f"QUANT_{name.upper()}_MODELS", "").split(",") if m.strip()]
        if name == "nvidia" and not models and e.get("QUANT_NVIDIA_MODEL"):
            # 지정 모델을 먼저, 과부하(503)·종료(404) 때 넘어갈 기본 후보를 뒤에
            models = list(dict.fromkeys([e["QUANT_NVIDIA_MODEL"], *spec.models]))
        out[name] = {"key": key, "models": tuple(models) or spec.models,
                     "account": e.get("CLOUDFLARE_ACCOUNT_ID") if name == "cloudflare" else None,
                     "daily": int(e.get(f"QUANT_{name.upper()}_DAILY_LIMIT") or spec.daily_requests)}
    return out


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///quant_ai.db"
    mode: Mode = Mode.RESEARCH
    initial_cash: float = 10_000_000
    artifacts_dir: Path = Path("artifacts")
    risk: RiskLimits = field(default_factory=RiskLimits)
    costs: CostModelConfig = field(default_factory=CostModelConfig)
    # 실매매 안전장치
    live_enabled: bool = False
    live_confirm: str = ""
    live_max_capital: float = 1_000_000  # 소액 Live 상한
    live_small_capital: float = 200_000  # 검증 사다리 '소액 Live' 단계의 실전 운용 상한
    auto_promote: bool = True  # 검증 사다리 통과 시 AI 를 자동으로 가상 장부(Paper)·소액 Live 에 쓴다
    # 외부 API 키 (없으면 해당 수집기는 건너뜀)
    dart_api_key: str | None = field(default=None, repr=False)
    fred_api_key: str | None = field(default=None, repr=False)
    news_feeds: tuple[str, ...] = ()
    # 멀티 AI
    anthropic_enabled: bool = False  # ANTHROPIC_API_KEY 가 있거나 QUANT_PRIMARY_ENABLED=true
    primary_model: str = "claude-opus-5"
    primary_effort: str = "high"
    nvidia_api_key: str | None = field(default=None, repr=False)
    nvidia_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nvidia_embed_model: str = "nvidia/nemotron-3-embed-1b"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    watchlist: tuple[str, ...] = ()
    llm_daily_budget_usd: float = 20.0  # 하루 LLM 비용 상한 (넘으면 LLM 기권, 시스템은 계속 동작)
    # 일봉 기반이라 같은 날 같은 입력(뉴스 변화 없음)은 재호출하지 않는다 → 무료 한도·비용 절약
    llm_cache_minutes: float = 720.0
    # 무료(한도 있는) OpenAI 호환 공급자: {이름: {"key", "models", "account", "daily"}} — Gemini·Groq·Cloudflare·NVIDIA
    llm_providers: dict = field(default_factory=dict, repr=False)
    chat_provider: str = ""  # 사이트 채팅 AI 공급자 (비우면 gemini → claude → groq → nvidia → cloudflare 중 있는 것)
    chat_models: tuple[str, ...] = ()  # 채팅 모델 목록 (비우면 공급자별 최고 품질부터)
    ai_roles: str = ""  # 예: "primary=gemini,second=nvidia,risk=groq,panel=cloudflare" (비우면 자동 배정)
    # 증권사
    strategy: str = "core_satellite"  # core_satellite (검증된 팩터 코어 + AI) / consensus (AI 합의만)
    broker: str = "none"  # none / kis
    # 주가 데이터가 이 영업일 수보다 오래되면 자동매매를 멈춘다 (데이터 갱신 장애 시 낡은 순위로 매매 방지). 0 = 끔
    max_data_age_days: int = 5
    core_only: bool = False  # QUANT_CORE_ONLY=true → 코어-위성 전략에서 AI 오버레이 끔 (코어 100%)
    # 코어 전용이어도 AI 는 하루 한 번 판단·채점만 한다 (주문에는 영향 없음) → AI 를 켜도 될지 증거를 쌓는다.
    # QUANT_AI_SHADOW=false 로 끔. LLM 키가 하나도 없으면 자동으로 꺼진다.
    ai_shadow: bool = True
    ai_overlay: str = "full"  # AI 를 켰을 때: full(거부권+긴급청산+위성) / veto(거부권·긴급청산만, 위성 없음)
    kis_env: str = "demo"  # demo(모의투자) / real

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ if env is None else env)

        def f(name: str, default: float) -> float:
            return float(e[name]) if e.get(name) else default

        risk = RiskLimits(
            max_position_weight=f("QUANT_MAX_POSITION_WEIGHT", RiskLimits.max_position_weight),
            max_gross_exposure=f("QUANT_MAX_GROSS_EXPOSURE", RiskLimits.max_gross_exposure),
            max_daily_loss_pct=f("QUANT_MAX_DAILY_LOSS_PCT", RiskLimits.max_daily_loss_pct),
            max_order_value=f("QUANT_MAX_ORDER_VALUE", RiskLimits.max_order_value),
            max_orders_per_day=int(f("QUANT_MAX_ORDERS_PER_DAY", RiskLimits.max_orders_per_day)),
            min_confidence=f("QUANT_MIN_CONFIDENCE", RiskLimits.min_confidence),
            max_adv_participation=f("QUANT_MAX_ADV_PARTICIPATION", RiskLimits.max_adv_participation),
            max_var95=f("QUANT_MAX_VAR95", RiskLimits.max_var95),
        )
        costs = CostModelConfig(
            commission_bps=f("QUANT_COMMISSION_BPS", CostModelConfig.commission_bps),
            slippage_bps=f("QUANT_SLIPPAGE_BPS", CostModelConfig.slippage_bps),
            sell_tax_bps=f("QUANT_SELL_TAX_BPS", CostModelConfig.sell_tax_bps),
        )
        raw_feeds = e.get("QUANT_NEWS_FEEDS", "").strip()
        # 비우면 기본 국내 경제·증권 RSS (키 필요 없음). 뉴스를 끄려면 QUANT_NEWS_FEEDS=none
        feeds = (() if raw_feeds.lower() == "none" else
                 tuple(u.strip() for u in raw_feeds.split(",") if u.strip()) if raw_feeds else
                 (DEFAULT_NEWS_FEEDS if env is None or e.get("QUANT_DEFAULT_FEEDS") == "1" else ()))
        return cls(
            database_url=e.get("DATABASE_URL", cls.database_url),
            mode=Mode(e.get("QUANT_MODE", Mode.RESEARCH.value)),
            initial_cash=f("QUANT_INITIAL_CASH", cls.initial_cash),
            artifacts_dir=Path(e.get("QUANT_ARTIFACTS_DIR", "artifacts")),
            risk=risk,
            costs=costs,
            live_enabled=e.get("QUANT_LIVE_ENABLED", "").lower() == "true",
            live_confirm=e.get("QUANT_LIVE_CONFIRM", ""),
            live_max_capital=f("QUANT_LIVE_MAX_CAPITAL", cls.live_max_capital),
            live_small_capital=f("QUANT_LIVE_SMALL_CAPITAL", cls.live_small_capital),
            auto_promote=e.get("QUANT_AUTO_PROMOTE", "true").lower() != "false",
            dart_api_key=e.get("DART_API_KEY") or None,
            fred_api_key=e.get("FRED_API_KEY") or None,
            news_feeds=feeds,
            anthropic_enabled=bool(e.get("ANTHROPIC_API_KEY")) or e.get("QUANT_PRIMARY_ENABLED", "") == "true",
            primary_model=e.get("QUANT_PRIMARY_MODEL", cls.primary_model),
            primary_effort=e.get("QUANT_PRIMARY_EFFORT", cls.primary_effort),
            nvidia_api_key=e.get("NVIDIA_API_KEY") or None,
            nvidia_model=e.get("QUANT_NVIDIA_MODEL", cls.nvidia_model),
            nvidia_embed_model=e.get("QUANT_NVIDIA_EMBED_MODEL", cls.nvidia_embed_model),
            nvidia_base_url=e.get("QUANT_NVIDIA_BASE_URL", cls.nvidia_base_url),
            watchlist=tuple(x.strip() for x in e.get("QUANT_WATCHLIST", "").split(",") if x.strip()),
            llm_daily_budget_usd=f("QUANT_LLM_DAILY_BUDGET_USD", cls.llm_daily_budget_usd),
            llm_cache_minutes=f("QUANT_LLM_CACHE_MINUTES", cls.llm_cache_minutes),
            llm_providers=_llm_providers(e),
            ai_roles=e.get("QUANT_AI_ROLES", ""),
            chat_provider=e.get("QUANT_CHAT_PROVIDER", "").strip().lower(),
            chat_models=tuple(m.strip() for m in e.get("QUANT_CHAT_MODELS", "").split(",") if m.strip()),
            broker=e.get("QUANT_BROKER", cls.broker),
            strategy=e.get("QUANT_STRATEGY", cls.strategy),
            kis_env=e.get("KIS_ENV", cls.kis_env),
            core_only=e.get("QUANT_CORE_ONLY", "").lower() == "true",
            ai_shadow=e.get("QUANT_AI_SHADOW", "true").lower() != "false",
            ai_overlay="veto" if e.get("QUANT_AI_OVERLAY", "").lower() == "veto" else "full",
            max_data_age_days=int(f("QUANT_MAX_DATA_AGE_DAYS", cls.max_data_age_days)),
        )

    @property
    def has_llm(self) -> bool:
        """LLM 공급자(무료 포함)가 하나라도 설정됐나 — 없으면 AI 는 휴리스틱만 쓴다."""
        return bool(self.llm_providers or self.anthropic_enabled or self.nvidia_api_key)

    @property
    def live_consent(self) -> bool:
        """실제 돈 사용에 대한 사람의 사전 동의 (.env 두 줄)."""
        return self.live_enabled and self.live_confirm == LIVE_CONFIRM_PHRASE

    def assert_live_allowed(self, champion_ready: bool) -> None:
        """실매매 진입 전 반드시 호출. 하나라도 실패하면 예외."""
        problems = []
        if not self.live_enabled:
            problems.append("QUANT_LIVE_ENABLED=true 가 아님")
        if self.live_confirm != LIVE_CONFIRM_PHRASE:
            problems.append(f"QUANT_LIVE_CONFIRM={LIVE_CONFIRM_PHRASE} 가 아님")
        if not champion_ready:
            problems.append("Shadow 검증을 통과한 champion 모델이 없음")
        if self.live_max_capital <= 0:
            problems.append("QUANT_LIVE_MAX_CAPITAL 이 0 이하")
        if problems:
            raise PermissionError("Live 모드 차단: " + "; ".join(problems))

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


@dataclass(frozen=True)
class RiskLimits:
    max_position_weight: float = 0.10  # 종목당 최대 비중
    max_gross_exposure: float = 1.0  # 총 노출 (1.0 = 레버리지 없음)
    max_daily_loss_pct: float = 0.02  # 일 손실 한도, 넘으면 당일 신규 매수 중단
    max_order_value: float = 5_000_000  # 1회 주문 최대 금액
    max_orders_per_day: int = 100
    min_confidence: float = 0.55  # 이 확률 미만의 매수 신호는 무시


@dataclass(frozen=True)
class CostModelConfig:
    commission_bps: float = 1.5  # 증권사 수수료 (편도)
    slippage_bps: float = 5.0  # 체결 미끄러짐 가정
    sell_tax_bps: float = 18.0  # 국내 매도 거래세 (연도별 변경되므로 확인 필요)


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
    # 외부 API 키 (없으면 해당 수집기는 건너뜀)
    dart_api_key: str | None = None
    fred_api_key: str | None = None
    news_feeds: tuple[str, ...] = ()
    # 멀티 AI
    anthropic_enabled: bool = False  # ANTHROPIC_API_KEY 가 있거나 QUANT_PRIMARY_ENABLED=true
    primary_model: str = "claude-opus-5"
    primary_effort: str = "high"
    nvidia_api_key: str | None = None
    nvidia_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nvidia_embed_model: str = "nvidia/nemotron-3-embed-1b"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    watchlist: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
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
        )
        feeds = tuple(u.strip() for u in e.get("QUANT_NEWS_FEEDS", "").split(",") if u.strip())
        return cls(
            database_url=e.get("DATABASE_URL", cls.database_url),
            mode=Mode(e.get("QUANT_MODE", Mode.RESEARCH.value)),
            initial_cash=f("QUANT_INITIAL_CASH", cls.initial_cash),
            artifacts_dir=Path(e.get("QUANT_ARTIFACTS_DIR", "artifacts")),
            risk=risk,
            live_enabled=e.get("QUANT_LIVE_ENABLED", "").lower() == "true",
            live_confirm=e.get("QUANT_LIVE_CONFIRM", ""),
            live_max_capital=f("QUANT_LIVE_MAX_CAPITAL", cls.live_max_capital),
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
        )

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

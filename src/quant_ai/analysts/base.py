"""AI 애널리스트 공통 타입.

원칙
- 모든 애널리스트는 **같은 입력(MarketContext)** 을 받지만 **서로의 의견은 보지 않는다** (독립성).
- 역할이 다르다: 종합 분석(primary) / 독립 검증(nvidia) / 숫자 분석(quant) / 반대 논거(risk).
- 애널리스트는 의견(Opinion)만 낸다. 주문 권한은 없다. 주문은 Ensemble → Risk Gate → Execution 만 가능.
- 실패(API 오류, JSON 파싱 실패)는 예외 대신 '기권'으로 처리한다. 한 AI 가 죽어도 시스템은 돈다.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime

# 채점 카테고리 (AI 성적표의 열)
DIRECTION = "direction"  # 단기 방향
NEWS = "news"  # 뉴스 해석
MACRO = "macro"  # 거시경제 해석
TREND = "trend"  # 추세 판단
RISK = "risk"  # 위험 경고 적중


@dataclass
class MarketContext:
    """모든 애널리스트에게 동일하게 주어지는 분석 재료 묶음."""

    symbol: str
    name: str | None
    market: str
    as_of: datetime
    horizon_days: int
    price: dict  # last, ret_1/5/20, vol_20, rsi, ma 거리 등
    regime: dict  # regime, trend, vol_pct, drawdown
    news: list[dict] = field(default_factory=list)  # 뉴스 '이벤트' [{ts, title, n_articles, sentiment, events}]
    disclosures: list[dict] = field(default_factory=list)
    macro: dict = field(default_factory=dict)  # {series: {last, chg_5d}}
    upcoming_events: list[dict] = field(default_factory=list)  # [{ts, name, importance}]
    similar_past: list[dict] = field(default_factory=list)  # RAG: 과거 유사 사례 + 당시 결과
    features: dict = field(default_factory=dict)  # 퀀트 피처 원본 (quant 애널리스트용)
    data_quality: dict = field(default_factory=dict)  # stale, gap, jump 등
    market_state: dict = field(default_factory=dict)  # 시장 상태 {label: RISK ON/OFF, score, type}
    cross_asset: list[dict] = field(default_factory=list)  # [{name, corr, beta, chg_5d}] NASDAQ·VIX·달러·금리·유가
    community: dict = field(default_factory=dict)  # 커뮤니티·SNS 분위기 요약 (참고용, 실시간 판단에만)
    sector: dict = field(default_factory=dict)  # 업종 · 업종 순위 · 상대강도 · 폭 · 같은 업종 상위 종목 (섹터 엔진)
    related: list[dict] = field(default_factory=list)  # 연관 종목 움직임 + 관계 근거 (지식 그래프)
    market_agents: dict = field(default_factory=dict)  # 뉴스·매크로·섹터 에이전트의 시장 요약 (이 종목 관련 사건 포함)
    flow: dict = field(default_factory=dict)  # 외국인·기관 수급 요약 (국내)
    signal: dict = field(default_factory=dict)  # v36 신호 엔진 2.0: 검증된 가격 신호 점수 + 보지 않은 기간 점수대별 결과

    def to_prompt(self) -> str:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        d.pop("features", None)  # LLM 에는 가공된 요약만
        return json.dumps(d, ensure_ascii=False, default=str, indent=1)


@dataclass
class Opinion:
    analyst: str
    symbol: str
    prob_up: float | None  # None = 방향 의견 없음(기권)
    confidence: float  # 0~1, 애널리스트 스스로의 확신
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    sub_scores: dict[str, float] = field(default_factory=dict)  # {news: -1~1, macro: -1~1, trend: ...}
    veto: bool = False
    veto_reason: str | None = None
    summary: str = ""
    backend: str = ""  # claude-opus-5 / nvidia/nemotron... / heuristic / sklearn
    error: str | None = None
    meta: dict = field(default_factory=dict)  # 채점용 부가정보 (예: model_version)

    @property
    def abstained(self) -> bool:
        return self.prob_up is None

    @property
    def stance(self) -> float:
        """-1(강한 하락) ~ +1(강한 상승)."""
        return 0.0 if self.prob_up is None else 2 * self.prob_up - 1

    @classmethod
    def abstain(cls, analyst: str, symbol: str, error: str, backend: str = "") -> Opinion:
        return cls(analyst, symbol, None, 0.0, error=error, backend=backend, summary=f"기권: {error}")


class Analyst(ABC):
    name: str = "base"
    role: str = ""
    categories: tuple[str, ...] = (DIRECTION,)

    @abstractmethod
    def analyze(self, ctx: MarketContext) -> Opinion: ...


def clip01(x, default: float = 0.5) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return min(max(v, 0.0), 1.0) if v == v else default


def clip11(x, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return min(max(v, -1.0), 1.0) if v == v else default

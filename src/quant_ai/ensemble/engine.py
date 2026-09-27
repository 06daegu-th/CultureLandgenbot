"""Ensemble Engine: 여러 AI 의견 → 하나의 CONSENSUS SIGNAL.

1. 가중치 = 사전 가중치 × 과거 성적(수축 추정 정확도) × 애널리스트 자기 확신
   - 성적이 쌓이기 전(n 작음)에는 모두 비슷한 가중치 → 성적이 쌓일수록 잘 맞히는 AI 비중 증가
   - 동전 던지기(50%) 이하 성적의 AI 는 가중치가 바닥까지 줄어든다
2. 확률 결합은 로그오즈 가중 평균 (단순 평균보다 확신 있는 의견을 잘 반영)
3. 충돌 탐지: 의견 분산 + 반대 방향의 강한 의견 존재 여부
4. Risk AI veto → NO_TRADE (신규 진입 금지). veto 는 가중치로 희석되지 않는다.
5. 최종 행동은 신호일 뿐이다. 주문은 Risk Gate → Execution Engine 만 낼 수 있다.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from ..analysts.base import DIRECTION, Opinion


@dataclass(frozen=True)
class TrackRecord:
    n: int = 0
    hits: int = 0
    brier: float | None = None

    def shrunk_accuracy(self, prior_n: float = 30.0) -> float:
        return (self.hits + 0.5 * prior_n) / (self.n + prior_n)


@dataclass(frozen=True)
class EnsembleConfig:
    prior_weights: dict = field(default_factory=lambda: {
        "primary": 1.0, "nvidia": 1.0, "panel": 0.7, "quant": 1.0, "regime": 0.4, "risk": 0.5,
    })
    skill_floor: float = 0.02  # 성적 0 이어도 남는 최소 가중치
    prior_n: float = 30.0  # 성적 수축 강도 (표본 30개 전까지는 50% 쪽으로 당김)
    buy_prob: float = 0.58
    sell_prob: float = 0.42
    min_confidence: float = 55.0
    min_responders: int = 2  # 방향 의견을 낸 AI 가 이보다 적으면 NO_TRADE
    high_conflict_std: float = 0.30
    medium_conflict_std: float = 0.15


@dataclass
class Contribution:
    analyst: str
    prob_up: float | None
    stance: float
    weight: float
    confidence: float
    accuracy: float | None
    n_scored: int
    backend: str
    summary: str
    veto: bool


@dataclass
class ConsensusSignal:
    symbol: str
    action: str  # BUY / SELL / HOLD / NO_TRADE
    prob_up: float
    confidence: float  # 0~100
    conflict: str  # low / medium / high
    conflict_score: float
    contributions: list[Contribution]
    vetoes: list[str]
    reasons: list[str]
    risks: list[str]
    explanation: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _logit(p: float) -> float:
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


class EnsembleEngine:
    def __init__(self, config: EnsembleConfig | None = None):
        self.cfg = config or EnsembleConfig()

    def weight(self, op: Opinion, rec: TrackRecord | None) -> float:
        prior = self.cfg.prior_weights.get(op.analyst, 0.5)
        rec = rec or TrackRecord()
        acc = rec.shrunk_accuracy(self.cfg.prior_n)
        # 검증된 실력 + '아직 모름' 보너스(표본이 쌓일수록 소멸) → 신규 AI 도 초기엔 발언권이 있다
        unknown_bonus = 0.08 * self.cfg.prior_n / (rec.n + self.cfg.prior_n)
        skill = self.cfg.skill_floor + max(acc - 0.5, 0.0) * 4 + unknown_bonus
        return prior * skill * (0.5 + 0.5 * op.confidence)

    def combine(self, symbol: str, opinions: list[Opinion],
                records: dict[str, TrackRecord] | None = None) -> ConsensusSignal:
        records = records or {}
        cfg = self.cfg
        contribs: list[Contribution] = []
        num = den = 0.0
        for op in opinions:
            rec = records.get(op.analyst)
            w = 0.0 if op.abstained else self.weight(op, rec)
            if not op.abstained:
                num += w * _logit(op.prob_up)
                den += w
            contribs.append(Contribution(
                analyst=op.analyst, prob_up=op.prob_up, stance=op.stance, weight=w, confidence=op.confidence,
                accuracy=rec.hits / rec.n if rec and rec.n else None, n_scored=rec.n if rec else 0,
                backend=op.backend, summary=op.summary or (op.error or ""), veto=op.veto,
            ))
        total_w = sum(c.weight for c in contribs) or 1.0
        for c in contribs:
            c.weight = c.weight / total_w  # 화면 표시용 정규화

        voters = [c for c in contribs if c.prob_up is not None]
        prob = 1 / (1 + math.exp(-num / den)) if den > 0 else 0.5
        cons_stance = 2 * prob - 1

        # ---- 충돌 탐지
        if voters:
            mean = sum(c.weight * c.stance for c in voters) / max(sum(c.weight for c in voters), 1e-9)
            std = math.sqrt(sum(c.weight * (c.stance - mean) ** 2 for c in voters)
                            / max(sum(c.weight for c in voters), 1e-9))
        else:
            std = 0.0
        strong_opposite = [c.analyst for c in voters
                           if abs(c.stance) >= 0.2 and abs(cons_stance) >= 0.05 and c.stance * cons_stance < 0]
        conflict_score = min(1.0, std / 0.5 + 0.25 * len(strong_opposite))
        if std >= cfg.high_conflict_std or len(strong_opposite) >= 2:
            conflict = "high"
        elif std >= cfg.medium_conflict_std or strong_opposite:
            conflict = "medium"
        else:
            conflict = "low"

        coverage = len(voters) / max(len(opinions), 1)
        confidence = 100 * max(prob, 1 - prob) * (1 - 0.5 * conflict_score) * (0.8 + 0.2 * coverage)

        vetoes = [f"{op.analyst}: {op.veto_reason or 'veto'}" for op in opinions if op.veto]
        explanation: list[str] = []
        if vetoes:
            action = "NO_TRADE"
            explanation.append("리스크 veto → 신규 진입 금지")
        elif len(voters) < cfg.min_responders:
            action = "NO_TRADE"
            explanation.append(f"방향 의견을 낸 AI {len(voters)}개 < {cfg.min_responders}")
        elif conflict == "high":
            action = "NO_TRADE"
            explanation.append(f"AI 간 의견 충돌 높음 (반대: {', '.join(strong_opposite) or '분산 큼'})")
        elif prob >= cfg.buy_prob and confidence >= cfg.min_confidence:
            action = "BUY"
        elif prob <= cfg.sell_prob and confidence >= cfg.min_confidence:
            action = "SELL"
        else:
            action = "HOLD"
            explanation.append("확률·신뢰도가 매매 기준 미달")

        reasons = [f"[{op.analyst}] {r}" for op in opinions for r in op.reasons[:3] if op.analyst != "risk"]
        risks = [f"[{op.analyst}] {r}" for op in opinions for r in op.risks[:3]]
        return ConsensusSignal(symbol, action, prob, round(confidence, 1), conflict, round(conflict_score, 3),
                               contribs, vetoes, reasons[:10], risks[:10], explanation)


def records_for(scoreboard: dict[tuple[str, str], TrackRecord], category: str = DIRECTION) -> dict[str, TrackRecord]:
    return {a: r for (a, c), r in scoreboard.items() if c == category}

"""예측 모드: 다음 거래일(또는 다음 시간대) 시나리오 생성.

모델 확률과 최근 변동성을 결합해 강세/기준/약세 3개 시나리오와 가격 범위를 만든다.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .prediction import Prediction
from .regime import RegimeState


@dataclass
class ScenarioCase:
    name: str
    probability: float
    expected_return: float
    price_low: float
    price_high: float
    narrative: str


def _norm_ppf(p: float) -> float:
    # Acklam 근사 없이 이분법으로 충분 (호출 빈도 낮음)
    lo, hi = -8.0, 8.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def build_scenarios(pred: Prediction, last_close: float, daily_vol: float, regime: RegimeState | None,
                    horizon_days: int = 1, news_note: str | None = None) -> dict:
    sigma = max(daily_vol, 1e-4) * math.sqrt(horizon_days)
    # 확률이 P(r>0) 이 되도록 정규분포 평균을 역산: P(r>0) = Phi(mu/sigma)
    p = min(max(pred.prob_up, 0.02), 0.98)
    mu = sigma * _norm_ppf(p)
    tail = 0.25  # 강세/약세 시나리오 각각 상하위 25% 구간
    cases = [
        ScenarioCase("bull", tail, mu + 1.15 * sigma,
                     last_close * math.exp(mu + 0.67 * sigma), last_close * math.exp(mu + 2.0 * sigma),
                     "모멘텀 지속 또는 긍정적 뉴스 반영 시"),
        ScenarioCase("base", 1 - 2 * tail, mu,
                     last_close * math.exp(mu - 0.67 * sigma), last_close * math.exp(mu + 0.67 * sigma),
                     "현재 추세·변동성 유지 시"),
        ScenarioCase("bear", tail, mu - 1.15 * sigma,
                     last_close * math.exp(mu - 2.0 * sigma), last_close * math.exp(mu - 0.67 * sigma),
                     "시장 위험회피 또는 부정적 이벤트 발생 시"),
    ]
    return {
        "symbol": pred.symbol,
        "as_of": str(pred.as_of),
        "prob_up": round(pred.prob_up, 4),
        "direction": pred.direction,
        "last_close": last_close,
        "sigma": sigma,
        "regime": regime.regime.value if regime else None,
        "news": news_note,
        "cases": [asdict(c) for c in cases],
        "rationale": pred.rationale,
    }

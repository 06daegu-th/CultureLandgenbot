"""코어-위성 포트폴리오.

    코어 (기본 80%): 실제 KRX 데이터로 검증된 팩터 전략 — 모멘텀 + 저변동성 + 52주 고점
        · 20거래일마다 리밸런싱, 상위 20 동일가중, 보유 종목은 40위 안이면 유지
        · 멀티 AI 역할 ① 거부권: 위험 종목은 신규 편입하지 않고 다음 순위로 대체
        · 멀티 AI 역할 ② 긴급 청산: 치명적 위험(상장폐지·위기 국면)은 보유 중이어도 즉시 제외
    위성 (기본 20%): 멀티 AI 합의 BUY 상위 종목 — AI 종목선택 능력을 작은 비중으로 실전 검증

AI 는 여기서도 '의견'만 낸다. 목표 비중 → 리스크 엔진 → 실행 엔진 순서는 그대로다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from ..engines.factors import CORE_FACTOR_WEIGHTS, factor_score_cross_section, price_factors


@dataclass(frozen=True)
class CoreSatelliteConfig:
    core_weight: float = 0.8
    core_top_k: int = 20
    core_buffer_k: int = 40
    core_rebalance_days: int = 20
    satellite_k: int = 5
    satellite_min_confidence: float = 60.0
    shortlist_k: int = 40  # AI 가 분석할 후보 수 (LLM 비용 통제)
    # 추세 필터: 코어 리밸런싱 시점에 지수가 N일 이동평균 아래면 코어 비중을 trend_off_scale 배로 축소.
    # 실데이터 사전등록 시험 통과 (dev MDD -55% → -43%, Sharpe 0.20 → 0.25; docs/RESEARCH_KRX.md 8장)
    trend_ma: int | None = 200
    trend_off_scale: float = 0.5
    affordability_slack: float = 1.5  # 1주 가격이 목표 금액의 이 배수를 넘으면 매수 불가로 보고 다음 순위로
    factor_weights: dict = field(default_factory=lambda: dict(CORE_FACTOR_WEIGHTS))


@dataclass
class Plan:
    core: list[str]
    core_rebalanced: bool
    weights: dict[str, float]
    ranks: dict[str, int]
    scores: dict[str, float]
    vetoed: dict[str, str] = field(default_factory=dict)  # 신규 편입 거부
    exits: dict[str, str] = field(default_factory=dict)  # 보유 중 긴급 제외
    satellite: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def core_scores(bars: dict[str, pd.DataFrame], universe: list[str] | None,
                weights: dict[str, float] | None = None) -> pd.Series:
    """마지막 봉 기준 횡단면 점수 (높을수록 좋음)."""
    rows = {}
    for sym, b in bars.items():
        if universe is not None and sym not in universe:
            continue
        if len(b) < 130:
            continue
        rows[sym] = price_factors(b["close"]).iloc[-1]
    if not rows:
        return pd.Series(dtype=float)
    return factor_score_cross_section(pd.DataFrame(rows).T, weights)


def build_plan(scores: pd.Series, prev_core: list[str], rebalance_due: bool, cfg: CoreSatelliteConfig,
               vetoes: dict[str, str] | None = None, exits: dict[str, str] | None = None,
               ai_buys: list[dict] | None = None, use_veto: bool = True, use_satellite: bool = True,
               core_weight: float | None = None, core_scale: float = 1.0,
               unaffordable: set[str] | None = None) -> Plan:
    """vetoes: {종목: 사유} 신규 편입 거부 / exits: {종목: 사유} 보유 중 즉시 제외 /
    ai_buys: [{symbol, confidence, prob_up, consensus_id}] 합의 BUY (신뢰도 내림차순 정렬 불필요)."""
    vetoes = vetoes if use_veto else {}
    exits = exits if use_veto else {}
    vetoes, exits = vetoes or {}, exits or {}
    unaffordable = unaffordable or set()
    ranks = {s: i for i, s in enumerate(scores.index)}
    plan = Plan(core=[], core_rebalanced=False, weights={}, ranks=ranks,
                scores={k: round(float(v), 4) for k, v in scores.items()})

    def fill(core: list[str]) -> list[str]:
        for s in scores.index:
            if len(core) >= cfg.core_top_k:
                break
            if s in core or s in exits:
                continue
            if s in unaffordable:  # 소액 계좌: 1주도 살 수 없는 종목은 다음 순위로
                plan.notes.append(f"{s} 1주 가격이 목표 금액 초과 → 다음 순위로 대체")
                continue
            if s in vetoes:
                plan.vetoed[s] = vetoes[s]
                continue
            core.append(s)
        return core

    if rebalance_due or not prev_core:
        # 보유 종목은 순위가 buffer 안이면 유지 (회전율 억제) — 이미 보유 중이므로 거부권 대상 아님
        keep = [s for s in prev_core if ranks.get(s, 10**9) < cfg.core_buffer_k and s not in exits]
        plan.core = fill(list(keep))  # 복사본 (keep 을 직접 바꾸면 유지/신규 집계가 틀어짐)
        plan.core_rebalanced = True
        plan.notes.append(f"코어 리밸런싱: 유지 {len(keep)} · 신규 {len(plan.core) - len(keep)}")
    else:
        plan.core = [s for s in prev_core if s not in exits]
        if len(plan.core) < len(prev_core):
            plan.core = fill(plan.core)  # 긴급 제외된 자리만 다음 순위로 채움
            plan.notes.append("긴급 제외로 코어 일부 교체")
    for s in prev_core:
        if s in exits:
            plan.exits[s] = exits[s]

    cw = cfg.core_weight if core_weight is None else core_weight
    if not use_satellite:
        cw = 1.0 if core_weight is None else core_weight
    if core_scale < 1.0:
        plan.notes.append(f"추세 필터: 지수가 이동평균 아래 → 코어 비중 {core_scale:.0%} 로 축소 (나머지 현금)")
    for s in plan.core:
        plan.weights[s] = cw * core_scale / max(len(plan.core), 1)

    if use_satellite and ai_buys:
        sat_w = (1 - cw) / cfg.satellite_k
        cands = sorted((b for b in ai_buys if b["confidence"] >= cfg.satellite_min_confidence
                        and b["symbol"] not in plan.weights and b["symbol"] not in vetoes
                        and b["symbol"] not in exits), key=lambda b: -b["confidence"])
        for b in cands[:cfg.satellite_k]:
            plan.weights[b["symbol"]] = sat_w
            plan.satellite.append({**b, "weight": sat_w})
        if len(plan.satellite) < cfg.satellite_k:
            plan.notes.append(f"위성 {cfg.satellite_k - len(plan.satellite)}자리 현금 (확신 있는 AI 합의 BUY 부족)")
    return plan

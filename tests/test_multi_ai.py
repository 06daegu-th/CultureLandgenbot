from datetime import UTC, datetime, timedelta

import pytest

from quant_ai.analysts.analysts import LLMAnalyst, RiskAnalyst
from quant_ai.analysts.base import MarketContext, Opinion
from quant_ai.analysts.llm_clients import HashingEmbeddings, LLMClient, LLMError, extract_json
from quant_ai.ensemble.engine import EnsembleEngine, TrackRecord

NOW = datetime(2024, 3, 4, 6, 0, tzinfo=UTC)


def ctx(**kw):
    base = dict(symbol="005930", name="삼성전자", market="KRX", as_of=NOW, horizon_days=5,
                price={"ret_20": 0.05, "vol_ratio": 1.0, "rsi_14": 0.6}, regime={"regime": "bull_quiet"})
    base.update(kw)
    return MarketContext(**base)


class FakeLLM(LLMClient):
    model = "fake-model"

    def __init__(self, out=None, exc=None):
        self.out, self.exc, self.calls = out, exc, []

    def complete_json(self, system, user, schema):
        self.calls.append((system, user))
        if self.exc:
            raise self.exc
        return self.out


# ------------------------------------------------------------------ LLM 애널리스트
def test_llm_analyst_parses_and_clips():
    out = {"prob_up": 1.7, "confidence": 0.8, "news_impact": -3, "macro_impact": 0.2, "key_drivers": ["반도체 수요"],
           "risks": ["환율"], "surprises": [], "veto": False, "veto_reason": "", "summary": "요약"}
    llm = FakeLLM(out)
    op = LLMAnalyst("nvidia", llm).analyze(ctx())
    assert op.prob_up == 1.0 and op.sub_scores["news"] == -1.0 and op.backend == "fake-model"
    system, user = llm.calls[0]
    assert "경제·시장 AI" in system and "독립적으로" in system and "005930" in user


def test_llm_failure_becomes_abstain():
    op = LLMAnalyst("primary", FakeLLM(exc=LLMError("rate limit"))).analyze(ctx())
    assert op.abstained and "rate limit" in op.error


def test_extract_json_strips_reasoning():
    text = '<think>생각 {"x": 0}</think>\n```json\n{"prob_up": 0.6, "veto": false}\n```'
    assert extract_json(text) == {"prob_up": 0.6, "veto": False}
    with pytest.raises(LLMError):
        extract_json("no json here")


# ------------------------------------------------------------------ Risk AI
def test_risk_ai_hard_vetoes():
    r = RiskAnalyst()
    event = {"ts": (NOW + timedelta(hours=10)).isoformat(), "name": "FOMC", "importance": 0.9}
    assert r.analyze(ctx(upcoming_events=[event])).veto
    assert r.analyze(ctx(regime={"regime": "crisis"})).veto
    assert r.analyze(ctx(price={"vol_ratio": 3.5})).veto
    assert r.analyze(ctx(data_quality={"stale": "3일 전"})).veto
    clean = r.analyze(ctx())
    assert not clean.veto and clean.prob_up is None


def test_risk_llm_cannot_override_rule_veto():
    llm_out = {"prob_up": 0.7, "confidence": 0.9, "news_impact": 0, "macro_impact": 0, "key_drivers": [],
               "risks": [], "surprises": [], "veto": False, "veto_reason": "", "summary": "괜찮음"}
    r = RiskAnalyst(LLMAnalyst("risk", FakeLLM(llm_out)))
    assert r.analyze(ctx(regime={"regime": "crisis"})).veto


# ------------------------------------------------------------------ 앙상블
def op(name, p, conf=0.7, veto=False):
    return Opinion(name, "X", p, conf, veto=veto, veto_reason="위험" if veto else None)


def test_consensus_buy_when_agreeing():
    sig = EnsembleEngine().combine("X", [op("primary", 0.68), op("nvidia", 0.61), op("quant", 0.73), op("regime", 0.66)])
    assert sig.action == "BUY" and sig.conflict == "low" and sig.confidence >= 55


def test_risk_veto_forces_no_trade():
    sig = EnsembleEngine().combine("X", [op("primary", 0.75), op("nvidia", 0.69), op("quant", 0.72),
                                         Opinion("risk", "X", None, 0.5, veto=True, veto_reason="이벤트 임박")])
    assert sig.action == "NO_TRADE" and sig.vetoes


def test_high_conflict_forces_no_trade():
    sig = EnsembleEngine().combine("X", [op("primary", 0.85), op("nvidia", 0.15), op("quant", 0.80), op("regime", 0.2)])
    assert sig.conflict == "high" and sig.action == "NO_TRADE"


def test_track_record_shifts_weight():
    e = EnsembleEngine()
    ops = [op("primary", 0.7), op("nvidia", 0.3)]
    good, bad = TrackRecord(n=300, hits=195), TrackRecord(n=300, hits=135)
    sig = e.combine("X", ops, {"primary": good, "nvidia": bad})
    assert sig.prob_up > 0.6  # 잘 맞히는 primary 쪽으로
    sig2 = e.combine("X", ops, {"primary": bad, "nvidia": good})
    assert sig2.prob_up < 0.4


def test_too_few_responders_no_trade():
    sig = EnsembleEngine().combine("X", [op("primary", 0.9), Opinion.abstain("nvidia", "X", "down")])
    assert sig.action == "NO_TRADE"


# ------------------------------------------------------------------ RAG 임베딩
def test_hashing_embeddings_similarity():
    e = HashingEmbeddings()
    v = e.embed(["삼성전자 반도체 실적 호조", "삼성전자 반도체 실적 개선", "테슬라 리콜 발표"])
    assert v[0] @ v[1] > v[0] @ v[2]

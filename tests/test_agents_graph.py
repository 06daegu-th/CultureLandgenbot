"""P1·P2: 드리프트(PSI) · 재학습 후보 자동 생성 · 섹터 엔진 · 지식 그래프 · 뉴스/매크로/섹터 에이전트."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops
from quant_ai.engines import drift as D
from quant_ai.engines import sector as SE


def test_psi_flags_distribution_shift():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1, 5000)
    assert D.psi(a, rng.normal(0, 1, 2000)) < 0.02
    assert D.psi(a, rng.normal(1.0, 1, 2000)) > 0.25
    assert D.psi(a[:10], a[:10]) is None and D.status_of(0.3) == "drift" and D.status_of(0.15) == "warn"


def _bars(n, seed, vol_jump=False, start="2025-01-02"):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0005, 0.01, n)
    if vol_jump:
        r[-20:] = rng.normal(-0.01, 0.05, 20)  # 마지막 20일: 변동성 급등 + 하락
    idx = pd.bdate_range(start, periods=n, tz="UTC")
    c = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6 * (1 + rng.random(n))})


def test_feature_drift_detects_regime_break():
    calm = {f"S{i}": _bars(400, i) for i in range(12)}
    rep = D.report(calm)
    assert rep["status"] in ("stable", "warn")
    shock = {f"S{i}": _bars(400, i, vol_jump=True) for i in range(12)}
    rep = D.report(shock)
    vol = next(f for f in rep["features"] if f["feature"] == "vol_20")
    assert rep["status"] == "drift" and vol["status"] == "drift" and vol["cur_mean"] > vol["ref_mean"]


def test_sector_mapping_stats_and_context(tmp_path):
    from quant_ai.data.db import init_db, make_engine
    assert SE.to_korean("Technology", "Semiconductors") == "반도체"
    assert SE.to_korean("Financial Services", "Banks - Regional") == "은행"
    assert SE.to_korean("Industrials", "Conglomerates") == "산업재" and SE.to_korean(None, None) is None
    e = make_engine(f"sqlite:///{tmp_path}/s.db")
    init_db(e)
    calls = []

    def fetch(sym):
        calls.append(sym)
        if sym == "BAD":
            raise RuntimeError("x")
        return {"sector": "Technology", "industry": "Semiconductors" if sym in ("A", "B") else "Software - Application"}
    r = SE.fill_map(e, ["A", "B", "C", "BAD"], fetch=fetch, pause=0)
    assert r["mapped"] == 3 and set(r["new"]) == {"A", "B", "C"}
    r = SE.fill_map(e, ["A", "B", "C", "BAD"], fetch=fetch, pause=0)
    assert r["tried"] == 0 and calls.count("BAD") == 1  # 채운 것은 30일, 실패한 것은 7일 쉰다
    mp = SE.sector_map(e)
    bars = {"A": _bars(60, 1), "B": _bars(60, 2), "C": _bars(60, 3)}
    stats = SE.sector_stats(bars, mp, _bars(60, 9), {"A": "에이", "B": "비"})
    assert {x["sector"] for x in stats} == {"반도체", "소프트웨어"} and stats[0]["rank"] == 1 and stats[0]["of"] == 2
    ctx = SE.for_context("A", mp, stats)
    assert ctx["sector"] == "반도체" and "/2" in ctx["rank"] and ctx["peers_5d"][0]["name"] == "비"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("ag")
    fake_marcap(d, n_codes=8, days=420)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a",
                        max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=8, end_year=2020)
    return a


def test_knowledge_graph_from_news_and_correlation(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import NewsArticle
    from quant_ai.engines.knowledge import ego, for_context, neighbors
    bars, _, _ = app.market_data()
    syms = list(bars)
    last = max(b.index.max() for b in bars.values()).to_pydatetime()
    with session_scope(app.engine) as s:
        for i in range(3):
            s.add(NewsArticle(source="t", url=f"k{i}", published_at=datetime.now(UTC) - timedelta(days=i), title=f"{i} 공급 계약",
                              symbols=[syms[0], syms[1]]))
    ops.set_state(app.engine, "sector_map", {"map": {syms[0]: "반도체", syms[1]: "반도체"}})
    stats = app.build_graph()
    assert stats["co_mention_pairs"] >= 1 and last
    g = ops.get_state(app.engine, "kgraph")
    nb = neighbors(g, syms[0])
    top = next(n for n in nb if n["symbol"] == syms[1])
    assert top["co_mention"] == 3 and top["same_sector"] == "반도체" and top["weight"] > 0.5
    eg = ego(g, syms[0])
    assert eg["center"] == syms[0] and syms[1] in eg["nodes"]
    ctx = for_context(app.engine, syms[0], bars)
    assert "뉴스 동시언급 3회" in ctx[0]["relation"] and "같은 업종(반도체)" in ctx[0]["relation"]


class FakeLLM:
    model = "fake-agent"
    provider = "fake"

    def __init__(self, out=None, exc=None):
        self.out, self.exc, self.calls = out, exc, []

    def complete_json(self, system, user, schema):
        self.calls.append((system, user))
        if self.exc:
            raise self.exc
        return self.out


def test_news_agent_llm_and_rules_fallback(app):
    from quant_ai.agents import news_agent, news_for_symbol
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import NewsArticle
    sym = list(app.market_data()[0])[2]
    with session_scope(app.engine) as s:
        s.add(NewsArticle(source="hk", url="na1", published_at=datetime.now(UTC) - timedelta(hours=2),
                          title="종목2 대규모 수주 공시", symbols=[sym], sentiment=0.6, importance=0.9, events=["contract"]))
    llm = FakeLLM({"events": [{"idx": 0, "impact": 0.7, "horizon": "단기", "priced_in": False, "affected": ["종목2"],
                               "why": "수주 규모가 매출 대비 큼"}], "summary": "수주 뉴스가 주도"})
    d = news_agent(app, client=llm)
    assert d["source"] == "llm" and d["summary"] == "수주 뉴스가 주도" and "신뢰할 수 없는" in llm.calls[0][0]
    ev = next(e for e in d["events"] if "수주" in e["title"])
    assert ev["impact"] == 0.7 and ev["priced_in"] is False
    assert news_for_symbol(app.engine, "종목2", sym)[0]["why"].startswith("수주")
    d2 = news_agent(app, client=FakeLLM(exc=RuntimeError("quota")))
    assert d2["source"] == "rules" and d2["events"] and d2["summary"]


def test_macro_and_sector_agents(app):
    from quant_ai.agents import macro_agent, sector_agent
    m = macro_agent(app, client=FakeLLM(exc=RuntimeError("no")))
    assert m["source"] == "rules" and m["risk_level"] in ("낮음", "보통", "높음") and -1 <= m["stance"] <= 1
    m2 = macro_agent(app, client=FakeLLM({"view": "금리 안정", "risk_level": "보통", "stance": 3, "drivers": ["VIX 하락"], "watch": ["CPI"]}))
    assert m2["source"] == "llm" and m2["stance"] == 1.0  # 범위 밖 값은 자른다
    syms = list(app.market_data()[0])
    ops.set_state(app.engine, "sector_map", {"map": {s: ("반도체" if i % 3 == 0 else "은행" if i % 3 == 1 else "화학")
                                                      for i, s in enumerate(syms)}})
    sv = sector_agent(app, client=FakeLLM({"view": "반도체 주도", "leaders": ["반도체"], "laggards": ["은행"], "rotation": "방어 → 성장"}))
    assert sv["source"] == "llm" and len(sv["table"]) == 3 and sv["rotation"] == "방어 → 성장"


def test_decide_gives_ais_sector_related_and_agent_context(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    sym = list(app.market_data()[0])[0]
    ops.set_state(app.engine, "macro_brief", {"view": "안정", "risk_level": "보통", "stance": 0.1})
    ds = app.decide(symbols=[sym], scenarios=False)
    ctx = ds[0].context
    assert ctx.sector.get("sector") and ctx.market_agents.get("macro", {}).get("view") == "안정"
    with session_scope(app.engine) as s:
        rec = s.get(ConsensusRecord, ds[0].consensus_id)
        ev = rec.payload["evidence"]
        assert ev.get("sector", {}).get("sector") and rec.row_hash and rec.payload["versions"]["roles"]


def test_drift_and_auto_retrain_with_lineage(app):
    rep = app.drift()
    assert rep["status"] in ("stable", "warn", "drift", "unknown") and ops.get_state(app.engine, "drift")["at"]
    r = app.auto_retrain(max_variants=1)
    assert "정기 (7일)" in r["reasons"] and r["trained"] and "id" in r["trained"][0], r
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ModelRecord
    with session_scope(app.engine) as s:
        m = s.get(ModelRecord, r["trained"][0]["id"])
        assert "정기" in m.params["reason"] and m.params["embargo"] >= 1 and m.params["n_train"] > 0
    ops.set_state(app.engine, "drift", {"status": "stable"})
    assert app.auto_retrain()["skipped"] == "트리거 없음"  # 방금 만들었고 드리프트 없음 → 건너뜀
    events = ops.get_state(app.engine, "model_events")["events"]
    assert events[-1]["kind"] == "retrain"

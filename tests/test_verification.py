"""P0 검증 핵심: 예측 장부(해시 봉인) · 결과 매칭 · 독립 평가기 · 누수 감사 · 시장 충격 · 반켈리 위성 · 업종 한도."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_ai.data.models import ConsensusRecord
from quant_ai.review import evaluator as E
from quant_ai.review import ledger as LG
from quant_ai.review.outcomes import forward, match


@pytest.fixture()
def engine(tmp_path):
    from quant_ai.data.db import init_db, make_engine
    e = make_engine(f"sqlite:///{tmp_path}/v.db")
    init_db(e)
    return e


def _bars(n=80, seed=1, start="2026-01-02"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n, tz="UTC")
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, n))), index=idx)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]) * 1.001, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": 1e6})


def _rec(sym, as_of, p=0.62, action="BUY", real=None, payload=None):
    from quant_ai.data.models import ConsensusRecord
    r = ConsensusRecord(symbol=sym, as_of=as_of, action=action, prob_up=p, confidence=62.0, conflict="low",
                        payload={"horizon": 5, "regime": "bull_quiet", "expected_return": 0.012, **(payload or {})},
                        realized_return=real, correct=None if real is None else (p >= 0.5) == (real > 0),
                        created_at=pd.Timestamp(as_of).to_pydatetime() + timedelta(hours=1))  # 봉 마감 1시간 뒤 저장
    LG.seal(r)
    return r


# ------------------------------------------------------------------ 예측 장부
def test_ledger_detects_edits_deletions_and_insertions(engine):
    from sqlalchemy import text

    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    t0 = datetime(2026, 3, 2, 6, tzinfo=UTC)
    with session_scope(engine) as s:
        for i in range(6):
            s.add(_rec("005930", t0 + timedelta(days=i)))
    with session_scope(engine) as s:
        a1 = LG.anchor(s)
        s.add(_rec("000660", t0 + timedelta(days=7)))
    with session_scope(engine) as s:
        a2 = LG.anchor(s)
        assert LG.anchor(s) is None  # 새 예측이 없으면 봉인도 없음
        v = LG.verify(s)
    assert a1["n"] == 6 and a2["n"] == 1 and v["ok"] and v["sealed"] == 7 and v["anchors"] == 2
    # 결과를 채우는 것은 봉인을 깨지 않는다 (결과는 해시 밖)
    with session_scope(engine) as s:
        r = s.get(ConsensusRecord, 2)
        r.realized_return, r.correct = 0.03, True
    with session_scope(engine) as s:
        assert LG.verify(s)["ok"]
    # 결과를 본 뒤 확률을 고치면 → 해시 불일치
    with session_scope(engine) as s:
        s.execute(text("UPDATE consensus_signals SET prob_up = 0.9 WHERE id = 3"))
    with session_scope(engine) as s:
        v = LG.verify(s)
    assert not v["ok"] and v["tampered"] == [3]
    with session_scope(engine) as s:
        s.execute(text("UPDATE consensus_signals SET prob_up = 0.62 WHERE id = 3"))
        s.execute(text("DELETE FROM consensus_signals WHERE id = 4"))  # 틀린 예측 몰래 지우기
    with session_scope(engine) as s:
        v = LG.verify(s)
    assert not v["ok"] and v["tampered"] == [] and v["breaks"][0]["why"].startswith("기록 수")
    with session_scope(engine) as s:
        rows = LG.entries(s, limit=3)
    assert rows[0]["hash_ok"] and rows[0]["hash"] and rows[0]["created_at"]


def test_legacy_rows_are_counted_not_blamed(engine):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    with session_scope(engine) as s:
        s.add(ConsensusRecord(symbol="005930", as_of=datetime(2025, 1, 2, tzinfo=UTC), action="HOLD", prob_up=0.5,
                              confidence=40, conflict="low", payload={}))
        s.add(_rec("005930", datetime(2026, 1, 2, tzinfo=UTC)))
    with session_scope(engine) as s:
        LG.anchor(s)
        v = LG.verify(s)
    assert v["ok"] and v["legacy"] == 1 and v["sealed"] == 1


# ------------------------------------------------------------------ 결과 매칭
def test_forward_uses_next_open_and_waits_for_full_window():
    b = _bars(30)
    t = b.index[10]
    assert forward(b, t, 5) == pytest.approx(b["close"].iloc[15] / b["open"].iloc[11] - 1)
    assert forward(b, b.index[-3], 5) is None  # 기간이 아직 안 끝남
    assert forward(b, b.index[10] + pd.Timedelta(hours=3), 1) == pytest.approx(b["close"].iloc[11] / b["open"].iloc[11] - 1)


def test_match_fills_1_5_20_day_outcomes_and_excess(engine):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    b, bench = _bars(60, 1), _bars(60, 2)
    with session_scope(engine) as s:
        s.add(_rec("005930", b.index[10].to_pydatetime()))
        s.add(_rec("005930", b.index[45].to_pydatetime()))  # 20일은 아직
    with session_scope(engine) as s:
        assert match(s, {"005930": b}, bench) == 2
    with session_scope(engine) as s:
        a, c = s.get(ConsensusRecord, 1).payload["outcomes"], s.get(ConsensusRecord, 2).payload["outcomes"]
    assert set(a) >= {"1", "5", "20", "excess", "bench"} and "20" not in c and "5" in c
    assert a["excess"] == pytest.approx(a["5"] - forward(bench, b.index[10], 5), abs=1e-6)
    with session_scope(engine) as s:
        assert s.get(ConsensusRecord, 1).row_hash == LG.row_hash(s.get(ConsensusRecord, 1))  # 결과는 봉인 밖
        assert match(s, {"005930": b}, bench) == 0  # 다 붙은 것은 다시 안 건드림


# ------------------------------------------------------------------ 누수 감사
def test_leakage_audit_catches_future_news_and_feature_lookahead(engine):
    from quant_ai.data.db import session_scope
    from quant_ai.review.leakage import audit, recompute_features
    b = _bars(80, 3)
    t = b.index[40]
    good = recompute_features(b, t)
    future = recompute_features(b, b.index[45])  # 미래 가격으로 만든 피처
    with session_scope(engine) as s:
        s.add(_rec("005930", t.to_pydatetime(), payload={"evidence": {"price": good, "news": [{"ts": str(t - pd.Timedelta(hours=5))}]}}))
    with session_scope(engine) as s:
        r = audit(s, {"005930": b}, backtest_cfg={"embargo": 1, "horizon": 5})
    assert r["ok"] and {c["key"]: c["status"] for c in r["checks"]} == dict.fromkeys(["L1", "L2", "L3", "L4", "L5", "L6"], "pass")
    with session_scope(engine) as s:
        s.add(_rec("000660", t.to_pydatetime(), payload={"evidence": {"price": future, "news": [{"ts": str(t + pd.Timedelta(days=2))}]}}))
    with session_scope(engine) as s:
        r = audit(s, {"005930": b, "000660": b}, backtest_cfg={"embargo": 0, "horizon": 5})
    st = {c["key"]: c["status"] for c in r["checks"]}
    assert not r["ok"] and st["L1"] == "fail" and st["L3"] == "fail" and st["L5"] == "fail"


# ------------------------------------------------------------------ 독립 평가기
def test_statistics_helpers():
    lo, hi = E.wilson(60, 100)
    assert lo < 0.6 < hi and 0.49 < lo < 0.51
    assert E.binom_p(60, 100, 0.5) == pytest.approx(0.0284, abs=0.002)
    assert E.binom_p(900, 1500, 0.5) < 1e-10  # 정규 근사 구간
    rng = np.random.default_rng(0)
    y = (rng.random(2000) < 0.55).astype(int)
    p = np.clip(0.55 + rng.normal(0, 0.05, 2000), 0, 1)
    m = E.murphy(list(p), list(y))
    assert m["brier"] == pytest.approx(m["reliability"] - m["resolution"] + m["uncertainty"], abs=0.01)
    assert E.bootstrap_ci([0.01] * 5) is None


def test_evaluator_recomputes_compares_baselines_and_decomposes_errors(engine):
    from quant_ai.data.db import session_scope
    b = _bars(260, 5)
    with session_scope(engine) as s:
        for i in range(20, 240, 2):
            t = b.index[i]
            real = forward(b, t, 5)
            mom = float(b["close"].iloc[i] / b["close"].iloc[i - 20] - 1)
            p = 0.62 if real > 0 else 0.45  # 대부분 맞히는 예측
            if i % 10 == 0:
                p = 1 - p  # 가끔 틀림
            contribs = [{"analyst": "primary", "prob_up": p, "weight": 0.6},
                        {"analyst": "risk", "prob_up": 0.5 + (0.1 if real > 0 else -0.1), "weight": 0.4}]
            stored = real + (0.05 if i == 60 else 0)  # 한 건은 저장값이 틀림 → 재계산이 잡는다
            s.add(_rec("005930", t.to_pydatetime(), p=p, action="BUY" if p >= 0.55 else "HOLD", real=stored,
                       payload={"contributions": contribs, "evidence": {"price": {"ret_20": mom}}}))
    with session_scope(engine) as s:
        r = E.evaluate(s, {"005930": b}, ledger={"ok": True}, leakage={"ok": True})
    assert r["n"] == 110 and r["recomputed"] == 110 and len(r["mismatches"]) == 1 and r["mismatches"][0]["recomputed"] != r["mismatches"][0]["stored"]
    assert r["hit_rate"] == pytest.approx(88 / 110) and r["p_value"] < 1e-6 and r["status"] == "pass"
    assert r["baselines"]["momentum"] is not None and r["edge_vs_baseline"] > 0.1
    errs = r["errors"]
    by_ai = {x["analyst"]: x for x in errs["by_ai"]}
    assert by_ai["risk"]["hit_rate"] == 1.0 and by_ai["primary"]["blame_share"] > by_ai["risk"]["blame_share"]
    assert by_ai["risk"]["saves"] == errs["n_miss"] == 22  # 합의가 틀릴 때 Risk AI 는 맞았다
    assert errs["miss_reasons"] and r["brier"]["resolution"] > 0
    with session_scope(engine) as s:
        bad = E.evaluate(s, {"005930": b}, ledger={"ok": False}, leakage={"ok": True})
    assert bad["status"] == "fail" and "무결성" in bad["verdict"]
    assert r["sealed_share"] == 1.0
    # 봉인 전 기록뿐이면 성적이 좋아도 "검증됨" 이라 하지 않는다
    from sqlalchemy import update
    with session_scope(engine) as s:
        s.execute(update(ConsensusRecord).values(row_hash=None))
    with session_scope(engine) as s:
        legacy = E.evaluate(s, {"005930": b}, ledger={"ok": True}, leakage={"ok": True})
    assert legacy["status"] == "insufficient" and legacy["sealed_share"] == 0.0 and "증명 전" in legacy["verdict"]


# ------------------------------------------------------------------ 비용 · 사이징 · 업종 한도
def test_square_root_market_impact():
    from quant_ai.config import CostModelConfig
    from quant_ai.trading.portfolio import CostModel, Side
    cm = CostModel(CostModelConfig(slippage_bps=5, impact_coef=0.7))
    assert cm.fill_price(Side.BUY, 100.0) == pytest.approx(100.05)  # 거래대금 모름 → 고정 슬리피지만
    small = cm.fill_price(Side.BUY, 100.0, 1e6, 1e10, 0.02)
    big = cm.fill_price(Side.BUY, 100.0, 1e9, 1e10, 0.02)
    assert 100.05 < small < big and big - 100 == pytest.approx(0.05 + 0.7 * 0.02 * (0.1 ** 0.5) * 100, rel=1e-6)
    assert cm.fill_price(Side.SELL, 100.0, 1e12, 1e10, 0.05) == pytest.approx(100 * (1 - 0.0005 - 0.015))  # 상한 150bp


def test_half_kelly_satellite_sizing():
    from quant_ai.strategy.core_satellite import CoreSatelliteConfig, build_plan
    scores = pd.Series({f"{i:06d}": 1.0 - i / 100 for i in range(30)})
    buys = [{"symbol": "900001", "confidence": 70, "prob_up": 0.62}, {"symbol": "900002", "confidence": 68, "prob_up": 0.55},
            {"symbol": "900003", "confidence": 66, "prob_up": 0.50}]
    cfg = CoreSatelliteConfig(core_top_k=5, satellite_k=4)
    plan = build_plan(scores, [], True, cfg, {}, {}, buys, use_veto=True, use_satellite=True)
    w = {x["symbol"]: x["weight"] for x in plan.satellite}
    sat = (1 - cfg.core_weight) / cfg.satellite_k
    assert w["900001"] == pytest.approx(sat) and w["900002"] == pytest.approx(sat / 2) and "900003" not in w
    eq = build_plan(scores, [], True, CoreSatelliteConfig(core_top_k=5, satellite_k=4, satellite_sizing="equal"), {}, {}, buys,
                    use_veto=True, use_satellite=True)
    assert all(x["weight"] == pytest.approx(sat) for x in eq.satellite)


def test_sector_limit_caps_concentration():
    from datetime import date

    from quant_ai.config import RiskLimits
    from quant_ai.trading.portfolio import Order, Portfolio, Position, Side
    from quant_ai.trading.risk import RiskEngine
    pf = Portfolio(cash=6_000_000)
    pf.positions["A"] = Position(qty=300, avg_price=10_000)  # 반도체 3,000,000 (30%)
    r = RiskEngine(RiskLimits(max_position_weight=0.5, max_sector_weight=0.4, min_confidence=0.0))
    r.start_day(date(2026, 1, 2), 9_000_000)
    r.sectors = {"A": "반도체", "B": "반도체", "C": "은행"}
    prices = {"A": 10_000, "B": 10_000, "C": 10_000}
    d = r.check(Order("B", Side.BUY, 300), pf, prices)
    assert d.approved and d.order.qty == 60 and "업종 한도" in " ".join(d.reasons)  # 40% × 900만 − 300만 = 60만원
    assert r.check(Order("C", Side.BUY, 300), pf, prices).order.qty == 300

"""v13 예측력(SPRT·사전 등록·사후 신호 연구) · P2(옵션·실적 모델·대체데이터·이벤트 추출·KG·로테이션·수급·내 저널) ·
로드맵(A/B·WICS·원화 환산·공증) · 장애 복구 · 데스크/API."""

import base64
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops


# ------------------------------------------------------------------ 예측력
def test_required_n_and_sprt_decisions():
    from quant_ai.review.power import required_n, sprt
    assert 600 < required_n(0.5, 0.55) < 640 and required_n(0.5, 0.6) < required_n(0.5, 0.55)
    rng = np.random.default_rng(1)
    good = sprt(list(rng.random(2000) < 0.62), 0.52, 0.56)
    bad = sprt(list(rng.random(2000) < 0.47), 0.52, 0.56)
    assert good["decision"] == "H1" and bad["decision"] == "H0" and good["decided_at"] < 2000
    few = sprt([True, False, True], 0.52, 0.56)
    assert few["decision"] == "continue" and few["expected_more"] > 0 and "계속" in few["label"]


def _panel(n_sym=60, n_days=800, signal=0.0, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", periods=n_days, tz="UTC")
    skill = rng.normal(0, 1, n_sym)
    bars = {}
    for k in range(n_sym):
        r = rng.normal(0.0003 + signal * skill[k] * 0.001, 0.02, n_days)  # 꾸준히 오르는 종목 = 모멘텀이 잡을 신호
        c = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
        bars[f"{k:06d}"] = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e6})
    elig = pd.DataFrame(True, index=idx, columns=list(bars))
    return bars, elig


def test_signal_study_detects_planted_signal_and_placebo_is_null():
    from quant_ai.review.power import signal_study
    bars, elig = _panel(signal=1.5)
    r = signal_study(bars, elig, dev_end="2016-06-30", weights={"mom_12_1": 1.0})
    assert r["all"]["ic_mean"] > 0.05 and r["all"]["ic_t"] > 3
    assert abs(r["all"]["placebo_ic_mean"]) < 0.05 and r["grade"] in ("strong", "partial")
    none = signal_study(*_panel(signal=0.0, seed=5), dev_end="2016-06-30", weights={"mom_12_1": 1.0})
    assert abs(none["all"]["ic_mean"]) < 0.04 and "개발 구간" in none["verdict"]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v13p")
    fake_marcap(d, n_codes=6, days=300)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def test_preregistration_is_sealed_and_forward_test_counts_only_after(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    from quant_ai.review import ledger as LG
    from quant_ai.review.power import forward_test, preregister
    with session_scope(app.engine) as s:  # 등록 전 기록 (세지 않아야 함)
        s.add(ConsensusRecord(symbol="000010", as_of=datetime(2026, 1, 2, tzinfo=UTC), action="BUY", prob_up=0.9, confidence=90,
                              conflict="low", payload={}, realized_return=0.05))
    reg = preregister(app, p0=0.52, p1=0.56)
    assert reg["hash"] and reg["required_n_fixed"] > 500 and preregister(app)["hash"] == reg["hash"]
    with session_scope(app.engine) as s:
        for i in range(30):
            r = ConsensusRecord(symbol="000010", as_of=datetime(2026, 2, 1, tzinfo=UTC) + timedelta(days=i), action="BUY",
                                prob_up=0.6, confidence=60, conflict="low", payload={"horizon": 5}, realized_return=0.01 if i % 3 else -0.01)
            LG.seal(r)
            s.add(r)
        s.add(ConsensusRecord(symbol="000010", as_of=datetime(2026, 3, 9, tzinfo=UTC), action="BUY", prob_up=0.6, confidence=60,
                              conflict="low", payload={}, realized_return=0.02))  # 봉인 안 된 기록은 제외
    with session_scope(app.engine) as s:
        fw = forward_test(s, reg)
    assert fw["scored"] == 30 and fw["sealed"] == 30 and fw["since_registration"] == 31 and fw["hits"] == 20
    assert fw["decision"] == "continue" and fw["eta"] is not None
    new = preregister(app, force=True)
    assert new["version"] == 2 and len(ops.get_state(app.engine, "prereg")["history"]) == 2


# ------------------------------------------------------------------ P2
def _chain(spot=100.0):
    strikes = np.arange(80, 121, 5, dtype=float)
    calls = pd.DataFrame({"strike": strikes, "impliedVolatility": 0.30 - (strikes - spot) * 0.001, "bid": np.maximum(spot - strikes, 0) + 2.9,
                          "ask": np.maximum(spot - strikes, 0) + 3.1, "lastPrice": 3.0, "volume": 100.0, "openInterest": 1000.0})
    puts = pd.DataFrame({"strike": strikes, "impliedVolatility": 0.30 - (strikes - spot) * 0.004, "bid": np.maximum(strikes - spot, 0) + 2.9,
                         "ask": np.maximum(strikes - spot, 0) + 3.1, "lastPrice": 3.0, "volume": 200.0, "openInterest": 3000.0})
    return {"spot": spot, "chains": {"2026-10-16": {"calls": calls, "puts": puts}, "2026-11-20": {"calls": calls, "puts": puts}}}


def test_options_summary_implied_move_skew_maxpain(app):
    from quant_ai.data.collectors import options
    s = options.summarize(_chain(), earnings=date(2026, 10, 15), today=date(2026, 10, 1))
    assert s["implied_move"] == pytest.approx(0.06, abs=0.005) and s["skew"] > 0 and s["pc_oi"] == 3.0
    assert s["max_pain"] is not None and "earnings_implied_move" in s and "풋 쏠림" in s["read"]
    got = options.get(app.engine, "NVDA", fetch=lambda sym: _chain())
    assert got["available"] and options.get(app.engine, "005930")["available"] is False
    assert options.get(app.engine, "AMD")["available"] is False  # 오프라인 → 실패 기록 · 12시간 쉼


def test_earnings_model_posterior_and_reactions():
    from quant_ai.engines.earnings import model
    idx = pd.bdate_range("2024-01-01", periods=600, tz="UTC")
    c = pd.Series(100.0, index=idx)
    hist = []
    for k, d in enumerate(pd.bdate_range("2024-04-01", periods=8, freq="63B", tz="UTC")):
        beat = k % 4 != 3
        i = idx.get_loc(d)
        c.iloc[i:] *= 1.05 if beat else 0.90
        hist.append({"date": d.date().isoformat(), "eps_est": 1.0, "eps": 1.1 if beat else 0.9, "surprise_pct": 10.0 if beat else -10.0})
    bars = pd.DataFrame({"close": c, "open": c, "high": c, "low": c, "volume": 1e6})
    m = model(hist, bars, None, "US", today=date(2026, 6, 1))
    assert m["beats"] == 6 and m["p_beat"] == pytest.approx((7 + 6) / (10 + 8), abs=1e-3)
    assert m["reaction_on_beat"] == pytest.approx(0.05, abs=0.01) and m["reaction_on_miss"] == pytest.approx(-0.10, abs=0.01)
    assert "하회" in m["asymmetry"] and m["expected_move"] > 0
    assert model([], None, None, "KR")["p_beat"] == 0.5


def test_alt_attention_and_wiki_parse(app):
    from quant_ai.data.collectors import altdata
    series = [(f"d{i}", 1000.0 + (i % 5) * 20) for i in range(40)] + [(f"e{i}", 5000.0) for i in range(7)]
    a = altdata.attention(series)
    assert a["spike"] and a["ratio"] > 3
    fake = lambda url, *a, **k: {"items": [{"timestamp": f"2026{m:02d}{d:02d}00", "views": 100 + d % 3 + (900 if (m, d) >= (9, 24) else 0)}  # noqa: E731
                                           for m in (7, 8, 9) for d in range(1, 29)]}
    r = altdata.collect(app.engine, [("000010", "종목0")], fetch=fake)
    assert r["collected"] == 1 and altdata.for_context(app.engine, "000010")["wiki_spike"]


def test_event_extraction_types_amounts_materiality():
    from quant_ai.engines.event_extract import extract, parse_amount
    assert parse_amount("삼성전자, 1조 2,000억원 규모 공급계약") == pytest.approx(1.2e12)
    assert parse_amount("$3.5 billion deal") == pytest.approx(3.5e9 * 1350)
    ev = extract("SK하이닉스, 엔비디아와 5,000억원 규모 HBM 공급계약 체결", {"000660": "SK하이닉스", "NVDA": "엔비디아"},
                 "000660", market_cap=1e14)
    c = next(e for e in ev if e["type"] == "contract")
    assert c["amount_krw"] == 5e11 and c["materiality"] == 0.005 and c["counterparties"] == ["NVDA"] and c["polarity"] == 1
    assert {e["type"] for e in extract("OO바이오 유상증자 결정")} == {"rights_issue"}
    assert extract("XX전자 어닝 쇼크, 적자전환")[0]["type"] == "earnings_miss"
    assert extract("오늘의 날씨") == []


def test_knowledge_graph_typed_relations_two_hop_and_propagation():
    from quant_ai.engines.knowledge import neighbors, propagate, relation_types, two_hop
    assert relation_types("SK하이닉스, 엔비디아에 HBM 공급") == ["supply"]
    assert set(relation_types("네이버·카카오 AI 경쟁, 협력도")) == {"competitor", "partner"}
    g = {"nodes": {"A": {"name": "A"}, "B": {"name": "B"}, "C": {"name": "C"}},
         "edges": [{"a": "A", "b": "B", "weight": 1.0, "relation": "supply", "co_mention": 3},
                   {"a": "B", "b": "C", "weight": 0.8, "corr": 0.7}]}
    assert neighbors(g, "A")[0]["relation"] == "supply"
    th = two_hop(g, "A")
    assert th[0]["symbol"] == "C" and th[0]["via"] == "B"
    pr = propagate(g, {"B": 0.09, "C": 0.01})
    assert len(pr) == 1 and {n["symbol"] for n in pr[0]["neighbors"]} == {"A", "C"}


def test_sector_rotation_quadrants():
    from quant_ai.engines.sector import rotation
    idx = pd.bdate_range("2026-01-01", periods=200, tz="UTC")
    rng = np.random.default_rng(3)
    mk = lambda d: pd.DataFrame({"close": 100 * np.exp(np.cumsum(rng.normal(d, 0.01, 200)))}, index=idx)  # noqa: E731
    bars = {"L1": mk(0.004), "L2": mk(0.004), "G1": mk(-0.003)}
    r = rotation(bars, {"L1": "강한업종", "L2": "강한업종", "G1": "약한업종"}, mk(0.0))
    q = {x["sector"]: x for x in r}
    assert q["강한업종"]["rs_ratio"] > q["약한업종"]["rs_ratio"] and len(q["강한업종"]["trail"]) == 8
    assert q["강한업종"]["quadrant"] in ("주도 (Leading)", "약화 (Weakening)")


def test_investor_flow_enhanced_signals():
    from quant_ai.data.collectors.investor_flow import summarize
    rows = [{"date": f"2026-08-{i:02d}", "foreign": 1000.0 * (1 if i % 2 else -1), "inst": 500.0 * (1 if i % 3 else -1), "close": 100.0 - i * 0.3,
             "foreign_ratio": 0.3} for i in range(1, 32)]
    for r in rows[-5:]:
        r["foreign"], r["inst"] = 20000.0, 15000.0
    s = summarize(rows)
    assert s["foreign_z5"] > 3 and s["signal"] == "강한 순매수" and s["divergence"].startswith("매집")
    assert len(s["smart_money_curve"]) == 31 and "flow_ret5_corr" in s


def test_my_journal_sealed_scored_and_compared(app):
    from quant_ai import desk
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord, UserJournal
    sym = next(iter(app.market_data()[0]))
    b = app.market_data()[0][sym]
    t = b.index[-30].to_pydatetime()
    with session_scope(app.engine) as s:
        s.add(ConsensusRecord(symbol=sym, as_of=t, action="BUY", prob_up=0.7, confidence=70, conflict="low", payload={},
                              realized_return=float(b["close"].iloc[-24] / b["close"].iloc[-29] - 1)))
    from quant_ai.review.my_journal import add
    with session_scope(app.engine) as s:
        add(s, sym, "SELL", 5, 5, "고점 같아서", now=t)
        add(s, sym, "BUY", 2, 5, "눌림목", now=t)
    with pytest.raises(ValueError):
        desk.my_journal_add(app, {"symbol": sym, "action": "SHORT"})
    out = desk.my_journal(app)
    assert out["scored"] == 2 and out["pairs"]["n"] == 2 and out["disagree"]["n"] == 1 and not out["tampered"]
    with session_scope(app.engine) as s:
        r = s.query(UserJournal).first()
        r.reason = "사실은 다른 이유"  # 나중에 고침
    assert desk.my_journal(app)["tampered"]


# ------------------------------------------------------------------ 로드맵
def test_batch_ab_assignment_report_and_adjust(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import AnalystOpinionRecord
    from quant_ai.review.batch_ab import adjust, batch_size_for, is_single_arm, report
    share = np.mean([is_single_arm(f"{i:06d}", "2026-09-30") for i in range(2000)])
    assert 0.15 < share < 0.25 and is_single_arm("000010", "2026-09-30") == is_single_arm("000010", "2026-09-30 10:00")
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        for i in range(500):
            batched = i % 2 == 0
            hit = (i % 10) < (4 if batched else 7)
            s.add(AnalystOpinionRecord(analyst="primary", symbol="X", as_of=now - timedelta(days=1), horizon_bars=5, category="direction",
                                       prob_up=0.7, confidence=60, veto=False, payload={"batch": 3} if batched else {},
                                       realized_return=0.01 if hit else -0.01))
    with session_scope(app.engine) as s:
        rep = report(s)
    row = rep["rows"][0]
    assert row["analyst"] == "primary" and row["single"]["hit_rate"] > row["batch"]["hit_rate"] and row["p_value"] < 0.05
    assert adjust(app.engine, rep, {"primary": 4}) == {"primary": (4, 2)}
    assert batch_size_for(app.engine, type("A", (), {"name": "primary"})(), 4) == 2


def test_wics_parse_and_refresh_prefers_official_map(app):
    from quant_ai.data.collectors import wics
    from quant_ai.engines.sector import sector_map
    fake = lambda url: {"list": [{"CMP_CD": "5930", "CMP_KOR": "삼성전자", "SEC_NM_KOR": "IT", "IDX_NM_KOR": "WICS 반도체"}]} \
        if "G45" in url else {"list": []}  # noqa: E731
    ops.set_state(app.engine, "sector_map", {"map": {"005930": "전자·가전", "AAPL": "IT 하드웨어"}})
    r = wics.refresh(app.engine, fake, force=True)
    assert r["mapped"] == 1 and sector_map(app.engine) == {"005930": "IT", "AAPL": "IT 하드웨어"}
    assert wics.refresh(app.engine, fake)["cached"]


def test_fx_attribution_math():
    from quant_ai.review.fx_attrib import attribute
    idx = pd.bdate_range("2026-01-01", periods=60, tz="UTC")
    eq = pd.Series(np.linspace(100_000, 110_000, 60), index=idx)
    fx = pd.Series(np.linspace(1400, 1330, 60), index=idx)
    r = attribute(eq, fx)
    assert r["usd_return"] == pytest.approx(0.10) and r["fx_return"] == pytest.approx(-0.05)
    assert r["krw_return"] == pytest.approx(1.1 * 0.95 - 1) and "환율이 수익을 깎음" in r["read"]
    assert attribute(eq.iloc[:1], fx)["insufficient"]


def test_notary_ots_file_and_receipts(app):
    from quant_ai.review.notary import OTS_MAGIC, anchor_document, notarize, ots_file
    a = {"upto_id": 7, "n": 7, "digest": "ab" * 32, "prev_digest": "0" * 64, "ts": "2026-09-30T00:00:00+00:00"}
    doc = anchor_document(a)
    assert b"digest: " + ("ab" * 32).encode() in doc
    f = ots_file(b"\x11" * 32, b"\xf0\x10resp")
    assert f.startswith(OTS_MAGIC) and f[len(OTS_MAGIC):len(OTS_MAGIC) + 2] == b"\x01\x08"
    calls = []
    rec = notarize(app, a, post=lambda url, data, h: (calls.append(url), b"\xf0\x10resp")[1])
    assert rec["ok"] and calls[0].endswith("/digest") and rec["results"][0]["service"] == "opentimestamps"
    p = app.settings.artifacts_dir / "notary" / "anchor-7.txt.ots"
    assert p.exists() and p.read_bytes().startswith(OTS_MAGIC)
    assert notarize(app, a, post=lambda *x: b"")["skipped"] == "이미 공증됨"
    off = notarize(app, {**a, "upto_id": 8}, post=None)  # 네트워크 없음 → 실패 기록, 예외 없음
    assert off["ok"] is False and "error" in off["results"][0]
    assert base64 and json  # (모듈 사용 확인)


def test_recovery_startup_catch_up_and_source_health(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import JobRun
    from quant_ai.recovery import heartbeat, source_health, startup
    now = datetime.now(UTC)
    heartbeat(app.engine, now - timedelta(hours=30))
    with session_scope(app.engine) as s:
        s.add(JobRun(job="news", started_at=now - timedelta(hours=2)))
        for _ in range(3):
            s.add(JobRun(job="investor_flow", started_at=now - timedelta(hours=1), finished_at=now, ok=False, error="403"))
    r = startup(app, now)
    assert r["downtime_s"] > 86000 and r["stale_jobs"] >= 1 and r["db"]["ok"]
    assert {c["step"] for c in r["catch_up"]} >= {"결과 매칭", "복기 · 채점", "장부 봉인", "독립 평가", "드리프트"}
    assert ops.get_state(app.engine, "heartbeat")["at"] == now.isoformat()
    sh = {x["source"]: x for x in source_health(app, now)}
    assert sh["수급 (네이버)"]["status"] == "down" and sh["수급 (네이버)"]["last_error"] == "403"
    assert sh["뉴스 (RSS)"]["status"] in ("degraded", "down")


# ------------------------------------------------------------------ 데스크 · API
def test_desk_and_api_endpoints_run_end_to_end(app, tmp_path):
    from quant_ai import desk
    from quant_ai.web.api import DashboardAPI
    api = DashboardAPI(app)
    sym = next(iter(app.market_data()[0]))
    desk.event_calendar(app)
    assert api.readiness()["checks"] and api.freshness()["items"]["bar_kr"]["status"] in ("old", "stale", "fresh")
    cal = api.calendar()
    assert cal["events"] and "calendar_source" in cal
    p = api.power()
    assert p["prereg"]["hash"] and p["forward"]["decision"] in ("continue", "H0", "H1") and len(p["layers"]) == 3
    assert "아직 검증 전" in p["bottom_line"] or "판정" in p["bottom_line"] or "통과" in p["bottom_line"]
    ex = api.execution()
    assert "slippage" in ex and ex["broker"] == "none"
    d = api.desk(sym)
    assert d["symbol"] == sym and "plan" in d and "options" in d and d["options"]["available"] is False
    assert api.rotation()["quadrants"] and api.pipeline_status()["sources"]
    assert api.my_journal()["n"] >= 0
    dash = api.dashboard()
    assert dash["asof"]["now"].endswith("KST") and "readiness" in dash
    res = desk.kis_validate(app)  # 키 없음 → 실패 기록 (Readiness 가 읽는다)
    assert res["ok"] is False and "KIS" in res["message"]
    sl = desk.slippage_calibrate(app)
    assert sl["live"]["n"] == 0 and not sl["live"]["applied"]
    assert desk.model_decay(app)["status"] in ("insufficient", "stable", "watch", "decaying")


def test_evaluator_flags_significantly_worse_than_baseline(tmp_path):
    from quant_ai.data.db import init_db, make_engine, session_scope
    from quant_ai.data.models import ConsensusRecord
    from quant_ai.review import evaluator as E
    from quant_ai.review import ledger as LG
    from quant_ai.review.outcomes import forward
    e = make_engine(f"sqlite:///{tmp_path}/w.db")
    init_db(e)
    rng = np.random.default_rng(2)
    idx = pd.bdate_range("2025-01-01", periods=400, tz="UTC")
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, 400))), index=idx)
    b = pd.DataFrame({"open": c.shift(1).fillna(100), "high": c, "low": c, "close": c, "volume": 1e6})
    with session_scope(e) as s:
        for i in range(20, 380):
            real = forward(b, idx[i], 5)
            wrong = rng.random() < 0.65  # 65% 틀리는 예측
            p = 0.6 if (real > 0) == (not wrong) else 0.4
            r = ConsensusRecord(symbol="X", as_of=idx[i].to_pydatetime(), action="BUY", prob_up=p, confidence=60, conflict="low",
                                payload={"horizon": 5}, realized_return=real, correct=(p >= 0.5) == (real > 0),
                                created_at=idx[i].to_pydatetime() + timedelta(hours=1))
            LG.seal(r)
            s.add(r)
    with session_scope(e) as s:
        r = E.evaluate(s, {"X": b}, ledger={"ok": True}, leakage={"ok": True})
    assert r["status"] == "worse" and "나쁨" in r["verdict"] and r["p_value_worse"] < 0.05

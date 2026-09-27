"""Net Alpha · 자동 킬스위치(HALTED) · champion 롤백 · PIT 보정 · 종목 검색 · 채팅 AI · DB 정리."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops
from quant_ai.review.net_alpha import (
    ai_alpha,
    calibration_step,
    data_step,
    execution_step,
    proof_chain,
    relative,
    repeat_step,
)


# ------------------------------------------------------------------ 증명 체인 (순수 함수)
def _series(rets, start="2026-01-01"):
    idx = pd.bdate_range(start, periods=len(rets) + 1)
    return pd.Series(np.cumprod([1.0, *[1 + r for r in rets]]) * 1e7, index=idx)


def _books(n=120, ai_edge=0.0015, seed=1):
    rng = np.random.default_rng(seed)
    b = rng.normal(0.0003, 0.01, n)
    core_r = 0.6 * b + 0.0006 + rng.normal(0, 0.003, n)
    veto_r = core_r + ai_edge / 2 + rng.normal(0, 0.001, n)
    full_r = veto_r + ai_edge / 2 + rng.normal(0, 0.001, n)
    return _series(b), {"attr-core": _series(core_r), "attr-veto": _series(veto_r), "attr-full": _series(full_r)}


def test_proof_chain_all_six_pass_when_evidence_is_strong():
    bench, books = _books()
    regimes = pd.Series(["bull_quiet"] * 60 + ["sideways"] * 61, index=bench.index)
    r = proof_chain(books["attr-full"], bench, books=books,
                    data_check={"checked": 500, "ts_violations": 0, "adjusted": True, "pit_universe": True},
                    calibration={"n": 400, "brier_skill": 0.02, "ece": 0.03}, hit={"n": 300, "hits": 170},
                    main_cost=20_000, ai_cost_gap=0.001, regimes=regimes,
                    execution={"live": True, "twin": "attr-full", "tracking": relative(books["attr-full"] * 1.0001, books["attr-full"]),
                               "slippage_mean": 3.0, "assumed": 5.0, "fill_rate": 0.98, "n_orders": 80})
    st = {s["key"]: s["status"] for s in r["steps"]}
    assert st == {"data": "pass", "calib": "pass", "ai": "pass", "cost": "pass", "exec": "pass", "repeat": "pass"}, st
    assert r["verdict"]["status"] == "pass" and r["headline"]["ai_excess"] > 0 and r["headline"]["ai_t"] > 1.64
    a = r["ai_alpha"]
    assert a["veto"]["excess"] > 0 and a["satellite"]["excess"] > 0  # AI 알파를 거부권·위성으로 분리
    parts = {p["key"]: p["value"] for p in r["attribution"]}
    assert sum(v for k, v in parts.items() if k != "total") == pytest.approx(parts["total"], abs=1e-4)


def test_proof_chain_is_honest_about_failures_and_small_samples():
    bench, books = _books(ai_edge=-0.002, seed=2)
    r = proof_chain(books["attr-full"], bench, books=books, data_check={"checked": 10, "ts_violations": 2, "adjusted": True,
                                                                         "pit_universe": True})
    st = {s["key"]: s for s in r["steps"]}
    assert st["data"]["status"] == "fail" and st["ai"]["status"] == "fail" and r["verdict"]["status"] == "fail"
    assert st["exec"]["status"] == "insufficient"  # 가상매매만으로는 '실주문 동일' 을 증명할 수 없다
    assert calibration_step({"n": 50})["status"] == "insufficient"
    assert calibration_step({"n": 500, "brier_skill": -0.01, "ece": 0.02})["status"] == "fail"  # 찍기보다 못함
    assert data_step({"checked": 5, "ts_violations": 0, "adjusted": True, "forward_only": True})["status"] == "pass"
    assert data_step({"checked": 5, "ts_violations": 0, "adjusted": True})["status"] == "fail"  # 생존편향
    short_bench, short = _books(n=30, seed=3)
    assert ai_alpha(short)["total"]["days"] == 31
    assert proof_chain(short["attr-full"], short_bench, books=short)["steps"][2]["status"] == "insufficient"
    assert repeat_step(None, None)["status"] == "insufficient"
    assert execution_step({"live": True, "n_orders": 3})["status"] == "insufficient"
    empty = proof_chain(pd.Series(dtype=float), None)
    assert empty["verdict"]["status"] == "insufficient" and empty["attribution"] == []


# ------------------------------------------------------------------ 통합 픽스처 (가짜 marcap)
@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("trust")
    fake_marcap(d, n_codes=12, days=400)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a",
                        max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=10, end_year=2020)
    return a


def _run_days(app, n=3):
    from quant_ai.config import Mode
    from quant_ai.strategy.core_satellite import CoreSatelliteConfig
    from quant_ai.trading.broker import MarketQuote
    cfg = CoreSatelliteConfig(core_top_k=4, core_buffer_k=6, satellite_k=2, shortlist_k=6)
    bars, _, _ = app.market_data()
    for t in max(bars.values(), key=len).index[-n:]:
        q = {s: MarketQuote(last=float(b.loc[t, "close"])) for s, b in bars.items() if t in b.index}
        app.run_core_satellite(Mode.PAPER, as_of=t.to_pydatetime(), ts=t.to_pydatetime() + timedelta(hours=6),
                               quotes=q, cfg=cfg)


def test_analytics_run_on_real_shaped_data(app):
    from quant_ai import analytics as an
    _run_days(app)
    r = an.net_alpha_report(app, "paper")
    assert [x["key"] for x in r["steps"]] == ["data", "calib", "ai", "cost", "exec", "repeat"] and r["mode"] == "paper"
    assert r["steps"][0]["checked"] > 0 and r["steps"][0]["ts_violations"] == 0  # 판단 입력에 미래 정보 없음
    assert an.execution_quality(app, "paper", days=10_000)["orders"] > 0  # 가짜 데이터는 2020년
    cf = an.counterfactual(app)
    assert {"attr-core", "attr-full"} <= set(cf["paths"])
    st = an.stress_test(app, "paper")
    assert len(st["scenarios"]) >= 5 and all(s["portfolio"] <= 0.5 for s in st["scenarios"])
    conf = an.data_confidence(app)
    assert 0 <= conf["score"] <= 100 and conf["items"][0]["key"] == "prices"


def test_event_type_classifier():
    from quant_ai.analytics import event_type
    assert event_type("주요사항보고서(유상증자결정)") == "유상증자"
    assert event_type("자기주식취득결정") == "자사주 매입"
    assert event_type("영업(잠정)실적(공정공시)") == "실적 발표"
    assert event_type("기타 안내") is None


# ------------------------------------------------------------------ 자동 킬스위치
def test_guardian_halts_on_duplicate_orders_and_blocks_all_trading(app):
    from quant_ai.config import Mode
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import OrderRecord
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        for i in (1, 2):  # 같은 목표(base)의 미완료 주문 둘 = 멱등성 위반
            s.add(OrderRecord(mode="paper", created_at=now, symbol="000001", side="buy", qty=10, order_type="limit",
                              status="submitted", client_order_id=f"paper:2026-01-02:000001:buy:10#{i}"))
    g = app.guardian("paper")
    dup = next(c for c in g["conditions"] if c["key"] == "duplicate")
    assert dup["status"] == "critical" and g["state"] == "HALTED" and g["acted"]
    assert ops.halted(app.engine)
    assert app.trade([], Mode.PAPER, ts=now) == []  # HALTED: 매도 포함 새 주문 없음
    app.set_kill_switch(False, by="test")  # 사람이 해제
    assert not ops.halted(app.engine)
    with session_scope(app.engine) as s:
        for r in s.query(OrderRecord).filter(OrderRecord.client_order_id.like("paper:2026-01-02:000001%")):
            r.status = "cancelled"
    assert app.guardian("paper", act=False)["state"] in ("TRADING", "DEGRADED")


def test_guardian_conditions_volatility_loss_broker(app):
    from quant_ai.trading import guardian as gd
    idx = pd.bdate_range("2026-01-01", periods=80, tz="UTC")
    calm = pd.DataFrame({"close": 100 * np.cumprod(1 + np.r_[np.full(79, 0.001), 0.0])}, index=idx)
    crash = calm.copy()
    crash.iloc[-1, 0] = crash.iloc[-2, 0] * 0.93
    assert gd.check_volatility(calm)["status"] == "ok"
    assert gd.check_volatility(crash)["status"] == "critical"
    for _ in range(3):
        ops.record_health(app.engine, "broker", False, "timeout")
    app_live = type("A", (), {"settings": replace(app.settings, broker="kis"), "engine": app.engine})()
    assert gd.check_broker(app_live, "live")["status"] == "critical"
    ops.record_health(app.engine, "broker", True)
    assert gd.check_broker(app_live, "live")["status"] == "ok"
    assert gd.check_broker(app, "paper")["status"] == "na"
    ops.set_state(app.engine, "reconcile", {"streak": 2, "drift_n": 3})
    assert gd.check_position(app, "live")["status"] == "critical"
    ops.set_state(app.engine, "reconcile", {"streak": 0})
    ops.set_state(app.engine, "live_quotes", {"ts": datetime.now(UTC).isoformat(), "conflicts": ["000001"], "bad": 0})
    assert gd.check_data_conflict(app_live, "live")["status"] == "critical"
    ops.set_state(app.engine, "live_quotes", {})


# ------------------------------------------------------------------ champion 롤백
def test_champion_rollback_restores_previous(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import AnalystOpinionRecord, ModelRecord
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        s.add(ModelRecord(name="q", version="old", created_at=now - timedelta(days=90), status="retired",
                          artifact_path="x"))
        s.add(ModelRecord(name="q", version="new", created_at=now - timedelta(days=30), status="champion",
                          artifact_path="y", shadow_metrics={"promoted_at": (now - timedelta(days=30)).isoformat()}))
        for i in range(80):  # 전진 적중률 30% → 롤백 기준(45%) 미달
            s.add(AnalystOpinionRecord(consensus_id=None, analyst="quant", symbol="000001",
                                       as_of=now - timedelta(days=20, minutes=i), horizon_bars=5, category="direction",
                                       prob_up=0.6 + (i % 7) * 0.01, confidence=0.5, veto=False, correct=i % 10 < 3,
                                       realized_return=0.01 if i % 10 < 3 else -0.01, payload={"model_version": "new"}))
    fwd = app.check_champion(rollback=False)
    assert fwd["n"] == 80 and not fwd["passed"]
    res = app.check_champion(rollback=True)
    assert res["rollback"] == {"rolled_back": "new", "restored": "old", "reason": res["rollback"]["reason"]}
    assert app.registry.champion().version == "old"
    assert ops.get_state(app.engine, "model_events")["events"][-1]["kind"] == "rollback"


# ------------------------------------------------------------------ PIT 보정 · 가중치
def test_point_in_time_calibration_and_weights(app):
    from quant_ai.data.db import session_scope
    from quant_ai.ensemble.calibration import calibrators_as_of, fit_calibrators
    from quant_ai.ensemble.engine import CONSENSUS_KEY
    from quant_ai.ensemble.tracker import scoreboard
    hist = {"fits": [{"at": "2026-01-10T00:00:00+00:00", "fitted": {"primary": {"a": 0.5, "b": 0.0, "n": 60}}},
                     {"at": "2026-03-10T00:00:00+00:00", "fitted": {"primary": {"a": 0.2, "b": 0.0, "n": 90}}}]}
    assert calibrators_as_of(hist, {}, datetime(2026, 2, 1, tzinfo=UTC))["primary"].a == 0.5
    assert calibrators_as_of(hist, {}, datetime(2026, 1, 1, tzinfo=UTC)) == {}  # 그 시점엔 보정기 없음
    assert calibrators_as_of(hist, {"primary": {"a": 0.9, "b": 0, "n": 1}}, None)["primary"].a == 0.9
    with session_scope(app.engine) as s:
        now = datetime.now(UTC)
        full = scoreboard(s, window_days=None)
        pit = scoreboard(s, window_days=None, until=now - timedelta(days=15))
        assert sum(r.n for r in pit.values()) <= sum(r.n for r in full.values())
        from quant_ai.data.models import ConsensusRecord
        for i in range(60):  # 채점된 합의: 절반은 오래전, 절반은 최근(판단 시점엔 결과 모름)
            t = now - timedelta(days=40 if i < 30 else 2)
            s.add(ConsensusRecord(symbol="000001", as_of=t, action="HOLD", prob_up=0.7, confidence=50, conflict="low",
                                  correct=i % 2 == 0, realized_return=0.01 if i % 2 == 0 else -0.01,
                                  payload={"prob_raw": 0.7}))
        s.flush()
        fitted = fit_calibrators(s, window_days=3650, min_n=20)
        assert fitted[CONSENSUS_KEY]["n"] == 60  # 합의 확률 보정기도 함께 적합
        past = fit_calibrators(s, window_days=3650, min_n=20, as_of=now - timedelta(days=20))
        assert past[CONSENSUS_KEY]["n"] == 30  # 그 시점에 결과가 나와 있던 것만 (PIT)


def test_ensemble_calibrator_pulls_uninformative_consensus_to_half():
    from quant_ai.analysts.base import Opinion
    from quant_ai.ensemble.calibration import Platt
    from quant_ai.ensemble.engine import CONSENSUS_KEY, EnsembleEngine
    ops_ = [Opinion("primary", "X", prob_up=0.75, confidence=0.9, reasons=["r"]),
            Opinion("nvidia", "X", prob_up=0.72, confidence=0.9, reasons=["r"])]
    raw = EnsembleEngine().combine("X", ops_)
    cal = EnsembleEngine().combine("X", ops_, calibrators={CONSENSUS_KEY: Platt(0.05, 0.0, 100)})
    assert raw.prob_raw is None and cal.prob_raw == pytest.approx(raw.prob_up)
    assert abs(cal.prob_up - 0.5) < abs(raw.prob_up - 0.5) and cal.action != "BUY"


# ------------------------------------------------------------------ 검색 · 해외 종목
def test_search_korean_aliases_and_global(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.global_stocks import ensure_global, find_in_text, search
    with session_scope(app.engine) as s:
        assert search(s, "엔비디아")[0]["symbol"] == "NVDA"
        assert search(s, "애플")[0]["symbol"] == "AAPL"
        assert search(s, "aapl")[0]["symbol"] == "AAPL"
        assert [x["symbol"] for x in find_in_text(s, "엔비디아랑 테슬라 어때?")] == ["NVDA", "TSLA"]
    idx = pd.bdate_range("2025-01-01", periods=300, tz="UTC")
    df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": np.linspace(100, 150, 300), "volume": 1e6},
                      index=idx)
    r = ensure_global(app.engine, "NVDA", "엔비디아", fetchers=(("fake", lambda sym: df),))
    assert r["ok"] and r["source"] == "fake" and r["rows"] == 300
    assert ensure_global(app.engine, "NVDA", fetchers=(("boom", lambda s: 1 / 0),))["source"] == "cache"
    assert "NVDA" not in app.symbols()  # 해외 조회 종목은 국내 전략 유니버스에 섞이지 않는다
    bad = ensure_global(app.engine, "ZZZZ", fetchers=(("boom", lambda s: 1 / 0),))
    assert not bad["ok"] and "boom" in bad["error"]


# ------------------------------------------------------------------ 채팅 AI
class FakeLLM:
    """OpenAI 호환 도구 호출을 흉내: 1회차 도구 호출 → 2회차 최종 답."""

    def __init__(self, tool_calls=True):
        self.models = ("fake-pro",)
        self.model = "fake-pro"
        self.exhausted = {}
        self.bodies = []
        self.tool_calls = tool_calls

    def _post(self, path, body):
        self.bodies.append(body)
        if self.tool_calls and len(self.bodies) == 1:
            return {"choices": [{"message": {"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "server_status", "arguments": "{}"}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        tool_msgs = [m for m in body["messages"] if m.get("role") == "tool"]
        return {"choices": [{"message": {"content": f"도구 {len(tool_msgs)}개 확인 완료"}}], "usage": {}}


def test_chat_rules_mode_answers_stock_server_db(app):
    from quant_ai.assistant import history, reply
    no_llm = lambda st: (None, None)  # noqa: E731
    name = next(iter(app.market_data()[0]))
    r = reply(app, f"{name} 어때?", "s1", client_factory=no_llm)
    assert r["mode"] == "rules" and r["tools_used"][0]["tool"] == "stock_overview"
    r = reply(app, "서버 상태 알려줘", "s1", client_factory=no_llm)
    assert "서버 상태" in r["answer"] and "DB" in r["answer"]
    r = reply(app, "DB 정리해줘", "s1", client_factory=no_llm)
    assert r["actions"] and r["actions"][0]["action"] == "db_clean"  # 삭제는 사람이 버튼으로
    assert len(history(app.engine, "s1")) == 6


def test_chat_llm_tool_loop_logs_usage(app):
    from sqlalchemy import select

    from quant_ai.assistant import reply
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import LLMCall
    fake = FakeLLM()
    r = reply(app, "요즘 어때?", "s2", client_factory=lambda st: (fake, "gemini"))
    assert r["mode"] == "llm" and r["answer"] == "도구 1개 확인 완료"
    assert [t["tool"] for t in r["tools_used"]] == ["server_status"]
    assert fake.bodies[0]["tools"] and fake.bodies[0]["messages"][0]["role"] == "system"
    with session_scope(app.engine) as s:
        assert s.scalar(select(LLMCall).where(LLMCall.analyst == "chat")) is not None
    # 모델이 도구를 지원하지 않으면(400) 미리 모은 데이터로 답한다
    import urllib.error

    class NoTools(FakeLLM):
        def _post(self, path, body):
            if "tools" in body:
                raise urllib.error.HTTPError("u", 400, "bad", {}, None)
            return {"choices": [{"message": {"content": "도구 없이 답"}}]}
    r = reply(app, "서버 상태", "s3", client_factory=lambda st: (NoTools(False), "cloudflare"))
    assert r["answer"] == "도구 없이 답" and r["tools_used"][0]["tool"] == "server_status"


def test_chat_client_prefers_gemini_pro():
    from quant_ai.assistant import chat_client
    from quant_ai.config import Settings
    c, prov = chat_client(Settings.from_env({"GROQ_API_KEY": "g", "GEMINI_API_KEY": "x"}))
    assert prov == "gemini" and c.models[0] == "gemini-pro-latest"  # 항상 최신 세대 별칭
    c, prov = chat_client(Settings.from_env({"GROQ_API_KEY": "g", "GEMINI_API_KEY": "x", "QUANT_CHAT_PROVIDER": "groq"}))
    assert prov == "groq"
    assert chat_client(Settings.from_env({}))[0] is None


# ------------------------------------------------------------------ DB 정리 · 준비 상태 · API
def test_db_maintenance_keeps_audit_records(app):
    from sqlalchemy import func, select

    from quant_ai.actions import db_maintenance
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord, JobRun
    old = datetime.now(UTC) - timedelta(days=60)
    with session_scope(app.engine) as s:
        for _ in range(3):
            s.add(JobRun(job="x", started_at=old, ok=True))
        n_cons = s.scalar(select(func.count()).select_from(ConsensusRecord))
    prev = db_maintenance(app, dry_run=True)
    assert next(r for r in prev["rows"] if r["key"] == "jobs")["rows"] >= 3
    done = db_maintenance(app, dry_run=False)
    assert "VACUUM" in done["vacuum"]
    with session_scope(app.engine) as s:
        assert s.scalar(select(func.count()).select_from(JobRun).where(JobRun.started_at < old + timedelta(days=1))) == 0
        assert s.scalar(select(func.count()).select_from(ConsensusRecord)) == n_cons


def test_dashboard_api_new_endpoints(app):
    from quant_ai.web.api import DashboardAPI
    api = DashboardAPI(app)
    assert len(api.net_alpha()["steps"]) == 6 and api.net_alpha(market="US")["market"] == "US"
    assert api.guardian()["conditions"][0]["key"] == "broker"
    for kind in ("execution", "counterfactual", "events", "confidence", "stress", "experiments", "ai_verdict", "champion"):
        assert "error" not in api.analytics(kind), kind
    assert api.search("엔비디아")["results"][0]["symbol"] == "NVDA"
    st = api.setup()
    assert st["steps"][0]["key"] == "prices" and st["steps"][0]["done"]
    assert api.server()["db"]["ok"]
    a = api.action("guardian", start=True)
    import time
    for _ in range(50):
        if not api.action("guardian").get("running"):
            break
        time.sleep(0.1)
    assert api.action("guardian")["result"]["state"]
    with pytest.raises(ValueError):
        api.action("rm -rf", start=True)
    assert a["name"] == "guardian"


# ------------------------------------------------------------------ 해외(미국) 장부
def test_us_books_run_same_rules_and_proof_chain(app):
    from quant_ai import global_market as gm
    from quant_ai.analytics import net_alpha_report
    from quant_ai.data.collectors.prices import SyntheticPriceSource
    src = SyntheticPriceSource(seed=11)
    fake = (("fake", lambda sym: src.fetch_bars(sym, datetime(2025, 1, 1), datetime(2026, 6, 30))),)
    r = gm.sync(app, fetchers=fake)
    assert r["ok"] == len(gm.universe()) + 1 and not r["failed"]
    assert not set(gm.universe()) & set(app.symbols())  # 국내 전략 유니버스와 분리
    bars, bench, _ = gm.market_data(app)
    for t in bench.index[-25:]:
        c = gm.run_cycle(app, as_of=t.to_pydatetime(), ts=t.to_pydatetime() + timedelta(hours=22))
        assert "skipped" not in c, c
    assert c["analyzed"] > 0 and set(c["books"]) == {gm.MAIN, *gm.BOOKS}
    pf = app.load_portfolio("us-attr-core")
    assert pf.positions and pf.cash < gm.CASH_USD  # USD 장부로 매수
    rep = net_alpha_report(app, market="US")
    assert rep["market"] == "US" and rep["bench_name"] == "SPY" and rep["currency"] == "USD"
    assert rep["ai_alpha"] is not None and rep["headline"]["days"] >= 20
    assert rep["steps"][0]["forward_only"]  # 미국: 과거 백테스트 없이 전진 기록만
    assert rep["steps"][4]["status"] == "insufficient"  # 해외 실주문 없음
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import FillRecord, OrderRecord
    with session_scope(app.engine) as s:
        o = s.query(OrderRecord).filter(OrderRecord.mode == "us-attr-core", OrderRecord.side == "buy",
                                        OrderRecord.status == "filled").first()
        f = s.query(FillRecord).filter(FillRecord.order_id == o.id).first()
        assert f.fee == pytest.approx(f.qty * f.price * 0.0025, rel=0.05)  # 해외주식 수수료 0.25%, 매도세 없음

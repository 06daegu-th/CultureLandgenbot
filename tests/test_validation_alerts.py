"""예측 성적표 · 검증 사다리(승격·강등) · 실시간 알림 · 이벤트 재분석 · 커뮤니티 · 24H 관제실 — 네트워크 없이."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from quant_ai import ops
from quant_ai.review import ladder as L
from quant_ai.review.scorecard import expected_move, scorecard

NOW = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)


# ------------------------------------------------------------------ 예측 성적표 (순수)
def test_expected_move_turns_probability_into_size():
    assert expected_move(0.5, 0.02, 5)[0] == pytest.approx(0.0, abs=1e-12)
    up, up1 = expected_move(0.68, 0.02, 5)
    assert 0.02 < up < 0.03 and up1 == pytest.approx(up / 5)  # 하루치 = 기간 기대수익 / 기간
    assert expected_move(0.3, 0.02, 5)[0] < 0 and expected_move(0.68, None, 5) == (None, None)


@pytest.fixture()
def engine(tmp_path):
    from quant_ai.data.db import init_db, make_engine
    e = make_engine(f"sqlite:///{tmp_path}/v.db")
    init_db(e)
    return e


def _add(s, sym, day, action, p, real, regime="bull_quiet", horizon=5, vol=0.02, rid_payload=None):
    from quant_ai.data.models import ConsensusRecord
    exp = expected_move(p, vol, horizon)[0]
    s.add(ConsensusRecord(symbol=sym, as_of=day, action=action, prob_up=p, confidence=60, conflict="low",
                          payload={"horizon": horizon, "regime": regime, "expected_return": exp,
                                   "contributions": [], **(rid_payload or {})},
                          realized_return=real, correct=None if real is None else (p >= 0.5) == (real > 0)))


def test_scorecard_counts_money_not_just_hits(engine):
    from quant_ai.data.db import session_scope
    d0 = datetime(2026, 6, 1, 6, tzinfo=UTC)
    with session_scope(engine) as s:
        # 10번 중 6번 방향 적중. 맞힌 것은 크게(+3%), 틀린 것은 작게(−1%)
        for i in range(10):
            hit = i < 6
            _add(s, "005930", d0 + timedelta(days=i), "BUY", 0.65, 0.03 if hit else -0.01,
                 regime="bull_quiet" if i % 2 else "sideways")
        _add(s, "005930", d0 + timedelta(days=9, hours=2), "HOLD", 0.55, 0.03)  # 같은 날 재판단 → 마지막만 센다
        _add(s, "000660", d0 + timedelta(days=20), "SELL", 0.30, None)  # 결과 대기
    with session_scope(engine) as s:
        sc = scorecard(s, "KR", 100, cost=0.0025, now=d0 + timedelta(days=30))
    sm = sc["summary"]
    assert sm["n"] == 10 and sc["pending"] == 1 and sc["total_scored"] == 10
    assert sm["hit_rate"] == pytest.approx(0.7)  # 날짜별 마지막만: 10일째 빗나간 BUY 대신 적중한 HOLD → 7/10
    assert sm["n_trades"] == 9  # BUY 9개 (마지막 날은 HOLD 로 대체)
    assert sm["avg_signal_net"] == pytest.approx((6 * 0.03 + 3 * -0.01) / 9 - 0.0025)
    assert sm["mdd"] <= 0 and sm["avg_expected"] > 0
    misses = [x for x in sc["recent"] if not x["hit"]]
    assert misses and all(x["miss_reason"] for x in misses)  # 틀린 이유 자동 기록
    assert {r["regime"] for r in sc["by_regime"]} == {"bull_quiet", "sideways"}
    assert sc["pending_list"][0]["symbol"] == "000660"
    with session_scope(engine) as s:
        assert scorecard(s, "US")["summary"] == {"n": 0}


# ------------------------------------------------------------------ 검증 사다리 (순수)
def _ev(n=400, hit=0.6, net=0.004, bss=0.03, ece=0.03, mdd=-0.05, days=120, paper_days=60, excess=0.01,
        regimes=(("bull_quiet", 0.6), ("sideways", 0.55)), r50=0.6, consent=True, **kw):
    return {"summary": {"n": n, "hit_rate": hit, "avg_signal_net": net, "n_trades": n // 2, "brier_skill": bss, "ece": ece,
                        "mdd": mdd, "cost": 0.0025},
            "last_30d": {"n": 60, "hit_rate": hit, "avg_signal_net": net, "n_trades": 30},
            "by_regime": [{"regime": r, "n": 50, "hit_rate": h} for r, h in regimes], "days_tracked": days,
            "rolling50": {"n": 50, "hit_rate": r50}, "paper": {"days": paper_days, "excess": excess, "dd_diff": 0.0},
            "backtest_ok": True, "live_consent": consent, **kw}


def test_seven_conditions_and_one_step_promotions():
    conds = {c["key"]: c["status"] for c in L.conditions(_ev())}
    assert conds == dict.fromkeys(("n", "hit", "net", "calib", "dd", "recent", "regime"), "pass")
    few = {c["key"]: c["status"] for c in L.conditions(_ev(n=120))}
    assert few["n"] == "fail" and few["hit"] == "wait"  # 표본이 모자라면 '판정 보류'
    bad = {c["key"]: c["status"] for c in L.conditions(_ev(hit=0.52, net=-0.001, regimes=(("bull_quiet", 0.7), ("bear_volatile", 0.4))))}
    assert bad["hit"] == bad["net"] == bad["regime"] == "fail"
    st, res = L.step({}, _ev(), NOW)
    assert (res["stage"], res["changed"]) == ("shadow", "promote")  # 과거 검증 → Shadow
    st, res = L.step(st, _ev(), NOW)
    assert res["stage"] == "paper"  # 한 번에 한 칸
    st, res = L.step(st, _ev(consent=False), NOW)
    assert res["stage"] == "paper" and res["ready"] == "live_small"  # 실제 돈은 사람의 사전 동의가 있어야
    st, res = L.step(st, _ev(), NOW)
    assert res["stage"] == "live_small" and [h["to"] for h in st["history"]] == ["shadow", "paper", "live_small"]
    st2, res = L.step(st, _ev(live={"days": 70, "return": 0.02, "max_drawdown": -0.03}), NOW)
    assert res["stage"] == "live_small" and res["ready"] == "live"  # 확대는 자동으로 하지 않는다


def test_demotion_goes_straight_to_shadow_with_cooldown():
    st = {"stage": "live_small"}
    st, res = L.step(st, _ev(r50=0.42), NOW)
    assert res["changed"] == "demote" and res["stage"] == "shadow" and "적중률" in res["reasons"][0]
    st, res = L.step(st, _ev(), NOW + timedelta(days=3))
    assert res["stage"] == "shadow" and res["cooling"]  # 14일은 재승격 없음
    st, res = L.step(st, _ev(), NOW + timedelta(days=15))
    assert res["stage"] == "paper"
    assert L.step({"stage": "live_small"}, _ev(live={"days": 5, "max_drawdown": -0.12}), NOW)[1]["changed"] == "demote"
    held = L.step({"stage": "paper"}, _ev(halted=True), NOW)[1]
    assert held["changed"] is None and "HALTED" in held["reasons"][0]  # 가상 단계는 강등 안 하지만 승격도 멈춘다
    assert L.step({"stage": "shadow"}, _ev(n=50, hit=0.5, bss=0.0), NOW)[1]["stage"] == "shadow"


# ------------------------------------------------------------------ 앱 통합
@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("val")
    fake_marcap(d, n_codes=6, days=300)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a",
                        max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def test_ladder_gates_ai_use_and_live_capital(app):
    from quant_ai.config import Mode
    base = app.settings
    try:
        app.settings = replace(base, core_only=True, auto_promote=True, broker="kis", kis_env="real",
                               live_max_capital=1_000_000, live_small_capital=200_000)
        ops.set_state(app.engine, "ladder", {"stage": "shadow"})
        assert not app.ai_enabled(Mode.PAPER) and not app.ai_enabled(Mode.LIVE)
        ops.set_state(app.engine, "ladder", {"stage": "paper"})
        assert app.ai_enabled(Mode.PAPER) and not app.ai_enabled(Mode.LIVE)  # 실제 돈은 아직
        assert app.live_cap() == 1_000_000
        ops.set_state(app.engine, "ladder", {"stage": "live_small"})
        assert app.ai_enabled(Mode.LIVE) and app.live_cap() == 200_000  # 소액 Live: 20만원 상한
        app.settings = replace(app.settings, core_only=False, auto_promote=False)
        ops.set_state(app.engine, "ladder", {"stage": "shadow"})
        assert app.ai_enabled(Mode.PAPER) and not app.ai_enabled(Mode.LIVE)  # 수동으로 켜도 실전은 사다리가 막는다
        app.settings = replace(app.settings, kis_env="demo")
        assert app.ai_enabled(Mode.LIVE)  # 모의투자 계좌는 실제 돈이 아니다
    finally:
        app.settings = base
        ops.set_state(app.engine, "ladder", {})
    r = app.ladder(act=True, now=NOW)
    assert r["state"]["stage"] in ("backtest", "shadow") and len(r["conditions"]) == 7 and len(r["stages"]) == 5


def test_price_watch_alerts_on_levels_and_fast_moves(app):
    from quant_ai.actions import add_watch
    from quant_ai.alerts import price_watch, recent
    sym = next(iter(app.market_data()[0]))
    add_watch(app, sym)
    q = {"price": 10_000.0, "chg_pct": 0.012}
    f = {"kr": lambda codes: {sym: dict(q)}, "us": lambda syms: {}}
    price_watch(app, NOW, fetchers=f, markets={"KR"})
    assert not [a for a in recent(app.engine)["items"] if a["kind"] == "price"]
    q.update(price=10_400.0, chg_pct=0.052)  # 10분 새 +4% · 전일 대비 +5.2%
    r = price_watch(app, NOW + timedelta(minutes=10), fetchers=f, markets={"KR"})
    titles = [a["title"] for a in recent(app.engine)["items"] if a["kind"] == "price"]
    assert r["alerts"] == 3 and any("급등 +5.2%" in t for t in titles) and any("급격한 상승" in t for t in titles)
    assert price_watch(app, NOW + timedelta(minutes=12), fetchers=f, markets={"KR"})["alerts"] == 0  # 같은 알림 반복 없음
    assert app.engine and ops.get_state(app.engine, "live_quotes")[sym]["price"] == 10_400.0
    assert price_watch(app, NOW, fetchers=f, markets=set())["checked"] == 0  # 장이 닫혀 있으면 조회 안 함


def test_alert_scan_signals_disclosures_results_and_halt(app):
    from datetime import date

    from quant_ai.alerts import alert_scan, recent
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Disclosure
    sym = next(iter(app.market_data()[0]))
    ops.set_state(app.engine, "alert_cursor", {})
    assert alert_scan(app, NOW)["first_run"]  # 처음엔 지나간 것을 쏟아내지 않는다
    with session_scope(app.engine) as s:
        _add(s, sym, NOW, "BUY", 0.68, None)
        _add(s, sym, NOW + timedelta(hours=1), "BUY", 0.66, None)  # 같은 신호 반복 → 알림 없음
        s.add(Disclosure(source="DART", receipt_no="r1", symbol=sym, title="단일판매·공급계약체결", filed_at=date(2026, 9, 30),
                         url="https://dart.example/1"))
    ops.set_kill_switch(app.engine, True, "Broker disconnect", by="guardian", halt=True)
    try:
        r = alert_scan(app, NOW + timedelta(minutes=2))
    finally:
        ops.set_kill_switch(app.engine, False, "", by="test")
    items = recent(app.engine)["items"]
    kinds = [a["kind"] for a in items]
    assert kinds.count("signal") == 1 and "disclosure" in kinds and "guardian" in kinds and r["alerts"] >= 3
    sig = next(a for a in items if a["kind"] == "signal")
    assert "BUY" in sig["title"] and "예상 +" in sig["body"] and sig["link"] == f"#analysis/{sym}"
    assert alert_scan(app, NOW + timedelta(minutes=4))["alerts"] >= 1  # 해제 알림
    assert recent(app.engine, after=items[0]["id"])["items"][0]["title"] == "매매 정지 해제"


def test_event_reanalysis_runs_once_per_cooldown(app):
    from datetime import date

    from quant_ai.actions import add_watch, event_reanalyze
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord, Disclosure
    sym = list(app.market_data()[0])[1]
    add_watch(app, sym)
    ops.set_state(app.engine, "event_cursor", {})
    assert event_reanalyze(app, NOW)["first_run"]
    with session_scope(app.engine) as s:
        s.add(Disclosure(source="DART", receipt_no="r2", symbol=sym, title="유상증자결정", filed_at=date(2026, 9, 30),
                         url="https://dart.example/2"))
    r = event_reanalyze(app, NOW + timedelta(minutes=10))
    assert r["reanalyzed"] == [sym]
    with session_scope(app.engine) as s:
        rec = s.query(ConsensusRecord).filter_by(symbol=sym).order_by(ConsensusRecord.id.desc()).first()
        assert rec.payload["trigger"].startswith("공시: 유상증자") and rec.payload.get("expected_return") is not None
    with session_scope(app.engine) as s:
        s.add(Disclosure(source="DART", receipt_no="r3", symbol=sym, title="정정", filed_at=date(2026, 9, 30), url="u"))
    assert event_reanalyze(app, NOW + timedelta(minutes=20))["reanalyzed"] == []  # 3시간 쿨다운


def test_community_mood_is_collected_and_given_to_ai_as_weak_hint(app):
    from quant_ai.data.collectors import community as C
    posts = [{"title": "to the moon", "sentiment": 0.6}, {"title": "bag", "sentiment": -0.6}, {"title": "lfg", "sentiment": 0.6}]
    r = C.collect(app.engine, ["NVDA", "005930"], fetchers={"us": lambda s: posts, "kr": C.fetch_naver_board}, now=NOW)
    assert r["collected"] == ["NVDA"] and r["failed"]  # 국내는 오프라인 → 다음 회차(1시간 쉼)
    ctx = C.for_context(app.engine, "NVDA", now=NOW + timedelta(hours=1))
    assert ctx["bullish"] == 2 and ctx["bearish"] == 1 and "신뢰도 낮음" in ctx["note"]
    assert C.for_context(app.engine, "NVDA", now=NOW + timedelta(hours=7)) == {}  # 오래된 분위기는 안 준다
    assert C.summarize([])["label"] == "중립"


def test_control_room_and_endpoints(app):
    from quant_ai.web.api import DashboardAPI
    api = DashboardAPI(app)
    c = api.control()
    assert [s["key"] for s in c["stages"]] == ["collect", "organize", "analyze", "decide", "predict", "compare", "score",
                                                "promote"]
    assert c["feed"] and {f["key"] for f in c["feed"][0]["factors"]} >= {"뉴스", "추세", "시장", "거래량"}
    assert any(j["job"] == "price_watch" for j in c["jobs"])
    assert "summary" in api.scorecard("KR") and api.alerts(0)["last_id"] >= 1
    assert "quotes" in api.quotes() and api.ladder()["conditions"]


def test_live_quote_parsing_handles_unsigned_falling_rows():
    from quant_ai.data.live_quotes import _naver_row, fetch_quotes
    r = _naver_row({"closePrice": "71,000", "fluctuationsRatio": "1.25", "compareToPreviousClosePrice": "900",
                    "compareToPreviousPrice": {"name": "FALLING"}})
    assert r["price"] == 71000 and r["chg_pct"] == pytest.approx(-0.0125) and r["chg"] == -900
    got = fetch_quotes(["005930", "NVDA"], {"kr": lambda c: {"005930": {"price": 1.0}}, "us": lambda s: {"NVDA": {"price": 2.0}}})
    assert set(got) == {"005930", "NVDA"} and all("ts" in v for v in got.values())


def test_scheduler_has_24h_observation_jobs(app):
    from quant_ai.config import Mode
    from quant_ai.scheduler import build_default_scheduler
    names = {j.name for j in build_default_scheduler(app, Mode.PAPER).jobs}
    assert {"price_watch", "alert_scan", "market_pulse", "event_reanalyze", "ladder", "community"} <= names

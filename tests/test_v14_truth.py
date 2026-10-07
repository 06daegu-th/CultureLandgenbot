"""완성 기준 체크리스트: 시장 시계 · 1차/2차 소스 · fail-closed · 감시견 · Truth Center · 왜 BUY/SELL/NO TRADE ·
purged walk-forward · 도전자 모델 · 체크리스트 자체 검증."""

import glob
import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Mode, Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("truth")
    fake_marcap(d, n_codes=6, days=300)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a",
                        max_data_age_days=0, mode=Mode.SHADOW))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def utc(*a):
    return datetime(*a, tzinfo=UTC)


# ------------------------------------------------------------------ MARKET: 시장 시계
def test_market_clock_next_open_close():
    from quant_ai.clock import KRX, US, clock_status, next_close, next_open
    now = utc(2026, 9, 30, 5, 0)  # 수 14:00 KST · 01:00 EDT
    assert next_close(KRX, now) == utc(2026, 9, 30, 6, 30)  # 오늘 15:30 KST
    assert next_open(KRX, now) == utc(2026, 10, 1, 0, 0)  # 장중 → 다음 거래일 09:00
    assert next_open(US, now) == utc(2026, 9, 30, 13, 30)  # 09:30 EDT
    assert next_close(US, now) == utc(2026, 9, 30, 20, 0)
    # 한글날(금) 전날 장 마감 뒤 → 다음 개장은 월요일
    assert next_open(KRX, utc(2026, 10, 8, 7, 0)) == utc(2026, 10, 12, 0, 0)
    # 서머타임 끝난 뒤 미국 개장은 14:30 UTC
    assert next_open(US, utc(2026, 11, 3, 12, 0)) == utc(2026, 11, 3, 14, 30)
    # 추수감사절 다음 날 조기 폐장 13:00 EST
    assert next_close(US, utc(2026, 11, 27, 15, 0)) == utc(2026, 11, 27, 18, 0)

    cs = clock_status(now)
    k, u = cs["markets"]["KRX"], cs["markets"]["US"]
    assert cs["calendar_ok"] and cs["now_kst"].endswith("KST")
    assert k["phase"] == "open" and k["next_event"] == "폐장" and k["seconds_to_next"] == 90 * 60
    assert k["next_close_kst"] == "09-30 15:30 KST" and k["tz"] == "Asia/Seoul"
    assert u["phase"] != "open" and u["next_event"] == "개장" and u["tz"] == "America/New_York"
    hol = clock_status(utc(2026, 10, 9, 3, 0))["markets"]["KRX"]
    assert hol["trading_day"] is False and "한글날" in hol["holiday"] and hol["session"] is None


# ------------------------------------------------------------------ DATA: 1차/2차 소스
def _frame(dates, close):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1000.0},
                        index=pd.DatetimeIndex(dates, tz="UTC"))


def test_secondary_source_fills_only_gap_days(tmp_path):
    from quant_ai.data.sources import align, cross_check
    days = pd.bdate_range("2026-09-01", periods=13, tz="UTC")
    prim = _frame(days[:10], np.linspace(100, 110, 10))
    sec = _frame(days[5:], np.r_[np.linspace(100, 110, 10)[5:], [112, 113, 114]] * 0.5)  # 수정주가 기준이 절반
    new, info = align(prim, sec)
    assert list(new.index) == list(days[10:])  # 1차 마지막 날 이후만
    assert info["ratio"] == pytest.approx(2.0) and info["reason"] == "채움"
    assert new["close"].tolist() == pytest.approx([112, 113, 114])  # 1차 기준으로 맞춤
    # 겹치는 날 비율이 흔들리면(분할 의심) 채우지 않는다
    bad = sec.copy()
    bad.loc[days[7], "close"] *= 1.2
    assert align(prim, bad)[0].empty
    # 겹치는 날이 없으면 기준을 못 맞추니 채우지 않는다
    assert align(prim, sec.loc[days[10]:])[0].empty
    assert cross_check({"A": ("2026-09-01", 100.0)}, {"A": ("2026-09-01", 103.0)}) == [
        {"symbol": "A", "date": "2026-09-01", "primary": 100.0, "secondary": 103.0, "gap": 0.03}]


def test_gap_fill_writes_secondary_then_primary_overwrites(tmp_path):
    from quant_ai.config import Settings
    from quant_ai.data.db import load_bars, session_scope, upsert_bars
    from quant_ai.data.models import PriceBar
    from quant_ai.data.sources import SECONDARY, gap_fill
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    fake_marcap(tmp_path, n_codes=3, days=120)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/g.db", artifacts_dir=tmp_path / "a",
                        max_data_age_days=0))
    a.ingest_krx(tmp_path, years=0, top_n=3, end_year=2020)
    syms = [s for s in a.market_data()[0] if s[:1].isdigit()]
    with session_scope(a.engine) as s:
        before = load_bars(s, syms)

    def fetch(sym):
        b = before[sym]
        extra = pd.bdate_range(b.index.max() + pd.Timedelta(days=1), periods=3, tz="UTC")
        tail = b["close"].iloc[-5:].tolist() + [b["close"].iloc[-1]] * 3
        return _frame(list(b.index[-5:]) + list(extra), np.array(tail) * 0.5)

    out = gap_fill(a, syms, fetch=fetch)
    assert len(out["filled"]) == len(syms) and ops.get_state(a.engine, "source_failover")["filled"]
    with session_scope(a.engine) as s:
        n_sec = s.query(PriceBar).filter(PriceBar.source == SECONDARY).count()
        after = load_bars(s, syms)
    assert n_sec == 3 * len(syms)
    sym = syms[0]
    assert len(after[sym]) == len(before[sym]) + 3
    assert after[sym]["close"].iloc[-1] == pytest.approx(before[sym]["close"].iloc[-1])  # 1차 기준 가격
    # 1차가 나중에 같은 날을 갱신하면 1차 값·출처로 돌아온다
    with session_scope(a.engine) as s:
        upsert_bars(s, sym, after[sym].iloc[-3:] * 1.0, "1d", "krx-marcap")
        assert s.query(PriceBar).filter(PriceBar.source == SECONDARY, PriceBar.symbol == sym).count() == 0


# ------------------------------------------------------------------ SAFETY: fail-closed
def test_fail_closed_when_readiness_or_calendar_missing(app, monkeypatch):
    from quant_ai import clock, readiness, truth
    # 1) 기록 없음 → 지금 점검 → 2020년 데이터라 NOT READY
    ops.set_state(app.engine, "readiness", {})
    assert readiness.buy_block(app, "shadow").startswith("NOT READY")
    assert readiness.buy_block(app, "paper") is None  # 기본 게이트: live·shadow 장부만
    # 2) 다른 장부 기준의 READY 기록은 믿지 않는다 → 다시 점검
    ops.set_state(app.engine, "readiness", {"status": "READY", "mode": "paper", "at": datetime.now(UTC).isoformat()})
    assert readiness.buy_block(app, "shadow").startswith("NOT READY")
    # 오래된 READY 도 믿지 않는다
    ops.set_state(app.engine, "readiness", {"status": "READY", "mode": "shadow", "at": (datetime.now(UTC) - timedelta(hours=3)).isoformat()})
    assert readiness.buy_block(app, "shadow").startswith("NOT READY")
    # 방금 · 같은 장부 기준 READY 면 그대로 쓴다 (점검을 매 주문마다 반복하지 않음)
    ops.set_state(app.engine, "readiness", {"status": "READY", "mode": "shadow", "at": datetime.now(UTC).isoformat()})
    assert readiness.buy_block(app, "shadow") is None
    # 3) 점검 자체가 실패하면 매수 중단
    ops.set_state(app.engine, "readiness", {})

    def boom(*a, **k):
        raise RuntimeError("db down")

    with monkeypatch.context() as m:
        m.setattr(readiness, "evaluate", boom)
        assert "fail-closed" in readiness.buy_block(app, "shadow")
    # 4) Truth Center 가 실패해도 DATA 관문 빨강
    with monkeypatch.context() as m:
        m.setattr(truth, "report", boom)
        r = readiness.evaluate(app, "paper", act=False)
        assert next(c for c in r["checks"] if c["key"] == "DATA")["detail"].endswith("fail-closed")
    # 5) 휴장일 캘린더가 없으면 EVENT 관문 빨강 (주말만 쉬는 것으로 계산 → 휴장일 주문 위험)
    with monkeypatch.context() as m:
        m.setattr(clock, "HOLIDAY_NAMES", {"KRX": {}, "US": {}})
        mc = truth.market_clock(datetime.now(UTC))
        assert mc["status"] == "bad" and mc["checks"][0]["key"] == "calendar_loaded"
        r = readiness.evaluate(app, "paper", act=False)
        ev = next(c for c in r["checks"] if c["key"] == "EVENT")
        assert ev["status"] == "red" and any("휴장일 캘린더" in x for x in r["truth_bad"])
    # 6) 이벤트 캘린더 기록이 없으면 EVENT 빨강
    prev = ops.get_state(app.engine, "event_calendar")
    ops.set_state(app.engine, "event_calendar", {})
    try:
        r = readiness.evaluate(app, "paper", act=False)
        assert next(c for c in r["checks"] if c["key"] == "EVENT")["status"] == "red"
    finally:
        ops.set_state(app.engine, "event_calendar", prev)


# ------------------------------------------------------------------ OPERATIONS: 감시견
def test_watchdog_decisions(app):
    from quant_ai import watchdog as w
    assert w.decide(False, 0, 10, None) == "restart_dead"
    assert w.decide(True, 0, 60, None) == "ok"  # 막 띄움 → 첫 심장박동 대기
    assert w.decide(True, 0, 600, None) == "restart_hung"
    assert w.decide(True, 0, 600, 30) == "ok"
    assert w.decide(True, 0, 600, w.HEARTBEAT_STALE_S + 1) == "restart_hung"
    assert [w.backoff(n) for n in (0, 1, 2, 3)] == [10, 10, 20, 40] and w.backoff(20) == 600
    now = datetime.now(UTC)
    ops.set_state(app.engine, "heartbeat", {"at": (now - timedelta(seconds=90)).isoformat()})
    assert w.heartbeat_age(app.engine, now) == pytest.approx(90)
    w.record(app.engine, "start", "pid 1")
    w.record(app.engine, "restart_hung", "심장박동 400초 멈춤")
    st = ops.get_state(app.engine, "watchdog")
    assert st["restarts"] == 1 and st["last"]["event"] == "restart_hung" and len(st["history"]) == 2
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import AlertRecord
    with session_scope(app.engine) as s:
        assert s.query(AlertRecord).filter(AlertRecord.title.like("감시견%")).count() == 1


# ------------------------------------------------------------------ Truth Center
def test_truth_center_sections(app, monkeypatch):
    from quant_ai import desk, readiness, truth
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import OrderRecord
    desk.event_calendar(app)
    rep = truth.report(app, "paper")
    assert [s["key"] for s in rep["sections"]] == ["clock", "data", "event", "broker", "risk", "execution"]
    assert rep["as_of"].endswith("KST") and ops.get_state(app.engine, "truth")["status"] == rep["status"]
    data = next(s for s in rep["sections"] if s["key"] == "data")
    keys = {c["key"]: c for c in data["checks"]}
    assert keys["fresh_bar_kr"]["status"] == "bad"  # 2020년 데이터
    assert {"secondary", "cross_check", "quality", "corporate_actions", "pit"} <= set(keys)
    assert keys["pit"]["status"] == "ok"  # 월별 유니버스 기록
    assert any("Data Truth" in b for b in rep["bad"])
    # 수정되지 않은 분할(−50%) 탐지
    bars, bench, sent = app.market_data()
    sym = next(s for s in bars if s[:1].isdigit())
    b = bars[sym].copy()
    b.iloc[-10:, b.columns.get_loc("close")] *= 0.5
    monkeypatch.setattr(app, "market_data", lambda *a, **k: ({**bars, sym: b}, bench, sent))
    ca = truth.corporate_action_scan(app)
    assert ca and ca[0]["symbol"] == sym and ca[0]["ret"] == pytest.approx(-0.5, abs=0.05)
    monkeypatch.undo()
    # 초과 체결 · 상태 불명 주문 → Execution/Broker bad → BROKER 관문 빨강
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        s.add(OrderRecord(mode="paper", created_at=now, symbol=sym, side="buy", qty=10, order_type="limit", status="filled",
                          client_order_id="t:over#1", filled_qty=12, ref_price=100, avg_price=100))
        s.add(OrderRecord(mode="paper", created_at=now, symbol=sym, side="buy", qty=5, order_type="limit", status="unknown",
                          client_order_id="t:unk#1"))
    try:
        rep = truth.report(app, "paper")
        ex = {c["key"]: c for c in next(s for s in rep["sections"] if s["key"] == "execution")["checks"]}
        br = {c["key"]: c for c in next(s for s in rep["sections"] if s["key"] == "broker")["checks"]}
        assert ex["overfill"]["status"] == "bad" and ex["idempotency"]["status"] == "ok"
        assert br["unknown"]["status"] == "bad" and br["broker"]["status"] == "na"
        r = readiness.evaluate(app, "paper", act=False)
        assert next(c for c in r["checks"] if c["key"] == "BROKER")["status"] == "red"
    finally:
        with session_scope(app.engine) as s:
            s.query(OrderRecord).filter(OrderRecord.client_order_id.like("t:%")).delete(synchronize_session=False)


# ------------------------------------------------------------------ UX: 왜 BUY · 왜 SELL · 왜 NO TRADE
PAYLOAD = {"contributions": [
    {"analyst": "quant", "prob_up": 0.70, "weight": 0.4, "summary": "모멘텀 상위", "accuracy": 0.56, "n_scored": 120},
    {"analyst": "primary", "prob_up": 0.60, "weight": 0.2, "summary": "호재 뉴스"},
    {"analyst": "nvidia", "prob_up": 0.40, "weight": 0.3, "summary": "금리 부담"},
    {"analyst": "risk", "prob_up": None, "weight": 0.1}],
    "plan": {"stop": 9500.0, "stop_pct": -0.05}, "reasons": ["20일 신고가"], "risks": ["실적 D-3"]}


def test_explain_why_actions():
    from quant_ai.explain import explain
    b = explain(PAYLOAD, "BUY", 0.64, 61)
    assert b["headline"].startswith("BUY") and "58%" in b["headline"] and "55" in b["headline"]
    assert [x["ai"] for x in b["for"]] == ["차트·Quant AI", "뉴스 AI"]  # 영향 큰 순 (0.4×0.2 > 0.2×0.1)
    assert b["for"][0]["track"] == "과거 적중 56% (n=120)"
    assert [x["ai"] for x in b["against"]] == ["경제·시장 AI"]
    assert any(x.startswith("SELL 이 되려면") and "22.0%p" in x for x in b["what_changes"])
    assert any("9,500.00" in x for x in b["what_changes"]) and not any(x.startswith("BUY") for x in b["what_changes"])

    s = explain(PAYLOAD, "SELL", 0.35, 60)
    assert s["headline"].startswith("SELL") and "42%" in s["headline"]
    assert [x["ai"] for x in s["for"]] == ["경제·시장 AI"]  # 하락 쪽이 찬성
    assert any(x.startswith("BUY 가 되려면: 상승 확률 +23.0%p") for x in s["what_changes"])

    h = explain(PAYLOAD, "HOLD", 0.52, 50)
    assert h["headline"].startswith("HOLD") and "사이" in h["headline"]
    assert any("+6.0%p" in x and "신뢰도 +5" in x for x in h["what_changes"])

    nt = explain(PAYLOAD | {"no_trade_codes": ["veto", "high_conflict"], "vetoes": ["유동성 부족"], "conflict": "high"},
                 "NO_TRADE", 0.61, 58, gates={"readiness": "NOT READY (DATA)", "event": "실적 D-1 → 매수 ×0.5"})
    assert nt["headline"] == "NO TRADE — Risk AI 거부권"
    assert nt["blocks"] == ["Risk AI 거부권", "AI 간 의견 충돌 높음", "거부권 — 유동성 부족", "매매 준비: NOT READY (DATA)",
                            "이벤트: 실적 D-1 → 매수 ×0.5"]
    assert any("거부권 해제" in x and "의견 충돌 완화" in x for x in nt["what_changes"])


def test_explain_for_symbol_uses_sealed_record(app):
    from quant_ai.explain import for_symbol
    assert for_symbol(app, "NOPE") is None
    app.decide(persist=True)
    sym = next(s for s in app.market_data()[0] if s[:1].isdigit())
    e = for_symbol(app, sym)
    assert e and e["action"] in ("BUY", "SELL", "HOLD", "NO_TRADE") and e["headline"].split()[0] in ("BUY", "SELL", "HOLD", "NO")
    assert e["as_of"].endswith(("KST", "일봉")) and e["sealed"] and e["thresholds"]["buy_prob"] == 0.58


# ------------------------------------------------------------------ MODEL: purged walk-forward · challenger
def test_purged_walk_forward_never_trains_on_test_labels(monkeypatch):
    from quant_ai.backtest import backtester as bt
    from quant_ai.data.collectors.prices import SyntheticPriceSource
    fits, preds = [], []

    class Spy(bt.Predictor):
        def fit(self, X, y):
            fits.append(X.index.get_level_values(0).max())
            return super().fit(X, y)

        def predict_proba(self, X):
            preds.append((X.index.get_level_values(0).max(), fits[-1]))
            return super().predict_proba(X)

    monkeypatch.setattr(bt, "Predictor", Spy)
    src = SyntheticPriceSource(seed=3)
    bars = {s: src.fetch_bars(s, "2019-01-01", "2020-12-31") for s in ("A", "B")}
    cfg = bt.BacktestConfig(min_train_dates=150, retrain_every=30)
    bt.Backtester(cfg).run(bars)
    dates = sorted(next(iter(bars.values())).index)
    pos = {d: i for i, d in enumerate(dates)}
    assert len(fits) >= 3 and preds
    # 학습 표본의 마지막 행(라벨 = +horizon 종가)과 예측일 사이에 horizon + embargo 이상 간격
    gaps = [pos[t] - pos[m] for t, m in preds]
    assert min(gaps) >= cfg.horizon + cfg.embargo


def test_challenger_scored_silently_and_promoted_only_through_gate(tmp_path):
    from quant_ai.config import Settings
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import AnalystOpinionRecord, ModelRecord
    from quant_ai.pipeline import QuantAI
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/c.db", artifacts_dir=tmp_path / "a"))
    now = datetime.now(UTC)
    with session_scope(a.engine) as s:
        s.add(ModelRecord(name="q", version="champ", created_at=now - timedelta(days=90), status="champion", artifact_path="x"))
        for v in ("good", "weak", "young"):
            s.add(ModelRecord(name="q", version=v, created_at=now - timedelta(days=40), status="shadow", artifact_path=v))
        for v, days, hit in (("good", 25, 0.6), ("weak", 25, 0.4), ("young", 8, 0.7)):
            for d in range(days):
                for k in range(10):
                    s.add(AnalystOpinionRecord(consensus_id=None, analyst="challenger", symbol=f"{k:06d}",
                                               as_of=now - timedelta(days=d + 1), horizon_bars=5, category="direction",
                                               prob_up=0.6, confidence=0.5, veto=False, correct=k < hit * 10,
                                               realized_return=0.0, payload={"model_version": v}))
    res = {v: r for v, (_, r) in zip(("good", "weak", "young"), a.evaluate_shadow_models())}
    with session_scope(a.engine) as s:
        st = {r.version: r.status for r in s.query(ModelRecord)}
    assert st == {"champ": "retired", "good": "champion", "weak": "rejected", "young": "shadow"}
    assert res["good"].passed and not res["weak"].passed and any("기간" in f for f in res["young"].failures)


# ------------------------------------------------------------------ 체크리스트 자체
def test_checklist_references_real_tests_and_doc_is_current(app):
    from quant_ai.checklist import ITEMS, evaluate, markdown
    root = Path(__file__).parent
    src = "".join(Path(f).read_text(encoding="utf-8") for f in glob.glob(str(root / "*.py")))
    files = {Path(f).stem for f in glob.glob(str(root / "*.py"))}
    missing = [t for *_, t in ITEMS if not t.startswith("e2e") and f"def {t}(" not in src and t not in files]
    assert not missing, missing
    areas = {a for a, *_ in ITEMS}
    assert areas == {"DATA", "MARKET", "EVENT", "AI", "MODEL", "PORTFOLIO", "TRADING", "SAFETY", "OPERATIONS", "UX"}
    ev = evaluate(app)
    assert ev["error"] is None and ev["total"]["n"] == len(ITEMS)
    by = {r["item"]: r for r in ev["items"]}
    assert by["next open"]["status"] == "live" and "KST" in by["next open"]["detail"]
    assert by["KIS"]["status"] == "setup"  # 증권사 미설정
    assert by["stale detection"]["status"] == "bad"  # 2020년 데이터 → 정직하게 문제로 표시
    doc = root.parent / "docs" / "FINAL_CHECKLIST.md"
    assert doc.read_text(encoding="utf-8") == markdown(), "python -m quant_ai.checklist > docs/FINAL_CHECKLIST.md 로 갱신"


def test_truth_clock_explain_checklist_routes(app):
    from http.server import ThreadingHTTPServer

    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), None, {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def get(path):
        c = HTTPConnection("127.0.0.1", port, timeout=60)
        c.request("GET", path)
        r = c.getresponse()
        return r.status, json.loads(r.read())

    try:
        st, clock = get("/api/clock")
        assert st == 200 and set(clock["markets"]) == {"KRX", "US"}
        st, tr = get("/api/truth?refresh=1")
        assert st == 200 and len(tr["sections"]) == 6
        st, cl = get("/api/checklist")
        assert st == 200 and cl["total"]["n"] > 80
        st, ex = get("/api/explain?symbol=NOPE")
        assert st == 200 and ex["error"]
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ UX: 관심종목 · 내 보유 · 적중률 · 오늘 할 일 · 비교
def test_star_holdings_track_today_compare(app):
    from quant_ai import accounts, ux
    from quant_ai.actions import add_watch, watch_symbols
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    syms = [s for s in app.market_data()[0] if s[:1].isdigit()]
    a, b = syms[0], syms[1]
    with pytest.raises(ValueError):
        ux.set_star(app, "<script>", True)
    assert ux.set_star(app, a, True) == [a]
    add_watch(app, b)
    assert watch_symbols(app)[:2] == [a, b]  # 별표가 먼저 · 최근 본 종목은 별도로 20개 유지
    assert ops.get_state(app.engine, "watch_symbols")["symbols"][-1] == b and a not in ops.get_state(app.engine, "watch_symbols")["symbols"]
    # 내 보유: 사용자 계좌 입력 → 종목 페이지에 보인다
    accounts.upsert(app.engine, {"name": "내 ISA", "type": "isa", "holdings": [{"symbol": a, "qty": 10, "avg_price": 1000}]})
    h = ux.holdings(app, a)
    row = next(r for r in h["rows"] if r["book"] == "내 ISA")
    assert row["qty"] == 10 and h["starred"] and row["pnl_pct"] == pytest.approx(h["last"] / 1000 - 1, abs=1e-4)
    # 종목별 과거 AI 적중률 (채점된 것만) + 기준(항상 상승) 비교
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        for i in range(40):
            s.add(ConsensusRecord(symbol=b, as_of=now - timedelta(days=60 + i), action="BUY" if i % 2 else "HOLD", prob_up=0.6,
                                  confidence=60, conflict="low", payload={}, correct=i % 4 != 0,
                                  realized_return=0.01 if i % 4 != 0 else -0.02))
    t = ux.track(app, b)
    assert t["n_scored"] == 40 and t["hit"] == 0.75 and t["baseline_up"] == 0.75 and "차이 없음" in t["verdict"]
    assert set(t["by_action"]) == {"BUY", "HOLD"} and len(t["recent"]) == 10
    # 오늘 할 일: 안전·데이터 문제가 맨 위 (2020년 데이터 → NOT READY)
    from quant_ai import readiness
    readiness.evaluate(app, "shadow")
    td = ux.today(app)
    assert td["todo"][0]["level"] == "bad" and any("NOT READY" in x["title"] for x in td["todo"])
    assert set(td["glance"]["markets"]) == {"KRX", "US"} and "kospi" in td["glance"] and a in td["starred"]
    # 비교
    c = ux.compare(app, [a, b, "NOPE"])
    assert c["symbols"] == [a, b] and c["missing"] == ["NOPE"] and c["corr"][a][a] == pytest.approx(1.0)
    r = {x["symbol"]: x for x in c["rows"]}
    assert r[b]["hit"] == 0.75 and r[a]["vol"] > 0 and r[a]["beta"] is not None and c["series"][a][0][1] == 100.0
    assert ux.compare(app, [a])["error"]

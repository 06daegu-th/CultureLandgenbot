"""v30: AI 자동매매(오토파일럿) · 목표 현실성 · 회사 이해 화면(DART 재무) · 로고 큐 · 지표(/metrics) · 화면 손질."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v30")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def _row(sym, score, last=10000.0, name=None, stop=None):
    return {"symbol": sym, "name": name or f"종목{sym}", "score": score, "last": last, "stop": stop or last * 0.95,
            "signals": [{"label": "추세", "text": "오름 추세", "verified": True, "score": 1.0}], "evidence": {"n": 100, "hit": 0.55}}


# ------------------------------------------------------------------ 1. 오늘의 결정 (순수 계산)
def test_plan_sells_buys_skips_and_replaces():
    from quant_ai.autopilot import DEFAULTS, plan
    rows = {"A": _row("A", 2.5), "B": _row("B", 2.0, last=500000), "C": _row("C", 1.2), "D": _row("D", -0.3), "E": _row("E", 0.9, last=9000),
            "F": _row("F", 0.6)}
    full = {"_rows": rows, "as_of": "2026-10-02", "regime": {"above_200": True, "text": "위"}, "calibration": {"tier": {"key": "warn"}}}
    hold = {"D": {"qty": 3, "held_days": 2, "stop": 1000},              # 점수 하락 → 팔기
            "E": {"qty": 3, "held_days": 25, "stop": 100},              # 20거래일 지남 · 점수 0.9 ≥ 0.8 → 계속 보유
            "F": {"qty": 3, "held_days": 25, "stop": 100},              # 20거래일 지남 · 점수 0.6 < 0.8 → 팔기
            "G": {"qty": 3, "held_days": 1, "stop": 100}}               # 계산 대상에서 빠짐 → 팔기
    p = plan(full, hold, 100000, 1_000_000, DEFAULTS, "2026-10-05")
    act = {a["symbol"]: a["action"] for a in p["actions"]}
    assert act["D"] == "sell" and act["F"] == "sell" and act["G"] == "sell" and act["E"] == "hold"
    assert act["A"] == "buy" and act["C"] == "buy" and "B" not in act  # B: 1주 50만원 > 상한 18만원
    assert p["skipped"][0]["symbol"] == "B" and p["weight"] == 0.18 and p["slots"] == 5
    assert set(p["targets"]) == {"A", "C", "E"} and all(w == 0.18 for w in p["targets"].values())
    # 손절선
    p2 = plan(full, {"A": {"qty": 1, "held_days": 1, "stop": 11000}}, 0, 1_000_000, DEFAULTS, "x")
    assert next(a for a in p2["actions"] if a["symbol"] == "A")["action"] == "sell"
    # 시장이 200일선 아래: 자리 절반 · 매수 기준 +0.5
    down = {**full, "regime": {"above_200": False}}
    p3 = plan(down, {}, 1_000_000, 1_000_000, DEFAULTS, "x")
    assert p3["slots"] == 3 and p3["buy_min"] == 1.3 and {a["symbol"] for a in p3["actions"]} == {"A"}
    # 자리가 꽉 찼는데 훨씬 좋은 후보 → 가장 약한 것 하나 교체
    cfg = {**DEFAULTS, "max_positions": 2}
    full2 = {**full, "_rows": {"A": _row("A", 2.9), "X": _row("X", 1.0), "Y": _row("Y", 1.5)}}
    p4 = plan(full2, {"X": {"qty": 1, "held_days": 1}, "Y": {"qty": 1, "held_days": 1}}, 0, 1_000_000, cfg, "x")
    act4 = {a["symbol"]: a["action"] for a in p4["actions"]}
    assert act4 == {"X": "sell", "A": "buy", "Y": "hold"}


def test_goal_odds_from_daily_returns_is_honest():
    from quant_ai.autopilot import goal_odds
    rng = np.random.default_rng(0)
    bt = {"daily": list(rng.normal(0.0004, 0.015, 140)), "from": "a", "to": "b", "days": 140, "total": 0.05, "market_total": 0.1}
    g = goal_odds(None, bt=bt, sims=500)
    h6, h12 = g["horizons"]
    assert h6["months"] == 6 and h12["months"] == 12 and h12["p_target"] == 0.0 and 0 < h12["p_gain"] < 1
    assert abs(h12["need_monthly"] - (100 ** (1 / 12) - 1)) < 1e-9 and h12["need_annual"] == pytest.approx(99.0)
    assert "한 번도" in g["verdict"] and len(g["advice"]) == 3
    assert goal_odds(None, bt={"error": "x"})["error"] == "x"


def test_backtest_on_holdout_with_costs(app):
    from quant_ai.autopilot import backtest
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2023-01-02", periods=520, tz="UTC")
    bars = {}
    for i in range(30):
        c = 50000 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, len(idx))))
        bars[f"{i + 1:05d}0"] = pd.DataFrame({"close": c, "volume": 1e6}, index=idx)
    bt = backtest(app, bars=bars)
    assert bt["days"] == len(idx) - 1 - (len(idx) - 120 - 20) and bt["rebalances"] >= 1 and "보지 않은 기간" in bt["note"]
    assert len(bt["daily"]) == len(bt["market"]) and np.isfinite(bt["total"])
    assert backtest(app, bars={k: v.iloc[:100] for k, v in bars.items()})["error"]


# ------------------------------------------------------------------ 2. 실행 · 봉인 기록 · 관문
def test_run_trades_paper_book_and_seals_log(app):
    from quant_ai import autopilot as AP
    bars, _, _ = app.market_data()
    syms = [s for s in bars if s[:1].isdigit()][:3]
    rows = {s: _row(s, 2.0 - i * 0.3, last=float(bars[s]["close"].iloc[-1])) for i, s in enumerate(syms)}
    full = {"_rows": rows, "as_of": str(pd.Timestamp(bars[syms[0]].index[-1]).date()), "regime": {"above_200": True}, "calibration": {}}
    r = AP.run(app, now=datetime(2026, 10, 5, 2, tzinfo=UTC), force=True, full=full, allow_stale=True)
    pl = r["books"][AP.BOOK]
    assert not pl.get("error") and {f["symbol"] for f in pl["fills"]} == set(syms) and all(f["side"] == "buy" for f in pl["fills"])
    pf = app.load_portfolio(AP.BOOK)
    assert set(s for s, p in pf.positions.items() if p.qty) == set(syms) and pf.cash < AP.DEFAULTS["principal"]
    meta = ops.get_state(app.engine, AP.STATE)["meta"][AP.BOOK]
    assert all(meta[s]["entry_date"] and meta[s]["stop"] for s in syms)
    # 다음 날: 첫 종목 점수 하락 → 팔기 · 기록은 앞 해시에 이어짐
    rows[syms[0]] = {**rows[syms[0]], "score": -1.0}
    r2 = AP.run(app, now=datetime(2026, 10, 6, 2, tzinfo=UTC), force=True, full=full, allow_stale=True)
    assert any(f["symbol"] == syms[0] and f["side"] == "sell" for f in r2["books"][AP.BOOK]["fills"])
    log = ops.get_state(app.engine, AP.LOG)[AP.BOOK]
    assert len(log) == 2 and log[1]["prev"] == log[0]["hash"] and len(log[1]["hash"]) == 64
    assert syms[0] not in ops.get_state(app.engine, AP.STATE)["meta"][AP.BOOK]
    # 시간 밖 · 하루 한 번 · 일봉이 밀렸으면 쉰다
    assert "장중" in AP.run(app, now=datetime(2026, 10, 5, 12, tzinfo=UTC), full=full)["skipped"]
    stale = AP.run(app, now=datetime(2026, 10, 7, 1, 30, tzinfo=UTC), full={**full, "as_of": "2026-09-01"})
    assert "밀림" in stale["skipped"]


def test_gates_block_live_until_all_pass(app):
    from quant_ai import autopilot as AP
    g = {x["key"]: x for x in AP.gates(app)}
    assert set(g) == {"forward", "paper", "kis", "proof", "user", "smallcap"} and not AP.live_enabled(app)
    assert not g["user"]["ok"]
    AP.set_config(app, {"live_requested": True})
    assert {x["key"]: x for x in AP.gates(app)}["user"]["ok"] and not AP.live_enabled(app)  # 다른 관문이 막고 있음
    AP.set_config(app, {"live_requested": False, "paper_on": False})
    assert AP.config(app)["paper_on"] is False
    AP.set_config(app, {"paper_on": True})
    sch = (ROOT / "src/quant_ai/scheduler.py").read_text()
    assert 'sch.add("autopilot"' in sch and "live_enabled(app)" in sch  # 실제 계좌를 맡으면 코어 전략은 같은 계좌에 주문 안 함


# ------------------------------------------------------------------ 3. 회사 이해 (DART 재무)
DART_PAYLOAD = {"status": "000", "list": [
    {"fs_div": "OFS", "account_nm": "매출액", "bsns_year": "2025", "thstrm_amount": "1", "frmtrm_amount": "1", "bfefrmtrm_amount": "1"},
    {"fs_div": "CFS", "account_nm": "매출액", "bsns_year": "2025", "thstrm_amount": "300,000", "frmtrm_amount": "250,000", "bfefrmtrm_amount": "200,000"},
    {"fs_div": "CFS", "account_nm": "영업이익", "bsns_year": "2025", "thstrm_amount": "30,000", "frmtrm_amount": "-5,000", "bfefrmtrm_amount": "20,000"},
    {"fs_div": "CFS", "account_nm": "당기순이익", "bsns_year": "2025", "thstrm_amount": "20,000", "frmtrm_amount": "-8,000", "bfefrmtrm_amount": "15,000"},
    {"fs_div": "CFS", "account_nm": "부채총계", "bsns_year": "2025", "thstrm_amount": "100,000", "frmtrm_amount": "90,000", "bfefrmtrm_amount": "-"},
    {"fs_div": "CFS", "account_nm": "자본총계", "bsns_year": "2025", "thstrm_amount": "200,000", "frmtrm_amount": "180,000", "bfefrmtrm_amount": "-"}]}


def test_parse_fnltt_prefers_consolidated_and_three_years():
    from quant_ai.company import parse_fnltt
    by = parse_fnltt(DART_PAYLOAD)
    assert sorted(by) == [2023, 2024, 2025] and by[2025]["revenue"] == 300000 and by[2024]["op_income"] == -5000
    assert by[2025]["liabilities"] == 100000 and "liabilities" not in by[2023]
    assert parse_fnltt({"list": []}) == {}


def test_financials_with_fake_dart_and_view(app):
    from quant_ai import company
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Disclosure
    sym = sorted(s for s in app.symbols() if s[:1].isdigit())[0]
    with session_scope(app.engine) as s:
        s.add(Disclosure(source="DART", receipt_no="v30-1", corp_code="00126380", symbol=sym, title="주요사항보고서(자기주식취득결정)",
                         filed_at=date.today(), url="u"))
    assert company.corp_code(app, sym) == "00126380"
    saved = app.settings
    app.settings = replace(saved, dart_api_key=None)
    try:
        assert "DART 키" in company.financials(app, sym, refresh=True)["error"]
        app.settings = replace(saved, dart_api_key="k" * 40)
        calls = []

        def get(url):
            calls.append(url)
            return json.dumps(DART_PAYLOAD).encode()
        f = company.financials(app, sym, get=get, refresh=True, now=datetime(2026, 10, 5, tzinfo=UTC))
        assert [r["year"] for r in f["rows"]] == [2023, 2024, 2025] and len(calls) == 2 and "corp_code=00126380" in calls[0]
        last = f["rows"][-1]
        assert last["op_margin"] == pytest.approx(0.1) and last["debt_ratio"] == pytest.approx(0.5)
        assert company.financials(app, sym, get=lambda u: (_ for _ in ()).throw(AssertionError("캐시여야 함")))["rows"]  # 7일 캐시
    finally:
        app.settings = saved
    v = company.view(app, sym)
    assert v["financials"]["rows"] and v["valuation"]["market_cap"] and v["easy3"]["good"] and v["easy3"]["next"]
    assert any(t["kind"] == "disclosure" for t in v["timeline"])
    assert any("영업이익 흑자" in x for x in v["easy3"]["good"]) and any("매출" in x for x in v["easy3"]["good"])
    assert v["valuation"]["per"] and "계산" in v["valuation"]["per_src"]  # 시가총액 ÷ 순이익


def test_easy3_never_invents():
    from quant_ai.company import easy3
    e = easy3({"rows": []}, {}, None, None, [], [])
    assert "없어요" in e["good"][0] and "없어요" in e["bad"][0] and "없어요" in e["next"][0]
    e2 = easy3({"rows": [{"year": 2024, "revenue": 100, "op_income": 5, "debt_ratio": 3.0}, {"year": 2025, "revenue": 80, "op_income": -2, "debt_ratio": 3.0}]},
               {"per": 55, "div_yield": 0.04}, None, -0.4, [{"label": "실적 발표", "date": "2026-10-20", "d_day": 14}], [])
    assert any("줄었" in x for x in e2["bad"]) and any("적자" in x for x in e2["bad"]) and "D-14" in e2["next"][0]


# ------------------------------------------------------------------ 4. 로고 큐 · 지표
def test_logo_coverage_and_metrics(app):
    from quant_ai import logos, metrics
    d = logos._dir(app)
    syms = sorted(s for s in app.symbols() if s[:1].isdigit())[:3]
    (d / "custom" / f"{syms[0]}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 300)
    (d / f"{syms[1]}.json").write_text(json.dumps({"failed_at": 1, "tried": ["toss: URLError"]}))
    cov = logos.coverage(app, syms, {s: f"이름{s}" for s in syms})
    st = {r["symbol"]: r["status"] for r in cov["missing"]}
    assert cov["total"] == 3 and cov["real"] == 1 and st == {syms[1]: "failed", syms[2]: "none"}
    assert cov["missing"][0]["why"] == "toss: URLError" and cov["by_source"]["custom"] == 1
    p = "/api/v30-metrics-test"
    metrics.observe(p, 12.0)
    metrics.observe(p, 30.0)
    metrics.observe("/static/x.js", 5.0)  # 화면 API 만
    txt = metrics.render(app)
    assert f'quant_http_requests_total{{path="{p}"}} 2' in txt and f'quant_http_latency_ms_max{{path="{p}"}} 30.0' in txt
    assert "/static" not in txt and "quant_kill_switch 0" in txt and metrics.http_stats()[p]["avg_ms"] == 21.0


# ------------------------------------------------------------------ 5. 화면 연결
def test_v30_wiring():
    t1 = (STATIC / "toss.js").read_text()
    t2 = (STATIC / "toss2.js").read_text()
    app_js = (STATIC / "app.js").read_text()
    srv = (ROOT / "src/quant_ai/web/server.py").read_text()
    assert "TV.autopilot" in t2 and "TV.logoq" in t2 and "async function tCompany" in t2 and "function tGoalCard" in t2
    assert '["co", "회사"]' in t1 and "tCompany(body, sym)" in t1 and "#autopilot" in t1 and "compare:" in t1 and "tsk-cmp" in t1
    assert '["autopilot"' in app_js and '["logoq"' in app_js and '["autopilot"' in (STATIC / "easy.js").read_text()
    assert "확인할 것" in app_js and "body.ui-easy #clock" in (STATIC / "toss.css").read_text()
    for route in ('"/api/autopilot"', '"/api/company-view"', '"/api/logo-queue"', '"/metrics"'):
        assert route in srv, route
    assert "QUANT_WARM_LOOP" in srv
    cli = (ROOT / "src/quant_ai/cli.py").read_text()
    assert '"autopilot"' in cli and 'choices=["status", "run", "goal"]' in cli
    assert 'name == "autopilot"' in (ROOT / "src/quant_ai/pipeline.py").read_text()

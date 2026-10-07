"""v32: 소액 현실 백테스트 (원 단위 · 1주 단위 · 월 적립) · 봉인된 소액 검증 → AI 자동매매 관문 · 메뉴 · 화면 테스트 CI."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


def _panel(prices: dict[str, float], days: int = 80, raw_mult: dict[str, float] | None = None, growth: dict[str, float] | None = None,
           last: dict[str, int] | None = None):
    """종목별 일정한 가격(또는 하루 growth 씩 오름) · 수정가 = 실제가 × (1 / raw_mult)."""
    from quant_ai.research_lab import Panel
    dates = pd.bdate_range("2021-01-04", periods=days)
    syms = list(prices)
    cl = pd.DataFrame({s: prices[s] * (1 + (growth or {}).get(s, 0.0)) ** np.arange(days) for s in syms}, index=dates)
    raw = cl * pd.Series({s: (raw_mult or {}).get(s, 1.0) for s in syms})
    for s, k in (last or {}).items():
        cl.loc[dates[k + 1]:, s] = np.nan
        raw.loc[dates[k + 1]:, s] = np.nan
    elig = pd.DataFrame(True, index=dates, columns=syms)
    lastd = pd.Series({s: dates[(last or {}).get(s, days - 1)] for s in syms})
    return Panel(dates, syms, {}, cl.copy(), cl, elig, lastd, raw), dates


def _cfg(**kw):
    from quant_ai.research_lab import StrategyConfig
    return StrategyConfig("t", model="factor", top_k=kw.pop("top_k", 2), buffer_k=kw.pop("buffer_k", 2), **kw)



def _costs():
    from quant_ai.research_lab import Costs
    return Costs(commission_bps=0.0, slippage_bps=0.0, sell_tax_bps=0.0)


# ------------------------------------------------------------------ 1. 소액 시뮬레이터
def test_cash_sim_whole_shares_skips_unaffordable_and_invests_deposits():
    from quant_ai.research_lab import simulate_cash
    p, dates = _panel({"A": 10_000.0, "B": 3_000_000.0, "C": 50_000.0})  # B: 1주 300만원 → 목표 금액으로 1주도 못 삼
    sc = pd.DataFrame({"A": 3.0, "B": 2.0, "C": 1.0}, index=dates)
    r = simulate_cash(p, sc, _cfg(top_k=2, buffer_k=2), _costs(), "2021-01-01", None, 2_000_000, 1_000_000)
    assert r["skipped_unaffordable"] >= 1  # B 건너뜀 → A·C 를 산다
    st = r["stats"]
    months = len(set(dates[1:].to_period("M")))
    assert st["paid"] == 2_000_000 + 1_000_000 * (months - 1)
    assert st["final"] == pytest.approx(st["paid"], abs=60_000)  # 가격이 그대로면 넣은 돈 그대로 (남는 현금은 1주 미만 자투리)
    assert st["twr_total"] == pytest.approx(0.0, abs=1e-9) and st["avg_cash"] < 0.05  # 매달 들어온 돈이 현금으로 쌓이지 않는다


def test_cash_sim_uses_real_share_price_not_adjusted():
    from quant_ai.research_lab import simulate_cash
    # 수정가는 1만원이지만 그날 실제 1주 가격은 50배(액면분할 전) → 50만원 → 목표 금액(100만원/2)으로 못 산다
    p, dates = _panel({"A": 10_000.0, "C": 10_000.0}, raw_mult={"A": 50.0, "C": 1.0})
    sc = pd.DataFrame({"A": 2.0, "C": 1.0}, index=dates)
    r = simulate_cash(p, sc, _cfg(top_k=2, buffer_k=2), _costs(), "2021-01-01", None, 900_000, 0)
    assert r["skipped_unaffordable"] >= 1


def test_cash_sim_no_false_delisting_at_end_but_real_delisting_haircut():
    from quant_ai.research_lab import Costs, simulate_cash
    p, dates = _panel({"A": 10_000.0, "B": 10_000.0}, growth={"A": 0.001, "B": 0.001})
    sc = pd.DataFrame({"A": 2.0, "B": 1.0}, index=dates)
    r = simulate_cash(p, sc, _cfg(top_k=2, buffer_k=2), _costs(), "2021-01-01", None, 1_000_000, 0)
    assert r["stats"]["twr_total"] > 0.05  # 자료 끝(오늘)까지 있는 종목을 상장폐지로 깎지 않는다
    p2, dates2 = _panel({"A": 10_000.0, "B": 10_000.0}, last={"A": 30})  # A 는 30일째 상장폐지
    r2 = simulate_cash(p2, pd.DataFrame({"A": 2.0, "B": 1.0}, index=dates2), _cfg(top_k=2, buffer_k=2),
                       replace(Costs(commission_bps=0, slippage_bps=0, sell_tax_bps=0), delist_haircut=0.3), "2021-01-01", None, 1_000_000, 0)
    assert r2["stats"]["twr_total"] == pytest.approx(-0.15, abs=0.02)  # 절반(A)이 30% 할인 청산


def test_money_stats_excludes_deposits_and_computes_irr():
    from quant_ai.research_lab import money_stats
    idx = pd.bdate_range("2021-01-04", periods=260)
    flows = pd.Series(0.0, index=idx)
    flows.iloc[0], flows.iloc[130] = 1_000_000, 1_000_000
    vals = flows.cumsum()  # 수익 0, 입금만
    st = money_stats(vals, flows)
    assert st["twr_total"] == pytest.approx(0.0, abs=1e-9) and st["paid"] == 2_000_000 and abs(st["irr"]) < 0.01
    grow = vals * np.linspace(1, 1.1, len(idx))
    assert money_stats(grow, flows)["twr_total"] > 0.09


def test_cash_index_dca_matches_index():
    from quant_ai.research_lab import simulate_cash_index
    idx = pd.bdate_range("2021-01-04", periods=300)
    ic = pd.Series(np.linspace(100, 150, 300), index=idx)
    r = simulate_cash_index(ic, idx, "2021-01-01", None, 2_000_000, 1_000_000, fee_annual=0.0, trade_cost=0.0)
    assert r["stats"]["twr_total"] == pytest.approx(150 / ic.iloc[0] - 1, rel=0.02) and r["stats"]["final"] > r["stats"]["paid"]


# ------------------------------------------------------------------ 2. 봉인된 결과 · 관문
def test_bundled_study_is_sealed_and_blocks_live(tmp_path):
    from quant_ai import smallcap
    b = json.loads(smallcap.BUNDLED.read_text(encoding="utf-8"))
    assert b["verdict"]["beat_etf_holdout"] is False and b["chosen"] is None  # 16년 KRX: 소액이면 지수 ETF 적립이 가장 나았다
    assert b["etf"]["holdout"]["final"] > max(r["holdout"]["final"] for r in b["results"])
    assert {r["name"] for r in b["results"]} >= {"factor-top5-trend200", "short-trend-top5"}
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    app = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/s.db", artifacts_dir=tmp_path / "a"))
    s = smallcap.status(app)
    assert s["origin"] == "bundled" and s["sealed_ok"] and "지수 ETF 적립" in s["headline"]
    g = smallcap.gate(app)
    assert g["ok"] is False and "참고" in g["detail"]
    # 내 PC 결과가 있으면 그걸 쓴다 · 봉인 깨지면 관문도 막힌다
    mine = smallcap.compact({**b, "verdict": {"beat_etf_holdout": True, "text": "이김"}}, {"from": "2010"}) | {"origin": "local"}
    ops.set_state(app.engine, smallcap.STATE, mine)
    assert smallcap.load(app)["sealed_ok"] and smallcap.gate(app)["ok"]
    ops.set_state(app.engine, smallcap.STATE, mine | {"chosen": "다른 것"})  # 봉인 뒤 바꿈
    assert smallcap.load(app)["sealed_ok"] is False and smallcap.gate(app)["ok"] is False


def test_run_needs_long_history(tmp_path):
    from quant_ai import smallcap
    from tests.test_marcap import fake_marcap
    fake_marcap(tmp_path, n_codes=4, days=300)
    with pytest.raises(ValueError, match="2010년부터"):
        smallcap.run(object(), str(tmp_path))


def test_autopilot_gate_api_cli_and_screens(tmp_path, monkeypatch, capsys):
    import argparse

    from quant_ai import autopilot as AP
    from quant_ai import smallcap
    from quant_ai.cli import cmd_smallcap
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from quant_ai.web.api import DashboardAPI
    app = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/g.db", artifacts_dir=tmp_path / "a"))
    g = {x["key"]: x for x in AP.gates(app)}
    assert not g["smallcap"]["ok"] and "지수 ETF" in g["smallcap"]["title"]
    api = DashboardAPI(app)
    assert api.goal({})["smallcap"]["etf"]["holdout"]["final"] > 0
    monkeypatch.setattr(smallcap, "run", lambda app, d=None, say=None: {"ok": True})
    assert api.autopilot_write({"action": "smallcap"}) == {"ok": True, "started": True}
    cmd_smallcap(argparse.Namespace(show=True, db=app.settings.database_url, marcap_dir=None, principal=2e6, monthly=1e6))
    out = capsys.readouterr().out
    assert "지수 ETF 적립" in out and "개발자 PC 계산(참고)" in out and "봉인 확인" in out
    js = (STATIC / "toss2.js").read_text(encoding="utf-8")
    assert "function tSmallcapCard" in js and "tSmallcapCard(s.smallcap)" in js and "tSmallcapCard(g.smallcap, true)" in js
    assert "관문 ${nOk}/${(s.gates || []).length}" in js
    assert '["goal", "target", "내 목표"]' in (STATIC / "easy.js").read_text(encoding="utf-8")
    assert "minmax(0, 1fr) minmax(0, 1fr)" in (STATIC / "toss.css").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "ui-smoke" in ci and "pytest -q tests_ui" in ci and (ROOT / "tests_ui/test_screens.py").exists()


def test_raw_close_kept_in_krx_bars(tmp_path):
    from quant_ai.data.collectors.marcap import build_krx_dataset
    from quant_ai.research_lab import build_panel
    from tests.test_marcap import fake_marcap
    fake_marcap(tmp_path, n_codes=4, days=300)
    ds = build_krx_dataset(tmp_path, 2020, 2021, top_n=4)
    assert all("close_raw" in b for b in ds.bars.values())
    p = build_panel(ds)
    assert p.raw_close is not None and p.raw_close.shape == p.close.shape

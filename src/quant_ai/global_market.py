"""해외(미국) 코어 + AI 장부 — 국내와 같은 규칙·같은 증명 체인으로 '해외장에서도 AI 알파가 있나' 를 측정.

- 유니버스: 미국 대형주 목록 (data/global_stocks.py). 과거 백테스트는 생존편향이 생기므로 하지 않고,
  **목록을 고정한 날부터의 전진(forward) 기록만** 성과로 쓴다 → 증명 체인 ①에서 '전진 기록만 사용' 으로 표시.
- 시세: 무료 일봉 (Yahoo → Stooq), 수정주가. 벤치마크 SPY.
- 장부 (모두 가상매매, USD): us-paper (실제 운용과 같은 설정) · us-attr-core / us-attr-veto / us-attr-full (AI 기여 측정)
- 해외 실주문(KIS 해외주식)은 실계좌로 검증하기 전까지 넣지 않는다 → 증명 체인 ⑤는 '가상매매만' 으로 남는다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select

from . import ops
from .config import CostModelConfig, Mode
from .data.db import load_bars, session_scope
from .data.global_stocks import GLOBAL_STOCKS, ensure_global
from .data.models import Instrument

log = logging.getLogger(__name__)
PREFIX = "us-"
MAIN = "us-paper"
BENCH = "SPY"
BOOKS = {"us-attr-core": (False, False), "us-attr-veto": (True, False), "us-attr-full": (True, True)}
CASH_USD = 100_000.0
ETFS = {"SPY", "QQQ", "SOXX", "TQQQ", "SCHD"}
# 국내 증권사 해외주식 온라인 수수료 수준(0.25%) + 슬리피지 5bp, 매도세 없음 (환전 비용은 별도)
COSTS = CostModelConfig(commission_bps=25.0, slippage_bps=5.0, sell_tax_bps=0.0)


def universe() -> list[str]:
    return [s for s, _, _ in GLOBAL_STOCKS if s not in ETFS]


def is_us_book(name: str | None) -> bool:
    return bool(name) and name.startswith(PREFIX)


def sync(app, fetchers=None) -> dict:
    """유니버스 + SPY 일봉 받기 (12시간 캐시). 네트워크가 막혀 있으면 가진 데이터로 계속."""
    ok, fail = 0, []
    for sym in [BENCH, *universe()]:
        r = ensure_global(app.engine, sym, fetchers=fetchers)
        if r.get("ok"):
            ok += 1
        else:
            fail.append(sym)
    ops.set_state(app.engine, "us_sync", {"at": datetime.now(UTC).isoformat(), "ok": ok, "failed": fail[:20]})
    return {"ok": ok, "failed": fail}


def market_data(app, as_of: datetime | None = None):
    """(bars, bench, sentiment=None) — 국내 market_data 와 같은 모양."""
    with session_scope(app.engine) as s:
        have = {i.symbol for i in s.scalars(select(Instrument))}
        syms = [x for x in universe() if x in have]
        bars = load_bars(s, syms)
        bench = load_bars(s, [BENCH]).get(BENCH)
    if as_of is not None:
        ts = pd.Timestamp(as_of)
        ts = ts.tz_localize("UTC") if ts.tz is None else ts
        bars = {k: v[v.index <= ts] for k, v in bars.items()}
        bench = bench[bench.index <= ts] if bench is not None else None
    bars = {k: v for k, v in bars.items() if len(v)}
    return bars, bench, None


def run_cycle(app, as_of: datetime | None = None, ts: datetime | None = None, cfg=None, force: bool = False) -> dict:
    """미국 코어-위성 한 사이클 (새 일봉마다 한 번): 실제 설정 장부 + AI 기여 측정 장부 3개."""
    from .strategy.core_satellite import CoreSatelliteConfig, build_plan, core_scores
    from .trading.broker import MarketQuote
    from .trading.execution import Signal
    st = app.settings
    cfg = cfg or CoreSatelliteConfig(core_top_k=10, core_buffer_k=20, satellite_k=3, shortlist_k=20,
                                     use_ai=not st.core_only)
    ts = ts or datetime.now(UTC)
    bars, bench, _ = market_data(app, as_of)
    if len(bars) < 5 or bench is None or len(bench) < 130:
        return {"skipped": "미국 일봉 부족 — 네트워크 확인 후 quant-ai us sync"}
    last_ts = max(b.index.max() for b in bars.values())
    if not force and as_of is None and ops.get_state(app.engine, "us-cycle").get("bar") == str(last_ts):
        return {"skipped": f"이미 처리한 일봉 ({last_ts.date()})"}
    scores = core_scores(bars, None, cfg.factor_weights)
    if scores.empty:
        return {"skipped": "팩터 점수 계산 불가 (종목당 130거래일 필요)"}
    trend = app._trend(bench, cfg)
    calendar = bench.index
    held = set(app.load_portfolio(MAIN).positions)
    prev = ops.get_state(app.engine, f"cs:{MAIN}")
    shortlist = list(dict.fromkeys([*scores.index[:cfg.shortlist_k], *prev.get("core", []), *[s for s in held if s in bars]]))
    decisions = []
    try:  # AI 는 코어 전용이어도 측정 장부를 위해 판단 (주문엔 us-paper 설정대로만 반영)
        decisions = app.decide(as_of=as_of, symbols=shortlist, scenarios=False, market="US")
    except Exception as e:  # noqa: BLE001
        log.warning("미국 AI 판단 실패: %s", e)
    vetoes, exits, buys = app._ai_overlay(decisions)
    by_sym = {d.symbol: d for d in decisions}
    quotes = {s: MarketQuote(last=float(b["close"].iloc[-1])) for s, b in bars.items()}

    def due(state: dict) -> bool:
        last = state.get("last_rebalance")
        return not last or int(((calendar > pd.Timestamp(last)) & (calendar <= last_ts)).sum()) >= cfg.core_rebalance_days

    def signals(plan) -> list:
        sat = {x["symbol"] for x in plan.satellite}
        out = []
        for sym, w in plan.weights.items():
            d = by_sym.get(sym)
            out.append(Signal(sym, w, d.signal.prob_up if (d and sym in sat) else None,
                              ("SATELLITE" if sym in sat else f"CORE #{plan.ranks.get(sym, -1) + 1}") + " US",
                              consensus_id=d.consensus_id if d else None))
        return out

    results = {}
    runs = {MAIN: (cfg.use_ai, cfg.use_ai and st.ai_overlay == "full"), **BOOKS}
    for book, (use_veto, use_sat) in runs.items():
        bst = ops.get_state(app.engine, f"cs:{book}")
        scale = trend["scale"] if due(bst) or "trend_scale" not in bst else bst["trend_scale"]
        # 1주 가격이 종목당 목표 금액보다 훨씬 비싸면 다음 순위로 대체 (국내와 같은 규칙, 소수점 주식 없음)
        equity = app.load_portfolio(book).equity({k: q.last for k, q in quotes.items()})
        plan = build_plan(scores, bst.get("core", []), due(bst), cfg, vetoes, exits, buys,
                          use_veto=use_veto, use_satellite=use_sat, core_scale=scale,
                          unaffordable=app._unaffordable(scores, bars, equity, cfg))
        app._apply_var_budget(plan, bars)
        fills = app.trade(decisions, Mode.PAPER, ts=ts, quotes=dict(quotes), signals=signals(plan), book=book)
        ops.set_state(app.engine, f"cs:{book}", {
            "core": plan.core, "last_rebalance": str(last_ts) if plan.core_rebalanced else bst.get("last_rebalance"),
            "trend_scale": trend["scale"] if plan.core_rebalanced else bst.get("trend_scale", 1.0)})
        results[book] = {"fills": len(fills), "core": plan.core, "rebalanced": plan.core_rebalanced}
        if book == MAIN:
            ops.set_state(app.engine, "cs-plan:us", {**plan.to_dict(), "as_of": str(last_ts), "ts": ts.isoformat(),
                                                     "trend": trend, "ai": {"analyzed": len(decisions)}})
    if as_of is None:
        ops.set_state(app.engine, "us-cycle", {"bar": str(last_ts), "ts": ts.isoformat(), "analyzed": len(decisions)})
    return {"as_of": str(last_ts), "analyzed": len(decisions), "books": results}


__all__ = ["PREFIX", "MAIN", "BENCH", "BOOKS", "COSTS", "CASH_USD", "universe", "sync", "market_data", "run_cycle",
           "is_us_book"]

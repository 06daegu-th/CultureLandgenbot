"""운용 분석 — Net Alpha · 실행 품질 · 반사실(Counterfactual) · 이벤트 반응 DB · 데이터 신뢰도 · 스트레스.

모두 DB 에 이미 쌓인 기록만 읽는다 (주문·상태를 바꾸지 않음). 대시보드·채팅·복기가 같은 함수를 쓴다.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from . import ops
from .data.db import session_scope
from .data.models import (
    ConsensusRecord,
    Disclosure,
    FillRecord,
    Instrument,
    LLMCall,
    MacroObservation,
    NewsArticle,
    OrderRecord,
    PortfolioSnapshot,
    PriceBar,
)
from .engines.regime import regime_series
from .review.net_alpha import net_alpha

log = logging.getLogger(__name__)
ATTR_BOOKS = ("attr-core", "attr-veto", "attr-full")


def main_mode(app) -> str:
    m = app.settings.mode.value
    return m if m in ("paper", "shadow", "live") else "paper"


def book_equity(app, book: str) -> pd.Series:
    with session_scope(app.engine) as s:
        snaps = s.execute(select(PortfolioSnapshot.ts, PortfolioSnapshot.equity).where(
            PortfolioSnapshot.mode == book).order_by(PortfolioSnapshot.ts, PortfolioSnapshot.id)).all()
    if not snaps:
        return pd.Series(dtype=float)
    return pd.Series([e for _, e in snaps], index=pd.DatetimeIndex([t for t, _ in snaps]), dtype=float)


# ====================================================================== ⑭ Net Alpha
def _book_costs(app, book: str, since) -> tuple[float, float]:
    """(수수료+세금, 슬리피지 금액) — 슬리피지는 기준가 대비 불리한 방향 +."""
    with session_scope(app.engine) as s:
        fee = s.scalar(select(func.coalesce(func.sum(FillRecord.fee), 0.0)).join(
            OrderRecord, FillRecord.order_id == OrderRecord.id).where(
            OrderRecord.mode == book, FillRecord.ts >= since)) or 0.0
        rows = s.execute(select(OrderRecord.side, OrderRecord.ref_price, OrderRecord.avg_price,
                                OrderRecord.filled_qty, OrderRecord.qty).where(
            OrderRecord.mode == book, OrderRecord.created_at >= since, OrderRecord.ref_price.is_not(None),
            OrderRecord.avg_price.is_not(None))).all()
    slip = sum((avg - ref) * (fq or q) * (1 if side == "buy" else -1) for side, ref, avg, fq, q in rows)
    return float(fee), float(slip)


def _is_market(sym: str, market: str) -> bool:
    kr = bool(re.fullmatch(r"\d{6}", sym or ""))
    return kr if market == "KR" else not kr


def _signal_stats(app, since, market: str = "KR") -> tuple[dict, dict]:
    """시장별 채점된 합의: 방향 신호 적중 + 확률 보정 지표."""
    from .ensemble.calibration import metrics
    with session_scope(app.engine) as s:
        resolved = [r for r in s.execute(select(ConsensusRecord.symbol, ConsensusRecord.action, ConsensusRecord.correct,
                                                ConsensusRecord.realized_return, ConsensusRecord.prob_up).where(
            ConsensusRecord.correct.is_not(None), ConsensusRecord.as_of >= since)).all() if _is_market(r[0], market)]
    direc = [x for x in resolved if x[1] in ("BUY", "SELL")]
    hit = {"n": len(direc), "hits": sum(bool(x[2]) for x in direc), "resolved": len(resolved)}
    cal = metrics([x[4] for x in resolved], [1.0 if (x[3] or 0) > 0 else 0.0 for x in resolved]) if resolved else {"n": 0}
    return hit, cal


def data_check(app, market: str = "KR", limit: int = 3000) -> dict:
    """① 판단에 쓴 입력이 그 시점에 알 수 있던 것인가: 최근 판단 limit 건의 저장된 근거(evidence) 속
    뉴스·공시·마지막 봉 시각 vs 판단 시각."""
    with session_scope(app.engine) as s:
        rows = s.execute(select(ConsensusRecord.symbol, ConsensusRecord.as_of, ConsensusRecord.payload)
                         .order_by(ConsensusRecord.id.desc()).limit(limit)).all()
        src = dict(s.execute(select(PriceBar.source, func.count()).group_by(PriceBar.source)).all())
    checked = viol = 0
    examples = []
    for sym, as_of, payload in rows:
        if not _is_market(sym, market):
            continue
        ev = (payload or {}).get("evidence")
        if not ev:
            continue
        checked += 1
        t0 = pd.Timestamp(as_of)
        t0 = t0.tz_localize("UTC") if t0.tz is None else t0
        bad = [n.get("title") for n in ev.get("news", []) if n.get("ts") and _ts(n["ts"]) > t0]
        bad += [d.get("title") for d in ev.get("disclosures", []) if d.get("date") and str(d["date"]) > str(t0.date())]
        lb = (ev.get("price") or {}).get("last_bar")
        if lb and _ts(lb) > t0:
            bad.append("가격 봉")
        if bad:
            viol += 1
            if len(examples) < 5:
                examples.append({"symbol": sym, "as_of": str(as_of)[:16], "items": [str(x)[:60] for x in bad[:2]]})
    dq = ops.get_state(app.engine, "data_quality")
    kr = market == "KR"
    return {"checked": checked, "ts_violations": viol, "examples": examples,
            "adjusted": ("marcap" in src or not src) if kr else True,
            "pit_universe": bool(ops.get_state(app.engine, "krx_universe")) if kr else False,
            "forward_only": not kr, "quality_warnings": len(dq.get("warnings", [])) if isinstance(dq, dict) else 0,
            "notes": [] if kr else ["미국은 과거 백테스트 없이 전진 기록만 (생존편향 회피)"]}


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tz is None else t


def net_alpha_report(app, mode: str | None = None, market: str = "KR") -> dict:
    """증명 체인 6단계 + AI 알파 분리 + 수익 분해. market: KR(국내) / US(미국 가상 장부)."""
    from . import global_market as gm
    from .review.net_alpha import relative
    us = market == "US"
    prefix = gm.PREFIX if us else ""
    mode = gm.MAIN if us else (mode or main_mode(app))
    eq = book_equity(app, mode)
    flows = [] if us else (app.cashflows(mode) if hasattr(app, "cashflows") else [])
    if flows and len(eq):
        from .pipeline import time_weighted_index
        eq = time_weighted_index(eq, flows)
    books = {f"{prefix}{b}": book_equity(app, f"{prefix}{b}") for b in ATTR_BOOKS}
    books = {k: v for k, v in books.items() if len(v)}
    _, bench, _ = gm.market_data(app) if us else app.market_data()
    bench_close = bench["close"] if bench is not None and len(bench) else None
    regimes = regime_series(bench)["regime"] if bench is not None and len(bench) > 60 else None
    since = eq.index.min() if len(eq) else datetime.now(UTC) - timedelta(days=365)
    fee, slip = _book_costs(app, mode, since)
    ai_book = "attr-veto" if app.settings.ai_overlay == "veto" else "attr-full"
    ai_cost_gap = 0.0
    if f"{prefix}{ai_book}" in books and f"{prefix}attr-core" in books:
        ai_c = sum(_book_costs(app, f"{prefix}{ai_book}", since))
        core_c = sum(_book_costs(app, f"{prefix}attr-core", since))
        start = float(books[f"{prefix}attr-core"].iloc[0]) or 1.0
        ai_cost_gap = (ai_c - core_c) / start
    hit, cal = _signal_stats(app, datetime.now(UTC) - timedelta(days=365), market)
    # ⑤ 실제 주문: 실제 장부 vs 같은 규칙의 시뮬레이션 장부
    live = not us and mode == "live" and app.settings.broker == "kis"
    twin = f"{prefix}attr-core" if app.settings.core_only else f"{prefix}{ai_book}"
    ex = execution_quality(app, mode, days=365)
    execution = {"live": live, "twin": twin, "tracking": relative(eq, books[twin]) if twin in books and len(eq) else None,
                 "slippage_mean": (ex.get("slippage_bps") or {}).get("mean"), "assumed": ex.get("assumed_slippage_bps"),
                 "fill_rate": ex.get("fill_rate"), "n_orders": ex.get("orders") or 0}
    rep = net_alpha(eq, bench_close, books=books, data_check=data_check(app, market), calibration=cal, hit=hit,
                    main_cost=fee + slip, ai_cost_gap=ai_cost_gap, execution=execution, regimes=regimes,
                    ai_book=ai_book, prefix=prefix)
    for st in rep["steps"]:
        st.pop("tracking", None)
    return rep | {"mode": mode, "market": market, "bench_name": gm.BENCH if us else "KOSPI", "currency": "USD" if us else "KRW",
                  "core_only": app.settings.core_only, "ai_book": f"{prefix}{ai_book}",
                  "books": {k: {"return": round(float(v.iloc[-1] / v.iloc[0] - 1), 5), "days": int(v.index.normalize().nunique())}
                            for k, v in books.items() if len(v) > 1}}


# ====================================================================== ⑦ Execution Quality
def execution_quality(app, mode: str | None = None, days: int = 90) -> dict:
    mode = mode or main_mode(app)
    since = datetime.now(UTC) - timedelta(days=days)
    with session_scope(app.engine) as s:
        orders = s.scalars(select(OrderRecord).where(OrderRecord.mode == mode, OrderRecord.created_at >= since)).all()
        fees = dict(s.execute(select(FillRecord.order_id, func.sum(FillRecord.fee)).join(
            OrderRecord, FillRecord.order_id == OrderRecord.id).where(
            OrderRecord.mode == mode, OrderRecord.created_at >= since).group_by(FillRecord.order_id)).all())
        names = {i.symbol: i.name for i in s.scalars(select(Instrument))}
        rows = [(o.id, o.symbol, o.side, o.qty, o.status, o.ref_price, o.avg_price, o.filled_qty,
                 o.created_at, o.updated_at) for o in orders]
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r[4]] = by_status.get(r[4], 0) + 1
    sent = [r for r in rows if r[4] not in ("rejected", "blocked")]
    ordered_qty = sum(r[3] for r in sent)
    filled_qty = sum((r[7] if r[7] is not None else (r[3] if r[4] == "filled" else 0)) for r in sent)
    slips, notional, shortfall, fee_total, latency = [], 0.0, 0.0, 0.0, []
    worst = []
    for oid, sym, side, qty, st, ref, avg, fq, created, updated in rows:
        if st not in ("filled", "partial") or not ref or not avg:
            continue
        q = fq or qty
        bps = (avg - ref) / ref * 1e4 * (1 if side == "buy" else -1)
        slips.append(bps)
        notional += avg * q
        shortfall += (avg - ref) * q * (1 if side == "buy" else -1)
        fee_total += float(fees.get(oid) or 0)
        if created and updated and updated > created:
            latency.append((updated - created).total_seconds())
        worst.append({"symbol": sym, "name": names.get(sym, sym), "side": side, "qty": q, "slippage_bps": round(bps, 1),
                      "ts": created.isoformat() if created else None})
    a = pd.Series(slips, dtype=float)
    assumed = app.settings.costs.slippage_bps
    out = {"mode": mode, "days": days, "orders": len(rows), "by_status": by_status,
           "fill_rate": round(filled_qty / ordered_qty, 4) if ordered_qty else None,
           "partial_rate": round(by_status.get("partial", 0) / len(sent), 4) if sent else None,
           "cancel_rate": round((by_status.get("cancelled", 0) + by_status.get("unfilled", 0)) / len(sent), 4) if sent else None,
           "unknown": by_status.get("unknown", 0), "rejected": by_status.get("rejected", 0),
           "n_filled": len(slips), "assumed_slippage_bps": assumed,
           "slippage_bps": {"mean": round(float(a.mean()), 2), "median": round(float(a.median()), 2),
                            "p90": round(float(a.quantile(0.9)), 2)} if len(a) else None,
           "implementation_shortfall_krw": round(shortfall), "fees_krw": round(fee_total),
           "cost_bps": round((fee_total + shortfall) / notional * 1e4, 2) if notional else None,
           "median_fill_seconds": round(float(np.median(latency)), 1) if latency else None,
           "worst": sorted(worst, key=lambda x: -x["slippage_bps"])[:8]}
    if len(a):
        out["verdict"] = ("warn", f"실측 슬리피지 평균 {a.mean():.1f}bp > 가정 {assumed:.0f}bp — 백테스트 비용 상향 필요") \
            if a.mean() > assumed else ("ok", f"실측 슬리피지 평균 {a.mean():.1f}bp ≤ 가정 {assumed:.0f}bp")
    return out


# ====================================================================== ⑤ Counterfactual
def counterfactual(app) -> dict:
    """'다르게 했다면?' — 거부권·NO TRADE 가 피한 손실/놓친 수익, 그리고 장부별 대안 경로."""
    from .review.evidence import no_trade_value
    since = datetime.now(UTC) - timedelta(days=365)
    with session_scope(app.engine) as s:
        rows = s.execute(select(ConsensusRecord.payload, ConsensusRecord.realized_return).where(
            ConsensusRecord.correct.is_not(None), ConsensusRecord.as_of >= since)).all()
        no_trade = no_trade_value(s, since)
    vetoed = [r or 0.0 for p, r in rows if (p or {}).get("vetoes")]
    veto = {"n": len(vetoed)}
    if vetoed:
        veto |= {"avg_return_if_bought": round(float(np.mean(vetoed)), 5),
                 "avoided_loss_rate": round(sum(r < 0 for r in vetoed) / len(vetoed), 4),
                 "verdict": "거부권이 손실을 피함" if np.mean(vetoed) < 0 else "거부권이 수익을 놓침"}
    paths = {}
    for b, label in (("attr-core", "AI 없이 (코어만)"), ("attr-veto", "코어 + AI 거부권"), ("attr-full", "코어 + 거부권 + 위성"),
                     (main_mode(app), "실제 운용")):
        eq = book_equity(app, b)
        if len(eq) > 1:
            paths[b] = {"label": label, "return": round(float(eq.iloc[-1] / eq.iloc[0] - 1), 5),
                        "max_drawdown": round(float((eq / eq.cummax() - 1).min()), 5)}
    _, bench, _ = app.market_data()
    first = min((book_equity(app, b).index.min() for b in paths), default=None)
    if bench is not None and first is not None and len(bench):
        k = bench["close"]
        k.index = pd.DatetimeIndex(k.index).tz_localize(None) if k.index.tz is not None else k.index
        k = k[k.index >= pd.Timestamp(first).tz_localize(None).normalize()]
        if len(k) > 1:
            paths["kospi"] = {"label": "KOSPI 그냥 보유", "return": round(float(k.iloc[-1] / k.iloc[0] - 1), 5),
                              "max_drawdown": round(float((k / k.cummax() - 1).min()), 5)}
    return {"veto": veto, "no_trade": no_trade, "paths": paths}


# ====================================================================== ⑥ Event Reaction DB
EVENT_TYPES = [
    ("유상증자", ("유상증자",)), ("무상증자", ("무상증자",)), ("전환사채·BW", ("전환사채", "신주인수권부사채", "교환사채")),
    ("자사주 매입", ("자기주식취득", "자기주식 취득", "자사주")), ("자사주 소각", ("소각",)),
    ("실적 발표", ("영업(잠정)실적", "잠정실적", "매출액또는손익구조")), ("배당", ("배당",)),
    ("공급계약", ("단일판매", "공급계약")), ("최대주주 변경", ("최대주주변경", "최대주주 변경")),
    ("합병·분할", ("합병", "분할")), ("소송", ("소송",)), ("거래정지·관리", ("매매거래정지", "관리종목", "상장폐지")),
    ("임원·주요주주 매매", ("임원ㆍ주요주주", "임원·주요주주", "주식등의대량보유")),
]


def event_type(title: str) -> str | None:
    t = re.sub(r"\s+", "", title or "")
    for label, keys in EVENT_TYPES:
        if any(k.replace(" ", "") in t for k in keys):
            return label
    return None


def _abnormal(close: pd.Series, bench: pd.Series, day, n: int) -> float | None:
    after = close.index[close.index.normalize() >= pd.Timestamp(day)]
    if len(after) <= n:
        return None
    i0 = close.index.get_loc(after[0])
    if i0 == 0:
        return None
    t0, t1 = close.index[i0 - 1], close.index[i0 + n - 1] if n > 0 else close.index[i0]
    r = close.loc[t1] / close.loc[t0] - 1
    if t0 in bench.index and t1 in bench.index:
        r -= bench.loc[t1] / bench.loc[t0] - 1
    return float(r)


def event_reactions(app, refresh: bool = False) -> dict:
    """공시 유형별: 공시 다음 1·5거래일 KOSPI 대비 초과수익 (평균·양수 비율·t). 하루 한 번 계산해 저장."""
    st = ops.get_state(app.engine, "event_reactions")
    if st and not refresh and st.get("computed_at", "")[:10] == str(date.today()):
        return st
    bars, bench, _ = app.market_data()
    b = bench["close"] if bench is not None and len(bench) else pd.Series(dtype=float)
    agg: dict[str, dict[str, list]] = {}
    with session_scope(app.engine) as s:
        rows = s.execute(select(Disclosure.symbol, Disclosure.title, Disclosure.filed_at).where(
            Disclosure.symbol.is_not(None))).all()
        names = {i.symbol: i.name for i in s.scalars(select(Instrument))}
    recent: list[dict] = []
    for sym, title, filed in rows:
        et = event_type(title)
        if et is None or sym not in bars or bars[sym].empty:
            continue
        c = bars[sym]["close"]
        ar1, ar5 = _abnormal(c, b, filed, 1), _abnormal(c, b, filed, 5)
        if ar1 is None:
            continue
        a = agg.setdefault(et, {"ar1": [], "ar5": []})
        a["ar1"].append(ar1)
        if ar5 is not None:
            a["ar5"].append(ar5)
        recent.append({"date": str(filed), "symbol": sym, "name": names.get(sym, sym), "type": et, "title": title[:80],
                       "ar1": round(ar1, 4), "ar5": None if ar5 is None else round(ar5, 4)})

    def summ(v):
        if not v:
            return None
        arr = np.array(v)
        sd = arr.std(ddof=1) if len(arr) > 1 else 0.0
        return {"n": len(arr), "mean": round(float(arr.mean()), 5), "pos_rate": round(float((arr > 0).mean()), 3),
                "t": round(float(arr.mean() / sd * math.sqrt(len(arr))), 2) if sd > 0 else None}

    types = sorted(({"type": k, "d1": summ(v["ar1"]), "d5": summ(v["ar5"])} for k, v in agg.items()),
                   key=lambda x: -(x["d1"]["n"] if x["d1"] else 0))
    out = {"computed_at": datetime.now(UTC).isoformat(), "types": types, "n": len(recent),
           "recent": sorted(recent, key=lambda x: x["date"], reverse=True)[:30],
           "note": "공시일 전날 종가 → 다음 1·5거래일, KOSPI 대비 초과수익. 과거 평균일 뿐 이번에도 같다는 보장은 없다."}
    ops.set_state(app.engine, "event_reactions", out)
    return out


def event_prior(app, title: str) -> str | None:
    """AI 입력용 한 줄: '과거 유상증자 공시 다음날 평균 -3.1% (n=24, 양수 21%)'."""
    et = event_type(title)
    if et is None:
        return None
    st = ops.get_state(app.engine, "event_reactions")
    for t in st.get("types", []):
        if t["type"] == et and t["d1"] and t["d1"]["n"] >= 5:
            d = t["d1"]
            return f"과거 {et} 공시 다음날 평균 {d['mean']:+.1%} (n={d['n']}, 양수 {d['pos_rate']:.0%})"
    return None


# ====================================================================== ⑫ Data Confidence / Freshness
def _bdays_between(a: date, b: date) -> int:
    return max(int(np.busday_count(a, b)), 0)


def data_confidence(app) -> dict:
    """소스별 신선도 + 커버리지 + 품질 경고 → 0~100 신뢰도. 낮으면 킬스위치 조건(Quote stale / Data conflict)에 쓰인다."""
    st = app.settings
    now = datetime.now(UTC)
    today = now.date()
    with session_scope(app.engine) as s:
        last_bar = s.scalar(select(func.max(PriceBar.ts)).where(PriceBar.interval == "1d"))
        n_sym = s.scalar(select(func.count(func.distinct(PriceBar.symbol)))) or 0
        n_last = (s.scalar(select(func.count(func.distinct(PriceBar.symbol))).where(PriceBar.ts == last_bar))
                  if last_bar else 0) or 0
        last_news = s.scalar(select(func.max(NewsArticle.published_at)))
        n_news = s.scalar(select(func.count()).select_from(NewsArticle)) or 0
        last_disc = s.scalar(select(func.max(Disclosure.filed_at)))
        last_macro = s.scalar(select(func.max(MacroObservation.ts)))
        last_llm = s.scalar(select(func.max(LLMCall.ts)).where(LLMCall.status == "ok"))
        last_cons = s.scalar(select(func.max(ConsensusRecord.as_of)))
    dq = ops.get_state(app.engine, "data_quality")
    quotes = ops.get_state(app.engine, "live_quotes")

    def age_days(x) -> int | None:
        if x is None:
            return None
        d = x.date() if isinstance(x, datetime) else x
        return _bdays_between(d, today)

    def item(key, label, configured, last, max_age, unit="영업일", hint=""):
        a = age_days(last)
        if not configured:
            status = "off"
        elif a is None:
            status = "missing"
        else:
            status = "ok" if a <= max_age else "stale"
        return {"key": key, "label": label, "configured": configured, "last": str(last)[:19] if last else None,
                "age": a, "max_age": max_age, "unit": unit, "status": status, "hint": hint}

    items = [
        item("prices", "국내 주가 (일봉)", True, last_bar, max(st.max_data_age_days, 1) if st.max_data_age_days else 5,
             hint="./run.sh data 로 갱신"),
        item("news", "뉴스", bool(st.news_feeds), last_news, 1, hint="QUANT_NEWS_FEEDS (비우면 기본 피드)"),
        item("disclosures", "공시 (DART)", bool(st.dart_api_key), last_disc, 3, hint="DART_API_KEY"),
        item("macro", "거시·해외지수 (FRED)", bool(st.fred_api_key), last_macro, 5, hint="FRED_API_KEY"),
        item("ai", "AI 응답 (LLM)", st.has_llm, last_llm, 3, hint="GEMINI_API_KEY 등"),
        item("decisions", "AI 판단 기록", True, last_cons, 3, hint="장중 사이클 또는 대시보드 '지금 AI 판단'"),
    ]
    if st.broker == "kis":
        qts = quotes.get("ts")
        items.append({"key": "quotes", "label": "실시간 시세 (KIS)", "configured": True, "last": qts,
                      "age": None, "max_age": 0, "unit": "분",
                      "status": "ok" if qts and (now - datetime.fromisoformat(qts)).total_seconds() < 1800 else "stale",
                      "hint": "장중 KIS 시세 조회"})
    coverage = n_last / n_sym if n_sym else 0.0
    warn = len(dq.get("warnings", [])) if isinstance(dq, dict) else 0
    weights = {"prices": 45, "news": 10, "disclosures": 10, "macro": 10, "ai": 10, "decisions": 5, "quotes": 10}
    tot = got = 0.0
    for it in items:
        if it["status"] == "off":
            continue
        w = weights.get(it["key"], 5)
        tot += w
        got += w * (1.0 if it["status"] == "ok" else 0.3 if it["status"] == "stale" else 0.0)
    score = (got / tot * 100 if tot else 0) * (0.5 + 0.5 * coverage) - min(warn, 10) * 1.5
    score = int(max(min(score, 100), 0))
    return {"score": score, "label": "높음" if score >= 80 else "보통" if score >= 55 else "낮음",
            "coverage": round(coverage, 3), "symbols": n_sym, "symbols_on_last_bar": n_last,
            "quality_warnings": warn, "news_count": n_news, "items": items, "checked_at": now.isoformat()}


# ====================================================================== ⑨ Portfolio Stress
# 실제 과거 급락 구간 (KOSPI). 데이터가 있으면 종목별 실제 수익률로 재현, 없으면 베타 × KOSPI 로 추정.
STRESS_SCENARIOS = [
    {"key": "gfc2008", "label": "2008 금융위기", "start": "2008-05-16", "end": "2008-10-24", "kospi": -0.508},
    {"key": "eu2011", "label": "2011 미국 신용등급 강등", "start": "2011-08-01", "end": "2011-08-19", "kospi": -0.172},
    {"key": "trade2018", "label": "2018 10월 미중 무역분쟁", "start": "2018-10-01", "end": "2018-10-29", "kospi": -0.134},
    {"key": "covid2020", "label": "2020 코로나 폭락", "start": "2020-02-17", "end": "2020-03-19", "kospi": -0.355},
    {"key": "rate2022", "label": "2022 금리 급등", "start": "2022-01-03", "end": "2022-09-30", "kospi": -0.270},
    {"key": "yen2024", "label": "2024-08-05 엔캐리 청산", "start": "2024-08-01", "end": "2024-08-05", "kospi": -0.121},
    {"key": "tariff2025", "label": "2025-04 관세 충격", "start": "2025-03-31", "end": "2025-04-09", "kospi": -0.090},
]


def stress_test(app, mode: str | None = None) -> dict:
    mode = mode or main_mode(app)
    pf = app.load_portfolio(mode)
    bars, bench, _ = app.market_data()
    prices = {s: float(b["close"].iloc[-1]) for s, b in bars.items() if len(b)}
    for s_, p in pf.positions.items():
        prices.setdefault(s_, p.avg_price)
    equity = pf.equity(prices)
    w = {s_: p.qty * prices[s_] / equity for s_, p in pf.positions.items() if p.qty and equity > 0}
    b = bench["close"] if bench is not None and len(bench) else None
    betas = {}
    if b is not None:
        rb = b.pct_change()
        for s_ in w:
            if s_ in bars and len(bars[s_]) > 60:
                r = bars[s_]["close"].pct_change().reindex(rb.index)
                ok = r.notna() & rb.notna()
                ok &= ok.cumsum() > ok.sum() - 250
                betas[s_] = float(np.cov(r[ok], rb[ok])[0, 1] / rb[ok].var()) if ok.sum() > 30 and rb[ok].var() > 0 else 1.0
    rows = []
    for sc in STRESS_SCENARIOS:
        t0, t1 = pd.Timestamp(sc["start"], tz="UTC"), pd.Timestamp(sc["end"], tz="UTC")
        loss, real = 0.0, 0
        kospi = sc["kospi"]
        if b is not None:
            kb = b[(b.index >= t0) & (b.index <= t1 + pd.Timedelta(days=1))]
            if len(kb) > 1:
                kospi = float(kb.iloc[-1] / kb.iloc[0] - 1)
        for s_, wt in w.items():
            c = bars.get(s_, pd.DataFrame()).get("close")
            seg = c[(c.index >= t0) & (c.index <= t1 + pd.Timedelta(days=1))] if c is not None else None
            if seg is not None and len(seg) > 1:
                loss += wt * float(seg.iloc[-1] / seg.iloc[0] - 1)
                real += 1
            else:
                loss += wt * betas.get(s_, 1.0) * kospi
        rows.append({"key": sc["key"], "label": sc["label"], "period": f"{sc['start']} ~ {sc['end']}",
                     "kospi": round(kospi, 4), "portfolio": round(loss, 4), "loss_krw": round(loss * equity),
                     "method": "실제 재현" if w and real == len(w) else "일부 추정" if real else "베타 추정",
                     "vs_kospi": round(loss - kospi, 4)})
    factor = [{"label": "KOSPI -10%", "portfolio": round(sum(wt * betas.get(s_, 1.0) for s_, wt in w.items()) * -0.10, 4)},
              {"label": "KOSPI -20% · 상관 1 로 수렴", "portfolio": round(sum(w.values()) * -0.20, 4)},
              {"label": "보유 1위 종목 -30% (하한가)",
               "portfolio": round(-0.30 * max(w.values()), 4) if w else 0.0}]
    worst = min(rows, key=lambda x: x["portfolio"]) if rows and w else None
    return {"mode": mode, "equity": round(equity), "n_positions": len(w), "scenarios": rows, "factor": factor,
            "worst": worst, "note": "실제 재현 = 그 기간 보유 종목의 실제 수익률, 추정 = 최근 1년 베타 × 당시 KOSPI 하락"}


__all__ = ["net_alpha_report", "execution_quality", "counterfactual", "event_reactions", "event_prior",
           "data_confidence", "stress_test", "event_type", "book_equity", "main_mode"]

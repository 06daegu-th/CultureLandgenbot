"""Net Alpha — "AI 가 얼마나 똑똑한가" 가 아니라 "돈으로 남았나" 를 다섯 단계로 답한다.

  ① 얼마나 자주 맞았나      방향 신호(BUY/SELL) 적중률 vs 기준율(그냥 '오른다' 고 찍었을 때)
  ② 몇 %가 돈으로 이어졌나   신호 → 실제 주문·체결 전환율, 체결된 신호의 평균 실현 수익
  ③ 비용을 빼고도 남았나      총수익(비용 전) vs 순수익(수수료·세금·슬리피지 후)
  ④ 벤치마크보다 나았나      KOSPI 대비 초과수익 · 베타 조정 알파 · 정보비율 · t 값 (+ AI 가 코어에 더한 알파)
  ⑤ 여러 국면에서 유지됐나    국면(강세/횡보/약세/위기)별 초과수익

각 단계는 pass / fail / insufficient(표본 부족) 로 판정한다. 판정 기준은 여기 상수로 고정 (결과를 보고 바꾸지 않는다).
Alpha Attribution: 순수익 = 시장(베타×KOSPI) + 코어 팩터 알파 + AI 거부권 + AI 위성 + 비용 + 기타(실행 차이).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

MIN_SIGNALS = 100
MIN_DAYS = 60
MIN_REGIME_DAYS = 10
EDGE_PP = 0.02  # 적중률이 기준율보다 2%p 이상 높아야 '맞췄다'
REGIME_LABELS = {"bull_quiet": "강세·안정", "bull_volatile": "강세·변동", "sideways": "횡보",
                 "bear_quiet": "약세·안정", "bear_volatile": "약세·변동", "crisis": "위기"}


def _daily(eq: pd.Series) -> pd.Series:
    eq = eq.dropna().astype(float)
    if eq.empty:
        return eq
    eq.index = pd.DatetimeIndex(eq.index)
    if eq.index.tz is not None:
        eq.index = eq.index.tz_convert(None)
    eq = eq.groupby(eq.index.normalize()).last()
    return eq[eq > 0]


def _ret(eq: pd.Series) -> float | None:
    return float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) >= 2 else None


def _step(key: str, title: str, status: str, headline: str, detail: str, **data) -> dict:
    return {"key": key, "title": title, "status": status, "headline": headline, "detail": detail, **data}


def hit_step(signals: dict) -> dict:
    """signals: {n, hits, base_rate(실제 상승 비율)} — BUY/SELL 방향 신호만."""
    n, hits = int(signals.get("n") or 0), int(signals.get("hits") or 0)
    base = signals.get("base_rate")
    if n == 0:
        return _step("hit", "① 얼마나 자주 맞았나", "insufficient", "기록 없음",
                     "AI 방향 신호가 채점되면 (보통 5거래일 뒤) 여기 쌓입니다.", n=0)
    rate = hits / n
    # 기준율: 무조건 다수 방향으로 찍었을 때의 적중률
    ref = max(base, 1 - base) if base is not None else 0.5
    edge = rate - ref
    z = edge / math.sqrt(ref * (1 - ref) / n) if 0 < ref < 1 else 0.0
    status = ("insufficient" if n < MIN_SIGNALS else "pass" if edge >= EDGE_PP and z > 1.64 else "fail")
    return _step("hit", "① 얼마나 자주 맞았나", status, f"{rate:.1%}",
                 f"방향 신호 {n}건 중 {hits}건 적중 · 찍기 기준 {ref:.1%} 대비 {edge:+.1%}p (z={z:.2f})"
                 + (f" · 판정에는 {MIN_SIGNALS}건 필요" if n < MIN_SIGNALS else ""),
                 n=n, hits=hits, rate=round(rate, 4), reference=round(ref, 4), edge=round(edge, 4), z=round(z, 2))


def money_step(conv: dict) -> dict:
    """conv: {signals, correct, traded, traded_correct, avg_trade_return(부호=방향 반영)}."""
    sig, traded = int(conv.get("signals") or 0), int(conv.get("traded") or 0)
    correct, tc = int(conv.get("correct") or 0), int(conv.get("traded_correct") or 0)
    avg = conv.get("avg_trade_return")
    if sig == 0:
        return _step("money", "② 몇 %가 돈으로 이어졌나", "insufficient", "기록 없음",
                     "신호가 주문·체결로 이어지면 여기서 연결률과 건당 수익을 봅니다.", signals=0)
    conv_rate = traded / sig
    money_rate = tc / correct if correct else 0.0
    status = ("insufficient" if traded < 30 else "pass" if (avg or 0) > 0 else "fail")
    return _step("money", "② 몇 %가 돈으로 이어졌나", status, f"{money_rate:.0%}",
                 f"맞힌 신호 {correct}건 중 {tc}건이 실제 체결 → 수익 · 전체 신호 체결 전환율 {conv_rate:.0%}"
                 + (f" · 체결 건당 평균 {avg:+.2%}" if avg is not None else ""),
                 signals=sig, traded=traded, correct=correct, traded_correct=tc, conversion=round(conv_rate, 4),
                 money_rate=round(money_rate, 4), avg_trade_return=avg)


def cost_step(eq: pd.Series, costs: float, slippage: float) -> dict:
    eq = _daily(eq)
    net = _ret(eq)
    if net is None:
        return _step("cost", "③ 비용을 빼고도 남았나", "insufficient", "기록 없음",
                     "운용 장부가 이틀 이상 쌓이면 계산합니다.")
    start = float(eq.iloc[0])
    total_cost = (costs + slippage) / start
    gross = net + total_cost
    share = total_cost / gross if gross > 0 else None
    days = len(eq)
    status = "insufficient" if days < MIN_DAYS else "pass" if net > 0 else "fail"
    return _step("cost", "③ 비용을 빼고도 남았나", status, f"{net:+.2%}",
                 f"비용 전 {gross:+.2%} → 수수료·세금 {costs / start:.2%} · 슬리피지 {slippage / start:.2%} → 순 {net:+.2%}"
                 + (f" (비용이 총수익의 {share:.0%})" if share is not None else ""),
                 gross=round(gross, 5), net=round(net, 5), cost_pct=round(total_cost, 5), days=days)


def alpha_stats(eq: pd.Series, bench: pd.Series) -> dict | None:
    """일간 수익률 회귀: r_p = α + β r_b. 초과수익·정보비율·t 값."""
    eq, bench = _daily(eq), _daily(bench)
    idx = eq.index.intersection(bench.index)
    if len(idx) < 3:
        return None
    rp, rb = eq.loc[idx].pct_change().dropna(), bench.loc[idx].pct_change().dropna()
    ex = rp - rb
    beta = float(np.cov(rp, rb)[0, 1] / rb.var()) if rb.var() > 0 else 1.0
    alpha_d = float(rp.mean() - beta * rb.mean())
    te = float(ex.std())
    n = len(ex)
    return {"days": n + 1, "book": float(eq.loc[idx[-1]] / eq.loc[idx[0]] - 1),
            "bench": float(bench.loc[idx[-1]] / bench.loc[idx[0]] - 1),
            "excess": float(eq.loc[idx[-1]] / eq.loc[idx[0]] - bench.loc[idx[-1]] / bench.loc[idx[0]]),
            "beta": round(beta, 3), "alpha_ann": round(alpha_d * 252, 4),
            "ir": round(float(ex.mean() / te * math.sqrt(252)), 3) if te > 0 else None,
            "t": round(float(ex.mean() / te * math.sqrt(n)), 2) if te > 0 else None,
            "curve": [[str(t.date()), round(float(eq.loc[t] / eq.loc[idx[0]] - bench.loc[t] / bench.loc[idx[0]]), 5)]
                      for t in idx]}


def bench_step(st: dict | None, ai: dict | None) -> dict:
    if st is None:
        return _step("bench", "④ 벤치마크보다 나았나", "insufficient", "기록 없음",
                     "운용 장부와 KOSPI 가 겹치는 날이 쌓이면 계산합니다.")
    status = ("insufficient" if st["days"] < MIN_DAYS else
              "pass" if st["excess"] > 0 and (st["t"] or 0) > 1.0 else "fail")
    ai_txt = ""
    if ai is not None:
        ai_txt = f" · AI 가 코어에 더한 순수익 {ai['excess']:+.2%}p"
    return _step("bench", "④ 벤치마크보다 나았나", status, f"{st['excess']:+.2%}p",
                 f"운용 {st['book']:+.2%} vs KOSPI {st['bench']:+.2%} · 베타 {st['beta']:.2f} · "
                 f"연 알파 {st['alpha_ann']:+.1%} · t={st['t'] if st['t'] is not None else '-'}{ai_txt}",
                 **{k: v for k, v in st.items() if k != "curve"}, ai=ai and {k: v for k, v in ai.items() if k != "curve"})


def regime_step(eq: pd.Series, bench: pd.Series, regimes: pd.Series | None) -> dict:
    eq, bench = _daily(eq), _daily(bench)
    if regimes is None or len(eq) < 3:
        return _step("regime", "⑤ 여러 국면에서 유지됐나", "insufficient", "기록 없음",
                     "국면이 두 가지 이상 지나가야 판정할 수 있습니다.", rows=[])
    reg = regimes.copy()
    reg.index = pd.DatetimeIndex(reg.index).tz_localize(None) if reg.index.tz is not None else pd.DatetimeIndex(reg.index)
    idx = eq.index.intersection(bench.index)
    rp, rb = eq.loc[idx].pct_change().dropna(), bench.loc[idx].pct_change().dropna()
    lab = reg.reindex(rp.index, method="ffill")
    rows = []
    for r, g in rp.groupby(lab):
        if not r:
            continue
        b = rb.loc[g.index]
        rows.append({"regime": r, "label": REGIME_LABELS.get(r, r), "days": len(g),
                     "book": round(float((1 + g).prod() - 1), 5), "bench": round(float((1 + b).prod() - 1), 5),
                     "excess": round(float((1 + g).prod() - (1 + b).prod()), 5)})
    enough = [x for x in rows if x["days"] >= MIN_REGIME_DAYS]
    wins = sum(x["excess"] > 0 for x in enough)
    status = ("insufficient" if len(enough) < 2 else "pass" if wins / len(enough) >= 2 / 3 else "fail")
    return _step("regime", "⑤ 여러 국면에서 유지됐나", status,
                 f"{wins}/{len(enough)} 국면" if enough else "기록 없음",
                 f"{MIN_REGIME_DAYS}거래일 이상 겪은 국면 {len(enough)}개 중 {wins}개에서 KOSPI 초과"
                 + ("" if len(enough) >= 2 else " · 판정에는 국면 2개 이상 필요"), rows=rows)


def attribution(main: dict | None, books: dict[str, pd.Series], bench: pd.Series, cost_pct: float | None) -> list[dict]:
    """순수익 분해 (같은 기간). 가상 장부가 없으면 시장/알파/비용만."""
    if main is None:
        return []
    mkt = main["beta"] * main["bench"]
    parts = [{"key": "market", "label": f"시장 (베타 {main['beta']:.2f} × KOSPI)", "value": mkt}]
    rets = {k: _ret(_daily(v)) for k, v in books.items()}
    core, veto, full = rets.get("attr-core"), rets.get("attr-veto"), rets.get("attr-full")
    gross_other = main["book"] - mkt + (cost_pct or 0)
    if core is not None:
        parts.append({"key": "core", "label": "코어 팩터 알파", "value": core + (cost_pct or 0) - mkt})
        explained = core + (cost_pct or 0) - mkt
        if veto is not None:
            parts.append({"key": "veto", "label": "AI 거부권·긴급청산", "value": veto - core})
            explained += veto - core
        if full is not None and veto is not None:
            parts.append({"key": "satellite", "label": "AI 위성 베팅", "value": full - veto})
            explained += full - veto
        parts.append({"key": "cost", "label": "비용 (수수료·세금·슬리피지)", "value": -(cost_pct or 0)})
        parts.append({"key": "other", "label": "기타 (실행 차이·현금 타이밍)", "value": gross_other - explained})
    else:
        parts.append({"key": "alpha", "label": "알파 (비용 전)", "value": gross_other})
        parts.append({"key": "cost", "label": "비용 (수수료·세금·슬리피지)", "value": -(cost_pct or 0)})
    total = sum(p["value"] for p in parts)
    for p in parts:
        p["value"] = round(p["value"], 5)
    return parts + [{"key": "total", "label": "순수익 합계", "value": round(total, 5)}]


def net_alpha(main_eq: pd.Series, bench: pd.Series | None, *, books: dict[str, pd.Series] | None = None,
              costs: float = 0.0, slippage: float = 0.0, signals: dict | None = None, conv: dict | None = None,
              regimes: pd.Series | None = None, ai_book: str | None = None) -> dict:
    books = books or {}
    bench = bench if bench is not None else pd.Series(dtype=float)
    main = alpha_stats(main_eq, bench) if len(bench) else None
    ai = None
    if ai_book and ai_book in books and "attr-core" in books:
        ai = alpha_stats(books[ai_book], books["attr-core"])  # AI 장부 vs 코어 장부 = AI 증분
    steps = [hit_step(signals or {}), money_step(conv or {}), cost_step(main_eq, costs, slippage),
             bench_step(main, ai), regime_step(main_eq, bench, regimes)]
    cost_pct = steps[2].get("cost_pct")
    passed = sum(s["status"] == "pass" for s in steps)
    failed = [s for s in steps if s["status"] == "fail"]
    pending = [s for s in steps if s["status"] == "insufficient"]
    if failed:
        verdict = {"status": "fail", "title": f"{failed[0]['title'][2:]} — 아니오",
                   "message": failed[0]["detail"]}
    elif pending:
        verdict = {"status": "insufficient", "title": f"검증 진행 중 ({passed}/5 통과)",
                   "message": f"다음 확인: {pending[0]['title'][2:]} — {pending[0]['detail']}"}
    else:
        verdict = {"status": "pass", "title": "5단계 모두 통과", "message": "비용 후 벤치마크 초과가 여러 국면에서 유지"}
    return {"headline": {"excess": main["excess"] if main else None, "alpha_ann": main["alpha_ann"] if main else None,
                         "book": main["book"] if main else _ret(_daily(main_eq)), "bench": main["bench"] if main else None,
                         "ir": main["ir"] if main else None, "days": main["days"] if main else len(_daily(main_eq)),
                         "ai_excess": ai["excess"] if ai else None},
            "curve": main["curve"] if main else [], "ai_curve": ai["curve"] if ai else [],
            "steps": steps, "passed": passed, "verdict": verdict,
            "attribution": attribution(main, books, bench, cost_pct)}


__all__ = ["net_alpha", "alpha_stats", "hit_step", "money_step", "cost_step", "bench_step", "regime_step"]

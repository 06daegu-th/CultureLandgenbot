"""AI 성적표 — 종목별 또는 전체. 최근 50 / 100 / 300회.

  적중률      방향(상승 확률 ≥50% ↔ 실제 상승)이 맞은 비율 + 95% 구간
  Brier       확률 오차 (0 이 완벽, 0.25 = 동전) · Brier Skill = 1 − Brier/기준 Brier (기준: 늘 '평균 상승 비율'이라고 말하기)
              → 0 보다 작으면 "그냥 평균을 말하는 것보다 못함"
  보정        확률 구간별 예측 평균 vs 실제 상승 비율 (70% 라고 한 것들이 실제로 70% 올랐나)
  실제 수익   AI 가 BUY 라고 한 뒤 실제 수익률 평균 · 전체 평균(그냥 들고 있기)
  AI Alpha    'AI 가 오른다고 본 것만 샀을 때' 평균 − '전부 샀을 때' 평균 (%p, 판단 1건당) — 음수면 AI 선택이 손해
숫자가 나빠도 그대로 보여준다 (검증 전 AI 를 좋아 보이게 만들지 않는다).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from .asof import label
from .review.evaluator import wilson

WINDOWS = (50, 100, 300)


def _card(rows: list) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    p = np.array([r.prob_up for r in rows], dtype=float)
    ret = np.array([r.realized_return for r in rows], dtype=float)
    y = (ret > 0).astype(float)
    hit = float(np.mean((p >= 0.5) == (y == 1)))
    ci = wilson(int(round(hit * n)), n)
    brier = float(np.mean((p - y) ** 2))
    base = float(y.mean())
    ref = float(np.mean((base - y) ** 2))
    skill = 1 - brier / ref if ref > 0 else None
    bins = []
    for lo, hi in ((0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 1.01)):
        m = (p >= lo) & (p < hi)
        if m.sum() >= 3:
            bins.append({"range": f"{lo:.0%}~{min(hi, 1):.0%}", "n": int(m.sum()), "predicted": round(float(p[m].mean()), 3),
                         "actual": round(float(y[m].mean()), 3)})
    buy = np.array([r.action == "BUY" for r in rows])
    picked = p >= 0.5
    alpha = float(ret[picked].mean() - ret.mean()) if picked.any() and (~picked).any() else None
    return {"n": n, "hit": round(hit, 3), "ci95": [round(ci[0], 3), round(ci[1], 3)] if ci else None,
            "base_up": round(base, 3), "brier": round(brier, 4), "brier_ref": round(ref, 4),
            "brier_skill": None if skill is None else round(skill, 3), "calibration": bins,
            "ret_all": round(float(ret.mean()), 4), "ret_buy": round(float(ret[buy].mean()), 4) if buy.any() else None,
            "n_buy": int(buy.sum()), "ai_alpha": None if alpha is None else round(alpha, 4),
            "verdict": _verdict(hit, ci, skill, alpha, n, base)}


def _verdict(hit, ci, skill, alpha, n, base) -> str:
    if n < 30:
        return f"표본 {n}건 — 판단 보류 (30건부터)"
    bits = []
    if ci and ci[0] > max(0.5, base):
        bits.append("방향 적중이 기준보다 유의하게 높음")
    elif ci and ci[1] < max(0.5, base):
        bits.append("방향 적중이 기준보다 유의하게 낮음")
    else:
        bits.append("방향 적중은 우연과 구분 안 됨")
    if skill is not None:
        bits.append("확률이 평균보다 정확" if skill > 0.02 else "확률이 '평균 말하기'보다 못함" if skill < -0.02 else "확률 정확도 평균 수준")
    if alpha is not None:
        bits.append(f"AI 선택 {'이득' if alpha > 0 else '손해'} {alpha * 100:+.2f}%p/건")
    return " · ".join(bits)


def scorecard(app, symbol: str | None = None, horizon_note: str = "5거래일") -> dict:
    from .data.db import session_scope
    from .data.models import ConsensusRecord
    with session_scope(app.engine) as s:
        q = select(ConsensusRecord.as_of, ConsensusRecord.action, ConsensusRecord.prob_up, ConsensusRecord.realized_return) \
            .where(ConsensusRecord.realized_return.is_not(None))
        if symbol:
            q = q.where(ConsensusRecord.symbol == symbol)
        rows = s.execute(q.order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()).limit(max(WINDOWS))).all()
        flt = [ConsensusRecord.symbol == symbol] if symbol else []
        total = s.scalar(select(func.count()).select_from(ConsensusRecord).where(*flt)) or 0
        n_scored = s.scalar(select(func.count()).select_from(ConsensusRecord).where(ConsensusRecord.realized_return.is_not(None), *flt)) or 0
    cards = {str(w): _card(rows[:w]) for w in WINDOWS if len(rows) >= min(w, 1)}
    main = cards.get("100") if len(rows) >= 100 else cards.get(str(max((w for w in WINDOWS if w <= len(rows)), default=50)))
    return {"symbol": symbol, "n_scored": int(n_scored), "n_total": int(total), "windows": cards, "main": main or _card(rows),
            "last": label(rows[0].as_of) if rows else None, "horizon": horizon_note,
            "note": "적중·Brier·Alpha 는 결과가 확정된 판단만 · 숫자가 나빠도 그대로 표시"}


def _ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """기대 보정 오차 (Expected Calibration Error) — 확률 구간별 |예측 − 실제| 의 가중 평균."""
    e, n = 0.0, len(p)
    for i in range(bins):
        m = (p >= i / bins) & (p < (i + 1) / bins if i < bins - 1 else p <= 1)
        if m.any():
            e += m.sum() / n * abs(p[m].mean() - y[m].mean())
    return float(e)


def verify_now(app, symbol: str, prob: float | None = None, band: float = 0.05) -> dict:
    """'AI 가 분석했으니 오릅니다' 대신: 지금 확률과 같은 구간(±5%p)의 과거 판단들이 실제로 얼마나 올랐나."""
    from .data.db import session_scope
    from .data.models import ConsensusRecord
    from .pipeline import latest_consensus
    if prob is None:
        rec = latest_consensus(app.engine, symbol)
        if rec is None:
            return {"symbol": symbol, "error": "이 종목 AI 판단 없음"}
        prob = rec.prob_up
    with session_scope(app.engine) as s:
        rows = s.execute(select(ConsensusRecord.prob_up, ConsensusRecord.realized_return, ConsensusRecord.symbol, ConsensusRecord.as_of)
                         .where(ConsensusRecord.realized_return.is_not(None), ConsensusRecord.prob_up >= prob - band,
                                ConsensusRecord.prob_up <= prob + band).order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()).limit(5000)).all()
        recent = s.execute(select(ConsensusRecord.prob_up, ConsensusRecord.realized_return).where(ConsensusRecord.realized_return.is_not(None))
                           .order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()).limit(100)).all()
    n = len(rows)
    up = float(np.mean([r.realized_return > 0 for r in rows])) if n else None
    ci = wilson(int(round((up or 0) * n)), n) if n else None
    gap = None if up is None else up - prob
    cal = None if up is None else ("GOOD" if abs(gap) <= 0.05 else "FAIR" if abs(gap) <= 0.10 else "POOR")
    same = [r for r in rows if r.symbol == symbol]
    hit = lambda rs: float(np.mean([(r.prob_up >= 0.5) == (r.realized_return > 0) for r in rs])) if rs else None  # noqa: E731
    from . import ops
    dec = ops.get_state(app.engine, "model_decay").get("consensus") or {}
    return {"symbol": symbol, "prob": round(prob, 3), "band": band, "similar_n": n, "similar_up": None if up is None else round(up, 3),
            "ci95": [round(ci[0], 3), round(ci[1], 3)] if ci else None, "calibration": cal, "gap": None if gap is None else round(gap, 3),
            "same_symbol_n": len(same), "same_symbol_up": round(float(np.mean([r.realized_return > 0 for r in same])), 3) if same else None,
            "recent100_hit": None if not recent else round(hit(recent), 3), "recent30_hit": None if not recent else round(hit(recent[:30]), 3),
            "model_status": {"decaying": "🔴 성능 저하", "watch": "🟡 관찰", "stable": "🟢 정상"}.get(dec.get("status"), "⚪ 판단 전"),
            "sentence": (f"이 판단(상승 {prob:.0%})과 비슷한 과거 {n}회 중 실제 상승 {up:.1%}" + (f" (95% 구간 {ci[0]:.0%}~{ci[1]:.0%})" if ci else "")
                         if n else "비슷한 확률의 과거 판단이 아직 채점되지 않았습니다")}


def public_report(app, n: int = 1000) -> dict:
    """공개 가능한 AI 성적표: 좋은 숫자만 고르지 않는다 — 최근 N건 전부 + 실패 사례 + 90일 안정성."""
    from . import ops
    from .data.db import session_scope
    from .data.models import ConsensusRecord, Instrument
    with session_scope(app.engine) as s:
        rows = s.execute(select(ConsensusRecord.id, ConsensusRecord.as_of, ConsensusRecord.symbol, ConsensusRecord.action, ConsensusRecord.prob_up,
                                ConsensusRecord.realized_return, ConsensusRecord.payload)
                         .where(ConsensusRecord.realized_return.is_not(None)).order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()).limit(n)).all()
        names = {i.symbol: i.name for i in s.scalars(select(Instrument))}
    if not rows:
        return {"n": 0, "message": "채점된 예측 없음"}
    p = np.array([r.prob_up for r in rows], dtype=float)
    ret = np.array([r.realized_return for r in rows], dtype=float)
    y = (ret > 0).astype(float)
    card = _card(rows)
    ece = _ece(p, y)
    # AI 를 따라갔을 때(오른다고 본 것만 동일 비중 보유) 일별 수익 → 누적 · MDD (예측 기간이 겹치므로 근사)
    df = pd.DataFrame({"d": [pd.Timestamp(r.as_of).normalize() for r in rows], "r": ret, "pick": p >= 0.5})
    daily = df.groupby("d").apply(lambda g: g.loc[g["pick"], "r"].mean() - g["r"].mean() if g["pick"].any() else 0.0, include_groups=False).sort_index()
    h = int(np.median([(r.payload or {}).get("horizon") or 5 for r in rows]))
    eq = (1 + daily / max(h, 1)).cumprod()
    mdd = float((eq / eq.cummax() - 1).min()) if len(eq) else None
    dec = ops.get_state(app.engine, "model_decay").get("consensus") or {}
    worst = sorted(rows, key=lambda r: (r.prob_up - 0.5) * r.realized_return)[:8]
    return {"n": len(rows), "from": str(pd.Timestamp(rows[-1].as_of).date()), "to": str(pd.Timestamp(rows[0].as_of).date()),
            "direction_accuracy": card["hit"], "ci95": card["ci95"], "base_up": card["base_up"], "brier": card["brier"],
            "brier_skill": card["brier_skill"], "calibration_pct": round((1 - ece) * 100, 1), "ece": round(ece, 4),
            "net_alpha_per_pred": card["ai_alpha"], "alpha_cum": round(float(eq.iloc[-1] - 1), 4) if len(eq) else None, "mdd": None if mdd is None else round(mdd, 4),
            "stability_90d": {"decaying": "🔴 성능 저하", "watch": "🟡 관찰", "stable": "🟢 Stable"}.get(dec.get("status"), "⚪ 판단 전"),
            "stability_msg": dec.get("message"), "verdict": card["verdict"], "calibration": card["calibration"],
            "failures": [{"id": r.id, "at": label(r.as_of, with_time=False), "symbol": r.symbol, "name": names.get(r.symbol, r.symbol),
                          "action": r.action, "prob_up": round(r.prob_up, 3), "realized": round(r.realized_return, 4)} for r in worst],
            "method": f"방향 = 상승확률 ≥50% ↔ {h}거래일 뒤 실제 상승 · Brier = 확률 오차 · Calibration = 1 − 기대 보정 오차 · "
                      "Net Alpha = 오른다고 본 것만 샀을 때 − 전부 샀을 때 (판단 1건당, 비용 전) · MDD = 그 차이를 날마다 누적한 곡선의 최대 낙폭 (근사)"}


REGIME_GROUP = {"bull_quiet": "상승장", "bull_volatile": "상승장", "sideways": "횡보장", "bear_quiet": "하락장", "bear_volatile": "하락장",
                "crisis": "하락장"}


def _roundtrip_cost(app, sym: str) -> float:
    """왕복 비용 (수익률). 국내: 수수료×2 + 미끄러짐×2 + 매도세 · 미국: 수수료×2 + 미끄러짐×2 (환전은 제외)."""
    import os
    c = app.settings.costs
    if sym[:1].isdigit():
        return (2 * c.commission_bps + 2 * c.slippage_bps + c.sell_tax_bps) / 1e4
    try:
        us = float(os.environ.get("QUANT_US_COMMISSION_BPS") or 25)
    except ValueError:
        us = 25.0
    return (2 * us + 2 * c.slippage_bps) / 1e4


def plain(app, n: int = 100, symbol: str | None = None) -> dict:
    """일반 사용자용 성적표: '최근 100회 중 63회 맞음' · 'AI 가 BUY 한 것만 샀다면 비용 뒤 +1.2% · 같은 기간 지수 +0.8%' ·
    틀린 사례(예상 → 실제 · 원인 후보) · 상승장/횡보장/하락장별 적중."""
    from . import ops
    from .data.db import session_scope
    from .data.models import ConsensusRecord, Instrument
    from .engines.features import forward_return
    q = select(ConsensusRecord.id, ConsensusRecord.as_of, ConsensusRecord.symbol, ConsensusRecord.action, ConsensusRecord.prob_up,
               ConsensusRecord.realized_return, ConsensusRecord.correct, ConsensusRecord.payload).where(ConsensusRecord.realized_return.is_not(None))
    if symbol:
        q = q.where(ConsensusRecord.symbol == symbol)
    with session_scope(app.engine) as s:
        rows = s.execute(q.order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()).limit(n)).all()
        names = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_({r.symbol for r in rows} or {""})))}
    if not rows:
        return {"n": 0, "headline": "아직 채점된 AI 판단이 없습니다 — 판단 뒤 5거래일이 지나야 맞았는지 알 수 있습니다"}
    _, benches = app._all_bars()
    bfwd: dict[tuple[str, int], pd.Series] = {}

    def bench_ret(sym, as_of, h):
        key = ("KR" if sym[:1].isdigit() else "US", h)
        if key not in bfwd:
            b = benches.get(key[0])
            if b is None or not len(b):
                bfwd[key] = None
            else:
                b = b if "open" in b else b.assign(open=b["close"])
                bfwd[key] = forward_return(b, h).dropna()
        ser = bfwd[key]
        if ser is None or ser.empty:
            return None
        ts = pd.Timestamp(as_of)
        ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
        idx = ser.index if ser.index.tz is not None else ser.index.tz_localize("UTC")
        k = int(idx.searchsorted(ts, side="right")) - 1
        return float(ser.iloc[k]) if k >= 0 else None
    hits = sum(1 for r in rows if r.correct)
    ups = sum(1 for r in rows if r.realized_return > 0)
    picks = [r for r in rows if r.action == "BUY"] or [r for r in rows if r.prob_up >= 0.5]
    pick_kind = "BUY 라고 한" if any(r.action == "BUY" for r in rows) else "'오른다'고 본"
    money = None
    if picks:
        raw = [r.realized_return for r in picks]
        net = [r.realized_return - _roundtrip_cost(app, r.symbol) for r in picks]
        bm = [bench_ret(r.symbol, r.as_of, int((r.payload or {}).get("horizon") or 5)) for r in picks]
        pairs = [(a, b) for a, b in zip(net, bm, strict=True) if b is not None]
        avg_b = float(np.mean([b for _, b in pairs])) if pairs else None
        excess = float(np.mean([a - b for a, b in pairs])) if pairs else None
        money = {"kind": pick_kind, "n": len(picks), "avg_raw": round(float(np.mean(raw)), 4), "avg_net": round(float(np.mean(net)), 4),
                 "avg_bench": None if avg_b is None else round(avg_b, 4), "excess": None if excess is None else round(excess, 4),
                 "win": round(sum(1 for x in net if x > 0) / len(net), 3),
                 "text": (f"AI 가 {pick_kind} {len(picks)}번을 그대로 샀다면: 평균 {np.mean(raw):+.1%} → 비용 뒤 {np.mean(net):+.1%}"
                          + (f" · 같은 기간 지수 {avg_b:+.1%} → 지수보다 {excess * 100:+.1f}%p" if excess is not None else " · 지수 비교 불가 (지수 데이터 없음)")),
                 "earned": None if excess is None else excess > 0}
    lab = {x["id"]: x for x in (ops.get_state(app.engine, "failure_lab").get("losses") or [])}
    wrong = sorted([r for r in rows if not r.correct], key=lambda r: -abs(r.realized_return))[:3]
    fails = []
    for r in wrong:
        p = r.payload or {}
        h = int(p.get("horizon") or 5)
        why = list((lab.get(r.id) or {}).get("why") or [])
        if not why:
            b = bench_ret(r.symbol, r.as_of, h)
            if b is not None and abs(b) >= 0.02 and np.sign(b) == np.sign(r.realized_return):
                why.append(f"시장 전체 {'하락' if b < 0 else '상승'} ({b:+.1%})")
            why = why or ["뚜렷한 외부 원인 없음 — 모델 자체 오류 가능성"]
        exp = p.get("expected_return")
        fails.append({"id": r.id, "symbol": r.symbol, "name": names.get(r.symbol, r.symbol), "at": label(r.as_of, with_time=False),
                      "action": r.action, "expected": exp, "actual": round(r.realized_return, 4), "why": why[:3],
                      "text": (f"예상 {exp:+.1%}" if isinstance(exp, (int, float)) else f"{r.action} (상승 {r.prob_up:.0%})")
                              + f" → 실제 {r.realized_return:+.1%} · 원인 후보: {', '.join(why[:2])}"})
    by_regime = {}
    for r in rows:
        g = REGIME_GROUP.get((r.payload or {}).get("regime"))
        if g:
            by_regime.setdefault(g, []).append(bool(r.correct))
    regimes = [{"regime": g, "n": len(v), "hit": round(sum(v) / len(v), 3)} for g, v in by_regime.items()]
    regimes.sort(key=lambda x: -x["hit"])
    good = [x for x in regimes if x["n"] >= 10]
    strong = good[0] if good and good[0]["hit"] >= 0.55 else None
    weak = good[-1] if good and good[-1]["hit"] < 0.5 and good[-1] is not strong else None
    alpha = None
    xs = [((r.payload or {}).get("outcomes") or {}).get("excess") for r in rows]
    pairs = [(r.prob_up >= 0.5, float(x)) for r, x in zip(rows, xs, strict=True) if x is not None]
    if pairs:  # v20: 시장 대비 채점 — '지수보다 더 오른다' 를 맞혔나 (시장 방향 덕분의 적중을 걷어낸다)
        ahit = sum(1 for up, x in pairs if up == (x > 0))
        abase = sum(1 for _, x in pairs if x > 0)
        alpha = {"n": len(pairs), "hits": ahit, "base_hits": abase, "hit_rate": round(ahit / len(pairs), 3),
                 "beat_base": ahit > max(abase, len(pairs) - abase),
                 "text": f"시장 대비로 채점하면 {len(pairs)}회 중 {ahit}회 적중 (그냥 '지수보다 더 오른다'고만 했으면 {abase}회 · "
                         f"반대로만 했으면 {len(pairs) - abase}회)"}
    by_mkt = {}
    for r in rows:
        by_mkt.setdefault("한국" if r.symbol[:1].isdigit() else "미국", []).append(bool(r.correct))
    return {"n": len(rows), "hits": hits, "base_hits": ups, "hit_rate": round(hits / len(rows), 3), "base_rate": round(ups / len(rows), 3),
            "headline": f"최근 {len(rows)}회 중 {hits}회 방향 적중" + (f" (그냥 '오른다'고만 했으면 {ups}회)" if ups else ""),
            "beat_base": hits > ups, "money": money, "alpha": alpha, "failures": fails, "regimes": regimes,
            "strong": None if not strong else f"{strong['regime']}에서 잘함 — 적중 {strong['hit']:.0%} ({strong['n']}회)",
            "weak": None if not weak else f"{weak['regime']}에서 약함 — 적중 {weak['hit']:.0%} ({weak['n']}회)",
            "markets": [{"market": k, "n": len(v), "hit": round(sum(v) / len(v), 3)} for k, v in by_mkt.items()],
            "from": label(rows[-1].as_of, with_time=False), "to": label(rows[0].as_of, with_time=False),
            "note": "방향 = 상승 확률 50% 이상이면 '오른다' · 비용 = 수수료·미끄러짐·세금 왕복 · 지수 = 같은 기간 시장 (코스피 대용 / 미국 지수)"}



def by_context(app, n: int = 500, min_n: int = 10) -> dict:
    """상황별 AI 성적 — 뉴스 유형별 · 실적 발표 전후 · 종목별 (채점 끝난 실제 전진 기록만, 표본 min_n 미만은 '표본 부족').
    근거는 판단 당시 저장된 증거(payload.evidence) — 지금 다시 계산하지 않는다 (사후 끼워 맞추기 방지)."""
    from collections import defaultdict

    from .data.db import session_scope
    from .data.models import ConsensusRecord, Instrument
    from .news_llm import EVENT_KO
    with session_scope(app.engine) as s:
        rows = s.execute(select(ConsensusRecord.symbol, ConsensusRecord.correct, ConsensusRecord.realized_return, ConsensusRecord.payload)
                         .where(ConsensusRecord.correct.is_not(None)).order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()).limit(n)).all()
        names = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_({r.symbol for r in rows} or {""})))}
    groups: dict[str, dict[str, list]] = {"news": defaultdict(list), "earnings": defaultdict(list), "symbol": defaultdict(list)}
    for r in rows:
        ev = (r.payload or {}).get("evidence") or {}
        cats = [e for it in ev.get("news") or [] for e in (it.get("events") or []) if e != "other"]
        top = max(set(cats), key=cats.count) if cats else None
        groups["news"][EVENT_KO.get(top, top) if top else "관련 뉴스 없음"].append(bool(r.correct))
        disc_e = any("earnings" in (d.get("events") or []) for d in ev.get("disclosures") or [])
        cal_e = any("실적" in str(e.get("title", e)) for e in ev.get("events") or [] if isinstance(e, dict | str))
        groups["earnings"]["실적 발표 전후" if (disc_e or cal_e or top == "earnings") else "평소"].append(bool(r.correct))
        groups["symbol"][names.get(r.symbol, r.symbol)].append(bool(r.correct))

    def table(g, k=8):
        out = [{"key": key, "n": len(v), "hit": round(sum(v) / len(v), 3), "enough": len(v) >= min_n} for key, v in g.items() if v]
        return sorted(out, key=lambda x: -x["n"])[:k]
    return {"n": len(rows), "min_n": min_n, "news": table(groups["news"]), "earnings": table(groups["earnings"]),
            "symbol": table(groups["symbol"], 10),
            "note": f"채점 끝난 최근 {len(rows)}건 · 표본 {min_n}건 미만은 우연일 수 있어 흐리게 표시 · 판단 당시 저장된 증거로 분류"}


__all__ = ["scorecard", "verify_now", "public_report", "plain", "by_context", "WINDOWS"]

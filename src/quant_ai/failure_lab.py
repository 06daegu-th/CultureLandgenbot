"""AI 가 틀리면 자동으로 연구 — 왜 틀렸나 (예측마다 원인 태그) + 반복되는 약점 찾기.

채점된 예측마다 그 기간(판단일 ~ +horizon 거래일)에 무슨 일이 있었는지 태그를 붙인다:
  시장     지수가 같은 방향으로 ±2% 넘게 움직임 (종목이 아니라 시장이 결정)
  실적     그 기간에 실적 발표
  뉴스     예측과 반대 톤의 뉴스가 나옴
  국면     시장 국면(regime)이 바뀜
  거래량   평소의 2배 넘는 거래량 폭증
  금리·환율 미 10년물 ±15bp 또는 원/달러 ±1.5% 이상 (거시 데이터 있을 때)
  데이터   2차 소스(보조 데이터)로 채운 봉이 기간에 있음
그 다음 태그별로 '태그가 있을 때 적중률' vs '없을 때' → 차이가 크고 표본이 충분하면 약점으로 보고:
  예) "최근 AI 가 실적 이벤트에서 성능 저하 — 실적 낀 예측 적중 38% (n=45) vs 나머지 55%"
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import ops
from .asof import label

TAGS = {"market": "시장 움직임", "earnings": "실적 발표", "news": "반대 뉴스", "regime": "국면 변화", "volume": "거래량 폭증",
        "macro": "금리·환율 급변", "data": "데이터 문제"}


def _earn_dates(app, sym: str) -> set[str]:
    prof = ops.get_state(app.engine, f"profile:{sym}").get("data") or {}
    d = {h["date"] for h in prof.get("earnings_history") or [] if h.get("date")}
    d |= {str(e.get("date"))[:10] for e in prof.get("events") or [] if e.get("kind") == "earnings" and e.get("date")}
    d |= {s["date"] for s in ops.get_state(app.engine, f"krcons:{sym}").get("surprises") or [] if s.get("date")}
    return d


def analyze(app, days: int = 180, limit: int = 3000, now: datetime | None = None) -> dict:
    from .data.db import session_scope
    from .data.models import ConsensusRecord, Instrument, NewsArticle, PriceBar
    from .data.sources import SECONDARY
    from .engines.market_intel import load_macro
    from .stockplus import tone
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    bars, benches = app._all_bars()
    with session_scope(app.engine) as s:
        rows = s.execute(select(ConsensusRecord.id, ConsensusRecord.symbol, ConsensusRecord.as_of, ConsensusRecord.prob_up,
                                ConsensusRecord.realized_return, ConsensusRecord.correct, ConsensusRecord.payload)
                         .where(ConsensusRecord.correct.is_not(None), ConsensusRecord.as_of >= since)
                         .order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()).limit(limit)).all()
        syms = sorted({r.symbol for r in rows})
        news = defaultdict(list)
        for n in s.scalars(select(NewsArticle).where(NewsArticle.published_at >= since - timedelta(days=1)).limit(50000)):
            for x in n.symbols or []:
                if x in syms:
                    news[x].append((n.published_at if n.published_at.tzinfo else n.published_at.replace(tzinfo=UTC), tone(n.sentiment)))
        sec_days = defaultdict(set)
        for sym, ts in s.execute(select(PriceBar.symbol, PriceBar.ts).where(PriceBar.source == SECONDARY, PriceBar.ts >= since)):
            sec_days[sym].add(pd.Timestamp(ts).date())
        names = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_(syms or [""])))}
        macro = load_macro(s, ["DGS10", "DEXKOUS"], days=days + 30)
    regimes = {}  # (날짜) → 국면: 합의 기록의 regime
    for r in rows:
        g = (r.payload or {}).get("regime")
        if g:
            regimes.setdefault(pd.Timestamp(r.as_of).date(), g)
    reg_days = sorted(regimes)
    earn = {sym: _earn_dates(app, sym) for sym in syms}
    items = []
    for r in rows:
        b = bars.get(r.symbol)
        h = int((r.payload or {}).get("horizon") or 5)
        t0 = pd.Timestamp(r.as_of)
        t0 = t0.tz_localize("UTC") if t0.tzinfo is None else t0  # SQLite 는 시간대를 버린다
        tags = set()
        if b is not None and len(b) > 30:
            idx = pd.DatetimeIndex(b.index)
            i = int(idx.searchsorted(t0.tz_convert(idx.tz) if idx.tz is not None else t0.tz_localize(None)))
            j = min(i + h, len(b) - 1)
            if i < len(b):
                d0, d1 = idx[i].date(), idx[j].date()
                bench = benches.get("KR" if r.symbol[:1].isdigit() else "US")
                if bench is not None:
                    bc = bench["close"].reindex(idx).ffill()
                    if pd.notna(bc.iloc[max(i - 1, 0)]) and pd.notna(bc.iloc[j]) and bc.iloc[max(i - 1, 0)] > 0:
                        mret = float(bc.iloc[j] / bc.iloc[max(i - 1, 0)] - 1)
                        if abs(mret) >= 0.02 and np.sign(mret) == np.sign(r.realized_return):
                            tags.add("market")
                if any(d0.isoformat() <= x <= d1.isoformat() for x in earn.get(r.symbol, ())):
                    tags.add("earnings")
                v = b["volume"].astype(float)
                base = v.iloc[max(i - 20, 0):i].mean()
                if base and v.iloc[i:j + 1].max() > 2 * base:
                    tags.add("volume")
                if any(d0 <= x <= d1 for x in sec_days.get(r.symbol, ())):
                    tags.add("data")
                opp = "부정" if r.prob_up >= 0.5 else "긍정"
                t0a = t0.to_pydatetime()
                if any(t0a <= t <= t0a + timedelta(days=h * 1.5 + 2) and tn == opp for t, tn in news.get(r.symbol, ())):
                    tags.add("news")
                g0 = regimes.get(d0) or (r.payload or {}).get("regime")
                later = next((regimes[x] for x in reg_days if x >= d1), None)
                if g0 and later and later != g0:
                    tags.add("regime")
                for key, thr in (("DGS10", 0.15), ("DEXKOUS", None)):
                    ser = macro.get(key)
                    if ser is None or len(ser) < 2:
                        continue
                    sub = ser[(ser.index.date >= d0) & (ser.index.date <= d1)] if hasattr(ser.index, "date") else ser
                    if len(sub) >= 2:
                        chg = float(sub.iloc[-1] - sub.iloc[0]) if thr else float(sub.iloc[-1] / sub.iloc[0] - 1)
                        if (thr and abs(chg) >= thr) or (not thr and abs(chg) >= 0.015):
                            tags.add("macro")
        items.append({"id": r.id, "symbol": r.symbol, "at": pd.Timestamp(r.as_of), "prob": r.prob_up, "ret": r.realized_return,
                      "correct": bool(r.correct), "tags": sorted(tags)})
    n = len(items)
    if not n:
        return {"n": 0, "message": "채점된 예측이 없습니다", "findings": [], "by_tag": [], "losses": []}
    overall = float(np.mean([x["correct"] for x in items]))
    by_tag, findings = [], []
    for t, name in TAGS.items():
        w = [x for x in items if t in x["tags"]]
        wo = [x for x in items if t not in x["tags"]]
        if not w:
            by_tag.append({"tag": t, "name": name, "n": 0})
            continue
        hw, hwo = float(np.mean([x["correct"] for x in w])), float(np.mean([x["correct"] for x in wo])) if wo else None
        se = np.sqrt(overall * (1 - overall) * (1 / len(w) + 1 / max(len(wo), 1))) if wo else None
        z = (hw - hwo) / se if se else None
        by_tag.append({"tag": t, "name": name, "n": len(w), "share": round(len(w) / n, 3), "hit_with": round(hw, 3),
                       "hit_without": None if hwo is None else round(hwo, 3), "z": None if z is None else round(float(z), 2)})
        if z is not None and z <= -2 and len(w) >= 20 and hwo - hw >= 0.05:
            findings.append({"tag": t, "text": f"{name}이(가) 낀 예측에서 성능 저하 — 적중 {hw:.0%} (n={len(w)}) vs 나머지 {hwo:.0%} (z={z:.1f})",
                             "action": {"earnings": "실적 발표 D-3 이내 예측은 확률을 줄이거나 NO TRADE 규칙 검토",
                                        "market": "시장 전체 방향(지수 예측)을 먼저 보는 국면 AI 가중치 점검",
                                        "news": "뉴스 반영 속도 — 장중 이벤트 재분석 주기 단축 검토",
                                        "regime": "국면 전환 감지 후 기존 예측 무효화 규칙 검토",
                                        "volume": "거래량 급증 종목은 방향 예측 신뢰도를 낮추는 규칙 검토",
                                        "macro": "금리·환율 급변 구간 가중치 점검", "data": "보조 데이터 구간은 판단 제외 검토"}[t]})
        elif z is not None and z >= 2 and len(wo) >= 20 and hwo is not None and hwo < 0.5:
            findings.append({"tag": f"no_{t}", "text": f"{name}이(가) 없는 구간에서 적중 {hwo:.0%} (n={len(wo)}) — 있을 때 {hw:.0%}. "
                             + ("AI 적중이 대부분 '시장 방향 맞히기'에서 나옴 — 시장이 조용할 때 종목 고르는 힘은 동전보다 못함" if t == "market"
                                else f"AI 가 {name}에 기대고 있음"),
                             "action": ("종목 선택력 검증: 시장 대비 초과수익(알파) 기준 채점을 따로 보고, 국면 AI 가 조용한 장에서는 거래를 줄이는 규칙 검토"
                                        if t == "market" else f"{name} 없는 구간의 판단 신뢰도를 낮추는 규칙 검토")})
    wrong = sorted([x for x in items if not x["correct"]], key=lambda x: -abs(x["ret"]))[:15]
    losses = [{"id": x["id"], "symbol": x["symbol"], "name": names.get(x["symbol"], x["symbol"]), "at": label(x["at"], with_time=False),
               "predicted": "UP" if x["prob"] >= 0.5 else "DOWN", "prob": round(x["prob"], 3), "actual": round(x["ret"], 4),
               "why": [TAGS[t] for t in x["tags"]] or ["뚜렷한 외부 원인 없음 — 모델 자체 오류 가능성"]} for x in wrong]
    out = {"at": now.isoformat(), "as_of": label(now), "n": n, "days": days, "hit": round(overall, 3), "by_tag": by_tag,
           "findings": findings, "losses": losses,
           "untagged_wrong": round(float(np.mean([not x["tags"] for x in items if not x["correct"]])), 3) if any(not x["correct"] for x in items) else None,
           "note": "태그는 원인 '후보'다 (같은 기간에 있었다는 뜻) — 인과를 증명하지 않는다. 약점 = 태그 있을 때 적중이 유의하게(z≤−2) 낮고 표본 20건 이상."}
    ops.set_state(app.engine, "failure_lab", {k: v for k, v in out.items() if k != "losses"} | {"losses": losses[:8]})
    if findings:
        from .alerts import push
        for f in findings:
            push(app.engine, "market", "AI 약점 발견", f["text"][:200], level="warn", link="#ailab",
                 dedupe=f"flab:{f['tag']}:{now.date().isoformat()[:7]}")
    return out


__all__ = ["analyze", "TAGS"]

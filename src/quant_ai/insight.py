"""실적 분석 페이지 · 뉴스 → 종목 영향 연결 (저장된 기록만).

earnings(sym)   예상 EPS · 실제 EPS · 서프라이즈 · 매출 서프라이즈(국내 컨센서스) · 가이던스(무료 소스 없음 → 명시) ·
                발표 후 주가 반응(시장 대비 다음 날 · 20일 표류) · 상회/하회 때 평균 반응 · 다음 발표일
news_impact(id) 뉴스 하나 → 관련 종목 · 업종 · 그 종목의 지금 AI 판단 · 과거 유사 뉴스(같은 이벤트 유형·같은 톤) 뒤
                평균 움직임(1일·5일, 시장 대비)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import ops
from .asof import label


def earnings(app, symbol: str) -> dict:
    from .engines.earnings import reactions
    bars, benches = app._all_bars()
    b = bars.get(symbol)
    kr = symbol[:1].isdigit()
    bench = benches.get("KR" if kr else "US")
    prof = ops.get_state(app.engine, f"profile:{symbol}").get("data") or {}
    rows = []
    for h in prof.get("earnings_history") or []:
        rows.append({"date": h["date"], "period": None, "eps_estimate": h.get("eps_estimate"), "eps_actual": h.get("eps_actual"),
                     "eps_surprise_pct": h.get("surprise_pct"), "source": "Yahoo"})
    kc = ops.get_state(app.engine, f"krcons:{symbol}")
    for s_ in kc.get("surprises") or []:
        rows.append({"date": s_["date"], "period": s_.get("label") or s_.get("period"),
                     "eps_estimate": s_.get("eps_estimate"), "eps_actual": s_.get("eps_actual"), "eps_surprise_pct": s_.get("eps_surprise_pct"),
                     "revenue_estimate": s_.get("revenue_estimate"), "revenue_actual": s_.get("revenue_actual"),
                     "revenue_surprise_pct": s_.get("revenue_surprise_pct"), "op_surprise_pct": s_.get("op_income_surprise_pct"),
                     "base_metric": s_.get("metric"), "base_surprise_pct": s_.get("surprise_pct"), "source": "네이버 컨센서스 스냅샷"})
    for r in rows:
        rx = reactions(b, bench, date.fromisoformat(r["date"])) if b is not None else None
        r["reaction_1d"] = round(rx["reaction"], 4) if rx else None
        r["drift_20d"] = round(rx["drift"], 4) if rx else None
        r["pre_20d"] = round(rx["pre20"], 4) if rx else None
        sp = r.get("eps_surprise_pct") if r.get("eps_surprise_pct") is not None else r.get("base_surprise_pct")
        r["beat"] = None if sp is None else sp > 0
    rows.sort(key=lambda r: r["date"], reverse=True)
    beat = [r["reaction_1d"] for r in rows if r["beat"] and r["reaction_1d"] is not None]
    miss = [r["reaction_1d"] for r in rows if r["beat"] is False and r["reaction_1d"] is not None]
    upcoming = next((e for e in sorted(prof.get("events") or [], key=lambda e: str(e.get("date")))
                     if e.get("kind") == "earnings" and str(e.get("date"))[:10] >= datetime.now(UTC).date().isoformat()), None)
    q = prof.get("quarterly") or []
    growth = None
    if len(q) >= 5 and q[-1].get("revenue") and q[-5].get("revenue"):
        growth = round(q[-1]["revenue"] / q[-5]["revenue"] - 1, 4)
    return {"symbol": symbol, "rows": rows[:12], "n": len(rows),
            "beat_rate": round(sum(1 for r in rows if r["beat"]) / sum(1 for r in rows if r["beat"] is not None), 3) if any(r["beat"] is not None for r in rows) else None,
            "avg_reaction_beat": round(float(np.mean(beat)), 4) if beat else None, "avg_reaction_miss": round(float(np.mean(miss)), 4) if miss else None,
            "upcoming": {"date": str(upcoming.get("date"))[:10], "eps_estimate": upcoming.get("eps_estimate"), "time": upcoming.get("time"),
                         "estimated": bool(upcoming.get("estimated"))} if upcoming else None,
            "kr_upcoming": kc.get("upcoming"), "quarterly": q[-6:], "revenue_yoy": growth,
            "guidance": "무료 공개 소스에 가이던스(회사 전망치) 데이터가 없어 표시하지 않습니다 — 실적 발표 자료·공시 원문 확인",
            "note": "반응 = 발표일 전날 종가 → 다음 날 종가, 시장(지수) 대비 · 발표 시각(장전/장후)을 모르면 하루 오차가 있을 수 있음",
            "sources": sorted({r["source"] for r in rows}) or ["없음 — 종목 상세(Yahoo)·국내 컨센서스 수집 후 채워짐"]}


def _aware(t):
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def news_impact(app, news_id: int, now: datetime | None = None) -> dict:
    from .data.db import session_scope
    from .data.models import Instrument, NewsArticle
    from .engines.sector import sector_map
    from .explain import for_symbol
    from .stockplus import tone
    now = now or datetime.now(UTC)
    with session_scope(app.engine) as s:
        n = s.get(NewsArticle, int(news_id))
        if n is None:
            return {"error": "뉴스 없음"}
        art = {"id": n.id, "title": n.title, "source": n.source, "url": n.url, "at": label(n.published_at), "sent": n.sentiment or 0.0,
               "tone": tone(n.sentiment), "events": n.events or [], "symbols": n.symbols or [], "importance": n.importance}
        pub = _aware(n.published_at)
        names = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_(art["symbols"] or [""])))}
        past = [(p.id, _aware(p.published_at), p.symbols or [], p.sentiment or 0.0, p.events or [], p.title) for p in s.scalars(
            select(NewsArticle).where(NewsArticle.published_at < pub - timedelta(days=1), NewsArticle.published_at >= pub - timedelta(days=720))
            .limit(20000))]
    sectors = sector_map(app.engine)
    bars, benches = app._all_bars()
    ev = set(art["events"])

    def move(sym, t, h):
        b = bars.get(sym)
        if b is None or len(b) < h + 2:
            return None
        idx = pd.DatetimeIndex(b.index)
        ts = pd.Timestamp(t).tz_convert(idx.tz) if idx.tz is not None else pd.Timestamp(t)
        i = int(idx.searchsorted(ts))
        if i < 1 or i + h - 1 >= len(b):
            return None
        r = float(b["close"].iloc[i + h - 1] / b["close"].iloc[i - 1] - 1)
        bench = benches.get("KR" if sym[:1].isdigit() else "US")
        if bench is not None:
            bi = bench["close"].reindex(idx).ffill()
            if pd.notna(bi.iloc[i - 1]) and pd.notna(bi.iloc[i + h - 1]) and bi.iloc[i - 1] > 0:
                r -= float(bi.iloc[i + h - 1] / bi.iloc[i - 1] - 1)
        return r
    related = []
    for sym in art["symbols"][:6]:
        ex = for_symbol(app, sym)
        sec = sectors.get(sym)
        sim = [(pid, t, title) for pid, t, syms, sv, evs, title in past
               if tone(sv) == art["tone"] and (set(evs) & ev if ev else True) and (sym in syms or (sec and any(sectors.get(x) == sec for x in syms)))]
        m1 = [x for x in (move(sym, t, 1) for _, t, _ in sim) if x is not None]
        m5 = [x for x in (move(sym, t, 5) for _, t, _ in sim) if x is not None]
        after = {"1d": move(sym, pub, 1), "5d": move(sym, pub, 5)}
        change = _ai_change(app, sym, pub)
        related.append({"symbol": sym, "name": names.get(sym, sym), "sector": sec or "미분류",
                        "ai": {"action": ex["action"], "headline": ex["headline"], "as_of": ex["as_of"]} if ex else None,
                        "similar_n": len(m1), "similar_avg_1d": round(float(np.mean(m1)), 4) if len(m1) >= 3 else None,
                        "similar_avg_5d": round(float(np.mean(m5)), 4) if len(m5) >= 3 else None,
                        "similar_up_share": round(float(np.mean([x > 0 for x in m1])), 3) if len(m1) >= 3 else None,
                        "examples": [{"title": title[:70], "at": label(t, with_time=False)} for _, t, title in sim[:3]],
                        "after_this": {k: None if v is None else round(v, 4) for k, v in after.items()}, "ai_change": change})
    peers = sorted({x for sym in art["symbols"] for x, sc in sectors.items() if sc and sc == sectors.get(sym) and x not in art["symbols"]})[:8]
    return {"news": art, "related": related, "sector_peers": [{"symbol": p, "name": p} for p in peers],
            "note": "유사 뉴스 = 같은 톤 + (같은 이벤트 유형) + 같은 종목 또는 같은 업종 · 움직임은 시장 대비 · 인과가 아니라 과거 평균"}


RISK_OF = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}


def _ai_change(app, symbol: str, pub: datetime) -> dict | None:
    """뉴스 직전 판단 → 뉴스 뒤 첫 판단: AI 점수(상승 확률×100) · 신호 · 위험(의견 충돌·거부권)."""
    from .data.db import session_scope
    from .data.models import ConsensusRecord
    with session_scope(app.engine) as s:
        before = s.scalar(select(ConsensusRecord).where(ConsensusRecord.symbol == symbol, ConsensusRecord.as_of < pub)
                          .order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()))
        after = s.scalar(select(ConsensusRecord).where(ConsensusRecord.symbol == symbol, ConsensusRecord.as_of >= pub)
                         .order_by(ConsensusRecord.as_of))
        if before is None and after is None:
            return None

        def risk(c):
            if c is None:
                return None
            return "HIGH" if (c.payload or {}).get("vetoes") else RISK_OF.get(c.conflict, "MEDIUM")
        out = {"before": None if before is None else {"score": round(before.prob_up * 100), "action": before.action, "risk": risk(before),
                                                        "at": label(before.as_of)},
               "after": None if after is None else {"score": round(after.prob_up * 100), "action": after.action, "risk": risk(after),
                                                      "at": label(after.as_of), "trigger": (after.payload or {}).get("trigger")}}
    if out["before"] and out["after"]:
        out["delta"] = out["after"]["score"] - out["before"]["score"]
        out["text"] = f"AI 점수 {out['before']['score']} → {out['after']['score']} · 신호 {out['before']['action']} → {out['after']['action']} · Risk {out['before']['risk']} → {out['after']['risk']}"
    else:
        out["text"] = "뉴스 이후 새 AI 판단 없음 (다음 판단 때 반영)" if out["before"] else "뉴스 이전 판단 없음"
    return out


def compare_extra(app, symbols: list[str]) -> dict:
    """비교 화면 보강: 성장률 · 밸류에이션(선행) · 수급 · 위험(VaR·MDD) · AI 성적."""
    from .scorecard import scorecard
    out = {}
    for sym in symbols:
        st = (ops.get_state(app.engine, f"profile:{sym}").get("data") or {}).get("stats") or {}
        fl = (ops.get_state(app.engine, f"flow:{sym}").get("summary") or {})
        sc = scorecard(app, sym)["main"]
        out[sym] = {"rev_growth": st.get("rev_growth"), "earn_growth": st.get("earn_growth"), "fwd_per": st.get("fwd_per"),
                    "roe": st.get("roe"), "op_margin": st.get("op_margin"),
                    "foreign_5d": fl.get("foreign_5d"), "inst_5d": fl.get("inst_5d"), "foreign_streak": fl.get("foreign_streak"),
                    "ai_hit": sc.get("hit"), "ai_brier_skill": sc.get("brier_skill"), "ai_alpha": sc.get("ai_alpha"), "ai_n": sc.get("n")}
    return out


__all__ = ["earnings", "news_impact", "compare_extra"]

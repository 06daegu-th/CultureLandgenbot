"""개인화 — 투자 성향 · 관심 시장/업종 · 목표 · 위험 한도에 따라 화면이 바뀐다 · 투자일지에서 자주 하는 실수 찾기.

profile   style: growth(성장) · dividend(배당) · value(가치) · balanced(균형)  markets: KR/US  sectors: [...]
          goal(자유 문장) · max_daily_loss(1일 최대 손실 %, 위험 한도)
discover  성향에 맞는 종목: 성장 → 모멘텀·매출 성장 · 배당 → 배당수익률·안정성(변동성 낮음) · 가치 → 낮은 PER·PBR
          + 관심 시장·업종 필터 + 지금 AI 판단
mistakes  내 저널(매매 기록)에서 반복되는 실수: 추격 매수 · AI 반대로 해서 틀림 · 실적 직전 매매 · 확신 과다 · 손실 종목 반복 매수
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select

from . import ops
from .asof import label

STYLES = {"growth": "성장", "dividend": "배당", "value": "가치", "balanced": "균형"}
KEY = "user_profile"


def get_profile(engine) -> dict:
    return ops.get_state(engine, KEY) or {}


def save_profile(engine, body: dict) -> dict:
    style = body.get("style") or "balanced"
    if style not in STYLES:
        raise ValueError("성향은 growth / dividend / value / balanced")
    markets = [m for m in body.get("markets") or ["KR", "US"] if m in ("KR", "US")] or ["KR"]
    sectors = [str(x)[:20] for x in body.get("sectors") or []][:10]
    mdl = body.get("max_daily_loss")
    mdl = None if mdl in (None, "") else float(mdl)
    if mdl is not None and not 0.2 <= mdl <= 20:
        raise ValueError("1일 최대 손실 한도는 0.2~20%")
    p = {"style": style, "markets": markets, "sectors": sectors, "goal": str(body.get("goal") or "")[:200],
         "max_daily_loss": mdl, "at": datetime.now(UTC).isoformat()}
    ops.set_state(engine, KEY, p)
    return p


def discover(app, limit: int = 8) -> dict:
    from .data.db import session_scope
    from .data.models import ConsensusRecord, Instrument, SystemState
    from .engines.sector import sector_map
    p = get_profile(app.engine)
    style = p.get("style", "balanced")
    markets = set(p.get("markets") or ["KR", "US"])
    want_sec = set(p.get("sectors") or [])
    bars, _ = app._all_bars()
    sectors = sector_map(app.engine)
    with session_scope(app.engine) as s:
        names = {i.symbol: i.name for i in s.scalars(select(Instrument))}
        latest = {}
        for c in s.scalars(select(ConsensusRecord).where(ConsensusRecord.as_of >= datetime.now(UTC) - timedelta(days=14))
                           .order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()).limit(5000)):
            latest.setdefault(c.symbol, c)
        stats = {r.key.split(":", 1)[1]: ((r.value or {}).get("data") or {}).get("stats") or {}
                 for r in s.scalars(select(SystemState).where(SystemState.key.like("profile:%")))}
    rows = []
    for sym, b in bars.items():
        if len(b) < 130 or sym in ("KOSPI", "KOSDAQ"):
            continue
        mkt = "KR" if sym[:1].isdigit() else "US"
        if mkt not in markets:
            continue
        sec = sectors.get(sym)
        if want_sec and sec not in want_sec:
            continue
        c = b["close"].astype(float)
        mom = float(c.iloc[-1] / c.iloc[-127] - 1)
        vol = float(c.pct_change().iloc[-120:].std() * np.sqrt(252))
        st = stats.get(sym, {})
        if style == "growth":
            score, why = mom + (st.get("rev_growth") or 0), f"6개월 {mom:+.0%}" + (f" · 매출 성장 {st['rev_growth']:+.0%}" if st.get("rev_growth") else "")
        elif style == "dividend":
            dy = st.get("div_yield") or 0
            if not dy:
                continue
            score, why = dy * 10 - vol, f"배당수익률 {dy:.1%} · 변동성 {vol:.0%}"
        elif style == "value":
            per = st.get("per") or st.get("fwd_per")
            if not per or per <= 0:
                continue
            score, why = -per - 5 * (st.get("pbr") or 1), f"PER {per:.1f}" + (f" · PBR {st['pbr']:.2f}" if st.get("pbr") else "")
        else:
            score, why = mom / max(vol, 0.1), f"6개월 {mom:+.0%} · 변동성 {vol:.0%} (위험 대비 성과)"
        ai = latest.get(sym)
        if ai is not None and ai.action in ("BUY",):
            score += 0.05
        rows.append({"symbol": sym, "name": names.get(sym, sym), "sector": sec or "미분류", "market": mkt, "score": round(score, 4), "why": why,
                     "ai": ai.action if ai else None, "prob_up": round(ai.prob_up, 3) if ai else None})
    rows.sort(key=lambda x: -x["score"])
    return {"profile": p or None, "style": STYLES[style], "rows": rows[:limit],
            "note": ("성향을 설정하지 않아 '균형'(위험 대비 성과)으로 보여줍니다" if not p else f"{STYLES[style]} 성향 · 관심 시장 {', '.join(sorted(markets))}")
            + " · 추천이 아니라 조건에 맞는 종목 목록입니다"}


def mistakes(app, days: int = 180) -> dict:
    """내 저널에서 자주 하는 실수 (채점된 기록 기준)."""
    from .data.db import session_scope
    from .data.models import UserJournal
    since = datetime.now(UTC) - timedelta(days=days)
    with session_scope(app.engine) as s:
        from . import tenancy
        rows = s.scalars(select(UserJournal).where(UserJournal.created_at >= since, tenancy.owner_filter(UserJournal.owner))
                         .order_by(UserJournal.created_at)).all()
        rows = [{"sym": r.symbol, "at": r.created_at, "action": r.action, "conv": r.conviction, "ret": r.realized_return, "ok": r.correct,
                 "tags": r.tags or {}} for r in rows]
    scored = [r for r in rows if r["ok"] is not None]
    if len(scored) < 5:
        return {"n": len(rows), "n_scored": len(scored), "items": [], "message": f"채점된 기록 {len(scored)}건 — 실수 패턴은 5건부터 (한 달쯤 기록하면 보입니다)"}
    base = float(np.mean([r["ok"] for r in scored]))
    items = []

    def pattern(key, title, cond, advice):
        g = [r for r in scored if cond(r)]
        if len(g) >= 3:
            hit = float(np.mean([r["ok"] for r in g]))
            avg = float(np.mean([r["ret"] for r in g if r["ret"] is not None])) if any(r["ret"] is not None for r in g) else None
            items.append({"key": key, "title": title, "n": len(g), "hit": round(hit, 3), "avg_ret": None if avg is None else round(avg, 4),
                          "worse": hit < base - 0.05, "advice": advice})
    pattern("chase", "추격 매수 (5일 +8% 넘게 오른 뒤 매수)", lambda r: r["action"] == "BUY" and (r["tags"].get("run5") or 0) > 0.08,
            "급등 직후엔 AI 진입 구간(매수 관심구간)까지 기다리기")
    pattern("against_ai", "AI 판단과 반대로 행동", lambda r: (r["tags"].get("ai") or {}).get("action") in ("BUY", "SELL") and
            (r["tags"]["ai"]["action"] == "BUY") != (r["action"] == "BUY") and r["action"] != "HOLD",
            "AI 와 반대로 할 때는 이유를 저널에 꼭 적고 결과를 비교하기")
    pattern("overconfident", "확신 4~5 인데 틀림", lambda r: r["conv"] >= 4, "확신이 높을수록 비중을 키우는 습관 점검 — 확신과 적중이 따로 노는지")
    pattern("low_conv", "확신 1~2 매매", lambda r: r["conv"] <= 2, "확신이 낮으면 거래하지 않는 규칙 검토")
    by_sym = {}
    for r in scored:
        by_sym.setdefault(r["sym"], []).append(r)
    repeat_loss = [r for rs in by_sym.values() for i, r in enumerate(rs) if r["action"] == "BUY" and i > 0 and rs[i - 1]["ok"] is False and rs[i - 1]["action"] == "BUY"]
    if len(repeat_loss) >= 3:
        hit = float(np.mean([r["ok"] for r in repeat_loss]))
        items.append({"key": "averaging", "title": "틀린 종목을 다시 매수 (물타기)", "n": len(repeat_loss), "hit": round(hit, 3), "avg_ret": None,
                      "worse": hit < base - 0.05, "advice": "무효화 조건(Thesis)을 먼저 확인하고 추가 매수"})
    items.sort(key=lambda x: (not x["worse"], x["hit"]))
    top = next((x for x in items if x["worse"]), None)
    return {"n": len(rows), "n_scored": len(scored), "base_hit": round(base, 3), "items": items,
            "headline": f"가장 자주 손해 본 패턴: {top['title']} — 적중 {top['hit']:.0%} (평소 {base:.0%}, {top['n']}건)" if top else
            f"평소 적중 {base:.0%} — 두드러진 실수 패턴 없음", "as_of": label(datetime.now(UTC))}


def risk_limit_check(app, var95: float | None) -> str | None:
    p = get_profile(app.engine)
    lim = p.get("max_daily_loss")
    if lim and var95 and var95 * 100 > lim:
        return f"설정한 1일 최대 손실 {lim:.1f}% 보다 포트폴리오 하루 예상 손실(VaR95) {var95:.1%} 가 큼 — 비중 축소 검토"
    return None


__all__ = ["get_profile", "save_profile", "discover", "mistakes", "risk_limit_check", "STYLES"]

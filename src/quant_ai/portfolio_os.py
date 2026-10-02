"""Portfolio OS · 오늘의 AI 브리핑 · 전략 시뮬레이션.

overview()   내 자산 한 화면: 총자산 · 주식/현금 · 업종 집중(LOW/MEDIUM/HIGH) · 최대 비중 종목 · 위험 수준 ·
             "지금 포트폴리오에서 가장 큰 위험은 무엇인가" (근거 숫자와 함께 한 문장)
briefing()   로그인하면: 오늘의 AI 브리핑 ①~⑤ (실적 D-n · AI 신호 변화 · 위험 증가 · 오늘 거시 일정 · 밤사이 미국) + 오늘 확인할 것 3개
simulate()   실제 돈을 넣기 전에: 지금 포트폴리오 vs 코어 전략 vs 동일 비중 vs 현금 50% — 1년 변동성 · VaR · MDD · 역사적 위기 재생
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from . import ops
from .asof import label

LEVEL = {"ok": "LOW", "warn": "MEDIUM", "bad": "HIGH"}


def _book(app, mode: str, source: str = "auto") -> tuple[dict[str, float], float, float, dict[str, str]]:
    """(종목→평가금액, 현금, 총액, 이름) — source: auto(입력한 계좌가 있으면 그것, 없으면 시스템 장부) · accounts · system."""
    from . import accounts
    s = accounts.summary(app)
    mine = [a for a in s["accounts"] if a["type"] != "system"] if source != "system" else []
    vals, cash, names = {}, 0.0, {}
    if mine:
        for a in mine:
            cash += a["cash"]
            for h in a["holdings"]:
                vals[h["symbol"]] = vals.get(h["symbol"], 0) + h["value"]
                names[h["symbol"]] = h.get("name") or h["symbol"]
        src = "내 계좌 " + ", ".join(a["name"] for a in mine)
    else:
        pf = app.load_portfolio(mode)
        bars, _ = app._all_bars()
        for sym, p in pf.positions.items():
            if p.qty:
                px = float(bars[sym]["close"].iloc[-1]) if sym in bars and len(bars[sym]) else p.avg_price
                vals[sym] = p.qty * px
        cash = pf.cash
        src = {"paper": "모의투자 장부", "live": "실계좌 장부", "shadow": "그림자 매매 장부"}.get(mode, f"{mode} 장부")
    return vals, cash, sum(vals.values()) + cash, names | {"_src": src}


# v19: 테마 — 업종 이름이 달라도 사실상 같은 베팅인 묶음 (NVDA+AMD+AVGO+TSM = 반도체·AI)
THEMES = {
    "반도체·AI": {"005930", "005935", "000660", "042700", "403870", "058470", "039030", "NVDA", "AMD", "AVGO", "TSM", "MU", "INTC", "ASML",
                "QCOM", "ARM", "SMCI", "MRVL", "AMAT", "LRCX", "KLAC", "TXN"},
    "2차전지·전기차": {"373220", "006400", "051910", "247540", "086520", "003670", "066970", "TSLA", "RIVN", "ALB"},
    "빅테크·플랫폼": {"035420", "035720", "323410", "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NFLX", "ORCL", "CRM"},
    "자동차": {"005380", "000270", "012330", "TSLA", "GM", "F", "TM"},
    "바이오·헬스": {"207940", "068270", "196170", "028300", "000100", "128940", "LLY", "NVO", "MRNA", "PFE", "JNJ", "UNH"},
    "금융": {"105560", "055550", "086790", "316140", "024110", "032830", "000810", "JPM", "BAC", "GS", "V", "MA", "BRK-B"},
    "방산·조선": {"012450", "042660", "064350", "329180", "010140", "047810", "009540", "LMT", "RTX", "NOC", "GD"},
}
SECTOR_THEME = (("반도체", "반도체·AI"), ("2차전지", "2차전지·전기차"), ("자동차", "자동차"), ("바이오", "바이오·헬스"), ("제약", "바이오·헬스"),
                ("은행", "금융"), ("증권", "금융"), ("보험", "금융"), ("조선", "방산·조선"), ("우주항공", "방산·조선"))


def themes(weights: dict[str, float], sectors: dict[str, str], names: dict[str, str]) -> list[dict]:
    """테마별 비중 · 들어 있는 종목 — 25% 넘으면 '사실상 같은 베팅' 경고."""
    agg: dict[str, list] = {}
    for sym, w in weights.items():
        ts = {t for t, xs in THEMES.items() if sym in xs}
        sec = sectors.get(sym) or ""
        ts |= {t for k, t in SECTOR_THEME if k in sec}
        for t in ts:
            agg.setdefault(t, []).append((sym, w))
    out = []
    for t, xs in sorted(agg.items(), key=lambda kv: -sum(w for _, w in kv[1])):
        tot = sum(w for _, w in xs)
        members = [names.get(s_, s_) for s_, _ in sorted(xs, key=lambda x: -x[1])]
        out.append({"theme": t, "weight": round(tot, 4), "n": len(xs), "members": members[:6],
                    "level": "HIGH" if tot > 0.4 else "MEDIUM" if tot > 0.25 else "LOW",
                    "text": f"{t} 집중 {tot:.0%} ({'·'.join(members[:4])}{' 외' if len(members) > 4 else ''})"
                            + (" — 사실상 같은 베팅" if tot > 0.25 and len(xs) >= 2 else "")})
    return out


def holding_extras(app, symbols: list[str], now: datetime | None = None) -> dict[str, dict]:
    """보유 종목마다: AI 마지막 판단 · 최근 3일 중요 뉴스 · 14일 안 실적 발표 D-day."""
    from datetime import timedelta

    from sqlalchemy import func, select

    from . import ops
    from .data.db import session_scope
    from .data.models import ConsensusRecord, NewsArticle
    now = now or datetime.now(UTC)
    out: dict[str, dict] = {s_: {} for s_ in symbols}
    if not symbols:
        return out
    icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡", "NO_TRADE": "⚪"}
    with session_scope(app.engine) as s:
        sub = select(ConsensusRecord.symbol, func.max(ConsensusRecord.id).label("mid")).where(
            ConsensusRecord.symbol.in_(symbols)).group_by(ConsensusRecord.symbol).subquery()
        for c in s.scalars(select(ConsensusRecord).join(sub, ConsensusRecord.id == sub.c.mid)):
            out[c.symbol]["ai"] = {"action": c.action, "icon": icon.get(c.action, "⚪"), "prob_up": round(c.prob_up, 3), "at": label(c.as_of, with_time=False)}
        for n in s.scalars(select(NewsArticle).where(NewsArticle.published_at >= now - timedelta(days=3), NewsArticle.importance >= 0.7)
                           .order_by(NewsArticle.importance.desc()).limit(2000)):
            for sym in set(n.symbols or []) & set(symbols):
                d = out[sym].setdefault("news", {"n": 0, "top": None})
                d["n"] += 1
                d["top"] = d["top"] or {"id": n.id, "title": n.title}
    today = now.date()
    for e in ops.get_state(app.engine, "event_calendar").get("events") or []:
        sym = e.get("symbol")
        if sym in out and e.get("kind") == "earnings":
            try:
                dd = (datetime.fromisoformat(str(e["date"])[:10]).date() - today).days
            except ValueError:
                continue
            if 0 <= dd <= 14 and (not out[sym].get("earn") or dd < out[sym]["earn"]["d_day"]):
                out[sym]["earn"] = {"d_day": dd, "d_label": "D-Day" if dd == 0 else f"D-{dd}", "date": str(e["date"])[:10],
                                    "estimated": bool(e.get("estimated"))}
    return out


def overview(app, mode: str = "paper", source: str = "auto") -> dict:
    from sqlalchemy import select

    from . import accounts
    from .center import risk_simple
    from .data.db import session_scope
    from .data.models import Instrument
    from .engines.sector import sector_map
    n_mine = sum(1 for a in accounts.summary(app)["accounts"] if a["type"] != "system")
    vals, cash, total, names = _book(app, mode, source)
    src = names.pop("_src")
    if total <= 0:
        return {"empty": True, "headline": "보유 자산 기록이 없습니다 — '계좌 · 세금 · 배당'에서 내 계좌를 입력하면 여기서 한눈에 봅니다"}
    with session_scope(app.engine) as s:
        names |= {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_(list(vals) or [""])))}
    stock = sum(vals.values())
    w = {s_: v / total for s_, v in vals.items()}
    sectors = sector_map(app.engine)
    sec = {}
    for s_, x in w.items():
        k = sectors.get(s_) or "미분류"
        sec[k] = sec.get(k, 0) + x
    sec_sorted = sorted(sec.items(), key=lambda x: -x[1])
    known = [(k, v) for k, v in sec_sorted if k != "미분류"]
    top_sec = known[0] if known else None
    sec_level = "UNKNOWN" if not known else "HIGH" if top_sec[1] > 0.4 else "MEDIUM" if top_sec[1] > 0.25 else "LOW"
    top = max(w.items(), key=lambda x: x[1]) if w else (None, 0)
    rs = risk_simple(app, mode) if not src.startswith("내 계좌") else {}
    risks = []
    if top[0] and top[1] > 0.15:
        risks.append((top[1], f"{names.get(top[0], top[0])} 한 종목이 자산의 {top[1]:.0%} — 이 종목이 20% 빠지면 전체 −{top[1] * 0.2:.1%}"))
    if top_sec and top_sec[1] > 0.4:
        risks.append((top_sec[1], f"{top_sec[0]} 업종이 {top_sec[1]:.0%} — 업종 악재 하나에 크게 흔들림"))
    if stock / total > 0.95:
        risks.append((0.3, f"현금 {cash / total:.0%} — 급락 때 추가 매수·버틸 여력이 거의 없음"))
    for c in rs.get("cards") or []:
        if c["level"] == "bad":
            risks.append((0.5, f"{c['title']}: {c['value']} — {c['plain']}"))
    for r in rs.get("rising") or []:
        risks.append((0.2, r))
    risks.sort(key=lambda x: -x[0])
    biggest = risks[0][1] if risks else "두드러진 집중·한도 초과 위험 없음 — 시장 전체 하락(베타)이 가장 큰 위험"
    lvl = rs.get("level") or ("bad" if any(x[0] > 0.3 for x in risks) else "warn" if risks else "ok")
    th = themes(w, sectors, names)
    hot = [t for t in th if t["level"] != "LOW" and t["n"] >= 2]
    if hot:
        risks.append((hot[0]["weight"], hot[0]["text"] + " — 한 가지 재료(예: AI 수요·금리)에 같이 오르내림"))
        risks.sort(key=lambda x: -x[0])
        biggest = risks[0][1]
    top12 = [s_ for s_, _ in sorted(vals.items(), key=lambda x: -x[1])[:12]]
    ex = holding_extras(app, top12)
    level = LEVEL.get(lvl, "MEDIUM")
    if hot and hot[0]["level"] == "HIGH" and level == "LOW":
        level = "MEDIUM"
    return {"source": src, "source_key": "accounts" if src.startswith("내 계좌") else "system", "n_accounts": n_mine, "total": round(total), "stock": round(stock), "cash": round(cash), "stock_pct": round(stock / total, 4),
            "cash_pct": round(cash / total, 4), "n": len(vals),
            "holdings": [{"symbol": s_, "name": names.get(s_, s_), "value": round(vals[s_]), "weight": round(vals[s_] / total, 4),
                          "sector": sectors.get(s_) or "미분류", **ex.get(s_, {})} for s_ in top12],
            "themes": th[:5], "earnings_soon": sorted([{"symbol": s_, "name": names.get(s_, s_), **x["earn"]} for s_, x in ex.items() if x.get("earn")],
                                                       key=lambda e: e["d_day"]),
            "news_alerts": [{"symbol": s_, "name": names.get(s_, s_), **x["news"]} for s_, x in ex.items() if x.get("news")],
            "sectors": [{"sector": k, "weight": round(v, 4)} for k, v in sec_sorted[:6]], "sector_level": sec_level,
            "top": {"symbol": top[0], "name": names.get(top[0], top[0]), "weight": round(top[1], 4)} if top[0] else None,
            "risk_level": level, "biggest_risk": biggest, "other_risks": [r[1] for r in risks[1:4]],
            "risk_cards": rs.get("cards"), "as_of": label(datetime.now(UTC))}


def briefing(app, mode: str = "paper") -> dict:
    from .center import action_center
    from .reports import _us_moves
    ac = action_center(app, mode)
    items = []
    for e in ac["events"][:2]:
        items.append({"kind": "event", "text": f"{e['title']} {e['d_label']}" + (" (보유)" if e.get("held") else ""), "link": f"#analysis/{e['symbol']}" if e.get("symbol") else "#calendar"})
    for c in ac["signal_changes"][:2]:
        up = c["to"] == "BUY" or (c["from"] == "SELL")
        items.append({"kind": "signal", "text": f"{c['name']} AI 신호 {'상승' if up else '하락'} ({c['from']}→{c['to']})", "link": f"#analysis/{c['symbol']}"})
    if ac["risk"]["rising"]:
        items.append({"kind": "risk", "text": "포트폴리오 위험 증가 — " + ac["risk"]["rising"][0], "link": "#risk"})
    try:
        us = _us_moves(app)
    except Exception:  # noqa: BLE001
        us = []
    if us:
        avg = float(np.mean([u["chg"] for u in us if u.get("chg") is not None])) if any(u.get("chg") is not None for u in us) else None
        if avg is not None:
            items.append({"kind": "us", "text": f"밤사이 미국 {'강세' if avg > 0.003 else '약세' if avg < -0.003 else '보합'} ("
                          + " · ".join(f"{u['name'].split(' (')[0]} {u['chg']:+.1%}" for u in us[:3] if u.get("chg") is not None) + ")", "link": "#market"})
    for d in ac["disclosures"][:1]:
        items.append({"kind": "disclosure", "text": f"{d['name']} 중요 공시: {d['title'][:30]}", "link": f"#analysis/{d['symbol']}"})
    return {"at": ac["at"], "as_of": ac["as_of"], "items": items[:5], "check3": ac["check"][:3], "empty_hint": ac.get("empty_hint")}


def simulate(app, mode: str = "paper") -> dict:
    from .trading.portfolio_risk import portfolio_risk
    bars, bench, _ = app.market_data()
    vals, cash, total, names = _book(app, mode)
    names.pop("_src", None)
    strategies = {}
    if total > 0 and vals:
        strategies["지금 포트폴리오"] = {s: v / total for s, v in vals.items()}
    plan = ops.get_state(app.engine, f"cs-plan:{mode}")
    if plan.get("weights"):
        strategies["코어 전략 (모멘텀·품질 상위)"] = {s: float(x) for s, x in plan["weights"].items()}
    from .ux import starred
    wl = [s for s in starred(app) if s in bars][:10]
    if wl:
        strategies["관심종목 동일 비중"] = {s: 1 / len(wl) for s in wl}
    if strategies:
        base = next(iter(strategies.values()))
        strategies["현금 50% + 지금 비중 절반"] = {s: x / 2 for s, x in base.items()}
    out = []
    for name, w in strategies.items():
        w = {s: x for s, x in w.items() if s in bars}
        if not w:
            continue
        r = portfolio_risk(w, bars, bench)
        R = []
        for s, x in w.items():
            c = bars[s]["close"].iloc[-251:]
            R.append(c.pct_change().fillna(0) * x)
        port = sum(R) if R else None
        eq = (1 + port.fillna(0)).cumprod() if port is not None else None
        out.append({"name": name, "n": len(w), "invested": round(sum(w.values()), 3), "vol": r.get("vol"), "var95": r.get("var95"),
                    "es95": r.get("es95"), "mdd_1y": round(float((eq / eq.cummax() - 1).min()), 4) if eq is not None and len(eq) else None,
                    "ret_1y": round(float(eq.iloc[-1] - 1), 4) if eq is not None and len(eq) else None,
                    "stress": [{"name": x["name"], "loss": x["loss"], "basis": x.get("basis")} for x in r.get("stress") or [] if x["kind"] == "historical"]})
    return {"total": round(total) if total else None, "strategies": out,
            "note": "과거 1년 일봉으로 계산한 '그랬다면' — 미래를 보장하지 않음 · 위기 재생은 그 기간 실제 가격(없으면 베타×지수 대용)"}


__all__ = ["overview", "briefing", "simulate"]

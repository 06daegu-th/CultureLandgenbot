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


def _book(app, mode: str) -> tuple[dict[str, float], float, float, dict[str, str]]:
    """(종목→평가금액, 현금, 총액, 이름) — 사용자가 입력한 계좌가 있으면 그것을, 없으면 시스템 장부."""
    from . import accounts
    s = accounts.summary(app)
    mine = [a for a in s["accounts"] if a["type"] != "system"]
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
        src = f"{mode.upper()} 장부 (시스템)"
    return vals, cash, sum(vals.values()) + cash, names | {"_src": src}


def overview(app, mode: str = "paper") -> dict:
    from sqlalchemy import select

    from .center import risk_simple
    from .data.db import session_scope
    from .data.models import Instrument
    from .engines.sector import sector_map
    vals, cash, total, names = _book(app, mode)
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
    return {"source": src, "total": round(total), "stock": round(stock), "cash": round(cash), "stock_pct": round(stock / total, 4),
            "cash_pct": round(cash / total, 4), "n": len(vals),
            "holdings": [{"symbol": s_, "name": names.get(s_, s_), "value": round(v), "weight": round(v / total, 4), "sector": sectors.get(s_) or "미분류"}
                         for s_, v in sorted(vals.items(), key=lambda x: -x[1])[:12]],
            "sectors": [{"sector": k, "weight": round(v, 4)} for k, v in sec_sorted[:6]], "sector_level": sec_level,
            "top": {"symbol": top[0], "name": names.get(top[0], top[0]), "weight": round(top[1], 4)} if top[0] else None,
            "risk_level": LEVEL.get(lvl, "MEDIUM"), "biggest_risk": biggest, "other_risks": [r[1] for r in risks[1:4]],
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

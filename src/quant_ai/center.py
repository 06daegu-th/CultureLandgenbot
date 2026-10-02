"""오늘의 Action Center · 관심종목(그룹·시장·신호 변화) · 쉬운 포트폴리오 위험.

Action Center 다섯 칸 (모두 저장된 기록에서 — 아침에 열면 바로 뜬다)
  1. 오늘 확인할 종목 3개   보유·관심 중 점수가 높은 순: AI 신호 변화 · 큰 움직임 · 가까운 이벤트 · 투자 논리(Thesis) 이탈 · 막힌 매수
  2. 주의할 이벤트 2개       오늘~3일 안: 내 종목 실적·공시 + 시장 큰 일정(FOMC·CPI·고용·금통위·옵션 만기)
  3. AI 신호 변경           최근 3일 보유·관심 종목의 판단이 바뀐 것 (BUY→HOLD 포함)
  4. 위험 증가 포트폴리오    VaR·집중도·꼬리 위험이 지난 기록보다 커졌거나 한도에 가까움
  5. 확인 필요한 공시        최근 3일 보유·관심 종목의 중요 공시
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from . import ops
from .asof import label

ETF_WORDS = ("KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "KOSEF", "SOL ", "ACE ", "PLUS ", "RISE ", "ETF")
US_ETFS = {"SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "SOXX", "SMH", "XLK", "XLF", "XLE", "TLT", "GLD", "ARKK", "TQQQ", "SQQQ", "SCHD", "JEPI"}


def market_type(symbol: str, name: str | None) -> str:
    if symbol in US_ETFS or any(w in (name or "").upper() for w in ETF_WORDS):
        return "ETF"
    return "국내" if symbol[:1].isdigit() else "미국"


# ------------------------------------------------------------------ 관심종목 (그룹 · 시장 · 신호 변화)
def set_group(app, symbol: str, group: str | None) -> dict:
    st = ops.get_state(app.engine, "starred")
    groups = dict(st.get("groups") or {})
    g = (group or "").strip()[:20]
    if g:
        groups[symbol] = g
    else:
        groups.pop(symbol, None)
    ops.set_state(app.engine, "starred", {**st, "groups": groups})
    return {"symbol": symbol, "group": g or None}


def _signal_pairs(s, symbols: list[str], limit: int = 2) -> dict[str, list]:
    from .data.models import ConsensusRecord
    out = {}
    for sym in symbols:
        out[sym] = s.execute(select(ConsensusRecord.action, ConsensusRecord.prob_up, ConsensusRecord.as_of)
                             .where(ConsensusRecord.symbol == sym).order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc())
                             .limit(limit)).all()
    return out


def watchlist(app) -> dict:
    from .data.db import session_scope
    from .data.models import Instrument
    from .ux import starred
    st = ops.get_state(app.engine, "starred")
    star = starred(app)
    groups = st.get("groups") or {}
    recent = [x for x in ops.get_state(app.engine, "watch_symbols").get("symbols", []) if x not in star][::-1][:20]
    syms = list(dict.fromkeys(star + recent))
    bars, _ = app._all_bars()
    held = set()
    for m in ("live", "shadow", "paper", "us-paper"):
        try:
            held |= {s_ for s_, p in app.load_portfolio(m).positions.items() if p.qty}
        except Exception:  # noqa: BLE001, S112
            continue
    live = ops.get_state(app.engine, "live_quotes")
    with session_scope(app.engine) as s:
        names = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_(syms or [""])))}
        sig = _signal_pairs(s, syms)
    rows = []
    for sym in syms:
        b = bars.get(sym)
        q = live.get(sym) or {}
        last = q.get("price") or (float(b["close"].iloc[-1]) if b is not None and len(b) else None)
        chg = q.get("chg_pct") if q.get("price") else (float(b["close"].iloc[-1] / b["close"].iloc[-2] - 1) if b is not None and len(b) > 1 else None)
        pr = sig.get(sym) or []
        cur, prev = (pr[0] if pr else None), (pr[1] if len(pr) > 1 else None)
        change = f"{prev.action}→{cur.action}" if cur and prev and cur.action != prev.action else None
        rows.append({"symbol": sym, "name": names.get(sym, sym), "type": market_type(sym, names.get(sym)), "starred": sym in star,
                     "group": groups.get(sym) or ("최근 본 종목" if sym not in star else "기본"), "held": sym in held,
                     "last": last, "chg_pct": chg, "price_src": "실시간" if q.get("price") else "일봉 종가",
                     "ai": cur.action if cur else None, "prob_up": round(cur.prob_up, 3) if cur else None, "ai_at": label(cur.as_of) if cur else None,
                     "change": change, "change_dir": (1 if cur.action == "BUY" else -1 if cur.action in ("SELL",) or prev.action == "BUY" else 0) if change else 0})
    return {"rows": rows, "groups": sorted({r["group"] for r in rows}), "types": ["국내", "미국", "ETF"], "n_star": len(star)}


# ------------------------------------------------------------------ 쉬운 포트폴리오 위험
def risk_simple(app, mode: str = "paper") -> dict:
    r = app.portfolio_risk(mode)
    if not r.get("n_positions"):
        return {"mode": mode, "empty": True, "cards": [], "rising": [], "level": "ok", "headline": f"{mode} 장부에 보유 종목이 없습니다"}
    eq = r.get("equity") or 0
    conc = r.get("concentration") or {}
    sec = (r.get("sectors") or [{}])[0]
    top = (r.get("component_var") or r.get("risk_contrib") or [{}])[0]
    ws = r.get("worst_stress") or {}
    lim = r.get("limits") or {}
    cards = [
        {"key": "concentration", "title": "종목 집중도", "value": f"최대 1종목 {conc.get('top1', 0):.0%} · 상위 5 {conc.get('top5', 0):.0%}",
         "plain": f"사실상 {conc.get('effective_n', 0):.0f}종목에 나눠 투자한 효과", "level": "bad" if conc.get("top1", 0) > lim.get("max_name_weight", 0.15) else "warn" if conc.get("effective_n", 99) < 5 else "ok"},
        {"key": "sector", "title": "업종 집중도", "value": f"{sec.get('sector', '-')} {sec.get('weight', 0):.0%}",
         "plain": "업종 분류가 없어 판단 불가 (WICS 수집 후 정확해짐)" if sec.get("sector") == "미분류" else f"한 업종이 {sec.get('weight', 0):.0%} — 한도 {lim.get('max_sector_weight', 0.4):.0%}",
         "level": "na" if sec.get("sector") == "미분류" else "bad" if sec.get("weight", 0) > lim.get("max_sector_weight", 0.4) else "ok"},
        {"key": "cash", "title": "현금 비중", "value": f"{r.get('cash_weight', 0):.0%}", "plain": "현금이 거의 없어 하락 때 추가 매수 여력이 작음" if r.get("cash_weight", 0) < 0.05 else "완충 현금 있음",
         "level": "warn" if r.get("cash_weight", 0) < 0.05 else "ok"},
        {"key": "top_risk", "title": "최대 위험 종목", "value": f"{top.get('name', '-')} (위험 기여 {top.get('share', 0):.0%})",
         "plain": f"비중은 {top.get('weight', 0):.0%} 인데 전체 흔들림의 {top.get('share', 0):.0%} 를 만듦", "level": "warn" if top.get("share", 0) > 0.15 else "ok"},
        {"key": "loss", "title": "예상 손실 (하루)", "value": f"{r.get('var95_krw', 0):,.0f}원 ({r.get('var95', 0):.1%})",
         "plain": f"20일에 하루 정도는 이보다 더 잃을 수 있음 · 그런 날 평균 {r.get('es95_krw', 0):,.0f}원", "level": "bad" if r.get("var95", 0) > lim.get("max_var95", 0.04) else "ok"},
        {"key": "stress", "title": "스트레스 상황", "value": f"{ws.get('name', '-')} 재현 시 −{ws.get('loss', 0):.1%}",
         "plain": f"약 {ws.get('loss', 0) * eq:,.0f}원 손실 ({ws.get('period', '')})", "level": "warn" if ws.get("loss", 0) > 0.15 else "ok"},
    ]
    hist = ops.get_state(app.engine, f"risk_hist:{mode}").get("rows") or []
    today = datetime.now(UTC).date().isoformat()
    if not hist or hist[-1]["d"] != today:
        hist = (hist + [{"d": today, "var95": r.get("var95"), "top1": conc.get("top1"), "eff_n": conc.get("effective_n")}])[-60:]
        ops.set_state(app.engine, f"risk_hist:{mode}", {"rows": hist})
    prev = hist[-2] if len(hist) >= 2 else None
    rising = []
    if prev and prev.get("var95") and r.get("var95") and r["var95"] > prev["var95"] * 1.2:
        rising.append(f"하루 예상 손실(VaR) {prev['var95']:.1%} → {r['var95']:.1%} (+{r['var95'] / prev['var95'] - 1:.0%})")
    if r.get("var95", 0) > 0.8 * lim.get("max_var95", 0.04):
        rising.append(f"VaR 가 한도 {lim.get('max_var95', 0.04):.0%} 의 80% 이상")
    if (r.get("var") or {}).get("fat_tail"):
        rising.append("꼬리 위험(급락 빈도)이 정규분포보다 큼")
    worst = max(({"ok": 0, "na": 0, "warn": 1, "bad": 2}[c["level"]] for c in cards), default=0)
    return {"mode": mode, "equity": eq, "cards": cards, "rising": rising, "level": ["ok", "warn", "bad"][worst],
            "headline": ["위험 수준 보통", "주의할 위험 있음", "한도 넘은 위험 있음"][worst], "as_of": label(r.get("as_of"), with_time=False)}


# ------------------------------------------------------------------ Action Center
def action_center(app, mode: str = "paper", now: datetime | None = None) -> dict:
    from zoneinfo import ZoneInfo

    from .data.db import session_scope
    from .data.models import Disclosure, Instrument
    from .engines.event_extract import extract
    from .stockplus import IMPORTANT_DISC
    now = now or datetime.now(UTC)
    today = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    wl = watchlist(app)
    held = set()
    try:
        held = {s_ for s_, p in app.load_portfolio(mode).positions.items() if p.qty}
    except Exception:  # noqa: BLE001, S110
        pass
    focus = {r["symbol"] for r in wl["rows"]} | held
    with session_scope(app.engine) as s:
        names = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_(list(focus) or [""])))}
        sig = _signal_pairs(s, sorted(focus), 3)
        discs = s.scalars(select(Disclosure).where(Disclosure.symbol.in_(list(focus) or [""]), Disclosure.filed_at >= today - timedelta(days=3))
                          .order_by(Disclosure.filed_at.desc()).limit(50)).all()
        discs = [(d.symbol, d.title, d.filed_at, d.summary, d.url) for d in discs]
    # 3. 신호 변경
    changes = []
    for sym, rows in sig.items():
        if len(rows) >= 2 and rows[0].action != rows[1].action and _aware(rows[0].as_of) >= now - timedelta(days=4):
            changes.append({"symbol": sym, "name": names.get(sym, sym), "from": rows[1].action, "to": rows[0].action,
                            "prob_up": round(rows[0].prob_up, 3), "at": label(rows[0].as_of), "held": sym in held})
    # 2. 이벤트
    evs = []
    for e in ops.get_state(app.engine, "event_calendar").get("events") or []:
        try:
            d = date.fromisoformat(str(e["date"])[:10])
        except (KeyError, ValueError):
            continue
        dd = (d - today).days
        if not 0 <= dd <= 3:
            continue
        mine = e.get("symbol") in focus
        big = not e.get("symbol") and e.get("kind") in ("fomc", "cpi", "nfp", "bok", "quad_witching", "options_expiry")
        if mine or big:
            evs.append({"title": (names.get(e.get("symbol"), "") + " " if mine else "") + e["title"], "d_label": "D-Day" if dd == 0 else f"D-{dd}",
                        "d_day": dd, "symbol": e.get("symbol"), "held": e.get("symbol") in held, "estimated": bool(e.get("estimated")),
                        "score": (3 if mine and e.get("symbol") in held else 2 if mine else 1) * (1.5 if e.get("kind") in ("earnings", "fomc") else 1) / (1 + dd)})
    evs.sort(key=lambda x: -x["score"])
    # 5. 공시
    imp = []
    for sym, title, filed, summ, url in discs:
        ex = extract(title, {sym: names.get(sym, sym)}, sym)
        important = any(k in title for k in IMPORTANT_DISC) or any(x["importance"] >= 0.7 for x in ex)
        if important:
            imp.append({"symbol": sym, "name": names.get(sym, sym), "title": title, "date": str(filed), "summary": summ, "url": url,
                        "held": sym in held, "labels": [x["label"] for x in ex]})
    # 1. 오늘 확인할 종목 (점수)
    from .thesis import breaches
    thesis_hits = {b["symbol"]: b for b in breaches(app)}
    score = {}
    why: dict[str, list[str]] = {}

    def bump(sym, pts, msg):
        score[sym] = score.get(sym, 0) + pts
        why.setdefault(sym, []).append(msg)
    for c in changes:
        bump(c["symbol"], 3 if c["held"] else 2, f"AI {c['from']}→{c['to']}")
    for r in wl["rows"]:
        if r["chg_pct"] is not None and abs(r["chg_pct"]) >= 0.03:
            bump(r["symbol"], 2 if r["held"] else 1, f"{r['chg_pct']:+.1%} 움직임")
    for e in evs:
        if e.get("symbol"):
            bump(e["symbol"], 2 if e["held"] else 1, f"{e['title'].split(' ', 1)[-1]} {e['d_label']}")
    for d in imp:
        bump(d["symbol"], 2 if d["held"] else 1, "중요 공시")
    for sym, b in thesis_hits.items():
        bump(sym, 4, b["message"])
    check = [{"symbol": s_, "name": names.get(s_, s_), "score": round(v, 2), "why": why[s_][:3], "held": s_ in held}
             for s_, v in sorted(score.items(), key=lambda x: -x[1])[:3]]
    rs = risk_simple(app, mode)
    return {"at": now.isoformat(), "as_of": label(now), "check": check, "events": evs[:2], "events_all": evs[:8], "signal_changes": changes[:8],
            "risk": {"headline": rs.get("headline"), "rising": rs.get("rising") or [], "level": rs.get("level", "ok")},
            "disclosures": imp[:5],
            "empty_hint": None if focus else "보유·관심 종목이 없습니다 — 종목을 검색해 ★ 를 누르면 여기서 매일 챙겨 드립니다"}


def _aware(t):
    return t if t.tzinfo else t.replace(tzinfo=UTC)


__all__ = ["action_center", "watchlist", "set_group", "risk_simple", "market_type"]


# ------------------------------------------------------------------ v17: 보유 종목 주간 일정 알림
def weekly_schedule(app, now: datetime | None = None, days: int = 7) -> dict:
    """보유·관심 종목의 다음 7일 일정(실적·배당락·공시·동종업체 실적) + 시장 큰 일정(FOMC·CPI·PCE·만기)."""
    from .alerts import focus_symbols
    from .stock import events
    now = now or datetime.now(UTC)
    focus = list(focus_symbols(app))[:30]
    rows, seen = [], set()
    for sym in focus:
        for e in events(app, sym, now, days=days):
            if not 0 <= e["d_day"] <= days:
                continue
            key = (e["title"], e["date"]) if e["scope"] == "시장" else (sym, e["title"], e["date"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(e | {"symbol": sym if e["scope"] != "시장" else None})
    rows.sort(key=lambda x: (x["d_day"], x["scope"] != "종목"))
    return {"rows": rows, "n": len(rows), "as_of": label(now)}


def weekly_alert(app, now: datetime | None = None) -> int:
    """월요일(한국 시간)에 한 번: 이번 주 보유 종목 일정을 알림으로."""
    from zoneinfo import ZoneInfo

    from .alerts import push
    now = now or datetime.now(UTC)
    k = now.astimezone(ZoneInfo("Asia/Seoul"))
    if k.weekday() != 0:
        return 0
    w = weekly_schedule(app, now)
    if not w["rows"]:
        return 0
    body = " · ".join(f"{e['d_label']} {e['title']}" for e in w["rows"][:8])
    r = push(app.engine, "event", f"이번 주 일정 {w['n']}건", body, level="info", link="#calendar",
             dedupe=f"weekly_schedule:{k.isocalendar().year}-{k.isocalendar().week}")
    return int(r is not None)


def oneline(app, mode: str = "paper", now: datetime | None = None) -> dict:
    """홈 맨 위 한 줄: 시장 분위기 · 가장 가까운 큰 일정 · 내 포트폴리오 위험 · AI 신뢰 — 각 칸은 해당 화면으로 연결."""
    from . import aitrack
    from .engines.market_intel import market_state
    now = now or datetime.now(UTC)
    out = []
    try:
        bars, bench, _ = app.market_data()
        ms = market_state(bench, bars)
        mood = {"RISK ON": ("위험 선호", "good"), "RISK OFF": ("위험 회피", "bad"), "NEUTRAL": ("중립", "warn")}.get(ms["label"], ("-", "warn"))
        out.append({"key": "market", "label": "시장", "text": f"{mood[0]} ({ms['score']}점 · {ms['type']})" if not ms.get("insufficient") else "지수 데이터 부족",
                    "level": mood[1] if not ms.get("insufficient") else "warn", "link": "#market"})
    except Exception as e:  # noqa: BLE001 - 한 칸이 실패해도 나머지는 보인다
        out.append({"key": "market", "label": "시장", "text": f"계산 실패: {type(e).__name__}", "level": "warn", "link": "#market"})
    try:
        w = weekly_schedule(app, now, days=3)
        big = next((e for e in w["rows"] if e["scope"] == "시장" or e["kind"] in ("earnings", "ex_div")), None)
        out.append({"key": "event", "label": "주요 일정", "text": f"{big['d_label']} {big['title']}" if big else "3일 안 큰 일정 없음",
                    "level": "warn" if big and big["d_day"] <= 1 else "ok", "link": "#calendar"})
    except Exception as e:  # noqa: BLE001
        out.append({"key": "event", "label": "주요 일정", "text": f"계산 실패: {type(e).__name__}", "level": "warn", "link": "#calendar"})
    try:
        r = risk_simple(app, mode)
        out.append({"key": "risk", "label": "내 위험", "text": r["headline"] + (f" · {r['rising'][0]}" if r.get("rising") else ""),
                    "level": {"ok": "ok", "warn": "warn", "bad": "bad"}.get(r["level"], "warn"), "link": "#pos"})
    except Exception as e:  # noqa: BLE001
        out.append({"key": "risk", "label": "내 위험", "text": f"계산 실패: {type(e).__name__}", "level": "warn", "link": "#pos"})
    t = aitrack.report(app)
    lv = t["trust"].get("level", "NO_DATA")
    dem = bool((t.get("demotion") or {}).get("on"))
    out.append({"key": "ai", "label": "AI 신뢰", "text": ("SHADOW 강등 — 주문에 안 씀" if dem else
                                                        {"UNTRUSTED": "기준 미달 — 참고만", "WATCH": "관찰 중", "CANDIDATE": "후보 (소액 위성만)",
                                                         "NO_DATA": "채점 기록 부족"}.get(lv, lv)),
                "level": "bad" if dem or lv == "UNTRUSTED" else "ok" if lv == "CANDIDATE" else "warn", "link": "#scorecard"})
    worst = max((["ok", "good", "warn", "bad"].index(x["level"]) for x in out), default=0)
    return {"items": out, "level": ["ok", "ok", "warn", "bad"][worst], "as_of": label(now)}


AI_STATE = {"verified": ("🟢", "검증됨", "기준을 넘은 상태가 이어짐 — 그래도 소액 위성에서만"),
            "checking": ("🟡", "검증 중", "아직 기록이 부족하거나 기준 근처 — 참고만"),
            "banned": ("🔴", "사용 금지", "성적이 기준 미달 — 주문에 쓰지 않음")}


def ai_state(app) -> dict:
    """홈·종목 공통 AI 상태 한 줄: 🟢 검증됨 / 🟡 검증 중 / 🔴 사용 금지."""
    from . import aitrack
    t = aitrack.report(app)
    lv = t["trust"].get("level", "NO_DATA")
    dem = bool((t.get("demotion") or {}).get("on"))
    key = "banned" if dem or lv == "UNTRUSTED" else "verified" if lv == "CANDIDATE" else "checking"
    icon, name, why = AI_STATE[key]
    last = t.get("last") or {}
    return {"key": key, "icon": icon, "label": name, "why": why, "level": lv, "demoted": dem,
            "accuracy": last.get("accuracy"), "base": last.get("base"), "n": last.get("n"), "reasons": t["trust"].get("reasons") or []}


def home5(app, mode: str = "paper", now: datetime | None = None) -> dict:
    """홈 첫 화면 5칸: 오늘 시장 · 내 자산 · AI 상태 · 중요한 뉴스 · 오늘 할 일 (각 칸 실패해도 나머지는 보인다)."""
    from .clock import clock_status
    now = now or datetime.now(UTC)
    out: dict = {"as_of": label(now)}

    def safe(key, fn):
        try:
            out[key] = fn()
        except Exception as e:  # noqa: BLE001
            out[key] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}

    def market():
        ol = oneline(app, mode, now)
        clk = clock_status(now)["markets"]
        bars, bench, _ = app.market_data()
        idx = None
        if bench is not None and len(bench) > 1:
            c = bench["close"].astype(float)
            idx = {"name": "코스피(대용)", "last": round(float(c.iloc[-1]), 2), "chg": round(float(c.iloc[-1] / c.iloc[-2] - 1), 4),
                   "date": str(c.index[-1].date())}
        return {"mood": next((x for x in ol["items"] if x["key"] == "market"), None),
                "event": next((x for x in ol["items"] if x["key"] == "event"), None), "index": idx,
                "markets": [{"flag": m["flag"], "name": m["short"], "light": m.get("light"),
                             "state": "장중" if m["phase"] == "open" else (m["session_label"] if m["trading_day"] else "휴장"),
                             "notice": m.get("notice")} for m in clk.values()]}

    def assets():
        pf = app.load_portfolio(mode)
        bars, _ = app._all_bars()
        px = {s_: float(bars[s_]["close"].iloc[-1]) for s_ in pf.positions if s_ in bars and len(bars[s_])}
        eq = pf.equity(px) if px or pf.cash else pf.cash
        rk = risk_simple(app, mode)
        base = app.settings.initial_cash
        return {"mode": mode, "equity": round(eq), "cash": round(pf.cash), "n": len(pf.positions), "pnl_pct": round(eq / base - 1, 4) if base else None,
                "risk": {"level": rk.get("level"), "headline": rk.get("headline")}}

    def news():
        from .board import news_board
        b = news_board(app, days=2, limit=12, now=now)
        top = sorted(b["cards"], key=lambda c: -(c.get("level") or {}).get("score", 0))[:3]
        return {"items": [{"id": c["id"], "title": c["title"], "level": c["level"], "tone": c["tone"],
                           "symbols": [{"symbol": x["symbol"], "name": x["name"]} for x in c["symbols"][:2]], "first": c["first"]} for c in top],
                "n": len(b["cards"])}

    def todo():
        a = action_center(app, mode, now)
        items = [{"text": f"{c['name']} 확인 — {' · '.join((c.get('why') or [])[:2])}", "link": f"#analysis/{c['symbol']}", "symbol": c["symbol"]}
                 for c in (a.get("check") or [])[:3]]
        items += [{"text": f"{e['d_label']} {e['title']}", "link": f"#analysis/{e['symbol']}" if e.get("symbol") else "#calendar"}
                  for e in (a.get("events") or [])[:2]]
        items += [{"text": f"AI 신호 변경: {c['name']} {c['from']} → {c['to']}", "link": f"#analysis/{c['symbol']}"}
                  for c in (a.get("signal_changes") or [])[:2]]
        return {"items": items[:6], "empty_hint": a.get("empty_hint")}

    safe("market", market)
    safe("assets", assets)
    safe("ai", lambda: ai_state(app))
    safe("news", news)
    safe("todo", todo)
    return out


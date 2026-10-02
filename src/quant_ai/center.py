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

import pandas as pd
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


AI_STATE = {"verified": ("🟢", "실전 가능", "기준을 넘은 상태가 이어짐 — 그래도 소액 위성에서만"),
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
    from .scorecard import plain
    try:
        pl = plain(app, 100)
    except Exception as e:  # noqa: BLE001 - 성적표 실패가 홈을 막지 않게
        pl = {"n": 0, "headline": f"성적 계산 실패: {type(e).__name__}"}
    easy = {"headline": pl.get("headline"), "money": (pl.get("money") or {}).get("text"), "earned": (pl.get("money") or {}).get("earned"),
            "beat_base": pl.get("beat_base"), "strong": pl.get("strong"), "weak": pl.get("weak"), "n": pl.get("n", 0)}
    # v20 알파 채점: 시장이 다 같이 오를 때 '오른다' 고 한 적중은 실력이 아니다 → 시장 대비로 다시 채점
    try:
        from .alphascore import alpha_card
        ac = alpha_card(app)
    except Exception:  # noqa: BLE001 - 보조 지표
        ac = {"n": 0}
    if ac.get("n"):
        easy["alpha"] = {"n": ac["n"], "hit": ac["hit_excess"], "base": ac["base_excess"], "market_share": ac["market_share"],
                         "verdict": ac["verdict"], "worse": "낮음" in ac["verdict"], "proven": "있음" in ac["verdict"],
                         "text": f"시장 대비로 채점하면 {ac['hit_excess']:.0%} (기준 {max(0.5, ac['base_excess']):.0%}) — {ac['verdict']}"}
        if key == "verified" and not easy["alpha"]["proven"]:
            key = "checking"  # 방향 적중은 넘었어도 종목 선택력이 증명되지 않았으면 🟢 를 주지 않는다
            icon, name, why = AI_STATE[key]
            why = "방향 적중은 기준을 넘었지만 시장 대비로는 아직 증명 안 됨 — 참고만"
    return {"key": key, "icon": icon, "label": name, "why": why, "level": lv, "demoted": dem, "easy": easy,
            "accuracy": last.get("accuracy"), "base": last.get("base"), "n": last.get("n"), "reasons": t["trust"].get("reasons") or []}


def _equity_spark(app, mode: str, now_eq: float, n: int = 90) -> list[list]:
    """홈 '내 자산' 미니 차트: 최근 n 개 평가일의 [날짜, 평가금액] (하루 마지막 값) + 지금 값."""
    from .data.db import session_scope
    from .data.models import PortfolioSnapshot
    with session_scope(app.engine) as s:
        rows = s.execute(select(PortfolioSnapshot.ts, PortfolioSnapshot.equity).where(PortfolioSnapshot.mode == mode)
                         .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.id.desc()).limit(n * 8)).all()
    by_day: dict[str, float] = {}
    for ts, e in rows:
        by_day.setdefault(str(ts.date()), float(e))  # 내림차순이라 처음 본 것이 그날 마지막 값
    out = [[d, round(v)] for d, v in sorted(by_day.items())][-n:]
    if out and abs(out[-1][1] - now_eq) > 0.5:
        out.append(["now", round(now_eq)])
    return out


def _d_word(dd: int) -> str:
    return "오늘" if dd == 0 else "내일" if dd == 1 else f"D-{dd}"


def today3(app, mode: str = "paper", now: datetime | None = None, n: int = 3) -> dict:
    """홈 맨 위 '오늘 내가 할 일' — 가장 중요한 3개만 (나머지는 '더 보기').

    우선순위: 자동 정지 > 투자 논리 깨짐 > 보유 종목 실적·일정 임박 > AI 신호 변경 > 보유 종목 중요 공시 >
              시장 큰 일정(CPI·FOMC·고용) > 큰 움직임 > (아무것도 없으면) 시작 안내.
    """
    from .thesis import breaches
    now = now or datetime.now(UTC)
    items: list[dict] = []

    def add(pri, icon, text, link, why="", symbol=None, kind="", name=None):
        items.append({"pri": pri, "icon": icon, "text": text, "link": link, "why": why, "symbol": symbol, "kind": kind, "name": name})
    if ops.halted(app.engine):
        add(100, "⛔", "자동 정지 중 — 이유를 확인하세요", "#safety", "안전장치가 모든 신규 매수를 막았습니다", kind="halt")
    elif ops.kill_switch_on(app.engine):
        add(90, "⏸️", "긴급 정지(수동) 켜져 있음 — 계속 둘지 확인", "#safety", "신규 매수가 멈춰 있습니다", kind="kill")
    try:
        for it in ops_status(app, now)["issues"]:
            if it["level"] == "bad":
                add(95 if it["key"] == "scheduler" else 92, "🔌" if it["key"] == "scheduler" else "🗓️", it["text"], "#server", it["fix"], kind="ops_" + it["key"])
    except Exception:  # noqa: BLE001, S110 - 상태 확인 실패가 할 일을 막지 않게
        pass
    a = action_center(app, mode, now)
    br = breaches(app)[:2]
    if br:
        from .data.db import session_scope
        from .data.models import Instrument
        with session_scope(app.engine) as s:
            nm = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_([b["symbol"] for b in br])))}
    for b in br:
        add(80, "⚠️", f"{nm.get(b['symbol'], b['symbol'])} 투자 논리 점검 — {b['message']}", f"#analysis/{b['symbol']}", "산 이유가 깨졌을 수 있습니다",
            b["symbol"], "thesis", nm.get(b["symbol"]))
    for e in a.get("events_all") or []:
        word = _d_word(e["d_day"])
        if e.get("symbol"):
            add(70 if e["held"] else 55, "📊" if "실적" in e["title"] else "📅", f"{e['title']} {word}" + (" (추정일)" if e.get("estimated") else ""),
                f"#analysis/{e['symbol']}", "보유 종목" if e["held"] else "관심 종목", e["symbol"], "event")

    try:  # 시장 큰 일정 (CPI·FOMC·고용 등) — 이벤트 캘린더가 아직 없어도 규칙 일정으로
        for e in weekly_schedule(app, now, days=2)["rows"]:
            if e.get("scope") == "시장" and e["d_day"] <= 1:
                add(50, "🏛️", f"{e['title']} {_d_word(e['d_day'])}", "#calendar", "시장 전체가 크게 움직일 수 있는 일정", kind="macro")
    except Exception:  # noqa: BLE001, S110 - 일정 계산 실패가 할 일 전체를 막지 않게
        pass
    for c in a.get("signal_changes") or []:
        to = "NO TRADE" if c["to"] == "NO_TRADE" else c["to"]
        fr = "NO TRADE" if c["from"] == "NO_TRADE" else c["from"]
        add(65 if c["held"] else 45, "🤖", f"{c['name']} AI 신호 변경 {fr} → {to}", f"#analysis/{c['symbol']}",
            "보유 종목" if c["held"] else "관심 종목", c["symbol"], "signal", c["name"])
    for d in a.get("disclosures") or []:
        add(60 if d["held"] else 40, "📑", f"{d['name']} 중요 공시 — {d['title'][:30]}", f"#analysis/{d['symbol']}",
            "보유 종목" if d["held"] else "관심 종목", d["symbol"], "disclosure", d["name"])
    from zoneinfo import ZoneInfo
    bars_all, _ = app._all_bars()
    today_kst = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    for c in a.get("check") or []:
        if any("움직임" in w for w in c.get("why") or []):
            mv = next(w for w in c["why"] if "움직임" in w)
            b = bars_all.get(c["symbol"])
            if b is not None and len(b):  # 오늘 움직임이 아니면 날짜를 붙인다 (오래된 데이터를 오늘 일처럼 보이지 않게)
                d = pd.Timestamp(b.index[-1]).tz_localize("UTC") if pd.Timestamp(b.index[-1]).tzinfo is None else pd.Timestamp(b.index[-1])
                d = d.tz_convert("Asia/Seoul").date()
                if (today_kst - d).days >= 1 and c["symbol"][:1].isdigit():
                    mv = f"{mv} ({d.month}/{d.day} 종가 기준)"
            add(35 if c["held"] else 25, "📈" if "+" in mv else "📉", f"{c['name']} {mv}", f"#analysis/{c['symbol']}",
                "보유 종목" if c["held"] else "관심 종목", c["symbol"], "move", c["name"])
    seen, out = set(), []
    for it in sorted(items, key=lambda x: -x["pri"]):
        k = (it["symbol"], it["kind"]) if it["symbol"] else it["text"]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    empty_hint = a.get("empty_hint")
    return {"items": out[:n], "more": max(0, len(out) - n), "total": len(out), "empty_hint": empty_hint,
            "calm": "오늘 꼭 할 일은 없습니다 — 기다리는 것도 투자입니다" if not out and not empty_hint else None, "as_of": label(now)}


def ops_status(app, now: datetime | None = None) -> dict:
    """'지금 제대로 돌고 있나' — 24시간 운영(심장박동) · 주가 데이터 날짜 · 오늘 뉴스 · 최근 작업 실패."""
    from sqlalchemy import func

    from .asof import stamp
    from .data.db import session_scope
    from .data.models import JobRun, NewsArticle, PriceBar
    from .watchdog import heartbeat_age
    now = now or datetime.now(UTC)
    hb = heartbeat_age(app.engine, now)
    with session_scope(app.engine) as s:
        last_bar = s.scalar(select(func.max(PriceBar.ts)).where(PriceBar.symbol.like("0%") | PriceBar.symbol.like("1%")
                                                                  | PriceBar.symbol.like("2%") | PriceBar.symbol.like("3%")))
        news24 = s.scalar(select(func.count()).select_from(NewsArticle).where(NewsArticle.published_at >= now - timedelta(days=1))) or 0
        fails = s.scalar(select(func.count()).select_from(JobRun).where(JobRun.ok.is_(False), JobRun.started_at >= now - timedelta(days=1))) or 0
        kr_syms = set(s.scalars(select(PriceBar.symbol).where(PriceBar.ts >= last_bar - timedelta(days=10)).distinct())) if last_bar else set()
    kr_syms = {x for x in kr_syms if x[:1].isdigit()}
    from .engines.sector import sector_map
    mapped = sum(1 for x in kr_syms if x in sector_map(app.engine))
    sector_cov = mapped / len(kr_syms) if kr_syms else None
    bar = stamp(last_bar, "bar_kr", now) if last_bar else {"status": "none", "lag_days": None, "label": None}
    running = hb is not None and hb < 15 * 60
    issues = []
    if not running:
        issues.append({"key": "scheduler", "level": "bad", "text": "24시간 운영이 꺼져 있습니다" + (f" (마지막 신호 {hb / 3600:.0f}시간 전)" if hb else " (한 번도 안 켜짐)"),
                       "fix": "터미널에서 ./run.sh — PC 를 켤 때 자동으로: ./run.sh install-service"})
    if bar.get("lag_days") and bar["lag_days"] >= 2:
        issues.append({"key": "data", "level": "bad", "text": f"주가 데이터가 {bar['lag_days']}거래일 밀렸습니다 ({bar['label']})",
                       "fix": "24시간 운영을 켜면 장 마감 뒤 자동으로 받습니다 — 지금 바로: ./run.sh data"})
    if running and news24 == 0:
        issues.append({"key": "news", "level": "warn", "text": "최근 하루 저장된 뉴스 0건 — 뉴스 AI 가 빈손으로 판단합니다",
                       "fix": "./run.sh doctor 로 뉴스 소스 연결 확인 (회사망·방화벽이면 RSS 주소가 막힐 수 있음) · 지금 바로: qa collect news"})
    if sector_cov is not None and len(kr_syms) >= 10 and sector_cov < 0.5:
        issues.append({"key": "sector", "level": "warn", "text": f"업종 분류가 {1 - sector_cov:.0%} 비어 있습니다 ({len(kr_syms) - mapped}/{len(kr_syms)}종목)",
                       "fix": "업종 집중 위험·섹터 비교가 부정확합니다 — 지금 바로: qa collect sectors (24시간 운영 중이면 장 마감 뒤 자동)"})
    if fails >= 3:
        issues.append({"key": "jobs", "level": "warn", "text": f"최근 하루 작업 실패 {fails}건", "fix": "서버 · DB 화면에서 실패한 작업 확인"})
    return {"running": running, "heartbeat_age_s": hb, "bar": {k: bar.get(k) for k in ("label", "status", "lag_days")},
            "news_24h": news24, "job_failures_24h": fails, "sector_coverage": None if sector_cov is None else round(sector_cov, 3), "issues": issues, "ok": not issues, "as_of": label(now)}


def start_guide(app) -> dict:
    """처음 쓰는 사람을 위한 순서: ① 데이터 ② 투자 한도 ③ 관심종목 3개 ④ 오늘 할 일. 다 하면 사라진다."""
    from sqlalchemy import func

    from .budget import get as budget_get
    from .data.db import session_scope
    from .data.models import PriceBar
    from .ux import starred
    with session_scope(app.engine) as s:
        has_bars = (s.scalar(select(func.count()).select_from(select(PriceBar.id).limit(1).subquery())) or 0) > 0
    b = budget_get(app)
    n_star = len(starred(app))
    held = 0
    try:
        held = sum(1 for p in app.load_portfolio("paper").positions.values() if p.qty)
    except Exception:  # noqa: BLE001
        held = 0
    steps = [
        {"key": "data", "n": 1, "title": "주가 데이터 받기", "done": has_bars,
         "how": "터미널에서 ./run.sh 를 실행하면 처음 한 번 자동으로 받습니다 (1~3분)", "link": None,
         "detail": "받았음" if has_bars else "아직 없음 — 대부분의 화면이 비어 보이는 이유"},
        {"key": "budget", "n": 2, "title": "투자 한도 정하기", "done": bool(b.get("principal")),
         "how": "넣을 돈(원금)과 최대로 잃어도 되는 돈만 정하면 나머지 한도는 자동", "link": "#budget",
         "detail": f"원금 {b['principal']:,}원 · 최대 손실 {b['max_loss']:,}원" if b.get("principal") else "아직 안 정함"},
        {"key": "watch", "n": 3, "title": "관심종목 3개 담기", "done": n_star >= 3,
         "how": "위 검색창(단축키 /)에서 종목을 찾아 ★ 를 누르세요 — 매일 AI 판단·뉴스·일정을 챙겨 드립니다", "link": "search",
         "detail": f"{n_star}/3개" + (f" · 보유 {held}종목" if held else "")},
        {"key": "today", "n": 4, "title": "'오늘 할 일' 확인하기", "done": False,
         "how": "매일 홈 맨 위 3줄만 보면 됩니다. 나머지 메뉴는 '고급'에 접혀 있습니다", "link": "#action", "detail": ""},
    ]
    need = [x for x in steps[:3] if not x["done"]]
    nxt = need[0] if need else steps[3]
    return {"steps": steps, "done": not need, "next": nxt["key"], "progress": sum(1 for x in steps[:3] if x["done"]),
            "optional": [{"title": "AI 키 넣기 (선택)", "done": bool(app.settings.has_llm),
                          "how": "없어도 규칙 AI 로 동작합니다. 무료 Gemini 키를 .env 에 넣으면 뉴스 번역·쉬운 설명이 켜집니다", "link": "#settings"}]}


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
        flows = app.cashflows(mode)
        paid = sum(float(x["amount"]) for x in flows)
        base = app.settings.initial_cash + paid  # 월 적립 등 입금은 수익이 아니다 → 원금에 더한다
        return {"mode": mode, "equity": round(eq), "cash": round(pf.cash), "n": len(pf.positions), "pnl_pct": round(eq / base - 1, 4) if base else None,
                "pnl": round(eq - base), "paid_in": round(paid), "spark": _equity_spark(app, mode, eq),
                "risk": {"level": rk.get("level"), "headline": rk.get("headline")}}

    def news():
        from .board import news_board
        b = news_board(app, days=2, limit=12, now=now)
        top = sorted(b["cards"], key=lambda c: -(c.get("level") or {}).get("score", 0))[:3]
        return {"items": [{"id": c["id"], "title": c["title"], "level": c["level"], "tone": c["tone"],
                           "symbols": [{"symbol": x["symbol"], "name": x["name"]} for x in c["symbols"][:2]], "first": c["first"]} for c in top],
                "n": len(b["cards"])}

    def todo():
        return today3(app, mode, now)

    safe("market", market)
    safe("assets", assets)
    safe("ai", lambda: ai_state(app))
    safe("news", news)
    safe("todo", todo)

    def goal_():
        from .goal import progress
        return progress(app, now)
    safe("goal", goal_)
    return out


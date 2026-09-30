"""사이트 알림 — 토스트 · 알림센터 · 데스크톱 알림의 원천.

알림은 DB(alerts)에 쌓이고, 대시보드가 몇 초마다 새 알림을 가져가 토스트로 띄운다.
같은 알림은 dedupe 키로 한 번만 만든다. 중요한 것(보유 종목 ±5%, HALTED, 강등)만 외부 알림(텔레그램 등)으로도 보낸다.

만드는 곳
  price_watch  (장중 2~3분)  급등·급락: 전일 대비 ±3/5/10% 돌파, 20분 안 ±2% 급변 — 보유·관심·코어·해외 관심 종목
  alert_scan   (2분)        새 AI 신호(방향이 바뀐 BUY/SELL) · 새 공시 · 중요 뉴스 · 예측 채점 결과 · 실적 D-1 ·
                              자동 감시 HALTED/해제
  market_pulse (1시간)      시장 상태 RISK ON/OFF 전환 (+ 24시간 관제실의 시장 흐름 기록)
  ladder / scheduler        검증 사다리 승격·강등 · 작업 연속 실패
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import ops
from .data.db import session_scope
from .data.models import AlertRecord, ConsensusRecord, Disclosure, Instrument, NewsArticle

log = logging.getLogger(__name__)
KEEP = 3000
DAY_LEVELS = (0.03, 0.05, 0.10)
FAST_MOVE = 0.02
FAST_WINDOW = timedelta(minutes=20)


def push(engine, kind: str, title: str, body: str | None = None, level: str = "info", symbol: str | None = None,
         link: str | None = None, dedupe: str | None = None, data: dict | None = None,
         now: datetime | None = None) -> int | None:
    """알림 하나 추가. 같은 dedupe 가 이미 있으면 None."""
    now = now or datetime.now(UTC)
    try:
        with session_scope(engine) as s:
            if dedupe and s.scalar(select(AlertRecord.id).where(AlertRecord.dedupe == dedupe)):
                return None
            rec = AlertRecord(ts=now, kind=kind, level=level, title=title[:300], body=(body or "")[:1000] or None,
                              symbol=symbol, link=link, dedupe=dedupe[:160] if dedupe else None, data=data)
            s.add(rec)
            s.flush()
            rid = rec.id
            if rid % 200 == 0:  # 가끔 오래된 알림 정리
                cut = s.scalar(select(AlertRecord.id).order_by(AlertRecord.id.desc()).offset(KEEP).limit(1))
                if cut:
                    s.query(AlertRecord).filter(AlertRecord.id <= cut).delete()
            return rid
    except IntegrityError:  # 동시에 같은 dedupe
        return None


def recent(engine, after: int = 0, limit: int = 50) -> dict:
    with session_scope(engine) as s:
        q = select(AlertRecord).order_by(AlertRecord.id.desc()).limit(limit)
        if after:
            q = q.where(AlertRecord.id > after)
        rows = s.scalars(q).all()
        last = s.scalar(select(func.max(AlertRecord.id))) or 0
        return {"last_id": last, "items": [{"id": r.id, "ts": r.ts.isoformat() if r.ts else None, "kind": r.kind,
                                            "level": r.level, "title": r.title, "body": r.body, "symbol": r.symbol,
                                            "link": r.link, "data": r.data} for r in rows]}


# ---------------------------------------------------------------- 관심 대상
def focus_symbols(app) -> dict[str, set[str]]:
    """알림 대상: 보유 · 관심(내가 본 종목) · 코어 · 해외 관심 종목 → {종목: {태그}}."""
    from .actions import watch_symbols
    from .analytics import main_mode
    out: dict[str, set[str]] = {}
    mode = main_mode(app)
    for m in {mode, "live"}:
        try:
            for sym in app.load_portfolio(m).positions:
                out.setdefault(sym, set()).add("보유")
        except Exception:  # noqa: BLE001, S112 - 장부가 없을 수 있다
            continue
    for sym in watch_symbols(app):
        out.setdefault(sym, set()).add("관심")
    for sym in (ops.get_state(app.engine, f"cs-plan:{mode}").get("core") or [])[:20]:
        out.setdefault(sym, set()).add("코어")
    for sym in app.load_portfolio("us-paper").positions if hasattr(app, "load_portfolio") else []:
        out.setdefault(sym, set()).add("미국 장부")
    return out


def _names(engine) -> dict[str, str]:
    with session_scope(engine) as s:
        return {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument))}


def _open_markets(now: datetime) -> set[str]:
    from .clock import MARKETS, Phase
    return {"KR" if k == "KRX" else k for k, m in MARKETS.items() if m.phase(now) is Phase.OPEN}


# ---------------------------------------------------------------- 급등·급락
def price_watch(app, now: datetime | None = None, fetchers: dict | None = None, markets: set[str] | None = None) -> dict:
    from .data.live_quotes import fetch_quotes
    now = now or datetime.now(UTC)
    markets = _open_markets(now) if markets is None else markets
    focus = focus_symbols(app)
    syms = [s for s in focus if ("KR" if s.isdigit() else "US") in markets][:40]
    if not syms:
        return {"checked": 0, "alerts": 0}
    quotes = fetch_quotes(syms, fetchers)
    names = _names(app.engine)
    state = ops.get_state(app.engine, "live_quotes")
    day = (now + timedelta(hours=9)).date().isoformat() if any(s.isdigit() for s in quotes) else now.date().isoformat()
    n = 0
    for sym, q in quotes.items():
        prev = state.get(sym) or {}
        hist = [h for h in prev.get("hist", []) if now - pd.Timestamp(h[0]).to_pydatetime() <= timedelta(hours=2)]
        hist.append([now.isoformat(), q["price"]])
        state[sym] = {**q, "hist": hist[-30:], "name": names.get(sym, sym), "tags": sorted(focus.get(sym, []))}
        name, tags = names.get(sym, sym), focus.get(sym, set())
        tag = "·".join(sorted(tags))
        chg = q.get("chg_pct")
        px = f"{q['price']:,.0f}원" if sym.isdigit() else f"${q['price']:,.2f}"
        if chg is not None:
            for lv in DAY_LEVELS:
                if abs(chg) >= lv:
                    up = chg > 0
                    rid = push(app.engine, "price", f"{name} {'급등' if up else '급락'} {chg:+.1%}",
                               f"{px} · 전일 대비 {'+' if up else '−'}{lv:.0%} 돌파 ({tag})", level="warn" if not up else "info",
                               symbol=sym, link=f"#analysis/{sym}", dedupe=f"px:{sym}:{day}:{'u' if up else 'd'}{lv}",
                               data={"dir": "up" if up else "down", "chg_pct": chg, "price": q["price"]}, now=now)
                    n += rid is not None
                    if rid and lv >= 0.05 and "보유" in tags:
                        app.notifier.send(f"보유 종목 {name} {chg:+.1%} ({px})", "warn")
        # 20분 안 급변 (장중 흐름)
        base = next((h for h in hist if now - pd.Timestamp(h[0]).to_pydatetime() <= FAST_WINDOW), None)
        if base and base[1]:
            mv = q["price"] / base[1] - 1
            if abs(mv) >= FAST_MOVE:
                bucket = now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0).isoformat()
                rid = push(app.engine, "price", f"{name} 급격한 {'상승' if mv > 0 else '하락'} {mv:+.1%}",
                           f"최근 {int((now - pd.Timestamp(base[0]).to_pydatetime()).total_seconds() // 60)}분 · 현재 {px}",
                           level="info" if mv > 0 else "warn", symbol=sym, link=f"#analysis/{sym}",
                           dedupe=f"fast:{sym}:{bucket}", data={"dir": "up" if mv > 0 else "down", "move": mv}, now=now)
                n += rid is not None
    state["_at"] = now.isoformat()
    ops.set_state(app.engine, "live_quotes", state)
    return {"checked": len(quotes), "alerts": n}


# ---------------------------------------------------------------- AI 신호 · 공시 · 뉴스 · 채점 · 일정 · 감시
def _pct(x):
    return "-" if x is None else f"{x:+.1%}"


def alert_scan(app, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    eng = app.engine
    cur = ops.get_state(eng, "alert_cursor")
    first = not cur
    focus = focus_symbols(app)
    names = _names(eng)
    made = 0
    with session_scope(eng) as s:
        max_c = s.scalar(select(func.max(ConsensusRecord.id))) or 0
        max_d = s.scalar(select(func.max(Disclosure.id))) or 0
        max_n = s.scalar(select(func.max(NewsArticle.id))) or 0
        new_c = [] if first else s.scalars(select(ConsensusRecord).where(ConsensusRecord.id > cur.get("c", 0))
                                           .order_by(ConsensusRecord.id)).all()
        sigs = []
        for c in new_c:
            if c.action not in ("BUY", "SELL") or c.symbol not in focus:
                continue
            prev = s.scalar(select(ConsensusRecord.action).where(ConsensusRecord.symbol == c.symbol,
                                                                 ConsensusRecord.id < c.id)
                            .order_by(ConsensusRecord.id.desc()))
            if prev == c.action:
                continue  # 같은 신호가 이어지는 것은 알리지 않는다
            p = c.payload or {}
            sigs.append((c.id, c.symbol, c.action, c.prob_up, p.get("expected_return"), p.get("horizon", 5), c.confidence))
        discs = [] if first else [(d.id, d.symbol, d.title, d.url) for d in s.scalars(
            select(Disclosure).where(Disclosure.id > cur.get("d", 0)).order_by(Disclosure.id)) if d.symbol in focus]
        news = [] if first else [(n.id, n.title, n.symbols or [], n.importance, n.url) for n in s.scalars(
            select(NewsArticle).where(NewsArticle.id > cur.get("n", 0), NewsArticle.importance >= 0.8)
            .order_by(NewsArticle.id).limit(200)) if set(n.symbols or []) & set(focus)]
        last_res = cur.get("res") or now.isoformat()
        resolved = [] if first else s.scalars(select(ConsensusRecord).where(
            ConsensusRecord.correct.is_not(None), ConsensusRecord.as_of > pd.Timestamp(last_res).to_pydatetime())
            .order_by(ConsensusRecord.as_of)).all()
        res_rows = [(r.id, r.symbol, r.correct, r.realized_return, (r.payload or {}).get("expected_return"), r.as_of)
                    for r in resolved]
    for cid, sym, act, p, exp, h, conf in sigs:
        made += push(eng, "signal", f"AI 신호: {names.get(sym, sym)} {act}",
                     f"상승 확률 {p:.0%} · 예상 {_pct(exp)} ({h}거래일) · 신뢰도 {conf:.0f}", level="info",
                     symbol=sym, link=f"#analysis/{sym}", dedupe=f"sig:{cid}",
                     data={"action": act, "prob_up": p, "expected": exp}, now=now) is not None
    for did, sym, title, url in discs:
        made += push(eng, "disclosure", f"공시: {names.get(sym, sym)}", title, level="info", symbol=sym,
                     link=f"#analysis/{sym}", dedupe=f"disc:{did}", data={"url": url}, now=now) is not None
    for nid, title, syms, imp, url in news:
        sym = next(x for x in syms if x in focus)
        made += push(eng, "news", f"중요 뉴스: {names.get(sym, sym)}", title, level="info", symbol=sym,
                     link=f"#analysis/{sym}", dedupe=f"news:{nid}", data={"url": url, "importance": imp},
                     now=now) is not None
    if res_rows:
        hits = sum(1 for r in res_rows if r[2])
        made += push(eng, "result", f"예측 채점 {len(res_rows)}건 — 적중 {hits}건 ({hits / len(res_rows):.0%})",
                     " · ".join(f"{names.get(sym, sym)} {'✔' if ok else '✘'} 실제 {_pct(r)}" for _, sym, ok, r, _, _ in res_rows[:6]),
                     level="good" if hits / len(res_rows) >= 0.5 else "warn", link="#control",
                     dedupe=f"res:{res_rows[-1][0]}:{len(res_rows)}", now=now) is not None
        last_res = pd.Timestamp(res_rows[-1][5]).isoformat()
    made += _earnings_alerts(app, focus, names, now)
    made += _guardian_alert(app, cur, now)
    ops.set_state(eng, "alert_cursor", {"c": max_c, "d": max_d, "n": max_n, "res": last_res,
                                        "halted": ops.halted(eng), "at": now.isoformat()})
    return {"alerts": made, "first_run": first}


def _earnings_alerts(app, focus: dict, names: dict, now: datetime) -> int:
    """캐시된 종목 상세(네트워크 없음)에서 실적 발표 D-1 · 당일."""
    from .data.fundamentals import with_d_day
    from .data.models import SystemState
    made = 0
    with session_scope(app.engine) as s:
        rows = [(r.key.split(":", 1)[1], (r.value or {}).get("data") or {})
                for r in s.scalars(select(SystemState).where(SystemState.key.like("profile:%")))]
    for sym, p in rows:
        if sym not in focus:
            continue
        for e in with_d_day([e for e in p.get("events") or [] if e.get("kind") == "earnings"]):
            if e["past"] or e["d_day"] > 1:
                continue
            made += push(app.engine, "earnings", f"{names.get(sym, sym)} 실적 발표 {'오늘' if e['d_day'] == 0 else '내일'}",
                         f"{e['date']}" + (f" · {e['time']}" if e.get("time") else "") + (f" · {e['detail']}" if e.get("detail") else ""),
                         level="warn", symbol=sym, link=f"#analysis/{sym}", dedupe=f"earn:{sym}:{e['date']}:{e['d_day']}",
                         now=now) is not None
    return made


def _guardian_alert(app, cur: dict, now: datetime) -> int:
    halted = ops.halted(app.engine)
    if not cur or halted == bool(cur.get("halted")):
        return 0
    ks = ops.get_state(app.engine, "kill_switch")
    if halted:
        return push(app.engine, "guardian", "자동 킬스위치: 매매 정지 (HALTED)", ks.get("reason") or "", level="bad",
                    link="#safety", dedupe=f"halt:{now.isoformat()[:16]}", now=now) is not None
    return push(app.engine, "guardian", "매매 정지 해제", "자동 감시 정상", level="good", link="#safety",
                dedupe=f"unhalt:{now.isoformat()[:16]}", now=now) is not None


# ---------------------------------------------------------------- 시장 흐름 (1시간)
def market_pulse(app, now: datetime | None = None) -> dict:
    from .engines.market_intel import load_macro, market_state
    now = now or datetime.now(UTC)
    bars, bench, _ = app.market_data()
    if bench is None or not bars:
        return {}
    with session_scope(app.engine) as s:
        vix = load_macro(s, ["VIXCLS"]).get("VIXCLS")
    ms = market_state(bench, bars, vix=vix)
    st = ops.get_state(app.engine, "market_pulse")
    hist = list(st.get("hist") or [])
    prev = hist[-1]["label"] if hist else None
    hist.append({"at": now.isoformat(), "label": ms["label"], "score": ms["score"]})
    ops.set_state(app.engine, "market_pulse", {"hist": hist[-24 * 14:]})
    if prev and prev != ms["label"]:
        push(app.engine, "market", f"시장 상태 전환: {prev} → {ms['label']}", f"시장 심리 {ms['score']}점",
             level="good" if ms["label"] == "RISK ON" else "warn", link="#market",
             dedupe=f"mkt:{now.isoformat()[:13]}", now=now)
    return {"label": ms["label"], "score": ms["score"]}


__all__ = ["push", "recent", "price_watch", "alert_scan", "market_pulse", "focus_symbols"]

"""사용자 화면용 묶음: 관심종목(별표) · 종목 페이지의 내 보유 · 종목별 과거 AI 적중률 · 오늘 할 일 · 종목 비교.

모두 이미 저장된 기록만 읽는다 (네트워크 호출 없음) — 화면이 빨리 뜨고, 같은 기록이면 같은 답.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from . import ops
from .asof import label

STAR_KEY = "starred"
STAR_MAX = 60


# ------------------------------------------------------------------ 관심종목 (별표)
def starred(app) -> list[str]:
    return list(ops.get_state(app.engine, STAR_KEY).get("symbols") or [])


def set_star(app, symbol: str, on: bool) -> list[str]:
    import re
    sym = (symbol or "").strip().upper()
    if not re.fullmatch(r"\d{6}|[A-Z][A-Z.\-]{0,9}", sym):
        raise ValueError("종목 코드 형식이 아닙니다")
    cur = [s for s in starred(app) if s != sym]
    if on:
        cur.append(sym)
    ops.set_state(app.engine, STAR_KEY, {"symbols": cur[-STAR_MAX:], "at": datetime.now(UTC).isoformat()})
    return cur[-STAR_MAX:]


# ------------------------------------------------------------------ 종목 페이지: 내 보유
def holdings(app, symbol: str) -> dict:
    """시스템 장부(paper/shadow/live) + 사용자가 입력한 계좌에서 이 종목 보유."""
    from . import accounts
    rows = []
    bars, _ = app._all_bars()
    b = bars.get(symbol)
    last = float(b["close"].iloc[-1]) if b is not None and len(b) else None
    for mode in ("live", "shadow", "paper", "us-paper"):
        try:
            pf = app.load_portfolio(mode)
        except Exception:  # noqa: BLE001, S112 - 장부가 없을 수 있다
            continue
        p = pf.positions.get(symbol)
        if p is not None and p.qty:
            rows.append(_hold_row(f"{mode.upper()} 장부", "system", p.qty, p.avg_price, last))
    for a in accounts.load(app.engine):
        for h in a.get("holdings") or []:
            if h.get("symbol") == symbol and h.get("qty"):
                rows.append(_hold_row(a.get("name") or a.get("id"), a.get("type") or "account", float(h["qty"]),
                                      float(h.get("avg_price") or 0) or None, last))
    total = sum(r["qty"] for r in rows)
    return {"symbol": symbol, "rows": rows, "total_qty": total, "last": last, "starred": symbol in starred(app),
            "value": round(total * last, 2) if last else None}


def _hold_row(book, kind, qty, avg, last) -> dict:
    pnl = (last / avg - 1) if (avg and last) else None
    return {"book": book, "kind": kind, "qty": qty, "avg_price": avg, "value": round(qty * last, 2) if last else None,
            "pnl_pct": None if pnl is None else round(pnl, 4), "pnl": None if pnl is None else round(qty * (last - avg), 2)}


# ------------------------------------------------------------------ 종목별 과거 AI 적중률
def track(app, symbol: str, limit: int = 500) -> dict:
    """이 종목에 대한 과거 AI 합의 판단의 채점 결과 (결과가 확정된 것만)."""
    from .data.db import session_scope
    from .data.models import ConsensusRecord
    with session_scope(app.engine) as s:
        rows = s.execute(select(ConsensusRecord.as_of, ConsensusRecord.action, ConsensusRecord.prob_up, ConsensusRecord.correct,
                                ConsensusRecord.realized_return)
                         .where(ConsensusRecord.symbol == symbol).order_by(ConsensusRecord.as_of.desc()).limit(limit)).all()
    scored = [r for r in rows if r.correct is not None]
    by = {}
    for act in ("BUY", "SELL", "HOLD", "NO_TRADE"):
        xs = [r for r in scored if r.action == act]
        if xs:
            rr = [r.realized_return for r in xs if r.realized_return is not None]
            by[act] = {"n": len(xs), "hit": round(sum(r.correct for r in xs) / len(xs), 3),
                       "avg_ret": round(float(np.mean(rr)), 4) if rr else None}
    n = len(scored)
    hit = sum(r.correct for r in scored) / n if n else None
    up_share = None
    rr_all = [r.realized_return for r in scored if r.realized_return is not None]
    if rr_all:
        up_share = sum(1 for x in rr_all if x > 0) / len(rr_all)  # 그냥 '항상 오른다'고 했을 때의 적중률 (비교 기준)
    se = (hit * (1 - hit) / n) ** 0.5 if n else None
    verdict = ("채점된 판단 없음" if not n else f"표본 {n}건 — 아직 우연과 구분 어려움" if n < 30
               else "기준(항상 상승)보다 나음" if up_share is not None and hit - 1.96 * se > up_share
               else "기준(항상 상승)보다 나쁨" if up_share is not None and hit + 1.96 * se < up_share else "기준과 통계적으로 차이 없음")
    return {"symbol": symbol, "n_total": len(rows), "n_scored": n, "hit": None if hit is None else round(hit, 3),
            "ci95": None if se is None else [round(max(hit - 1.96 * se, 0), 3), round(min(hit + 1.96 * se, 1), 3)],
            "baseline_up": None if up_share is None else round(up_share, 3), "by_action": by, "verdict": verdict,
            "recent": [{"as_of": label(r.as_of), "action": r.action, "prob_up": round(r.prob_up, 3), "correct": r.correct,
                        "ret": None if r.realized_return is None else round(r.realized_return, 4)} for r in scored[:10]]}


# ------------------------------------------------------------------ 오늘 할 일
def today(app, now: datetime | None = None) -> dict:
    """아침에 열었을 때 처리할 일 (중요한 순): 안전 → 데이터 → 보유·관심 종목 이벤트 → AI 신호 → 확인할 알림."""
    from zoneinfo import ZoneInfo

    from .clock import clock_status
    from .data.db import session_scope
    from .data.models import AlertRecord, ConsensusRecord, Instrument, OrderRecord
    now = now or datetime.now(UTC)
    kst_today = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    todo: list[dict] = []

    def add(level, title, detail="", link=""):
        todo.append({"level": level, "title": title, "detail": detail, "link": link})

    ks = ops.get_state(app.engine, "kill_switch")
    if ks.get("on"):
        add("bad", "긴급 정지 켜짐 — 신규 매수 중단 중", ks.get("reason") or "", "#safety")
    rd = ops.get_state(app.engine, "readiness")
    if rd.get("status") == "NOT_READY":
        add("bad", "매매 준비 NOT READY", "; ".join(rd.get("blockers") or [])[:220], "#readiness")
    elif rd.get("status") == "CAUTION":
        add("warn", "매매 준비 CAUTION", ", ".join(c["key"] for c in rd.get("checks") or [] if c.get("status") == "yellow"), "#readiness")
    tr = ops.get_state(app.engine, "truth")
    for b in (tr.get("bad") or [])[:3]:
        add("bad", "Truth Center 문제", b[:200], "#truth")
    held: set[str] = set()
    for mode in ("live", "shadow", "paper"):
        try:
            held |= {s for s, p in app.load_portfolio(mode).positions.items() if p.qty}
        except Exception:  # noqa: BLE001, S112
            continue
    from .actions import watch_symbols
    mine = held | set(starred(app)) | set(watch_symbols(app))
    with session_scope(app.engine) as s:
        names = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_(list(mine) or [""])))}
        stuck = s.scalar(select(OrderRecord.id).where(OrderRecord.status.in_(("pending", "submitted", "unknown")),
                                                      OrderRecord.created_at < now - timedelta(minutes=30)).limit(1))
        since = now - timedelta(days=2)
        sigs = s.execute(select(ConsensusRecord.symbol, ConsensusRecord.action, ConsensusRecord.prob_up, ConsensusRecord.confidence,
                                ConsensusRecord.as_of).where(ConsensusRecord.as_of >= since, ConsensusRecord.symbol.in_(list(mine) or [""]))
                         .order_by(ConsensusRecord.as_of.desc())).all()
        alerts = s.scalar(select(func.count()).select_from(AlertRecord).where(AlertRecord.ts >= now - timedelta(hours=24),
                                                                                AlertRecord.level.in_(("warn", "bad")))) or 0
    if stuck:
        add("bad", "30분 넘게 확정되지 않은 주문", "증권사 앱에서 확인 — 재시작 복구가 자동으로 정리합니다", "#orders")
    cal = ops.get_state(app.engine, "event_calendar")
    for e in cal.get("events") or []:
        try:
            d = date.fromisoformat(str(e["date"])[:10])
        except (KeyError, ValueError):
            continue
        dd = (d - kst_today).days
        sym = e.get("symbol")
        if sym and sym in mine and 0 <= dd <= 3:
            add("warn" if dd <= 1 else "info", f"{names.get(sym, sym)} · {e['title']} " + ("오늘" if dd == 0 else f"D-{dd}"),
                ("보유 종목" if sym in held else "관심 종목") + (" · 추정 일정" if e.get("estimated") else ""), f"#analysis/{sym}")
        elif not sym and e.get("importance", 0) >= 0.8 and 0 <= dd <= 1:
            add("warn", f"{e['title']} " + ("오늘" if dd == 0 else "내일"), f"{e.get('market', '')} 시장 이벤트 — 고베타 매수 ×0.75", "#calendar")
    seen = set()
    for r in sigs:
        if r.symbol in seen:
            continue
        seen.add(r.symbol)
        if r.action in ("BUY", "SELL") or (r.action == "NO_TRADE" and r.symbol in held):
            add("info" if r.action == "BUY" else "warn", f"{names.get(r.symbol, r.symbol)} AI {r.action}",
                f"상승 확률 {r.prob_up:.0%} · 신뢰도 {r.confidence:.0f} · {label(r.as_of)}" + (" · 보유 중" if r.symbol in held else ""),
                f"#analysis/{r.symbol}")
    if alerts:
        add("info", f"최근 24시간 경고 알림 {alerts}건", "알림 센터(종 모양)에서 확인", "#control")
    cs = clock_status(now)
    order = {"bad": 0, "warn": 1, "info": 2}
    todo.sort(key=lambda x: order[x["level"]])
    glance = _glance(app, cs)
    return {"at": now.isoformat(), "as_of": label(now), "todo": todo[:14], "n": len(todo), "clock": cs, "glance": glance,
            "held": sorted(held), "starred": starred(app)}


def _glance(app, cs) -> dict:
    """시장 한눈에: 지수 등락 · 국면 · 변동성 · 시장 시계."""
    out = {"markets": {k: {"phase": v["phase"], "holiday": v["holiday"], "next": v["next_event"], "seconds": v["seconds_to_next"]}
                       for k, v in cs["markets"].items()}}
    try:
        bars, bench, _ = app.market_data()
        if bench is not None and len(bench) > 1:
            from .data.db import session_scope
            from .data.models import Instrument
            with session_scope(app.engine) as s:
                nm = s.scalar(select(Instrument.name).where(Instrument.symbol == "KOSPI"))
            c = bench["close"]
            out["kospi"] = {"name": nm or "KOSPI", "last": round(float(c.iloc[-1]), 2), "chg": round(float(c.iloc[-1] / c.iloc[-2] - 1), 4),
                            "as_of": label(c.index[-1], with_time=False) + " 종가"}
    except Exception:  # noqa: BLE001, S110
        pass
    cal = ops.get_state(app.engine, "event_calendar")
    if cal.get("vkospi"):
        out["vkospi"] = {k: cal["vkospi"].get(k) for k in ("level", "percentile_1y", "proxy", "source") if k in cal["vkospi"]}
    return out


# ------------------------------------------------------------------ 종목 비교
def compare(app, symbols: list[str], days: int = 250) -> dict:
    from .data.db import session_scope
    from .data.models import Instrument, SystemState
    from .pipeline import latest_consensus
    syms = [s for s in dict.fromkeys(x.strip().upper() for x in symbols if x.strip())][:4]
    if len(syms) < 2:
        return {"error": "비교할 종목을 2~4개 고르세요"}
    bars, benches = app._all_bars()
    with session_scope(app.engine) as s:
        names = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_(syms)))}
        profs = {r.key.split(":", 1)[1]: ((r.value or {}).get("data") or {}).get("stats") or {}
                 for r in s.scalars(select(SystemState).where(SystemState.key.in_([f"profile:{x}" for x in syms])))}
    closes = {}
    missing = []
    for sym in syms:
        b = bars.get(sym)
        if b is None or len(b) < 20:
            missing.append(sym)
            continue
        closes[sym] = b["close"].iloc[-days:]
    if len(closes) < 2:
        return {"error": f"일봉이 없는 종목: {', '.join(missing)}", "missing": missing}
    idx = {k: pd.DatetimeIndex(v.index).normalize() for k, v in closes.items()}
    df = pd.DataFrame({k: pd.Series(v.values, index=idx[k]) for k, v in closes.items()}).sort_index().ffill().dropna()
    rets = df.pct_change().dropna()
    series = {k: [[str(t.date()), round(float(v / df[k].iloc[0] * 100), 2)] for t, v in df[k].items()] for k in df}
    rows = []
    for sym in df.columns:
        c = df[sym]
        mkt = "KR" if sym[:1].isdigit() else "US"
        bench = benches.get(mkt)
        beta = None
        if bench is not None and len(bench) > 30:
            bi = pd.Series(bench["close"].values, index=pd.DatetimeIndex(bench.index).normalize()).pct_change()
            j = pd.concat([rets[sym], bi], axis=1, join="inner").dropna()
            if len(j) > 30 and j.iloc[:, 1].var() > 0:
                beta = round(float(j.cov().iloc[0, 1] / j.iloc[:, 1].var()), 2)
        tk = track(app, sym)
        cons = latest_consensus(app.engine, sym)
        st = profs.get(sym, {})

        def ret(n, c=c):
            return round(float(c.iloc[-1] / c.iloc[-n - 1] - 1), 4) if len(c) > n else None

        dd = float((c / c.cummax() - 1).min())
        rows.append({"symbol": sym, "name": names.get(sym, sym), "last": round(float(c.iloc[-1]), 2), "as_of": label(c.index[-1], with_time=False) + " 종가",
                     "ret_1m": ret(21), "ret_3m": ret(63), "ret_1y": ret(min(len(c) - 1, 250)),
                     "vol": round(float(rets[sym].std() * np.sqrt(252)), 4), "beta": beta, "mdd": round(dd, 4),
                     "ai": None if cons is None else {"action": cons.action, "prob_up": round(cons.prob_up, 3),
                                                      "confidence": round(cons.confidence), "as_of": label(cons.as_of)},
                     "hit": tk["hit"], "n_scored": tk["n_scored"],
                     "per": st.get("per"), "pbr": st.get("pbr"), "div_yield": st.get("div_yield"), "market_cap": st.get("market_cap")})
    corr = rets.corr().round(2)
    return {"symbols": list(df.columns), "missing": missing, "from": str(df.index[0].date()), "to": str(df.index[-1].date()),
            "series": series, "rows": rows, "corr": {a: {b: float(corr.loc[a, b]) for b in corr.columns} for a in corr.index}}


__all__ = ["starred", "set_star", "holdings", "track", "today", "compare"]

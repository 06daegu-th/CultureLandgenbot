"""v25 토스식 화면용 데이터 — 화면 하나가 API 하나로 그려지게 모아 준다 (새 계산은 거의 없고, 이미 있는 엔진을 묶는다).

  home(app)            인사 · 지수 카드(코스피·나스닥·S&P500 + 60일 흐름) · AI 가 본 오늘의 종목 · 오늘의 주요 이벤트 · 내 보유 요약
  stock(app, sym)      가격 · 등락 · 시세 시각 · 시가/고가/저가/거래량/52주 · 시가총액(있으면) · 장 상태
  feed(app, ...)       뉴스 · 공시 · 일정 한 목록 (탭: 전체/뉴스/공시/이벤트 · 지역: 한국/미국 · 주제: AI/반도체/내 종목)
  portfolio(app, mode) 총 평가금액 · 손익 · 흐름 · 보유 종목(수량·평단·평가·수익률) · 자산 구성 · 거래 내역
  alerts(app)          알림 설정 화면: 종목별 가격 알림(±%) · 뉴스/공시 · 일정/AI 신호 켜고 끄기
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select

from . import ops
from .asof import label

KST = ZoneInfo("Asia/Seoul")
AI_WORDS = re.compile(r"\bAI\b|인공지능|생성형|GPU|LLM|데이터센터|data ?center|엔비디아|NVIDIA|오픈AI|OpenAI", re.I)
SEMI_WORDS = re.compile(r"반도체|semiconductor|chip|칩|HBM|파운드리|foundry|TSMC|메모리|DRAM|낸드|NAND|웨이퍼", re.I)
SEMI_SYMS = {"005930", "000660", "042700", "009150", "NVDA", "AMD", "AVGO", "TSM", "INTC", "QCOM", "MU", "ASML", "ARM", "SMCI", "SOXX"}


def _ago(t: datetime | None, now: datetime) -> str:
    if t is None:
        return ""
    t = t if t.tzinfo else t.replace(tzinfo=UTC)
    s = (now - t).total_seconds()
    if s < 60:
        return "방금"
    if s < 3600:
        return f"{int(s // 60)}분 전"
    if s < 86400:
        return f"{int(s // 3600)}시간 전"
    if s < 86400 * 7:
        return f"{int(s // 86400)}일 전"
    return t.astimezone(KST).strftime("%m.%d")


def _names(app, syms) -> dict[str, str]:
    from .companies import identity
    from .data.db import session_scope
    from .data.models import Instrument
    syms = [s for s in dict.fromkeys(syms) if s]
    with session_scope(app.engine) as s:
        out = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_(syms or [""]))) if i.name}
    for s_ in syms:
        if s_ not in out or out[s_] == s_:
            out[s_] = identity(None, s_).get("name") or s_
    return out


def _bars_for(app, sym: str):
    bars, _ = app._all_bars()
    b = bars.get(sym)
    if b is None and not sym[:1].isdigit():
        try:
            from . import global_market
            b = global_market.market_data(app, extra=[sym])[0].get(sym)
        except Exception:  # noqa: BLE001 - 해외 시세를 못 받으면 없음
            b = None
    return b


def _chg(b) -> tuple[float | None, float | None]:
    if b is None or len(b) < 2:
        return (float(b["close"].iloc[-1]) if b is not None and len(b) else None), None
    c = b["close"].astype(float)
    return float(c.iloc[-1]), float(c.iloc[-1] / c.iloc[-2] - 1)


# ------------------------------------------------------------------ 홈
def _indices(app) -> list[dict]:
    """코스피(대용) · 나스닥 · S&P500 — 있는 자료로: 국내 지수(시총가중 대용) · FRED 지수 · 없으면 QQQ/SPY ETF."""
    from .data.db import session_scope
    from .engines.market_intel import load_macro
    out = []
    _, benches = app._all_bars()
    kb = benches.get("KR")
    if kb is not None and len(kb) > 2:
        c = kb["close"].astype(float)
        out.append({"key": "KOSPI", "name": "코스피", "proxy": True, "proxy_note": "국내 상위 종목 시가총액 가중으로 계산한 대용 지수 — 실제 코스피와 숫자는 다르고 흐름만 참고", "last": round(float(c.iloc[-1]), 2),
                    "chg_pct": round(float(c.iloc[-1] / c.iloc[-2] - 1), 4), "spark": [round(float(x), 2) for x in c.iloc[-60:]],
                    "as_of": str(c.index[-1].date())})
    with session_scope(app.engine) as s:
        mac = load_macro(s, ["NASDAQCOM", "SP500"], days=120)
    for sid, name, etf in (("NASDAQCOM", "나스닥", "QQQ"), ("SP500", "S&P 500", "SPY")):
        x = mac.get(sid)
        if x is not None and len(x.dropna()) > 2:
            x = x.dropna()
            out.append({"key": sid, "name": name, "proxy": False, "last": round(float(x.iloc[-1]), 2), "chg_pct": round(float(x.iloc[-1] / x.iloc[-2] - 1), 4),
                        "spark": [round(float(v), 2) for v in x.iloc[-60:]], "as_of": str(x.index[-1].date())})
            continue
        b = _bars_for(app, etf)
        if b is not None and len(b) > 2:
            c = b["close"].astype(float)
            out.append({"key": etf, "name": f"{name} ({etf})", "proxy": True, "last": round(float(c.iloc[-1]), 2),
                        "chg_pct": round(float(c.iloc[-1] / c.iloc[-2] - 1), 4), "spark": [round(float(v), 2) for v in c.iloc[-60:]],
                        "as_of": str(c.index[-1].date())})
            continue
        out.append({"key": sid, "name": name, "missing": True, "why": "자료 없음 — FRED 경제지표 또는 미국 시세 수집이 필요해요"})
    if not any(x["key"] == "KOSPI" for x in out):
        out.insert(0, {"key": "KOSPI", "name": "코스피", "missing": True, "why": "자료 없음 — 국내 일봉을 받으면 나와요"})
    return out


def _picks(app, n: int = 6) -> list[dict]:
    """AI 가 본 오늘의 종목 — 관심·보유 종목 먼저, 그다음 상승 확률이 높은 판단 (판단이 없으면 비움)."""
    from .alerts import focus_symbols
    from .data.db import session_scope
    from .data.models import ConsensusRecord
    focus = list(focus_symbols(app))
    with session_scope(app.engine) as s:
        rows = s.scalars(select(ConsensusRecord).order_by(ConsensusRecord.as_of.desc()).limit(400)).all()
        latest: dict[str, ConsensusRecord] = {}
        for r in rows:
            latest.setdefault(r.symbol, r)
    ranked = sorted(latest.values(), key=lambda r: (r.symbol not in focus, -(r.prob_up if r.action == "BUY" else r.prob_up - 0.5)))[:n]
    names = _names(app, [r.symbol for r in ranked])
    out = []
    for r in ranked:
        last, chg = _chg(_bars_for(app, r.symbol))
        out.append({"symbol": r.symbol, "name": names.get(r.symbol, r.symbol), "action": r.action, "prob_up": round(r.prob_up, 3),
                    "last": last, "chg_pct": chg, "as_of": label(r.as_of), "focus": r.symbol in focus})
    return out


def _holdings(app, mode: str) -> dict:
    from .center import _equity_spark
    pf = app.load_portfolio(mode)
    bars, _ = app._all_bars()
    px = {s_: float(bars[s_]["close"].iloc[-1]) for s_ in pf.positions if s_ in bars and len(bars[s_])}
    eq = pf.equity(px) if px or pf.cash else pf.cash
    flows = app.cashflows(mode)
    base = app.settings.initial_cash + sum(float(x["amount"]) for x in flows)
    return {"mode": mode, "equity": round(eq), "pnl": round(eq - base), "pnl_pct": round(eq / base - 1, 4) if base else None,
            "n": sum(1 for p in pf.positions.values() if p.qty), "spark": [v for _, v in _equity_spark(app, mode, eq)]}


def home(app, mode: str = "paper", now: datetime | None = None) -> dict:
    from .center import upcoming
    now = now or datetime.now(UTC)
    h = now.astimezone(KST).hour
    greet = "좋은 아침이에요" if 5 <= h < 11 else "좋은 오후예요" if 11 <= h < 18 else "좋은 저녁이에요" if 18 <= h < 23 else "늦은 시간이에요"
    out: dict = {"greeting": f"{greet}, 오늘도 차분한 투자 되세요", "date": now.astimezone(KST).strftime("%-m월 %-d일"), "as_of": label(now)}

    def safe(k, fn):
        try:
            out[k] = fn()
        except Exception as e:  # noqa: BLE001 - 한 칸이 실패해도 나머지는 보인다
            out[k] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    safe("indices", lambda: _indices(app))
    safe("picks", lambda: _picks(app))
    safe("events", lambda: upcoming(app, now, days=30, n=6))
    safe("holdings", lambda: _holdings(app, mode))
    safe("trust", lambda: _trust(app))
    safe("markets", lambda: _markets(now))
    return out


def _trust(app) -> dict:
    from .center import ai_state
    st = ai_state(app)
    return {"key": st.get("key"), "label": st.get("label"), "why": st.get("why"), "headline": (st.get("easy") or {}).get("headline")}


def _markets(now: datetime) -> list[dict]:
    from .clock import clock_status
    out = []
    for k, short in (("KRX", "한국"), ("US", "미국")):
        m = clock_status(now)["markets"][k]
        state = "장중" if m["phase"] == "open" else (m["session_label"] if m["trading_day"] else "휴장")
        out.append({"key": k, "name": short, "state": state, "open": m["phase"] == "open", "holiday": None if m["trading_day"] else m.get("holiday"),
                    "next": m.get("next_event"), "next_kst": m.get("next_close_kst") if m.get("next_event") == "폐장" else m.get("next_open_kst")})
    return out


# ------------------------------------------------------------------ 종목
def stock(app, sym: str, now: datetime | None = None) -> dict:
    from .clock import clock_status
    from .companies import identity
    now = now or datetime.now(UTC)
    b = _bars_for(app, sym)
    idt = identity(app, sym)
    out: dict = {"symbol": sym, "identity": idt}
    if b is not None and len(b):
        c = b["close"].astype(float)
        last_bar = b.iloc[-1]
        prev = float(c.iloc[-2]) if len(c) > 1 else None
        year = b.iloc[-252:]
        out |= {"last": float(c.iloc[-1]), "prev": prev, "chg": None if prev is None else float(c.iloc[-1] - prev),
                "chg_pct": None if not prev else float(c.iloc[-1] / prev - 1),
                "bar_date": str(b.index[-1].date()),
                "stats": {"open": float(last_bar.get("open", float("nan"))), "high": float(last_bar.get("high", float("nan"))),
                          "low": float(last_bar.get("low", float("nan"))), "volume": float(last_bar.get("volume", 0) or 0),
                          "high52": float(year["high"].max()) if "high" in year else float(year["close"].max()),
                          "low52": float(year["low"].min()) if "low" in year else float(year["close"].min()),
                          "avg_volume20": float(b["volume"].iloc[-20:].mean()) if "volume" in b else None}}
        for k, v in list(out["stats"].items()):
            if v is not None and pd.isna(v):
                out["stats"][k] = None
    q = (ops.get_state(app.engine, "live_quotes").get(sym) or {})
    if q.get("price"):
        out["live"] = {"price": q["price"], "chg_pct": q.get("chg_pct"), "at": q.get("at"), "src": q.get("src")}
    prof = (ops.get_state(app.engine, f"profile:{sym}").get("data") or {})
    mc = (prof.get("stats") or {}).get("market_cap") or prof.get("market_cap")
    if mc:
        out["market_cap"] = mc
    m = clock_status(now)["markets"]["KRX" if sym[:1].isdigit() else "US"]
    out["market"] = {"state": "장중" if m["phase"] == "open" else (m["session_label"] if m["trading_day"] else "휴장"),
                     "holiday": m.get("holiday") if not m["trading_day"] else None, "tz": m.get("tz"), "dst": m.get("dst"),
                     "next": m.get("next_event"), "next_kst": m.get("next_close_kst") if m.get("next_event") == "폐장" else m.get("next_open_kst")}
    return out


# ------------------------------------------------------------------ 뉴스 · 공시 · 일정 한 목록
def feed(app, tab: str = "all", region: str = "all", topic: str = "all", days: int = 7, limit: int = 60, now: datetime | None = None) -> dict:
    from .alerts import focus_symbols
    from .data.db import session_scope
    from .data.models import Disclosure, NewsArticle
    from .news_llm import tone_value
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    mine = set(focus_symbols(app))
    items: list[dict] = []
    with session_scope(app.engine) as s:
        if tab in ("all", "news"):
            for a in s.scalars(select(NewsArticle).where(NewsArticle.published_at >= since.replace(tzinfo=None))
                               .order_by(NewsArticle.published_at.desc()).limit(400)):
                t = a.published_at if a.published_at.tzinfo else a.published_at.replace(tzinfo=UTC)
                tone = tone_value(a.sentiment, a.extract)
                items.append({"kind": "news", "id": a.id, "title": a.title, "source": a.source, "at": t, "symbols": list(a.symbols or [])[:3],
                              "tone": "긍정" if tone > 0.2 else "부정" if tone < -0.2 else "중립", "imp": a.importance or 0.5,
                              "ko": bool(re.search(r"[가-힣]", a.title or ""))})
        if tab in ("all", "disc"):
            for d in s.scalars(select(Disclosure).where(Disclosure.filed_at >= since.date()).order_by(Disclosure.filed_at.desc()).limit(200)):
                t = datetime.combine(d.filed_at, datetime.min.time(), KST if d.source == "DART" else ZoneInfo("America/New_York")).astimezone(UTC)
                items.append({"kind": "disc", "id": d.id, "title": d.title, "source": d.source, "at": t, "symbols": [d.symbol] if d.symbol else [],
                              "tone": "긍정" if (d.sentiment or 0) > 0.2 else "부정" if (d.sentiment or 0) < -0.2 else "중립", "imp": 0.7,
                              "ko": d.source == "DART"})
    if tab in ("all", "event"):
        from .center import upcoming
        for e in upcoming(app, now, days=30, n=30)["rows"]:
            items.append({"kind": "event", "id": None, "title": e["title"], "source": "일정", "at": None, "d_label": e["d_label"], "d_day": e["d_day"], "date": e.get("date"),
                          "symbols": [e["symbol"]] if e.get("symbol") else [], "market": e.get("market"), "tone": "중립", "imp": 0.6,
                          "ko": True, "ev_kind": e.get("kind")})

    def is_kr(x):
        if x["symbols"]:
            return x["symbols"][0][:1].isdigit()
        return x.get("market") == "KR" if x.get("market") else x["ko"]
    if region == "kr":
        items = [x for x in items if is_kr(x)]
    elif region == "us":
        items = [x for x in items if not is_kr(x)]
    if topic == "ai":
        items = [x for x in items if AI_WORDS.search(x["title"] or "")]
    elif topic == "semi":
        items = [x for x in items if SEMI_WORDS.search(x["title"] or "") or any(s_ in SEMI_SYMS for s_ in x["symbols"])]
    elif topic == "mine":
        items = [x for x in items if any(s_ in mine for s_ in x["symbols"])]
    news_like = sorted([x for x in items if x["kind"] != "event"], key=lambda x: x["at"], reverse=True)
    events = sorted([x for x in items if x["kind"] == "event"], key=lambda x: x["d_day"])
    merged = (events + news_like) if tab == "event" else (news_like + events if tab != "all" else news_like[:limit - min(5, len(events))] + events[:5])
    merged = merged[:limit]
    names = _names(app, [s_ for x in merged for s_ in x["symbols"]])
    for x in merged:
        if x["kind"] == "disc" and x["at"]:  # 공시는 날짜만 있다 — '15시간 전' 대신 오늘/어제/날짜
            dd = (now.astimezone(KST).date() - x["at"].astimezone(KST).date()).days
            x["ago"] = "오늘" if dd <= 0 else "어제" if dd == 1 else x["at"].astimezone(KST).strftime("%m.%d")
        else:
            x["ago"] = _ago(x["at"], now) if x["at"] else x.get("d_label")
        x["at"] = x["at"].isoformat() if x["at"] else None
        x["names"] = [names.get(s_, s_) for s_ in x["symbols"]]
        x["mine"] = any(s_ in mine for s_ in x["symbols"])
    return {"items": merged, "n": len(merged), "as_of": label(now), "filters": {"tab": tab, "region": region, "topic": topic}}


# ------------------------------------------------------------------ 포트폴리오
def portfolio(app, mode: str = "paper", now: datetime | None = None) -> dict:
    from .center import _equity_spark
    from .data.db import session_scope
    from .data.models import OrderRecord
    from .engines.sector import sector_map
    now = now or datetime.now(UTC)
    pf = app.load_portfolio(mode)
    bars, _ = app._all_bars()
    names = _names(app, list(pf.positions))
    rows, px = [], {}
    for sym, p in pf.positions.items():
        if not p.qty:
            continue
        b = bars.get(sym) if sym in bars else _bars_for(app, sym)
        last, chg = _chg(b)
        last = last if last is not None else p.avg_price
        px[sym] = last
        rows.append({"symbol": sym, "name": names.get(sym, sym), "qty": p.qty, "avg_price": p.avg_price, "last": last, "chg_pct": chg,
                     "value": round(p.qty * last), "cost": round(p.qty * p.avg_price),
                     "pnl": round(p.qty * (last - p.avg_price)), "pnl_pct": (last / p.avg_price - 1) if p.avg_price else None})
    eq = pf.cash + sum(r["value"] for r in rows)
    for r in rows:
        r["weight"] = r["value"] / eq if eq else 0
    rows.sort(key=lambda r: -r["value"])
    flows = app.cashflows(mode)
    base = app.settings.initial_cash + sum(float(x["amount"]) for x in flows)
    secs = sector_map(app.engine)
    alloc: dict[str, float] = {}
    for r in rows:
        k = secs.get(r["symbol"]) or ("해외주식" if not r["symbol"][:1].isdigit() else "기타")
        alloc[k] = alloc.get(k, 0) + r["value"]
    alloc_rows = sorted(({"name": k, "value": round(v), "weight": v / eq if eq else 0} for k, v in alloc.items()), key=lambda x: -x["value"])
    alloc_rows.append({"name": "현금", "value": round(pf.cash), "weight": pf.cash / eq if eq else 0})
    with session_scope(app.engine) as s:
        orders = s.scalars(select(OrderRecord).where(OrderRecord.mode == mode).order_by(OrderRecord.created_at.desc()).limit(40)).all()
        trades = [{"symbol": o.symbol, "side": o.side, "qty": o.qty, "price": o.avg_price or o.ref_price, "status": o.status,
                   "at": label(o.created_at), "reason": (o.reason or "")[:80]} for o in orders]
    tn = _names(app, [t["symbol"] for t in trades])
    for t in trades:
        t["name"] = tn.get(t["symbol"], t["symbol"])
    return {"mode": mode, "equity": round(eq), "cash": round(pf.cash), "stock": round(eq - pf.cash), "pnl": round(eq - base),
            "pnl_pct": (eq / base - 1) if base else None, "base": round(base), "spark": _equity_spark(app, mode, eq, n=180),
            "positions": rows, "alloc": alloc_rows, "trades": trades, "as_of": label(now)}


# ------------------------------------------------------------------ 알림 설정
ALERT_GROUPS = {"news": [("news", "중요 뉴스", "보유·관심 종목의 중요한 뉴스"), ("disclosure", "공시", "보유·관심 종목의 중요 공시 (DART·SEC)")],
                "event": [("earnings", "실적 발표", "보유·관심 종목 실적 D-1 · 결과"), ("event", "경제지표 · 일정", "CPI · FOMC · 금통위 등 큰 일정"),
                          ("signal", "AI 판단 변경", "매수 / 관망 / 매도 / 쉬어가기 가 바뀔 때"), ("guardian", "자동 정지", "안전장치가 매수를 멈출 때")]}


def alerts(app) -> dict:
    from .alerts import ROUTE, active_rules
    from .center import watchlist
    from .prefs import channel_on
    from .prefs import get as get_prefs
    notify = get_prefs(app.engine)["notify"]
    rules = [r for r in active_rules(app.engine) if r["active"] and r["kind"] == "move"]
    by_sym = {r["symbol"]: r for r in rules}
    w = watchlist(app)["rows"]
    stocks = [{"symbol": r["symbol"], "name": r["name"], "last": r["last"], "on": r["symbol"] in by_sym,
               "value": by_sym[r["symbol"]]["value"] if r["symbol"] in by_sym else 5.0, "rule_id": (by_sym.get(r["symbol"]) or {}).get("id")}
              for r in w if r["starred"] or r["held"]]

    def on(kind):
        return channel_on(notify, "push", kind, kind in ROUTE["push_kinds"]) or channel_on(notify, "external", kind, kind in ROUTE["kinds"])
    groups = {g: [{"kind": k, "title": t, "desc": d, "on": on(k)} for k, t, d in rows] for g, rows in ALERT_GROUPS.items()}
    return {"price": stocks, **groups, "channels": {"external": ROUTE.get("notifier") is not None, "push": ROUTE.get("push") is not None},
            "note": "사이트 알림센터에는 모두 쌓입니다 — 여기서는 휴대폰(웹 푸시)·텔레그램으로 보낼지를 정합니다"}


def alerts_write(app, body: dict) -> dict:
    """{kind, on} → 웹 푸시·텔레그램 둘 다 켜고 끄기 · {symbol, price_on, value} → 종목 가격 알림(±%) 만들기/끄기."""
    from .alerts import add_rule, update_rule
    from .prefs import get as get_prefs
    from .prefs import save
    if body.get("kind"):
        k = str(body["kind"])
        n = get_prefs(app.engine)["notify"]
        ext, push = dict(n.get("external") or {}), dict(n.get("push") or {})
        ext[k] = push[k] = bool(body.get("on"))
        save(app.engine, {"notify": {**n, "external": ext, "push": push}})
        return {"ok": True, "kind": k, "on": bool(body.get("on"))}
    sym = str(body.get("symbol") or "")[:12]
    if not sym:
        raise ValueError("kind 또는 symbol 필요")
    if body.get("price_on"):
        r = add_rule(app.engine, sym, "move", float(body.get("value") or 5), note="v25 알림 설정", repeat=True)
        return {"ok": True, "rule_id": r.get("id")}
    if body.get("rule_id"):
        update_rule(app.engine, int(body["rule_id"]), False, True)
    return {"ok": True, "off": True}


# ------------------------------------------------------------------ 시장
def market(app, now: datetime | None = None) -> dict:
    """시장 화면: 지수 카드 · 한국/미국 장 상태 · 많이 오른/내린 종목(거래대금 상위 안에서) · 시장 일정."""
    from .center import upcoming
    now = now or datetime.now(UTC)
    out: dict = {"as_of": label(now)}
    try:
        out["indices"] = _indices(app)
    except Exception as e:  # noqa: BLE001
        out["indices"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    out["markets"] = _markets(now)
    bars, _ = app._all_bars()
    rows = []
    for sym, b in bars.items():
        if b is None or len(b) < 2:
            continue
        last, chg = _chg(b)
        if chg is None or not last:
            continue
        tv = float(b["close"].iloc[-1] * b["volume"].iloc[-1]) if "volume" in b else 0.0
        rows.append({"symbol": sym, "last": last, "chg_pct": chg, "turnover": tv, "date": str(b.index[-1].date())})
    out_mv = {}
    for mk, pick in (("kr", lambda r: r["symbol"][:1].isdigit()), ("us", lambda r: not r["symbol"][:1].isdigit())):
        xs = sorted([r for r in rows if pick(r)], key=lambda r: -r["turnover"])[:200]  # 거래가 적은 종목의 큰 등락은 빼고
        out_mv[mk] = {"up": sorted(xs, key=lambda r: -r["chg_pct"])[:5], "down": sorted(xs, key=lambda r: r["chg_pct"])[:5],
                      "turnover": xs[:5], "n": len(xs), "date": max((r["date"] for r in xs), default=None)}
    names = _names(app, [r["symbol"] for m in out_mv.values() for k in ("up", "down", "turnover") for r in m[k]])
    for m in out_mv.values():
        for k in ("up", "down", "turnover"):
            for r in m[k]:
                r["name"] = names.get(r["symbol"], r["symbol"])
    out["movers"] = out_mv
    try:
        out["events"] = [e for e in upcoming(app, now, days=30, n=30)["rows"] if e.get("scope") == "시장"][:8]
    except Exception as e:  # noqa: BLE001
        out["events"] = {"error": f"{type(e).__name__}"}
    return out


# ------------------------------------------------------------------ 여러 종목 시세 · AI 리포트
def quotes(app, syms: list[str]) -> dict:
    """관련 종목 카드 등에 쓰는 가벼운 시세 묶음 (일봉 종가 기준 · 실시간 시세가 있으면 그것)."""
    syms = [s_ for s_ in dict.fromkeys(syms) if s_][:20]
    names = _names(app, syms)
    live = ops.get_state(app.engine, "live_quotes")
    out = []
    for s_ in syms:
        q = live.get(s_) or {}
        last, chg = _chg(_bars_for(app, s_))
        if q.get("price"):
            last, chg = q["price"], q.get("chg_pct", chg)
        out.append({"symbol": s_, "name": names.get(s_, s_), "last": last, "chg_pct": chg})
    return {"rows": out}


def report(app, sym: str) -> dict:
    """AI 투자 분석 리포트 — 최종 판단 + 전일(직전 판단) 대비 변화 + 최근 판단 흐름."""
    from .data.db import session_scope
    from .data.models import ConsensusRecord
    with session_scope(app.engine) as s:
        rows = s.scalars(select(ConsensusRecord).where(ConsensusRecord.symbol == sym).order_by(ConsensusRecord.as_of.desc()).limit(30)).all()
        hist = [{"at": label(r.as_of), "date": r.as_of.strftime("%m.%d"), "action": r.action, "prob_up": round(r.prob_up, 3)} for r in reversed(rows)]
    prev = hist[-2] if len(hist) > 1 else None
    return {"symbol": sym, "history": hist, "prev": prev,
            "delta": round(hist[-1]["prob_up"] - prev["prob_up"], 3) if prev else None}


__all__ = ["home", "stock", "feed", "portfolio", "alerts", "alerts_write", "quotes", "report", "market"]

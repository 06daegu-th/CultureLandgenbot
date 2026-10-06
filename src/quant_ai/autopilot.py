"""v30 AI 자동매매 (오토파일럿) — 신호 엔진 2.0 의 후보로 매일 스스로 사고판다.

흐름 (하루 한 번, 국내 정규장 09:10~15:10 사이 첫 사이클)
  1. 신호 엔진 2.0 계산 (일봉이 최신이 아니면 그날은 쉰다 — 오래된 가격으로 사지 않음)
  2. 가진 종목 점검: 손절선 도달 · 점수 하락(0 이하) · 20거래일이 지났는데 더 이상 후보가 아님 · 후보 계산 대상에서 빠짐 → 판다
  3. 빈 자리에 점수 높은 순으로 산다 (점수 +0.8 이상 · 1주 가격 ≤ 종목당 상한 · 시장이 200일선 아래면 자리 절반·기준 +0.5)
  4. 자리가 꽉 찼는데 훨씬 좋은 후보(점수 차 1.0 이상)가 있으면 가장 약한 종목 하나만 교체
  5. 결정과 이유를 그날 한 번 봉인 기록 (앞 기록 해시에 이어 붙임)
주문은 기존 매매 파이프라인(리스크 엔진 · 킬스위치 · 매매 준비 점검 · 중복 방지 · 장부)을 그대로 지난다.

단계
  - 가상 100만원 장부('autopilot')는 늘 자동 운용 — 실제 돈 없이 '실제로 굴려 본 기록'을 쌓는다
  - 실제 계좌는 관문 5개를 모두 넘고 사용자가 직접 켜야 연결된다:
    전진 기록(매수 후보 60건+ · 이긴 비율 55%+ · 평균 초과수익 > 0) · 가상 장부 40거래일+ 코스피 초과 ·
    KIS 전 과정 검증 · 증명 프로젝트(실제 계좌) 진행 중 · 사용자 켜기
목표 현실성: 같은 규칙을 '보지 않은 기간'에 돌려 본 일별 수익으로 6·12개월 결과를 수천 번 뽑아
'100만원 → 1억' 확률 · 두 배 확률 · 원금 -15% 정지 확률 · 중간값을 그대로 보여 준다.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, time, timedelta

import numpy as np
import pandas as pd

from . import ops

BOOK = "autopilot"
STATE = "autopilot"
LOG = "autopilot_log"
DEFAULTS = {"principal": 1_000_000, "max_positions": 5, "max_position_weight": 0.20, "cash_buffer": 0.10, "buy_min": 0.8,
            "sell_below": 0.0, "max_hold_days": 20, "replace_gap": 1.0, "paper_on": True, "live_requested": False}
GATE_FWD_N, GATE_FWD_HIT, GATE_PAPER_DAYS = 60, 0.55, 40
TRADE_START, TRADE_END = time(9, 10), time(15, 10)
ROUNDTRIP_COST = 0.0033  # 수수료 왕복 0.03% + 거래세 0.20% + 미끄러짐 왕복 0.10%


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def config(app) -> dict:
    return {**DEFAULTS, **(ops.get_state(app.engine, STATE).get("config") or {})}


def set_config(app, body: dict) -> dict:
    st = ops.get_state(app.engine, STATE)
    cfg = {**DEFAULTS, **(st.get("config") or {})}
    for k in ("paper_on", "live_requested"):
        if k in body:
            cfg[k] = bool(body[k])
    ops.set_state(app.engine, STATE, {**st, "config": cfg})
    return cfg


def _kst(t: datetime) -> datetime:
    from .asof import KST
    return t.astimezone(KST)


def ensure_book(app, principal: float, ts: datetime | None = None) -> None:
    """가상 장부를 원금으로 시작 (처음 한 번)."""
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import PortfolioSnapshot
    with session_scope(app.engine) as s:
        if s.scalar(select(PortfolioSnapshot.id).where(PortfolioSnapshot.mode == BOOK).limit(1)) is None:
            # 시작 기록은 첫 결정 '직전' 시각으로 (뒤에 남는 매매 기록보다 늦으면 장부가 빈 것으로 읽힌다)
            s.add(PortfolioSnapshot(mode=BOOK, ts=(ts or datetime.now(UTC)) - timedelta(seconds=1), cash=float(principal), equity=float(principal), positions={}))


# ------------------------------------------------------------------ 오늘의 결정 (순수 계산 — 주문 없음)
def plan(full: dict, holdings: dict[str, dict], cash: float, equity: float, cfg: dict, today: str) -> dict:
    """full = signals2.compute_all 결과 · holdings = {종목: {qty, entry_date, entry_price, stop, held_days}}."""
    rows = full.get("_rows") or {}
    regime_up = (full.get("regime") or {}).get("above_200")
    slots = cfg["max_positions"] if regime_up is not False else max(1, math.ceil(cfg["max_positions"] / 2))
    buy_min = cfg["buy_min"] + (0.5 if regime_up is False else 0.0)
    w = min(cfg["max_position_weight"], (1 - cfg["cash_buffer"]) / cfg["max_positions"])
    cap_value = equity * w
    actions, keep = [], {}
    for sym, h in holdings.items():
        r = rows.get(sym)
        name = (r or {}).get("name", sym)
        if r is None:
            actions.append({"symbol": sym, "name": name, "action": "sell", "reason": "후보 계산 대상에서 빠짐 (거래대금 부족·시세 끊김)", "score": None})
            continue
        px, sc = float(r["last"]), float(r["score"])
        if h.get("stop") and px <= h["stop"]:
            actions.append({"symbol": sym, "name": name, "action": "sell", "reason": f"손절선 {h['stop']:,.0f} 도달 (지금 {px:,.0f})", "score": sc})
        elif sc <= cfg["sell_below"]:
            actions.append({"symbol": sym, "name": name, "action": "sell", "reason": f"점수 {sc:+.2f} 로 하락 — 근거가 사라짐", "score": sc})
        elif h.get("held_days", 0) >= cfg["max_hold_days"] and sc < buy_min:
            actions.append({"symbol": sym, "name": name, "action": "sell", "reason": f"{h['held_days']}거래일 보유 · 더 이상 매수 후보 아님 (점수 {sc:+.2f})", "score": sc})
        else:
            keep[sym] = sc
    cands = [r for r in sorted(rows.values(), key=lambda x: -x["score"])
             if r["score"] >= buy_min and r["symbol"] not in holdings]
    skipped = []
    affordable = []
    for r in cands:
        if float(r["last"]) > cap_value:
            skipped.append({"symbol": r["symbol"], "name": r["name"], "score": r["score"], "why": f"1주 {float(r['last']):,.0f}원 > 종목당 상한 {cap_value:,.0f}원"})
        else:
            affordable.append(r)
    free = max(0, slots - len(keep))
    buys = affordable[:free]
    if not free and affordable and keep:  # 교체: 가장 약한 보유 vs 가장 좋은 후보
        weakest = min(keep, key=lambda s: keep[s])
        best = affordable[0]
        if best["score"] - keep[weakest] >= cfg["replace_gap"]:
            actions.append({"symbol": weakest, "name": rows[weakest]["name"], "action": "sell",
                            "reason": f"더 좋은 후보로 교체 ({best['name']} {best['score']:+.2f} vs {keep[weakest]:+.2f})", "score": keep[weakest]})
            keep.pop(weakest)
            buys = [best]
    if len(keep) > slots:  # 시장이 나빠져 자리가 줄면 약한 순으로 정리
        for sym in sorted(keep, key=lambda s: keep[s])[: len(keep) - slots]:
            actions.append({"symbol": sym, "name": rows[sym]["name"], "action": "sell", "reason": "시장이 200일선 아래 — 보유 종목 수를 줄임", "score": keep[sym]})
            keep.pop(sym)
    for r in buys:
        ev = r.get("evidence") or {}
        why = " · ".join(s["text"] for s in (r.get("signals") or []) if s.get("verified"))[:120]
        actions.append({"symbol": r["symbol"], "name": r["name"], "action": "buy", "score": r["score"], "price": float(r["last"]), "stop": r.get("stop"),
                        "reason": f"점수 {r['score']:+.2f}" + (f" · {why}" if why else "") + (f" · 과거 같은 점수대 {ev.get('n')}번 중 {ev.get('hit', 0) * 100:.0f}% 시장보다 상승" if ev.get("n") else "")})
    for sym, sc in keep.items():
        actions.append({"symbol": sym, "name": rows[sym]["name"], "action": "hold", "score": sc, "reason": f"점수 {sc:+.2f} · 계속 보유"})
    targets = {a["symbol"]: w for a in actions if a["action"] in ("buy", "hold")}
    return {"date": today, "as_of": full.get("as_of"), "slots": slots, "buy_min": buy_min, "weight": round(w, 4), "cap_value": round(cap_value),
            "regime": (full.get("regime") or {}).get("text"), "actions": actions, "targets": targets, "skipped": skipped[:8],
            "tier": (full.get("calibration") or {}).get("tier")}


# ------------------------------------------------------------------ 관문 (실제 계좌 연결 조건)
def gates(app, cfg: dict | None = None) -> list[dict]:
    from . import proof
    cfg = cfg or config(app)
    out = []
    try:
        from .signals2 import cached
        fw = (cached(app, "KR").get("forward") or {}).get("buy") or {}
    except Exception:  # noqa: BLE001
        fw = {}
    n, hit, mean = fw.get("n") or 0, fw.get("hit"), fw.get("mean_excess")
    out.append({"key": "forward", "title": "신호 엔진 전진 기록", "ok": n >= GATE_FWD_N and (hit or 0) >= GATE_FWD_HIT and (mean or 0) > 0,
                "detail": f"채점된 매수 후보 {n}/{GATE_FWD_N}건 · 이긴 비율 {'-' if hit is None else f'{hit:.0%}'} (기준 {GATE_FWD_HIT:.0%}) · 평균 초과 {'-' if mean is None else f'{mean * 100:+.1f}%'}"})
    p = paper_performance(app)
    out.append({"key": "paper", "title": "가상 100만원 자동 운용 성적", "ok": p["days"] >= GATE_PAPER_DAYS and (p.get("excess") or 0) > 0,
                "detail": f"{p['days']}/{GATE_PAPER_DAYS}거래일 · 누적 {_pc(p.get('ret'))} · 코스피 대비 {_pc(p.get('excess'))}"})
    v = ops.get_state(app.engine, "kis_validation")
    e2e = any(h.get("e2e") for h in (v.get("history") or [])) or bool(v.get("e2e_verified"))
    out.append({"key": "kis", "title": "KIS 주문 전 과정 검증", "ok": e2e,
                "detail": "주문→체결→장부→잔고 대조 확인됨" if e2e else "./run.sh kis-check --suite --e2e (모의투자 · 장중)"})
    a = proof.active(app)
    out.append({"key": "proof", "title": "증명 프로젝트 (실제 계좌) 진행 중", "ok": bool(a and a.get("mode") == "live"),
                "detail": f"{a['id']} · {a['mode']}" if a else "화면 '증명 프로젝트'에서 실제 계좌로 시작 (규칙·기준 봉인)"})
    out.append({"key": "user", "title": "사용자가 실제 계좌 자동매매를 켬", "ok": bool(cfg.get("live_requested")),
                "detail": "켜져 있음" if cfg.get("live_requested") else "아래 '실제 계좌 연결 요청'을 눌러야 해요 — 관문을 모두 넘어도 사람이 켜야 시작"})
    return out


def live_enabled(app) -> bool:
    return all(g["ok"] for g in gates(app))


def _pc(v) -> str:
    return "-" if v is None else f"{v * 100:+.1f}%"


def paper_performance(app) -> dict:
    """가상 장부 평가금액 흐름 (하루 마지막 값) vs 코스피 대용 — 시작 = 원금."""
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import PortfolioSnapshot
    with session_scope(app.engine) as s:
        rows = s.execute(select(PortfolioSnapshot.ts, PortfolioSnapshot.equity).where(PortfolioSnapshot.mode == BOOK)
                         .order_by(PortfolioSnapshot.ts, PortfolioSnapshot.id)).all()
    if not rows:
        return {"days": 0, "curve": []}
    eq = pd.Series([r[1] for r in rows], index=pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows]))
    eq.index = eq.index.tz_localize("UTC") if eq.index.tz is None else eq.index.tz_convert("UTC")
    daily = eq.groupby(eq.index.tz_convert("Asia/Seoul").date).last()
    days = len(daily) - 1  # 첫 값은 시작 원금
    ret = float(daily.iloc[-1] / daily.iloc[0] - 1) if len(daily) > 1 else 0.0
    bench = None
    try:
        _, b, _ = app.market_data()
        if b is not None and len(b) and len(daily) > 1:
            bc = b["close"].astype(float)
            bc.index = pd.DatetimeIndex(bc.index).tz_convert("Asia/Seoul").date if pd.DatetimeIndex(bc.index).tz is not None else pd.DatetimeIndex(bc.index).date
            sub = bc[(bc.index >= daily.index[0]) & (bc.index <= daily.index[-1])]
            bench = float(sub.iloc[-1] / sub.iloc[0] - 1) if len(sub) > 1 else None
    except Exception:  # noqa: BLE001
        bench = None
    peak = daily.cummax()
    return {"days": max(days, 0), "ret": ret, "bench": bench, "excess": None if bench is None else ret - bench,
            "equity": float(daily.iloc[-1]), "start": float(daily.iloc[0]), "mdd": float((daily / peak - 1).min()),
            "curve": [[str(d), round(float(v), 2)] for d, v in daily.items()][-260:]}


# ------------------------------------------------------------------ 실행
def _holdings(app, book: str, today) -> dict:
    pf = app.load_portfolio(book)
    meta = (ops.get_state(app.engine, STATE).get("meta") or {}).get(book) or {}
    from .clock import KRX
    out = {}
    try:
        from .signals2 import _names
        names = _names(app.engine, [s for s, p in pf.positions.items() if p.qty])
    except Exception:  # noqa: BLE001
        names = {}
    for sym, p in pf.positions.items():
        if not p.qty:
            continue
        m = meta.get(sym) or {}
        held = 0
        if m.get("entry_date"):
            try:
                held = KRX.trading_days_between(datetime.fromisoformat(m["entry_date"]).date(), today)
            except (ValueError, TypeError):
                held = 0
        out[sym] = {"name": names.get(sym, sym), "qty": p.qty, "avg_price": p.avg_price, "entry_date": m.get("entry_date"), "stop": m.get("stop"), "held_days": held}
    return out


def run(app, now: datetime | None = None, force: bool = False, full: dict | None = None) -> dict:
    """하루 한 번 결정 → 가상 장부(늘) · 실제 계좌(관문 통과 + 켬) 주문. force 면 시간·하루 한 번 제한 없이."""
    from .config import Mode
    from .trading.execution import Signal
    now = now or datetime.now(UTC)
    cfg = config(app)
    k = _kst(now)
    st = ops.get_state(app.engine, STATE)
    today = k.date().isoformat()
    if not force:
        from .clock import KRX, Phase
        if KRX.phase(now) is not Phase.OPEN or not (TRADE_START <= k.time() <= TRADE_END):
            return {"skipped": "장중(09:10~15:10)에만 결정해요"}
        if st.get("last_run") == today:
            return {"skipped": "오늘은 이미 결정했어요"}
    if full is None:
        from .signals2 import cached
        full = cached(app, "KR")
    if full.get("error"):
        return {"skipped": full["error"]}
    if not force:
        from .clock import KRX
        try:
            lag = KRX.trading_days_between(datetime.fromisoformat(str(full["as_of"])).date(), k.date())
        except (KeyError, ValueError):
            lag = 99
        if lag > 1:
            _set_state(app, {"last_run": today, "last_skip": f"일봉이 {lag}거래일 밀려 오늘은 쉬어요 (오래된 가격으로 사지 않음)"})
            return {"skipped": f"일봉 {lag}거래일 밀림"}
    out = {"date": today, "books": {}}
    books = []
    if cfg.get("paper_on", True):
        ensure_book(app, cfg["principal"], now)
        books.append((BOOK, Mode.PAPER, BOOK))
    live_ok = live_enabled(app)
    if live_ok:
        books.append(("live", Mode.LIVE, None))
    for name, mode, book in books:
        pf = app.load_portfolio(name)
        rows = full.get("_rows") or {}
        prices = {s: float(r["last"]) for s, r in rows.items()}
        for s, p in pf.positions.items():
            prices.setdefault(s, p.avg_price)
        equity = pf.equity(prices)
        hold = _holdings(app, name, k.date())
        pl = plan(full, hold, pf.cash, equity, cfg, today)
        sigs = [Signal(s, w, reason="AI 자동매매: " + next((a["reason"] for a in pl["actions"] if a["symbol"] == s), "")[:150])
                for s, w in pl["targets"].items()]
        fills = []
        try:
            fills = app.trade([], mode, ts=now, signals=sigs, book=book) or []
        except Exception as e:  # noqa: BLE001 - 한 장부 실패가 다른 장부를 막지 않게 (기록)
            pl["error"] = f"{type(e).__name__}: {e}"[:200]
        _update_meta(app, name, pl, fills, today)
        pl["fills"] = [{"symbol": f.order.symbol, "side": f.order.side.value, "qty": f.qty, "price": round(f.price, 2)} for f in fills]
        out["books"][name] = pl
        _seal(app, name, pl, now)
    _set_state(app, {"last_run": today, "last_skip": None, "last": {n: {k2: v for k2, v in p.items() if k2 != "targets"} for n, p in out["books"].items()}})
    return out


def _set_state(app, patch: dict) -> None:
    st = ops.get_state(app.engine, STATE)
    ops.set_state(app.engine, STATE, {**st, **patch})


def _update_meta(app, book: str, pl: dict, fills, today: str) -> None:
    st = ops.get_state(app.engine, STATE)
    meta = dict((st.get("meta") or {}).get(book) or {})
    buys = {a["symbol"]: a for a in pl["actions"] if a["action"] == "buy"}
    for f in fills:
        s = f.order.symbol
        if f.order.side.value == "buy" and s in buys and s not in meta:
            meta[s] = {"entry_date": today, "entry_price": round(f.price, 2), "stop": buys[s].get("stop")}
    pf = app.load_portfolio(book if book != "live" else "live")
    meta = {s: m for s, m in meta.items() if pf.qty(s) > 0}
    ops.set_state(app.engine, STATE, {**st, "meta": {**(st.get("meta") or {}), book: meta}})


def _seal(app, book: str, pl: dict, now: datetime) -> None:
    lg = ops.get_state(app.engine, LOG)
    days = list(lg.get(book) or [])
    rec = {"date": pl["date"], "as_of": pl.get("as_of"), "at": now.isoformat(), "actions": [{k: a.get(k) for k in ("symbol", "action", "score", "reason")} for a in pl["actions"]],
           "fills": pl.get("fills"), "error": pl.get("error"), "prev": days[-1]["hash"] if days else "genesis"}
    rec["hash"] = _h(rec)
    days.append(rec)
    ops.set_state(app.engine, LOG, {**lg, book: days[-400:]})


def preview(app) -> dict:
    """주문 없이 '지금 돌리면 이렇게 하겠다' (화면용)."""
    from .signals2 import cached
    full = cached(app, "KR")
    if full.get("error"):
        return {"error": full["error"]}
    cfg = config(app)
    ensure_book(app, cfg["principal"])
    pf = app.load_portfolio(BOOK)
    rows = full.get("_rows") or {}
    prices = {s: float(r["last"]) for s, r in rows.items()}
    for s, p in pf.positions.items():
        prices.setdefault(s, p.avg_price)
    k = _kst(datetime.now(UTC))
    return plan(full, _holdings(app, BOOK, k.date()), pf.cash, pf.equity(prices), cfg, k.date().isoformat())


def status(app) -> dict:
    cfg = config(app)
    st = ops.get_state(app.engine, STATE)
    g = gates(app, cfg)
    try:
        pv = preview(app)
    except Exception as e:  # noqa: BLE001
        pv = {"error": f"{type(e).__name__}: {e}"[:200]}
    lg = ops.get_state(app.engine, LOG)
    perf = paper_performance(app)
    hold = _holdings(app, BOOK, _kst(datetime.now(UTC)).date())
    return {"config": cfg, "gates": g, "live": all(x["ok"] for x in g), "preview": pv, "paper": perf,
            "holdings": hold, "last_run": st.get("last_run"), "last_skip": st.get("last_skip"),
            "log": (lg.get(BOOK) or [])[-20:], "log_live": (lg.get("live") or [])[-20:],
            "goal": st.get("goal"), "rule": "하루 한 번 장중(09:10~15:10) · 점수 +0.8 이상 매수 · 점수 0 이하·손절선·20거래일 경과 시 매도 · 최대 5종목 · 종목당 18% · 현금 10% 남김"}


# ------------------------------------------------------------------ 목표 현실성 (100만원 → 1억)
def backtest(app, bars=None, top: int = 5, hold: int = 20) -> dict:
    """같은 규칙(점수 상위 · 1주 ≤ 상한 · 20일마다 교체 · 왕복 비용)을 '가중치를 정할 때 보지 않은 기간'에 돌린 일별 수익."""
    from . import signals2 as S2
    if bars is None:
        bars, _ = S2._bars_for(app, "KR")
    close, volume = S2.panels(bars)
    if close.empty or len(close) < 400:
        return {"error": "일봉이 부족해요 (400거래일 이상 필요)"}
    from .engines.sector import sector_map
    sig = S2.price_signals(close, volume, sector_map(app.engine))
    learned = S2.learn_weights(sig, close)
    comb = S2.combine(sig, learned["weights"])
    t20 = (close * volume).rolling(20, min_periods=10).mean()
    rets = close.pct_change(fill_method=None)
    start = len(close) - S2.HOLDOUT - S2.HORIZON  # 보지 않은 기간
    idx = close.index
    cap = DEFAULTS["principal"] * min(DEFAULTS["max_position_weight"], (1 - DEFAULTS["cash_buffer"]) / DEFAULTS["max_positions"])
    w = min(DEFAULTS["max_position_weight"], (1 - DEFAULTS["cash_buffer"]) / DEFAULTS["max_positions"])
    daily, picks_log = [], []
    held: list[str] = []
    for i in range(start, len(idx) - 1):
        if (i - start) % hold == 0:
            sc = comb.iloc[i]
            ok = (t20.iloc[i] >= 1e9) & (close.iloc[i] <= cap) & sc.notna() & (sc >= DEFAULTS["buy_min"])
            new = list(sc[ok].sort_values(ascending=False).index[:top])
            turnover = len(set(new) ^ set(held)) / max(1, top) / 2
            held = new
            picks_log.append({"date": str(idx[i].date()), "picks": new})
            cost = turnover * ROUNDTRIP_COST * w * top
        else:
            cost = 0.0
        r = rets.iloc[i + 1][held].fillna(0).mean() * w * len(held) if held else 0.0
        daily.append(float(r - cost))
    mkt = rets.iloc[start + 1:].mean(axis=1).fillna(0).values
    d = np.array(daily)
    return {"from": str(idx[start].date()), "to": str(idx[-1].date()), "days": len(d), "daily": d.tolist(), "market": mkt.tolist(),
            "total": float(np.prod(1 + d) - 1), "market_total": float(np.prod(1 + mkt) - 1),
            "avg_positions": float(np.mean([len(p["picks"]) for p in picks_log])) if picks_log else 0.0,
            "rebalances": len(picks_log), "note": "가중치를 정할 때 보지 않은 기간 · 비용(왕복 0.33%) 반영 · 1주 20만원 이하 종목만"}


def goal_odds(app, start: float = 1_000_000, target: float = 100_000_000, sims: int = 4000, seed: int = 7, bt: dict | None = None) -> dict:
    """보지 않은 기간의 일별 수익을 20일 묶음으로 다시 뽑아(블록 부트스트랩) 6·12개월 결과 분포."""
    bt = bt or backtest(app)
    if bt.get("error"):
        return {"error": bt["error"]}
    d = np.array(bt["daily"])
    if len(d) < 40:
        return {"error": "보지 않은 기간이 너무 짧아요"}
    rng = np.random.default_rng(seed)
    block = 20
    out = {"start": start, "target": target, "multiple": target / start, "source": bt, "horizons": []}
    for months, days in ((6, 126), (12, 252)):
        n_blocks = math.ceil(days / block)
        starts = rng.integers(0, len(d) - block, size=(sims, n_blocks))
        paths = np.stack([d[s:s + block] for s in starts.ravel()]).reshape(sims, n_blocks * block)[:, :days]
        eq = np.cumprod(1 + paths, axis=1)
        final = eq[:, -1]
        hit_stop = (eq.min(axis=1) <= 0.85).mean()
        ann = float(np.prod(1 + d) ** (252 / len(d)) - 1)
        need = (target / start) ** (1 / (months / 12)) - 1
        out["horizons"].append({"months": months, "p_target": float((final >= target / start).mean()), "p_double": float((final >= 2).mean()),
                                "p_gain": float((final > 1).mean()), "p_stop": float(hit_stop), "median": float(np.median(final) * start),
                                "p10": float(np.percentile(final, 10) * start), "p90": float(np.percentile(final, 90) * start),
                                "need_annual": need, "need_monthly": (target / start) ** (1 / months) - 1, "hist_annual": ann})
    out["verdict"] = (f"이 규칙의 '보지 않은 기간' 성과로는 100만원 → 1억(100배)이 {sims:,}번 시뮬레이션 중 한 번도 나오지 않아요"
                      if out["horizons"][-1]["p_target"] < 1 / sims else "아주 드물게 나와요 — 그래도 운에 가까운 결과예요")
    out["advice"] = ["목표를 '1년 동안 봉인된 기록으로 코스피를 이긴다'로 두면 실제로 증명할 수 있어요 (증명 프로젝트)",
                     "100배를 노리려면 레버리지·몰빵·급등주밖에 없고, 그 길은 원금 전부를 잃을 확률이 훨씬 높아요 — 이 시스템은 그 길을 막아 둡니다",
                     "기록이 좋으면 그 기록이 플랫폼의 가장 강한 근거가 돼요 (투자자·사용자·심사 모두 '기록'을 봅니다)"]
    return out


def refresh_goal(app) -> dict:
    g = goal_odds(app)
    slim = {k: v for k, v in g.items() if k != "source"}
    if "source" in g:
        slim["source"] = {k: v for k, v in g["source"].items() if k not in ("daily", "market")}
    slim["at"] = datetime.now(UTC).isoformat()
    _set_state(app, {"goal": slim})
    return slim


__all__ = ["run", "plan", "gates", "status", "preview", "backtest", "goal_odds", "refresh_goal", "live_enabled", "BOOK", "set_config"]

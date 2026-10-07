"""모바일 3~4번 터치 주문: 검색 → 종목 → AI → [모의 주문].

실제 돈이 아닌 '수동 모의 장부'(mode=manual)에만 체결한다 — 전략의 가상/섀도/실전 장부와 섞이지 않는다.
실전 주문 경로는 여기서 열지 않는다 (자동 매매의 사다리·안전장치를 통과한 경로만 실주문).

preview()  수량(주 또는 금액) → 기준가 · 예상 체결가(비용 모델) · 수수료/세금 · 주문 후 비중 · 사전 게이트(HALTED · 긴급 정지 ·
           데이터 건강 · 이벤트 · 리스크 엔진) · AI 판단 한 줄
place()    preview 와 같은 계산 후 confirm=True 일 때만 PaperBroker 로 체결 · 주문/체결 기록(OrderRecord/FillRecord) · 감사 로그
"""

from __future__ import annotations

from datetime import UTC, datetime

from . import ops, tenancy
from .asof import label

MODE = "manual"
US_MODE = "us-manual"  # v27: 미국 종목 수동 모의 장부 (달러 · 시작 $100,000 · 국내 장부와 따로)


def _is_us(symbol: str) -> bool:
    return bool(symbol) and not symbol[:1].isdigit()


def _us_bars(app, symbol: str):
    from .global_market import market_data as us_md
    bars, _, _ = us_md(app, extra=[symbol])
    if symbol not in bars:
        try:  # 유니버스 밖 종목은 처음 한 번 받아 둔다 (인터넷 필요)
            from .data.global_stocks import ensure_global
            ensure_global(app.engine, symbol)
            bars, _, _ = us_md(app, extra=[symbol])
        except Exception:  # noqa: BLE001, S110 - 아래에서 안내
            pass
    return bars


def _ctx(app, symbol: str, side: str, qty: int | None, amount: float | None, now: datetime):
    from .data.db import recent_adv, session_scope
    from .desk import cost_config, event_caps_for
    from .engines.sector import sector_map
    from .failmode import apply_caps
    from .trading.broker import MarketQuote
    from .trading.portfolio import CostModel, Order, Side
    from .trading.risk import RiskEngine
    if side not in ("buy", "sell"):
        raise ValueError("side 는 buy/sell")
    us = _is_us(symbol)
    mode = tenancy.book(US_MODE if us else MODE)  # v34: 회원마다 따로 (manual@1z)
    bars = _us_bars(app, symbol) if us else app.market_data()[0]
    b = bars.get(symbol)
    if b is None or b.empty:
        raise ValueError("미국 일봉이 없는 종목이에요 — 인터넷이 되는 곳에서 종목 화면을 한 번 열면 받아요" if us
                         else "국내 일봉이 있는 종목만 모의 주문할 수 있습니다")
    q = (ops.get_state(app.engine, "live_quotes") or {}).get(symbol) or {}
    price = float(q.get("price") or b["close"].iloc[-1])
    src = q.get("source") or q.get("src") or f"{label(b.index[-1], with_time=False)} 종가"
    pf = app.load_portfolio(mode)
    prices = {s: float(x["close"].iloc[-1]) for s, x in bars.items() if len(x)}
    prices[symbol] = price
    for s_, p in pf.positions.items():
        prices.setdefault(s_, p.avg_price)
    equity = pf.equity(prices)
    if qty is None:
        if not amount or amount <= 0:
            raise ValueError("수량 또는 금액을 입력하세요")
        qty = int(amount // price)
    qty = int(qty)
    if qty <= 0:
        raise ValueError("1주 미만 — 금액을 늘리세요")
    if qty > 10_000_000:
        raise ValueError("수량이 너무 큽니다")
    sd = Side.BUY if side == "buy" else Side.SELL
    order = Order(symbol=symbol, side=sd, qty=qty, reason="수동 모의 주문", ref_price=price)
    lim = app.settings.risk
    risk = RiskEngine(lim)
    risk.kill_switch = app.kill_switch_on()
    risk.start_day(now.date(), app._day_start_equity(mode, now, equity), app._orders_today(mode, now))
    with session_scope(app.engine) as s:
        risk.adv = recent_adv(s, {symbol} | set(pf.positions))
    risk.sectors = sector_map(app.engine)
    risk.event_caps = apply_caps(app, event_caps_for(app, {symbol}), {symbol}, now)
    steps = []
    halted = ops.halted(app.engine)
    steps.append({"gate": "HALTED", "status": "bad" if halted else "ok", "detail": "자동 감시가 모든 주문을 멈춤" if halted else "정상"})
    dh = ops.get_state(app.engine, "data_health")
    data_block = dh.get("trading") == "BLOCKED" and not us  # 데이터 건강 점수는 국내 시세 기준 — 미국 종목은 그 종목 일봉으로 따로 본다
    steps.append({"gate": "데이터 건강", "status": "bad" if data_block else "ok",
                  "detail": (dh.get("block_reason") or "거래 차단") if data_block else f"{dh.get('overall', '-')}% · 거래 가능" if dh else "점검 기록 없음"})
    if sd is Side.BUY and data_block:
        risk.buy_block = "데이터 건강 BLOCKED"  # fail-closed: 데이터를 믿을 수 없으면 매수하지 않는다 (매도는 허용)
    before = risk._orders_today
    dec = risk.check(order, pf, prices)
    risk._orders_today = before
    for r in dec.reasons:
        if "데이터 건강 BLOCKED" in r:
            continue  # 위 '데이터 건강' 단계에 이미 표시
        steps.append({"gate": "리스크 엔진", "status": "bad" if not dec.approved else "warn", "detail": r})
    if dec.approved and not dec.reasons:
        steps.append({"gate": "리스크 엔진", "status": "ok", "detail": "종목·총노출·현금·업종·유동성 한도 안"})
    if halted:
        dec = type(dec)(False, None, ["HALTED"])
    got = dec.order.qty if dec.approved and dec.order else 0
    if us:
        from .global_market import COSTS as US_COSTS
        costs = CostModel(US_COSTS)  # 해외 수수료 0.25% + 체결 차이 · 매도세 없음 (환전 비용은 따로)
    else:
        costs = CostModel(cost_config(app, app.settings.costs))
    sig = None
    if len(b) > 21:
        import numpy as np
        sig = float(np.log(b["close"]).diff().iloc[-20:].std())
    quote = MarketQuote(last=price, bid=price * 0.9995, ask=price * 1.0005, adv=risk.adv.get(symbol), sigma=sig)
    fill_px = costs.fill_price(sd, price, price * max(got, 1), quote.adv, quote.sigma) if got else None
    fee = costs.fee(sd, fill_px, got) if got else 0.0
    held = pf.qty(symbol)
    after_w = ((held + (got if sd is Side.BUY else -got)) * price / equity) if equity > 0 else None
    from .explain import for_symbol
    try:
        ex = for_symbol(app, symbol)
    except Exception:  # noqa: BLE001 - AI 판단이 없는 미국 종목
        ex = None
    fx = None
    if us:
        from .usorder import fx_rate
        fx = fx_rate(app)
    return {"pf": pf, "prices": prices, "quote": quote, "costs": costs, "decision": dec, "risk": risk, "order": order,
            "mode": mode,
            "view": {"symbol": symbol, "side": side, "mode": mode, "currency": "USD" if us else "KRW",
                     "fx": round(fx[0], 2) if fx else None, "fx_source": fx[1] if fx else None, "price": price, "price_source": src, "want_qty": qty, "allowed_qty": got,
                     "verdict": "차단" if not got else "축소" if got < qty else "통과", "est_fill": round(fill_px, 2) if fill_px else None,
                     "notional": round(got * (fill_px or price), 2 if us else 0), "fee_tax": round(fee, 2 if us else 0), "held": held, "cash": round(pf.cash, 2 if us else 0),
                     "notional_krw": round(got * (fill_px or price) * fx[0]) if fx else None,
                     "equity": round(equity), "weight_after": None if after_w is None else round(after_w, 4), "steps": steps,
                     "ai": {"action": ex["action"], "effective": ex.get("effective_action"), "headline": ex["headline"]} if ex else None,
                     "note": ("미국 수동 모의 장부(us-manual · 달러) — 실제 돈·실제 주문 아님. 원화는 참고 환율로 환산" if us
                              else "수동 모의 장부(manual) — 실제 돈·실제 주문 아님. 전략 장부와 따로 기록됩니다.")}}


def preview(app, symbol: str, side: str = "buy", qty: int | None = None, amount: float | None = None, now: datetime | None = None) -> dict:
    return _ctx(app, symbol, side, qty, amount, now or datetime.now(UTC))["view"]


def place(app, body: dict, now: datetime | None = None) -> dict:
    from .trading.broker import PaperBroker
    from .trading.journal import DBJournal
    from .trading.portfolio import Side
    now = now or datetime.now(UTC)
    sym = str(body.get("symbol", ""))[:12]
    qty = body.get("qty")
    amount = body.get("amount")
    try:
        qty = int(qty) if qty not in (None, "") else None
        amount = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("수량·금액은 숫자로") from None
    c = _ctx(app, sym, str(body.get("side", "buy")), qty, amount, now)
    v = c["view"]
    if not body.get("confirm"):
        return v | {"placed": False, "message": "확인(confirm) 없이 주문하지 않습니다"}
    dec = c["decision"]
    mode = c["mode"]
    j = DBJournal(mode, app.engine)
    if not dec.approved or dec.order is None:
        j.order(now, c["order"], "rejected", dec.reasons, None)
        return v | {"placed": False, "message": "차단: " + (" · ".join(dec.reasons) or "게이트")}
    approved = dec.order
    coid = j.begin(now, approved, f"{mode}:{now.strftime('%Y%m%dT%H%M%S%f')}:{sym}:{approved.side.value}")
    if coid is None:
        return v | {"placed": False, "message": "중복 주문으로 판단되어 건너뜀"}
    from dataclasses import replace
    approved = replace(approved, client_order_id=coid)
    pf = c["pf"]
    fill = PaperBroker(pf, c["costs"]).submit(approved, c["quote"], now)
    j.order(now, approved, "filled" if fill else "unfilled", dec.reasons, fill)
    from .data.db import session_scope
    from .data.models import PortfolioSnapshot
    with session_scope(app.engine) as s:
        s.add(PortfolioSnapshot(mode=mode, ts=now, **pf.snapshot(c["prices"])))
    from .governance import audit
    audit(app.engine, "manual_order", f"{sym} {'매수' if approved.side is Side.BUY else '매도'} {approved.qty}주 @ {fill.price if fill else '-'}")
    return v | {"placed": bool(fill), "fill": {"qty": fill.qty, "price": round(fill.price, 2), "fee": round(fill.fee)} if fill else None,
                "message": (f"모의 체결 {fill.qty}주 @ ${fill.price:,.2f}" if us_book(mode) else f"모의 체결 {fill.qty}주 @ {fill.price:,.0f}") if fill else "미체결"}


def us_book(mode: str) -> bool:
    return mode.startswith(US_MODE)


def book(app, mode: str = MODE) -> dict:
    """수동 모의 장부. mode=us-manual 이면 미국 장부 (달러 · 원화 환산 함께). v34: 회원이면 그 사람 장부."""
    mode = tenancy.book(US_MODE if us_book(mode) else MODE)
    us = us_book(mode)
    pf = app.load_portfolio(mode)
    if us:
        from .global_market import CASH_USD
        from .global_market import market_data as us_md
        bars, _, _ = us_md(app, extra=list(pf.positions))
        start = CASH_USD
    else:
        bars, _ = app._all_bars()
        start = app.settings.initial_cash
    prices = {s: float(bars[s]["close"].iloc[-1]) if s in bars and len(bars[s]) else p.avg_price for s, p in pf.positions.items()}
    eq = pf.equity(prices)
    out = {"mode": mode, "currency": "USD" if us else "KRW", "cash": round(pf.cash, 2 if us else 0), "equity": round(eq, 2 if us else 0),
           "return": round(eq / start - 1, 4),
           "positions": [{"symbol": s, "qty": p.qty, "avg_price": round(p.avg_price, 2), "price": prices[s],
                          "pnl_pct": round(prices[s] / p.avg_price - 1, 4) if p.avg_price else None}
                         for s, p in pf.positions.items() if p.qty]}
    if us:
        from .usorder import fx_rate
        fx, src = fx_rate(app)
        out |= {"fx": round(fx, 2), "fx_source": src, "equity_krw": round(eq * fx), "cash_krw": round(pf.cash * fx)}
    return out


__all__ = ["preview", "place", "book", "MODE", "US_MODE"]

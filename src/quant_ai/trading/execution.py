"""목표 비중 → 주문 → 리스크 → 브로커 → 저널.

백테스트, Paper, Shadow, Live 가 모두 이 엔진을 공유한다. 그래서 백테스트에서 검증한 로직이
실매매에서도 그대로 돈다 (모드별로 바뀌는 것은 Broker 뿐).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .broker import Broker, MarketQuote
from .journal import Journal
from .portfolio import Fill, Order, Side
from .risk import RiskEngine


@dataclass
class Signal:
    symbol: str
    target_weight: float
    prob_up: float | None = None
    reason: str = ""
    prediction_id: int | None = None
    consensus_id: int | None = None


class ExecutionEngine:
    def __init__(self, broker: Broker, risk: RiskEngine, journal: Journal, min_trade_weight: float = 0.01):
        self.broker = broker
        self.risk = risk
        self.journal = journal
        self.min_trade_weight = min_trade_weight  # 이보다 작은 비중 변화는 거래하지 않음 (회전율 억제)
        self.errors: list[str] = []
        self.unknown: list[str] = []  # v23: 통신 끊김으로 접수 여부를 모르는 주문 → 신규 매수 중단

    def rebalance(self, signals: list[Signal], quotes: dict[str, MarketQuote], ts: datetime,
                  exposure_multiplier: float = 1.0, tradable: set[str] | None = None) -> list[Fill]:
        """quotes 는 평가용으로 보유 종목 전부를 포함해야 한다. tradable 에 없는 종목은 주문하지 않는다."""
        pf = self.broker.portfolio
        tradable = set(quotes) if tradable is None else tradable
        prices = {s: q.last for s, q in quotes.items()}
        equity = pf.equity(prices)
        targets = {sig.symbol: sig for sig in signals}
        # 목표에 없는 보유 종목은 비중 0 으로
        for sym, pos in pf.positions.items():
            if pos.qty and sym not in targets and sym in quotes:
                targets[sym] = Signal(sym, 0.0, reason="목표 제외 → 청산")

        orders: list[Order] = []
        for sym, sig in targets.items():
            if sym not in quotes or sym not in tradable:
                continue
            price = prices[sym]
            cur_w = pf.qty(sym) * price / equity if equity > 0 else 0.0
            if abs(sig.target_weight - cur_w) < self.min_trade_weight and sig.target_weight > 0:
                continue
            delta_qty = int((sig.target_weight * equity - pf.qty(sym) * price) / price)  # 0 방향 절사
            if sig.target_weight == 0:
                delta_qty = -pf.qty(sym)
            if delta_qty == 0:
                continue
            side = Side.BUY if delta_qty > 0 else Side.SELL
            orders.append(Order(sym, side, abs(delta_qty), reason=sig.reason, prob_up=sig.prob_up,
                                prediction_id=sig.prediction_id, consensus_id=sig.consensus_id, ref_price=price))

        # 매도 먼저 → 현금 확보 후 매수
        orders.sort(key=lambda o: 0 if o.side is Side.SELL else 1)
        fills: list[Fill] = []
        # 증권사가 주문번호를 주는 즉시 기록 (체결 대기 중 죽어도 재시작 시 복구)
        if hasattr(self.broker, "on_placed"):
            self.broker.on_placed = self.journal.placed
        for order in orders:
            decision = self.risk.check(order, pf, prices, exposure_multiplier)
            if not decision.approved or decision.order is None:
                self.journal.order(ts, order, "rejected", decision.reasons, None)
                continue
            approved = decision.order
            # 멱등 키: 장부·거래일·종목·방향·주문 후 목표 수량 → 재시작해 같은 계획을 다시 돌려도 중복 주문 없음
            target_after = pf.qty(approved.symbol) + approved.signed_qty
            base = f"{self.journal.mode}:{ts.date().isoformat()}:{approved.symbol}:{approved.side.value}:{target_after}"
            coid = self.journal.begin(ts, approved, base)
            if coid is None:
                continue
            approved = replace(approved, client_order_id=coid)
            try:
                fill = self.broker.submit(approved, quotes[order.symbol], ts)
            except Exception as exc:  # noqa: BLE001 - 한 종목 주문 실패가 나머지를 막으면 안 됨
                # v23: 통신이 끊기면 증권사가 주문을 받았는지 알 수 없다 → 'error'(확정 실패)가 아니라 'unknown'.
                # 호출한 쪽이 신규 매수를 멈추고(킬스위치) 사람이 증권사 앱에서 확인하게 한다 (재시작 복구와 같은 규칙).
                net = is_network_error(exc)
                self.journal.order(ts, approved, "unknown" if net else "error",
                                   [*decision.reasons, f"{'통신 끊김 — 주문 접수 여부 불명' if net else '브로커 오류'}: {exc}"], None)
                self.errors.append(f"{order.symbol}: {exc}")
                if net:
                    self.unknown.append(f"{order.symbol} {approved.side.value} {approved.qty:.0f}주")
                continue
            status = "unfilled" if not fill else "filled" if fill.qty >= approved.qty else "partial"
            self.journal.order(ts, approved, status, decision.reasons, fill)
            if fill:
                fills.append(fill)
        return fills


def is_network_error(exc: Exception) -> bool:
    """통신 오류(응답을 못 받음) vs 증권사의 명확한 거절. 앞의 것은 주문이 들어갔을 수도 있다."""
    import http.client
    import socket
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    return isinstance(exc, (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError, socket.timeout)) \
        or (isinstance(exc, OSError) and not isinstance(exc, (FileNotFoundError, PermissionError)))


def signals_from_probs(probs: dict[str, float], min_prob: float, max_weight: float,
                       top_k: int | None = None) -> list[Signal]:
    """확률 → 목표 비중. min_prob 이상 종목을 확률 순으로 골라 동일 비중 (종목당 max_weight 상한)."""
    picks = sorted(((p, s) for s, p in probs.items() if p >= min_prob), reverse=True)
    if top_k:
        picks = picks[:top_k]
    if not picks:
        return []
    w = min(max_weight, 1.0 / len(picks))
    return [Signal(s, w, p, reason=f"P(up)={p:.2f}") for p, s in picks]

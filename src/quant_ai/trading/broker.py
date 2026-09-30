"""Broker Execution.

- ``PaperBroker``: 가상매매. 참조가격 + 슬리피지로 즉시 체결.
- ``ShadowBroker``: 실제 주문 파이프라인을 그대로 타되, 마지막 전송만 하지 않고
  '실제로 냈다면' 받았을 호가(매수=매도1호가, 매도=매수1호가)로 가상 체결해 기록.
- ``LiveBroker``: 증권사 API 연결부. 여러 안전장치를 통과해야만 생성 가능.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime

from ..config import Settings
from .portfolio import CostModel, Fill, Order, Portfolio, Side


@dataclass
class MarketQuote:
    last: float
    bid: float | None = None
    ask: float | None = None
    bid_qty: float | None = None
    ask_qty: float | None = None
    adv: float | None = None  # 20일 평균 거래대금 (시장 충격 계산용)
    sigma: float | None = None  # 20일 일간 변동성


class Broker(ABC):
    mode = "base"

    def __init__(self, portfolio: Portfolio):
        self.portfolio = portfolio

    @abstractmethod
    def submit(self, order: Order, quote: MarketQuote, ts: datetime) -> Fill | None: ...


class PaperBroker(Broker):
    mode = "paper"

    def __init__(self, portfolio: Portfolio, costs: CostModel | None = None):
        super().__init__(portfolio)
        self.costs = costs or CostModel()

    def _fill(self, order: Order, ref: float, ts: datetime, quote: MarketQuote | None = None) -> Fill | None:
        price = self.costs.fill_price(order.side, ref, ref * order.qty, quote.adv if quote else None,
                                      quote.sigma if quote else None)
        if order.order_type == "limit" and order.limit_price is not None:
            crosses = price <= order.limit_price if order.side is Side.BUY else price >= order.limit_price
            if not crosses:
                return None
        fill = Fill(order, ts, order.qty, price, self.costs.fee(order.side, price, order.qty))
        self.portfolio.apply(fill)
        return fill

    def submit(self, order: Order, quote: MarketQuote, ts: datetime) -> Fill | None:
        return self._fill(order, quote.last, ts, quote)


class ShadowBroker(PaperBroker):
    mode = "shadow"

    def __init__(self, portfolio: Portfolio, costs: CostModel | None = None):
        super().__init__(portfolio, costs)
        self.would_have_sent: list[tuple[datetime, Order, MarketQuote]] = []

    def submit(self, order: Order, quote: MarketQuote, ts: datetime) -> Fill | None:
        self.would_have_sent.append((ts, order, quote))
        ref = quote.ask if order.side is Side.BUY else quote.bid
        if ref is None:
            ref = quote.last
        # 호가 잔량이 부족하면 실제로는 전량 체결되지 않았을 것 → 가능한 수량만 체결
        avail = quote.ask_qty if order.side is Side.BUY else quote.bid_qty
        if avail is not None and avail < order.qty:
            if int(avail) <= 0:
                return None
            order = replace(order, qty=int(avail), reason=order.reason + " (호가잔량 부족: 부분체결)")
        return self._fill(order, ref, ts, quote)


class LiveBroker(Broker):
    """실계좌 주문. 구현체는 증권사 API(예: 한국투자증권 KIS Open API)로 작성한다.

    생성 시 ``Settings.assert_live_allowed`` 를 강제하며, 총 투입 자본을 live_max_capital 로 제한한다.
    """

    mode = "live"

    def __init__(self, portfolio: Portfolio, settings: Settings, champion_ready: bool):
        settings.assert_live_allowed(champion_ready)
        if portfolio.cash > settings.live_max_capital:
            raise PermissionError(
                f"Live 자본 {portfolio.cash:,.0f} > 상한 {settings.live_max_capital:,.0f}. 소액부터 시작하세요."
            )
        super().__init__(portfolio)
        self.settings = settings

    def submit(self, order: Order, quote: MarketQuote, ts: datetime) -> Fill | None:  # pragma: no cover
        raise NotImplementedError(
            "증권사 주문 API 연동 필요: 토큰 발급 → 주문 전송 → 체결 조회 → Fill 반환. "
            "Shadow 모드로 충분히 검증한 뒤 구현/활성화하세요."
        )

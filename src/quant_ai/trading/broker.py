"""Broker Execution.

- ``PaperBroker``: 가상매매. 참조가격 + 슬리피지로 즉시 체결.
- ``ShadowBroker``: 실제 주문 파이프라인을 그대로 타되, 마지막 전송만 하지 않고
  '실제로 냈다면' 받았을 호가(매수=매도1호가, 매도=매수1호가)로 가상 체결해 기록.
- ``LiveBroker``: 증권사가 설정되지 않은 LIVE 요청을 안전장치 검사 뒤 거절 (실제 주문은 trading/kis.py 의 KISBroker).
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

    def __init__(self, portfolio: Portfolio, costs: CostModel | None = None, book_fn=None):
        super().__init__(portfolio, costs)
        self.would_have_sent: list[tuple[datetime, Order, MarketQuote]] = []
        self.book_fn = book_fn  # (종목) -> 10단계 호가 dict | None · 기본: KIS 웹소켓 최신 호가
        self.last_sim: dict | None = None
        self.sim_bias_bps = 0.0  # 실측 parity(100건+)로 잰 시뮬레이터 편향 — 예측 체결가에 더한다

    def _book(self, symbol: str) -> dict | None:
        if self.book_fn is not None:
            return self.book_fn(symbol)
        from .kis_ws import latest_book
        return latest_book(symbol)

    def submit(self, order: Order, quote: MarketQuote, ts: datetime) -> Fill | None:
        self.would_have_sent.append((ts, order, quote))
        book = self._book(order.symbol)
        if book is not None:  # 10단계 호가가 있으면 실제 잔량을 먹어 들어가는 체결가 (체결 시뮬레이터)
            from .exec_sim import simulate
            sim = simulate(order.side.value, order.qty, quote.last, book=book, sigma=quote.sigma,
                           limit=order.limit_price if order.order_type == "limit" else None)
            if sim.qty <= 0 or sim.avg_price is None:
                return None
            if sim.qty < order.qty:
                order = replace(order, qty=sim.qty, reason=order.reason + f" (호가 {sim.levels}단계까지: 부분체결)")
            px = sim.avg_price * (1 + (1 if order.side is Side.BUY else -1) * self.sim_bias_bps / 1e4)
            fill = Fill(order, ts, sim.qty, px, self.costs.fee(order.side, px, sim.qty))
            self.portfolio.apply(fill)
            self.last_sim = sim.as_dict()
            return fill
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


class BrokerNotConfigured(RuntimeError):
    """실전(LIVE) 모드인데 실제 증권사 연결(QUANT_BROKER=kis)이 설정되지 않음 — 주문을 내지 않는다 (fail-closed)."""


class LiveBroker(Broker):
    """실전 모드의 '안전장치 겸 자리 표시자' — 증권사가 설정되지 않았을 때만 쓰인다.

    책임 분리 (v23 정리):
      Broker (이 파일)          주문 인터페이스: submit(order, quote, ts) → Fill | None
      PaperBroker/ShadowBroker  가상 체결 (모의·그림자 장부)
      KISBroker (trading/kis.py) 실제 증권사 주문: 토큰 → 주문 → 체결 조회 → 잔량 취소 → 잔고 동기화 (실계좌·KIS 모의 모두)
      LiveBroker (이 클래스)    QUANT_BROKER 가 kis 가 아닐 때 LIVE 를 요청하면 생성된다 — 안전장치를 검사한 뒤
                                어떤 주문도 내지 않고 BrokerNotConfigured 로 멈춘다.
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

    def submit(self, order: Order, quote: MarketQuote, ts: datetime) -> Fill | None:
        raise BrokerNotConfigured(
            "실전 주문을 보낼 증권사가 설정되지 않았습니다 — .env 에 QUANT_BROKER=kis 와 KIS 키를 넣으세요 "
            "(실제 주문은 trading/kis.py 의 KISBroker 가 처리합니다). 이 주문은 보내지 않았습니다."
        )

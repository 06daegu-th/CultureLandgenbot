"""주문 / 체결 / 포트폴리오 기본 타입."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import CostModelConfig


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Order:
    symbol: str
    side: Side
    qty: int
    order_type: str = "market"
    limit_price: float | None = None
    reason: str = ""
    prob_up: float | None = None  # 이 주문을 만든 예측 확률 (리스크/복기용)
    prediction_id: int | None = None

    @property
    def signed_qty(self) -> int:
        return self.qty if self.side is Side.BUY else -self.qty


@dataclass
class Fill:
    order: Order
    ts: datetime
    qty: int
    price: float
    fee: float


@dataclass
class Position:
    qty: int = 0
    avg_price: float = 0.0


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0

    def qty(self, symbol: str) -> int:
        p = self.positions.get(symbol)
        return p.qty if p else 0

    def market_value(self, prices: dict[str, float]) -> float:
        return sum(p.qty * prices[s] for s, p in self.positions.items() if p.qty)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def weights(self, prices: dict[str, float]) -> dict[str, float]:
        eq = self.equity(prices)
        return {s: p.qty * prices[s] / eq for s, p in self.positions.items() if p.qty and eq > 0}

    def apply(self, fill: Fill) -> None:
        signed = fill.qty if fill.order.side is Side.BUY else -fill.qty
        pos = self.positions.setdefault(fill.order.symbol, Position())
        if signed > 0:
            new_qty = pos.qty + signed
            pos.avg_price = (pos.avg_price * pos.qty + fill.price * signed) / new_qty
            pos.qty = new_qty
            self.cash -= fill.price * signed + fill.fee
        else:
            sell = -signed
            if sell > pos.qty:
                raise ValueError(f"{fill.order.symbol}: 보유 {pos.qty} < 매도 {sell} (공매도 미지원)")
            self.realized_pnl += (fill.price - pos.avg_price) * sell - fill.fee
            pos.qty -= sell
            self.cash += fill.price * sell - fill.fee
            if pos.qty == 0:
                pos.avg_price = 0.0
        self.fees_paid += fill.fee

    def snapshot(self, prices: dict[str, float]) -> dict:
        return {
            "cash": self.cash,
            "equity": self.equity(prices),
            "positions": {s: {"qty": p.qty, "avg_price": p.avg_price} for s, p in self.positions.items() if p.qty},
        }


@dataclass(frozen=True)
class CostModel:
    cfg: CostModelConfig = CostModelConfig()

    def fill_price(self, side: Side, ref_price: float) -> float:
        slip = self.cfg.slippage_bps / 1e4
        return ref_price * (1 + slip) if side is Side.BUY else ref_price * (1 - slip)

    def fee(self, side: Side, price: float, qty: int) -> float:
        notional = price * qty
        fee = notional * self.cfg.commission_bps / 1e4
        if side is Side.SELL:
            fee += notional * self.cfg.sell_tax_bps / 1e4
        return fee

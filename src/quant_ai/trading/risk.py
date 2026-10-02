"""Risk Engine: 모든 주문은 브로커로 가기 전에 여기를 통과해야 한다.

매도(위험 축소)는 가능한 한 허용하고, 매수(위험 확대)는 엄격히 제한한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

from ..config import RiskLimits
from .portfolio import Order, Portfolio, Side


@dataclass
class RiskDecision:
    approved: bool
    order: Order | None
    reasons: list[str] = field(default_factory=list)


class RiskEngine:
    def __init__(self, limits: RiskLimits):
        self.limits = limits
        self.kill_switch = False
        self._day: date | None = None
        self._day_start_equity = 0.0
        self._orders_today = 0
        self.adv: dict[str, float] = {}  # 종목별 20일 평균 거래대금 (없으면 유동성 한도 생략)
        self.sectors: dict[str, str] = {}  # 종목 → 업종 (없으면 업종 한도 생략)
        # v13 — 매수만 막거나 줄인다 (매도는 항상 허용)
        self.buy_block: str | None = None  # Trading Readiness 가 NOT READY 면 사유
        self.event_caps: dict[str, tuple[float, str]] = {}  # 종목 → (수량 배수 0~1, 사유) · 실적 발표 직전 등
        self.portfolio_gate = None  # (종목, 수량, 가격, 포트폴리오, 가격들) → (최대 수량, 사유|None) · 업종/묶음/VaR

    def start_day(self, day: date, equity: float, orders_so_far: int = 0) -> None:
        """orders_so_far: 오늘 이미 낸 주문 수 (프로세스 재시작/여러 사이클에 걸쳐 한도 유지)."""
        self._day = day
        self._day_start_equity = equity
        self._orders_today = orders_so_far

    def daily_pnl_pct(self, equity: float) -> float:
        if self._day_start_equity <= 0:
            return 0.0
        return equity / self._day_start_equity - 1

    def check(self, order: Order, portfolio: Portfolio, prices: dict[str, float],
              exposure_multiplier: float = 1.0) -> RiskDecision:
        L = self.limits
        price = prices[order.symbol]
        equity = portfolio.equity(prices)
        reasons: list[str] = []

        if order.qty <= 0:
            return RiskDecision(False, None, ["수량 0"])

        if order.side is Side.SELL:
            held = portfolio.qty(order.symbol)
            if held <= 0:
                return RiskDecision(False, None, ["보유 없음 (공매도 금지)"])
            if order.qty > held:
                order = _with_qty(order, held)
                reasons.append(f"보유수량 {held} 로 축소")
            # 위험 축소 주문은 킬스위치/손실한도와 무관하게 허용
            self._orders_today += 1
            return RiskDecision(True, order, reasons)

        # ---- 이하 매수
        if self.kill_switch:
            return RiskDecision(False, None, ["킬스위치 ON"])
        if self.buy_block:
            return RiskDecision(False, None, [f"매매 준비 상태: {self.buy_block}"])
        if self._orders_today >= L.max_orders_per_day:
            return RiskDecision(False, None, [f"일 주문 한도 {L.max_orders_per_day} 초과"])
        if self.daily_pnl_pct(equity) <= -L.max_daily_loss_pct:
            return RiskDecision(False, None, [f"일 손실 한도 {L.max_daily_loss_pct:.1%} 도달 → 신규 매수 중단"])
        if order.prob_up is not None and order.prob_up < L.min_confidence:
            return RiskDecision(False, None, [f"확률 {order.prob_up:.2f} < 최소 {L.min_confidence:.2f}"])

        qty = order.qty
        cur_val = portfolio.qty(order.symbol) * price
        gross_cap = L.max_gross_exposure * exposure_multiplier
        caps = [
            (L.max_order_value, f"1회 주문 한도 {L.max_order_value:,.0f}"),
            (L.max_position_weight * equity - cur_val, f"종목 비중 한도 {L.max_position_weight:.0%}"),
            (gross_cap * equity - portfolio.market_value(prices),
             f"총노출 한도 {gross_cap:.0%} (국면배수 {exposure_multiplier})"),
            (portfolio.cash / 1.01, "가용 현금"),  # 수수료/세금 여유분
        ]
        sector = self.sectors.get(order.symbol)
        if sector and getattr(L, "max_sector_weight", 0) > 0:
            held_sector = sum(p.qty * prices.get(s, p.avg_price) for s, p in portfolio.positions.items()
                              if p.qty and self.sectors.get(s) == sector)
            caps.append((L.max_sector_weight * equity - held_sector, f"업종 한도 {sector} {L.max_sector_weight:.0%}"))
        adv = self.adv.get(order.symbol)
        if adv and L.max_adv_participation > 0:
            caps.append((adv * L.max_adv_participation, f"유동성 한도 (20일 평균 거래대금의 {L.max_adv_participation:.0%})"))
        for room, label in caps:
            max_qty = int(max(room, 0) // price)
            if qty > max_qty:
                qty = max_qty
                reasons.append(f"{label} → {max_qty}주로 축소")
        cap = self.event_caps.get(order.symbol)
        if cap and qty > 0:
            m, why = cap
            new = int(qty * max(0.0, min(1.0, m)))
            if new < qty:
                reasons.append(f"이벤트 위험: {why} → {new}주" if new else f"이벤트 위험: {why} → 매수 보류")
                qty = new
        if self.portfolio_gate is not None and qty > 0:
            max_qty, why = self.portfolio_gate(order.symbol, qty, price, portfolio, prices)
            if max_qty < qty:
                qty = max(int(max_qty), 0)
                reasons.append(f"포트폴리오 한도: {why} → {qty}주")

        if qty <= 0:
            return RiskDecision(False, None, reasons or ["한도 소진"])
        self._orders_today += 1
        return RiskDecision(True, _with_qty(order, qty) if qty != order.qty else order, reasons)


def _with_qty(order: Order, qty: int) -> Order:
    return replace(order, qty=int(qty))

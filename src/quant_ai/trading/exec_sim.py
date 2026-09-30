"""체결 시뮬레이터 — '실제로 냈다면 얼마에 몇 주가 체결됐을까'.

우선순위
1. 10단계 호가(KIS 웹소켓)가 있으면: 호가 잔량을 한 단계씩 먹어 들어간다 → 평균 체결가 · 체결 수량 · 몇 단계까지 먹었나
   지정가면 그 가격을 넘는 단계는 먹지 않는다 (나머지는 미체결). 표시 잔량 밖은 새로 채워지는 몫(refill)만 추가로 본다.
2. 1단계 호가(매수1/매도1)만 있으면: 1단계 잔량까지는 그 가격, 나머지는 제곱근 충격으로 미끄러진 가격
3. 호가가 없으면: 추정 스프레드(호가 단위 · 거래대금 기준) 절반 + 제곱근 시장 충격
모든 결과에 비용 분해를 붙인다: 스프레드 절반 · 호가 깊이 · 시장 충격 · 지연(latency) — 실측 슬리피지와 비교하는 기준.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .kis import krx_tick


@dataclass
class SimFill:
    qty: int
    avg_price: float | None
    requested: int
    mid: float
    levels: int
    method: str
    slippage_bps: float | None  # 중간가 대비 불리한 방향 +
    parts: dict

    def as_dict(self) -> dict:
        return {"qty": self.qty, "avg_price": None if self.avg_price is None else round(self.avg_price, 4),
                "requested": self.requested, "mid": round(self.mid, 4), "levels": self.levels, "method": self.method,
                "slippage_bps": None if self.slippage_bps is None else round(self.slippage_bps, 2),
                "fill_ratio": round(self.qty / self.requested, 4) if self.requested else 0.0, "parts": self.parts}


def est_spread_bps(price: float, adv: float | None, kr: bool = True) -> float:
    """호가가 없을 때 스프레드 추정: 최소 1호가 단위 + 거래대금이 작을수록 넓어짐."""
    tick_bps = (krx_tick(price) / price * 1e4) if kr and price > 0 else 1.0
    liq = 8.0 if adv is None else 25.0 / math.sqrt(max(adv, 1e6) / 1e9)  # 일 10억 → 25bp, 1000억 → 2.5bp
    return max(tick_bps, min(liq, 80.0))


def walk_book(side: str, qty: int, book: dict, limit: float | None = None, refill: float = 0.0) -> tuple[int, float, int]:
    """호가 먹기. side='buy' 면 매도호가(asks)를 오름차순으로. → (체결수량, 평균가, 사용 단계 수)."""
    levels = sorted(book["asks"]) if side == "buy" else sorted(book["bids"], reverse=True)
    left, cost, used = qty, 0.0, 0
    for p, q in levels:
        if limit is not None and ((side == "buy" and p > limit) or (side == "sell" and p < limit)):
            break
        take = min(left, int(q * (1 + refill)))
        if take <= 0:
            continue
        cost += take * p
        left -= take
        used += 1
        if left <= 0:
            break
    filled = qty - left
    return filled, (cost / filled if filled else float("nan")), used


def simulate(side: str, qty: int, last: float, bid: float | None = None, ask: float | None = None,
             bid_qty: float | None = None, ask_qty: float | None = None, book: dict | None = None,
             adv: float | None = None, sigma: float | None = None, limit: float | None = None, kr: bool = True,
             impact_coef: float = 0.7, latency_s: float = 0.5, refill: float = 0.0) -> SimFill:
    buy = side == "buy"
    sgn = 1 if buy else -1
    if book and book.get("asks") and book.get("bids"):
        a1, b1 = min(p for p, _ in book["asks"]), max(p for p, _ in book["bids"])
        mid = (a1 + b1) / 2
        filled, avg, used = walk_book(side, qty, book, limit, refill)
        # 지연 동안의 불리한 움직임 기대값 (σ_일 × √(지연/하루 거래시간) × 0.4)
        lat = (sigma or 0.02) * math.sqrt(latency_s / 23400) * 0.4 * 1e4
        if filled:
            avg = avg * (1 + sgn * lat / 1e4)
        slip = None if not filled else sgn * (avg - mid) / mid * 1e4
        half = (a1 - b1) / 2 / mid * 1e4
        return SimFill(filled, avg if filled else None, qty, mid, used, "book10", slip,
                       {"half_spread": round(half, 2), "depth": round((slip or 0) - half - lat, 2), "latency": round(lat, 2),
                        "impact": 0.0})
    touch = (ask if buy else bid)
    if touch and (bid and ask):
        mid = (bid + ask) / 2
        half = abs(touch - mid) / mid * 1e4
        method = "book1"
    else:
        mid = last
        half = est_spread_bps(last, adv, kr) / 2
        touch = last * (1 + sgn * half / 1e4)
        method = "model"
    shown = ask_qty if buy else bid_qty
    first = qty if shown is None or method == "model" else min(qty, int(shown))
    rest = qty - first
    impact = 0.0
    if adv and adv > 0 and sigma:
        impact = min(150.0, impact_coef * sigma * math.sqrt(qty * last / adv) * 1e4)
    elif rest > 0:
        impact = 5.0 * rest / max(qty, 1)  # 잔량 밖을 먹는데 거래대금을 모르면 보수적 가산
    px_rest = touch * (1 + sgn * impact / 1e4)
    avg = (first * touch + rest * px_rest) / qty if qty else touch
    if limit is not None and ((buy and avg > limit) or (not buy and avg < limit)):
        ok = first if (buy and touch <= limit) or (not buy and touch >= limit) else 0
        if not ok:
            return SimFill(0, None, qty, mid, 0, method, None, {"half_spread": round(half, 2), "impact": 0, "depth": 0, "latency": 0})
        avg, qty_f = touch, ok
    else:
        qty_f = qty
    slip = sgn * (avg - mid) / mid * 1e4
    return SimFill(qty_f, avg, qty, mid, 1, method, slip,
                   {"half_spread": round(half, 2), "impact": round(slip - half, 2), "depth": 0.0, "latency": 0.0})


__all__ = ["simulate", "walk_book", "est_spread_bps", "SimFill"]

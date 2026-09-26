"""리밸런싱 주문표 — 다른 증권사·ISA·수동 매매 사용자를 위한 "이번 달 무엇을 몇 주 사고팔지".

자동매매(KIS API)를 쓰지 않아도 전략을 그대로 따라 할 수 있게, 현재 보유/현금 → 목표 비중 차이를
정수 주식 수로 바꾸고 참고 지정가·예상 비용까지 계산한다. 판단은 모두 이 모듈 밖(코어 팩터·AI 오버레이)에서
끝나고, 여기서는 산수만 한다.

지정가 가이드: 매수는 기준가 +1% 이내(호가단위 올림), 매도는 기준가 −1% 이내(호가단위 내림).
    → 장 시작 직후 급변을 피해 09:10 이후, 이 가격을 넘으면 그날은 주문하지 말고 다음 날 다시.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import asdict, dataclass, field

from ..config import CostModelConfig
from ..trading.kis import round_to_tick
from ..trading.portfolio import Side


@dataclass
class OrderLine:
    symbol: str
    name: str
    side: str  # BUY / SELL / HOLD
    qty: int  # 주문 수량 (HOLD 는 0)
    current_qty: int
    target_qty: int
    ref_price: float
    limit_price: int | None
    value: float  # 주문 금액 (수량 × 기준가)
    est_cost: float  # 수수료 + 매도세
    current_weight: float
    target_weight: float
    role: str  # core / satellite / exit / not_in_plan
    reason: str = ""


@dataclass
class OrderSheet:
    as_of: str
    equity: float
    cash: float
    cash_after: float
    lines: list[OrderLine]
    notes: list[str] = field(default_factory=list)
    trend: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["순서", "구분", "종목코드", "종목명", "수량", "지정가(참고)", "기준가", "금액", "예상비용",
                    "현재수량", "목표수량", "현재비중", "목표비중", "역할", "사유"])
        side_ko = {"SELL": "매도", "BUY": "매수", "HOLD": "유지"}
        for i, ln in enumerate(self.lines, 1):
            w.writerow([i, side_ko[ln.side], ln.symbol, ln.name, ln.qty, ln.limit_price or "", round(ln.ref_price),
                        round(ln.value), round(ln.est_cost), ln.current_qty, ln.target_qty,
                        f"{ln.current_weight:.1%}", f"{ln.target_weight:.1%}", ln.role, ln.reason])
        return buf.getvalue()

    def to_text(self) -> str:
        side_ko = {"SELL": "매도", "BUY": "매수", "HOLD": "유지"}
        out = [f"리밸런싱 주문표 (기준일 {self.as_of}) · 평가금액 {self.equity:,.0f}원 · 현금 {self.cash:,.0f}원",
               f"추세: {'지수 < 200일선 → 코어 비중 축소' if self.trend.get('below') else '지수 ≥ 200일선 (정상 비중)'}",
               "", f"{'구분':<4} {'코드':<7} {'종목명':<14} {'수량':>6} {'지정가':>10} {'금액':>13}  사유"]
        for ln in self.lines:
            if ln.side == "HOLD":
                continue
            out.append(f"{side_ko[ln.side]:<4} {ln.symbol:<7} {ln.name[:12]:<14} {ln.qty:>6,} "
                       f"{(ln.limit_price or 0):>10,} {ln.value:>13,.0f}  {ln.reason}")
        holds = [ln for ln in self.lines if ln.side == "HOLD"]
        if holds:
            out.append(f"유지 {len(holds)}종목: " + ", ".join(f"{ln.name}({ln.current_qty})" for ln in holds))
        t = self.totals
        out += ["", f"매도 {t.get('sell_value', 0):,.0f}원 · 매수 {t.get('buy_value', 0):,.0f}원 · "
                    f"예상 비용 {t.get('cost', 0):,.0f}원 · 주문 후 현금 {self.cash_after:,.0f}원",
                "순서: 매도 먼저 → 체결 확인 → 매수. 지정가를 넘는 급등·급락이면 그날은 건너뛰기."]
        out += [f"· {n}" for n in self.notes]
        return "\n".join(out)


def make_order_sheet(weights: dict[str, float], holdings: dict[str, int], cash: float, prices: dict[str, float],
                     names: dict[str, str] | None = None, roles: dict[str, str] | None = None,
                     reasons: dict[str, str] | None = None, costs: CostModelConfig | None = None,
                     min_trade_frac: float = 0.005, limit_band: float = 0.01, as_of: str = "",
                     trend: dict | None = None, notes: list[str] | None = None) -> OrderSheet:
    """weights: 목표 비중 {종목: 0~1}, holdings: 현재 보유 {종목: 수량}, prices: 기준가 {종목: 원}.

    - 목표에 없는 보유 종목은 전량 매도 (가격이 없으면 판단 불가 → 유지하고 경고)
    - 비중 차이가 평가금액의 min_trade_frac 미만인 미세 조정은 생략 (불필요한 수수료·세금 방지)
    - 매수는 비용을 감안해 정수 주로 내림, 매도 대금 + 현금 안에서만 매수 (부족하면 목표 대비 비율로 축소)
    """
    costs = costs or CostModelConfig()
    names, roles, reasons = names or {}, roles or {}, reasons or {}
    notes = list(notes or [])
    fee = costs.commission_bps / 1e4
    tax = costs.sell_tax_bps / 1e4
    holdings = {s: int(q) for s, q in holdings.items() if int(q) > 0}
    unknown = [s for s in holdings if s not in prices]
    for s in unknown:
        notes.append(f"{s}: 가격 데이터 없음 → 판단 불가, 그대로 유지 (종목코드 확인)")
    equity = cash + sum(q * prices[s] for s, q in holdings.items() if s in prices)
    if equity <= 0:
        raise ValueError("평가금액이 0 이하입니다 (현금·보유 수량 확인)")

    lines: list[OrderLine] = []
    for s in sorted(set(weights) | set(holdings)):
        if s in unknown:
            continue
        p = float(prices[s])
        cur = holdings.get(s, 0)
        w = float(weights.get(s, 0.0))
        tgt = int(math.floor(w * equity / (p * (1 + fee)))) if w > 0 else 0
        role = roles.get(s, "not_in_plan" if w == 0 else "core")
        reason = reasons.get(s, "")
        if w > 0 and tgt == 0:
            reason = (reason + " · " if reason else "") + "1주 가격이 목표 금액보다 큼 → 매수 불가"
        delta = tgt - cur
        # 미세 조정 생략 (신규 편입·전량 매도는 항상 실행)
        if delta and cur and tgt and abs(delta) * p < min_trade_frac * equity:
            reason = (reason + " · " if reason else "") + "차이 작아 생략"
            delta = 0
        side = "BUY" if delta > 0 else "SELL" if delta < 0 else "HOLD"
        if side == "SELL" and tgt == 0 and not reason:
            reason = "목표에서 제외 → 전량 매도"
        elif side in ("SELL", "BUY") and cur and tgt:
            reason = f"비중 {'축소' if side == 'SELL' else '확대'} {cur * p / equity:.1%} → {w:.1%}" \
                + (f" · {reason}" if reason else "")
        qty = abs(delta)
        value = qty * p
        limit = None
        if side == "BUY":
            limit = round_to_tick(p * (1 + limit_band), Side.BUY)
        elif side == "SELL":
            limit = round_to_tick(p * (1 - limit_band), Side.SELL)
        est = value * (fee + (tax if side == "SELL" else 0.0))
        lines.append(OrderLine(s, names.get(s, s), side, qty, cur, tgt if side != "HOLD" else cur, p, limit, value, est,
                               cur * p / equity, w, role, reason))

    # 현금 제약: 매도 대금 + 현금 안에서 매수. 부족하면 매수 수량을 같은 비율로 축소 (지정가 상한 기준으로 보수적)
    sells = [ln for ln in lines if ln.side == "SELL"]
    buys = [ln for ln in lines if ln.side == "BUY"]
    avail = cash + sum(ln.value - ln.est_cost for ln in sells)
    need = sum(ln.qty * ln.limit_price * (1 + fee) for ln in buys)
    if buys and need > avail:
        scale = max(avail, 0) / need
        for ln in buys:
            ln.qty = int(math.floor(ln.qty * scale))
            ln.target_qty = ln.current_qty + ln.qty
            ln.value = ln.qty * ln.ref_price
            ln.est_cost = ln.value * fee
        notes.append(f"현금 부족 → 매수 수량을 {scale:.0%} 로 축소 (지정가 상한 기준 보수 계산)")
        for ln in buys:
            if ln.qty == 0:
                ln.side, ln.reason = "HOLD", (ln.reason + " · " if ln.reason else "") + "현금 부족으로 이번엔 매수 못함"
    order = {"SELL": 0, "BUY": 1, "HOLD": 2}
    lines.sort(key=lambda ln: (order[ln.side], -ln.value, ln.symbol))
    sell_v = sum(ln.value for ln in lines if ln.side == "SELL")
    buy_v = sum(ln.value for ln in lines if ln.side == "BUY")
    cost = sum(ln.est_cost for ln in lines)
    cash_after = cash + sell_v - buy_v - cost
    totals = {"sell_value": sell_v, "buy_value": buy_v, "cost": cost,
              "n_sell": sum(ln.side == "SELL" for ln in lines), "n_buy": sum(ln.side == "BUY" for ln in lines),
              "turnover": (sell_v + buy_v) / equity,
              "invested_after": 1 - cash_after / equity}
    return OrderSheet(as_of, equity, cash, cash_after, lines, notes, trend or {}, totals)


_HOLDING_RE = re.compile(r'^"?([A-Za-z0-9]+)(?:\.[A-Za-z]+)?"?\s*[,;\t ]\s*"?([0-9]{1,3}(?:,[0-9]{3})+(?![0-9])|[0-9]+)')


def parse_holdings(text: str) -> dict[str, int]:
    """CSV/텍스트 → {종목코드: 수량}. 형식: '005930,10' · '005930 10' · '005930\t"1,000"\t70000'
    (헤더·주석(#)·천단위 쉼표·뒤쪽 추가 열 허용). 6자리 미만 숫자 코드는 앞에 0을 채운다 (엑셀이 0을 지우는 경우)."""
    out: dict[str, int] = {}
    for raw in text.splitlines():
        m = _HOLDING_RE.match(raw.split("#")[0].strip())
        if not m:
            continue  # 빈 줄·헤더
        code, n = m.group(1), int(m.group(2).replace(",", ""))
        if code.isdigit() and len(code) < 6:
            code = code.zfill(6)
        if n > 0:
            out[code] = out.get(code, 0) + n
    return out


__all__ = ["OrderLine", "OrderSheet", "make_order_sheet", "parse_holdings"]

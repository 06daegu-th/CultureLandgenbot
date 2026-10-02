"""Trade Journal: 신호, 주문, 리스크 차단, 체결을 전부 기록한다 (복기의 원천 데이터)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from ..data.db import session_scope
from ..data.models import FillRecord, JournalEntry, OrderRecord
from .portfolio import Fill, Order


@dataclass
class Entry:
    ts: datetime
    mode: str
    kind: str
    symbol: str | None
    message: str
    data: dict = field(default_factory=dict)


FINAL = ("filled", "partial", "unfilled", "cancelled", "rejected", "error")
OPEN = ("pending", "submitted")


class Journal:
    """메모리 저널 (백테스트용). ``DBJournal`` 은 DB 에도 저장.

    멱등성: 주문마다 client_order_id 를 붙인다. 같은 기본 키(장부·날짜·종목·방향·목표수량)로
    - 아직 끝나지 않은 주문(pending/submitted)이 있으면 → 새로 내지 않는다 (복구가 먼저)
    - 이미 체결된 주문이 있으면 → 새로 내지 않는다 (재시작 후 같은 계획을 다시 실행해도 중복 매수 없음)
    - 미체결·취소·오류로 끝났으면 → 시도 번호를 올려 다시 낼 수 있다
    """

    def __init__(self, mode: str):
        self.mode = mode
        self.entries: list[Entry] = []
        self._orders: dict[str, str] = {}  # client_order_id → status

    def note(self, ts: datetime, kind: str, message: str, symbol: str | None = None, **data) -> None:
        self.entries.append(Entry(ts, self.mode, kind, symbol, message, data))

    # ---------------------------------------------------------------- 멱등 주문 수명주기
    def _attempts(self, base: str) -> dict[str, str]:
        return {k: v for k, v in self._orders.items() if k.rsplit("#", 1)[0] == base}

    def begin(self, ts: datetime, order: Order, base: str) -> str | None:
        """제출 직전에 호출. 새 client_order_id 를 돌려주거나, 중복이면 None."""
        prev = self._attempts(base)
        if any(st in OPEN or st in ("filled", "partial") for st in prev.values()):
            self.note(ts, "dedupe", f"중복 주문 방지: {order.symbol} {order.side.value} ({base})", order.symbol,
                      base=base, previous=prev)
            return None
        coid = f"{base}#{len(prev) + 1}"
        self._orders[coid] = "pending"
        return coid

    def placed(self, coid: str, broker_order_id: str, orgno: str | None = None) -> None:
        """증권사가 주문번호를 준 즉시 호출 (체결 대기 중 프로세스가 죽어도 복구할 수 있게)."""
        self._orders[coid] = "submitted"

    def order(self, ts: datetime, order: Order, status: str, reasons: list[str], fill: Fill | None) -> None:
        if order.client_order_id:
            self._orders[order.client_order_id] = status
        self.note(ts, "order", f"{order.side.value} {order.qty} {order.symbol} → {status}", order.symbol,
                  side=order.side.value, qty=order.qty, status=status, reasons=reasons, prob_up=order.prob_up,
                  reason=order.reason, client_order_id=order.client_order_id,
                  fill_price=fill.price if fill else None, fee=fill.fee if fill else None)

    def of_kind(self, kind: str) -> list[Entry]:
        return [e for e in self.entries if e.kind == kind]


class DBJournal(Journal):
    def __init__(self, mode: str, engine: Engine):
        super().__init__(mode)
        self.engine = engine

    def note(self, ts, kind, message, symbol=None, **data) -> None:
        super().note(ts, kind, message, symbol, **data)
        with session_scope(self.engine) as s:
            s.add(JournalEntry(ts=ts, mode=self.mode, kind=kind, symbol=symbol, message=message,
                               data=_jsonable(data)))

    def begin(self, ts, order, base) -> str | None:
        with session_scope(self.engine) as s:
            prev = {r.client_order_id: r.status for r in s.scalars(
                select(OrderRecord).where(OrderRecord.mode == self.mode,
                                          OrderRecord.client_order_id.like(base.replace("%", "") + "#%")))}
        self._orders.update(prev)
        coid = super().begin(ts, order, base)
        if coid is None:
            return None
        try:
            with session_scope(self.engine) as s:  # 제출 전에 먼저 기록 → 여기서 죽어도 흔적이 남는다
                s.add(OrderRecord(mode=self.mode, created_at=ts, symbol=order.symbol, side=order.side.value,
                                  qty=order.qty, order_type=order.order_type, limit_price=order.limit_price,
                                  status="pending", reason=order.reason, prediction_id=order.prediction_id,
                                  client_order_id=coid, consensus_id=order.consensus_id, ref_price=order.ref_price,
                                  updated_at=datetime.now(UTC)))
        except IntegrityError:  # 다른 프로세스가 같은 키로 방금 기록 → 중복
            self._orders[coid] = "pending"
            from .. import ops
            st = ops.get_state(self.engine, "health:duplicate_blocked")
            ops.set_state(self.engine, "health:duplicate_blocked",
                          {"count": int(st.get("count", 0)) + 1, "last": coid, "at": datetime.now(UTC).isoformat()})
            return None
        return coid

    def placed(self, coid, broker_order_id, orgno=None) -> None:
        super().placed(coid, broker_order_id, orgno)
        with session_scope(self.engine) as s:
            rec = s.scalar(select(OrderRecord).where(OrderRecord.client_order_id == coid))
            if rec is not None:
                rec.status, rec.broker_order_id, rec.broker_orgno = "submitted", str(broker_order_id), orgno
                rec.updated_at = datetime.now(UTC)

    def order(self, ts, order, status, reasons, fill) -> None:
        super().order(ts, order, status, reasons, fill)
        reason = "; ".join([order.reason, *reasons]).strip("; ")
        with session_scope(self.engine) as s:
            rec = s.scalar(select(OrderRecord).where(OrderRecord.client_order_id == order.client_order_id)) \
                if order.client_order_id else None
            if rec is None:
                rec = OrderRecord(mode=self.mode, created_at=ts, symbol=order.symbol, side=order.side.value,
                                  qty=order.qty, order_type=order.order_type, limit_price=order.limit_price,
                                  status=status, reason=reason, prediction_id=order.prediction_id,
                                  client_order_id=order.client_order_id, consensus_id=order.consensus_id,
                                  ref_price=order.ref_price)
                s.add(rec)
            rec.status, rec.reason, rec.updated_at = status, reason, datetime.now(UTC)
            rec.limit_price = order.limit_price if order.limit_price is not None else rec.limit_price
            if fill:
                rec.qty = fill.qty
                rec.filled_qty, rec.avg_price = fill.qty, fill.price
            s.flush()
            if fill:
                s.add(FillRecord(order_id=rec.id, ts=fill.ts, qty=fill.qty, price=fill.price, fee=fill.fee))


def _jsonable(d: dict) -> dict:
    import json
    return json.loads(json.dumps(d, default=str))

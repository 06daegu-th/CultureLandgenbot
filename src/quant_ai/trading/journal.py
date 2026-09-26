"""Trade Journal: 신호, 주문, 리스크 차단, 체결을 전부 기록한다 (복기의 원천 데이터)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.engine import Engine

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


class Journal:
    """메모리 저널 (백테스트용). ``DBJournal`` 은 DB 에도 저장."""

    def __init__(self, mode: str):
        self.mode = mode
        self.entries: list[Entry] = []

    def note(self, ts: datetime, kind: str, message: str, symbol: str | None = None, **data) -> None:
        self.entries.append(Entry(ts, self.mode, kind, symbol, message, data))

    def order(self, ts: datetime, order: Order, status: str, reasons: list[str], fill: Fill | None) -> None:
        self.note(ts, "order", f"{order.side.value} {order.qty} {order.symbol} → {status}", order.symbol,
                  side=order.side.value, qty=order.qty, status=status, reasons=reasons, prob_up=order.prob_up,
                  reason=order.reason,
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
            s.add(JournalEntry(ts=ts, mode=self.mode, kind=kind, symbol=symbol, message=message, data=data))

    def order(self, ts, order, status, reasons, fill) -> None:
        super().order(ts, order, status, reasons, fill)
        with session_scope(self.engine) as s:
            rec = OrderRecord(mode=self.mode, created_at=ts, symbol=order.symbol, side=order.side.value,
                              qty=fill.qty if fill else order.qty, order_type=order.order_type,
                              limit_price=order.limit_price, status=status,
                              reason="; ".join([order.reason, *reasons]).strip("; "),
                              prediction_id=order.prediction_id)
            s.add(rec)
            s.flush()
            if fill:
                s.add(FillRecord(order_id=rec.id, ts=fill.ts, qty=fill.qty, price=fill.price, fee=fill.fee))

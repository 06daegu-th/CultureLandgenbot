"""주문 멱등성 · 재시작 복구 · 슬리피지 실측 · 예전 DB 자동 업그레이드 · 합의 ID 기록."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from quant_ai.config import Mode
from quant_ai.data.db import init_db, session_scope
from quant_ai.data.models import OrderRecord
from quant_ai.trading.execution import ExecutionEngine, Signal
from quant_ai.trading.journal import DBJournal, Journal
from quant_ai.trading.portfolio import Order, Portfolio, Side
from tests.test_kis_live import cfg, live  # noqa: F401 - pytest fixture 재사용


# ------------------------------------------------------------------ 저널 단위
def test_memory_journal_blocks_duplicates_and_allows_retry_after_unfilled():
    j = Journal("live")
    o = Order("A", Side.BUY, 10)
    now = datetime(2026, 9, 28, 1, tzinfo=UTC)
    c1 = j.begin(now, o, "live:2026-09-28:A:buy:10")
    assert c1 == "live:2026-09-28:A:buy:10#1"
    assert j.begin(now, o, "live:2026-09-28:A:buy:10") is None  # 아직 진행 중 → 중복 금지
    j.order(now, replace(o, client_order_id=c1), "unfilled", [], None)
    assert j.begin(now, o, "live:2026-09-28:A:buy:10") == "live:2026-09-28:A:buy:10#2"  # 미체결 → 재시도 허용
    j.order(now, replace(o, client_order_id="live:2026-09-28:A:buy:10#2"), "filled", [], None)
    assert j.begin(now, o, "live:2026-09-28:A:buy:10") is None  # 이미 체결 → 다시 사지 않음
    assert j.of_kind("dedupe")


def test_db_journal_lifecycle_and_persistence(tmp_path):
    from quant_ai.data.db import make_engine
    eng = make_engine(f"sqlite:///{tmp_path}/j.db")
    init_db(eng)
    now = datetime(2026, 9, 28, 1, tzinfo=UTC)
    j = DBJournal("live", eng)
    o = Order("005930", Side.BUY, 3, ref_price=70000, consensus_id=77)
    coid = j.begin(now, o, "live:2026-09-28:005930:buy:3")
    with session_scope(eng) as s:
        rec = s.query(OrderRecord).one()
        assert (rec.status, rec.client_order_id, rec.consensus_id, rec.prediction_id) == ("pending", coid, 77, None)
    j.placed(coid, "0000012345", "00950")
    assert DBJournal("live", eng).begin(now, o, "live:2026-09-28:005930:buy:3") is None  # 새 프로세스에서도 중복 차단
    with session_scope(eng) as s:
        rec = s.query(OrderRecord).one()
        assert (rec.status, rec.broker_order_id, rec.broker_orgno) == ("submitted", "0000012345", "00950")


def test_old_database_gets_new_columns(tmp_path):
    """예전 버전으로 만든 quant_ai.db 도 업데이트 후 그대로 쓴다 (컬럼 자동 추가)."""
    url = f"sqlite:///{tmp_path}/old.db"
    raw = create_engine(url)
    with raw.begin() as c:
        c.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, mode VARCHAR(16), created_at DATETIME, "
                       "symbol VARCHAR(32), side VARCHAR(4), qty FLOAT, order_type VARCHAR(16), limit_price FLOAT, "
                       "status VARCHAR(16), reason TEXT, broker_order_id VARCHAR(64), prediction_id INTEGER)"))
        c.execute(text("INSERT INTO orders (mode, created_at, symbol, side, qty, order_type, status) "
                       "VALUES ('paper', '2026-01-01', 'A', 'buy', 1, 'market', 'filled')"))
    from quant_ai.data.db import make_engine
    eng = make_engine(url)
    init_db(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("orders")}
    assert {"client_order_id", "consensus_id", "ref_price", "avg_price", "updated_at"} <= cols
    with session_scope(eng) as s:
        assert s.query(OrderRecord).one().symbol == "A"  # 기존 데이터 보존
    init_db(eng)  # 두 번 실행해도 안전


# ------------------------------------------------------------------ KIS 모의 서버로 재시작 복구
def _open_order_at_broker(app, mock, code, qty=3):
    """증권사에 미체결로 살아 있는 주문 + DB 에는 'submitted' 로 남은 상태 (체결 대기 중 프로세스 종료를 재현)."""
    from quant_ai.trading.kis import KISClient
    c = KISClient.from_env(app.settings.artifacts_dir)
    mock.fill_ratio = 0.0
    placed = c.order(code, Side.BUY, qty, int(mock.prices[code] * 0.5))
    mock.fill_ratio = 1.0
    with session_scope(app.engine) as s:
        s.add(OrderRecord(mode="live", created_at=datetime.now(UTC), symbol=code, side="buy", qty=qty,
                          order_type="limit", status="submitted", client_order_id=f"live:crash:{code}#1",
                          broker_order_id=placed["odno"], broker_orgno=placed["orgno"], ref_price=mock.prices[code]))
    return placed["odno"]


def test_restart_recovers_and_cancels_open_order(live):  # noqa: F811
    app, mock = live
    code = sorted(mock.prices)[0]
    odno = _open_order_at_broker(app, mock, code)
    app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    assert mock.orders[odno].get("cancelled")  # 살아 있던 미체결 주문은 취소됨
    with session_scope(app.engine) as s:
        rec = s.query(OrderRecord).filter_by(broker_order_id=odno).one()
        assert rec.status == "unfilled" and "재시작 복구" in rec.reason
        assert not s.query(OrderRecord).filter(OrderRecord.status.in_(("pending", "submitted"))).count()
    assert not app.kill_switch_on()


def test_unknown_order_state_stops_new_buys(live):  # noqa: F811
    app, mock = live
    with session_scope(app.engine) as s:  # 접수 응답을 받기 전에 죽은 주문 (주문번호 없음)
        s.add(OrderRecord(mode="live", created_at=datetime.now(UTC), symbol=sorted(mock.prices)[0], side="buy",
                          qty=5, order_type="limit", status="pending", client_order_id="live:crash:x#1"))
    n0 = len(mock.orders)
    app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    assert app.kill_switch_on()
    buys = [o for o in list(mock.orders.values())[n0:] if o["buy"]]
    assert not buys  # 증권사에 주문이 살아 있을 수 있으므로 신규 매수 금지
    assert any("상태 불명" in m for _, m in app.notifier.sent)


def test_live_orders_record_keys_consensus_and_slippage(live):  # noqa: F811
    app, mock = live
    app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    with session_scope(app.engine) as s:
        recs = s.query(OrderRecord).filter(OrderRecord.mode == "live", OrderRecord.status == "filled").all()
        assert recs and all(r.client_order_id and r.broker_order_id and r.ref_price and r.avg_price for r in recs)
        assert all(r.prediction_id is None for r in recs)  # 합의 ID 를 예측 FK 에 넣던 버그 회귀 방지
    st = app.slippage_stats("live")
    assert st["n"] == len(recs) and st["mean_bps"] > 0  # 모의 서버는 매도1호가+1원에 체결 → 불리한 방향


def test_execution_engine_skips_duplicate_when_journal_says_so():
    from quant_ai.config import RiskLimits
    from quant_ai.trading.broker import MarketQuote, PaperBroker
    from quant_ai.trading.risk import RiskEngine
    pf = Portfolio(cash=1_000_000)
    j = Journal("paper")
    eng = ExecutionEngine(PaperBroker(pf), RiskEngine(RiskLimits(max_position_weight=1.0)), j)
    ts = datetime(2026, 9, 28, 1, tzinfo=UTC)
    q = {"A": MarketQuote(last=1000.0, bid=999.0, ask=1001.0)}
    j._orders["paper:2026-09-28:A:buy:100#1"] = "submitted"  # 같은 목표로 진행 중인 주문이 있음
    assert eng.rebalance([Signal("A", 0.1)], q, ts) == []
    assert pf.qty("A") == 0 and j.of_kind("dedupe")


def test_paper_pending_rows_are_closed_on_next_cycle(tmp_path):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    app = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/p.db", artifacts_dir=tmp_path))
    with session_scope(app.engine) as s:
        s.add(OrderRecord(mode="paper", created_at=datetime.now(UTC), symbol="A", side="buy", qty=1,
                          order_type="market", status="pending", client_order_id="paper:x#1"))
    res = app.recover_orders("paper")
    assert res and res[0]["status"] == "cancelled" and not app.kill_switch_on()
    with pytest.raises(ValueError):
        app.trade([], Mode.RESEARCH)

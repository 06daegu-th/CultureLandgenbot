"""KIS 모의투자 LIVE 경로 통합 테스트 (로컬 가짜 KIS 서버).

코어-위성 → 리스크 → KISBroker(보호 지정가·체결확인·잔량취소) → 잔고 동기화 → DB 장부 가 끝까지 맞물리는지.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from quant_ai.config import Mode, Settings
from tests.kis_mock import KISMock
from tests.test_marcap import fake_marcap

KIS_ENV = {"KIS_APP_KEY": "APPKEY-TEST-1234", "KIS_APP_SECRET": "SECRET", "KIS_ACCOUNT": "50000000-01", "KIS_ENV": "demo"}


@pytest.fixture()
def live(tmp_path, monkeypatch):
    from quant_ai.pipeline import QuantAI
    fake_marcap(tmp_path, n_codes=14, days=500)
    st = replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/t.db", artifacts_dir=tmp_path / "a",
                 broker="kis", kis_env="demo")
    app = QuantAI(st)
    app.ingest_krx(tmp_path, years=0, top_n=10, end_year=2020)
    bars, _, _ = app.market_data()
    mock = KISMock({s: float(b["close"].iloc[-1]) for s, b in bars.items() if s != "KOSPI"})
    for k, v in {**KIS_ENV, "KIS_BASE_URL": mock.url}.items():
        monkeypatch.setenv(k, v)
    import quant_ai.trading.kis as kis
    monkeypatch.setattr(kis, "MIN_INTERVAL", {"demo": 0.0, "real": 0.0})  # 테스트 속도
    yield app, mock
    mock.close()


def cfg():
    from quant_ai.strategy.core_satellite import CoreSatelliteConfig
    return CoreSatelliteConfig(core_top_k=4, core_buffer_k=6, satellite_k=2, shortlist_k=8)


def test_live_cycle_places_orders_and_syncs_with_broker(live):
    app, mock = live
    r = app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    core = r["plan"].core
    assert len(core) == 4 and len(r["fills"]) >= 3
    # 모의 TR 만 사용, 실전 TR 은 한 번도 호출하지 않음
    trs = {c[2] for c in mock.calls if c[2]}
    assert "VTTC0012U" in trs and not any(t.startswith("TTTC") for t in trs)
    # 증권사 잔고 == DB 장부
    held_broker = {c: int(v[0]) for c, v in mock.positions.items() if v[0] > 0}
    pf = app.load_portfolio("live")
    assert {s: p.qty for s, p in pf.positions.items() if p.qty} == held_broker
    assert set(held_broker) <= set(core)
    # 보호 지정가: 매수 주문가가 매도1호가보다 높되 0.5% 이내 + 호가단위
    for o in mock.orders.values():
        assert o["filled"] > 0


def test_second_cycle_does_not_duplicate_orders(live):
    app, mock = live
    app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    n_orders = len(mock.orders)
    app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    assert len(mock.orders) - n_orders <= 1  # 이미 목표 비중 → 사실상 추가 주문 없음


def test_db_drift_is_corrected_from_broker_balance(live):
    """DB 장부가 틀어져도(장애 등) 다음 사이클에서 증권사 잔고로 바로잡는다."""
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import PortfolioSnapshot
    app, mock = live
    app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    with session_scope(app.engine) as s:  # 잘못된 스냅샷 주입 (가짜 종목 1000주)
        s.add(PortfolioSnapshot(mode="live", ts=datetime.now(UTC), cash=1.0, equity=1.0,
                                positions={"999999": {"qty": 1000, "avg_price": 1.0}}))
    n_orders = len(mock.orders)
    app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    assert "999999" not in app.load_portfolio("live").positions
    assert not any(o["code"] == "999999" for o in list(mock.orders.values())[n_orders:])  # 없는 주식을 팔지 않음


def test_partial_fill_remainder_is_cancelled(live):
    app, mock = live
    mock.fill_ratio = 0.5
    import quant_ai.trading.kis as kis
    orig = kis.KISBroker.__init__

    def fast(self, *a, **k):
        orig(self, *a, **k)
        self.fill_timeout_s, self.poll_s = 0.05, 0.0
    kis.KISBroker.__init__ = fast
    try:
        app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg())
    finally:
        kis.KISBroker.__init__ = orig
    cancels = [c for c in mock.calls if c[1].endswith("order-rvsecncl")]
    assert cancels and all(c[2] == "VTTC0013U" for c in cancels)
    assert all(o.get("cancelled") for o in mock.orders.values() if o["filled"] < o["qty"])


def test_real_env_requires_all_live_gates(live, monkeypatch):
    app, _ = live
    app.settings = replace(app.settings, kis_env="real")
    with pytest.raises(PermissionError):
        app.run_core_satellite(Mode.LIVE, ts=datetime.now(UTC), cfg=cfg(), attribution=False)


def test_base_url_override_only_localhost():
    from quant_ai.trading.kis import KISClient
    with pytest.raises(ValueError):
        KISClient("k", "s", "12345678-01", "demo", base_url="https://evil.example")


def test_kis_check_cli_with_test_order(live, capsys, monkeypatch, tmp_path):
    from quant_ai.cli import main
    app, mock = live
    monkeypatch.setenv("QUANT_ARTIFACTS_DIR", str(tmp_path / "cli-artifacts"))  # 저장소에 토큰 캐시를 남기지 않음
    mock.prices["005930"] = 70000.0
    main(["kis-check", "--symbol", "005930", "--test-order"])
    out = capsys.readouterr().out
    assert "토큰 OK" in out and "✅ 주문·조회·취소 경로 정상" in out
    o = list(mock.orders.values())[-1]
    assert o["filled"] == 0 and o.get("cancelled") and o["qty"] == 1


def test_scheduler_trades_only_in_krx_window():
    """미국장 시간·시가 직후·동시호가에는 국내 주문을 내지 않는다."""
    from types import SimpleNamespace

    from quant_ai.scheduler import build_default_scheduler
    calls = []
    app = SimpleNamespace(settings=replace(Settings.from_env({}), strategy="core_satellite"), notifier=None,
                          engine=None, run_core_satellite=lambda mode, ts: calls.append(ts),
                          review=lambda d: None, evaluate_shadow_models=lambda: None, train_candidate=lambda: None)
    sch = build_default_scheduler(app, Mode.LIVE)
    job = next(j for j in sch.jobs if j.name == "core_satellite")
    kst = lambda h, m: datetime(2026, 9, 24, h - 9, m, tzinfo=UTC) if h >= 9 else datetime(2026, 9, 23, h + 15, m, tzinfo=UTC)  # noqa: E731
    for h, m, expect in [(9, 5, False), (9, 30, True), (15, 5, True), (15, 25, False), (23, 30, False)]:
        calls.clear()
        job.fn(kst(h, m))
        assert bool(calls) is expect, (h, m)

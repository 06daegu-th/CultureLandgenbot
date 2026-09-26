"""실제 PostgreSQL 통합 테스트. QUANT_TEST_DATABASE_URL 이 있을 때만 실행 (CI 에서 실행됨)."""

import os
from dataclasses import replace

import pytest
from sqlalchemy import text

URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="QUANT_TEST_DATABASE_URL 미설정")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.data.db import make_engine
    from quant_ai.demo import run_demo
    from quant_ai.pipeline import QuantAI
    eng = make_engine(URL)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    d = tmp_path_factory.mktemp("pg")
    a = QuantAI(replace(Settings.from_env({}), database_url=URL, artifacts_dir=d))
    run_demo(a, replay_days=5, verbose=False, years=3)
    return a


def test_jsonb_columns(app):
    with app.engine.connect() as c:
        t = c.execute(text("SELECT data_type FROM information_schema.columns "
                           "WHERE table_name='consensus_signals' AND column_name='payload'")).scalar()
    assert t == "jsonb"


def test_advisory_lock_blocks_second_cycle(app, tmp_path):
    from quant_ai import ops
    with ops.trading_lock(app.engine, "trade-live", tmp_path), pytest.raises(ops.LockBusy):
        with ops.trading_lock(app.engine, "trade-live", tmp_path):
            pass


def test_full_cycle_on_postgres(app):
    from quant_ai.config import Mode
    from quant_ai.web.api import DashboardAPI
    ds = app.decide()
    app.trade(ds, Mode.PAPER)
    app.review()
    d = DashboardAPI(app).dashboard()
    assert d["watchlist"] and d["portfolios"]["paper"]["equity"] > 0

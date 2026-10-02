"""로드맵 7개: 예측력 판정 알림 · KIS 자동 검증 · 체결 parity 자동 기록/보정 · 국내 컨센서스 · 금통위/기준금리 · VKOSPI ·
다계좌/세금/배당."""

from dataclasses import replace
from datetime import UTC, date, datetime

import numpy as np
import pytest

from quant_ai import ops


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v14")
    fake_marcap(d, n_codes=6, days=300)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


# ------------------------------------------------------------------ R1 예측력 판정 알림 · 브리핑
def test_power_alerts_on_decision_and_milestones(app):
    from quant_ai.desk import power_alerts
    sent = power_alerts(app, {"decision": "continue", "scored": 90}, {"decision": "continue", "scored": 130, "hit_rate": 0.55,
                                                                      "label": "계속 관찰", "eta": "2027-01-10"})
    assert sent == ["예측력 전진 검증 100건 돌파"]
    sent = power_alerts(app, {"decision": "continue", "scored": 130}, {"decision": "H1", "scored": 420, "hit_rate": 0.58, "label": "x"})
    assert sent and "통과" in sent[0]
    assert power_alerts(app, {"decision": "H1", "scored": 420}, {"decision": "H1", "scored": 430}) == []
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import AlertRecord
    with session_scope(app.engine) as s:
        assert s.query(AlertRecord).filter(AlertRecord.kind == "power").count() == 2


def test_brief_and_report_show_power_progress(app):
    from quant_ai import reports as R
    ops.set_state(app.engine, "prediction_power", {"forward": {"decision": "continue", "scored": 40, "hit_rate": 0.55,
                                                               "more_needed": 600, "eta": "2027-03-01", "label": "계속 관찰"}})
    ops.set_state(app.engine, "readiness", {"status": "CAUTION"})
    b = R.morning_brief(app)
    assert "예측력 전진 검증: 등록 후 채점 40건" in b["text"] and "2027-03-01" in b["text"] and "CAUTION" in b["text"]
    assert "예측력" in R.daily_report(app)["text"]


# ------------------------------------------------------------------ R2 · R3 KIS 자동 검증 · parity
def test_scheduler_adds_kis_validation_only_with_kis(app):
    from quant_ai.config import Mode
    from quant_ai.scheduler import build_default_scheduler
    names = [j.name for j in build_default_scheduler(app, Mode.PAPER).jobs]
    assert "kis_validate" not in names and {"kr_consensus", "bok", "vkospi"} <= set(names)
    kis = replace(app.settings, broker="kis")
    orig = app.settings
    app.settings = kis
    try:
        assert "kis_validate" in [j.name for j in build_default_scheduler(app, Mode.PAPER).jobs]
    finally:
        app.settings = orig


def test_live_fills_record_parity_and_bias_corrects_shadow(app):
    from quant_ai.desk import PARITY_MIN, record_parity, sim_bias
    from quant_ai.trading.broker import MarketQuote, ShadowBroker
    from quant_ai.trading.portfolio import Fill, Order, Portfolio, Side
    ops.set_state(app.engine, "execution_parity", {"rows": []})
    q = {"005930": MarketQuote(last=70000, bid=69900, ask=70000, bid_qty=1000, ask_qty=1000)}
    fills = [Fill(Order("005930", Side.BUY, 10), datetime.now(UTC), 10, 70050.0, 100.0)]
    assert record_parity(app, fills, q, book_fn=lambda s: None) == 1
    row = ops.get_state(app.engine, "execution_parity")["rows"][0]
    assert row["real_bps"] == pytest.approx((70050 - 69950) / 69950 * 1e4, abs=0.05) and row["sim_bps"] is not None
    assert sim_bias(app) == 0.0  # 100건 전에는 보정 없음
    ops.set_state(app.engine, "execution_parity", {"rows": [{"real_bps": 12.0, "sim_bps": 7.0}] * PARITY_MIN})
    assert sim_bias(app) == pytest.approx(5.0)
    book = {"asks": [(70000.0, 500.0)], "bids": [(69900.0, 500.0)]}
    br = ShadowBroker(Portfolio(cash=1e9), book_fn=lambda s: book)
    br.sim_bias_bps = 5.0
    base = ShadowBroker(Portfolio(cash=1e9), book_fn=lambda s: book)
    f1 = br.submit(Order("005930", Side.BUY, 10), MarketQuote(last=69950, sigma=0.02), datetime.now(UTC))
    f0 = base.submit(Order("005930", Side.BUY, 10), MarketQuote(last=69950, sigma=0.02), datetime.now(UTC))
    assert f1.price == pytest.approx(f0.price * (1 + 5 / 1e4))


# ------------------------------------------------------------------ R4 국내 컨센서스
def _naver_q(consensus_next=True, actual=None):
    heads = [{"key": "202603", "title": "2026.03.", "isConsensus": "N"}, {"key": "202606", "title": "2026.06.", "isConsensus": "N"},
             {"key": "202509", "title": "2025.09.", "isConsensus": "N"},
             {"key": "202609", "title": "2026.09.(E)", "isConsensus": "Y" if consensus_next else "N"}]
    op = {"202603": {"value": "66,000"}, "202606": {"value": "91,000"}, "202509": {"value": "60,000"},
          "202609": {"value": "100,000" if actual is None else f"{actual:,}"}}
    return {"financeInfo": {"trTitleList": heads, "rowList": [
        {"title": "매출액", "columns": {k: {"value": "700,000"} for k in op}},
        {"title": "영업이익", "columns": op}]}}


def test_kr_consensus_snapshots_then_surprise(app):
    from quant_ai.data.collectors import kr_consensus as K
    from quant_ai.engines.earnings import get as eget
    p = K.parse_quarter(_naver_q())
    assert [x["consensus"] for x in p["periods"]] == [False, False, False, True] and p["values"]["op_income"]["202609"] == 100000
    st = K.update(app.engine, "000020", p, datetime(2026, 9, 1, tzinfo=UTC))
    assert st["upcoming"]["period"] == "202609" and st["upcoming"]["op_income_yoy"] == pytest.approx(100000 / 60000 - 1)
    st = K.update(app.engine, "000020", K.parse_quarter(_naver_q(False, 112000)), datetime(2026, 9, 20, tzinfo=UTC))
    s = st["surprises"][-1]
    assert s["period"] == "202609" and s["surprise_pct"] == pytest.approx(12.0) and s["date"] == "2026-09-20"
    assert len(K.update(app.engine, "000020", K.parse_quarter(_naver_q(False, 112000)))["surprises"]) == 1  # 한 번만
    assert K.history_for_model(app.engine, "000020")[0]["surprise_pct"] == pytest.approx(12.0)
    bars = app.market_data()[0].get("000020")
    m = eget(app.engine, "000020", bars, None)
    assert m["kr_history"] == 1 and m["beats"] == 1 and "네이버 컨센서스" in m["source"]
    assert K.collect(app.engine, ["000030"], fetch=lambda c: {"weird": 1})["failed"]


# ------------------------------------------------------------------ R5 금통위 · 기준금리
def test_bok_schedule_parsing_manual_and_base_rate(app):
    from quant_ai.data.collectors import bok
    html = "<td>2026.10.22(목)</td><td>2026.11.26 (목)</td><span>게시일 2026.09.01</span>"
    assert bok.parse_schedule(html) == ["2026-10-22", "2026-11-26"]
    assert bok.schedule(app.engine, env={"QUANT_BOK_DATES": "2026-10-22, bad"})["dates"] == ["2026-10-22"]
    st = bok.schedule(app.engine, fetch=lambda u: html, env={})
    assert st["dates"] == ["2026-10-22", "2026-11-26"] and "한국은행" in st["source"]
    rows = [{"TIME": "20260101", "DATA_VALUE": "2.75"}, {"TIME": "20260227", "DATA_VALUE": "2.50"}, {"TIME": "20260228", "DATA_VALUE": "2.50"}]
    r = bok.base_rate(app.engine, "k", fetch=lambda u: {"StatisticSearch": {"row": rows}})
    assert r["current"] == 2.5 and r["changes"] == [{"date": "2026-02-27", "from": 2.75, "to": 2.5, "bp": -25}]
    assert bok.base_rate(app.engine, None)["available"] is False


def test_bok_meeting_enters_calendar_and_event_risk():
    from quant_ai.engines import events as E
    ev = E.with_dday(E.build_calendar(date(2026, 10, 1), date(2026, 11, 30), bok={"dates": ["2026-10-22"], "source": "사용자 입력"}),
                     date(2026, 10, 22))
    b = next(e for e in ev if e["kind"] == "bok")
    assert b["market"] == "KR" and b["importance"] >= 0.9 and b["d_day"] == 0
    r = E.event_risk("000660", ev, date(2026, 10, 22), beta=1.5)
    assert r["buy_multiplier"] == 0.75 and "금통위" in r["reason"]


# ------------------------------------------------------------------ R6 VKOSPI
def test_vkospi_parse_fallback_and_stock_move(app):
    from quant_ai.data.collectors import vkospi as V
    rows = [{"TRD_DD": f"2026/0{m}/{d:02d}", "CLSPRC_IDX": f"{18 + (m * d) % 9}.5"} for m in (7, 8, 9) for d in range(1, 29)]
    s = V.parse_krx({"output": rows})
    assert len(s) == 84 and s.index.is_monotonic_increasing
    got = V.get(app.engine, None, post=lambda url, form: {"output": rows}, now=datetime(2026, 9, 30, tzinfo=UTC))
    assert got["available"] and not got["proxy"] and got["daily_move"] == pytest.approx(got["level"] / 100 / np.sqrt(252), rel=1e-3)
    ops.set_state(app.engine, "vkospi", {})
    bench = app.market_data()[1]
    fb = V.get(app.engine, bench, post=lambda url, form: (_ for _ in ()).throw(RuntimeError("403")))
    assert fb["available"] and fb["proxy"] and "대용" in fb["source"]
    m1 = V.stock_move({"available": True, "daily_move": 0.01}, 1.5, 0.02, 1)
    assert m1 == pytest.approx(np.sqrt(0.015 ** 2 + 0.02 ** 2)) and V.stock_move({"available": True, "daily_move": 0.01}, 1.0, 0.0, 4) == pytest.approx(0.02)


# ------------------------------------------------------------------ R7 다계좌 · 세금 · 배당
def test_account_tax_rules():
    from quant_ai.accounts import RULES, evaluate
    accts = [
        {"id": "a", "name": "키움 일반", "type": "general", "cash": 1_000_000, "holdings": [{"symbol": "005930", "qty": 100, "avg_price": 60000}]},
        {"id": "b", "name": "ISA", "type": "isa", "cash": 0, "realized_ytd": 500_000,
         "holdings": [{"symbol": "000660", "qty": 10, "avg_price": 100000}]},
        {"id": "c", "name": "해외", "type": "overseas", "cash": 0, "realized_ytd": 2_000_000,
         "holdings": [{"symbol": "NVDA", "qty": 10, "avg_price": 100.0}, {"symbol": "TSLA", "qty": 5, "avg_price": 300.0}]},
        {"id": "d", "name": "연금", "type": "pension", "cash": 0, "holdings": [{"symbol": "069500", "qty": 10, "avg_price": 30000}]},
    ]
    prices = {"005930": 70000, "000660": 400000, "NVDA": 150.0, "TSLA": 250.0, "069500": 40000}
    r = evaluate(accts, prices, 1400.0, {"005930": {"rate": 1444, "ex_date": "2026-12-29"}}, date(2026, 10, 1))
    by = {a["id"]: a for a in r["accounts"]}
    assert by["a"]["gain"] == 1_000_000 and by["a"]["div_12m"] == 144_400 and by["a"]["div_tax"] == round(144_400 * 0.154)
    isa_net = 3_000_000 + 500_000
    assert by["b"]["tax_now"] == round((isa_net - RULES["isa_exempt"]) * RULES["isa_rate"])
    assert by["d"]["tax_now"] == 0 and "과세 이연" in by["d"]["note"]
    ov = 10 * 50 * 1400 + 5 * (-50) * 1400 + 2_000_000  # 700,000 − 350,000 + 2,000,000
    assert r["overseas"]["gain_ytd_est"] == ov and r["overseas"]["tax"] == round((ov - 2_500_000) * 0.22 if ov > 2_500_000 else 0)
    assert r["dividend_calendar"][0]["symbol"] == "005930"
    assert any("ISA" in t or "해외" in t for t in r["tips"])
    big = evaluate([{**accts[2], "realized_ytd": 5_000_000}], prices, 1400.0, {}, date(2026, 10, 1))
    assert big["overseas"]["tax"] > 0 and any("상계" in t for t in big["tips"])


def test_accounts_crud_and_summary(app):
    from quant_ai import accounts
    from quant_ai.web.api import DashboardAPI
    with pytest.raises(ValueError):
        accounts.upsert(app.engine, {"name": "x", "type": "crypto"})
    with pytest.raises(ValueError):
        accounts.upsert(app.engine, {"name": "x", "type": "general", "holdings": [{"symbol": "bad sym!", "qty": 1, "avg_price": 1}]})
    sym = next(iter(app.market_data()[0]))
    api = DashboardAPI(app)
    a = api.accounts_write({"name": "테스트 ISA", "type": "isa", "cash": 100000, "holdings": [{"symbol": sym, "qty": 3, "avg_price": 10000}]})
    s = api.accounts()
    row = next(x for x in s["accounts"] if x["id"] == a["id"])
    assert row["holdings"][0]["priced"] and row["type_label"] == "ISA" and s["fx_source"].startswith("기본값")
    assert api.accounts_write({"delete": a["id"]})["deleted"]
    assert not any(x["id"] == a["id"] for x in api.accounts()["accounts"])

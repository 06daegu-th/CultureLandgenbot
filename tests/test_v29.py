"""v29: 1단계 남은 것 — 데이터 정합성 점검 · KIS 모의 전 과정(주문→체결→장부) · 100만원 증명 프로젝트(봉인 기록·공개 페이지) · 차트 손질."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v29")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


# ------------------------------------------------------------------ 1. 데이터 정합성
def test_krx_facts_detects_split_and_marcap_mismatch():
    from quant_ai.data.collectors.marcap import krx_facts
    d = pd.bdate_range("2026-01-01", periods=6, tz="UTC")
    close = [100000, 101000, 2040, 2050, 2060, 2070]  # 셋째 날 50:1 액면분할 (기준가 2020 = 101000/50)
    chg = [0, 1000, 20, 10, 10, 10]
    stocks = [1e6, 1e6, 5e7, 5e7, 5e7, 5e7]
    df = pd.DataFrame({"Date": d, "Code": "000100", "Close": close, "Changes": chg, "Stocks": stocks, "Market": "KOSPI",
                       "Marcap": [c * s for c, s in zip(close, stocks, strict=True)]})
    df.loc[5, "Marcap"] *= 1.2  # 원천 시가총액이 주식수×종가와 다른 날
    f = krx_facts(df, ["000100"])["000100"]
    assert f["n_adj_events"] == 1 and f["adj_events"][0]["date"] == str(d[2].date()) and abs(f["adj_events"][0]["factor"] - 0.02) < 1e-3
    assert f["marcap_mismatch"] == 1 and f["shares"] == 5e7 and f["close"] == 2070


def test_ingest_stores_facts_and_stock_market_cap(app):
    f = ops.get_state(app.engine, "krx_facts")
    assert f["symbols"] and all("marcap" in v for v in f["symbols"].values())
    sym = sorted(f["symbols"])[0]
    assert f["symbols"][sym]["date"] and f["symbols"][sym]["rows"] > 100
    from quant_ai import toss
    st = toss.stock(app, sym)
    assert st.get("market_cap") and "KRX" in st.get("market_cap_src", "")


def test_naver_parser_and_compare():
    from quant_ai.datacheck import compare, parse_naver_chart
    xml = '<chartdata><item data="20260921|1|2|0.5|285000|100" /><item data="20260922|1|2|0.5|286000|100" /><item data="bad|x" /></chartdata>'
    assert parse_naver_chart(xml.encode()) == [("2026-09-21", 285000.0), ("2026-09-22", 286000.0)]
    idx = pd.bdate_range("2026-01-01", periods=100, tz="UTC")
    ours = pd.Series(np.linspace(100, 200, 100), index=idx)
    same = [(str(t.date()), float(v)) for t, v in ours.items()]
    assert compare(ours, same)["verdict"] == "ok"
    scaled = [(d, v * 50) for d, v in same]  # 수정주가 기준이 다른 원천 (분할 전 가격)
    c = compare(ours, scaled)
    assert c["verdict"] == "bad" and "수정주가 기준" in c["text"]
    one = [(d, v * (1.1 if i == 50 else 1)) for i, (d, v) in enumerate(same)]
    w = compare(ours, one)
    assert w["verdict"] == "warn" and w["n_bad"] == 1 and w["bad"][0]["date"] == same[50][0]
    assert compare(ours, [("1999-01-01", 1.0)])["verdict"] == "unknown"


def test_scan_bars_flags_limit_bigmove_halt_and_skips_delisted():
    from quant_ai.datacheck import scan_bars
    idx = pd.bdate_range("2026-01-01", periods=60, tz="UTC")

    def bars(c, v=1e5):
        c = pd.Series(c, index=idx[: len(c)], dtype=float)
        return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": v})
    base = list(np.linspace(1000, 1100, 60))
    jump = base.copy()
    jump[40] = jump[39] * 1.45  # 하루 +45% (제한폭 밖)
    big = base.copy()
    big[50] = big[49] * 1.2   # 대형주 +20%
    halt = bars(base)
    halt.iloc[-5:, halt.columns.get_loc("volume")] = 0
    old = bars(base[:20])     # 이미 끝난 종목 (상장폐지)
    old.iloc[10, old.columns.get_loc("close")] *= 2
    iss = scan_bars({"000100": bars(jump), "000200": bars(big), "000300": halt, "000400": old}, {}, top={"000200"})
    kinds = {(x["symbol"], x["kind"]) for x in iss}
    assert ("000100", "limit") in kinds and ("000200", "bigmove") in kinds and ("000300", "halt") in kinds
    assert not any(x["symbol"] == "000400" for x in iss)
    assert iss[0]["level"] == "bad"  # 오류가 먼저


def test_datacheck_run_offline_and_with_reference(app):
    from quant_ai import datacheck as DC
    off = DC.run(app, get=lambda url: (_ for _ in ()).throw(ConnectionError("no net")))
    assert off["reference"]["status"] == "unreachable" and len(off["reference"]["rows"]) == 2  # 처음 두 종목 실패 → 그만
    assert off["status"] in ("warn", "bad") and "외부 시세 대조 못 함" in off["headline"]
    assert off["n_symbols"] >= 4 and ops.get_state(app.engine, DC.STATE_KEY)["at"] == off["at"]
    bars, _, _ = app.market_data()

    def fake_get(url):
        sym = url.split("symbol=")[1].split("&")[0]
        c = bars[sym]["close"]
        items = "".join(f'<item data="{pd.Timestamp(t).strftime("%Y%m%d")}|1|1|1|{v:.4f}|1" />' for t, v in c.items())
        return f"<chartdata>{items}</chartdata>".encode()
    on = DC.run(app, get=fake_get, sample=3)
    assert on["reference"]["status"] == "done" and all(r["verdict"] == "ok" and r["source"] == "네이버 차트" for r in on["reference"]["rows"])
    assert on["update"]["configured"] in (True, False) and on["update"]["text"]


# ------------------------------------------------------------------ 2. KIS 전 과정
class StatefulKIS:
    """주문하면 잔고가 실제로 바뀌는 가짜 모의계좌."""

    def __init__(self, env="demo"):
        self.env, self.cano, self.prdt, self.app_key, self.app_secret, self.base = env, "12345678", "01", "k" * 20, "s" * 20, "http://x"
        self.qty, self.orders, self.cancelled = 3, {}, set()

    def token(self):
        return "t"

    def balance(self):
        from quant_ai.trading.portfolio import Position
        return 1_000_000.0, ({"005930": Position(self.qty, 70000)} if self.qty else {})

    def quote(self, code):
        from quant_ai.trading.broker import MarketQuote
        return MarketQuote(last=70000, bid=69900, ask=70000, bid_qty=500, ask_qty=400)

    def price(self, code):
        return 70000.0

    def order(self, code, side, qty, px):
        odno = str(len(self.orders) + 1)
        fills = px >= 69000
        self.orders[odno] = {"side": side.value, "qty": qty, "px": px, "filled": qty if fills else 0}
        if fills:
            self.qty += qty if side.value == "buy" else -qty
        return {"odno": odno, "orgno": "0001"}

    def order_status(self, odno, day=None):
        o = self.orders[odno]
        rem = 0 if odno in self.cancelled else o["qty"] - o["filled"]
        return {"filled": o["filled"], "avg_price": 70000.0 if o["filled"] else 0.0, "remaining": rem, "cancelled": odno in self.cancelled}

    def cancel(self, odno, orgno):
        self.cancelled.add(odno)


def test_kis_e2e_order_fill_ledger_dedupe_recover(app):
    from sqlalchemy import select

    from quant_ai.data.db import session_scope
    from quant_ai.data.models import FillRecord, OrderRecord
    from quant_ai.trading.kis_check import run
    c = StatefulKIS()
    r = run(c, market_open=True, ws_key_fn=lambda: "k", sleep=lambda s: None, e2e_engine=app.engine)
    st = {s["key"]: s for s in r["steps"]}
    assert r["ok"] and r["e2e_verified"], r["failed"]
    assert any("중복 방지" in x for x in st["e2e"]["checks"]) and any("재시작 복구" in x for x in st["e2e"]["checks"])
    assert c.qty == 3  # 사고 되판 뒤 원래대로
    with session_scope(app.engine) as s:
        recs = list(s.scalars(select(OrderRecord).where(OrderRecord.mode == "kis-verify")))
        assert {x.status for x in recs} >= {"filled", "cancelled"} and all(x.broker_order_id for x in recs)
        assert len(list(s.scalars(select(FillRecord)))) >= 2
    # 장외 · 실전 · 요청 안 함 → 건너뜀 (주문 없음)
    for kw in ({"market_open": False, "e2e_engine": app.engine}, {"market_open": True}):
        rr = run(StatefulKIS(), ws_key_fn=lambda: "k", sleep=lambda s: None, **kw)
        assert {s["key"]: s for s in rr["steps"]}["e2e"]["ok"] is None and not rr["e2e_verified"]
    real = run(StatefulKIS("real"), market_open=True, ws_key_fn=lambda: "k", e2e_engine=app.engine)
    assert {s["key"]: s for s in real["steps"]}["e2e"]["ok"] is None


def test_kis_e2e_fails_when_ledger_and_broker_disagree(app):
    from quant_ai.trading.kis_check import run
    c = StatefulKIS()
    orig = c.order

    def lost(code, side, qty, px):  # 증권사가 매수 체결을 잔고에 반영하지 않음 (장부와 불일치)
        out = orig(code, side, qty, px)
        if side.value == "buy" and px >= 69000:
            c.qty -= qty
        return out
    c.order = lost
    r = run(c, market_open=True, ws_key_fn=lambda: "k", sleep=lambda s: None, e2e_engine=app.engine)
    assert not r["ok"] and "잔고 대조 실패" in r["failed"]


def test_validation_progress_counts_e2e(app):
    from quant_ai import validation
    ops.set_state(app.engine, "kis_validation", {"ok": True, "order_path_verified": True, "fill_path_verified": True, "history": []})
    saved = app.settings
    app.settings = replace(saved, broker="kis")
    try:
        k = next(i for i in validation.progress(app)["items"] if i["key"] == "kis")
    finally:
        app.settings = saved
    assert k["progress"] == 0.75 and "--e2e" in k["next"] and "전 과정" in k["detail"]


# ------------------------------------------------------------------ 3. 증명 프로젝트
def test_proof_project_rules_record_chain_and_public(app, monkeypatch):
    from quant_ai import budget, proof
    ops.set_state(app.engine, proof.STATE, {})
    ops.set_state(app.engine, proof.LOG, {})
    t0 = datetime(2026, 9, 21, 1, tzinfo=UTC)  # 월요일 10시 KST
    rec = proof.start(app, "paper", now=t0)
    assert rec["params"]["principal"] == 1_000_000 and rec["params"]["max_loss"] == 150_000 and len(rec["hash"]) == 64
    assert app.settings.risk.max_position_weight == 0.20 and app.settings.risk.max_daily_loss_pct == 0.03
    assert budget.get(app)["on_stop"] == "liquidate" and budget.get(app)["proof"]
    with pytest.raises(ValueError, match="이미 진행 중"):
        proof.start(app, "paper", now=t0)
    assert proof.record_day(app, now=datetime(2026, 9, 21, 5, tzinfo=UTC)) is None  # 14시 KST — 장 마감 전
    d1 = proof.record_day(app, now=datetime(2026, 9, 21, 7, tzinfo=UTC))     # 16시 KST
    assert d1 and d1["prev"] == rec["hash"] and d1["cum"] == 0.0
    assert proof.record_day(app, now=datetime(2026, 9, 21, 8, tzinfo=UTC)) is None  # 같은 날 두 번 안 남김
    assert proof.record_day(app, now=datetime(2026, 9, 26, 8, tzinfo=UTC)) is None  # 토요일
    d2 = proof.record_day(app, now=datetime(2026, 9, 22, 7, tzinfo=UTC))
    assert d2["prev"] == d1["hash"]
    s = proof.status(app, now=datetime(2026, 9, 22, 8, tzinfo=UTC))
    assert s["chain"]["ok"] and s["n_days"] == 2 and not s["finished"] and "진행 중" in s["verdict"]
    assert [c["key"] for c in s["criteria"]] == ["orders", "excess", "mdd", "sprt"]
    # 지난 기록을 고치면 체인 검증에서 드러난다
    lg = ops.get_state(app.engine, proof.LOG)
    lg["days"][0]["cum"] = 0.5
    ops.set_state(app.engine, proof.LOG, lg)
    bad = proof.status(app, now=datetime(2026, 9, 22, 8, tzinfo=UTC))["chain"]
    assert not bad["ok"] and bad["broken_at"] == "2026-09-21"
    lg["days"][0]["cum"] = 0.0
    ops.set_state(app.engine, proof.LOG, lg)
    # 공개: 기본 비공개 · 금액 숨김
    monkeypatch.delenv("QUANT_PROOF_PUBLIC", raising=False)
    assert not proof.is_public(app)
    v = proof.public_view(app)
    assert "equity" not in v["days"][0] and next(r for r in v["rules"] if r["key"] == "principal")["value"] == "비공개"
    html = proof.public_html(v)
    assert "<svg" in html and "성공 기준" in html and "1,000,000" not in html and "12345678" not in html
    proof.set_public(app, True, show_amounts=True)
    assert proof.is_public(app) and "equity" in proof.public_view(app)["days"][0]
    done = proof.end(app, "시험 끝", now=datetime(2026, 9, 23, 8, tzinfo=UTC))
    assert done["log_head"] == d2["hash"] and proof.active(app) is None and proof.status(app)["history"][-1]["id"] == rec["id"]


def test_risk_engine_max_positions_only_for_proof_book(app):
    from quant_ai import proof
    from quant_ai.config import RiskLimits
    from quant_ai.trading.portfolio import Order, Portfolio, Position, Side
    from quant_ai.trading.risk import RiskEngine
    ops.set_state(app.engine, proof.STATE, {})
    proof.start(app, "paper", now=datetime(2026, 9, 21, 1, tzinfo=UTC))
    r = RiskEngine(RiskLimits(max_position_weight=0.5, max_order_value=1e9))
    proof.apply_risk(app, "live", r)
    assert r.max_positions is None  # 다른 장부엔 적용 안 함
    proof.apply_risk(app, "paper", r)
    assert r.max_positions == 5
    pf = Portfolio(cash=1e7, positions={f"00{i}000": Position(1, 1000) for i in range(1, 6)})
    prices = {**{s: 1000.0 for s in pf.positions}, "009990": 1000.0}
    new = r.check(Order("009990", Side.BUY, 10), pf, prices)
    assert not new.approved and "보유 종목 수" in new.reasons[0]
    assert r.check(Order("001000", Side.BUY, 10), pf, prices).approved  # 이미 가진 종목 추가는 됨
    assert r.check(Order("001000", Side.SELL, 1), pf, prices).approved
    proof.end(app, "정리")


# ------------------------------------------------------------------ 4. 화면 · 연결
def test_v29_wiring():
    t1 = (STATIC / "toss.js").read_text()
    t2 = (STATIC / "toss2.js").read_text()
    app_js = (STATIC / "app.js").read_text()
    srv = (ROOT / "src/quant_ai/web/server.py").read_text()
    # 차트: 콤마 · 기준선 점선 · 라이브러리 로고 대신 글 출처 · 구간 수익률
    assert 'type: "custom"' in t1 and "createPriceLine" in t1 and "어제 종가" in t1 and "range:" in t1 and "TradingView" in t1
    assert "attributionLogo: false" in app_js and "priceFormatter" in app_js
    assert "TV.datacheck" in t2 and "TV.proof" in t2 and '["datacheck"' in app_js and '["proof"' in app_js and "#proof" in t1
    assert '"/api/datacheck"' in srv and '"/api/proof-project"' in srv and '"/proof", "/proof.json"' in srv
    sch = (ROOT / "src/quant_ai/scheduler.py").read_text()
    assert 'sch.add("datacheck"' in sch and 'sch.add("proof_day"' in sch
    cli = (ROOT / "src/quant_ai/cli.py").read_text()
    assert '"datacheck"' in cli and '"proof-project"' in cli and '"--e2e"' in cli

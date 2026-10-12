"""v40 실적 대비 · KB증권 읽기 전용 · 투자위원회 · 신뢰 상태줄. 네트워크는 모두 가짜."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


@pytest.fixture(scope="module")
def mapp(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v40")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0,
                        news_feeds=[], dart_api_key=None, fred_api_key=None, llm_providers={}, anthropic_enabled=False))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def fake_kb(log):
    def t(url, headers, body):
        log.append((url, headers, body))
        if url.endswith("/oauth2/token"):
            return {"dataHeader": {}, "dataBody": {"access_token": "T" * 40, "expires_in": 86400}}
        if url.endswith("/api/v1/ivu10140"):
            return {"dataBody": {"is_nm": "삼성전자", "now_prc": "71000", "b_sq1_askprc": "70900", "s_sq1_askprc": "71000",
                                 "askprc_rcp_tm": "101500", "up_dwn_r_p2": "1.25", "bdy_cls_prc": "70100"}}
        if url.endswith("/api/v1/szqm0771"):
            return {"dataBody": {"bsnss_dt": "20261012", "stk_mkoprt_ccd": "21"}}
        if url.endswith("/api/v1/iva60190"):
            return {"dataBody": {"out2": [{"crncy_cd": "USD", "cls_prc_p4": "1385.5", "bdy_cmpr_r_p2": "0.10"}]}}
        raise AssertionError(url)
    return t


# ------------------------------------------------------------------ KB
def test_kb_requests_follow_official_sample():
    from quant_ai.trading.kb import BASE_URL, KBClient
    log = []
    c = KBClient("APPKEY-1234", "SECRET-9999", transport=fake_kb(log))
    q = c.quote("005930")
    assert q.last == 71000 and q.bid == 70900 and q.ask == 71000 and abs(q.change_pct - 0.0125) < 1e-9
    tok_url, tok_h, tok_b = log[0]
    assert tok_url == BASE_URL + "/oauth2/token" and tok_b["dataBody"] == {"appKey": "APPKEY-1234", "appSecret": "SECRET-9999", "grantType": "client_credentials"}
    assert tok_b["dataHeader"] == {"ipAddr": "", "macAddr": ""}
    url, h, b = log[1]
    assert url.endswith("/api/v1/ivu10140") and h["Authorization"].startswith("bearer ") and h["appKey"] == "APPKEY-1234"
    assert b["dataBody"] == {"excg_clsf": "1", "shrt_cd": "005930"}
    c.quote("000660")
    assert sum(u.endswith("/oauth2/token") for u, _, _ in log) == 1  # 토큰 재사용


def test_kb_refuses_unverified_features_and_foreign_hosts():
    from quant_ai.trading.kb import KBClient, KBNotVerified
    c = KBClient("k", "s", transport=fake_kb([]))
    for fn in (c.balance, c.fills, c.place_order, c.cancel):
        with pytest.raises(KBNotVerified):
            fn()
    with pytest.raises(ValueError):
        KBClient("k", "s", base_url="https://evil.example.com")


def test_kb_check_saves_without_keys(mapp):
    from quant_ai import ops
    from quant_ai.trading.kb import KBClient, check
    c = KBClient("APPKEY-1234", "SECRET-9999", transport=fake_kb([]))
    r = check(mapp, client=c, now=datetime(2026, 10, 12, 1, 16, tzinfo=UTC))
    assert r["ok"] and r["read_only"]
    st = {s["key"]: s for s in r["steps"]}
    assert st["quote"]["ok"] and "60초 전" in st["quote"]["detail"] and st["account"]["ok"] is None
    saved = ops.get_state(mapp.engine, "kb_validation")
    assert saved["ok"] and "SECRET" not in str(saved) and "APPKEY" not in str(saved)
    bad = check(None, env={})
    assert not bad["ok"] and bad["steps"][0]["ok"] is False


def test_live_broker_blocks_kb_orders(mapp):
    from quant_ai.trading.broker import BrokerNotConfigured, LiveBroker
    lb = LiveBroker.__new__(LiveBroker)
    lb.settings = replace(mapp.settings, broker="kb")
    with pytest.raises(BrokerNotConfigured, match="읽기 전용"):
        lb.submit(None, None, None)


# ------------------------------------------------------------------ 실적 대비
def test_reactions_measure_two_day_moves():
    from quant_ai.prep import reactions
    idx = pd.bdate_range("2026-01-01", periods=60)
    close = pd.Series(100.0, index=idx)
    close.iloc[31:] = 110.0  # 30번째 날 발표 → 다음 날 +10%
    bench = pd.DataFrame({"close": np.full(60, 50.0)}, index=idx)
    r = reactions(pd.DataFrame({"close": close}), bench, [str(idx[30].date())])
    assert r and abs(r[0]["move"] - 0.10) < 1e-9 and abs(r[0]["vs_market"] - 0.10) < 1e-9


def test_earnings_prep_without_schedule(mapp):
    from quant_ai.prep import earnings_prep
    sym = next(s for s in mapp._all_bars()[0] if s[:1].isdigit())
    p = earnings_prep(mapp, sym, now=datetime(2026, 10, 12, tzinfo=UTC))
    assert p["next"] is None and "일정이 아직 없어요" in p["lines"][0] and "방향" in p["note"]


# ------------------------------------------------------------------ 투자위원회 · 상태줄
def test_committee_splits_record(mapp):
    from quant_ai.committee import committee, text
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    sym = next(s for s in mapp._all_bars()[0] if s[:1].isdigit())
    assert committee(mapp, sym)["ready"] is False
    bar = mapp._all_bars()[0][sym].index[-1].to_pydatetime()
    payload = {"horizon": 5, "reasons": ["[quant] 20일 모멘텀 상승"], "risks": ["변동성 확대"],
               "contributions": [{"analyst": "quant", "backend": "sklearn", "prob_up": 0.62, "weight": 0.5, "summary": "모멘텀 강함"},
                                 {"analyst": "primary", "backend": "gemini:m", "prob_up": 0.40, "weight": 0.5, "summary": "실적 우려 기사"}],
               "evidence": {"price": {"last_bar": str(bar)[:10]}, "news": [{"title": "x", "at": str(bar)}]}}
    with session_scope(mapp.engine) as s:
        s.add(ConsensusRecord(symbol=sym, as_of=bar, action="HOLD", prob_up=0.51, confidence=40, conflict="high",
                              payload=payload, created_at=bar + timedelta(hours=10)))
    c = committee(mapp, sym, now=bar + timedelta(hours=12))
    assert c["ready"] and c["final"] == "HOLD"
    sides = {a["analyst"]: a["side"] for a in c["analysts"]}
    assert sides == {"quant": "bull", "primary": "bear"}
    assert any("모멘텀" in x for x in c["bull"]) and any("실적 우려" in x for x in c["bear"])
    assert c["pm"]["split"] and c["transparency"]["n_llm"] == 1 and "주문 권한" in c["note"]
    assert c["confidence"]["level"] in ("low", "medium", "high") and text(c)[0].startswith("최종")


def test_trust_strip_items(mapp):
    from quant_ai.truststrip import strip
    t = strip(mapp, now=datetime(2026, 10, 11, 3, tzinfo=UTC))  # 일요일 — 장 마감
    keys = [i["key"] for i in t["items"]]
    assert keys == ["broker", "quotes", "buy", "ledger", "ai"]
    q = next(i for i in t["items"] if i["key"] == "quotes")
    assert "종가" in q["text"]
    mapp.settings = replace(mapp.settings, broker="kb")
    try:
        t2 = strip(mapp, now=datetime(2026, 10, 11, 3, tzinfo=UTC))
        led = next(i for i in t2["items"] if i["key"] == "ledger")
        assert led["text"] == "대조 불가"
    finally:
        mapp.settings = replace(mapp.settings, broker="none")


def test_static_and_routes_v40():
    toss = (STATIC / "toss.js").read_text()
    t2 = (STATIC / "toss2.js").read_text()
    assert "/api/earnings-prep" in toss and "/api/committee" in toss and "/api/trust-strip" in toss
    assert "tKbCard" in t2 and 'runAction("kb_check"' in t2 and "e.move" in t2
    from quant_ai import service
    assert "/api/committee" in service.MEMBER_GET and "/api/trust-strip" not in service.MEMBER_GET

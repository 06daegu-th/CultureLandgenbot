"""v37 '지금 가격 하나로' + '지금 최신으로': 모든 화면 같은 가격 · 시세 시각 키 · 시세 덮어쓰기 · 원버튼 갱신."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from quant_ai import ops

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


def _bars(last=100.0, prev=90.0, day="2026-09-23"):
    idx = pd.DatetimeIndex([pd.Timestamp(day, tz="UTC") - pd.Timedelta(days=1), pd.Timestamp(day, tz="UTC")])
    return pd.DataFrame({"close": [prev, last], "volume": [1, 1]}, index=idx)


# ------------------------------------------------------------------ 1. 한 규칙
def test_resolve_uses_newer_quote_with_right_reference():
    from quant_ai.pricenow import resolve
    b = _bars()
    now = datetime(2026, 9, 24, 3, tzinfo=UTC)  # 9/24 12:00 KST
    # 다음 날 시세 → 기준은 마지막 일봉(9/23) 종가 100
    r = resolve("005930", b, {"price": 110.0, "ts": "2026-09-24T02:30:00+00:00", "chg_pct": 0.99}, now)
    assert r["src"] == "live" and r["price"] == 110 and r["prev_close"] == 100 and abs(r["chg_pct"] - 0.1) < 1e-12
    assert r["label"] == "실시간 11:30"
    # 같은 날 시세 → 기준은 그 전날(9/22) 종가 90 · 'at' 으로 저장된 시세도 읽는다
    r2 = resolve("005930", b, {"price": 99.0, "at": "2026-09-23T05:00:00+00:00"}, now)
    assert r2["src"] == "live" and r2["prev_close"] == 90 and r2["label"].startswith("09.23")
    # 일봉보다 오래된 시세는 무시 (서버가 꺼졌다 켜진 뒤의 묵은 시세가 새 일봉을 덮지 않게)
    r3 = resolve("005930", b, {"price": 50.0, "ts": "2026-09-20T05:00:00+00:00"}, now)
    assert r3["src"] == "close" and r3["price"] == 100 and r3["label"] == "09.23 종가" and abs(r3["chg_pct"] - 100 / 90 + 1) < 1e-12
    assert resolve("005930", None, None)["label"] == "가격 없음"


@pytest.fixture(scope="module")
def mapp(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v37")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0,
                        news_feeds=[], dart_api_key=None, fred_api_key=None, llm_providers={}, anthropic_enabled=False))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def test_every_screen_shows_the_same_price(mapp):
    from quant_ai import center, toss
    from quant_ai.actions import add_watch
    sym = next(s for s in mapp._all_bars()[0] if s[:1].isdigit())
    add_watch(mapp, sym)
    b = mapp._all_bars()[0][sym]
    q_at = (b.index[-1] + timedelta(days=1)).replace(hour=2).isoformat()
    ops.set_state(mapp.engine, "live_quotes", {sym: {"price": float(b["close"].iloc[-1]) * 1.03, "ts": q_at}})
    st = toss.stock(mapp, sym)
    qs = {r["symbol"]: r for r in toss.quotes(mapp, [sym])["rows"]}
    wl = {r["symbol"]: r for r in center.watchlist(mapp)["rows"]}
    px = st["live"]["price"]
    assert qs[sym]["last"] == px == wl[sym]["last"] and wl[sym]["price_src"] == "실시간"
    assert abs(st["live"]["chg_pct"] - 0.03) < 1e-9 and abs(qs[sym]["chg_pct"] - 0.03) < 1e-9 and abs(wl[sym]["chg_pct"] - 0.03) < 1e-9
    assert qs[sym]["price_label"] == wl[sym]["price_label"] == st["live"]["label"]
    ops.set_state(mapp.engine, "live_quotes", {})


def test_trading_cycle_does_not_wipe_screen_quotes(mapp):
    from types import SimpleNamespace
    ops.set_state(mapp.engine, "live_quotes", {"005930": {"price": 1.0, "ts": "2026-10-05T01:00:00+00:00"}, "_at": "x"})
    mapp._record_quotes({"005930": SimpleNamespace(last=1.0)})
    st = ops.get_state(mapp.engine, "live_quotes")
    assert st["005930"]["price"] == 1.0 and st["n"] == 1 and "ts" in st and st["_at"] == "x"
    ops.set_state(mapp.engine, "live_quotes", {})


def test_verdict_sees_free_quote_timestamp(mapp):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    from quant_ai.explain import verdict
    sym = next(s for s in mapp._all_bars()[0] if s[:1].isdigit())
    now = datetime(2026, 10, 7, 2, tzinfo=UTC)  # 수 11:00 KST · 장중 (10/5 는 개천절 대체휴일)
    with session_scope(mapp.engine) as s:
        s.add(ConsensusRecord(symbol=sym, as_of=now - timedelta(hours=2), action="HOLD", prob_up=0.5, confidence=40, conflict="low",
                              payload={"horizon": 5}))
    ops.set_state(mapp.engine, "live_quotes", {sym: {"price": 1.0, "ts": (now - timedelta(minutes=3)).isoformat()}})
    v = verdict(mapp, sym, now=now)
    assert not any("실시간 가격 없음" in x or "가격 지연" in x for x in v["no_trade"])  # v36 까지: 'ts' 를 못 읽어 늘 '실시간 없음'
    ops.set_state(mapp.engine, "live_quotes", {})
    assert any("실시간 가격 없음" in x for x in verdict(mapp, sym, now=now)["no_trade"])


# ------------------------------------------------------------------ 2. 지금 최신으로
def test_refresh_offline_is_fast_honest_and_skips_ai(mapp):
    from quant_ai import refresh
    from quant_ai.actions import add_watch
    add_watch(mapp, next(s for s in mapp._all_bars()[0] if s[:1].isdigit()))
    r = refresh.run(mapp, fetchers={"online": lambda: (False, "URLError: tunnel")})
    st = {x["key"]: x for x in r["steps"]}
    assert r["online"] is False and "인터넷" in r["headline"]
    assert st["quotes"]["status"] == "fail" and "인터넷" in st["quotes"]["why"] and st["quotes"]["sec"] == 0
    assert st["disclosures"]["status"] == "skip" and "DART" in st["disclosures"]["why"]  # 키 없음이 '인터넷 실패'보다 먼저
    assert st["macro"]["status"] == "skip" and st["news"]["status"] == "skip"
    assert st["ai"]["status"] == "skip" and "새로 들어온 자료가 없어" in st["ai"]["why"]
    assert refresh.last(mapp)["headline"] == r["headline"]


def test_refresh_online_with_sources_judges_again(mapp):
    from quant_ai import refresh
    from quant_ai.actions import add_watch
    syms = [s for s in mapp._all_bars()[0] if s[:1].isdigit()][:2]
    for s_ in syms:
        add_watch(mapp, s_)
    fx = {"online": lambda: (True, None),
          "quotes": {"kr": lambda codes: {c: {"price": 100.0, "chg_pct": 0.01, "src": "naver"} for c in codes}, "us": lambda s: {}},
          "indices": lambda url: (_ for _ in ()).throw(OSError("URLError: offline")),
          "community": {"kr": lambda sym: [{"title": "좋다", "ts": "2026-10-05"}], "us": lambda sym: []},
          "flow": lambda code: [{"date": "2026-10-02", "foreign": 100, "inst": -50, "close": 1}],
          "bars_kr": lambda app: {"skip": "테스트"}}
    r = refresh.run(mapp, fetchers=fx)
    st = {x["key"]: x for x in r["steps"]}
    assert st["quotes"]["status"] == "ok" and st["quotes"]["result"]["checked"] >= len(syms)
    assert st["indices"]["status"] == "fail" and "인터넷" in st["indices"]["why"] and st["indices"]["detail"]
    assert st["community"]["status"] == "ok" and st["flow"]["status"] == "ok"
    assert st["ai"]["status"] == "ok" and st["ai"]["result"]["judged"] >= 1  # 새 자료가 있으면 다시 판단
    assert st["signals"]["status"] in ("ok", "skip") and r["ok"] >= 4 and "실패" in r["headline"]
    q = ops.get_state(mapp.engine, "live_quotes")
    assert all(q[s_]["price"] == 100.0 and q[s_].get("ts") for s_ in syms)


def test_friendly_errors():
    from quant_ai.refresh import friendly
    assert "인터넷" in friendly("URLError: <urlopen error Tunnel connection failed: 403 Forbidden>")
    assert "인터넷" in friendly("Yahoo: GET 실패: https://x (URLError)")
    assert "너무 많다" in friendly("HTTP Error 429: Too Many Requests")
    assert "키" in friendly("HTTP Error 401: Unauthorized")


def test_refresh_is_a_button_everywhere_prices_go_stale():
    toss = (STATIC / "toss.js").read_text()
    toss2 = (STATIC / "toss2.js").read_text()
    desk = (STATIC / "desk.js").read_text()
    assert "function startRefresh(" in toss and 'runAction("refresh_all"' in toss and "[data-refresh]" in toss
    assert "${tRefreshBtn()}</div>` : \"\";" in toss and "tRefreshBtn(\"지금 최신으로 받기\"" in toss  # 종목 경고 · 자료 카드
    assert "${tRefreshBtn()}</div>" in toss2  # 자동매매 '오래된 가격'
    assert "asof-refresh" in desk and "data-refresh" in desk  # 상단 '데이터 N거래일 밀림' 옆
    assert "function tPriceNote(" in toss and toss.count("tPriceNote(") >= 4  # 관심종목 · 홈 보유 · 포트폴리오
    assert "canRefresh" in toss and "isMember" in toss  # 회원 화면에는 버튼 없음
    from quant_ai import actions
    assert "refresh_all" in Path(actions.__file__).read_text()

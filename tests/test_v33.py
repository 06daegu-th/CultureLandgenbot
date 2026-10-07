"""v33: '오늘 바로 쓰기' — 시작 안내 ② 목표 계획 · 홈 맨 위 '내 목표' · 적립일 할 일 · 실계좌 '샀어요' 기록 → 진행률."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from quant_ai import ops

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from quant_ai import goal
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    monkeypatch.setattr(goal, "last_close", lambda app, sym: (40_000.0, "2026-10-06"))  # 인터넷 없이
    return QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/t.db", artifacts_dir=tmp_path / "a"))


def test_next_dca_moves_with_month_holidays_and_done():
    from quant_ai.goal import next_dca
    g = {"dca": {"on": True, "day": 25, "amount": 1_000_000, "mode": "live", "target": "etf", "etf": "069500"}, "monthly": 1_000_000}
    n = next_dca(g, date(2026, 10, 7))
    assert n["date"] == "2026-10-26" and n["d_day"] == 19  # 10/25 일요일 → 월요일
    assert next_dca(g, date(2026, 10, 27))["date"] == "2026-10-27"  # 지났는데 안 했으면 오늘
    g["dca"]["last"] = "2026-10-26"
    assert next_dca(g, date(2026, 10, 27))["date"] == "2026-11-25"  # 이번 달 했으면 다음 달
    assert next_dca({"dca": {"on": False}}, date(2026, 10, 7)) is None


def test_start_guide_second_step_is_goal(app):
    from quant_ai import center, goal
    g = center.start_guide(app)
    assert [x["key"] for x in g["steps"]] == ["data", "goal", "watch", "today"] and not g["steps"][1]["done"]
    goal.apply_recommended(app, "m100", "live")
    s = center.start_guide(app)["steps"][1]
    assert s["done"] and "200만원 + 매달 100만원" in s["detail"]
    assert "목표 계획 정하기" in (STATIC / "easy.js").read_text(encoding="utf-8")


def test_home_card_and_today_dca_item(app, monkeypatch):
    from quant_ai import center, goal
    assert goal.home_card(app)["set"] is False and "추천" in goal.home_card(app)["hint"]
    goal.apply_recommended(app, "m100", "live")
    c = goal.home_card(app, now=datetime(2026, 10, 7, 3, tzinfo=UTC))
    assert c["set"] and c["next_dca"]["date"] == "2026-10-26" and c["next_dca"]["what"] == "KODEX 200" and c["years_left"] == 7.0
    # 적립일 당일: 할 일 맨 위 근처에 '오늘은 적립일' (실계좌) + 주문표
    monkeypatch.setattr(center, "ops_status", lambda a, now=None: {"issues": []})
    day = datetime(2026, 10, 26, 1, tzinfo=UTC)
    t = center.today3(app, now=day)
    it = next(x for x in t["items"] if x["kind"] == "dca")
    assert "오늘은 적립일" in it["text"] and "100만원" in it["text"] and it["link"] == "#goal"
    sheet = goal.home_card(app, now=day)["next_dca"]["sheet"]
    assert "KODEX 200" in sheet and "24주" in sheet  # 100만원 ÷ 4만원 (수수료 여유)
    # 이틀 전: 준비 알림
    t2 = center.today3(app, now=datetime(2026, 10, 24, 1, tzinfo=UTC))
    assert any(x["kind"] == "dca" and "준비" in x["text"] for x in t2["items"])


def test_record_buy_tracks_real_holdings_and_stops_reminder(app, monkeypatch):
    from quant_ai import accounts, center, goal
    from quant_ai.web.api import DashboardAPI
    goal.apply_recommended(app, "m100", "live")
    est = goal.buy_estimate(app)
    assert est["deposit"] == 2_000_000 and est["qty"] == 49 and est["name"] == "KODEX 200"
    r = goal.record_buy(app, 49, 40_100, now=datetime(2026, 10, 7, 3, tzinfo=UTC))
    assert r["holding"]["qty"] == 49 and r["cash"] > 0 and r["n_lots"] == 1
    acct = next(a for a in accounts.load(app.engine) if a["id"] == goal.GOAL_ACCOUNT)
    assert acct["type"] == "isa" and acct["holdings"][0]["symbol"] == "069500"
    g = goal.get(app)
    assert g["dca"]["last"] == "2026-10-07" and len(g["lots"]) == 1
    assert goal.next_dca(g, date(2026, 10, 26))["date"] == "2026-11-25"  # 이번 달 적립 끝 → 알림은 다음 달
    # 진행률은 '목표 적립 계좌'(ETF 49주 + 남은 현금)를 따라간다
    total, src = goal.current_assets(app)
    assert total == pytest.approx(49 * 40_100 + r["cash"], rel=0.01) and "목표 적립 계좌" in src
    # 다음 달: 넣은 돈 기본값 = 매달 금액 · 평균단가 갱신
    est2 = goal.buy_estimate(app)
    assert est2["deposit"] == 1_000_000
    r2 = goal.record_buy(app, 24, 41_000)
    assert r2["holding"]["qty"] == 73 and 40_100 < r2["holding"]["avg_price"] < 41_000
    with pytest.raises(ValueError):
        goal.record_buy(app, 0, 41_000)
    # API
    api = DashboardAPI(app)
    out = api.goal_save({"record_buy": {"qty": "1", "price": "41,500", "deposit": ""}})
    assert out["ok"] and out["n_lots"] == 3
    assert api.goal({})["buy_est"]["etf"] == "069500" and api.goal_home()["n_lots"] == 3
    monkeypatch.setattr(center, "ops_status", lambda a, now=None: {"issues": []})
    assert not any(x["kind"] == "dca" for x in center.today3(app, now=datetime(2026, 10, 26, 1, tzinfo=UTC))["items"])


def test_goal_low_band_shows_in_today(app, monkeypatch):
    from quant_ai import center, goal
    goal.apply_recommended(app, "m100", "paper")
    g = goal.get(app)
    ops.set_state(app.engine, goal.KEY, g | {"start": "2025-10-01"})  # 1년 전에 시작했는데 자산은 200만원 그대로
    monkeypatch.setattr(goal, "current_assets", lambda a: (2_000_000.0, "테스트"))
    monkeypatch.setattr(center, "ops_status", lambda a, now=None: {"issues": []})
    t = center.today3(app, now=datetime(2026, 10, 7, 3, tzinfo=UTC), n=10)
    assert any(x["kind"] == "goal_low" for x in t["items"])


def test_home_screen_wiring():
    t = (STATIC / "toss.js").read_text(encoding="utf-8")
    t2 = (STATIC / "toss2.js").read_text(encoding="utf-8")
    assert '<div id="th-goal"></div>' in t and "tGoalHome(root.querySelector(\"#th-goal\"))" in t
    assert t.index('id="th-goal"') < t.index('tSec("지수"')  # 목표가 지수보다 위
    assert "모의투자 장부" in t and "async function tGoalHome" in t2 and "function goalBuyForm" in t2 and "record_buy" in t2
    assert "16년 검증에서 작은 계좌는 종목 고르기보다 지수 ETF 적립이 나았어요" in t2
    srv = (ROOT / "src/quant_ai/web/server.py").read_text(encoding="utf-8")
    assert '"/api/goal-home"' in srv

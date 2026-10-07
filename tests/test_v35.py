"""v35 '목표를 끝까지': 체험 계산기 · 연속 적립 · 월간 리포트 · 배지 · 급락 안내 · 친구 초대 · 함께 모으기."""

import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import UTC, date, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops, tenancy

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"
PW = "correct-horse-battery-9"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from quant_ai import goal, members
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    monkeypatch.setattr(goal, "last_close", lambda app, sym: (40_000.0, "2026-10-06"))
    monkeypatch.delenv("QUANT_DATA_KEY", raising=False)
    for d in (members._IP, members._SIGNUPS, members._SESS_CACHE):
        d.clear()
    return QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/t.db", artifacts_dir=tmp_path / "a"))


# ------------------------------------------------------------------ 1. 체험 계산기
def test_explore_is_honest_about_10x():
    from quant_ai.goal import explore
    lump = explore(1_000_000, 0, 10_000_000, 5)
    assert lump["prob"] < 0.02 and lump["level"] == "bad" and lump["need_cagr"] > 0.5
    assert any("몰빵" in h for h in lump["honest"]) and any("30년" in h for h in lump["honest"])
    assert lump["alternatives"] and all(a["monthly"] > 0 for a in lump["alternatives"])  # 확률을 올리는 길 = 적립
    dca = explore(1_000_000, 150_000, 10_000_000, 5)
    assert dca["prob"] > 0.6 and dca["level"] == "great"
    t = dca["at_target"]
    assert t["paid"] == 10_000_000 and t["p10"] < t["p50"] < t["p90"] and any("내가 넣는 돈" in h for h in dca["honest"])
    assert len(dca["path"]) >= 10 and dca["path"][4]["p_reach"] == dca["prob"]
    with pytest.raises(ValueError):
        explore(0, 0, 10_000_000, 5)
    with pytest.raises(ValueError):
        explore(1, 1, 1e13, 5)


@pytest.fixture()
def planned(app):
    from quant_ai import goal
    goal.apply_recommended(app, "m100", "live")
    return app


def test_save_preserves_lots_and_marks_done(planned):
    """v35 버그 수정: 계획을 고쳐 저장하면 '샀어요' 기록이 지워지던 것."""
    from quant_ai import goal
    app = planned
    goal.record_buy(app, 49, 40_000, now=datetime(2026, 10, 7, 3, tzinfo=UTC))
    goal.save(app, {"principal": 2_000_000, "monthly": 1_200_000, "goal": 100_000_000, "target_years": 7, "strategy": "kospi"})
    g = goal.get(app)
    assert len(g["lots"]) == 1 and "2026-10" in g["done_months"] and g["monthly"] == 1_200_000


# ------------------------------------------------------------------ 2. 꾸준함
def test_streak_pending_missed_and_calendar():
    from quant_ai.habit import streak
    g = {"start": "2026-05-03", "dca": {"day": 25}, "done_months": ["2026-06", "2026-07", "2026-08", "2026-09"]}
    s = streak(g, date(2026, 10, 7))
    assert s["current"] == 4 and s["status"] == "pending" and s["best"] == 4 and "2026-05" in s["missed"]  # 10월은 아직 기다리는 중
    s2 = streak(g, date(2026, 10, 30))
    assert s2["status"] == "missed" and s2["current"] == 4  # 지난 달까지 4개월 (이번 달은 아직 안 함)
    g["done_months"].append("2026-10")
    s3 = streak(g, date(2026, 10, 30))
    assert s3["current"] == 5 and s3["this_month"] and len(s3["calendar"]) == 12 and s3["calendar"][-1]["done"]


def test_snapshot_badges_report_and_daily_push(planned):
    from quant_ai import alerts, goal, habit
    app = planned
    ops.set_state(app.engine, goal.KEY, goal.get(app) | {"start": "2026-09-01"})  # 9월에 시작한 계획
    goal.record_buy(app, 49, 40_000, deposit=2_000_000, now=datetime(2026, 9, 26, 3, tzinfo=UTC))
    habit.snapshot(app, datetime(2026, 9, 30, 3, tzinfo=UTC))
    goal.record_buy(app, 24, 41_000, deposit=1_000_000, now=datetime(2026, 10, 26, 3, tzinfo=UTC))
    habit.daily(app, datetime(2026, 10, 30, 3, tzinfo=UTC))
    g = goal.get(app)
    assert "first" in g["badges"] and "streak3" not in g["badges"]
    assert set(g["history"]) == {"2026-09", "2026-10"} and g["history"]["2026-10"]["paid"] == 3_000_000
    rep = habit.monthly_report(app, "2026-10", datetime(2026, 11, 2, 3, tzinfo=UTC))
    assert rep["added"] == 1_000_000 and rep["end_total"] - rep["start_total"] == rep["added"] + rep["market"]
    assert rep["streak"] == 2 and "넣은 돈" in rep["text"]
    r2 = habit.daily(app, datetime(2026, 11, 2, 3, tzinfo=UTC))  # 11월 2일: 10월 리포트 알림 한 번
    assert r2["report"] == "2026-10"
    assert habit.daily(app, datetime(2026, 11, 3, 3, tzinfo=UTC))["report"] is None  # 두 번은 안 보냄
    kinds = [(i["kind"], i["title"]) for i in alerts.recent(app.engine)["items"]]
    assert ("habit", "배지: 첫 적립") in kinds and ("habit", "10월 목표 리포트") in kinds
    ov = habit.overview(app, datetime(2026, 11, 3, 3, tzinfo=UTC))
    assert ov["streak"]["current"] >= 2 and any(b["earned"] for b in ov["badges"])


def _fake_index(app, monkeypatch, closes, last_day):
    idx = pd.bdate_range(end=last_day, periods=len(closes))
    ser = pd.DataFrame({"close": closes}, index=idx)
    monkeypatch.setattr(app, "_all_bars", lambda: ({}, {"KR": ser}))


def test_crash_watch_calm_card_and_one_push_per_episode(planned, monkeypatch):
    from quant_ai import alerts, habit
    app = planned
    rng = np.random.default_rng(1)
    base = list(np.cumprod(1 + rng.normal(0.0004, 0.01, 2500)) * 100)
    base += [base[-1] * (1 - 0.006 * k) for k in range(1, 20)]  # 최근 고점 대비 −10% 넘게
    _fake_index(app, monkeypatch, base, "2026-10-06")
    now = datetime(2026, 10, 7, 3, tzinfo=UTC)
    card = habit.calm_card(app, now)
    assert card and "계획은 그대로" in card["title"] and card["history"]["n"] >= 1 and "매매 권유가 아니" in card["note"]
    r = habit.crash_job(app, now)
    assert r["sent"] == 1
    assert habit.crash_job(app, now)["sent"] == 0  # 같은 하락 구간·단계는 한 번만
    assert any(i["kind"] == "calm" for i in alerts.recent(app.engine)["items"])
    _fake_index(app, monkeypatch, base, "2026-09-01")  # 시세가 오래됐으면 경보 안 함
    assert habit.calm_card(app, now) is None and habit.crash_job(app, now)["sent"] == 0


# ------------------------------------------------------------------ 3. 함께 (여러 사용자)
def _mk(app, email):
    from quant_ai import members
    return members.create(app, email, PW, consent={"terms": members.TERMS_VERSION}, verified=True) | {"role": "member"}


def test_club_shows_progress_never_amounts(app, monkeypatch):
    monkeypatch.setenv("QUANT_SERVICE_MODE", "multi")
    from quant_ai import goal, habit, together
    a, b, c = _mk(app, "a@x.com"), _mk(app, "b@x.com"), _mk(app, "c@x.com")
    with tenancy.as_user(a):
        goal.apply_recommended(app, "m100", "live")
        goal.record_buy(app, 49, 40_000, deposit=2_000_000)
        habit.snapshot(app)
        club = together.create_club(app, "첫 1억 모임", "에이")
        code = club["code"]
    with tenancy.as_user(b):
        with pytest.raises(ValueError, match="참여 코드"):
            together.join_club(app, "ZZZZZZZZ")
        v = together.join_club(app, code.lower(), "비")
        assert v["n"] == 2
        txt = json.dumps(v, ensure_ascii=False)
        assert "2000000" not in txt and '"total"' not in txt and '"goal"' not in txt and '"paid"' not in txt  # 금액은 절대 안 보임
        row_a = next(r for r in v["rows"] if r["nick"] == "에이")
        assert row_a["has_goal"] and row_a["pct"] is not None and row_a["this_month"]
    with tenancy.as_user(c), pytest.raises(ValueError, match="회원이 아니"):
        together.view(app, club["id"])  # 코드 없이 남의 모임은 못 본다
    with tenancy.as_user(a):
        together.set_me(app, club["id"], hide_pct=True)
    with tenancy.as_user(b):
        assert next(r for r in together.view(app, club["id"])["rows"] if r["nick"] == "에이")["pct"] is None
    with tenancy.as_user(a):
        together.leave_club(app, club["id"])  # 모임장이 나가면 다음 사람이 모임장
    with tenancy.as_user(b):
        v = together.view(app, club["id"])
        assert v["is_owner"] and v["n"] == 1
    # 탈퇴하면 모임에서도 빠진다
    from quant_ai import members
    members.delete_account(app, b["id"], PW, "b@x.com")
    assert ops.get_state(app.engine, f"club:{club['id']}") == {}


def test_referral_signup_reward_and_policy(app, monkeypatch):
    monkeypatch.setenv("QUANT_SERVICE_MODE", "multi")
    from quant_ai import members, service, together
    service.set_policy(app, {"signup": "invite", "ref_reward_days": 30})
    a = _mk(app, "inviter@x.com")
    with tenancy.as_user(a):
        r = together.referral(app, "http://h", create=True)
    tok = r["link"].split("#signup/")[1]
    new = members.signup(app, {"email": "friend@x.com", "password": PW, "invite": tok,
                               "consent": {"terms": 1, "privacy": 1, "risk": 1, "age14": 1}}, "8.8.8.8", service.policy(app))
    rows = {u["email"]: u for u in members.list_users(app)["rows"]}
    assert rows["inviter@x.com"]["plan"] == "pro" and rows["friend@x.com"]["plan"] == "pro" and new["role"] == "member"
    with tenancy.as_user(a):
        again = together.referral(app, "http://h")
        assert again["joined"] == 1 and again["granted_days"] == 30 and again["link"] == r["link"]  # 같은 링크 그대로
    service.set_policy(app, {"member_invites": False})
    with tenancy.as_user(a):
        assert together.referral(app, "http://h")["enabled"] is False
    with pytest.raises(members.AuthError, match="회원 초대"):
        members.signup(app, {"email": "f2@x.com", "password": PW, "invite": tok,
                             "consent": {"terms": 1, "privacy": 1, "risk": 1, "age14": 1}}, "8.8.8.9", service.policy(app))


# ------------------------------------------------------------------ 4. 서버 · 화면
class Client:
    def __init__(self, base):
        self.base, self.cookie = base, None

    def req(self, method, path, body=None):
        h = {"Host": "127.0.0.1"}
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        if self.cookie:
            h["Cookie"] = self.cookie
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=h, method=method), timeout=60) as r:
                if r.headers.get("Set-Cookie"):
                    self.cookie = r.headers["Set-Cookie"].split(";")[0]
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def test_server_explore_public_and_member_flows(app, monkeypatch):
    monkeypatch.setenv("QUANT_SERVICE_MODE", "multi")
    from quant_ai import members, service
    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    members.create(app, "own@x.com", PW, role="owner", verified=True, consent={"terms": members.TERMS_VERSION})
    service.set_policy(app, {"signup": "open"})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), None, {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        anon = Client(base)
        st, r = anon.req("GET", "/api/explore?principal=1000000&monthly=0&goal=10000000&years=5")
        assert st == 200 and r["level"] == "bad"  # 로그인 전에도 계산 가능
        assert anon.req("GET", "/api/explore?principal=abc")[0] == 400
        assert anon.req("GET", "/api/habit")[0] == 401
        m = Client(base)
        assert m.req("POST", "/api/signup", {"email": "m@x.com", "password": PW, "consent": {"terms": 1, "privacy": 1, "risk": 1, "age14": 1}})[0] == 200
        assert m.req("GET", "/api/habit")[1] == {"set": False}
        assert m.req("POST", "/api/goal", {"principal": 1_000_000, "monthly": 150_000, "goal": 10_000_000, "target_years": 5,
                                           "strategy": "kospi", "restart": True, "dca": {"on": True, "mode": "live", "target": "etf"}})[0] == 200
        h = m.req("GET", "/api/habit")[1]
        assert h["set"] and "streak" in h and len(h["badges"]) >= 8
        assert not any(b["earned"] for b in h["badges"])  # 아무것도 안 샀으면 진행률 0 · 배지 없음 (가상 장부를 자산으로 세지 않음)
        assert m.req("GET", "/api/goal-home")[1]["progress"]["total"] == 0
        gh = m.req("GET", "/api/goal-home")[1]
        assert gh["set"] and gh["streak"]["status"] in ("pending", "missed", "done")
        st, t = m.req("POST", "/api/together", {"action": "create", "name": "같이 모으기"})
        assert st == 200 and len(t["club"]["code"]) == 8
        ov = m.req("GET", "/api/together")[1]
        assert ov["available"] and ov["clubs"][0]["name"] == "같이 모으기"
        assert m.req("GET", f"/api/club?id={t['club']['id']}")[0] == 200
        st, link = m.req("POST", "/api/together", {"action": "invite_link"})
        assert st == 200 and "#signup/" in link["referral"]["link"]
    finally:
        httpd.shutdown()


def test_frontend_wiring():
    js = "".join((STATIC / f).read_text(encoding="utf-8") for f in ("member.js", "toss2.js", "toss.js", "goal.js", "app.js", "habit.js"))
    for s in ("TV.explore", "TV.together", "exploreView", "/api/explore", "habitSection", "/api/habit", "/api/together", "#club/",
              "calmCard", "같이 모으기", "체험"):
        assert s in js, s
    assert '<script src="/habit.js"></script>' in (STATIC / "index.html").read_text(encoding="utf-8")
    assert "/habit.js" in (STATIC / "sw.js").read_text(encoding="utf-8")

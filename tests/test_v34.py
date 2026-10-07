"""v34 여러 사용자 서비스: 계정 · 사람별 데이터 분리 · 권한 · 투자 정보 범위 · 요금제 한도 · 개인정보 (내보내기·탈퇴) · 보안."""

import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from quant_ai import ops, tenancy

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"
PW = "correct-horse-battery-9"
CONSENT = {"terms": True, "privacy": True, "risk": True, "age14": True}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from quant_ai import goal, members
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    monkeypatch.setattr(goal, "last_close", lambda app, sym: (40_000.0, "2026-10-06"))
    monkeypatch.delenv("QUANT_DATA_KEY", raising=False)
    monkeypatch.delenv("QUANT_SMTP_HOST", raising=False)
    members._IP.clear()
    members._SIGNUPS.clear()
    members._SESS_CACHE.clear()
    return QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/m.db", artifacts_dir=tmp_path / "a"))


def _member(app, email, plan="free"):
    from quant_ai import members
    u = members.create(app, email, PW, consent={"terms": members.TERMS_VERSION}, plan=plan, verified=True)
    return u


# ------------------------------------------------------------------ 1. 사람별 데이터 칸
def test_personal_state_is_separated_and_owner_keeps_legacy(app):
    from quant_ai import goal
    goal.apply_recommended(app, "m100", "live")  # 1인 모드(문맥 없음) = 소유자 데이터
    a, b = _member(app, "a@x.com"), _member(app, "b@x.com")
    with tenancy.as_user(a):
        assert goal.get(app) == {}  # 회원 A 는 빈 칸에서 시작
        goal.apply_recommended(app, "m100", "paper", monthly=500_000)
        assert goal.get(app)["monthly"] == 500_000
    with tenancy.as_user(b):
        assert goal.get(app) == {}  # B 는 A 의 계획을 못 본다
    assert goal.get(app)["monthly"] == 1_000_000  # 소유자(1인 모드) 데이터는 그대로
    with tenancy.as_user({"id": 99, "role": "owner", "owner": True}):
        assert goal.get(app)["monthly"] == 1_000_000  # 소유자 계정은 예전 칸을 그대로 쓴다
    # 공용 키(시장 자료)는 나뉘지 않는다
    ops.set_state(app.engine, "event_calendar", {"x": 1})
    with tenancy.as_user(a):
        assert ops.get_state(app.engine, "event_calendar") == {"x": 1}
    # 장부 이름: 회원이 paper/live 를 달라고 해도 자기 모의 장부
    with tenancy.as_user(a):
        assert tenancy.personal_book("paper") == tenancy.personal_book("live") == f"manual@{tenancy.b36(a['id'])}"
        assert tenancy.personal_book("us-paper").startswith("us-manual@") and tenancy.personal_book(f"manual@{tenancy.b36(b['id'])}") != f"manual@{tenancy.b36(b['id'])}"
        assert app.load_portfolio("live").cash == app.settings.initial_cash  # 운영자 실계좌 장부가 아니라 새 모의 장부
    assert tenancy.personal_book("paper") == "paper"


def test_personal_data_encrypted_at_rest(app, monkeypatch):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import SystemState
    monkeypatch.setenv("QUANT_DATA_KEY", "test-only-secret-value")
    a = _member(app, "enc@x.com")
    with tenancy.as_user(a):
        ops.set_state(app.engine, "accounts", {"accounts": [{"id": "isa", "name": "내 ISA", "cash": 1234567}]})
        assert ops.get_state(app.engine, "accounts")["accounts"][0]["cash"] == 1234567
    with session_scope(app.engine) as s:
        raw = s.get(SystemState, f"u:{tenancy.b36(a['id'])}:accounts").value
    assert set(raw) == {"_enc"} and "1234567" not in json.dumps(raw)  # DB 에는 암호문만
    monkeypatch.delenv("QUANT_DATA_KEY")
    with tenancy.as_user(a), pytest.raises(RuntimeError, match="QUANT_DATA_KEY"):
        ops.get_state(app.engine, "accounts")  # 키 없이 조용히 빈 값을 주지 않는다


def test_alert_rules_journal_and_alerts_belong_to_owner(app):
    from quant_ai import alerts
    from quant_ai.data.db import session_scope
    from quant_ai.review.my_journal import add, compare
    a, b = _member(app, "ra@x.com"), _member(app, "rb@x.com")
    with tenancy.as_user(a):
        rid = alerts.add_rule(app.engine, "005930", "above", 80_000)["id"]
        with session_scope(app.engine) as s:
            add(s, "005930", "BUY", 4, 5, "테스트")
    with tenancy.as_user(b):
        assert alerts.active_rules(app.engine) == []
        with pytest.raises(ValueError, match="규칙 없음"):
            alerts.update_rule(app.engine, rid, delete=True)  # 남의 규칙은 지울 수 없다
        with session_scope(app.engine) as s:
            assert compare(s)["n"] == 0
    assert alerts.active_rules(app.engine) == []  # 운영자(1인 모드)에게도 회원 규칙은 안 보인다
    assert len(alerts.active_rules(app.engine, all_owners=True)) == 1
    # 규칙이 울리면 그 사람에게만
    n = alerts.evaluate_rules(app, {"005930": {"price": 81_000, "chg_pct": 0.02}}, now=datetime(2026, 10, 7, 2, tzinfo=UTC), names={})
    assert n == 1
    with tenancy.as_user(a):
        assert any(i["kind"] == "rule" for i in alerts.recent(app.engine)["items"])
    with tenancy.as_user(b):
        assert not any(i["kind"] == "rule" for i in alerts.recent(app.engine)["items"])
    assert not any(i["kind"] == "rule" for i in alerts.recent(app.engine)["items"])
    # 운영 알림(긴급 정지 등)은 회원에게 안 보이고 · 공용 알림(공시·뉴스)은 보인다
    alerts.push(app.engine, "guardian", "긴급 정지", level="bad", route=False)
    alerts.push(app.engine, "disclosure", "삼성전자 공시", route=False)
    with tenancy.as_user(a):
        kinds = {i["kind"] for i in alerts.recent(app.engine)["items"]}
    assert "guardian" not in kinds and "disclosure" in kinds


# ------------------------------------------------------------------ 2. 계정
def test_password_rules_and_login_lockout(app):
    from quant_ai import members
    assert members.password_problem("short") and members.password_problem("1234567890")
    assert members.password_problem("password1") and members.password_problem("alice-pass-123", "alice@x.com")
    assert members.password_problem(PW, "z@x.com") is None
    _member(app, "lock@x.com")
    sid, u = members.login(app, "LOCK@x.com ", PW, None, "1.1.1.1")
    assert members.session_user(app, sid)["email"] == "lock@x.com"
    for _ in range(5):
        with pytest.raises(members.AuthError):
            members.login(app, "lock@x.com", "wrong-password-1", None, "2.2.2.2")
    with pytest.raises(members.AuthError, match="잠겼"):
        members.login(app, "lock@x.com", PW, None, "3.3.3.3")  # 맞는 비밀번호라도 15분 잠금
    with pytest.raises(members.AuthError, match="이메일 또는 비밀번호"):
        members.login(app, "nobody@x.com", PW, None, "4.4.4.4")  # 없는 계정도 같은 문장
    members.logout(app, sid)
    assert members.session_user(app, sid) is None


def test_signup_policy_consent_invite_and_reset(app):
    from quant_ai import mailer, members
    pol = {"signup": "invite"}
    with pytest.raises(members.AuthError, match="초대"):
        members.signup(app, {"email": "n@x.com", "password": PW, "consent": CONSENT}, "9.9.9.1", pol)
    inv = members.invite(app, None, None, "pro", 7, 3)
    with pytest.raises(members.AuthError, match="필수 동의"):
        members.signup(app, {"email": "n@x.com", "password": PW, "consent": {"terms": True}, "invite": inv["token"]}, "9.9.9.1", pol)
    u = members.signup(app, {"email": "n@x.com", "password": PW, "consent": CONSENT, "invite": inv["token"], "name": "새 회원"}, "9.9.9.1", pol)
    assert u["role"] == "member" and u["plan"] == "pro" and u["terms_ok"]
    assert members.invite_info(app, inv["token"])["ok"]  # 여러 명용 링크는 3명까지
    with pytest.raises(members.AuthError, match="이미 가입"):
        members.signup(app, {"email": "N@x.com", "password": PW, "consent": CONSENT, "invite": inv["token"]}, "9.9.9.2", pol)
    with pytest.raises(members.AuthError, match="지금은 새로 가입"):
        members.signup(app, {"email": "c@x.com", "password": PW, "consent": CONSENT}, "9.9.9.3", {"signup": "closed"})
    # 비밀번호 찾기: 있든 없든 같은 응답 · 메일(편지함)의 링크로 재설정 → 모든 기기 로그아웃
    sid, _ = members.login(app, "n@x.com", PW, None, "9.9.9.4")
    assert members.request_reset(app, "ghost@x.com", "http://h")["message"] == members.request_reset(app, "n@x.com", "http://h")["message"]
    link = next(i for i in mailer.outbox(app)["items"] if i["kind"] == "reset")["body"]
    tok = link.split("#reset/")[1].split()[0]
    with pytest.raises(members.AuthError):
        members.reset_password(app, tok, "short")
    assert members.reset_password(app, tok, "brand-new-pass-77")["ok"]
    assert members.session_user(app, sid) is None
    with pytest.raises(members.AuthError, match="만료"):
        members.reset_password(app, tok, "another-pass-88")  # 링크는 한 번만
    members.login(app, "n@x.com", "brand-new-pass-77", None, "9.9.9.5")


def test_totp_enable_login_and_no_reuse(app):
    from quant_ai import members
    from quant_ai.auth import totp
    u = _member(app, "otp@x.com")
    sec = members.totp_begin(app, u["id"])["secret"]
    members.totp_enable(app, u["id"], totp(sec))
    with pytest.raises(members.AuthError, match="2단계"):
        members.login(app, "otp@x.com", PW, None, "5.5.5.5")
    import time
    code = totp(sec, time.time() + 30)  # 다음 칸 코드 (등록 때 쓴 칸 이후)
    members.login(app, "otp@x.com", PW, code, "5.5.5.5")
    with pytest.raises(members.AuthError, match="이미 쓴"):
        members.login(app, "otp@x.com", PW, code, "5.5.5.6")  # 같은 코드 재사용 거부


def test_export_and_delete_account_purge_everything(app):
    from quant_ai import alerts, goal, members
    u = _member(app, "bye@x.com")
    with tenancy.as_user(u):
        goal.apply_recommended(app, "m100", "paper")
        alerts.add_rule(app.engine, "005930", "below", 50_000)
        app.add_cashflow("paper", 1_000_000)
        assert app.cashflows("paper")  # 내 모의 장부 입금
    other = _member(app, "stay@x.com")
    with tenancy.as_user(other):
        goal.apply_recommended(app, "m100", "live")
    ex = members.export(app, u["id"])
    assert ex["account"]["email"] == "bye@x.com" and ex["data"]["goal_plan"]["monthly"] == 1_000_000 and len(ex["alert_rules"]) == 1
    assert "pw_hash" not in json.dumps(ex) and "totp" not in json.dumps(ex["account"])
    with pytest.raises(members.AuthError, match="이메일"):
        members.delete_account(app, u["id"], PW, "wrong@x.com")
    out = members.delete_account(app, u["id"], PW, "bye@x.com")
    assert out["ok"] and out["deleted"] >= 4
    with tenancy.as_user(u):
        assert goal.get(app) == {} and alerts.active_rules(app.engine) == [] and app.cashflows("paper") == []
    assert ops.user_states(app.engine, tenancy.user_prefix(u["id"])) == {}
    with tenancy.as_user(other):
        assert goal.get(app)["dca"]["mode"] == "live"  # 다른 사람 데이터는 그대로
    with pytest.raises(members.AuthError):
        members.login(app, "bye@x.com", PW, None, "6.6.6.6")


def test_quota_and_count_limits(app):
    from quant_ai import service
    u = _member(app, "q@x.com")
    with tenancy.as_user(u | {"role": "member"}):
        for _ in range(service.PLANS["free"]["chat"]):
            service.take(app, "chat")
        with pytest.raises(service.QuotaError, match="AI 채팅"):
            service.take(app, "chat")
        assert service.usage(app)["today"]["chat"] == 5
        with pytest.raises(service.QuotaError, match="관심종목"):
            service.check_count("watch", 30)
        service.check_count("watch", 29)
    with tenancy.as_user({"id": 1, "role": "admin"}):
        for _ in range(10):
            service.take(app, "chat")  # 운영자는 무제한


# ------------------------------------------------------------------ 3. 웹 서버 (여러 사용자 모드)
class Client:
    def __init__(self, base):
        self.base, self.cookie = base, None

    def req(self, method, path, body=None, headers=None):
        h = {"Host": "127.0.0.1", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        if self.cookie:
            h["Cookie"] = self.cookie
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                ck = resp.headers.get("Set-Cookie")
                if ck:
                    self.cookie = ck.split(";")[0]
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


@pytest.fixture()
def server(app, monkeypatch):
    monkeypatch.setenv("QUANT_SERVICE_MODE", "multi")
    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), "ops-token-xyz", {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", app
    httpd.shutdown()


def test_server_signup_login_isolation_and_permissions(server):
    from quant_ai import members, service
    base, app = server
    anon = Client(base)
    st, info = anon.req("GET", "/api/auth")
    assert st == 200 and info["multi"] and info["needs_owner"] and info["user"] is None
    assert anon.req("GET", "/api/goal")[0] == 401  # 로그인 전엔 아무것도
    members.create(app, "owner@x.com", PW, role="owner", verified=True, consent={"terms": members.TERMS_VERSION})
    service.set_policy(app, {"signup": "open"})
    a, b = Client(base), Client(base)
    st, r = a.req("POST", "/api/signup", {"email": "a@x.com", "password": PW, "consent": CONSENT, "name": "에이"})
    assert st == 200 and r["user"]["email"] == "a@x.com" and a.cookie and "HttpOnly" not in a.cookie
    assert b.req("POST", "/api/signup", {"email": "b@x.com", "password": PW, "consent": CONSENT})[0] == 200
    # A 의 목표·관심종목은 A 에게만 (서버 캐시까지 포함)
    assert a.req("POST", "/api/goal", {"apply": "m100", "mode": "live"})[0] == 200
    assert a.req("GET", "/api/goal-home")[1]["set"] is True
    assert b.req("GET", "/api/goal-home")[1]["set"] is False
    assert a.req("POST", "/api/star", {"symbol": "005930", "on": True})[0] == 200
    assert "005930" in a.req("GET", "/api/star")[1]["starred"] and b.req("GET", "/api/star")[1]["starred"] == []
    # 회원은 운영 기능 못 씀 · 운영자 토큰은 됨
    for path in ("/api/keys", "/api/server", "/api/governance", "/api/orders", "/api/autopilot", "/api/admin/users", "/api/db"):
        st, r = a.req("GET", path)
        assert st == 403, path
    assert a.req("POST", "/api/killswitch", {"on": True})[0] == 403
    assert a.req("POST", "/api/action", {"name": "collect"})[0] == 403
    assert Client(base).req("GET", "/api/keys", headers={"X-Token": "ops-token-xyz"})[0] == 200
    # 투자 정보 범위: 기본 none → AI 매수·매도 판단 막힘 · 홈 응답에서도 빠짐
    st, r = a.req("GET", "/api/signals2")
    assert st == 403 and r["code"] == "advice_off"
    home = a.req("GET", "/api/t/home")[1]
    assert "picks" not in home and "trust" not in home and "holdings" in home
    h5 = a.req("GET", "/api/home5")[1]
    assert "system" not in h5 and "ai" not in h5 and "data" not in h5
    # 운영자가 info 로 바꾸면 열린다
    owner = Client(base)
    assert owner.req("POST", "/api/login", {"email": "owner@x.com", "password": PW})[0] == 200
    st, ov = owner.req("GET", "/api/admin/overview")
    assert st == 200 and ov["stats"]["total"] == 3
    assert owner.req("POST", "/api/admin/policy", {"advice": "info"})[0] == 200
    assert a.req("GET", "/api/signals2")[0] != 403
    # 운영자 콘솔: 회원 정지 → 그 회원 세션 즉시 끊김
    st, users = owner.req("GET", "/api/admin/users")
    bid = next(u["id"] for u in users["rows"] if u["email"] == "b@x.com")
    assert owner.req("POST", "/api/admin/user", {"id": bid, "status": "disabled"})[0] == 200
    assert b.req("GET", "/api/goal")[0] == 401
    # 회원은 다른 회원을 못 바꾼다
    assert a.req("POST", "/api/admin/user", {"id": bid, "status": "active"})[0] == 403
    # 내 정보 · 내보내기 · 로그아웃
    me = a.req("GET", "/api/me")[1]
    assert me["user"]["email"] == "a@x.com" and me["usage"]["limits"]["chat"] == 5
    assert a.req("GET", "/api/me/export")[1]["data"]["goal_plan"]["dca"]["mode"] == "live"
    assert a.req("POST", "/api/logout", {})[0] == 200
    a.cookie = None
    assert a.req("GET", "/api/goal")[0] == 401


def test_server_quota_terms_and_csrf(server):
    from quant_ai import members
    base, app = server
    members.create(app, "t@x.com", PW, verified=True, consent={"terms": "2000-01"})  # 예전 약관
    c = Client(base)
    assert c.req("POST", "/api/login", {"email": "t@x.com", "password": PW})[0] == 200
    st, r = c.req("GET", "/api/goal")
    assert st == 428 and r["code"] == "terms"  # 바뀐 약관 재동의 전엔 막힘
    assert c.req("POST", "/api/me", {"accept_terms": True})[0] == 200
    members._SESS_CACHE.clear()
    assert c.req("GET", "/api/goal")[0] == 200
    # 하루 한도: 투자 일지 30번
    from quant_ai import service
    with tenancy.as_user({"id": next(u["id"] for u in members.list_users(app)["rows"]), "role": "member", "plan": "free"}):
        for _ in range(service.PLANS["free"]["journal"]):
            service.take(app, "journal")
    st, r = c.req("POST", "/api/myjournal", {"symbol": "005930", "action": "BUY"})
    assert st == 429 and r["code"] == "quota"
    # 다른 사이트에서 온 요청(CSRF) · JSON 아닌 요청 거부
    assert c.req("POST", "/api/star", {"symbol": "005930"}, headers={"Origin": "https://evil.example"})[0] == 403
    r = urllib.request.Request(base + "/api/star", data=b"symbol=1", headers={"Host": "127.0.0.1", "Cookie": c.cookie,
                                                                              "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(r, timeout=10)
    assert e.value.code == 415


def test_personal_mode_unchanged(app, monkeypatch):
    """QUANT_SERVICE_MODE 가 없으면 예전 그대로 (로그인 없이 이 PC 에서 전부)."""
    monkeypatch.delenv("QUANT_SERVICE_MODE", raising=False)
    from quant_ai import service
    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    assert not service.multi()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), None, {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = Client(f"http://127.0.0.1:{httpd.server_address[1]}")
        assert c.req("GET", "/api/keys")[0] == 200 and c.req("GET", "/api/auth")[1].get("multi") is None
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ 4. 스키마 · 운영
def test_alembic_migration_matches_models(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect
    db = tmp_path / "mig.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    insp = inspect(create_engine(f"sqlite:///{db}"))
    assert {"users", "user_sessions", "user_tokens"} <= set(insp.get_table_names())
    from quant_ai.data.models import Base
    for t in ("users", "user_sessions", "user_tokens", "alerts", "alert_rules", "user_journal"):
        have = {c["name"] for c in insp.get_columns(t)}
        want = {c.name for c in Base.metadata.tables[t].columns}
        assert want <= have, (t, want - have)


def test_cli_owner_and_member_jobs(app, monkeypatch, capsys):
    import argparse

    from quant_ai import members
    from quant_ai.cli import cmd_users
    monkeypatch.setenv("QUANT_OWNER_PASSWORD", PW)
    ns = dict(db=app.settings.database_url, email="boss@x.com", name="운영자", role=None, plan=None, status=None, days=None, uses=1,
              unlock=False, yes=True)
    cmd_users(argparse.Namespace(action="owner", **ns))
    assert "소유자" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="하나만"):
        cmd_users(argparse.Namespace(action="owner", **(ns | {"email": "boss2@x.com"})))
    cmd_users(argparse.Namespace(action="invite", **(ns | {"email": None, "uses": 5})))
    assert "#signup/" in capsys.readouterr().out
    # 회원마다 적립일 작업이 그 사람 문맥에서
    from quant_ai import goal
    m = _member(app, "dca@x.com")
    with tenancy.as_user(m):
        goal.apply_recommended(app, "m100", "live")
    seen = []
    r = members.for_each_member(app, lambda: seen.append((tenancy.uid(), goal.get(app)["dca"]["mode"])))
    assert r["ok"] == 1 and seen == [(m["id"], "live")]


def test_frontend_has_member_screens():
    js = "".join((STATIC / f).read_text(encoding="utf-8") for f in ("app.js", "member.js"))
    for s in ("memberAuth", "verifyLink", 'v === "reset"', 'v === "signup"', "TV.account", "TV.admin", "/api/me/export",
              "/api/admin/policy", "개인정보 수집·이용", "투자 위험", "만 14세", "authBoot", "memberCanView"):
        assert s in js, s
    assert (STATIC / "legal" / "terms.html").exists() and (STATIC / "legal" / "privacy.html").exists()
    assert '<script src="/member.js"></script>' in (STATIC / "index.html").read_text(encoding="utf-8")
    assert "/member.js" in (STATIC / "sw.js").read_text(encoding="utf-8")

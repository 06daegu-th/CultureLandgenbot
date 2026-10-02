"""v17: AI 성적 추적·자동 SHADOW 강등 · 알파 채점 · 조용한 장 규칙 · 라이선스 가드 · 로그인/2단계 인증/읽기 전용 ·
외부 연결 점검 · 빠른 일봉 로딩 · KIS 검증 진행."""

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from quant_ai import ops


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v17")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def _syms(app):
    return [s for s in app.market_data()[0] if s[:1].isdigit()]


def _consensus(app, sym, rows):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    from quant_ai.review.ledger import seal
    with session_scope(app.engine) as s:
        for at, p, rr, ex, created in rows:
            r = ConsensusRecord(symbol=sym, as_of=at, action="BUY" if p >= 0.58 else "HOLD", prob_up=p, confidence=60.0, conflict="low",
                                payload={"outcomes": {"5": rr, "excess": ex}}, realized_return=rr, correct=(p >= 0.5) == (rr > 0),
                                created_at=created)
            seal(r)
            s.add(r)


# ------------------------------------------------------------------ AI 성적 추적 · 자동 강등
def test_ai_track_trust_and_auto_demotion(app):
    from quant_ai import aitrack, explain
    from quant_ai.config import Mode
    sym = _syms(app)[0]
    b = app.market_data()[0][sym]
    # 확률이 높을수록 오히려 떨어지는 '나쁜 AI'
    _consensus(app, sym, [(b.index[-200 + i].to_pydatetime(), 0.7 if i % 2 else 0.3, -0.02 if i % 2 else 0.02,
                           -0.01 if i % 2 else 0.01, None) for i in range(150)])
    t = aitrack.trust({"n": 500, "accuracy": 0.45, "base": 0.5, "brier_skill": -0.05, "ece": 0.15, "alpha": -0.01})
    assert t["level"] == "UNTRUSTED" and len(t["reasons"]) == 4
    assert aitrack.trust({"n": 500, "accuracy": 0.56, "base": 0.5, "brier_skill": 0.03, "ece": 0.04, "alpha": 0.004})["level"] == "CANDIDATE"
    assert aitrack.trust({"n": 100, "accuracy": 0.56, "base": 0.5, "brier_skill": 0.03, "ece": 0.04, "alpha": 0.004})["level"] == "WATCH"
    ops.set_state(app.engine, "ai_demotion", {})
    app.settings = replace(app.settings, core_only=False)
    assert app.ai_enabled(Mode.PAPER)
    day = datetime(2026, 10, 1, tzinfo=UTC)
    for k in range(3):
        m = aitrack.snapshot(app, day + timedelta(days=k))
        assert m["trust"] == "UNTRUSTED"
    assert aitrack.demoted(app.engine) and not app.ai_enabled(Mode.PAPER)  # 3일 연속 → SHADOW
    r = aitrack.report(app)
    assert r["days"] == 3 and r["trust"]["level"] == "UNTRUSTED" and r["demotion"]["on"]
    _consensus(app, sym, [(b.index[-1].to_pydatetime(), 0.7, None, None, None)][:0])
    at = b.index[-1].to_pydatetime()
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    from quant_ai.review.ledger import seal
    with session_scope(app.engine) as s:
        rec = ConsensusRecord(symbol=sym, as_of=at, action="BUY", prob_up=0.7, confidence=70.0, conflict="low", payload={})
        seal(rec)
        s.add(rec)
    ops.set_state(app.engine, "data_health", {"trading": "OK"})
    e = explain.for_symbol(app, sym)
    assert any("자동 강등" in x for x in e["blocks"])
    ops.set_state(app.engine, "ai_demotion", {})
    app.settings = replace(app.settings, core_only=True)


# ------------------------------------------------------------------ 알파 채점 · 조용한 장 규칙
def test_alpha_card_separates_market_direction(app):
    from quant_ai import alphascore
    a = alphascore.alpha_card(app)
    assert a["n"] >= 150 and 0 <= a["hit_excess"] <= 1 and "market_share" in a and a["verdict"]
    q = alphascore.quiet_rule(app)
    assert q["registration"]["hypothesis"] and q["decision"].startswith(("전진 검증 중", "채택", "기각"))
    assert set(q["forward"]) == {"quiet", "active"} and not q["throttle_on"]
    reg = q["registration"]["registered_at"]
    assert alphascore.quiet_rule(app)["registration"]["registered_at"] == reg  # 사전 등록은 한 번만 (나중에 못 바꿈)


def test_quiet_series_uses_only_past_and_throttle_is_opt_in(app, monkeypatch):
    import pandas as pd

    from quant_ai import alphascore
    _, bench, _ = app.market_data()
    q = alphascore.quiet_series(bench)
    assert len(q) > 50
    cut = q.index[len(q) // 2]
    q2 = alphascore.quiet_series(bench[pd.DatetimeIndex(bench.index).tz_localize(None) <= cut] if pd.DatetimeIndex(bench.index).tz
                                 else bench[bench.index <= cut])
    assert (q2 == q[q.index <= cut]).all()  # 미래 데이터를 더해도 과거 판정은 그대로 (룩어헤드 없음)
    buys = [{"symbol": "X", "confidence": 70}]
    assert alphascore.throttle(app, buys) == (buys, None)  # 기본 꺼짐
    monkeypatch.setenv("QUANT_AI_QUIET_THROTTLE", "true")
    monkeypatch.setattr(alphascore, "is_quiet", lambda app_, when=None: True)
    out, why = alphascore.throttle(app, buys)
    assert out == [] and "조용한 장" in why


# ------------------------------------------------------------------ 라이선스 가드
def test_license_guard_blocks_noncommercial_sources(app):
    from quant_ai import governance
    from quant_ai.config import Mode
    from quant_ai.scheduler import build_default_scheduler
    assert governance.source_allowed(app.settings, "naver") and governance.blocked_sources(app.settings) == []
    sch = build_default_scheduler(app, Mode.PAPER)
    calls = []
    job = next(j for j in sch.jobs if j.name == "investor_flow")
    inner = job.fn.__defaults__[0]  # 감싼 원래 함수
    job.fn.__defaults__ = (lambda now: calls.append(now) or "ran",) + job.fn.__defaults__[1:]
    assert job.fn("t") == "ran" and calls == ["t"]
    app.settings = replace(app.settings, service_level="commercial")
    try:
        assert job.fn("t2") == {"skipped": "license", "sources": ["naver"]} and calls == ["t"]
        assert ops.get_state(app.engine, "license_blocked")["investor_flow"]["sources"] == ["naver"]
        assert set(governance.blocked_sources(app.settings)) >= {"yahoo", "naver", "krx_marcap"}
        assert governance.source_allowed(app.settings, "dart")
        lic = governance.licenses(app)
        assert "자동으로 수집을 멈춤" in lic["warning"] and "naver" in lic["blocked_sources"]
        from quant_ai.web.api import DashboardAPI
        p = DashboardAPI(app).profile(_syms(app)[0])
        assert "license_blocked" in p
    finally:
        app.settings = replace(app.settings, service_level="personal")
    assert inner is not None


# ------------------------------------------------------------------ 로그인 · 2단계 인증 · 읽기 전용
def test_totp_rfc6238_vectors_and_password_hash():
    import base64

    from quant_ai.auth import check_password, hash_password, totp, verify_totp
    sec = base64.b32encode(b"12345678901234567890").decode()
    assert totp(sec, 59) == "287082" and totp(sec, 1111111109) == "081804" and totp(sec, 1234567890) == "005924"
    assert verify_totp(sec, "287082", t=59 + 30) and not verify_totp(sec, "287082", t=59 + 120) and not verify_totp(sec, "abc")
    h = hash_password("correct horse battery")
    assert check_password(h, "correct horse battery") and not check_password(h, "wrong") and not check_password("junk", "x")


def _serve(app, env):
    from quant_ai.auth import Auth
    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    a = Auth(env)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), env.get("QUANT_WEB_TOKEN"), {"127.0.0.1"}, a))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def call(method, path, body=None, headers=None):
        c = HTTPConnection("127.0.0.1", port, timeout=60)
        h = {"Content-Type": "application/json", "Host": f"127.0.0.1:{port}"} | (headers or {})
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}"), r.getheader("Set-Cookie")
    return httpd, call


def test_login_mfa_session_and_viewer_rbac(app):
    from quant_ai.auth import hash_password, new_secret, totp
    sec = new_secret()
    env = {"QUANT_WEB_PASSWORD_HASH": hash_password("pw-1234567890"), "QUANT_WEB_TOTP_SECRET": sec, "QUANT_WEB_VIEWER_TOKEN": "view-tok"}
    httpd, call = _serve(app, env)
    try:
        st, info, _ = call("GET", "/api/auth")
        assert st == 200 and info["login_required"] and info["mfa"] and info["role"] is None
        assert call("GET", "/api/clock")[0] == 401
        assert call("GET", "/api/health")[0] in (200, 503)  # 모니터링용은 공개
        st, r, _ = call("POST", "/api/login", {"password": "pw-1234567890", "otp": "000000"})
        assert st == 401 and "2단계" in r["error"]
        st, r, cookie = call("POST", "/api/login", {"password": "pw-1234567890", "otp": totp(sec)})
        assert st == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie
        sid = cookie.split(";")[0]
        assert call("GET", "/api/clock", headers={"Cookie": sid})[0] == 200
        assert call("POST", "/api/prefs", {"theme": "dark"}, headers={"Cookie": sid})[0] == 200  # 관리자 쓰기 가능
        # 읽기 전용 토큰: 조회 O · 쓰기 403
        assert call("GET", "/api/clock", headers={"X-Token": "view-tok"})[0] == 200
        st, r, _ = call("POST", "/api/killswitch", {"on": True}, headers={"X-Token": "view-tok"})
        assert st == 403 and "읽기 전용" in r["error"] and not app.kill_switch_on()
        call("POST", "/api/logout", {}, headers={"Cookie": sid})
        assert call("GET", "/api/clock", headers={"Cookie": sid})[0] == 401
        for _ in range(5):
            call("POST", "/api/login", {"password": "bad", "otp": "1"})
        st, r, _ = call("POST", "/api/login", {"password": "pw-1234567890", "otp": totp(sec)})
        assert st == 401 and "잠금" in r["error"]  # 5번 실패 → 15분 잠금
        from quant_ai.governance import audit_log
        acts = [a["action"] for a in audit_log(app.engine)]
        assert "login" in acts and "login_fail" in acts
    finally:
        httpd.shutdown()


def test_no_auth_configured_keeps_local_default(app):
    httpd, call = _serve(app, {})
    try:
        assert call("GET", "/api/clock")[0] == 200 and call("GET", "/api/auth")[1]["role"] == "admin"
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ 외부 연결 점검
def test_netcheck_classifies_failures(app):
    from quant_ai import netcheck

    def fetch(url):
        if "github" in url:
            return b"x" * 200
        if "naver" in url:
            raise RuntimeError("GET 실패 (URLError: Tunnel connection failed: 403 Forbidden)")
        if "yahoo" in url:
            raise RuntimeError("GET 실패 (URLError: [Errno -3] Temporary failure in name resolution)")
        if "koreainvestment" in url:
            raise RuntimeError("HTTP 404: not found")
        raise RuntimeError("timed out")
    r = netcheck.run(app, fetch=fetch)
    st = {x["key"]: x["status"] for x in r["rows"]}
    assert st["krx_marcap"] == "ok" and st["naver_quote"] == "blocked" and st["yahoo"] == "dns" and st["rss"] == "timeout"
    assert st["kis_demo"] == "ok" and st["dart"] == "missing"
    assert netcheck.last(app)["total"] == len(r["rows"])


# ------------------------------------------------------------------ 빠른 일봉 로딩
def test_fast_load_bars_matches_orm(app):
    import pandas as pd
    from sqlalchemy import select

    from quant_ai.data.db import load_bars, session_scope, to_utc_index
    from quant_ai.data.models import PriceBar
    syms = _syms(app)[:3] + ["NOPE"]
    with session_scope(app.engine) as s:
        fast = load_bars(s, syms)
        for sym in syms[:3]:
            rows = s.scalars(select(PriceBar).where(PriceBar.symbol == sym, PriceBar.interval == "1d").order_by(PriceBar.ts)).all()
            ref = pd.DataFrame([(r.ts, r.open, r.high, r.low, r.close, r.volume) for r in rows],
                               columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
            ref.index = to_utc_index(ref.index)
            pd.testing.assert_frame_equal(fast[sym], ref)
    assert list(fast) == syms[:3]


def test_validation_shows_kis_setup_and_eta(app):
    from quant_ai.validation import progress
    p = progress(app)
    kis = next(i for i in p["items"] if i["key"] == "kis")
    assert "APP_KEY ✗" in kis["detail"] and len(kis["steps"]) == 5
    sl = next(i for i in p["items"] if i["key"] == "slippage")
    assert "예상 완료일 계산 불가" in sl["detail"]

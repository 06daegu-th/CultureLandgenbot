"""v34 회원 — 가입 · 로그인 · 세션 · 2단계 인증 · 비밀번호 재설정 · 이메일 확인 · 초대 · 운영자 관리 · 내보내기 · 탈퇴.

보안 원칙
  · 비밀번호는 scrypt 해시만 (auth.hash_password) · 세션·토큰은 값이 아니라 sha256 만 DB 에 (DB 가 새도 로그인 못 함)
  · 로그인 실패: 계정별 5번 → 15분 잠금 (DB · 서버 여러 대여도 같이) + IP 별 20번 → 15분 (서버 메모리)
  · 없는 이메일도 같은 시간이 걸리게 (가짜 해시 계산) · 가입·재설정 응답으로 '그 이메일이 있는지' 알 수 없게
  · OTP 는 한 번만 (같은 시간 칸·이전 칸 재사용 거부) · 비밀번호 바꾸면 다른 기기 로그아웃
  · 세션 12시간(활동하면 연장, 최대 30일) · HttpOnly · SameSite=Strict · HTTPS 면 Secure
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from . import ops, tenancy
from .auth import check_password, hash_password, new_secret, provisioning_uri, totp_counter
from .data.db import session_scope
from .data.models import (
    AlertRecord,
    AlertRule,
    FillRecord,
    OrderRecord,
    PortfolioSnapshot,
    User,
    UserJournal,
    UserSession,
    UserToken,
)

SESSION_IDLE = timedelta(hours=12)
SESSION_MAX = timedelta(days=30)
LOCK_AFTER, LOCK_FOR = 5, timedelta(minutes=15)
IP_FAILS, IP_WINDOW = 20, 15 * 60
SIGNUP_PER_IP_HOUR = 5
TERMS_VERSION = "2026-10"  # 약관·개인정보 처리방침이 바뀌면 올리고, 다음 로그인 때 다시 동의 받는다
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")
COMMON = {"password", "password1", "12345678", "123456789", "1234567890", "qwerty123", "qwertyuiop", "iloveyou", "11111111",
          "00000000", "abcd1234", "1q2w3e4r", "1q2w3e4r5t", "qwer1234", "asdf1234", "zxcv1234", "a12345678", "admin1234",
          "letmein1", "welcome1", "sunshine1", "princess1", "dragon123", "monkey123", "football1", "baseball1", "korea123",
          "samsung123", "passw0rd", "p@ssw0rd", "aa123456", "aaaa1111", "test1234"}
_DUMMY = hash_password("not-a-real-password-for-timing")
_IP: dict[str, list[float]] = {}
_SIGNUPS: dict[str, list[float]] = {}
_LOCK = threading.Lock()
_SESS_CACHE: dict[str, tuple[float, dict]] = {}  # sha → (만든 시각, 사용자) — 30초 (DB 부담 줄이기)
SESS_CACHE_S = 30


class AuthError(ValueError):
    """로그인·가입 실패 — 화면에 그대로 보여 줘도 되는 문장 (code 는 화면 분기용)."""

    def __init__(self, msg: str, code: str = "error", status: int = 400):
        super().__init__(msg)
        self.code, self.status = code, status


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(t: datetime | None) -> datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=UTC)


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if len(e) > 254 or not EMAIL_RE.match(e):
        raise AuthError("이메일 주소 형식이 맞지 않아요", "email")
    return e


def password_problem(pw: str, email: str = "") -> str | None:
    """비밀번호 규칙: 10자 이상 · 숫자만/한 글자 반복 금지 · 흔한 비밀번호 금지 · 이메일 앞부분 금지."""
    pw = pw or ""
    if len(pw) < 10:
        return "비밀번호는 10자 이상으로 해 주세요"
    if len(pw) > 200:
        return "비밀번호가 너무 길어요 (200자까지)"
    if pw.isdigit() or len(set(pw)) <= 2:
        return "숫자만 쓰거나 같은 글자를 반복한 비밀번호는 쓸 수 없어요"
    if pw.lower() in COMMON or pw.lower().rstrip("!@#") in COMMON:
        return "너무 흔한 비밀번호예요 — 다른 것으로 해 주세요"
    local = (email or "").split("@")[0].lower()
    if len(local) >= 4 and local in pw.lower():
        return "비밀번호에 이메일 아이디를 넣지 마세요"
    return None


def _seal_secret(v: str) -> str:
    """OTP 비밀값 — 데이터 암호화 키가 있으면 암호화해서 저장."""
    sealed = tenancy.seal({"s": v})
    return "enc:" + sealed["_enc"] if "_enc" in sealed else v


def _open_secret(v: str | None) -> str | None:
    if not v:
        return None
    return tenancy.unseal({"_enc": v[4:]})["s"] if v.startswith("enc:") else v


def public(u: User) -> dict:
    """화면·문맥에 쓰는 사용자 정보 (비밀 없음)."""
    return {"id": u.id, "email": u.email, "name": u.name or u.email.split("@")[0], "role": u.role, "plan": effective_plan(u),
            "owner": u.role == "owner", "verified": u.email_verified_at is not None, "mfa": bool(u.totp_secret),
            "status": u.status, "created_at": _aware(u.created_at).isoformat() if u.created_at else None,
            "terms_ok": (u.consent or {}).get("terms") == TERMS_VERSION, "marketing": bool((u.consent or {}).get("marketing"))}


def effective_plan(u: User) -> str:
    if u.plan != "free" and u.plan_until and _aware(u.plan_until) < _now():
        return "free"  # 기간이 끝난 유료 요금제
    return u.plan or "free"


# ------------------------------------------------------------------ 만들기
def count(app) -> int:
    with session_scope(app.engine) as s:
        return int(s.scalar(select(func.count(User.id))) or 0)


def create(app, email: str, password: str, name: str | None = None, role: str = "member", plan: str = "free",
           consent: dict | None = None, verified: bool = False) -> dict:
    email = normalize_email(email)
    bad = password_problem(password, email)
    if bad:
        raise AuthError(bad, "password")
    if role not in ("owner", "admin", "member"):
        raise AuthError("알 수 없는 권한", "role")
    now = _now()
    with session_scope(app.engine) as s:
        if s.scalar(select(User.id).where(User.email == email)):
            raise AuthError("이미 가입된 이메일이에요 — 로그인하거나 비밀번호 찾기를 써 주세요", "exists", 409)
        if role == "owner" and s.scalar(select(User.id).where(User.role == "owner")):
            raise AuthError("소유자 계정은 하나만 만들 수 있어요", "owner_exists", 409)
        u = User(email=email, name=(name or "").strip()[:60] or None, pw_hash=hash_password(password), role=role, plan=plan,
                 status="active", failed=0, created_at=now, pw_changed_at=now, consent=consent,
                 email_verified_at=now if verified else None)
        s.add(u)
        s.flush()
        return public(u)


def signup(app, body: dict, ip: str, policy: dict, base_url: str = "") -> dict:
    """가입. policy['signup']: open(누구나) · invite(초대 링크) · closed(막음)."""
    mode = policy.get("signup", "invite")
    if mode == "closed":
        raise AuthError("지금은 새로 가입을 받지 않아요", "closed", 403)
    with _LOCK:
        hits = [t for t in _SIGNUPS.get(ip, []) if time.time() - t < 3600]
        if len(hits) >= SIGNUP_PER_IP_HOUR:
            raise AuthError("가입 시도가 너무 많아요 — 1시간 뒤에 다시 해 주세요", "rate", 429)
        _SIGNUPS[ip] = [*hits, time.time()]
    c = body.get("consent") or {}
    if not (c.get("terms") and c.get("privacy") and c.get("risk")):
        raise AuthError("필수 동의(이용약관 · 개인정보 수집·이용 · 투자 위험 안내)에 체크해 주세요", "consent")
    if not c.get("age14"):
        raise AuthError("만 14세 이상만 가입할 수 있어요", "age")
    inv = None
    if mode == "invite" or body.get("invite"):
        inv = _token_peek(app, str(body.get("invite") or ""), "invite")
        if inv is None:
            raise AuthError("초대 링크가 없거나 만료됐어요 — 초대한 사람에게 새 링크를 받아 주세요", "invite", 403)
        if (inv.get("data") or {}).get("ref") and not policy.get("member_invites", True):
            raise AuthError("지금은 회원 초대 링크로 가입할 수 없어요", "invite", 403)
        want = (inv.get("data") or {}).get("email")
        if want and want != (body.get("email") or "").strip().lower():
            raise AuthError("이 초대 링크는 다른 이메일용이에요", "invite_email", 403)
    consent = {"terms": TERMS_VERSION, "privacy": TERMS_VERSION, "risk": True, "age14": True,
               "marketing": bool(c.get("marketing")), "at": _now().isoformat(), "ip": ip[:64]}
    data = (inv or {}).get("data") or {}
    u = create(app, str(body.get("email", "")), str(body.get("password", "")), str(body.get("name") or "")[:60] or None,
               role="member", plan=data.get("plan") if data.get("plan") in ("free", "pro") else "free", consent=consent,
               verified=bool(data.get("email")))  # 이메일을 지정한 초대로 왔으면 그 이메일은 확인된 것
    if inv is not None:
        _token_use(app, str(body.get("invite")), "invite", multi=not data.get("email"))
        if data.get("ref") and data.get("by"):  # v35 친구 초대: 기록 + (켰으면) 둘 다 프로 N일
            from . import together
            together.on_signup(app, int(data["by"]), u["id"])
            with session_scope(app.engine) as s:
                nu = s.get(User, u["id"])
                nu.consent = {**(nu.consent or {}), "ref_by": int(data["by"])}
    if not u["verified"]:
        send_verify(app, u["id"], base_url)
    ops_audit(app, "signup", f"#{u['id']}", u["id"])
    return u


# ------------------------------------------------------------------ 로그인 · 세션
def _ip_blocked(ip: str) -> bool:
    with _LOCK:
        f = [t for t in _IP.get(ip, []) if time.time() - t < IP_WINDOW]
        _IP[ip] = f
        return len(f) >= IP_FAILS


def _ip_fail(ip: str) -> None:
    with _LOCK:
        _IP.setdefault(ip, []).append(time.time())


def _check_login(u: User | None, password: str, otp: str | None, now: datetime, require_verified: bool) -> AuthError | None:
    """로그인 판정 — 틀리면 실패 횟수를 올리고(호출한 쪽이 저장) 오류를 돌려준다."""
    if u is None:
        return AuthError("이메일 또는 비밀번호가 맞지 않아요", "bad", 401)
    if u.locked_until and _aware(u.locked_until) > now:
        mins = int((_aware(u.locked_until) - now).total_seconds() // 60) + 1
        return AuthError(f"로그인 실패가 많아 {mins}분 동안 잠겼어요 (비밀번호 찾기로 바로 풀 수 있어요)", "locked", 429)

    def fail(msg: str, code: str) -> AuthError:
        u.failed = (u.failed or 0) + 1
        if u.failed >= LOCK_AFTER:
            u.locked_until, u.failed = now + LOCK_FOR, 0
        return AuthError(msg, code, 401)
    if not check_password(u.pw_hash, password or ""):
        return fail("이메일 또는 비밀번호가 맞지 않아요", "bad")
    if u.status != "active":
        return AuthError("사용이 정지된 계정이에요 — 고객센터로 문의해 주세요", "disabled", 403)
    if u.totp_secret:
        if not otp:
            return AuthError("2단계 인증 코드(6자리)를 넣어 주세요", "otp_required", 401)
        c = totp_counter(_open_secret(u.totp_secret), otp)
        if c is None or (u.totp_last is not None and c <= u.totp_last):
            return fail("2단계 인증 코드가 맞지 않거나 이미 쓴 코드예요 (휴대폰 시간도 확인)", "otp_bad")
        u.totp_last = c
    if require_verified and u.email_verified_at is None and u.role == "member":
        return AuthError("이메일 확인이 필요해요 — 받은 메일의 링크를 눌러 주세요 (못 받았으면 '확인 메일 다시 받기')", "verify_required", 403)
    return None


def login(app, email: str, password: str, otp: str | None, ip: str, agent: str = "", require_verified: bool = False) -> tuple[str, dict]:
    """(세션 값, 사용자). 실패하면 AuthError — 문장은 '어디가 틀렸는지' 최소한만."""
    if _ip_blocked(ip):
        raise AuthError("로그인 실패가 너무 많아요 — 15분 뒤에 다시 해 주세요", "locked", 429)
    try:
        email = normalize_email(email)
    except AuthError:
        check_password(_DUMMY, password or "")
        _ip_fail(ip)
        raise AuthError("이메일 또는 비밀번호가 맞지 않아요", "bad", 401) from None
    now = _now()
    err: AuthError | None = None
    with session_scope(app.engine) as s:  # 실패 횟수는 반드시 저장되게: 예외는 저장(커밋)한 뒤에 던진다
        u = s.scalar(select(User).where(User.email == email))
        err = _check_login(u, password, otp, now, require_verified)
        if err is not None:
            if u is None:
                check_password(_DUMMY, password or "")  # 없는 계정도 같은 시간
            if err.code in ("bad", "otp_bad"):
                _ip_fail(ip)
        else:
            u.failed, u.locked_until, u.last_login_at, u.last_seen_at = 0, None, now, now
            sid = secrets.token_urlsafe(32)
            s.add(UserSession(id=_sha(sid), user_id=u.id, created_at=now, expires_at=now + SESSION_IDLE, last_seen_at=now,
                              ip=ip[:64], agent=(agent or "")[:200]))
            # 한 사람이 세션을 끝없이 만들지 않게: 최근 20개만
            old = s.scalars(select(UserSession.id).where(UserSession.user_id == u.id).order_by(UserSession.created_at.desc()).offset(20)).all()
            if old:
                s.query(UserSession).filter(UserSession.id.in_(old)).delete(synchronize_session=False)
            out = public(u)
    if err is not None:
        raise err
    with _LOCK:
        _IP.pop(ip, None)
    ops_audit(app, "login", f"#{out['id']} {ip}", out["id"])
    return sid, out


def session_user(app, sid: str | None) -> dict | None:
    """세션 값 → 사용자 (만료·정지면 None). 활동하면 12시간 연장 (만든 지 30일까지)."""
    if not sid or len(sid) > 100:
        return None
    h = _sha(sid)
    hit = _SESS_CACHE.get(h)
    if hit and time.monotonic() - hit[0] < SESS_CACHE_S:
        return hit[1]
    now = _now()
    with session_scope(app.engine) as s:
        row = s.get(UserSession, h)
        if row is None or _aware(row.expires_at) < now:
            if row is not None:
                s.delete(row)
            _SESS_CACHE.pop(h, None)
            return None
        u = s.get(User, row.user_id)
        if u is None or u.status != "active":
            return None
        if not row.last_seen_at or now - _aware(row.last_seen_at) > timedelta(minutes=5):
            row.last_seen_at = now
            row.expires_at = min(now + SESSION_IDLE, _aware(row.created_at) + SESSION_MAX)
            u.last_seen_at = now
        out = public(u)
    if len(_SESS_CACHE) > 5000:
        _SESS_CACHE.clear()
    _SESS_CACHE[h] = (time.monotonic(), out)
    return out


def logout(app, sid: str | None) -> None:
    if not sid:
        return
    h = _sha(sid)
    _SESS_CACHE.pop(h, None)
    with session_scope(app.engine) as s:
        row = s.get(UserSession, h)
        if row is not None:
            s.delete(row)


def _drop_sessions(app, user_id: int, keep_sid: str | None = None) -> int:
    keep = _sha(keep_sid) if keep_sid else None
    with session_scope(app.engine) as s:
        q = s.query(UserSession).filter(UserSession.user_id == user_id)
        if keep:
            q = q.filter(UserSession.id != keep)
        n = q.delete(synchronize_session=False)
    for k in [k for k, v in _SESS_CACHE.items() if v[1]["id"] == user_id and k != keep]:
        _SESS_CACHE.pop(k, None)
    return n


def sessions(app, user_id: int, current_sid: str | None = None) -> list[dict]:
    cur = _sha(current_sid) if current_sid else None
    with session_scope(app.engine) as s:
        rows = s.scalars(select(UserSession).where(UserSession.user_id == user_id).order_by(UserSession.last_seen_at.desc())).all()
        return [{"id": r.id[:12], "current": r.id == cur, "ip": r.ip, "agent": (r.agent or "")[:80],
                 "created_at": _aware(r.created_at).isoformat(), "last_seen_at": _aware(r.last_seen_at).isoformat() if r.last_seen_at else None}
                for r in rows]


def logout_others(app, user_id: int, sid: str | None) -> int:
    n = _drop_sessions(app, user_id, keep_sid=sid)
    ops_audit(app, "logout_others", f"#{user_id} {n}개", user_id)
    return n


# ------------------------------------------------------------------ 한 번 쓰는 토큰 (확인 · 재설정 · 초대 · OTP 등록)
def _token_new(app, kind: str, user_id: int | None, data: dict | None, ttl: timedelta) -> str:
    tok = secrets.token_urlsafe(32)
    now = _now()
    with session_scope(app.engine) as s:
        if user_id is not None and kind in ("verify", "reset", "totp"):  # 같은 종류의 예전 토큰은 무효
            s.query(UserToken).filter(UserToken.user_id == user_id, UserToken.kind == kind, UserToken.used_at.is_(None)).delete()
        s.add(UserToken(id=_sha(tok), kind=kind, user_id=user_id, data=data, created_at=now, expires_at=now + ttl))
    return tok


def _token_peek(app, tok: str, kind: str) -> dict | None:
    if not tok or len(tok) > 100:
        return None
    with session_scope(app.engine) as s:
        r = s.get(UserToken, _sha(tok))
        if r is None or r.kind != kind or _aware(r.expires_at) < _now():
            return None
        if r.used_at is not None and not (r.data or {}).get("multi"):
            return None
        return {"user_id": r.user_id, "data": r.data or {}}


def _token_use(app, tok: str, kind: str, multi: bool = False) -> dict | None:
    """토큰 쓰기 — 한 번만 (초대 중 여러 명이 쓰는 링크는 multi: 횟수만 센다)."""
    with session_scope(app.engine) as s:
        r = s.get(UserToken, _sha(tok))
        if r is None or r.kind != kind or _aware(r.expires_at) < _now():
            return None
        d = dict(r.data or {})
        if d.get("multi") or multi:
            d["uses"] = int(d.get("uses", 0)) + 1
            if d.get("max_uses") and d["uses"] >= int(d["max_uses"]):
                r.used_at = _now()
            r.data = d
            return {"user_id": r.user_id, "data": d}
        if r.used_at is not None:
            return None
        r.used_at = _now()
        return {"user_id": r.user_id, "data": d}


def send_verify(app, user_id: int, base_url: str = "") -> bool:
    from . import mailer
    with session_scope(app.engine) as s:
        u = s.get(User, user_id)
        if u is None or u.email_verified_at is not None:
            return False
        email = u.email
    tok = _token_new(app, "verify", user_id, None, timedelta(days=3))
    link = f"{base_url}/#verify/{tok}"
    return mailer.send(app, email, "이메일 주소를 확인해 주세요",
                       f"아래 링크를 누르면 가입이 마무리돼요 (3일 동안 유효).\n\n{link}\n\n직접 가입하지 않았다면 이 메일은 무시해 주세요.",
                       kind="verify")


def verify_email(app, tok: str) -> dict:
    t = _token_use(app, tok, "verify")
    if t is None:
        raise AuthError("확인 링크가 만료됐거나 이미 썼어요 — 로그인 후 '확인 메일 다시 받기'를 눌러 주세요", "token")
    with session_scope(app.engine) as s:
        u = s.get(User, t["user_id"])
        if u is None:
            raise AuthError("계정이 없어요", "token")
        u.email_verified_at = u.email_verified_at or _now()
        out = public(u)
    _SESS_CACHE.clear()
    return out


def request_reset(app, email: str, base_url: str = "", ip: str = "") -> dict:
    """비밀번호 찾기 — 그 이메일이 있든 없든 같은 응답 (가입 여부를 알려 주지 않는다)."""
    from . import mailer
    msg = {"ok": True, "message": "가입된 이메일이면 재설정 링크를 보냈어요 (1시간 동안 유효 · 스팸함도 확인)"}
    if _ip_blocked(ip or "?"):
        return msg
    try:
        email = normalize_email(email)
    except AuthError:
        return msg
    with session_scope(app.engine) as s:
        u = s.scalar(select(User).where(User.email == email))
        uid_ = u.id if u is not None and u.status == "active" else None
    if uid_ is None:
        _ip_fail(ip or "?")  # 없는 이메일을 마구 넣어 보는 것도 막는다
        return msg
    tok = _token_new(app, "reset", uid_, None, timedelta(hours=1))
    mailer.send(app, email, "비밀번호 재설정", f"아래 링크에서 새 비밀번호를 정해 주세요 (1시간 동안 유효).\n\n{base_url}/#reset/{tok}\n\n"
                "직접 요청하지 않았다면 이 메일은 무시해 주세요 — 비밀번호는 바뀌지 않아요.", kind="reset")
    ops_audit(app, "reset_request", f"#{uid_}", uid_)
    return msg


def reset_password(app, tok: str, new_password: str) -> dict:
    t = _token_peek(app, tok, "reset")
    if t is None:
        raise AuthError("재설정 링크가 만료됐거나 이미 썼어요 — 비밀번호 찾기를 다시 해 주세요", "token")
    with session_scope(app.engine) as s:
        u = s.get(User, t["user_id"])
        email = u.email if u is not None else None
    if email is None:
        raise AuthError("계정이 없어요", "token")
    bad = password_problem(new_password, email)
    if bad:
        raise AuthError(bad, "password")
    if not _token_use(app, tok, "reset"):  # 비밀번호 규칙을 통과한 뒤에 링크를 쓴다 (규칙에 걸려도 다시 시도 가능)
        raise AuthError("재설정 링크가 만료됐거나 이미 썼어요", "token")
    with session_scope(app.engine) as s:
        u = s.get(User, t["user_id"])
        u.pw_hash, u.pw_changed_at, u.failed, u.locked_until = hash_password(new_password), _now(), 0, None
        u.email_verified_at = u.email_verified_at or _now()  # 메일 링크를 눌렀으니 이메일도 확인된 것
        uid_ = u.id
    _drop_sessions(app, uid_)  # 모든 기기 로그아웃 (누가 몰래 쓰고 있었다면 끊긴다)
    ops_audit(app, "reset_done", f"#{uid_}", uid_)
    return {"ok": True, "message": "비밀번호를 바꿨어요 — 새 비밀번호로 로그인해 주세요"}


def change_password(app, user_id: int, old: str, new: str, sid: str | None = None) -> dict:
    with session_scope(app.engine) as s:
        u = s.get(User, user_id)
        if u is None or not check_password(u.pw_hash, old or ""):
            raise AuthError("지금 비밀번호가 맞지 않아요", "bad", 401)
        bad = password_problem(new, u.email)
        if bad:
            raise AuthError(bad, "password")
        if check_password(u.pw_hash, new):
            raise AuthError("지금과 다른 비밀번호로 해 주세요", "same")
        u.pw_hash, u.pw_changed_at = hash_password(new), _now()
    n = _drop_sessions(app, user_id, keep_sid=sid)
    ops_audit(app, "password_change", f"#{user_id}", user_id)
    return {"ok": True, "logged_out": n, "message": "비밀번호를 바꿨어요" + (f" (다른 기기 {n}곳은 로그아웃)" if n else "")}


def update_profile(app, user_id: int, body: dict) -> dict:
    with session_scope(app.engine) as s:
        u = s.get(User, user_id)
        if "name" in body:
            u.name = str(body.get("name") or "").strip()[:60] or None
        if "marketing" in body:
            u.consent = {**(u.consent or {}), "marketing": bool(body["marketing"]), "marketing_at": _now().isoformat()}
        if body.get("accept_terms"):
            u.consent = {**(u.consent or {}), "terms": TERMS_VERSION, "privacy": TERMS_VERSION, "at": _now().isoformat()}
        out = public(u)
    _SESS_CACHE.clear()
    return out


# ------------------------------------------------------------------ 2단계 인증 (OTP 앱)
def totp_begin(app, user_id: int) -> dict:
    with session_scope(app.engine) as s:
        u = s.get(User, user_id)
        email = u.email
    sec = new_secret()
    _token_new(app, "totp", user_id, {"secret": _seal_secret(sec)}, timedelta(minutes=15))
    return {"secret": sec, "uri": provisioning_uri(sec, email, "Quant AI"),
            "how": "OTP 앱(Google Authenticator · 1Password · Microsoft Authenticator)에 '키 직접 입력'으로 넣고, 나오는 6자리를 입력하세요"}


def totp_enable(app, user_id: int, code: str) -> dict:
    with session_scope(app.engine) as s:
        r = s.scalars(select(UserToken).where(UserToken.user_id == user_id, UserToken.kind == "totp", UserToken.used_at.is_(None))
                      .order_by(UserToken.created_at.desc())).first()
        if r is None or _aware(r.expires_at) < _now():
            raise AuthError("등록 시간이 지났어요 — 처음부터 다시 해 주세요", "token")
        sec = _open_secret((r.data or {}).get("secret"))
        c = totp_counter(sec, code)
        if c is None:
            raise AuthError("코드가 맞지 않아요 — 휴대폰 시간이 맞는지 확인해 주세요", "otp_bad")
        u = s.get(User, user_id)
        u.totp_secret, u.totp_last, r.used_at = _seal_secret(sec), c, _now()
        out = public(u)
    _SESS_CACHE.clear()
    ops_audit(app, "mfa_on", f"#{user_id}", user_id)
    return out


def totp_disable(app, user_id: int, password: str, code: str) -> dict:
    with session_scope(app.engine) as s:
        u = s.get(User, user_id)
        if not check_password(u.pw_hash, password or ""):
            raise AuthError("비밀번호가 맞지 않아요", "bad", 401)
        if u.totp_secret and totp_counter(_open_secret(u.totp_secret), code) is None:
            raise AuthError("2단계 인증 코드가 맞지 않아요", "otp_bad")
        u.totp_secret, u.totp_last = None, None
        out = public(u)
    _SESS_CACHE.clear()
    ops_audit(app, "mfa_off", f"#{user_id}", user_id)
    return out


# ------------------------------------------------------------------ 운영자
def invite(app, by: int, email: str | None = None, plan: str = "free", days: int = 7, max_uses: int = 1, base_url: str = "",
           ref: bool = False) -> dict:
    data: dict = {"plan": plan if plan in ("free", "pro") else "free", "by": by}
    if ref:
        data["ref"] = True  # v35 회원 초대 링크 (요금제는 항상 무료로 시작)
    if email:
        data["email"] = normalize_email(email)
    elif max_uses > 1:
        data |= {"multi": True, "max_uses": int(max_uses), "uses": 0}
    tok = _token_new(app, "invite", None, data, timedelta(days=max(1, min(int(days), 90))))
    link = f"{base_url}/#signup/{tok}"
    sent = False
    if email:
        from . import mailer
        sent = mailer.send(app, data["email"], "Quant AI 초대", f"초대를 받았어요. 아래 링크에서 가입해 주세요 ({days}일 동안 유효).\n\n{link}", kind="invite")
    ops_audit(app, "invite", f"{data.get('email') or f'링크 {max_uses}명'} · {data['plan']}", by)
    return {"link": link, "token": tok, "emailed": sent, "expires_days": days, "max_uses": max_uses if not email else 1}


def invite_info(app, tok: str) -> dict:
    t = _token_peek(app, tok, "invite")
    if t is None:
        return {"ok": False}
    return {"ok": True, "email": (t["data"] or {}).get("email"), "plan": (t["data"] or {}).get("plan", "free")}


def list_users(app, q: str = "", limit: int = 100, offset: int = 0) -> dict:
    with session_scope(app.engine) as s:
        base = select(User)
        if q:
            base = base.where(User.email.contains(q.strip().lower()[:80]))
        total = int(s.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows = s.scalars(base.order_by(User.id.desc()).limit(max(1, min(limit, 500))).offset(max(0, offset))).all()
        return {"total": total, "rows": [public(u) | {"last_login_at": _aware(u.last_login_at).isoformat() if u.last_login_at else None,
                                                      "last_seen_at": _aware(u.last_seen_at).isoformat() if u.last_seen_at else None,
                                                      "plan_until": _aware(u.plan_until).isoformat() if u.plan_until else None,
                                                      "locked": bool(u.locked_until and _aware(u.locked_until) > _now())} for u in rows]}


def admin_update(app, by: dict, user_id: int, body: dict) -> dict:
    """운영자가 회원 바꾸기: role · plan(+기간) · status · 잠금 풀기. 소유자는 소유자만 · 자기 권한은 못 낮춤."""
    with session_scope(app.engine) as s:
        u = s.get(User, int(user_id))
        if u is None:
            raise AuthError("회원이 없어요", "missing", 404)
        if u.role == "owner" and not by.get("owner"):
            raise AuthError("소유자 계정은 소유자만 바꿀 수 있어요", "forbidden", 403)
        if "role" in body:
            role = str(body["role"])
            if role not in ("admin", "member"):
                raise AuthError("권한은 admin / member", "role")
            if u.id == by.get("id"):
                raise AuthError("자기 권한은 바꿀 수 없어요", "self")
            if u.role == "owner":
                raise AuthError("소유자 권한은 바꿀 수 없어요", "forbidden", 403)
            if role == "admin" and not by.get("owner"):
                raise AuthError("운영자 지정은 소유자만 할 수 있어요", "forbidden", 403)
            u.role = role
        if "plan" in body:
            plan = str(body["plan"])
            if plan not in ("free", "pro"):
                raise AuthError("요금제는 free / pro", "plan")
            u.plan = plan
            days = body.get("days")
            u.plan_until = (_now() + timedelta(days=int(days))) if days not in (None, "", 0, "0") and plan != "free" else None
        if "status" in body:
            st = str(body["status"])
            if st not in ("active", "disabled"):
                raise AuthError("상태는 active / disabled", "status")
            if u.id == by.get("id"):
                raise AuthError("자기 계정은 정지할 수 없어요", "self")
            u.status = st
        if body.get("unlock"):
            u.failed, u.locked_until = 0, None
        out = public(u)
    if out["status"] != "active":
        _drop_sessions(app, out["id"])
    _SESS_CACHE.clear()
    ops_audit(app, "admin_user", f"#{user_id} {sorted(k for k in body)}", by.get("id"))
    return out


def stats(app) -> dict:
    now = _now()
    with session_scope(app.engine) as s:
        total = int(s.scalar(select(func.count(User.id))) or 0)
        act7 = int(s.scalar(select(func.count(User.id)).where(User.last_seen_at >= now - timedelta(days=7))) or 0)
        act1 = int(s.scalar(select(func.count(User.id)).where(User.last_seen_at >= now - timedelta(days=1))) or 0)
        plans = {p: int(n) for p, n in s.execute(select(User.plan, func.count(User.id)).group_by(User.plan))}
        mfa = int(s.scalar(select(func.count(User.id)).where(User.totp_secret.is_not(None))) or 0)
        created = [_aware(t) for (t,) in s.execute(select(User.created_at).where(User.created_at >= now - timedelta(days=30)))]
    days = [(now - timedelta(days=i)).date().isoformat() for i in range(13, -1, -1)]
    by_day = {d: 0 for d in days}
    for t in created:
        d = t.date().isoformat()
        if d in by_day:
            by_day[d] += 1
    return {"total": total, "active_1d": act1, "active_7d": act7, "plans": plans, "mfa": mfa,
            "signups_14d": [{"date": d, "n": n} for d, n in by_day.items()]}


# ------------------------------------------------------------------ 개인정보: 내보내기 · 탈퇴
def _books(user_id: int) -> list[str]:
    from .goal import ETF_BOOK
    from .ticket import MODE, US_MODE
    return [f"{b}@{tenancy.b36(user_id)}" for b in (MODE, US_MODE, ETF_BOOK)]


def export(app, user_id: int) -> dict:
    """내 데이터 전부 (개인정보 보호법 · 열람/이동권) — 비밀번호 해시·OTP 비밀값은 빼고."""
    with session_scope(app.engine) as s:
        u = s.get(User, user_id)
        me = public(u) | {"consent": u.consent, "last_login_at": _aware(u.last_login_at).isoformat() if u.last_login_at else None}
        rules = [{"symbol": r.symbol, "kind": r.kind, "value": r.value, "note": r.note, "active": r.active,
                  "created_at": _aware(r.created_at).isoformat() if r.created_at else None}
                 for r in s.scalars(select(AlertRule).where(AlertRule.owner == user_id))]
        journal = [{"created_at": _aware(r.created_at).isoformat(), "symbol": r.symbol, "action": r.action, "conviction": r.conviction,
                    "horizon": r.horizon, "reason": r.reason, "ref_price": r.ref_price, "realized_return": r.realized_return, "hash": r.row_hash}
                   for r in s.scalars(select(UserJournal).where(UserJournal.owner == user_id))]
        books = _books(user_id)
        orders = [{"mode": r.mode, "created_at": _aware(r.created_at).isoformat() if r.created_at else None, "symbol": r.symbol,
                   "side": r.side, "qty": r.qty, "status": r.status, "avg_price": r.avg_price}
                  for r in s.scalars(select(OrderRecord).where(OrderRecord.mode.in_(books)))]
        alerts = [{"ts": _aware(r.ts).isoformat(), "kind": r.kind, "title": r.title, "body": r.body}
                  for r in s.scalars(select(AlertRecord).where(AlertRecord.owner == user_id).order_by(AlertRecord.id.desc()).limit(500))]
    state = ops.user_states(app.engine, tenancy.user_prefix(user_id))
    state.pop("push_subs", None)  # 기기 알림 주소(비밀 키 포함)는 빼고
    return {"exported_at": _now().isoformat(), "account": me, "data": state, "alert_rules": rules, "journal": journal,
            "mock_orders": orders, "alerts": alerts, "sessions": sessions(app, user_id)}


def delete_account(app, user_id: int, password: str, confirm: str) -> dict:
    """탈퇴 — 확인 문구(이메일)와 비밀번호를 받고 그 사람 데이터를 모두 지운다 (감사 기록에는 번호만 남김)."""
    with session_scope(app.engine) as s:
        u = s.get(User, user_id)
        if u is None:
            raise AuthError("계정이 없어요", "missing", 404)
        if u.role == "owner":
            raise AuthError("소유자 계정은 탈퇴할 수 없어요 (서비스 데이터의 주인)", "forbidden", 403)
        if not check_password(u.pw_hash, password or ""):
            raise AuthError("비밀번호가 맞지 않아요", "bad", 401)
        if (confirm or "").strip().lower() != u.email:
            raise AuthError("확인을 위해 이메일 주소를 정확히 입력해 주세요", "confirm")
    n = purge(app, user_id)
    ops_audit(app, "account_delete", f"#{user_id} 데이터 {n}건 삭제", None)
    return {"ok": True, "deleted": n}


def grant_pro(app, user_id: int, days: int, why: str = "") -> dict:
    """프로 요금제 N일 더하기 (초대 보상 등) — 이미 프로면 남은 기간 뒤에 이어 붙인다 · 기간 없는 프로면 그대로."""
    with session_scope(app.engine) as s:
        u = s.get(User, user_id)
        if u is None or days <= 0:
            return {"ok": False}
        if u.plan == "pro" and u.plan_until is None:
            return {"ok": True, "unlimited": True}
        base = max(_now(), _aware(u.plan_until) or _now()) if u.plan == "pro" else _now()
        u.plan, u.plan_until = "pro", base + timedelta(days=int(days))
        until = u.plan_until.isoformat()
    _SESS_CACHE.clear()
    ops_audit(app, "grant_pro", f"#{user_id} +{days}일 {why}", user_id)
    return {"ok": True, "until": until}


def purge(app, user_id: int) -> int:
    """회원 데이터 전부 삭제 (탈퇴 · 운영자 삭제)."""
    try:  # v35: 모임에서 먼저 빼기 (사람 칸이 지워지기 전에)
        from . import together
        together.purge_user(app, user_id)
    except Exception:  # noqa: BLE001, S110 - 모임 정리 실패가 탈퇴를 막지 않게
        pass
    n = ops.delete_state_prefix(app.engine, tenancy.user_prefix(user_id))
    books = _books(user_id)
    with session_scope(app.engine) as s:
        n += s.query(AlertRule).filter(AlertRule.owner == user_id).delete(synchronize_session=False)
        n += s.query(UserJournal).filter(UserJournal.owner == user_id).delete(synchronize_session=False)
        n += s.query(AlertRecord).filter(AlertRecord.owner == user_id).delete(synchronize_session=False)
        oids = [i for (i,) in s.execute(select(OrderRecord.id).where(OrderRecord.mode.in_(books)))]
        if oids:
            n += s.query(FillRecord).filter(FillRecord.order_id.in_(oids)).delete(synchronize_session=False)
            n += s.query(OrderRecord).filter(OrderRecord.id.in_(oids)).delete(synchronize_session=False)
        n += s.query(PortfolioSnapshot).filter(PortfolioSnapshot.mode.in_(books)).delete(synchronize_session=False)
        n += s.query(UserSession).filter(UserSession.user_id == user_id).delete(synchronize_session=False)
        n += s.query(UserToken).filter(UserToken.user_id == user_id).delete(synchronize_session=False)
        u = s.get(User, user_id)
        if u is not None:
            s.delete(u)
            n += 1
    for k in [k for k, v in _SESS_CACHE.items() if v[1]["id"] == user_id]:
        _SESS_CACHE.pop(k, None)
    return n


def admin_delete(app, by: dict, user_id: int) -> dict:
    with session_scope(app.engine) as s:
        u = s.get(User, int(user_id))
        if u is None:
            raise AuthError("회원이 없어요", "missing", 404)
        if u.role == "owner" or u.id == by.get("id"):
            raise AuthError("소유자·자기 계정은 지울 수 없어요", "forbidden", 403)
        if u.role == "admin" and not by.get("owner"):
            raise AuthError("운영자 계정은 소유자만 지울 수 있어요", "forbidden", 403)
    n = purge(app, int(user_id))
    ops_audit(app, "admin_delete", f"#{user_id} 데이터 {n}건", by.get("id"))
    return {"ok": True, "deleted": n}


def cleanup(app) -> dict:
    """만료된 세션·토큰 정리 (스케줄러 하루 한 번)."""
    now = _now()
    with session_scope(app.engine) as s:
        a = s.query(UserSession).filter(UserSession.expires_at < now).delete(synchronize_session=False)
        b = s.query(UserToken).filter(UserToken.expires_at < now - timedelta(days=1)).delete(synchronize_session=False)
    return {"sessions": a, "tokens": b}


def all_ids(app, active_only: bool = True) -> list[dict]:
    """스케줄러용: 회원 목록 (사람별 작업 — 적립일 알림·모의 적립)."""
    with session_scope(app.engine) as s:
        q = select(User)
        if active_only:
            q = q.where(User.status == "active")
        return [{"id": u.id, "role": u.role, "owner": u.role == "owner", "plan": effective_plan(u)} for u in s.scalars(q)]


def for_each_member(app, fn, include_owner: bool = False) -> dict:
    """스케줄러용: 회원마다 그 사람 문맥에서 fn() (적립일 알림·모의 적립). 한 사람이 실패해도 나머지는 계속."""
    import logging
    ok = fail = 0
    for u in all_ids(app):
        if u["owner"] and not include_owner:
            continue  # 소유자는 문맥 없는 기본 실행이 이미 처리
        try:
            with tenancy.as_user(u):
                fn()
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            logging.getLogger("quant_ai.members").warning("회원 #%s 작업 실패: %s", u["id"], e)
    return {"ok": ok, "fail": fail}


def ops_audit(app, action: str, detail: str, user_id: int | None) -> None:
    """감사 로그 (누가 · 무엇을) — 이메일 같은 개인정보는 남기지 않고 회원 번호만."""
    from .governance import audit
    try:
        audit(app.engine, action, (f"[#{user_id}] " if user_id else "") + detail)
    except Exception:  # noqa: BLE001, S110 - 감사 기록 실패가 로그인을 막지는 않는다
        pass


__all__ = ["AuthError", "signup", "login", "logout", "session_user", "create", "count", "public", "password_problem",
           "normalize_email", "send_verify", "verify_email", "request_reset", "reset_password", "change_password", "update_profile",
           "totp_begin", "totp_enable", "totp_disable", "invite", "invite_info", "list_users", "admin_update", "admin_delete",
           "stats", "export", "delete_account", "grant_pro", "purge", "cleanup", "all_ids", "for_each_member", "sessions", "logout_others", "TERMS_VERSION"]

"""로그인 · 2단계 인증(TOTP) · 역할(관리자/읽기 전용) — 표준 라이브러리만.

설정 (.env, 모두 선택 — 아무것도 없으면 지금처럼 '이 PC 에서만' 접속):
  QUANT_WEB_PASSWORD_HASH   비밀번호 해시 (`./run.sh auth-setup` 이 만들어 줌 · 평문 QUANT_WEB_PASSWORD 도 가능하나 비권장)
  QUANT_WEB_TOTP_SECRET     2단계 인증 비밀값 (Google Authenticator·1Password 등 OTP 앱에 등록) — 있으면 로그인에 6자리 코드 필요
  QUANT_WEB_VIEWER_TOKEN    읽기 전용 토큰 (가족·동료에게 보여주기: 조회만, 긴급 정지·설정·주문 등 쓰기 불가)
  QUANT_WEB_TOKEN           (기존) 관리자 토큰 — 스크립트·자동화용
  QUANT_TRUSTED_PROXIES     리버스 프록시(nginx·Caddy 등) 주소, 쉼표로 (예: 127.0.0.1) — 이 주소에서 온 요청만 X-Forwarded-For/Proto 를 믿는다
  QUANT_WEB_SECURE_COOKIE   1 이면 세션 쿠키에 항상 Secure (HTTPS 로만 전송) — 외부 공개 시 권장

세션은 HttpOnly · SameSite=Strict 쿠키 (12시간) · 로그인 실패 5번이면 15분 잠금 (실제 접속 IP 별) · 로그인·실패는 감사 로그에.
OTP 코드는 한 번만 쓸 수 있다 (같은 코드·이전 코드 재사용 거부). TOTP 비밀값만 있고 비밀번호가 없으면 설정 오류로 서버가 시작하지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import threading
import time
from urllib.parse import quote

SESSION_TTL = 12 * 3600
LOCK_AFTER = 5
LOCK_S = 15 * 60


# ------------------------------------------------------------------ TOTP (RFC 6238)
def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def totp(secret: str, t: float | None = None, step: int = 30, digits: int = 6) -> str:
    counter = int((time.time() if t is None else t) // step)
    h = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_counter(secret: str, code: str, t: float | None = None, window: int = 1) -> int | None:
    """맞는 코드의 시간 칸(counter) — 재사용 방지에 쓴다. 틀리면 None."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return None
    now = time.time() if t is None else t
    for k in range(-window, window + 1):
        if hmac.compare_digest(totp(secret, now + k * 30), code):
            return int((now + k * 30) // 30)
    return None


def verify_totp(secret: str, code: str, t: float | None = None, window: int = 1) -> bool:
    return totp_counter(secret, code, t, window) is not None


def client_ip(remote: str, forwarded_for: str | None, trusted: set[str]) -> str:
    """실제 접속 IP. 믿는 프록시에서 온 요청만 X-Forwarded-For 를 보고, 오른쪽부터 믿는 프록시가 아닌 첫 주소를 쓴다
    (아무나 헤더를 꾸며 잠금을 피하거나 남을 잠그지 못하게)."""
    if remote not in trusted or not forwarded_for:
        return remote
    for hop in reversed([h.strip() for h in forwarded_for.split(",") if h.strip()]):
        if hop not in trusted:
            return hop[:64]
    return remote


def provisioning_uri(secret: str, account: str = "quant-ai", issuer: str = "Quant AI") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


# ------------------------------------------------------------------ 비밀번호 (scrypt)
def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def check_password(stored: str, pw: str) -> bool:
    try:
        kind, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------ 설정 · 세션
class Auth:
    def __init__(self, env: dict | None = None):
        e = os.environ if env is None else env
        self.admin_token = e.get("QUANT_WEB_TOKEN") or None
        self.viewer_token = e.get("QUANT_WEB_VIEWER_TOKEN") or None
        self.pw_hash = e.get("QUANT_WEB_PASSWORD_HASH") or (hash_password(e["QUANT_WEB_PASSWORD"]) if e.get("QUANT_WEB_PASSWORD") else None)
        self.totp_secret = e.get("QUANT_WEB_TOTP_SECRET") or None
        self.trusted_proxies = {x.strip() for x in (e.get("QUANT_TRUSTED_PROXIES") or "").split(",") if x.strip()}
        self.secure_cookie = (e.get("QUANT_WEB_SECURE_COOKIE") or "").strip().lower() in ("1", "true", "yes", "on")
        self.last_otp = -1  # 마지막으로 쓴 OTP 시간 칸 — 같은 칸·이전 칸 코드는 다시 못 쓴다
        self.sessions: dict[str, dict] = {}
        self.fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """하나라도 설정되면 인증 필요 (없으면 기존처럼 로컬 전용 무인증)."""
        return bool(self.admin_token or self.viewer_token or self.pw_hash)

    def config_errors(self) -> list[str]:
        """조용히 보안이 약해지는 설정 — 서버가 시작을 거부한다."""
        out = []
        if self.totp_secret and not self.pw_hash:
            out.append("QUANT_WEB_TOTP_SECRET 만 있고 비밀번호(QUANT_WEB_PASSWORD_HASH)가 없습니다 — 2단계 인증이 쓰이지 않습니다. "
                       "./run.sh auth-setup --password 로 비밀번호를 만들거나 TOTP 값을 지우세요")
        if self.totp_secret:
            try:
                _key(self.totp_secret)
            except (ValueError, TypeError):
                out.append("QUANT_WEB_TOTP_SECRET 형식이 잘못됐습니다 (base32)")
        return out

    def info(self) -> dict:
        return {"login_required": self.enabled, "password": bool(self.pw_hash), "mfa": bool(self.totp_secret),
                "viewer_token": bool(self.viewer_token), "errors": self.config_errors(),
                "note": "토큰(QUANT_WEB_TOKEN·VIEWER)은 2단계 인증을 거치지 않습니다 — 외부 공개 시 토큰은 길게, 노출 주의" if self.totp_secret and (self.admin_token or self.viewer_token) else None}

    def is_secure(self, remote: str, forwarded_proto: str | None) -> bool:
        """Secure 쿠키를 붙일지: 강제 설정이거나, 믿는 프록시가 https 라고 알려 줄 때만 (아무 클라이언트의 헤더는 믿지 않음)."""
        return self.secure_cookie or (remote in self.trusted_proxies and (forwarded_proto or "").lower() == "https")

    def role_for_token(self, tok: str | None) -> str | None:
        if not tok:
            return None
        if self.admin_token and hmac.compare_digest(tok, self.admin_token):
            return "admin"
        if self.viewer_token and hmac.compare_digest(tok, self.viewer_token):
            return "viewer"
        return None

    def session_role(self, sid: str | None) -> str | None:
        if not sid:
            return None
        with self._lock:
            s = self.sessions.get(sid)
            if not s:
                return None
            if s["exp"] < time.time():
                self.sessions.pop(sid, None)
                return None
            return s["role"]

    def locked(self, ip: str) -> int:
        now = time.time()
        f = [t for t in self.fails.get(ip, []) if now - t < LOCK_S]
        self.fails[ip] = f
        return int(LOCK_S - (now - f[0])) if len(f) >= LOCK_AFTER else 0

    def login(self, ip: str, password: str, otp: str | None) -> tuple[str | None, str]:
        """(세션 id, 메시지)."""
        if not self.pw_hash:
            return None, "비밀번호 로그인이 설정되지 않았습니다 (./run.sh auth-setup)"
        wait = self.locked(ip)
        if wait:
            return None, f"로그인 실패가 많아 {wait // 60 + 1}분 동안 잠금"
        ok = check_password(self.pw_hash, password or "")
        if ok and self.totp_secret:
            with self._lock:
                c = totp_counter(self.totp_secret, otp or "")
                if c is not None and c <= self.last_otp:
                    self.fails.setdefault(ip, []).append(time.time())
                    return None, "이미 사용한 인증 코드입니다 — OTP 앱에서 다음 코드가 나오면 다시 입력하세요"
                if c is None:
                    self.fails.setdefault(ip, []).append(time.time())
                    return None, "2단계 인증 코드가 맞지 않습니다 (OTP 앱의 6자리 · 휴대폰 시간 확인)"
                self.last_otp = c
        if not ok:
            self.fails.setdefault(ip, []).append(time.time())
            return None, "비밀번호가 맞지 않습니다"
        self.fails.pop(ip, None)
        sid = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v["exp"] > now}
            if len(self.sessions) > 200:  # 오래된 것부터 정리
                for k in sorted(self.sessions, key=lambda k: self.sessions[k]["exp"])[:50]:
                    self.sessions.pop(k, None)
            self.sessions[sid] = {"role": "admin", "exp": now + SESSION_TTL, "ip": ip, "created": now}
        return sid, "로그인됨"

    def logout(self, sid: str | None) -> None:
        with self._lock:
            self.sessions.pop(sid or "", None)


__all__ = ["Auth", "totp", "verify_totp", "totp_counter", "client_ip", "new_secret", "provisioning_uri", "hash_password", "check_password"]

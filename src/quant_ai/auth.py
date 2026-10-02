"""로그인 · 2단계 인증(TOTP) · 역할(관리자/읽기 전용) — 표준 라이브러리만.

설정 (.env, 모두 선택 — 아무것도 없으면 지금처럼 '이 PC 에서만' 접속):
  QUANT_WEB_PASSWORD_HASH   비밀번호 해시 (`./run.sh auth-setup` 이 만들어 줌 · 평문 QUANT_WEB_PASSWORD 도 가능하나 비권장)
  QUANT_WEB_TOTP_SECRET     2단계 인증 비밀값 (Google Authenticator·1Password 등 OTP 앱에 등록) — 있으면 로그인에 6자리 코드 필요
  QUANT_WEB_VIEWER_TOKEN    읽기 전용 토큰 (가족·동료에게 보여주기: 조회만, 긴급 정지·설정·주문 등 쓰기 불가)
  QUANT_WEB_TOKEN           (기존) 관리자 토큰 — 스크립트·자동화용

세션은 HttpOnly · SameSite=Strict 쿠키 (12시간) · 로그인 실패 5번이면 15분 잠금 (IP 별) · 로그인·실패는 감사 로그에.
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


def verify_totp(secret: str, code: str, t: float | None = None, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    now = time.time() if t is None else t
    return any(hmac.compare_digest(totp(secret, now + k * 30), code) for k in range(-window, window + 1))


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
        self.sessions: dict[str, dict] = {}
        self.fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """하나라도 설정되면 인증 필요 (없으면 기존처럼 로컬 전용 무인증)."""
        return bool(self.admin_token or self.viewer_token or self.pw_hash)

    def info(self) -> dict:
        return {"login_required": self.enabled, "password": bool(self.pw_hash), "mfa": bool(self.totp_secret),
                "viewer_token": bool(self.viewer_token)}

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
            ok = verify_totp(self.totp_secret, otp or "")
            if not ok:
                self.fails.setdefault(ip, []).append(time.time())
                return None, "2단계 인증 코드가 맞지 않습니다 (OTP 앱의 6자리 · 휴대폰 시간 확인)"
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


__all__ = ["Auth", "totp", "verify_totp", "new_secret", "provisioning_uri", "hash_password", "check_password"]

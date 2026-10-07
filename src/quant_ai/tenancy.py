"""v34 여러 사용자 — '지금 요청한 사람'(문맥)과 사람별 데이터 칸.

설계 (docs/MULTI_USER.md):
  · 시장 자료·AI 분석·연구 결과는 **모두가 함께 보는 것** (운영자가 한 번 수집·계산).
  · 목표 계획·내 계좌·관심종목·알림 기준·설정·투자 일지·모의투자 장부·채팅은 **사람마다 따로**.
  · 요청마다 서버가 로그인한 사람을 문맥(ContextVar)에 올려 두고, 저장소(ops.get_state/set_state)·장부 이름·캐시가
    그 문맥을 보고 자동으로 그 사람 칸을 쓴다 → 기존 기능 코드를 거의 고치지 않고 사람별로 나뉜다.
  · 문맥이 없으면(스케줄러·CLI·개인용 1인 모드) 예전 그대로 — 1인 사용자의 데이터는 손대지 않는다.
  · '소유자(owner)' 계정은 1인 모드 때의 데이터를 그대로 쓴다 (서비스로 바꿔도 내 기록이 사라지지 않게).

개인 데이터는 QUANT_DATA_KEY 가 있으면 DB 에 암호화해 저장한다 (Fernet · AES-128-CBC + HMAC).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
from collections.abc import Iterator
from contextvars import ContextVar

_CUR: ContextVar[dict | None] = ContextVar("quant_ai_user", default=None)

# 사람마다 따로 저장하는 상태 키 (ops.get_state/set_state 가 자동으로 'u:{id}:키' 로 바꾼다)
PERSONAL_KEYS = frozenset({
    "goal_plan",        # 목표 계획 · 적립 기록
    "accounts",         # 내 계좌 (직접 입력)
    "starred",          # 관심종목 · 묶음
    "user_prefs",       # 화면·알림 설정
    "user_profile",     # 투자 성향
    "thesis",           # 종목별 투자 근거
    "push_subs",        # 휴대폰 알림 구독
    "chat_sessions",    # AI 채팅 목록
    "usage",            # 요금제 사용량 (하루 단위)
    "onboarding",       # 시작 안내 진행
})
PERSONAL_PREFIXES = ("chat:", "cashflows:manual@", "cashflows:us-manual@", "cashflows:etf-dca@")


def current() -> dict | None:
    """지금 요청한 사람 {id, email, role, plan, owner} — 없으면 None (스케줄러·CLI·1인 모드)."""
    return _CUR.get()


def uid() -> int | None:
    u = _CUR.get()
    return int(u["id"]) if u and u.get("id") is not None else None


def scoped() -> bool:
    """사람별 칸을 써야 하나: 로그인한 사람이 있고 소유자(1인 모드 데이터 주인)가 아닐 때."""
    u = _CUR.get()
    return bool(u and u.get("id") is not None and not u.get("owner"))


def is_member() -> bool:
    u = _CUR.get()
    return bool(u and u.get("role") == "member")


@contextlib.contextmanager
def as_user(user: dict | None) -> Iterator[dict | None]:
    tok = _CUR.set(dict(user) if user else None)
    try:
        yield user
    finally:
        _CUR.reset(tok)


def b36(n: int) -> str:
    chars, out = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    n = int(n)
    while True:
        n, r = divmod(n, 36)
        out = chars[r] + out
        if n == 0:
            return out


def is_personal(key: str) -> bool:
    return key in PERSONAL_KEYS or key.startswith(PERSONAL_PREFIXES)


def state_key(key: str) -> str:
    """저장소 키 → 지금 사람의 칸. 이미 사람 칸 키('u:')면 그대로."""
    if key.startswith("u:") or not scoped() or not is_personal(key):
        return key
    return f"u:{b36(uid())}:{key}"


def user_prefix(user_id: int) -> str:
    return f"u:{b36(user_id)}:"


def book(base: str) -> str:
    """모의 장부 이름 → 사람별 장부 (예: manual → manual@1z). 장부 이름 칸이 16자라 36진수로 짧게."""
    if not scoped():
        return base
    return f"{base}@{b36(uid())}"


def personal_book(mode: str) -> str:
    """회원 요청이면 어떤 장부 이름이 와도 그 사람 장부로 (운영자의 AI 장부·실계좌 장부를 회원이 볼 수 없게).
    미국 장부(us-*) → us-manual@나 · 월 적립(etf-dca) → etf-dca@나 · 나머지(paper·shadow·live·manual) → manual@나."""
    if not scoped():
        return mode
    m = mode or "paper"
    if "@" in m and book_owner(m) == uid():
        return m
    base = m.split("@", 1)[0]
    if base.startswith("us-"):
        return book("us-manual")
    if base.startswith("etf-dca"):
        return book("etf-dca")
    return book("manual")


def book_owner(mode: str) -> int | None:
    """사람별 장부 이름에서 사람 번호 (manual@1z → 71). 공용 장부면 None."""
    if "@" not in (mode or ""):
        return None
    try:
        return int(mode.rsplit("@", 1)[1], 36)
    except ValueError:
        return None


def owner_value() -> int | None:
    """DB 행의 owner 칸에 넣을 값 — 회원이면 그 사람 번호, 1인 모드·소유자면 None."""
    return uid() if scoped() else None


def owner_filter(col):
    """SQLAlchemy 조건: 지금 사람의 행만 (회원 → owner = 나 · 1인 모드·소유자 → owner 없음)."""
    return col == uid() if scoped() else col.is_(None)


def cache_key(key: str) -> str:
    """화면 캐시 키 — 사람별 칸이면 앞에 사람 번호 (다른 사람 화면이 섞여 보이는 사고를 원천 차단)."""
    return f"@{b36(uid())}:{key}" if scoped() else key


# ------------------------------------------------------------------ 개인 데이터 암호화 (선택)
_FERNET = None
_FERNET_SRC: str | None = None


def _fernet():
    global _FERNET, _FERNET_SRC
    raw = os.environ.get("QUANT_DATA_KEY") or ""
    if not raw:
        return None
    if _FERNET is not None and _FERNET_SRC == raw:
        return _FERNET
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # cryptography 없으면 암호화 없이 (서버 시작 시 경고)
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())  # 아무 길이의 비밀값 → Fernet 키
    _FERNET, _FERNET_SRC = Fernet(key), raw
    return _FERNET


def encryption_on() -> bool:
    return _fernet() is not None


def seal(value: dict) -> dict:
    f = _fernet()
    if f is None:
        return value
    import json
    return {"_enc": f.encrypt(json.dumps(value, ensure_ascii=False, default=str).encode()).decode()}


def unseal(value: dict) -> dict:
    if not isinstance(value, dict) or "_enc" not in value:
        return value
    f = _fernet()
    if f is None:
        raise RuntimeError("암호화된 개인 데이터예요 — QUANT_DATA_KEY 가 필요해요 (예전에 쓰던 값 그대로)")
    import json
    return json.loads(f.decrypt(value["_enc"].encode()))


__all__ = ["current", "uid", "scoped", "is_member", "as_user", "state_key", "user_prefix", "book", "book_owner", "cache_key",
           "is_personal", "PERSONAL_KEYS", "personal_book", "owner_value", "owner_filter", "seal", "unseal", "encryption_on", "b36"]

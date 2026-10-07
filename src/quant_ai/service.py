"""v34 서비스 운영 정책 — 1인 모드 / 여러 사용자 모드 · 회원이 쓸 수 있는 기능 · 투자 정보 범위 · 요금제 한도 · 속도 제한.

QUANT_SERVICE_MODE
  personal (기본) : 지금까지처럼 혼자 쓰는 프로그램 (회원 없음 · 모든 기능)
  multi           : 회원 가입·로그인 · 사람마다 데이터 따로 · 회원은 '회원 기능'만 · 운영 기능은 운영자만

투자 정보 범위 (운영자 콘솔에서 바꿈 · 기본 none — 법률 검토 전 가장 안전한 쪽):
  none : 회원에게 AI 매수·매도 후보 · 종목별 AI 판단 · AI 성적을 보여 주지 않는다 (시세·뉴스·일정·목표·내 계좌·모의투자 도구만)
  info : 모든 회원에게 같은 AI 분석을 '정보'로 보여 준다 (불특정 다수 대상 — 유사투자자문업 신고 등 확인 후 켤 것)
  ※ 회원 한 사람에게 맞춘 매매 권유(1:1 자문)·대신 주문(일임)은 어느 범위에서도 열지 않는다.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime

from . import ops, tenancy

POLICY_KEY = "service_policy"
DEFAULT_POLICY = {"signup": "invite", "advice": "none", "require_verify": None, "brand": "Quant AI", "support": "",
                  "notice": "", "member_invites": True, "ref_reward_days": 0}

# 요금제별 한도 (하루 사용량은 한국 시간 날짜 기준 · 개수 한도는 동시에 가질 수 있는 수)
PLANS = {
    "free": {"title": "무료", "price": 0, "watch": 30, "alert_rules": 10, "accounts": 3, "chat": 5, "ai_explain": 10,
             "ticket": 50, "journal": 30},
    "pro": {"title": "프로", "price": 9900, "watch": 300, "alert_rules": 100, "accounts": 20, "chat": 100, "ai_explain": 100,
            "ticket": 500, "journal": 300},
}
QUOTA_NAMES = {"chat": "AI 채팅", "ai_explain": "AI 설명(뉴스·공시·종목 요약)", "ticket": "모의 주문", "journal": "투자 일지",
               "watch": "관심종목", "alert_rules": "가격 알림", "accounts": "내 계좌"}


def mode() -> str:
    return "multi" if (os.environ.get("QUANT_SERVICE_MODE") or "").strip().lower() == "multi" else "personal"


def multi() -> bool:
    return mode() == "multi"


def policy(app) -> dict:
    p = DEFAULT_POLICY | ops.get_state(app.engine, POLICY_KEY)
    if p.get("require_verify") is None:  # 메일을 보낼 수 있을 때만 이메일 확인을 요구 (못 보내면 가입자가 막힌다)
        from .mailer import configured
        p["require_verify"] = configured()
    return p


def set_policy(app, body: dict) -> dict:
    cur = ops.get_state(app.engine, POLICY_KEY)
    if "signup" in body:
        if body["signup"] not in ("open", "invite", "closed"):
            raise ValueError("가입 방식은 open / invite / closed")
        cur["signup"] = body["signup"]
    if "advice" in body:
        if body["advice"] not in ("none", "info"):
            raise ValueError("투자 정보 범위는 none / info")
        cur["advice"] = body["advice"]
    if "require_verify" in body:
        cur["require_verify"] = bool(body["require_verify"])
    if "member_invites" in body:
        cur["member_invites"] = bool(body["member_invites"])
    if "ref_reward_days" in body:
        d = int(body["ref_reward_days"] or 0)
        if not 0 <= d <= 90:
            raise ValueError("초대 보상은 0~90일")
        cur["ref_reward_days"] = d
    for k, n in (("brand", 40), ("support", 120), ("notice", 300)):
        if k in body:
            cur[k] = str(body[k] or "")[:n]
    cur["at"] = datetime.now(UTC).isoformat()
    ops.set_state(app.engine, POLICY_KEY, cur)
    return policy(app)


def advice_on(app) -> bool:
    """지금 요청한 사람에게 AI 매수·매도 판단을 보여 줘도 되나 (운영자·1인 모드는 항상)."""
    if not tenancy.is_member():
        return True
    return policy(app).get("advice") == "info"


def public_policy(app) -> dict:
    p = policy(app)
    return {"member_invites": p.get("member_invites", True), "ref_reward_days": p.get("ref_reward_days", 0), "mode": mode(), "signup": p["signup"], "advice": p["advice"], "brand": p["brand"], "support": p["support"],
            "notice": p["notice"], "require_verify": bool(p["require_verify"]), "plans": PLANS}


# ------------------------------------------------------------------ 회원이 쓸 수 있는 API (허용 목록 — 없으면 운영자 전용)
MEMBER_GET = frozenset({
    # 내 계정 · 서비스
    "/api/auth", "/api/me", "/api/me/sessions", "/api/me/export", "/api/service",
    # 시세 · 종목 · 뉴스 · 일정 (모두가 보는 정보)
    "/api/search", "/api/company", "/api/chart", "/api/t/home", "/api/t/stock", "/api/t/feed", "/api/t/market", "/api/t/quotes",
    "/api/t/intraday", "/api/t/community", "/api/news-board", "/api/news-search", "/api/news-impact", "/api/market-map",
    "/api/calendar", "/api/clock", "/api/quotes", "/api/alerts", "/api/weekly", "/api/freshness", "/api/profile",
    "/api/company-view", "/api/ensure", "/api/compare", "/api/stock", "/api/home5",
    # 나만의 것 (사람마다 따로 저장)
    "/api/goal", "/api/goal-home", "/api/accounts", "/api/watchlist", "/api/star", "/api/prefs", "/api/user-profile",
    "/api/thesis", "/api/myjournal", "/api/rules", "/api/ticket", "/api/ticket/book", "/api/holdings", "/api/start-guide",
    "/api/push/key", "/api/chat", "/api/discover", "/api/mistakes", "/api/t/portfolio", "/api/t/alerts", "/api/risk-simple",
    "/api/habit", "/api/together", "/api/club", "/api/explore",  # v35
})
MEMBER_GET_PREFIX = ("/api/news/", "/api/disclosure/", "/api/stock/")
MEMBER_POST = frozenset({
    "/api/star", "/api/watch-group", "/api/prefs", "/api/goal", "/api/accounts", "/api/rules", "/api/t/alerts", "/api/myjournal",
    "/api/thesis", "/api/user-profile", "/api/ticket", "/api/push/subscribe", "/api/push/unsubscribe", "/api/push/test",
    "/api/chat", "/api/chat/clear", "/api/news-explain", "/api/disclosure-explain", "/api/stock/digest",
    "/api/me", "/api/me/password", "/api/me/mfa", "/api/me/logout-others", "/api/me/delete", "/api/me/verify-resend",
    "/api/together",  # v35
})
# AI 가 매수·매도·확률을 말하는 것 — 투자 정보 범위가 info 일 때만 회원에게
ADVICE_GET = frozenset({"/api/signals2", "/api/signals2/stock", "/api/verdict", "/api/ai-card", "/api/ai-plain", "/api/explain",
                        "/api/analysis", "/api/ai-trust", "/api/ai-public", "/api/ai-context", "/api/scorecard", "/api/t/report",
                        "/api/pretrade"})
ADVICE_POST = frozenset({"/api/stock/digest"})
# 회원 응답에서 빼는 칸: 운영 정보(항상) · AI 판단(범위 none 일 때)
OPS_FIELDS = {"/api/home5": ("system",), "/api/t/home": (), "/api/start-guide": ()}
ADVICE_FIELDS = {
    "/api/t/home": ("picks", "trust"),
    "/api/home5": ("ai", "picks"),
    "/api/t/stock": ("ai", "verdict", "signal", "signals", "picks", "ai_view", "consensus"),
    "/api/stock": ("ai", "verdict", "signal", "consensus", "ai_view", "why", "decision", "explain"),
    "/api/watchlist": ("ai",),
    "/api/goal": ("ai_cap", "autopilot", "smallcap"),
}
ROW_ADVICE_FIELDS = ("signal", "ai", "action", "prob_up", "verdict", "ai_action", "consensus")


def member_can(method: str, path: str, advice: str) -> tuple[bool, str]:
    """회원이 이 API 를 써도 되나 → (가능, 막는 이유 코드)."""
    if method == "GET":
        if path in ADVICE_GET:
            return (advice == "info", "advice_off")
        ok = path in MEMBER_GET or path.startswith(MEMBER_GET_PREFIX) or path.startswith("/api/logo/")
        return (ok, "operator_only")
    if path in ADVICE_POST and advice != "info":
        return (False, "advice_off")
    return (path in MEMBER_POST, "operator_only")


def _strip_rows(obj, keys: tuple[str, ...]):
    if isinstance(obj, list):
        return [_strip_rows(x, keys) for x in obj]
    if isinstance(obj, dict):
        return {k: _strip_rows(v, keys) for k, v in obj.items() if k not in keys}
    return obj


def filter_for_member(path: str, out, advice: str):
    """회원에게 보내기 전에 운영 정보·(범위 none 이면) AI 판단 칸을 뺀다."""
    if not isinstance(out, dict):
        return out
    drop = set(OPS_FIELDS.get(path, ()))
    if advice != "info":
        drop |= set(ADVICE_FIELDS.get(path, ()))
    out = {k: v for k, v in out.items() if k not in drop}
    if advice != "info" and path in ("/api/watchlist", "/api/t/home", "/api/t/portfolio", "/api/holdings"):
        for k in ("rows", "items", "holdings", "positions"):
            if isinstance(out.get(k), list):
                out[k] = _strip_rows(out[k], ROW_ADVICE_FIELDS)
    return out


# ------------------------------------------------------------------ 요금제 한도
class QuotaError(ValueError):
    pass


def limits(plan: str | None = None) -> dict:
    u = tenancy.current() or {}
    return PLANS.get(plan or u.get("plan") or "free", PLANS["free"])


def _today() -> str:
    from datetime import timedelta
    return (datetime.now(UTC) + timedelta(hours=9)).date().isoformat()


def take(app, kind: str, n: int = 1) -> None:
    """하루 한도 사용 (회원만 · 운영자는 무제한). 넘으면 QuotaError."""
    u = tenancy.current()
    if not u or u.get("role") != "member":
        return
    lim = limits().get(kind)
    if lim is None:
        return
    st = ops.get_state(app.engine, "usage")
    day = _today()
    used = (st.get("n") or {}) if st.get("day") == day else {}
    if used.get(kind, 0) + n > lim:
        up = " · 프로 요금제는 더 많이 쓸 수 있어요" if (u.get("plan") or "free") == "free" else ""
        raise QuotaError(f"오늘 {QUOTA_NAMES.get(kind, kind)}를 다 썼어요 (하루 {lim}번){up} — 내일 다시 쓸 수 있어요")
    used = {**used, kind: used.get(kind, 0) + n}
    ops.set_state(app.engine, "usage", {"day": day, "n": used})


def check_count(kind: str, have: int) -> None:
    """개수 한도 (관심종목 · 가격 알림 · 계좌) — 새로 하나 더 만들 수 있나."""
    u = tenancy.current()
    if not u or u.get("role") != "member":
        return
    lim = limits().get(kind)
    if lim is not None and have >= lim:
        up = " · 프로 요금제는 더 많이" if (u.get("plan") or "free") == "free" else ""
        raise QuotaError(f"{QUOTA_NAMES.get(kind, kind)}는 {lim}개까지예요{up}")


def usage(app) -> dict:
    st = ops.get_state(app.engine, "usage")
    used = (st.get("n") or {}) if st.get("day") == _today() else {}
    lim = limits()
    return {"plan": (tenancy.current() or {}).get("plan", "free"), "limits": lim,
            "today": {k: used.get(k, 0) for k in ("chat", "ai_explain", "ticket", "journal")}}


# ------------------------------------------------------------------ 속도 제한 (사람마다 · 서버 메모리)
_BUCKET: dict[str, tuple[float, float]] = {}
_BLOCK = threading.Lock()
RATE, BURST = 8.0, 80.0  # 1초에 8번 · 한 번에 80번까지 (화면 하나가 API 를 여러 개 부른다)


def allow(key: str, cost: float = 1.0) -> bool:
    now = time.monotonic()
    with _BLOCK:
        tokens, t = _BUCKET.get(key, (BURST, now))
        tokens = min(BURST, tokens + (now - t) * RATE)
        if tokens < cost:
            _BUCKET[key] = (tokens, now)
            return False
        _BUCKET[key] = (tokens - cost, now)
        if len(_BUCKET) > 20000:
            _BUCKET.clear()
        return True


__all__ = ["advice_on", "mode", "multi", "policy", "set_policy", "public_policy", "member_can", "filter_for_member", "take", "check_count",
           "usage", "limits", "allow", "QuotaError", "PLANS", "MEMBER_GET", "MEMBER_POST", "ADVICE_GET"]

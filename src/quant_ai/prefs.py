"""사용자 설정 (서버 저장 — PC·휴대폰 어디서 열어도 같다).

  home    홈 화면 구성: 카드 숨기기 · 순서
  theme   다크/라이트 (v16 — 기기 간 동기화)
  widgets 종목 페이지 섹션 숨기기 · 순서 (v16)
  ui      쉬운 화면(easy, 기본) / 전체 화면(pro) · 시작 안내 숨김 (v19)
  notify  외부 알림: 종류별로 텔레그램/디스코드 · 웹 푸시 켜고 끄기 · 조용한 시간(밤에는 외부 알림 보류, 긴급은 예외)
           설정이 없으면 .env 기본값(QUANT_NOTIFY_KINDS 등)을 그대로 쓴다.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import ops

KEY = "user_prefs"
KINDS = ("price", "signal", "event", "disclosure", "news", "earnings", "result", "guardian", "ladder", "market", "job", "rule",
         "readiness", "power", "ops", "brief", "report")
HOME_CARDS = ("today", "readiness", "live", "setup", "proof", "market", "watch", "portfolio", "system")
KST = ZoneInfo("Asia/Seoul")


def get(engine) -> dict:
    p = ops.get_state(engine, KEY)
    return {"home": p.get("home") or {"hidden": [], "order": []}, "notify": p.get("notify") or {}, "theme": p.get("theme"),
            "widgets": p.get("widgets") or {}, "ui": {"mode": "easy", "guide_hidden": False} | (p.get("ui") or {}), "at": p.get("at")}


def _hhmm(v, default: str) -> str:
    v = str(v or default)
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
        raise ValueError("시각은 HH:MM (예: 23:00)")
    return v


def save(engine, body: dict) -> dict:
    cur = get(engine)
    if "home" in body:
        h = body["home"] or {}
        cur["home"] = {"hidden": [x for x in h.get("hidden") or [] if x in HOME_CARDS],
                       "order": [x for x in dict.fromkeys(h.get("order") or []) if x in HOME_CARDS]}
    if "notify" in body:
        n = body["notify"] or {}
        q = n.get("quiet") or {}
        cur["notify"] = {"external": {k: bool(v) for k, v in (n.get("external") or {}).items() if k in KINDS},
                         "push": {k: bool(v) for k, v in (n.get("push") or {}).items() if k in KINDS},
                         "quiet": {"on": bool(q.get("on")), "start": _hhmm(q.get("start"), "23:00"), "end": _hhmm(q.get("end"), "07:00")}}
    if "theme" in body:
        if body["theme"] not in ("dark", "light", None):
            raise ValueError("theme 은 dark/light")
        cur["theme"] = body["theme"]
    if "widgets" in body:  # 종목 페이지 섹션 숨기기/순서 (v16)
        w = body["widgets"] or {}
        ok = re.compile(r"^pf-[a-z]{2,12}$")
        cur["widgets"] = {"hidden": [x for x in w.get("hidden") or [] if ok.match(str(x))][:30],
                          "order": [x for x in dict.fromkeys(w.get("order") or []) if ok.match(str(x))][:30]}
    if "ui" in body:  # v19: 쉬운 화면 / 전체 화면
        u = body["ui"] or {}
        if u.get("mode", cur["ui"]["mode"]) not in ("easy", "pro"):
            raise ValueError("ui.mode 는 easy/pro")
        cur["ui"] = {"mode": u.get("mode", cur["ui"]["mode"]), "guide_hidden": bool(u.get("guide_hidden", cur["ui"]["guide_hidden"]))}
    cur["at"] = datetime.now(KST).isoformat()
    ops.set_state(engine, KEY, cur)
    return cur


def in_quiet(quiet: dict, now: datetime | None = None) -> bool:
    if not quiet or not quiet.get("on"):
        return False
    t = (now or datetime.now(KST)).astimezone(KST).strftime("%H:%M")
    s, e = quiet.get("start", "23:00"), quiet.get("end", "07:00")
    return (s <= t < e) if s < e else (t >= s or t < e)


def channel_on(notify: dict, channel: str, kind: str, default: bool) -> bool:
    """channel: external | push. 사용자가 이 종류를 명시했으면 그 값, 아니면 .env 기본값."""
    m = (notify or {}).get(channel) or {}
    return bool(m[kind]) if kind in m else default


__all__ = ["get", "save", "in_quiet", "channel_on", "KINDS", "HOME_CARDS"]

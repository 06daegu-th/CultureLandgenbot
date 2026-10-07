"""v35 함께 — 친구 초대 · '함께 모으기' 모임.

친구 초대
  · 회원마다 자기 초대 링크(90일 · 50명) — 가입 방식이 '초대'여도 회원 초대 링크로 가입할 수 있다 (운영자가 끌 수 있음)
  · 보상(선택): 운영 콘솔의 '초대 보상 일수' — 친구가 가입하면 초대한 사람과 친구 모두 프로 N일 (한 사람이 받는 보상은 1년까지)

함께 모으기 (모임)
  · 모임 만들기 → 8자리 참여 코드 공유 → 같이 목표를 향해 적립
  · 모임에서 보이는 것: 별명 · 내 목표 대비 진행률(%) · 연속 적립 · 이번 달 적립 여부 — **금액은 절대 보이지 않는다**
  · 진행률도 숨기고 연속 적립만 보이게 할 수 있다 · 모임은 최대 30명 · 한 사람 5개까지
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import ops, tenancy

KST = ZoneInfo("Asia/Seoul")
CLUB_MAX, MY_CLUBS_MAX, REWARD_CAP = 30, 5, 365
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 글자(0·O·1·I) 뺌


def _need_member() -> int:
    uid = tenancy.uid()
    if uid is None:
        raise ValueError("회원 로그인이 필요해요 (여러 사용자 모드)")
    return uid


# ------------------------------------------------------------------ 친구 초대
def referral(app, base_url: str = "", create: bool = False) -> dict:
    """내 초대 링크 · 초대한 친구 수 · 받은 보상."""
    from . import members, service
    uid = _need_member()
    pol = service.policy(app)
    st = ops.get_state(app.engine, "referral")
    refs = ops.get_state(app.engine, "referrals")
    out = {"enabled": pol.get("member_invites", True) and pol.get("signup") != "closed", "reward_days": int(pol.get("ref_reward_days") or 0),
           "joined": len(refs.get("list") or []), "granted_days": int(refs.get("granted") or 0), "link": None}
    if not out["enabled"]:
        out["why"] = "지금은 운영자가 새 가입을 막아 두었어요" if pol.get("signup") == "closed" else "운영자가 회원 초대를 꺼 두었어요"
        return out
    valid = st.get("token") and st.get("exp", "") > datetime.now(UTC).isoformat()
    if not valid and create:
        r = members.invite(app, uid, None, "free", 90, 50, base_url, ref=True)
        st = {"token": r["token"], "exp": (datetime.now(UTC) + timedelta(days=90)).isoformat()}
        ops.set_state(app.engine, "referral", st)
        valid = True
    if valid:
        out["link"] = f"{base_url}/#signup/{st['token']}"
        out["expires"] = st["exp"][:10]
    return out


def on_signup(app, by: int, new_user: int) -> dict:
    """회원 초대 링크로 누가 가입했을 때 (members.signup 이 부른다): 기록 + (켰으면) 둘 다 프로 N일."""
    from . import members, service
    days = int(service.policy(app).get("ref_reward_days") or 0)
    with tenancy.as_user({"id": by, "role": "member"}):
        refs = ops.get_state(app.engine, "referrals")
        lst = [*(refs.get("list") or []), {"id": new_user, "at": datetime.now(UTC).isoformat()[:10]}][-500:]
        granted = int(refs.get("granted") or 0)
        give = max(0, min(days, REWARD_CAP - granted))
        ops.set_state(app.engine, "referrals", {"list": lst, "granted": granted + give})
    if give:
        members.grant_pro(app, by, give, "친구 초대 보상")
    if days:
        members.grant_pro(app, new_user, days, "초대받아 가입")
    return {"referrer_days": give, "friend_days": days}


# ------------------------------------------------------------------ 함께 모으기
def _club(app, cid: str) -> dict:
    c = ops.get_state(app.engine, f"club:{cid}")
    if not c.get("id"):
        raise ValueError("없는 모임이에요")
    return c


def _save_club(app, c: dict) -> None:
    ops.set_state(app.engine, f"club:{c['id']}", c)


def _my_ids(app) -> list[str]:
    return list(ops.get_state(app.engine, "my_clubs").get("ids") or [])


def _set_my_ids(app, ids: list[str]) -> None:
    ops.set_state(app.engine, "my_clubs", {"ids": ids})


def _nick_default() -> str:
    u = tenancy.current() or {}
    return (u.get("name") or (u.get("email") or "회원").split("@")[0])[:16]


def _entry(uid: int, nick: str | None) -> dict:
    u = tenancy.current() or {}
    return {"uid": uid, "nick": (nick or _nick_default()).strip()[:16] or "회원", "joined": datetime.now(UTC).isoformat()[:10],
            "hide_pct": False, "o": bool(u.get("owner"))}  # o: 소유자 계정(1인 모드 데이터 칸을 씀)


def create_club(app, name: str, nick: str | None = None) -> dict:
    uid = _need_member()
    name = (name or "").strip()[:30]
    if len(name) < 2:
        raise ValueError("모임 이름은 2자 이상")
    mine = _my_ids(app)
    if len(mine) >= MY_CLUBS_MAX:
        raise ValueError(f"모임은 한 사람당 {MY_CLUBS_MAX}개까지예요")
    for _ in range(20):
        code = "".join(secrets.choice(CODE_CHARS) for _ in range(8))
        if not ops.get_state(app.engine, f"club_code:{code}").get("id"):
            break
    cid = secrets.token_hex(6)
    c = {"id": cid, "name": name, "code": code, "owner": uid, "created": datetime.now(UTC).isoformat()[:10],
         "members": [_entry(uid, nick)]}
    _save_club(app, c)
    ops.set_state(app.engine, f"club_code:{code}", {"id": cid})
    _set_my_ids(app, [*mine, cid])
    return view(app, cid)


def join_club(app, code: str, nick: str | None = None) -> dict:
    uid = _need_member()
    code = (code or "").strip().upper().replace("-", "").replace(" ", "")
    ref = ops.get_state(app.engine, f"club_code:{code}") if len(code) == 8 else {}
    if not ref.get("id"):
        raise ValueError("참여 코드가 맞지 않아요 — 8자리 코드를 다시 확인해 주세요")
    c = _club(app, ref["id"])
    if any(m["uid"] == uid for m in c["members"]):
        return view(app, c["id"])
    if len(c["members"]) >= CLUB_MAX:
        raise ValueError(f"모임 인원이 가득 찼어요 ({CLUB_MAX}명)")
    mine = _my_ids(app)
    if len(mine) >= MY_CLUBS_MAX:
        raise ValueError(f"모임은 한 사람당 {MY_CLUBS_MAX}개까지예요")
    c["members"].append(_entry(uid, nick))
    _save_club(app, c)
    _set_my_ids(app, [*mine, c["id"]])
    return view(app, c["id"])


def leave_club(app, cid: str, uid: int | None = None) -> dict:
    """나가기 — 모임장이 나가면 가장 먼저 들어온 사람이 모임장 · 아무도 없으면 모임 삭제."""
    uid = uid if uid is not None else _need_member()
    try:
        c = _club(app, cid)
    except ValueError:
        c = None
    if c is not None:
        c["members"] = [m for m in c["members"] if m["uid"] != uid]
        if not c["members"]:
            ops.set_state(app.engine, f"club:{cid}", {})
            ops.set_state(app.engine, f"club_code:{c['code']}", {})
        else:
            if c["owner"] == uid:
                c["owner"] = c["members"][0]["uid"]
            _save_club(app, c)
    if tenancy.uid() == uid:
        _set_my_ids(app, [x for x in _my_ids(app) if x != cid])
    return {"ok": True}


def set_me(app, cid: str, nick: str | None = None, hide_pct: bool | None = None) -> dict:
    uid = _need_member()
    c = _club(app, cid)
    me = next((m for m in c["members"] if m["uid"] == uid), None)
    if me is None:
        raise ValueError("이 모임 회원이 아니에요")
    if nick is not None and nick.strip():
        me["nick"] = nick.strip()[:16]
    if hide_pct is not None:
        me["hide_pct"] = bool(hide_pct)
    _save_club(app, c)
    return view(app, cid)


def _member_status(app, m: dict, today) -> dict:
    """한 사람의 공개 가능한 정보만: 진행률(%) · 연속 적립 · 이번 달 적립 — 금액은 절대 내보내지 않는다."""
    from .goal import get
    from .habit import streak
    with tenancy.as_user({"id": m["uid"], "role": "member", "owner": bool(m.get("o"))}):
        try:
            g = get(app)
        except Exception:  # noqa: BLE001 - 암호화 키 문제 등 → 그 사람만 '-'
            g = {}
    if not g.get("goal"):
        return {"has_goal": False}
    st = streak(g, today)
    hist = g.get("history") or {}
    last = list(hist.values())[-1] if hist else None
    pct = round(min(last["total"] / g["goal"], 9.99), 4) if last and g.get("goal") else None
    return {"has_goal": True, "pct": None if m.get("hide_pct") else pct, "streak": st["current"], "best": st["best"],
            "this_month": st["this_month"], "years": g.get("target_years")}


def view(app, cid: str) -> dict:
    uid = _need_member()
    c = _club(app, cid)
    if not any(m["uid"] == uid for m in c["members"]):
        raise ValueError("이 모임 회원이 아니에요")
    today = datetime.now(KST).date()
    rows = []
    for m in c["members"]:
        s = _member_status(app, m, today)
        rows.append({"nick": m["nick"], "me": m["uid"] == uid, "owner": m["uid"] == c["owner"], "joined": m["joined"],
                     "hide_pct": bool(m.get("hide_pct")), **s})
    with_goal = [r for r in rows if r.get("has_goal")]
    done = sum(1 for r in with_goal if r["this_month"])
    rows.sort(key=lambda r: (-(r.get("streak") or 0), -(r.get("pct") or 0)))
    return {"id": c["id"], "name": c["name"], "code": c["code"], "is_owner": c["owner"] == uid, "created": c["created"],
            "n": len(rows), "rows": rows, "month_done": done, "month_total": len(with_goal),
            "month_rate": round(done / len(with_goal), 3) if with_goal else None,
            "streak_sum": sum(r.get("streak") or 0 for r in with_goal),
            "note": "모임에는 금액이 보이지 않아요 — 각자 목표 대비 진행률(%)과 연속 적립만 보여요"}


def overview(app, base_url: str = "") -> dict:
    """'함께' 화면: 내 초대 링크 · 내 모임들 요약."""
    from . import service
    if not service.multi() or tenancy.uid() is None:
        return {"available": False, "why": "여러 사용자 모드(회원 로그인)에서 쓸 수 있어요"}
    clubs = []
    keep = []
    for cid in _my_ids(app):
        try:
            v = view(app, cid)
        except ValueError:
            continue
        keep.append(cid)
        clubs.append({"id": v["id"], "name": v["name"], "n": v["n"], "month_rate": v["month_rate"], "month_done": v["month_done"],
                      "month_total": v["month_total"], "code": v["code"], "is_owner": v["is_owner"]})
    if keep != _my_ids(app):
        _set_my_ids(app, keep)
    return {"available": True, "referral": referral(app, base_url), "clubs": clubs, "max_clubs": MY_CLUBS_MAX, "club_max": CLUB_MAX}


def purge_user(app, user_id: int) -> None:
    """탈퇴할 때: 그 사람이 들어간 모임에서 빼기 (사람 칸이 지워지기 전에 불러야 한다)."""
    with tenancy.as_user({"id": user_id, "role": "member"}):
        ids = _my_ids(app)
        for cid in ids:
            leave_club(app, cid, user_id)


def action(app, body: dict, base_url: str = "") -> dict:
    a = str(body.get("action") or "")
    if a == "invite_link":
        return {"ok": True, "referral": referral(app, base_url, create=True)}
    if a == "create":
        return {"ok": True, "club": create_club(app, str(body.get("name") or ""), body.get("nick"))}
    if a == "join":
        return {"ok": True, "club": join_club(app, str(body.get("code") or ""), body.get("nick"))}
    if a == "leave":
        return leave_club(app, str(body.get("id") or ""))
    if a == "me":
        return {"ok": True, "club": set_me(app, str(body.get("id") or ""), body.get("nick"), body.get("hide_pct"))}
    raise ValueError("action 은 invite_link / create / join / leave / me")


__all__ = ["referral", "on_signup", "create_club", "join_club", "leave_club", "set_me", "view", "overview", "action", "purge_user"]

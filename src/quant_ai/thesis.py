"""종목별 투자 논리 (Thesis) — 왜 샀는지 · 무엇이 틀리면 파는지 · 목표/무효화 가격 · 점검일.

사람이 쓰는 기록이다 (AI 판단과 따로). 가격이 목표·무효화에 닿거나 점검일이 되면 Action Center 와 알림으로 알려준다.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from . import ops

KEY = "thesis"


def all_(engine) -> dict:
    return dict(ops.get_state(engine, KEY).get("items") or {})


def get(engine, symbol: str) -> dict | None:
    return all_(engine).get(symbol)


def save(engine, symbol: str, body: dict) -> dict:
    sym = (symbol or "").strip().upper()
    if not re.fullmatch(r"\d{6}|[A-Z][A-Z.\-]{0,9}", sym):
        raise ValueError("종목 코드 형식이 아닙니다")
    items = all_(engine)
    if body.get("delete"):
        items.pop(sym, None)
        ops.set_state(engine, KEY, {"items": items})
        return {"symbol": sym, "deleted": True}

    def num(k):
        v = body.get(k)
        if v in (None, ""):
            return None
        v = float(v)
        if v <= 0:
            raise ValueError(f"{k} 는 0 보다 커야 합니다")
        return v
    target, stop, entry = num("target"), num("stop"), num("entry_price")
    if target and stop and stop >= target:
        raise ValueError("무효화(손절) 가격은 목표보다 낮아야 합니다")
    review = (body.get("review_date") or "").strip() or None
    if review:
        date.fromisoformat(review)
    why = str(body.get("why") or "").strip()[:1000]
    if not why:
        raise ValueError("'왜 샀는지'를 적어 주세요")
    prev = items.get(sym) or {}
    now = datetime.now(UTC).isoformat()
    items[sym] = {"why": why, "sell_if": str(body.get("sell_if") or "").strip()[:1000], "target": target, "stop": stop,
                  "entry_price": entry, "review_date": review, "created_at": prev.get("created_at") or now, "updated_at": now,
                  "history": (prev.get("history") or [])[-9:] + ([{"at": prev["updated_at"], "why": prev["why"]}] if prev else [])}
    ops.set_state(engine, KEY, {"items": items})
    return {"symbol": sym, **items[sym]}


def breaches(app, today: date | None = None) -> list[dict]:
    """목표 도달 · 무효화 이탈 · 점검일 도래."""
    items = all_(app.engine)
    if not items:
        return []
    today = today or datetime.now(UTC).date()
    bars, _ = app._all_bars()
    live = ops.get_state(app.engine, "live_quotes")
    out = []
    for sym, t in items.items():
        from .pricenow import resolve
        px = resolve(sym, bars.get(sym), live.get(sym))["price"]
        if px is not None and t.get("stop") and px <= t["stop"]:
            out.append({"symbol": sym, "kind": "stop", "message": f"무효화 가격 {t['stop']:,.2f} 이탈 (현재 {px:,.2f}) — 매도 조건 점검", "price": px})
        elif px is not None and t.get("target") and px >= t["target"]:
            out.append({"symbol": sym, "kind": "target", "message": f"목표 {t['target']:,.2f} 도달 (현재 {px:,.2f}) — 일부 이익 실현·논리 갱신", "price": px})
        if t.get("review_date") and date.fromisoformat(t["review_date"]) <= today:
            out.append({"symbol": sym, "kind": "review", "message": f"투자 논리 점검일 ({t['review_date']})", "price": px})
    return out


def alert(app) -> int:
    from .alerts import push
    n = 0
    day = datetime.now(UTC).date().isoformat()
    for b in breaches(app):
        n += push(app.engine, "rule", f"투자 논리: {b['symbol']} {({'stop': '무효화', 'target': '목표', 'review': '점검'})[b['kind']]}",
                  b["message"], level="warn" if b["kind"] == "stop" else "info", symbol=b["symbol"],
                  link=f"#analysis/{b['symbol']}", dedupe=f"thesis:{b['symbol']}:{b['kind']}:{day}") is not None
    return n


__all__ = ["get", "save", "breaches", "alert", "all_"]

"""사이트 알림 — 토스트 · 알림센터 · 데스크톱 알림의 원천.

알림은 DB(alerts)에 쌓이고, 대시보드가 몇 초마다 새 알림을 가져가 토스트로 띄운다.
같은 알림은 dedupe 키로 한 번만 만든다. 중요한 것(보유 종목 ±5%, HALTED, 강등)만 외부 알림(텔레그램 등)으로도 보낸다.

만드는 곳
  price_watch  (장중 2~3분)  급등·급락: 전일 대비 ±3/5/10% 돌파, 20분 안 ±2% 급변 — 보유·관심·코어·해외 관심 종목
  alert_scan   (2분)        새 AI 신호(방향이 바뀐 BUY/SELL) · 새 공시 · 중요 뉴스 · 예측 채점 결과 · 실적 D-1 ·
                              자동 감시 HALTED/해제
  market_pulse (1시간)      시장 상태 RISK ON/OFF 전환 (+ 24시간 관제실의 시장 흐름 기록)
  ladder / scheduler        검증 사다리 승격·강등 · 작업 연속 실패
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import ops
from .data.db import session_scope
from .data.models import AlertRecord, ConsensusRecord, Disclosure, Instrument, NewsArticle

log = logging.getLogger(__name__)
KEEP = 3000
DAY_LEVELS = (0.03, 0.05, 0.10)
FAST_MOVE = 0.02
FAST_WINDOW = timedelta(minutes=20)


# 외부로도 보낼 알림 (앱이 시작할 때 configure): 텔레그램·디스코드 / 웹 푸시
# (아침 브리핑·일일 리포트는 reports.run_if_due 가 전문을 따로 보내므로 여기엔 넣지 않는다 — 중복 방지)
ROUTE = {"notifier": None, "kinds": {"rule", "earnings"}, "push": None,
         "push_kinds": {"price", "rule", "signal", "earnings", "guardian", "ladder", "brief", "report"}}


def configure(notifier=None, kinds: set[str] | None = None, push_sender=None, push_kinds: set[str] | None = None) -> None:
    if notifier is not None:
        ROUTE["notifier"] = notifier
    if kinds is not None:
        ROUTE["kinds"] = set(kinds)
    if push_sender is not None:
        ROUTE["push"] = push_sender
    if push_kinds is not None:
        ROUTE["push_kinds"] = set(push_kinds)


def _route(kind: str, level: str, title: str, body: str | None, link: str | None, engine=None) -> None:
    from .prefs import channel_on, get, in_quiet
    notify = {}
    if engine is not None:
        try:
            notify = get(engine)["notify"]
        except Exception as e:  # noqa: BLE001 - 설정을 못 읽으면 .env 기본값
            log.warning("알림 설정 읽기 실패: %s", e)
    urgent = level in ("bad", "critical")
    if in_quiet(notify.get("quiet") or {}) and not urgent:
        return  # 조용한 시간: 외부로는 보내지 않는다 (사이트 알림센터에는 이미 저장됨) · 긴급은 예외
    from . import tenancy
    n = None if tenancy.scoped() else ROUTE.get("notifier")  # v34: 회원 알림은 운영자 텔레그램으로 보내지 않는다 (그 사람 휴대폰 푸시만)
    if n is not None and channel_on(notify, "external", kind, kind in ROUTE["kinds"]):
        try:
            n.send(f"{title}\n{body}" if body else title, {"bad": "critical", "warn": "warn"}.get(level, "info"))
        except Exception as e:  # noqa: BLE001 - 외부 알림 실패가 알림 저장을 막으면 안 됨
            log.warning("외부 알림 실패: %s", e)
    sender = ROUTE.get("push")
    if sender is not None and (channel_on(notify, "push", kind, kind in ROUTE["push_kinds"]) or urgent):
        try:
            sender(title, body or "", link or "#control")
        except Exception as e:  # noqa: BLE001
            log.warning("웹 푸시 실패: %s", e)


def push(engine, kind: str, title: str, body: str | None = None, level: str = "info", symbol: str | None = None,
         link: str | None = None, dedupe: str | None = None, data: dict | None = None,
         now: datetime | None = None, route: bool = True) -> int | None:
    """알림 하나 추가. 같은 dedupe 가 이미 있으면 None. 설정된 종류는 텔레그램·웹 푸시로도 보낸다.
    v34: 회원 문맥(tenancy)에서 부르면 그 사람에게만 보이는 알림 (휴대폰 푸시도 그 사람 기기로만)."""
    rid = _store(engine, kind, title, body, level, symbol, link, dedupe, data, now)
    if rid is not None and route:
        _route(kind, level, title, body, link, engine)
    return rid


def _store(engine, kind, title, body, level, symbol, link, dedupe, data, now) -> int | None:
    now = now or datetime.now(UTC)
    try:
        with session_scope(engine) as s:
            if dedupe and s.scalar(select(AlertRecord.id).where(AlertRecord.dedupe == dedupe)):
                return None
            from . import tenancy
            rec = AlertRecord(ts=now, kind=kind, level=level, title=title[:300], body=(body or "")[:1000] or None,
                              symbol=symbol, link=link, dedupe=dedupe[:160] if dedupe else None, data=data, owner=tenancy.owner_value())
            s.add(rec)
            s.flush()
            rid = rec.id
            if rid % 200 == 0:  # 가끔 오래된 알림 정리
                cut = s.scalar(select(AlertRecord.id).order_by(AlertRecord.id.desc()).offset(KEEP).limit(1))
                if cut:
                    s.query(AlertRecord).filter(AlertRecord.id <= cut).delete()
            return rid
    except IntegrityError:  # 동시에 같은 dedupe
        return None


# v34: 회원에게도 보여 주는 공용 알림 (시장 전체 소식) — 운영 알림(긴급 정지·작업 실패·장부·AI 승격 등)은 운영자만
PUBLIC_KINDS = ("market", "disclosure", "news", "earnings")


def _visible(public_kinds: tuple[str, ...] | None = None):
    """지금 사람이 볼 수 있는 알림 조건: 회원 → 내 알림 + 공용 알림 · 운영자(1인 모드) → 회원 개인 알림만 빼고 전부."""
    from sqlalchemy import or_

    from . import tenancy
    if tenancy.scoped():
        return or_(AlertRecord.owner == tenancy.uid(),
                   (AlertRecord.owner.is_(None)) & AlertRecord.kind.in_(public_kinds or PUBLIC_KINDS))
    return AlertRecord.owner.is_(None)


def recent(engine, after: int = 0, limit: int = 50, public_kinds: tuple[str, ...] | None = None) -> dict:
    with session_scope(engine) as s:
        vis = _visible(public_kinds)
        q = select(AlertRecord).where(vis).order_by(AlertRecord.id.desc()).limit(limit)
        if after:
            q = q.where(AlertRecord.id > after)
        rows = s.scalars(q).all()
        last = s.scalar(select(func.max(AlertRecord.id)).where(vis)) or 0
        return {"last_id": last, "items": [{"id": r.id, "ts": r.ts.isoformat() if r.ts else None, "kind": r.kind,
                                            "level": r.level, "title": r.title, "body": r.body, "symbol": r.symbol,
                                            "link": r.link, "data": r.data} for r in rows]}


# ---------------------------------------------------------------- 관심 대상
def member_focus(app) -> dict[str, set[str]]:
    """v34 회원의 '내 종목': 내 모의 장부 보유 · 내 계좌 보유 · 별표 관심 · 내 알림 규칙 (운영자 장부·AI 코어는 섞지 않는다)."""
    from . import accounts
    from .ux import starred
    out: dict[str, set[str]] = {}
    for m in ("manual", "us-manual"):
        try:
            for sym, p in app.load_portfolio(m).positions.items():
                if p.qty:
                    out.setdefault(sym, set()).add("보유")
        except Exception:  # noqa: BLE001, S112
            continue
    for a in accounts.load(app.engine):
        for h in a.get("holdings") or []:
            sym = str(h.get("symbol") or "")
            if sym:
                out.setdefault(sym, set()).add("보유")
    for sym in starred(app):
        out.setdefault(sym, set()).add("관심")
    for r in active_rules(app.engine):
        if r["active"]:
            out.setdefault(r["symbol"], set()).add("규칙")
    return out


def focus_symbols(app) -> dict[str, set[str]]:
    """알림 대상: 보유 · 관심(내가 본 종목) · 코어 · 해외 관심 종목 → {종목: {태그}}."""
    from . import tenancy
    from .actions import watch_symbols
    from .analytics import main_mode
    if tenancy.scoped():
        return member_focus(app)
    out: dict[str, set[str]] = {}
    mode = main_mode(app)
    for m in {mode, "live"}:
        try:
            for sym in app.load_portfolio(m).positions:
                out.setdefault(sym, set()).add("보유")
        except Exception:  # noqa: BLE001, S112 - 장부가 없을 수 있다
            continue
    for sym in watch_symbols(app):
        out.setdefault(sym, set()).add("관심")
    for sym in (ops.get_state(app.engine, f"cs-plan:{mode}").get("core") or [])[:20]:
        out.setdefault(sym, set()).add("코어")
    for sym in app.load_portfolio("us-paper").positions if hasattr(app, "load_portfolio") else []:
        out.setdefault(sym, set()).add("미국 장부")
    return out


def _names(engine) -> dict[str, str]:
    with session_scope(engine) as s:
        return {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument))}


def _open_markets(now: datetime) -> set[str]:
    from .clock import MARKETS, Phase
    return {"KR" if k == "KRX" else k for k, m in MARKETS.items() if m.phase(now) is Phase.OPEN}


# ---------------------------------------------------------------- 급등·급락
# ---------------------------------------------------------------- 종목별 알림 규칙
RULE_KINDS = {"above": "목표가 도달", "below": "손절가 도달", "move": "등락률", "volume": "거래량 급증"}


def active_rules(engine, all_owners: bool = False) -> list[dict]:
    """알림 규칙. v34: 회원은 자기 규칙만 · 운영자(1인 모드)는 자기 규칙만 · all_owners 는 감시 작업용(모두)."""
    from . import tenancy
    from .data.models import AlertRule
    with session_scope(engine) as s:
        q = select(AlertRule).order_by(AlertRule.id.desc())
        if not all_owners:
            q = q.where(tenancy.owner_filter(AlertRule.owner))
        return [{"id": r.id, "symbol": r.symbol, "kind": r.kind, "value": r.value, "note": r.note, "repeat": r.repeat,
                 "active": r.active, "fired_at": r.fired_at.isoformat() if r.fired_at else None,
                 "created_at": r.created_at.isoformat() if r.created_at else None, "owner": r.owner}
                for r in s.scalars(q)]


def add_rule(engine, symbol: str, kind: str, value: float, note: str | None = None, repeat: bool = False,
             max_rules: int = 200) -> dict:
    from . import tenancy
    from .data.models import AlertRule
    if kind not in RULE_KINDS:
        raise ValueError(f"알 수 없는 규칙: {kind}")
    value = float(value)
    if value <= 0:
        raise ValueError("값은 0 보다 커야 합니다")
    mine = tenancy.owner_filter(AlertRule.owner)
    with session_scope(engine) as s:
        same = s.query(AlertRule).filter(mine, AlertRule.active.is_(True), AlertRule.symbol == symbol, AlertRule.kind == kind,
                                         AlertRule.value == value).first()
        if same is not None:  # 같은 규칙을 두 번 누른 경우 — 새로 만들지 않는다
            return {"id": same.id, "duplicate": True}
        if s.query(AlertRule).filter(mine, AlertRule.active.is_(True)).count() >= max_rules:
            raise ValueError(f"활성 알림 규칙은 {max_rules}개까지예요")
        r = AlertRule(symbol=symbol, kind=kind, value=value, note=(note or "")[:200] or None, active=True,
                      repeat=bool(repeat), created_at=datetime.now(UTC), owner=tenancy.owner_value())
        s.add(r)
        s.flush()
        return {"id": r.id}


def update_rule(engine, rule_id: int, active: bool | None = None, delete: bool = False) -> dict:
    from . import tenancy
    from .data.models import AlertRule
    with session_scope(engine) as s:
        r = s.get(AlertRule, int(rule_id))
        if r is None or r.owner != tenancy.owner_value():  # v34: 남의 규칙은 '없는 규칙' (있는지조차 알려 주지 않음)
            raise ValueError("규칙 없음")
        if delete:
            s.delete(r)
            return {"deleted": rule_id}
        if active is not None:
            r.active = bool(active)
            if active:
                r.fired_at = None
        return {"id": rule_id, "active": r.active}


def _session_fraction(sym: str, now: datetime) -> float:
    """장 시작 후 지난 비율 (거래량 급증 판단: 누적 거래량 ÷ (평균 × 비율))."""
    from .clock import MARKETS
    m = MARKETS["KRX" if sym.isdigit() else "US"]
    loc = m.local(now)
    o = loc.replace(hour=m.open_time.hour, minute=m.open_time.minute, second=0, microsecond=0)
    c = loc.replace(hour=m.close_time.hour, minute=m.close_time.minute, second=0, microsecond=0)
    total = (c - o).total_seconds()
    return max(0.05, min(1.0, (loc - o).total_seconds() / total)) if total > 0 else 1.0


def _avg_volume(engine, symbols: list[str], n: int = 20) -> dict[str, float]:
    from .data.models import PriceBar
    out = {}
    with session_scope(engine) as s:
        for sym in symbols:
            vs = [v for (v,) in s.execute(select(PriceBar.volume).where(PriceBar.symbol == sym, PriceBar.interval == "1d")
                                          .order_by(PriceBar.ts.desc()).limit(n)) if v]
            if vs:
                out[sym] = sum(vs) / len(vs)
    return out


def evaluate_rules(app, quotes: dict[str, dict], now: datetime | None = None, names: dict | None = None) -> int:
    """시세가 들어올 때마다(2.5분 · 또는 실시간 체결) 규칙 확인 → 알림 + 외부 전송."""
    from .data.models import AlertRule
    now = now or datetime.now(UTC)
    from . import tenancy
    rules = [r for r in active_rules(app.engine, all_owners=True) if r["active"] and r["symbol"] in quotes]
    if not rules:
        return 0
    names = names or _names(app.engine)
    vol_avg = _avg_volume(app.engine, sorted({r["symbol"] for r in rules if r["kind"] == "volume"}))
    fired = 0
    day = now.date().isoformat()
    for r in rules:
        q = quotes[r["symbol"]]
        px, chg = q.get("price"), q.get("chg_pct")
        if r["fired_at"] and r["repeat"] and r["fired_at"][:10] == day:
            continue  # 반복 규칙은 하루 한 번
        hit, why = False, ""
        if r["kind"] == "above" and px and px >= r["value"]:
            hit, why = True, f"현재 {px:,.2f} ≥ 목표 {r['value']:,.2f}"
        elif r["kind"] == "below" and px and px <= r["value"]:
            hit, why = True, f"현재 {px:,.2f} ≤ 기준 {r['value']:,.2f}"
        elif r["kind"] == "move" and chg is not None and abs(chg) * 100 >= r["value"]:
            hit, why = True, f"전일 대비 {chg:+.2%} (기준 ±{r['value']:g}%)"
        elif r["kind"] == "volume" and q.get("volume") and vol_avg.get(r["symbol"]):
            ratio = q["volume"] / (vol_avg[r["symbol"]] * _session_fraction(r["symbol"], now))
            if ratio >= r["value"]:
                hit, why = True, f"거래량 평소의 {ratio:.1f}배 (기준 {r['value']:g}배)"
        if not hit:
            continue
        name = names.get(r["symbol"], r["symbol"])
        with tenancy.as_user({"id": r["owner"], "role": "member"} if r.get("owner") else None):  # v34: 규칙 주인에게만
            rid = push(app.engine, "rule", f"{name} {RULE_KINDS[r['kind']]}", why + (f" · {r['note']}" if r["note"] else ""),
                       level="warn", symbol=r["symbol"], link=f"#analysis/{r['symbol']}",
                       dedupe=f"rule:{r['id']}:{day}", data={"rule": r["id"], "dir": "up" if (chg or 0) >= 0 else "down"}, now=now)
        if rid is not None:
            fired += 1
            with session_scope(app.engine) as s:
                rr = s.get(AlertRule, r["id"])
                rr.fired_at = now
                if not rr.repeat:
                    rr.active = False
    return fired


def watch_list(app, markets: set[str]) -> tuple[dict[str, set[str]], list[str]]:
    """감시 대상: 보유·관심·코어 + 규칙이 걸린 종목, 열려 있는 시장만."""
    focus = focus_symbols(app)
    for r in active_rules(app.engine, all_owners=True):  # 규칙이 걸린 종목은 관심 종목이 아니어도 본다 (v34: 모든 회원 규칙)
        if r["active"]:
            focus.setdefault(r["symbol"], set()).add("규칙")
    import os
    cap = int(os.environ.get("QUANT_WATCH_MAX") or 50)  # v34: 회원이 많으면 늘린다 (무료 시세 출처 부담 주의)
    syms = [s for s in focus if ("KR" if s.isdigit() else "US") in markets]
    syms.sort(key=lambda s: 0 if focus[s] & {"규칙", "보유"} else 1)  # 누군가 기준을 걸어 둔 종목·보유가 먼저 (잘리지 않게)
    return focus, syms[:cap]


def price_watch(app, now: datetime | None = None, fetchers: dict | None = None, markets: set[str] | None = None) -> dict:
    """2.5분마다 무료 시세를 받아 알림 (증권사 실시간 체결이 켜져 있으면 그쪽이 더 빠르다)."""
    from .data.live_quotes import fetch_quotes
    now = now or datetime.now(UTC)
    markets = _open_markets(now) if markets is None else markets
    focus, syms = watch_list(app, markets)
    if not syms:
        return {"checked": 0, "alerts": 0}
    return ingest_quotes(app, fetch_quotes(syms, fetchers), now, focus)


HIST_EVERY = timedelta(seconds=60)


def ingest_quotes(app, quotes: dict[str, dict], now: datetime | None = None, focus: dict | None = None) -> dict:
    """시세 묶음 → 화면용 실시간 시세 저장 + 급등락·급변 알림 + 종목별 규칙. (무료 폴링 · KIS 실시간 공용)"""
    now = now or datetime.now(UTC)
    if not quotes:
        return {"checked": 0, "alerts": 0}
    focus = focus if focus is not None else focus_symbols(app)
    names = _names(app.engine)
    state = ops.get_state(app.engine, "live_quotes")
    day = (now + timedelta(hours=9)).date().isoformat() if any(s.isdigit() for s in quotes) else now.date().isoformat()
    n = 0
    for sym, q in quotes.items():
        if not q.get("price"):
            continue
        prev = state.get(sym) or {}
        hist = [h for h in prev.get("hist", []) if now - pd.Timestamp(h[0]).to_pydatetime() <= timedelta(hours=2)]
        if not hist or now - pd.Timestamp(hist[-1][0]).to_pydatetime() >= HIST_EVERY:  # 체결마다가 아니라 1분 간격 표본
            hist.append([now.isoformat(), q["price"]])
        state[sym] = {**q, "hist": hist[-40:], "name": names.get(sym, sym), "tags": sorted(focus.get(sym, []))}
        name, tags = names.get(sym, sym), focus.get(sym, set())
        tag = "·".join(sorted(tags))
        chg = q.get("chg_pct")
        px = f"{q['price']:,.0f}원" if sym.isdigit() else f"${q['price']:,.2f}"
        if chg is not None:
            for lv in DAY_LEVELS:
                if abs(chg) >= lv:
                    up = chg > 0
                    rid = push(app.engine, "price", f"{name} {'급등' if up else '급락'} {chg:+.1%}",
                               f"{px} · 전일 대비 {'+' if up else '−'}{lv:.0%} 돌파 ({tag})", level="warn" if not up else "info",
                               symbol=sym, link=f"#analysis/{sym}", dedupe=f"px:{sym}:{day}:{'u' if up else 'd'}{lv}",
                               data={"dir": "up" if up else "down", "chg_pct": chg, "price": q["price"]}, now=now)
                    n += rid is not None
                    if rid and lv >= 0.05 and "보유" in tags:
                        app.notifier.send(f"보유 종목 {name} {chg:+.1%} ({px})", "warn")
        # 20분 안 급변 (장중 흐름)
        base = next((h for h in hist if now - pd.Timestamp(h[0]).to_pydatetime() <= FAST_WINDOW), None)
        if base and base[1]:
            mv = q["price"] / base[1] - 1
            if abs(mv) >= FAST_MOVE:
                bucket = now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0).isoformat()
                rid = push(app.engine, "price", f"{name} 급격한 {'상승' if mv > 0 else '하락'} {mv:+.1%}",
                           f"최근 {int((now - pd.Timestamp(base[0]).to_pydatetime()).total_seconds() // 60)}분 · 현재 {px}",
                           level="info" if mv > 0 else "warn", symbol=sym, link=f"#analysis/{sym}",
                           dedupe=f"fast:{sym}:{bucket}", data={"dir": "up" if mv > 0 else "down", "move": mv}, now=now)
                n += rid is not None
    n += _volume_spikes(app, quotes, focus, names, now, day)
    state["_at"] = now.isoformat()
    ops.set_state(app.engine, "live_quotes", state)
    n += evaluate_rules(app, quotes, now, names)
    return {"checked": len(quotes), "alerts": n}


VOLUME_SPIKE = 3.0
_VOL_CACHE: dict = {}


def _volume_spikes(app, quotes: dict, focus: dict, names: dict, now: datetime, day: str) -> int:
    """보유·관심 종목의 거래량이 평소(20일 평균 × 장 경과 비율)의 3배 이상 → 하루 한 번 알림 (규칙 없이 자동)."""
    syms = [s for s, q in quotes.items() if q.get("volume") and s in focus]
    if not syms:
        return 0
    key = (id(app.engine), day)
    cache = _VOL_CACHE.setdefault(key, {})
    need = [s for s in syms if s not in cache]
    if need:
        cache.update(_avg_volume(app.engine, need))
    n = 0
    for sym in syms:
        avg = cache.get(sym)
        if not avg:
            continue
        ratio = quotes[sym]["volume"] / (avg * _session_fraction(sym, now))
        if ratio >= VOLUME_SPIKE:
            n += push(app.engine, "price", f"{names.get(sym, sym)} 거래량 폭증 {ratio:.1f}배",
                      f"평소(20일 평균) 대비 · 장 경과 비율 반영 ({'·'.join(sorted(focus.get(sym, [])))})", level="info",
                      symbol=sym, link=f"#analysis/{sym}", dedupe=f"vol:{sym}:{day}", data={"ratio": round(ratio, 2)}, now=now) is not None
    return n


# ---------------------------------------------------------------- AI 신호 · 공시 · 뉴스 · 채점 · 일정 · 감시
def _pct(x):
    return "-" if x is None else f"{x:+.1%}"


def alert_scan(app, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    eng = app.engine
    cur = ops.get_state(eng, "alert_cursor")
    first = not cur
    focus = focus_symbols(app)
    names = _names(eng)
    made = 0
    with session_scope(eng) as s:
        max_c = s.scalar(select(func.max(ConsensusRecord.id))) or 0
        max_d = s.scalar(select(func.max(Disclosure.id))) or 0
        max_n = s.scalar(select(func.max(NewsArticle.id))) or 0
        new_c = [] if first else s.scalars(select(ConsensusRecord).where(ConsensusRecord.id > cur.get("c", 0))
                                           .order_by(ConsensusRecord.id)).all()
        sigs = []
        for c in new_c:
            if c.symbol not in focus:
                continue
            prev = s.scalar(select(ConsensusRecord.action).where(ConsensusRecord.symbol == c.symbol,
                                                                 ConsensusRecord.id < c.id)
                            .order_by(ConsensusRecord.id.desc()))
            if prev == c.action:
                continue  # 같은 신호가 이어지는 것은 알리지 않는다
            if c.action not in ("BUY", "SELL") and prev not in ("BUY", "SELL"):
                continue  # HOLD↔NO_TRADE 같은 변화는 알리지 않음 · BUY→HOLD 같은 '신호 해제'는 알림
            p = c.payload or {}
            sigs.append((c.id, c.symbol, c.action, c.prob_up, p.get("expected_return"), p.get("horizon", 5), c.confidence, prev))
        discs = [] if first else [(d.id, d.symbol, d.title, d.url) for d in s.scalars(
            select(Disclosure).where(Disclosure.id > cur.get("d", 0)).order_by(Disclosure.id)) if d.symbol in focus]
        news = [] if first else [(n.id, n.title, n.symbols or [], n.importance, n.url) for n in s.scalars(
            select(NewsArticle).where(NewsArticle.id > cur.get("n", 0), NewsArticle.importance >= 0.8)
            .order_by(NewsArticle.id).limit(200)) if set(n.symbols or []) & set(focus)]
        last_res = cur.get("res") or now.isoformat()
        resolved = [] if first else s.scalars(select(ConsensusRecord).where(
            ConsensusRecord.correct.is_not(None), ConsensusRecord.as_of > pd.Timestamp(last_res).to_pydatetime())
            .order_by(ConsensusRecord.as_of)).all()
        res_rows = [(r.id, r.symbol, r.correct, r.realized_return, (r.payload or {}).get("expected_return"), r.as_of)
                    for r in resolved]
    for cid, sym, act, p, exp, h, conf, prev in sigs:
        released = act not in ("BUY", "SELL")
        title = (f"AI 신호 해제: {names.get(sym, sym)} {prev}→{act}" if released
                 else f"AI 신호: {names.get(sym, sym)} {act}" + (f" (이전 {prev})" if prev else ""))
        made += push(eng, "signal", title,
                     f"상승 확률 {p:.0%} · 예상 {_pct(exp)} ({h}거래일) · 신뢰도 {conf:.0f}", level="warn" if released and "보유" in focus.get(sym, set()) else "info",
                     symbol=sym, link=f"#analysis/{sym}", dedupe=f"sig:{cid}",
                     data={"action": act, "prev": prev, "prob_up": p, "expected": exp}, now=now) is not None
    for did, sym, title, url in discs:
        made += push(eng, "disclosure", f"공시: {names.get(sym, sym)}", title, level="info", symbol=sym,
                     link=f"#analysis/{sym}", dedupe=f"disc:{did}", data={"url": url}, now=now) is not None
    for nid, title, syms, imp, url in news:
        sym = next(x for x in syms if x in focus)
        made += push(eng, "news", f"중요 뉴스: {names.get(sym, sym)}", title, level="info", symbol=sym,
                     link=f"#analysis/{sym}", dedupe=f"news:{nid}", data={"url": url, "importance": imp},
                     now=now) is not None
    if res_rows:
        hits = sum(1 for r in res_rows if r[2])
        made += push(eng, "result", f"예측 채점 {len(res_rows)}건 — 적중 {hits}건 ({hits / len(res_rows):.0%})",
                     " · ".join(f"{names.get(sym, sym)} {'✔' if ok else '✘'} 실제 {_pct(r)}" for _, sym, ok, r, _, _ in res_rows[:6]),
                     level="good" if hits / len(res_rows) >= 0.5 else "warn", link="#control",
                     dedupe=f"res:{res_rows[-1][0]}:{len(res_rows)}", now=now) is not None
        last_res = pd.Timestamp(res_rows[-1][5]).isoformat()
    made += _earnings_alerts(app, focus, names, now)
    made += _guardian_alert(app, cur, now)
    ops.set_state(eng, "alert_cursor", {"c": max_c, "d": max_d, "n": max_n, "res": last_res,
                                        "halted": ops.halted(eng), "at": now.isoformat()})
    return {"alerts": made, "first_run": first}


def _earnings_alerts(app, focus: dict, names: dict, now: datetime) -> int:
    """캐시된 종목 상세(네트워크 없음)에서 실적 발표 D-1 · 당일."""
    from .data.fundamentals import with_d_day
    from .data.models import SystemState
    made = 0
    with session_scope(app.engine) as s:
        rows = [(r.key.split(":", 1)[1], (r.value or {}).get("data") or {})
                for r in s.scalars(select(SystemState).where(SystemState.key.like("profile:%")))]
    for sym, p in rows:
        if sym not in focus:
            continue
        for e in with_d_day([e for e in p.get("events") or [] if e.get("kind") == "earnings"]):
            if e["past"] or e["d_day"] > 1:
                continue
            made += push(app.engine, "earnings", f"{names.get(sym, sym)} 실적 발표 {'오늘' if e['d_day'] == 0 else '내일'}",
                         f"{e['date']}" + (f" · {e['time']}" if e.get("time") else "") + (f" · {e['detail']}" if e.get("detail") else ""),
                         level="warn", symbol=sym, link=f"#analysis/{sym}", dedupe=f"earn:{sym}:{e['date']}:{e['d_day']}",
                         now=now) is not None
    return made


def _guardian_alert(app, cur: dict, now: datetime) -> int:
    halted = ops.halted(app.engine)
    if not cur or halted == bool(cur.get("halted")):
        return 0
    ks = ops.get_state(app.engine, "kill_switch")
    if halted:
        return push(app.engine, "guardian", "자동 킬스위치: 매매 정지 (HALTED)", ks.get("reason") or "", level="bad",
                    link="#safety", dedupe=f"halt:{now.isoformat()[:16]}", now=now) is not None
    return push(app.engine, "guardian", "매매 정지 해제", "자동 감시 정상", level="good", link="#safety",
                dedupe=f"unhalt:{now.isoformat()[:16]}", now=now) is not None


# ---------------------------------------------------------------- 시장 흐름 (1시간)
def market_pulse(app, now: datetime | None = None) -> dict:
    from .engines.market_intel import load_macro, market_state
    now = now or datetime.now(UTC)
    bars, bench, _ = app.market_data()
    if bench is None or not bars:
        return {}
    with session_scope(app.engine) as s:
        vix = load_macro(s, ["VIXCLS"]).get("VIXCLS")
    ms = market_state(bench, bars, vix=vix)
    st = ops.get_state(app.engine, "market_pulse")
    hist = list(st.get("hist") or [])
    prev = hist[-1]["label"] if hist else None
    hist.append({"at": now.isoformat(), "label": ms["label"], "score": ms["score"]})
    ops.set_state(app.engine, "market_pulse", {"hist": hist[-24 * 14:]})
    if prev and prev != ms["label"]:
        push(app.engine, "market", f"시장 상태 전환: {prev} → {ms['label']}", f"시장 심리 {ms['score']}점",
             level="good" if ms["label"] == "RISK ON" else "warn", link="#market",
             dedupe=f"mkt:{now.isoformat()[:13]}", now=now)
    return {"label": ms["label"], "score": ms["score"]}


__all__ = ["push", "recent", "price_watch", "alert_scan", "market_pulse", "focus_symbols"]

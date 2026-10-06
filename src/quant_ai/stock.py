"""올인원 종목 페이지의 '저장된 기록만으로' 답하는 부분 (외부 호출 없음 — 외부 자료가 늦어도 먼저 뜬다).

  · trust(sym)      데이터 신뢰도 · 신선도: 일봉 기준일(거래일 기준 밀림) · 1차/2차 소스 · 품질 경고 · 거래정지 의심 · AI 판단 나이
  · pretrade(sym)   사전 리스크 게이트: 실제 매매와 같은 RiskEngine 설정으로 "지금 이 종목을 사면" 통과/축소/차단과 이유
  · story(sym)      왜 샀나 / 왜 안 샀나: 코어 순위·점수 · 거부 · 매수 불가 · 주문 기록 · 리스크 차단 · AI 신호
  · digest(sym)     뉴스·공시 자동 요약: 최근 7/30일 건수 · 긍정/부정 · 중요 헤드라인 · 공시 원문 요약 · 추출된 이벤트
  · events(sym)     이 종목 일정 + 시장 큰 일정 (이벤트 캘린더 기준 D-day)
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from . import ops
from .asof import label, stamp

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ 데이터 신뢰도 · 신선도
def trust(app, symbol: str, now: datetime | None = None) -> dict:
    from .data.db import session_scope
    from .data.models import ConsensusRecord, PriceBar
    from .data.quality import validate_bars
    from .data.sources import SECONDARY
    now = now or datetime.now(UTC)
    bars, _ = app._all_bars()
    b = bars.get(symbol)
    checks = []

    def add(key, label_, status, detail):
        checks.append({"key": key, "label": label_, "status": status, "detail": detail})

    kr = symbol[:1].isdigit()
    if b is None or b.empty:
        add("bars", "일봉", "bad", "저장된 일봉 없음")
    else:
        st = stamp(b.index[-1], "bar_kr" if kr else "bar_us", now)
        add("fresh", "일봉 기준일", {"fresh": "ok", "stale": "warn", "old": "bad"}.get(st["status"], "warn"), f"{st['label']} 종가 · {st['age']}")
        with session_scope(app.engine) as s:
            srcs = dict(s.execute(select(PriceBar.source, func.count()).where(PriceBar.symbol == symbol, PriceBar.interval == "1d",
                                                                             PriceBar.ts >= b.index[-1] - timedelta(days=45))
                                  .group_by(PriceBar.source)).all())
        n_sec = srcs.get(SECONDARY, 0)
        add("source", "출처", "warn" if n_sec else "ok",
            " · ".join(f"{k} {v}봉" for k, v in srcs.items()) + (" — 2차(Yahoo)로 채운 봉 포함, 1차 갱신 시 덮어씀" if n_sec else " (최근 45일)"))
        _, rep = validate_bars(b.iloc[-260:], symbol)
        warns = list(rep.warnings) + [f"{k} {v}행 제거" for k, v in rep.dropped.items()]
        add("quality", "품질 검사", "warn" if warns else "ok", "; ".join(warns)[:200] if warns else f"최근 {min(len(b), 260)}봉 이상 없음")
        v0 = float(b["volume"].iloc[-1] or 0) == 0
        flat = len(b) > 3 and b["close"].iloc[-3:].nunique() == 1 and float(b["volume"].iloc[-3:].sum() or 0) == 0
        add("halt", "거래정지 의심", "bad" if flat else "warn" if v0 else "ok",
            "최근 3봉 가격 고정 · 거래량 0 — 거래정지 가능성" if flat else "마지막 봉 거래량 0" if v0 else "정상 거래")
    with session_scope(app.engine) as s:
        c = s.scalar(select(ConsensusRecord.as_of).where(ConsensusRecord.symbol == symbol).order_by(ConsensusRecord.as_of.desc()))
    if c is None:
        add("ai", "AI 판단", "warn", "이 종목 AI 판단 기록 없음")
    else:
        st = stamp(c, "consensus", now)
        add("ai", "AI 판단 나이", {"fresh": "ok", "stale": "warn", "old": "bad"}.get(st["status"], "warn"), f"{st['label']} · {st['age']}")
    rank = {"ok": 0, "warn": 1, "bad": 2}
    worst = max((rank[x["status"]] for x in checks), default=0)
    score = max(0, 100 - sum({"ok": 0, "warn": 12, "bad": 35}[x["status"]] for x in checks))
    return {"symbol": symbol, "status": ["ok", "warn", "bad"][worst], "score": score, "checks": checks, "as_of": label(now)}


# ------------------------------------------------------------------ 사전 리스크 게이트 (Pre-Trade)
def pretrade(app, symbol: str, weight: float | None = None, mode: str = "paper", now: datetime | None = None) -> dict:
    """실제 _trade 와 같은 순서·설정으로 매수 1건을 시험 (주문은 내지 않음, 일 주문 수는 건드리지 않음)."""
    from . import readiness
    from .data.db import recent_adv, session_scope
    from .desk import event_caps_for, portfolio_gate
    from .engines.sector import sector_map
    from .trading.portfolio import Order, Side
    from .trading.risk import RiskEngine
    now = now or datetime.now(UTC)
    bars, _, _ = app.market_data()
    b = bars.get(symbol)
    if b is None or b.empty:
        return {"symbol": symbol, "error": "국내 일봉이 없어 시험할 수 없습니다 (해외 종목은 미국 장부 기준)"}
    price = float(b["close"].iloc[-1])
    pf = app.load_portfolio(mode)
    prices = {s: float(x["close"].iloc[-1]) for s, x in bars.items() if len(x)}
    for s_, p in pf.positions.items():
        prices.setdefault(s_, p.avg_price)
    equity = pf.equity(prices)
    lim = app.settings.risk
    w = float(weight) if weight else lim.max_position_weight
    qty = int(w * equity // price) if equity > 0 else 0
    steps = []

    def step(name, ok, detail):
        steps.append({"gate": name, "status": ok, "detail": detail})

    halted = ops.halted(app.engine)
    step("HALTED", "bad" if halted else "ok", "자동 감시가 모든 주문을 멈춤" if halted else "정상")
    risk = RiskEngine(lim)
    risk.kill_switch = app.kill_switch_on()
    step("긴급 정지", "bad" if risk.kill_switch else "ok", "켜짐 — 신규 매수 중단" if risk.kill_switch else "꺼짐")
    risk.start_day(now.date(), app._day_start_equity(mode, now, equity), app._orders_today(mode, now))
    with session_scope(app.engine) as s:
        risk.adv = recent_adv(s, {symbol} | set(pf.positions))
    risk.sectors = sector_map(app.engine)
    gated = readiness.gate_applies(app.settings, mode)
    risk.buy_block = readiness.buy_block(app, mode)
    rd = ops.get_state(app.engine, "readiness")
    step("매매 준비", "bad" if risk.buy_block else "ok" if gated else "na",
         risk.buy_block or (f"{rd.get('status', '-')} · 게이트 적용" if gated else f"{rd.get('status', '-')} · 이 장부({mode})는 게이트 미적용 (기록만)"))
    risk.event_caps = event_caps_for(app, {symbol})
    from .failmode import apply_caps, news_down
    risk.event_caps = apply_caps(app, risk.event_caps, {symbol}, now)
    nd = news_down(app, now)
    if nd:
        step("뉴스 수집", "warn", nd)
    cap = risk.event_caps.get(symbol)
    step("이벤트", "warn" if cap else "ok", f"{cap[1]} → 수량 ×{cap[0]}" if cap else "가까운 실적·큰 이벤트 없음")
    try:
        risk.portfolio_gate = portfolio_gate(app, bars)
    except Exception as e:  # noqa: BLE001
        step("포트폴리오 게이트", "warn", f"계산 실패 ({type(e).__name__}) — 기존 한도로만 판단")
    order = Order(symbol=symbol, side=Side.BUY, qty=qty, reason="사전 점검", ref_price=price)
    orders_before = risk._orders_today
    dec = risk.check(order, pf, prices)
    risk._orders_today = orders_before
    got = dec.order.qty if dec.approved and dec.order else 0
    verdict = "차단" if not dec.approved else "축소" if got < qty else "통과"
    for r in dec.reasons:
        if r.startswith(("매매 준비 상태", "킬스위치")):
            continue  # 위 단계에 이미 표시
        step("리스크 엔진", "bad" if not dec.approved else "warn", r)
    if dec.approved and not dec.reasons:
        step("리스크 엔진", "ok", "종목·총노출·현금·업종·유동성·VaR 한도 안")
    return {"symbol": symbol, "mode": mode, "price": price, "equity": round(equity, 0), "want_weight": round(w, 4), "want_qty": qty,
            "allowed_qty": got, "allowed_weight": round(got * price / equity, 4) if equity > 0 else 0, "verdict": verdict,
            "steps": steps, "as_of": label(b.index[-1], with_time=False) + " 종가 기준",
            "note": "주문은 나가지 않습니다 — 실제 매매 사이클과 같은 한도·게이트로 계산만 합니다"}


# ------------------------------------------------------------------ 왜 샀나 / 왜 안 샀나
def story(app, symbol: str, mode: str = "paper", days: int = 90) -> dict:
    from .data.db import session_scope
    from .data.models import ConsensusRecord, JournalEntry
    now = datetime.now(UTC)
    plan = ops.get_state(app.engine, f"cs-plan:{mode}")
    cfg = plan.get("config") or {}
    try:
        held = app.load_portfolio(mode).positions.get(symbol)
    except Exception:  # noqa: BLE001
        held = None
    held_qty = held.qty if held else 0
    with session_scope(app.engine) as s:
        rows = s.scalars(select(JournalEntry).where(JournalEntry.symbol == symbol, JournalEntry.mode == mode,
                                                    JournalEntry.ts >= now - timedelta(days=days)).order_by(JournalEntry.ts.desc()).limit(40)).all()
        timeline = [{"ts": label(r.ts), "kind": r.kind, "message": r.message,
                     "reason": (r.data or {}).get("reason"), "reasons": (r.data or {}).get("reasons") or [],
                     "status": (r.data or {}).get("status")} for r in rows]
        cons = s.scalar(select(ConsensusRecord).where(ConsensusRecord.symbol == symbol).order_by(ConsensusRecord.as_of.desc()))
    ranks, scores = plan.get("ranks") or {}, plan.get("scores") or {}
    top_k, buf = cfg.get("core_top_k"), cfg.get("core_buffer_k")
    rk = ranks.get(symbol)
    facts = []
    if plan:
        if symbol in (plan.get("core") or []):
            facts.append(f"코어 편입 — 모멘텀·품질 점수 {scores.get(symbol)} · 순위 #{rk + 1 if rk is not None else '-'} (상위 {top_k})")
        elif rk is not None:
            facts.append(f"코어 후보 순위 #{rk + 1} — 상위 {top_k} 밖" + (f" (보유 중이면 {buf}위까지 유지)" if buf else ""))
        else:
            facts.append(f"이번 코어 유니버스({plan.get('universe_size')}종목 · 월말 시총 상위) 밖 — 점수 계산 대상 아님")
        if symbol in (plan.get("vetoed") or {}):
            facts.append(f"AI 거부권: {plan['vetoed'][symbol]}")
        if symbol in (plan.get("exits") or {}):
            facts.append(f"청산 결정: {plan['exits'][symbol]}")
        if symbol in (plan.get("unaffordable") or []):
            facts.append("1주 가격이 목표 금액보다 커서 살 수 없음 (매수 불가)")
        sat = [x.get("symbol") for x in plan.get("satellite") or []]
        if symbol in sat:
            facts.append("위성(AI) 편입")
        if not cfg.get("use_ai", True):
            facts.append("코어 전용 모드 — AI 신호만으로는 사지 않음 (AI 는 섀도 채점만)")
    blocks = [t for t in timeline if t["kind"] in ("risk_block", "dedupe") or (t["kind"] == "order" and t["status"] not in ("filled", "partial", None))]
    buys = [t for t in timeline if t["kind"] == "order" and t["status"] in ("filled", "partial") and t["message"].startswith("buy")]
    if held_qty:
        head = f"보유 중 {held_qty:,.0f}주 — " + (f"{buys[-1]['ts']} 매수: {buys[-1]['reason'] or '사유 기록 없음'}" if buys else "매수 기록이 조회 기간 밖")
    elif blocks:
        head = "사려고 했지만 막힘 — " + "; ".join(blocks[0]["reasons"] or [blocks[0]["message"]])[:160]
    elif plan and symbol in (plan.get("core") or []):
        head = "코어 대상이지만 아직 보유 없음 — 다음 매매 사이클에 매수 (또는 리스크 게이트 확인)"
    else:
        head = "사지 않음 — " + (facts[0] if facts else "코어 계획 기록 없음 (자동매매를 아직 돌리지 않음)")
    sig = None
    if cons is not None:
        sig = {"action": cons.action, "prob_up": round(cons.prob_up, 3), "confidence": round(cons.confidence), "as_of": label(cons.as_of)}
    return {"symbol": symbol, "mode": mode, "held_qty": held_qty, "headline": head, "facts": facts, "signal": sig,
            "timeline": timeline[:15], "plan_at": label(plan.get("ts")) if plan.get("ts") else None,
            "rule": f"코어: 월말 시총 상위 유니버스에서 점수 상위 {top_k} 편입 · {buf}위 밖이면 교체 · {cfg.get('core_rebalance_days')}거래일마다 리밸런싱" if cfg else None}


# ------------------------------------------------------------------ 뉴스·공시 자동 요약
def digest(app, symbol: str, now: datetime | None = None) -> dict:
    from .data.db import session_scope
    from .data.models import Disclosure, Instrument, NewsArticle
    from .engines.event_extract import extract
    now = now or datetime.now(UTC)
    with session_scope(app.engine) as s:
        name = s.scalar(select(Instrument.name).where(Instrument.symbol == symbol)) or symbol
        news = [n for n in s.scalars(select(NewsArticle).where(NewsArticle.published_at >= now - timedelta(days=30))
                                     .order_by(NewsArticle.published_at.desc()).limit(3000)) if symbol in (n.symbols or [])]
        discs = s.scalars(select(Disclosure).where(Disclosure.symbol == symbol, Disclosure.filed_at >= (now - timedelta(days=60)).date())
                          .order_by(Disclosure.filed_at.desc()).limit(20)).all()
        news = [{"at": n.published_at, "title": n.title, "sent": n.sentiment or 0.0, "imp": n.importance or 0.5, "events": n.events or [],
                 "source": n.source, "url": n.url} for n in news]
        discs = [{"date": d.filed_at, "title": d.title, "summary": d.summary, "sent": d.sentiment or 0.0, "url": d.url} for d in discs]

    def aware(t):
        return t if t.tzinfo else t.replace(tzinfo=UTC)

    wk = [n for n in news if aware(n["at"]) >= now - timedelta(days=7)]
    pos, neg = sum(n["sent"] > 0.2 for n in wk), sum(n["sent"] < -0.2 for n in wk)
    tone = "긍정 우세" if pos > neg * 1.5 and pos >= 2 else "부정 우세" if neg > pos * 1.5 and neg >= 2 else "중립·혼재" if wk else "뉴스 없음"
    top = sorted(wk or news, key=lambda n: -(n["imp"] + abs(n["sent"]) * 0.5))[:5]
    ext = []
    for d in discs[:10]:
        for e in extract(d["title"], {symbol: name}, symbol):
            ext.append({"label": e["label"], "polarity": e.get("polarity", 0), "title": d["title"], "date": str(d["date"])})
    lines = []
    if wk:
        lines.append(f"최근 7일 뉴스 {len(wk)}건 — 긍정 {pos} · 부정 {neg} → {tone}")
    elif news:
        lines.append(f"최근 7일 뉴스 없음 · 30일 {len(news)}건")
    if discs:
        lines.append(f"최근 60일 공시 {len(discs)}건" + (f" — 주요: {', '.join(dict.fromkeys(e['label'] for e in ext))}" if ext else ""))
    kinds = [k for n in wk for k in n["events"]]
    if kinds:
        from collections import Counter
        lines.append("뉴스 속 이벤트: " + ", ".join(f"{k} {v}" for k, v in Counter(kinds).most_common(4)))
    return {"symbol": symbol, "name": name, "summary": lines or ["저장된 뉴스·공시 없음 — 수집 작업(장중 5분 · DART 키)이 돌면 자동으로 채워집니다"],
            "tone": tone, "news_7d": len(wk), "news_30d": len(news), "pos": pos, "neg": neg,
            "headlines": [{"at": label(n["at"]), "title": n["title"], "sent": round(n["sent"], 2), "source": n["source"], "url": n["url"]} for n in top],
            "disclosures": [{"date": str(d["date"]), "title": d["title"], "summary": d["summary"], "url": d["url"]} for d in discs[:6]],
            "extracted": ext[:6]}


# ------------------------------------------------------------------ 일정 (D-day)
# 종목 화면에 항상 보여줄 시장 일정 (중요도와 무관): FOMC · CPI · 고용 · 옵션 만기 · 지수 편입/편출 · 금통위
ALWAYS_KINDS = {"fomc", "cpi", "nfp", "pce", "options_expiry", "quad_witching", "index_rebalance", "bok"}
US_FOR_KR = {"fomc", "cpi", "nfp", "pce"}  # 국내 종목도 미국 거시 일정의 영향을 받는다
def events(app, symbol: str, now: datetime | None = None, days: int = 45) -> list[dict]:
    from zoneinfo import ZoneInfo
    now = now or datetime.now(UTC)
    today = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    kr = symbol[:1].isdigit()
    out = []
    from .engines.sector import sector_map
    secs = sector_map(app.engine)
    my_sec = secs.get(symbol)
    peers_seen = 0
    cal = ops.get_state(app.engine, "event_calendar")
    evs = list(cal.get("events") or [])
    try:
        fresh = cal.get("at") and now - datetime.fromisoformat(cal["at"]) < timedelta(hours=26)
    except (TypeError, ValueError):
        fresh = False
    if not fresh:  # 캘린더가 없거나 오래됨 → 규칙으로 만들 수 있는 시장 일정(휴장·만기·FOMC·지표)은 바로 만든다
        from .engines import events as E
        try:
            evs += E.market_events(today - timedelta(days=3), today + timedelta(days=days)) + \
                E.econ_events(today - timedelta(days=3), today + timedelta(days=days), ops.get_state(app.engine, "fred_releases").get("dates"))
        except Exception as e:  # noqa: BLE001 - 생성 실패해도 저장된 일정은 보인다
            log.info("시장 일정 생성 실패: %s", e)
    seen_titles = set()
    for e in evs:
        if (e.get("title"), e.get("symbol"), str(e.get("date"))[:10]) in seen_titles:
            continue
        seen_titles.add((e.get("title"), e.get("symbol"), str(e.get("date"))[:10]))
        try:
            d = date.fromisoformat(str(e["date"])[:10])
        except (KeyError, ValueError):
            continue
        dd = (d - today).days
        if dd < -3 or dd > days:
            continue
        mine = e.get("symbol") == symbol
        mkt_ok = e.get("market") in (("KR", "GLOBAL") if kr else ("US", "GLOBAL")) or (kr and e.get("kind") in US_FOR_KR)
        market = not e.get("symbol") and mkt_ok and (e.get("kind") in ALWAYS_KINDS or e.get("importance", 0) >= 0.7)
        # 동종업체 실적: 같은 업종 다른 종목의 실적 발표 (14일 안 · 최대 3개) — 업종 분위기가 먼저 반영되는 날
        peer = (not mine and e.get("kind") == "earnings" and e.get("symbol") and my_sec and my_sec != "미분류"
                and secs.get(e.get("symbol")) == my_sec and 0 <= dd <= 14 and peers_seen < 3)
        if peer:
            peers_seen += 1
            out.append({"date": d.isoformat(), "d_day": dd, "d_label": "D-Day" if dd == 0 else f"D-{dd}", "kind": "peer_earnings",
                        "title": f"동종업체 실적: {e.get('title')}", "estimated": bool(e.get("estimated")), "scope": "동종",
                        "source": e.get("source"), "importance": 0.5})
            continue
        if mine or (market and dd <= 21):
            out.append({"date": d.isoformat(), "d_day": dd, "d_label": "D-Day" if dd == 0 else f"D-{dd}" if dd > 0 else f"D+{-dd}",
                        "kind": e.get("kind"), "title": e.get("title"), "estimated": bool(e.get("estimated")), "scope": "종목" if mine else "시장",
                        "source": e.get("source"), "importance": e.get("importance"), "time": e.get("time"),
                        "eps_estimate": e.get("eps_estimate"), "revenue_estimate": e.get("revenue_estimate")})
    out += _lockup(app, symbol, today)
    if not any(x["kind"] == "earnings" and x["scope"] == "종목" for x in out) and not _earnings_known(app, symbol):
        # '일정 없음'과 '모름'을 구분 — 무료 소스에 실적일 정보가 아예 없으면 확인 안 됨으로 표시
        out.append({"date": None, "d_day": 999, "d_label": "확인 안 됨", "kind": "unknown", "scope": "종목", "estimated": False,
                    "title": "실적 발표일 확인 안 됨 — 무료 소스에 정보 없음 (회사 IR·공시로 확인)", "source": None, "importance": 0.4})
    out.sort(key=lambda x: (x["d_day"] < 0, abs(x["d_day"]), x["scope"] != "종목"))
    return out[:16]


def _earnings_known(app, symbol: str) -> bool:
    """이 종목의 실적 일정 정보를 어디서든 받은 적이 있나 (프로필 일정·실적 이력·국내 컨센서스)."""
    prof = ops.get_state(app.engine, f"profile:{symbol}").get("data") or {}
    if any(e.get("kind") == "earnings" for e in prof.get("events") or []) or prof.get("earnings_history"):
        return True
    kc = ops.get_state(app.engine, f"krcons:{symbol}")
    return bool(kc.get("surprises") or kc.get("next_date"))


LOCKUP_WORDS = ("보호예수", "의무보유", "매각제한", "락업", "lock-up", "lockup")


def _lockup(app, symbol: str, today: date) -> list[dict]:
    """락업(보호예수·의무보유) 관련 공시 — 해제일 자체는 공시 원문에만 있어 '공시가 나왔다'만 띠에 올린다 (해제일은 원문 확인)."""
    from .data.db import session_scope
    from .data.models import Disclosure
    with session_scope(app.engine) as s:
        rows = s.scalars(select(Disclosure).where(Disclosure.symbol == symbol, Disclosure.filed_at >= today - timedelta(days=60))
                         .order_by(Disclosure.filed_at.desc()).limit(200)).all()
        hits = [(r.filed_at, r.title, r.url) for r in rows if any(w in (r.title or "").lower() for w in LOCKUP_WORDS)]
    return [{"date": d.isoformat(), "d_day": (d - today).days, "d_label": f"D+{(today - d).days}" if d < today else "D-Day", "kind": "lockup",
             "title": f"락업 관련 공시: {t[:40]} (해제일은 원문 확인)", "estimated": False, "scope": "종목", "source": "DART", "url": u,
             "importance": 0.6} for d, t, u in hits[:2]]


def page(app, symbol: str, mode: str = "paper") -> dict:
    """종목 페이지 한 번에 (빠른 것만)."""
    from . import stockplus, thesis
    from .scorecard import verify_now
    out = {"symbol": symbol}
    for k, f in (("header", lambda: stockplus.header(app, symbol)), ("situation", lambda: stockplus.situation(app, symbol)),
                 ("freshness", lambda: stockplus.freshness(app, symbol)), ("position", lambda: stockplus.position(app, symbol)),
                 ("trust", lambda: trust(app, symbol)), ("story", lambda: story(app, symbol, mode)),
                 ("digest", lambda: digest(app, symbol)), ("events", lambda: events(app, symbol)),
                 ("thesis", lambda: thesis.get(app.engine, symbol)), ("verify", lambda: verify_now(app, symbol))):
        try:
            out[k] = f()
        except Exception as e:  # noqa: BLE001 - 한 칸이 실패해도 나머지는 보인다
            out[k] = {"error": f"{type(e).__name__}: {e}"}
    return out


__all__ = ["trust", "pretrade", "story", "digest", "events", "page"]

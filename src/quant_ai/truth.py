"""Truth Center — AI 를 더 넣기 전에, 시스템이 보는 '사실'이 맞는지부터.

여섯 가지 진실. 각 항목은 ok / warn / bad / na 와 근거(숫자·출처·시각)를 가진다.
  1. Market Clock   — 지금 시장이 열렸나, 다음 개장·폐장은 언제인가, 휴장일 캘린더가 실제로 로드됐나
  2. Data Truth     — 1차/2차 소스 · 최신성 · 교차 검증 · 데이터 품질 · 기업 행동(분할 등) · PIT 유니버스
  3. Event Truth    — 이벤트 출처 · 추정 비율 · 소스 간 날짜 충돌 · 캘린더 최신성
  4. Broker Truth   — 증권사 연결 · 잔고 대조 · 미확정 주문 · 검증 스위트
  5. Portfolio Risk — VaR · 업종 · 집중도 · 스트레스 한도
  6. Execution Truth — 주문 수명주기 · 멱등성 · 부분체결 · 취소 · 실측 슬리피지 · 시뮬레이터 정확도
bad 가 하나라도 있으면 매매 준비(Readiness)가 NOT READY 가 되도록 연결된다 (fail-closed).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from . import ops
from .asof import freshness, label

RANK = {"ok": 0, "na": 0, "warn": 1, "bad": 2}


def _c(key: str, label_: str, status: str, detail: str, **ev) -> dict:
    return {"key": key, "label": label_, "status": status, "detail": detail, **({"evidence": ev} if ev else {})}


def _section(key: str, title: str, checks: list[dict], extra: dict | None = None) -> dict:
    worst = max((RANK[c["status"]] for c in checks), default=0)
    return {"key": key, "title": title, "status": ["ok", "warn", "bad"][worst], "checks": checks,
            "n_bad": sum(c["status"] == "bad" for c in checks), "n_warn": sum(c["status"] == "warn" for c in checks), **(extra or {})}


# ------------------------------------------------------------------ 1. Market Clock
def market_clock(now: datetime) -> dict:
    from datetime import date as _d

    from .clock import CALENDAR_SOURCE, HOLIDAY_NAMES, KRX, US, clock_status
    cs = clock_status(now)
    y = now.year
    checks = [
        _c("calendar_loaded", "휴장일 캘린더", "ok" if cs["calendar_ok"] else "bad",
           CALENDAR_SOURCE if cs["calendar_ok"] else "휴장일 캘린더 없음 — 주말만 쉬는 것으로 계산 (휴장일에 주문할 수 있음 → 실전 차단)"),
        _c("next_year", "내년 휴장일", "ok" if any(d.year == y + 1 for d in HOLIDAY_NAMES.get("KRX", {})) else "warn",
           f"KRX {y + 1}년 휴장일 {sum(1 for d in HOLIDAY_NAMES.get('KRX', {}) if d.year == y + 1)}일 로드"),
        _c("krx_year_end", "KRX 연말 휴장", "ok" if not KRX.is_trading_day(_d(y, 12, 31)) or _d(y, 12, 31).weekday() >= 5 else "warn",
           "12/31 휴장 반영" if not KRX.is_trading_day(_d(y, 12, 31)) else "12/31 이 거래일로 되어 있음 — holidays.json 확인"),
        _c("timezone", "시간대", "ok", "KRX Asia/Seoul · US America/New_York (서머타임 자동) · 모든 기록 UTC 저장, 화면 KST"),
    ]
    for k, m in cs["markets"].items():
        checks.append(_c(f"next_{k}", f"{m['name']} 다음 {m['next_event']}", "ok",
                         f"{m['next_open_kst'] if m['next_event'] == '개장' else m['next_close_kst']} · {m['seconds_to_next'] // 3600}시간 {m['seconds_to_next'] % 3600 // 60}분 뒤"
                         + (f" · 오늘 휴장({m['holiday']})" if m["holiday"] else "") + (f" · {m['session']['note']}" if m.get("session") and m["session"].get("note") else "")))
    us_early = [d for d in (US.session(_d(y, 11, 1) + timedelta(days=i))[2] for i in range(60)) if d]
    checks.append(_c("early_close", "조기 폐장 규칙", "ok" if us_early else "warn", f"미국 조기 폐장 {len(us_early)}일 (연말) · KRX 새해 10시 개장"))
    return _section("clock", "Market Clock", checks, {"clock": cs})


# ------------------------------------------------------------------ 2. Data Truth
def data_truth(app, now: datetime) -> dict:
    from .data.db import session_scope
    from .data.models import PriceBar
    from .data.sources import REGISTRY, SECONDARY
    fr = freshness(app, now)
    it = fr["items"]
    checks = []
    for k in ("bar_kr", "bar_index", "bar_us", "quote", "news", "disclosure", "macro"):
        v = it.get(k)
        if not v:
            continue
        st = {"fresh": "ok", "stale": "warn", "old": "bad", "none": "warn" if k not in ("bar_kr", "bar_index") else "bad"}[v["status"]]
        if k in ("bar_us", "quote", "news", "disclosure", "macro") and st == "bad":
            st = "warn"  # 매매 대상(국내 일봉·지수)이 아니면 경고까지만
        checks.append(_c(f"fresh_{k}", f"최신성 · {v['name']}", st, f"{v.get('label') or '없음'} · {v.get('age')}"))
    with session_scope(app.engine) as s:
        n_sec = s.scalar(select(func.count()).select_from(PriceBar).where(PriceBar.source == SECONDARY)) or 0
        by_src = dict(s.execute(select(PriceBar.source, func.count()).group_by(PriceBar.source)).all())
    fo = ops.get_state(app.engine, "source_failover")
    checks.append(_c("secondary", "2차 소스 사용", "warn" if n_sec else "ok",
                     f"2차(Yahoo)로 채운 봉 {n_sec}개 — 1차(KRX)가 갱신되면 덮어씀" if n_sec else "모든 국내 일봉이 1차 소스 (KRX)",
                     sources=by_src, last_failover=fo.get("at")))
    xc = ops.get_state(app.engine, "live_quotes").get("conflicts") or []
    checks.append(_c("cross_check", "교차 검증 (증권사 ↔ DB)", "bad" if xc else "ok",
                     f"±30% 넘게 다른 종목: {', '.join(xc[:5])}" if xc else "불일치 없음 (증권사·실시간 시세 기준)"))
    from .datahealth import price_delay
    pdl = price_delay(app, now)  # 장중 실시간 시세가 15분 넘게 멈추면 → DATA 관문 빨강 → 신규 매수 차단
    checks.append(_c("price_delay", "장중 가격 지연", "bad" if pdl["block"] else "ok" if pdl["open"] and pdl["delay_s"] is not None else "na", pdl["detail"]))
    q = quality_scan(app)
    checks.append(_c("quality", "데이터 품질 (급변·0값·결측·정지)", "warn" if q["issues"] else "ok",
                     f"{q['checked']}종목 중 경고 {len(q['issues'])}종목: " + ", ".join(f"{x['symbol']}({x['warning'][:24]})" for x in q["issues"][:4])
                     if q["issues"] else f"{q['checked']}종목 최근 1년 이상 없음"))
    ca = corporate_action_scan(app)
    checks.append(_c("corporate_actions", "기업 행동 (분할·병합 반영)", "bad" if ca else "ok",
                     f"수정되지 않은 것으로 보이는 급변 {len(ca)}건: " + ", ".join(f"{x['symbol']} {x['date']} {x['ret']:+.0%}" for x in ca[:4])
                     if ca else "KRX 전일대비로 수정주가 복원 · 하루 ±45% 넘는 미설명 급변 없음"))
    uni = ops.get_state(app.engine, "krx_universe")
    months = len(uni) if uni else 0
    checks.append(_c("pit", "PIT 유니버스 (그 시점 기준)", "ok" if months else "warn",
                     f"월별 시총 상위 유니버스 {months}개월 (상장폐지 포함)" if months else "월별 유니버스 기록 없음 — 현재 종목으로 과거를 보면 생존편향"))
    return _section("data", "Data Truth", checks, {"registry": REGISTRY, "freshness": fr})


def quality_scan(app, max_symbols: int = 300) -> dict:
    """저장된 일봉을 다시 검사 (수집 때 놓친 것 · 1차/2차 소스가 섞인 구간 포함)."""
    from .data.quality import validate_bars
    bars, _, _ = app.market_data()
    issues, n = [], 0
    for sym, b in list(bars.items())[:max_symbols]:
        if len(b) < 30:
            continue
        n += 1
        _, rep = validate_bars(b.iloc[-260:], sym)
        if rep.dropped or rep.warnings:
            issues.append({"symbol": sym, "warning": (rep.warnings or [str(rep.dropped)])[-1]})
    return {"checked": n, "issues": issues}


def corporate_action_scan(app, limit: int = 40) -> list[dict]:
    """수정주가에서 하루 ±45% 넘는 변동 (상한가·하한가 ±30% 를 넘으므로 KRX 에선 분할 미반영 신호)."""
    bars, _, _ = app.market_data()
    out = []
    for sym, b in list(bars.items())[:400]:
        if not sym[:1].isdigit() or len(b) < 3:
            continue
        r = b["close"].pct_change().iloc[-260:]
        for t, v in r[r.abs() > 0.45].items():
            out.append({"symbol": sym, "date": str(t.date()), "ret": float(v)})
    return out[:limit]


# ------------------------------------------------------------------ 3. Event Truth
def event_truth(app, now: datetime) -> dict:
    cal = ops.get_state(app.engine, "event_calendar")
    checks = []
    if not cal.get("at"):
        return _section("event", "Event Truth", [_c("calendar", "이벤트 캘린더", "bad", "캘린더 기록 없음 — 이벤트 위험을 모름 (fail-closed)")])
    age = (now - datetime.fromisoformat(cal["at"])).total_seconds() / 3600
    checks.append(_c("calendar_age", "캘린더 최신성", "ok" if age < 26 else "bad", f"{label(cal['at'])} ({age:.0f}시간 전)"))
    evs = [e for e in cal.get("events", []) if e.get("d_day", -1) >= 0]
    est = sum(1 for e in evs if e.get("estimated"))
    checks.append(_c("estimated", "추정 일정 비율", "ok" if not evs or est / len(evs) < 0.6 else "warn",
                     f"다가오는 {len(evs)}건 중 추정 {est}건 — 확정 일정은 출처가 공식(거래소·연준·DART·FRED)",
                     by_source=dict(Counter(e.get("source") for e in evs).most_common(8))))
    kinds = {e["kind"] for e in evs}
    need = {"fomc": "FOMC", "options_expiry": "옵션 만기", "nfp": "미국 고용", "holiday": "휴장"}
    miss = [v for k, v in need.items() if k not in kinds and not (k == "holiday")]
    checks.append(_c("coverage", "이벤트 종류", "ok" if not miss else "warn", f"다가오는 종류 {len(kinds)}개" + (f" · 없음: {', '.join(miss)}" if miss else "")))
    conflicts = earnings_conflicts(app)
    checks.append(_c("conflicts", "소스 간 실적일 충돌", "warn" if conflicts else "ok",
                     "; ".join(f"{c['symbol']} {', '.join(c['dates'])}" for c in conflicts[:4]) if conflicts else "충돌 없음 (Yahoo·Nasdaq·DART 비교)"))
    bok = (cal.get("bok") or {}).get("schedule") or {}
    checks.append(_c("bok", "한은 금통위 일정", "ok" if bok.get("dates") else "warn",
                     f"{len(bok.get('dates') or [])}일 · {bok.get('source', '-')}" if bok.get("dates") else "일정 없음 — .env QUANT_BOK_DATES 로 넣으면 이벤트 위험에 반영"))
    fred = ops.get_state(app.engine, "fred_releases")
    checks.append(_c("fred", "CPI·고용 공식 일정", "ok" if fred.get("dates") else "warn",
                     f"FRED 공식 일정 {len(fred.get('dates') or [])}건" if fred.get("dates") else "FRED_API_KEY 없음 — 고용은 '첫째 금요일' 추정, CPI 는 빠짐"))
    return _section("event", "Event Truth", checks)


def earnings_conflicts(app, tol_days: int = 3) -> list[dict]:
    from datetime import date as _d

    from .data.db import session_scope
    from .data.models import SystemState
    out = []
    with session_scope(app.engine) as s:
        rows = s.scalars(select(SystemState).where(SystemState.key.like("profile:%"))).all()
        for r in rows:
            evs = [e for e in (((r.value or {}).get("data") or {}).get("events") or []) if e.get("kind") == "earnings" and not e.get("filed")]
            ds = {}
            for e in evs:
                try:
                    ds[e.get("source") or "?"] = _d.fromisoformat(str(e.get("date"))[:10])
                except ValueError:
                    continue
            if len(ds) >= 2 and (max(ds.values()) - min(ds.values())).days > tol_days:
                out.append({"symbol": r.key.split(":", 1)[1], "dates": [f"{k} {v}" for k, v in ds.items()]})
    return out


# ------------------------------------------------------------------ 4. Broker Truth
def broker_truth(app, now: datetime) -> dict:
    from .data.db import session_scope
    from .data.models import OrderRecord
    st = app.settings
    if st.broker != "kis":
        checks = [_c("broker", "증권사", "na", "가상 체결 (증권사 연결 없음) — 실전 전에 KIS 모의투자 검증 필요")]
    else:
        h = ops.get_state(app.engine, "health:broker")
        v = ops.get_state(app.engine, "kis_validation")
        rec = ops.get_state(app.engine, "reconcile")
        ws = ops.get_state(app.engine, "kis_ws")
        checks = [
            _c("broker_health", "증권사 호출", "bad" if int(h.get("fails") or 0) >= 2 else "warn" if h.get("fails") else "ok",
               f"연속 실패 {h.get('fails', 0)}회 · 마지막 성공 {label(h.get('ok_at')) or '-'}" + (f" · {h.get('error')}" if h.get("error") else "")),
            _c("validation", "KIS 검증 스위트", "ok" if v.get("ok") else "bad" if v else "warn",
               ("통과" if v.get("ok") else f"실패: {v.get('failed') or v.get('message')}") + f" · {label(v.get('at')) or '기록 없음'}"
               + (" · 주문 경로 확인" if v.get("order_path_verified") else "")),
            _c("reconcile", "잔고 대조 (증권사 = 진실)", "bad" if int(rec.get("streak") or 0) >= 2 else "warn" if rec.get("streak") else "ok",
               f"불일치 연속 {rec.get('streak', 0)}회 · 마지막 {label(rec.get('at')) or '-'}"),
            _c("ws", "실시간 연결", "ok" if ws.get("state") in ("on", "off") else "warn", f"{ws.get('state', '기록 없음')} · 체결 {ws.get('subs', 0)} · 호가 {ws.get('book_subs', 0)}"),
            _c("env", "계좌 종류", "ok", "모의투자 (실제 돈 아님)" if st.kis_env == "demo" else "실전 계좌 — 실제 돈"),
        ]
    with session_scope(app.engine) as s:
        open_ = s.scalar(select(func.count()).select_from(OrderRecord).where(
            OrderRecord.status.in_(("pending", "submitted")), OrderRecord.created_at < now - timedelta(minutes=30))) or 0
        unknown = s.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.status == "unknown",
                                                                               OrderRecord.created_at >= now - timedelta(days=7))) or 0
    checks.append(_c("open_orders", "30분 넘게 미확정 주문", "bad" if open_ else "ok", f"{open_}건 (재시작 복구가 증권사에서 확정)"))
    checks.append(_c("unknown", "상태 불명 주문 (7일)", "bad" if unknown else "ok", f"{unknown}건 — 증권사 앱에서 확인 필요" if unknown else "0건"))
    return _section("broker", "Broker Truth", checks)


# ------------------------------------------------------------------ 5. Portfolio Risk
def portfolio_truth(app, mode: str) -> dict:
    try:
        r = app.portfolio_risk(mode)
    except Exception as e:  # noqa: BLE001
        return _section("risk", "Portfolio Risk", [_c("calc", "리스크 계산", "bad", f"실패: {type(e).__name__}")])
    lim = r.get("limits") or {}
    if not r.get("n_positions"):
        return _section("risk", "Portfolio Risk", [_c("empty", "보유", "ok", f"{mode} 장부 보유 없음")])
    checks = [
        _c("var", "1일 VaR95", "bad" if (r.get("var95") or 0) > lim.get("max_var95", 0.04) else "ok", f"{(r.get('var95') or 0):.2%} · 한도 {lim.get('max_var95', 0.04):.0%} · ES {(r.get('es95') or 0):.2%}"),
        _c("sector", "업종 집중", "bad" if any(x["sector"] != "미분류" and x["weight"] > lim.get("max_sector_weight", 0.4) for x in r.get("sectors") or []) else "ok",
           ", ".join(f"{x['sector']} {x['weight']:.0%}" for x in (r.get("sectors") or [])[:3])),
        _c("concentration", "유효 종목 수", "warn" if (r.get("concentration") or {}).get("effective_n", 99) < 5 else "ok",
           f"{(r.get('concentration') or {}).get('effective_n')} · 최대 1종목 {(r.get('concentration') or {}).get('top1', 0):.0%}"),
        _c("stress", "최악 스트레스", "warn" if (r.get("worst_stress") or {}).get("loss", 0) > lim.get("max_stress_loss", 0.25) else "ok",
           f"{(r.get('worst_stress') or {}).get('name', '-')} −{(r.get('worst_stress') or {}).get('loss', 0):.1%}"),
        _c("tail", "꼬리 위험", "warn" if (r.get("var") or {}).get("fat_tail") else "ok",
           f"초과 첨도 {(r.get('var') or {}).get('excess_kurtosis')} · CF VaR {(r.get('var') or {}).get('cornish_fisher95', 0):.2%}"),
    ]
    return _section("risk", "Portfolio Risk", checks, {"mode": mode})


# ------------------------------------------------------------------ 6. Execution Truth
def execution_truth(app, now: datetime, days: int = 30) -> dict:
    from .data.db import session_scope
    from .data.models import OrderRecord
    from .trading.slippage import parity
    since = now - timedelta(days=days)
    with session_scope(app.engine) as s:
        rows = s.execute(select(OrderRecord.mode, OrderRecord.status, OrderRecord.client_order_id, OrderRecord.qty, OrderRecord.filled_qty,
                                OrderRecord.ref_price, OrderRecord.avg_price, OrderRecord.side)
                         .where(OrderRecord.created_at >= since)).all()
    st = Counter(r.status for r in rows)
    ids = [r.client_order_id for r in rows if r.client_order_id]
    dup = len(ids) - len(set(ids))
    partial = sum(1 for r in rows if r.status == "partial")
    no_ref = sum(1 for r in rows if r.status in ("filled", "partial") and (r.ref_price is None or r.avg_price is None))
    over = sum(1 for r in rows if r.filled_qty and r.qty and r.filled_qty > r.qty + 1e-9)
    live = [r for r in rows if r.mode == "live" and r.status in ("filled", "partial") and r.ref_price and r.avg_price]
    slip = [((r.avg_price - r.ref_price) / r.ref_price * 1e4) * (1 if r.side == "buy" else -1) for r in live]
    assumed = app.settings.costs.slippage_bps
    par = parity(ops.get_state(app.engine, "execution_parity").get("rows") or [])
    mean_slip = sum(slip) / len(slip) if slip else None
    checks = [
        _c("lifecycle", "주문 수명주기", "ok", " · ".join(f"{k} {v}" for k, v in st.most_common()) or f"최근 {days}일 주문 없음"),
        _c("idempotency", "멱등성 (같은 키 두 번)", "bad" if dup else "ok", f"중복 {dup}건 · 키 {len(ids)}개"),
        _c("overfill", "초과 체결", "bad" if over else "ok", f"{over}건"),
        _c("partial", "부분 체결 · 취소", "ok", f"부분체결 {partial} · 취소 {st.get('cancelled', 0)} · 미체결 {st.get('unfilled', 0)}"),
        _c("slip_record", "슬리피지 기록", "warn" if no_ref else "ok", f"기준가·체결가 없는 체결 {no_ref}건"),
        _c("slippage", "실측 슬리피지 vs 가정", "na" if mean_slip is None else "warn" if mean_slip > assumed * 1.5 else "ok",
           "실주문 체결 없음" if mean_slip is None else f"실측 평균 {mean_slip:.1f}bp (n={len(slip)}) · 가정 {assumed}bp"),
        _c("parity", "체결 시뮬레이터 정확도", "na" if not par.get("n") else "warn" if abs(par.get("bias_bps", 0)) > 5 else "ok",
           "비교 기록 없음" if not par.get("n") else f"n={par['n']} · 편향 {par['bias_bps']}bp · 평균 오차 {par['mae_bps']}bp"),
    ]
    return _section("execution", "Execution Truth", checks, {"by_status": dict(st)})


def report(app, mode: str | None = None, now: datetime | None = None, store: bool = True) -> dict:
    now = now or datetime.now(UTC)
    mode = mode or (app.settings.mode.value if app.settings.mode.value in ("paper", "shadow", "live") else "paper")
    secs = [market_clock(now), data_truth(app, now), event_truth(app, now), broker_truth(app, now), portfolio_truth(app, mode),
            execution_truth(app, now)]
    worst = max(RANK[s["status"]] for s in secs)
    out = {"at": now.isoformat(), "as_of": label(now), "mode": mode, "status": ["ok", "warn", "bad"][worst], "sections": secs,
           "bad": [f"{s['title']} · {c['label']}: {c['detail']}" for s in secs for c in s["checks"] if c["status"] == "bad"]}
    if store:
        ops.set_state(app.engine, "truth", {k: v for k, v in out.items() if k != "sections"}
                      | {"sections": [{k: v for k, v in s.items() if k not in ("freshness", "clock", "registry")} for s in secs]})
    return out


__all__ = ["report", "market_clock", "data_truth", "event_truth", "broker_truth", "portfolio_truth", "execution_truth",
           "corporate_action_scan", "earnings_conflicts"]

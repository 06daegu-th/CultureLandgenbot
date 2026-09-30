"""아침 브리핑 (평일 08:30 KST) · 일일 리포트 (장 마감 후 16:10 KST).

아침 브리핑 — 장 시작 전에 한 번에:
  오늘 일정(관심·보유 종목 실적 발표 D-0/D-1) · 밤사이 미국(SPY·QQQ·반도체·관심 미국 종목) · 어제 채점 결과 ·
  관심·보유 종목의 AI 예상 · 매크로·뉴스·섹터 에이전트 요약 · 검증 사다리 단계
일일 리포트 — 장 마감 후 하루 정리:
  시장 · 장부 손익 · 오늘 체결 · 오늘 예측/채점 · 성적표 · 독립 평가 · 장부 봉인 해시(외부 증거) · 드리프트 ·
  사다리 · 알림 수 · 내일 일정
둘 다 artifacts/reports/ 에 HTML 로 저장되고, 알림센터 · 텔레그램/디스코드(요약) · 웹 푸시로 보낸다.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select

from . import ops
from .data.db import session_scope
from .data.models import AlertRecord, ConsensusRecord, Instrument, JournalEntry, LedgerAnchor, PortfolioSnapshot

log = logging.getLogger(__name__)

KST = timedelta(hours=9)


def _kst_date(now: datetime) -> date:
    return (now.astimezone(UTC) + KST).date()


def _utc(t) -> datetime:
    t = pd.Timestamp(t)
    return (t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")).to_pydatetime()


def _pct(x, d=1):
    return "-" if x is None else f"{x * 100:+.{d}f}%"


def _names(s) -> dict[str, str]:
    return {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument))}


def _us_moves(app, syms=("SPY", "QQQ", "SOXX")) -> list[dict]:
    from .actions import watch_symbols
    from .data.db import load_bars
    want = list(dict.fromkeys([*syms, *[x for x in watch_symbols(app) if not x.isdigit()]]))[:8]
    out = []
    with session_scope(app.engine) as s:
        bars = load_bars(s, want)
        names = _names(s)
    for sym in want:
        b = bars.get(sym)
        if b is not None and len(b) >= 2:
            out.append({"symbol": sym, "name": names.get(sym, sym), "chg": float(b["close"].iloc[-1] / b["close"].iloc[-2] - 1),
                        "date": str(b.index[-1].date())})
    return out


def _focus_predictions(app, focus: dict, limit: int = 10) -> list[dict]:
    out = []
    with session_scope(app.engine) as s:
        names = _names(s)
        for sym in list(focus)[:30]:
            c = s.scalar(select(ConsensusRecord).where(ConsensusRecord.symbol == sym)
                         .order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()))
            if c is not None:
                out.append({"symbol": sym, "name": names.get(sym, sym), "action": c.action, "prob_up": c.prob_up,
                            "expected": (c.payload or {}).get("expected_return"), "horizon": (c.payload or {}).get("horizon", 5),
                            "as_of": str(c.as_of)[:10], "tags": sorted(focus.get(sym, []))})
    out.sort(key=lambda x: (x["action"] not in ("BUY", "SELL"), -abs(x["prob_up"] - 0.5)))
    return out[:limit]


def _scored_since(app, since: datetime) -> list[dict]:
    with session_scope(app.engine) as s:
        names = _names(s)
        rows = s.scalars(select(ConsensusRecord).where(ConsensusRecord.correct.is_not(None),
                                                       ConsensusRecord.as_of >= since - timedelta(days=12))
                         .order_by(ConsensusRecord.as_of.desc()).limit(300)).all()
        return [{"symbol": r.symbol, "name": names.get(r.symbol, r.symbol), "correct": r.correct, "actual": r.realized_return,
                 "expected": (r.payload or {}).get("expected_return"), "as_of": str(r.as_of)[:10]} for r in rows][:40]


def _today_events(app, focus: dict, today: date) -> list[dict]:
    from .data.fundamentals import with_d_day
    from .data.models import SystemState
    out = []
    with session_scope(app.engine) as s:
        names = _names(s)
        for r in s.scalars(select(SystemState).where(SystemState.key.like("profile:%"))):
            sym = r.key.split(":", 1)[1]
            if sym not in focus:
                continue
            for e in with_d_day((r.value or {}).get("data", {}).get("events") or [], today):
                if not e["past"] and e["d_day"] <= 1:
                    out.append({"symbol": sym, "name": names.get(sym, sym), **{k: e.get(k) for k in ("label", "date", "d_label", "time", "detail")}})
    return sorted(out, key=lambda x: x["date"])


# ------------------------------------------------------------------ 아침 브리핑
def morning_brief(app, now: datetime | None = None) -> dict:
    from .alerts import focus_symbols
    now = now or datetime.now(UTC)
    today = _kst_date(now)
    focus = focus_symbols(app)
    mb, nd, sv = (ops.get_state(app.engine, k) for k in ("macro_brief", "news_digest", "sector_view"))
    scored = _scored_since(app, now - timedelta(days=1))
    lad = ops.get_state(app.engine, "ladder")
    brief = {
        "kind": "morning", "date": today.isoformat(), "at": now.isoformat(),
        "events": _today_events(app, focus, today), "us": _us_moves(app), "predictions": _focus_predictions(app, focus),
        "scored": {"n": len(scored), "hits": sum(1 for x in scored if x["correct"]), "items": scored[:8]},
        "macro": {k: mb.get(k) for k in ("view", "risk_level", "stance", "watch", "source")} if mb else None,
        "news": {"summary": nd.get("summary"), "top": [{k: e.get(k) for k in ("title", "impact", "why")} for e in (nd.get("events") or [])[:4]]} if nd else None,
        "sectors": {k: sv.get(k) for k in ("view", "leaders", "laggards")} if sv else None,
        "ladder": lad.get("stage") or "backtest",
        "power": _power_line(app), "readiness": ops.get_state(app.engine, "readiness").get("status"),
    }
    brief |= _portfolio_risk_lines(app)
    brief["text"] = brief_text(brief)
    return brief


def brief_text(b: dict) -> str:
    L = [f"🌅 아침 브리핑 {b['date']}"]
    if b["events"]:
        L.append("📅 오늘·내일 일정")
        L += [f" · {e['name']} {e['label']} {e['d_label']}" + (f" ({e['time']})" if e.get("time") else "") for e in b["events"][:5]]
    if b["us"]:
        L.append("🇺🇸 밤사이 미국: " + " · ".join(f"{u['name'].split(' (')[0]} {_pct(u['chg'])}" for u in b["us"][:6]))
    if b["scored"]["n"]:
        L.append(f"🎯 최근 채점 {b['scored']['n']}건 · 적중 {b['scored']['hits']}건")
    if b["predictions"]:
        L.append("🤖 관심·보유 종목 AI 예상")
        L += [f" · {p['name']} {p['action']} 상승 {p['prob_up']:.0%}" + (f" · 예상 {_pct(p['expected'])}" if p.get("expected") is not None else "")
              for p in b["predictions"][:6]]
    if b.get("macro") and b["macro"].get("view"):
        L.append(f"🌐 매크로 ({b['macro'].get('risk_level', '-')} 위험): {b['macro']['view']}")
    if b.get("news") and b["news"].get("summary"):
        L.append(f"📰 뉴스: {b['news']['summary']}")
    if b.get("sectors") and b["sectors"].get("leaders"):
        L.append("🏭 강한 업종: " + ", ".join(b["sectors"]["leaders"][:3]))
    if b.get("portfolio"):
        pf = b["portfolio"]
        L.append(f"💼 내 포트폴리오: {pf['total']:,}원 · 주식 {pf['stock_pct']:.0%} / 현금 {pf['cash_pct']:.0%} · 위험 {pf['risk_level']}")
    if b.get("risks"):
        L.append("⚠️ 위험 요소")
        L += [f" · {x}" for x in b["risks"][:3]]
    if b.get("check3"):
        L.append("✅ 오늘 확인할 것: " + " · ".join(f"{c['name']}({', '.join(c['why'][:1])})" for c in b["check3"][:3]))
    L.append(f"🪜 검증 단계: {b['ladder']}")
    if b.get("readiness"):
        L.append(f"🚦 매매 준비: {b['readiness']}")
    if b.get("power"):
        L.append(f"🧪 {b['power']}")
    return "\n".join(L)


def _portfolio_risk_lines(app) -> dict:
    """브리핑용: 내 포트폴리오 한 줄 · 가장 큰 위험 3개 · 오늘 확인할 종목 3개 (실패해도 브리핑은 나간다)."""
    out: dict = {}
    mode = app.settings.mode.value if app.settings.mode.value in ("paper", "shadow", "live") else "paper"
    try:
        from .portfolio_os import overview
        o = overview(app, mode)
        if not o.get("empty"):
            out["portfolio"] = {k: o[k] for k in ("total", "stock_pct", "cash_pct", "risk_level", "source")}
            out["risks"] = [o["biggest_risk"], *o.get("other_risks", [])][:3]
    except Exception as e:  # noqa: BLE001
        log.warning("브리핑 포트폴리오 요약 실패: %s", e)
    try:
        from .center import action_center
        out["check3"] = action_center(app, mode)["check"][:3]
    except Exception as e:  # noqa: BLE001
        log.warning("브리핑 확인 목록 실패: %s", e)
    return out


def _power_line(app) -> str | None:
    """예측력 전진 검증 진행 한 줄 (브리핑·리포트)."""
    fw = ops.get_state(app.engine, "prediction_power").get("forward") or {}
    if not fw:
        return None
    if fw.get("decision") in ("H1", "H0"):
        return f"예측력 전진 검증: {fw.get('label')} ({fw.get('scored')}건)"
    hr = "-" if fw.get("hit_rate") is None else f"{fw['hit_rate']:.0%}"
    return (f"예측력 전진 검증: 등록 후 채점 {fw.get('scored', 0)}건 · 적중 {hr} · 약 {fw.get('more_needed') or '-'}건 더 필요"
            + (f" · 예상 판정 {fw['eta']}" if fw.get("eta") else ""))


# ------------------------------------------------------------------ 일일 리포트
def daily_report(app, now: datetime | None = None) -> dict:
    from .review.scorecard import scorecard
    now = now or datetime.now(UTC)
    day = _kst_date(now)
    start = datetime.combine(day, datetime.min.time(), UTC) - KST
    with session_scope(app.engine) as s:
        names = _names(s)
        books = {}
        for mode in ("paper", "shadow", "live", "attr-core", "attr-full", "us-paper"):
            snaps = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode)
                              .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.id.desc()).limit(400)).all()
            if len(snaps) >= 1:
                last = snaps[0]
                prev = next((x for x in snaps if _utc(x.ts) < start), None)  # 오늘 시작 전 마지막 평가
                books[mode] = {"equity": last.equity, "day_pnl": (last.equity - prev.equity) if prev else None,
                               "day_ret": (last.equity / prev.equity - 1) if prev and prev.equity else None}
        trades = [{"mode": j.mode, "symbol": j.symbol, "name": names.get(j.symbol, j.symbol), "side": (j.data or {}).get("side"),
                   "qty": (j.data or {}).get("qty"), "price": (j.data or {}).get("fill_price")}
                  for j in s.scalars(select(JournalEntry).where(JournalEntry.kind == "order", JournalEntry.ts >= start,
                                                                ~JournalEntry.mode.startswith("attr-")).order_by(JournalEntry.ts))
                  if (j.data or {}).get("status") == "filled"][:40]
        made = s.scalar(select(func.count()).select_from(ConsensusRecord).where(ConsensusRecord.created_at >= start)) or 0
        alerts = s.scalar(select(func.count()).select_from(AlertRecord).where(AlertRecord.ts >= start)) or 0
        anchor = s.scalar(select(LedgerAnchor).order_by(LedgerAnchor.id.desc()))
        sc = scorecard(s, "KR", 100, names=names, recent=10)
    ev, dr, lad = (ops.get_state(app.engine, k) for k in ("evaluation", "drift", "ladder"))
    pulse = ops.get_state(app.engine, "market_pulse").get("hist", [])
    from .alerts import focus_symbols
    tomorrow = _today_events(app, focus_symbols(app), day + timedelta(days=1))
    rep = _portfolio_risk_lines(app) | {
        "kind": "daily", "date": day.isoformat(), "at": now.isoformat(), "books": books, "trades": trades,
        "predictions_made": int(made), "alerts": int(alerts), "scorecard": sc["summary"], "recent": sc["recent"][:10],
        "market": pulse[-1] if pulse else None,
        "evaluation": {k: ev.get(k) for k in ("verdict", "hit_rate", "p_value", "best_baseline", "n")} if ev else None,
        "ledger": {"digest": anchor.digest, "upto_id": anchor.upto_id, "at": anchor.ts.isoformat()} if anchor else None,
        "drift": {k: dr.get(k) for k in ("status", "message")} if dr else None,
        "ladder": {k: lad.get(k) for k in ("stage", "reasons")} if lad else None, "tomorrow": tomorrow,
        "power": _power_line(app), "readiness": ops.get_state(app.engine, "readiness").get("status"),
    }
    rep["text"] = report_text(rep)
    return rep


def report_text(r: dict) -> str:
    L = [f"📊 일일 리포트 {r['date']}"]
    if r.get("market"):
        L.append(f"🌐 시장 {r['market'].get('label')} {r['market'].get('score')}점")
    for m in ("paper", "live", "us-paper"):
        b = r["books"].get(m)
        if b:
            L.append(f"💼 {m}: {b['equity']:,.0f} ({_pct(b['day_ret'], 2)})")
    if r["trades"]:
        L.append(f"🧾 체결 {len(r['trades'])}건")
    sc = r["scorecard"] or {}
    L.append(f"🤖 오늘 예측 {r['predictions_made']}건 · 누적 적중 {sc.get('hit_rate', 0) * 100:.0f}% (최근 {sc.get('n', 0)}회)"
             if sc.get("n") else f"🤖 오늘 예측 {r['predictions_made']}건 · 채점 대기")
    if r.get("evaluation") and r["evaluation"].get("verdict"):
        L.append(f"🔬 독립 평가: {r['evaluation']['verdict']}")
    if r.get("drift"):
        L.append(f"📈 드리프트: {r['drift'].get('message')}")
    if r.get("ladder"):
        L.append(f"🪜 검증 단계: {r['ladder'].get('stage')}")
    if r.get("readiness"):
        L.append(f"🚦 매매 준비: {r['readiness']}")
    if r.get("power"):
        L.append(f"🧪 {r['power']}")
    if r.get("ledger"):
        L.append(f"🔒 장부 봉인 #{r['ledger']['upto_id']}: {r['ledger']['digest'][:16]}… (이 해시가 외부 증거입니다)")
    if r["tomorrow"]:
        L.append("📅 내일: " + ", ".join(f"{e['name']} {e['label']}" for e in r["tomorrow"][:4]))
    if r.get("risks"):
        L.append("⚠️ 위험 요소: " + " · ".join(r["risks"][:3]))
    return "\n".join(L)


# ------------------------------------------------------------------ HTML 저장
CSS = """body{font-family:Pretendard,-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#0b1220;color:#e8eefb;margin:0;padding:24px;line-height:1.55}
.w{max-width:860px;margin:0 auto}h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px;color:#9fb3d9}
.card{background:#111c33;border:1px solid #22345f;border-radius:14px;padding:16px;margin:12px 0}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:6px 4px;border-bottom:1px solid #1d2c52;text-align:left}
.up{color:#f0474f}.down{color:#3b8cff}.dim{color:#8a9bbd;font-size:12px}.mono{font-family:ui-monospace,monospace;word-break:break-all}
.kpi{display:flex;gap:18px;flex-wrap:wrap}.kpi div{min-width:120px}.kpi b{display:block;font-size:20px}
@media (prefers-color-scheme: light){body{background:#f5f7fb;color:#0f172a}.card{background:#fff;border-color:#e1e7f2}td,th{border-color:#e9eef6}h2{color:#475a7a}}"""


def _cls(x):
    return "" if x is None else "up" if x >= 0 else "down"


def to_html(r: dict) -> str:
    e = html.escape
    title = "아침 브리핑" if r["kind"] == "morning" else "일일 리포트"
    parts = [f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Quant AI {title} {e(r['date'])}</title><style>{CSS}</style></head><body><div class='w'>",
             f"<h1>{'🌅' if r['kind'] == 'morning' else '📊'} {title}</h1><div class='dim'>{e(r['date'])} · 생성 {e(r['at'][:16])} UTC</div>"]
    if r["kind"] == "morning":
        if r["events"]:
            parts.append("<h2>오늘·내일 일정</h2><div class='card'><table>" + "".join(
                f"<tr><td>{e(x['name'])}</td><td>{e(x['label'])}</td><td>{e(x['d_label'])}</td><td class='dim'>{e(x.get('detail') or '')}</td></tr>" for x in r["events"]) + "</table></div>")
        if r["us"]:
            parts.append("<h2>밤사이 미국</h2><div class='card kpi'>" + "".join(
                f"<div><span class='dim'>{e(u['name'])}</span><b class='{_cls(u['chg'])}'>{_pct(u['chg'])}</b></div>" for u in r["us"]) + "</div>")
        if r["predictions"]:
            parts.append("<h2>관심·보유 종목 AI 예상</h2><div class='card'><table><tr><th>종목</th><th>신호</th><th>상승 확률</th><th>예상</th></tr>" + "".join(
                f"<tr><td>{e(p['name'])}</td><td>{e(p['action'])}</td><td>{p['prob_up']:.0%}</td><td class='{_cls(p.get('expected'))}'>{_pct(p.get('expected'))}</td></tr>" for p in r["predictions"]) + "</table></div>")
        for key, label in (("macro", "매크로"), ("news", "뉴스"), ("sectors", "업종")):
            v = r.get(key) or {}
            txt = v.get("view") or v.get("summary")
            if txt:
                parts.append(f"<h2>{label}</h2><div class='card'>{e(txt)}</div>")
        parts.append(f"<div class='card dim'>최근 채점 {r['scored']['n']}건 · 적중 {r['scored']['hits']}건 · 검증 단계 {e(str(r['ladder']))}</div>")
    else:
        parts.append("<h2>장부</h2><div class='card kpi'>" + "".join(
            f"<div><span class='dim'>{e(m)}</span><b>{b['equity']:,.0f}</b><span class='{_cls(b.get('day_ret'))}'>{_pct(b.get('day_ret'), 2)}</span></div>"
            for m, b in r["books"].items()) + "</div>")
        sc = r.get("scorecard") or {}
        parts.append("<h2>AI 예측</h2><div class='card kpi'>"
                     f"<div><span class='dim'>오늘 예측</span><b>{r['predictions_made']}</b></div>"
                     f"<div><span class='dim'>적중률(최근 {sc.get('n', 0)})</span><b>{(sc.get('hit_rate') or 0) * 100:.1f}%</b></div>"
                     f"<div><span class='dim'>비용 후 신호 수익</span><b class='{_cls(sc.get('avg_signal_net'))}'>{_pct(sc.get('avg_signal_net'), 2)}</b></div>"
                     f"<div><span class='dim'>알림</span><b>{r['alerts']}</b></div></div>")
        if r["recent"]:
            parts.append("<div class='card'><table><tr><th>종목</th><th>신호</th><th>예상</th><th>실제</th><th></th></tr>" + "".join(
                f"<tr><td>{e(x['name'])}</td><td>{e(x['action'])}</td><td>{_pct(x.get('expected'))}</td><td class='{_cls(x['actual'])}'>{_pct(x['actual'])}</td>"
                f"<td>{'✔' if x['hit'] else '✘ ' + e(x.get('miss_reason') or '')}</td></tr>" for x in r["recent"]) + "</table></div>")
        if r["trades"]:
            parts.append("<h2>오늘 체결</h2><div class='card'><table>" + "".join(
                f"<tr><td>{e(t['mode'])}</td><td>{e(t['name'])}</td><td>{'매수' if t['side'] == 'buy' else '매도'}</td><td>{t['qty']}</td><td>{(t['price'] or 0):,.0f}</td></tr>"
                for t in r["trades"]) + "</table></div>")
        for key, label, field in (("evaluation", "독립 평가", "verdict"), ("drift", "데이터 드리프트", "message")):
            v = r.get(key) or {}
            if v.get(field):
                parts.append(f"<h2>{label}</h2><div class='card'>{e(str(v[field]))}</div>")
        if r.get("ledger"):
            parts.append(f"<h2>예측 장부 봉인</h2><div class='card'><div class='dim'>예측 #{r['ledger']['upto_id']} 까지</div>"
                         f"<div class='mono'>{e(r['ledger']['digest'])}</div><div class='dim'>이 해시를 외부(텔레그램 등)에 남겨 두면 이후 기록을 고쳤을 때 드러납니다.</div></div>")
        if r["tomorrow"]:
            parts.append("<h2>내일 일정</h2><div class='card'>" + "<br>".join(f"{e(x['name'])} {e(x['label'])} {e(x['d_label'])}" for x in r["tomorrow"]) + "</div>")
    parts.append("<p class='dim'>정보 제공 목적이며 투자 권유나 수익 보장이 아닙니다.</p></div></body></html>")
    return "".join(parts)


def report_dir(app) -> Path:
    d = Path(app.settings.artifacts_dir) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def publish(app, r: dict) -> dict:
    """HTML 저장 + 알림센터 + 텔레그램(요약) + 웹 푸시. 같은 날 같은 종류는 한 번만 알린다."""
    from .alerts import push
    name = f"{r['kind']}-{r['date']}.html"
    path = report_dir(app) / name
    path.write_text(to_html(r), encoding="utf-8")
    (report_dir(app) / name.replace(".html", ".json")).write_text(json.dumps(r, ensure_ascii=False, default=str), encoding="utf-8")
    title = "아침 브리핑" if r["kind"] == "morning" else "일일 리포트"
    first = r["text"].split("\n")
    push(app.engine, "brief" if r["kind"] == "morning" else "report", f"{title} {r['date']}",
         " · ".join(first[1:3])[:280], level="info", link=f"#reports/{name}", dedupe=f"{r['kind']}:{r['date']}")
    return {"file": name, "path": str(path)}


def due(now: datetime, kind: str) -> bool:
    """평일 08:30~09:00 (아침) / 16:10~17:30 (리포트) KST 안에 있나."""
    loc = now.astimezone(UTC) + KST
    if loc.weekday() >= 5:
        return False
    t = loc.hour * 60 + loc.minute
    return 510 <= t < 540 if kind == "morning" else 970 <= t < 1050


def run_if_due(app, kind: str, now: datetime | None = None, force: bool = False) -> dict | None:
    now = now or datetime.now(UTC)
    day = _kst_date(now).isoformat()
    key = f"report_sent:{kind}"
    if not force and (not due(now, kind) or ops.get_state(app.engine, key).get("date") == day):
        return None
    r = morning_brief(app, now) if kind == "morning" else daily_report(app, now)
    out = publish(app, r)
    app.notifier.send(r["text"], "info")
    ops.set_state(app.engine, key, {"date": day, "file": out["file"]})
    return {**out, "text": r["text"]}


def list_reports(app, limit: int = 60) -> list[dict]:
    d = report_dir(app)
    out = []
    for p in sorted(d.glob("*.html"), reverse=True)[:limit]:
        kind, _, dt = p.stem.partition("-")
        out.append({"file": p.name, "kind": kind, "date": dt, "size": p.stat().st_size})
    return sorted(out, key=lambda x: (x["date"], x["kind"]), reverse=True)


__all__ = ["morning_brief", "daily_report", "to_html", "publish", "run_if_due", "list_reports", "due"]

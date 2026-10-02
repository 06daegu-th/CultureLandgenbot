"""데이터 충돌 감지 — 소스끼리 말이 다르면 숨기지 않고 드러낸다 (어느 쪽이 맞는지 모를 때는 '확인 필요').

price      1차(KRX) vs 2차(Yahoo) 겹치는 날 비율이 흔들림 · 증권사 실시간 시세 vs DB 종가 ±30% (분할·오류 의심)
earnings   같은 종목의 다음 실적일이 소스마다 다름 (네이버 컨센서스 · Yahoo 프로필 · SEC 8-K/DART 실제 공시) — 7일 이상 차이
news       같은 소식 묶음 안에서 매체마다 톤이 반대 (긍정 기사와 부정 기사가 함께) — '해석 엇갈림'
저장된 기록만 읽는다 (외부 호출 없음).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from . import ops
from .asof import label


def _earnings_dates(app, symbol: str, today: date) -> list[tuple[str, date]]:
    out = []
    prof = ops.get_state(app.engine, f"profile:{symbol}").get("data") or {}
    for e in prof.get("events") or []:
        if e.get("kind") == "earnings":
            try:
                out.append(("Yahoo 프로필" + (" (추정)" if e.get("estimated") else ""), date.fromisoformat(str(e["date"])[:10])))
            except (KeyError, ValueError):
                pass
    kc = ops.get_state(app.engine, f"krcons:{symbol}")
    if kc.get("next_date"):
        try:
            out.append(("네이버 컨센서스", date.fromisoformat(str(kc["next_date"])[:10])))
        except ValueError:
            pass
    return [(src, d) for src, d in out if d >= today - timedelta(days=3)]


def detect(app, now: datetime | None = None, symbols: list[str] | None = None) -> dict:
    from .data.db import session_scope
    from .data.models import NewsArticle
    from .news_llm import tone_value
    now = now or datetime.now(UTC)
    today = now.date()
    rows = []
    # 가격
    fo = ops.get_state(app.engine, "source_failover")
    for x in fo.get("skipped") or []:
        if x.get("reason"):
            rows.append({"kind": "price", "symbol": x.get("symbol"), "level": "warn", "title": f"2차 시세와 맞지 않아 채우지 않음: {x['reason']}",
                         "sources": ["KRX", "Yahoo"]})
    for sym in ops.get_state(app.engine, "live_quotes").get("conflicts") or []:
        rows.append({"kind": "price", "symbol": sym, "level": "bad", "title": "증권사 시세와 DB 종가가 ±30% 넘게 다름 (분할·오류 확인 전 매매 차단)",
                     "sources": ["KIS", "DB"]})
    # 실적일
    if symbols is None:
        from .alerts import focus_symbols
        symbols = list(focus_symbols(app))[:60]
    for sym in symbols:
        ds = _earnings_dates(app, sym, today)
        if len(ds) >= 2:
            lo, hi = min(d for _, d in ds), max(d for _, d in ds)
            if (hi - lo).days >= 7:
                rows.append({"kind": "earnings", "symbol": sym, "level": "warn",
                             "title": "다음 실적일이 소스마다 다름: " + " · ".join(f"{s} {d.isoformat()}" for s, d in ds) + " → 회사 IR·공시로 확인",
                             "sources": [s for s, _ in ds]})
    # 뉴스 해석 엇갈림
    with session_scope(app.engine) as s:
        arts = [(a.cluster, a.source, tone_value(a.sentiment, a.extract), a.title, a.symbols or []) for a in s.scalars(
            select(NewsArticle).where(NewsArticle.published_at >= now - timedelta(days=3), NewsArticle.cluster.is_not(None)).limit(3000))]
    groups: dict[str, list] = {}
    for c, src, t, title, syms in arts:
        groups.setdefault(c, []).append((src, t, title, syms))
    for c, g in groups.items():
        pos = sorted({src for src, t, *_ in g if t > 0.2})
        neg = sorted({src for src, t, *_ in g if t < -0.2})
        if pos and neg:
            rows.append({"kind": "news", "symbol": (g[0][3] or [None])[0], "level": "info", "cluster": c,
                         "title": f"같은 소식, 해석 엇갈림: '{g[0][2][:50]}' — 긍정 {', '.join(pos)} / 부정 {', '.join(neg)}",
                         "sources": pos + neg})
    order = {"bad": 0, "warn": 1, "info": 2}
    rows.sort(key=lambda r: order.get(r["level"], 3))
    cnt = {k: sum(1 for r in rows if r["kind"] == k) for k in ("price", "earnings", "news")}
    out = {"rows": rows[:100], "counts": cnt, "n": len(rows), "as_of": label(now),
           "headline": "소스 간 충돌 없음" if not rows else f"충돌 {len(rows)}건 — 가격 {cnt['price']} · 실적일 {cnt['earnings']} · 뉴스 해석 {cnt['news']}",
           "note": "충돌 = 어느 쪽이 틀렸다는 뜻이 아니라 '확인 필요' · 가격 충돌(bad)은 매매 차단 조건과 연결됨"}
    ops.set_state(app.engine, "data_conflicts", {k: v for k, v in out.items() if k != "rows"} | {"rows": rows[:30]})
    return out


__all__ = ["detect"]

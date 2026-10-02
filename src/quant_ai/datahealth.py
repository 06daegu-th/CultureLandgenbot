"""DATA HEALTH — 데이터 종류별 신뢰도 점수(%)와 출처 추적.

  주가      국내 일봉 기준일(휴장일 반영 밀림) · 관심/보유 종목 커버리지 · 품질 경고 비율 · 장중 실시간 시세 지연
  뉴스      마지막 수집(SLA 3시간) · 수집 시각이 기록된 비율(출처 추적)
  공시      DART 마지막 수집(SLA 2일) · 요약 비율 (DART 키 없으면 '해당 없음')
  재무      보유·관심 종목의 재무/지표 캐시가 있고 36시간 안인 비율
  경제지표  FRED 마지막 값(SLA 4일) (키 없으면 '해당 없음')
  AI 판단   마지막 AI 합의 나이
전체 = 해당 있는 항목의 가중 평균 (주가 40% · 뉴스 15 · 공시 15 · 재무 10 · 경제 10 · AI 10).
가격이 장중 15분 넘게 지연되면 BLOCK — Truth Center 가 DATA 관문을 빨강으로 만들어 신규 매수를 막는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from . import ops
from .asof import freshness, label

WEIGHTS = {"price": 0.40, "news": 0.15, "disclosure": 0.15, "fundamental": 0.10, "macro": 0.10, "ai": 0.10}
NAMES = {"price": "주가", "news": "뉴스", "disclosure": "공시", "fundamental": "재무", "macro": "경제지표", "ai": "AI 판단"}
PRICE_DELAY_BLOCK_S = 15 * 60


def _by_status(st: str | None) -> float | None:
    return {"fresh": 1.0, "stale": 0.7, "old": 0.25, "none": 0.0}.get(st or "none")


def price_delay(app, now: datetime | None = None) -> dict:
    """장중 실시간 시세 지연 (초). 장외면 판단 안 함."""
    from .clock import KRX, Phase
    now = now or datetime.now(UTC)
    lq = ops.get_state(app.engine, "live_quotes")
    at = lq.get("_at")
    if KRX.phase(now) is not Phase.OPEN:
        return {"open": False, "delay_s": None, "block": False, "detail": "장외 — 실시간 지연 판단 안 함"}
    if not at:
        return {"open": True, "delay_s": None, "block": False, "detail": "실시간 시세 기록 없음 (일봉만 사용 — 가상 장부 기준)"}
    d = (now - datetime.fromisoformat(at)).total_seconds()
    return {"open": True, "delay_s": int(d), "block": d > PRICE_DELAY_BLOCK_S,
            "detail": f"마지막 실시간 시세 {d / 60:.0f}분 전" + (" → TRADING BLOCKED (15분 초과)" if d > PRICE_DELAY_BLOCK_S else "")}


def report(app, now: datetime | None = None) -> dict:
    from .alerts import focus_symbols
    from .data.db import session_scope
    from .data.models import Disclosure, NewsArticle, SystemState
    now = now or datetime.now(UTC)
    fr = freshness(app, now)["items"]
    cats = {}
    # 주가
    bar = fr.get("bar_kr") or {}
    lag = int(bar.get("lag_days") or 0)
    focus = [s for s in focus_symbols(app) if s[:1].isdigit()]
    bars, _, _ = app.market_data()
    have = [s for s in focus if s in bars and len(bars[s])]
    cov = len(have) / len(focus) if focus else 1.0
    tr = ops.get_state(app.engine, "truth")
    q_issue = next((c for s in tr.get("sections") or [] for c in s["checks"] if c["key"] == "quality"), None)
    q_pen = 0.03 if q_issue and q_issue["status"] == "warn" else 0.0
    pdl = price_delay(app, now)
    price = max(0.0, (1.0 if lag <= 0 else 0.8 if lag == 1 else 0.6 if lag == 2 else 0.25) * cov - q_pen)
    if pdl["block"]:
        price = min(price, 0.4)
    cats["price"] = {"score": price, "detail": f"국내 일봉 {bar.get('label') or '없음'} · {bar.get('age') or '-'} · 관심/보유 {len(have)}/{len(focus)}종목"
                     + (f" · {pdl['detail']}" if pdl["open"] else ""), "source": "KRX (marcap) · 실시간: KIS/네이버", "as_of": bar.get("at")}
    with session_scope(app.engine) as s:
        n_news = s.scalar(select(func.count()).select_from(NewsArticle)) or 0
        n_news_prov = s.scalar(select(func.count()).select_from(NewsArticle).where(NewsArticle.collected_at.is_not(None))) or 0
        n_disc = s.scalar(select(func.count()).select_from(Disclosure)) or 0
        n_disc_sum = s.scalar(select(func.count()).select_from(Disclosure).where(Disclosure.summary.is_not(None))) or 0
        profs = {r.key.split(":", 1)[1]: r.updated_at for r in s.scalars(select(SystemState).where(SystemState.key.like("profile:%")))}
    nw = fr.get("news") or {}
    if n_news:
        prov = n_news_prov / n_news
        cats["news"] = {"score": _by_status(nw.get("status")) * (0.9 + 0.1 * prov), "detail": f"마지막 {nw.get('label') or '-'} · {nw.get('age') or '-'} · 수집 시각 기록 {prov:.0%}",
                        "source": "RSS · 네이버 · Yahoo", "as_of": nw.get("at")}
    else:
        cats["news"] = {"score": 0.0, "detail": "뉴스 없음 — 수집 작업 확인", "source": "RSS · 네이버 · Yahoo", "as_of": None}
    ds = fr.get("disclosure") or {}
    if app.settings.dart_api_key or n_disc:
        cats["disclosure"] = {"score": _by_status(ds.get("status")) if n_disc else 0.0,
                              "detail": f"마지막 {ds.get('label') or '-'} · 요약 {n_disc_sum}/{n_disc}", "source": "DART", "as_of": ds.get("at")}
    fnd = [s_ for s_ in focus if s_ in profs and (now - (profs[s_] if profs[s_].tzinfo else profs[s_].replace(tzinfo=UTC))).total_seconds() < 36 * 3600]
    cats["fundamental"] = {"score": len(fnd) / len(focus) if focus else 0.0, "detail": f"보유·관심 {len(fnd)}/{len(focus)}종목 재무·지표 36시간 안",
                           "source": "Yahoo · 네이버 컨센서스", "as_of": None}
    mc = fr.get("macro") or {}
    if app.settings.fred_api_key or mc.get("at"):
        cats["macro"] = {"score": _by_status(mc.get("status")), "detail": f"FRED 마지막 {mc.get('label') or '-'}", "source": "FRED", "as_of": mc.get("at")}
    ai = fr.get("consensus") or {}
    cats["ai"] = {"score": _by_status(ai.get("status")), "detail": f"마지막 AI 판단 {ai.get('label') or '-'} · {ai.get('age') or '-'}", "source": "AI 합의", "as_of": ai.get("at")}
    tot_w = sum(WEIGHTS[k] for k in cats)
    overall = sum(WEIGHTS[k] * v["score"] for k, v in cats.items()) / tot_w if tot_w else 0.0
    rows = [{"key": k, "name": NAMES[k], "pct": round(v["score"] * 100, 1),
             "status": "ok" if v["score"] >= 0.9 else "warn" if v["score"] >= 0.6 else "bad", **{x: v[x] for x in ("detail", "source", "as_of")}}
            for k, v in cats.items()]
    rows += [{"key": k, "name": NAMES[k], "pct": None, "status": "na", "detail": "키 없음 — 해당 없음 (점수에서 제외)", "source": "-", "as_of": None}
             for k in WEIGHTS if k not in cats]
    blocked = pdl["block"] or cats["price"]["score"] < 0.5
    out = {"at": now.isoformat(), "as_of": label(now), "overall": round(overall * 100, 1), "rows": rows, "price_delay": pdl,
           "trading": "BLOCKED" if blocked else "OK",
           "block_reason": pdl["detail"] if pdl["block"] else (f"주가 데이터 신뢰도 {cats['price']['score']:.0%} — {cats['price']['detail']}" if blocked else None)}
    ops.set_state(app.engine, "data_health", out)
    return out


__all__ = ["report", "price_delay", "PRICE_DELAY_BLOCK_S"]

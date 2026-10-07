"""v36 'AI 가 무엇을 보고 판단했나' — 판단 하나에 실제로 들어간 자료와 빠진 자료를 숨김없이.

사용자가 가장 먼저 묻는 것: "이 AI 가 뉴스·공시·커뮤니티·경제까지 정말 보고 판단한 거야?"
  · for_record: 봉인된 합의 판단(ConsensusRecord)의 evidence 스냅샷 = 그때 AI 들이 받은 재료 그대로.
    재료마다 used(들어감) / empty(없어서 빠짐) + 몇 건·언제 + 누가 읽었나(LLM 인지 규칙인지).
  · coverage: 지금 이 서버가 모으고 있는 자료 (전체) — 수집이 꺼졌는지, 종목에 없었던 건지 구분.
지어내지 않는다: 기록에 없는 것은 '없음' 으로 둔다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select


LABELS = {"primary": "뉴스 AI", "nvidia": "경제·시장 AI", "panel": "공시·실적 AI", "quant": "차트·Quant AI", "chart": "차트 신호",
          "regime": "시장 국면", "risk": "Risk AI", "challenger": "도전자 모델"}
SOURCES = (  # key, 이름, 누가 읽나
    ("chart", "차트·거래량", "차트·Quant AI · 신호 엔진"),
    ("news", "뉴스", "뉴스 AI"),
    ("disclosure", "공시", "뉴스 AI · 공시·실적 AI"),
    ("flow", "외국인·기관 수급", "AI 입력 · 신호 엔진"),
    ("community", "커뮤니티", "뉴스 AI (약하게 · 역지표)"),
    ("macro", "경제지표", "경제·시장 AI"),
    ("market", "시장 상태·국면", "경제·시장 AI · 시장 국면"),
    ("sector", "업종 흐름", "AI 입력"),
    ("events", "일정 (실적·FOMC 등)", "Risk AI"),
)
RULE_BACKENDS = ("heuristic", "rules", "regime-engine")
CORE = ("news", "disclosure", "flow", "community", "macro")  # '차트만 본 판단' 인지 가르는 재료


def _llm(backend: str | None) -> bool:
    b = str(backend or "")
    if b.startswith("rules+"):
        return True
    return bool(b) and not b.startswith(("sklearn", "rules", "heuristic", "signals2")) and b not in RULE_BACKENDS


def for_record(payload: dict | None) -> dict:
    """봉인된 판단 payload → 재료 표. 같은 기록이면 언제 봐도 같은 결과 (새로 계산하지 않음)."""
    p = payload or {}
    ev = p.get("evidence") or {}
    price = ev.get("price") or {}
    news = ev.get("news") or []
    discs = ev.get("disclosures") or []
    macro = {k: v for k, v in (ev.get("macro") or {}).items() if not str(k).startswith("_")} if isinstance(ev.get("macro"), dict) else {}
    comm = ev.get("community") or {}
    flow = ev.get("flow") or {}
    market = ev.get("market") or {}
    regime = (ev.get("regime") or {}).get("regime")
    sector = ev.get("sector") or {}
    events = ev.get("events") or []
    sig = ev.get("signal") or {}
    n_art = sum(int(x.get("n_articles") or 1) for x in news if isinstance(x, dict))
    rows = {
        "chart": (bool(price.get("last_bar") or price.get("last_close")),
                  (f"{str(price.get('last_bar') or '')[:10]} 일봉까지" if price.get("last_bar") else "일봉 없음")
                  + (f" · 가격 신호 점수 {float(sig['score']):+.1f} ({(sig.get('tier') or {}).get('label') or '-'})" if sig.get("score") is not None else "")),
        "news": (bool(news), f"{len(news)}개 소식 (기사 {n_art}건)" if news else "판단 전 72시간 이 종목 뉴스 0건"),
        "disclosure": (bool(discs), f"{len(discs)}건 · " + ", ".join(str(d.get("title") or "")[:18] for d in discs[:2]) if discs else "최근 7일 공시 0건"),
        "flow": (bool(flow), "최근 순매수 흐름 반영" if flow else "수급 자료 없음 (국내 · 4일 이내만)"),
        "community": (bool(comm), f"글 {comm.get('posts') or 0}개 · {comm.get('label') or ''}".strip(" ·") if comm else "커뮤니티 자료 없음 (6시간 이내만)"),
        "macro": (bool(macro), f"{len(macro)}개 지표 (금리·환율·변동성 등)" if macro else "경제지표 없음"),
        "market": (bool(regime or market.get("label")),
                   " · ".join(x for x in (f"국면 {regime}" if regime else "", f"시장 {market.get('label')}" if market.get("label") else "") if x) or "없음"),
        "sector": (bool(sector), str(sector.get("sector") or sector.get("name") or "업종 순위 반영") if sector else "업종 자료 없음"),
        "events": (True, f"다가오는 일정 {len(events)}개 확인" if events else "다가오는 큰 일정 없음 (확인함)"),
    }
    sources = [{"key": k, "name": n, "reader": r, "used": rows[k][0], "detail": rows[k][1]} for k, n, r in SOURCES]
    readers = []
    for c in p.get("contributions") or []:
        a = c.get("analyst")
        b = c.get("backend") or ""
        abstain = c.get("prob_up") is None
        empty = abstain or "재료 없음" in str(c.get("summary") or "") or (b.startswith("heuristic") and "휴리스틱" in str(c.get("summary") or ""))
        readers.append({"analyst": a, "label": LABELS.get(a, a), "backend": b, "llm": _llm(b), "abstain": abstain,
                        "kind": ("쉼 — " + str(c.get("summary") or "의견 없음")[:60]) if abstain else "AI 모델(LLM)" if _llm(b)
                        else "학습 모델" if b.startswith("sklearn") else "검증된 가격 신호" if b == "signals2" else "규칙",
                        "prob_up": c.get("prob_up"), "weight": c.get("weight"), "empty": bool(empty),
                        "summary": str(c.get("summary") or "")[:120]})
    used = [s["name"] for s in sources if s["used"] and s["key"] != "events"]
    missing = [s["name"] for s in sources if not s["used"]]
    core_used = [s for s in sources if s["key"] in CORE and s["used"]]
    any_llm = any(r["llm"] for r in readers)
    if not core_used:
        head = "차트 위주 판단이에요 — 뉴스·공시·수급·커뮤니티·경제지표가 없어 보지 못했어요"
        level = "warn"
    elif len(core_used) < 3:
        head = f"일부 자료로 판단했어요 — {', '.join(s['name'] for s in core_used)} 는 봤고 {', '.join(m for m in missing if m != '업종 흐름') or '나머지'} 는 없었어요"
        level = "info"
    else:
        head = f"여러 자료를 함께 봤어요 — {', '.join(used)}"
        level = "good"
    if not any_llm and readers:
        head += " · AI 키가 없어 뉴스·경제 해석은 규칙으로 했어요"
    return {"sources": sources, "readers": readers, "used": used, "missing": missing, "n_used": len(core_used), "n_core": len(CORE),
            "llm": any_llm, "chart_only": not core_used, "headline": head, "level": level}


def coverage(app, now: datetime | None = None) -> dict:
    """지금 서버가 모으는 자료 (전체). 종목에 뉴스가 없었던 건지, 수집 자체가 꺼진 건지 구분해 준다."""
    from .data.db import session_scope
    from .data.models import Disclosure, MacroObservation, NewsArticle
    now = now or datetime.now(UTC)

    def age_txt(at) -> tuple[str | None, float | None]:
        if at is None:
            return None, None
        a = at if getattr(at, "tzinfo", None) else (datetime(at.year, at.month, at.day, tzinfo=UTC) if not isinstance(at, datetime) else at.replace(tzinfo=UTC))
        h = (now - a).total_seconds() / 3600
        return a.isoformat(), h

    with session_scope(app.engine) as s:
        n_last = s.scalar(select(func.max(NewsArticle.published_at)))
        n_cnt = s.scalar(select(func.count()).select_from(NewsArticle).where(NewsArticle.published_at >= now - timedelta(days=3))) or 0
        d_last = s.scalar(select(func.max(Disclosure.filed_at)))
        d_cnt = s.scalar(select(func.count()).select_from(Disclosure).where(Disclosure.filed_at >= (now - timedelta(days=7)).date())) or 0
        m_last = s.scalar(select(func.max(MacroObservation.ts)))
        m_cnt = s.scalar(select(func.count(func.distinct(MacroObservation.series_id)))) or 0
    rows = []

    def add(key, name, at, n, unit, ok_h, off_why):
        iso, h = age_txt(at)
        st = "off" if iso is None else "ok" if h is not None and h <= ok_h else "stale"
        rows.append({"key": key, "name": name, "at": iso, "hours": None if h is None else round(h, 1), "n": n, "unit": unit,
                     "status": st, "why": off_why if st != "ok" else None})

    add("news", "뉴스", n_last, n_cnt, "최근 3일 기사", 24, "뉴스 수집이 안 돌고 있어요 — 24시간 운영(./run.sh)과 인터넷 연결이 필요해요")
    add("disclosure", "공시", d_last, d_cnt, "최근 7일 공시", 24 * 4, "공시 수집이 안 돌고 있어요 — DART 키와 24시간 운영이 필요해요")
    add("macro", "경제지표", m_last, m_cnt, "지표 종류", 24 * 7, "경제지표 수집이 안 돌고 있어요 — FRED 키(무료)와 인터넷 연결이 필요해요")
    from .data.models import SystemState
    with session_scope(app.engine) as s:
        def scan(prefix):
            ats = [str((r.value or {}).get("at") or "") for r in s.query(SystemState).filter(SystemState.key.startswith(prefix))]
            ats = [a for a in ats if a]
            return len(ats), (max(ats) if ats else None)
        comm, c_last = scan("community:")
        flow, f_last = scan("flow:")
    add("community", "커뮤니티", datetime.fromisoformat(c_last) if c_last else None, comm, "종목", 12,
        "커뮤니티 수집이 안 돌고 있어요 — 24시간 운영과 인터넷 연결이 필요해요")
    add("flow", "외국인·기관 수급", datetime.fromisoformat(f_last) if f_last else None, flow, "종목", 24 * 4,
        "수급 수집이 안 돌고 있어요 — 24시간 운영과 인터넷 연결이 필요해요")
    bars, _ = app._all_bars()
    last_bar = max((b.index[-1] for b in (bars or {}).values() if b is not None and len(b)), default=None)
    lag = None
    if last_bar is not None:
        from .clock import KRX
        try:
            lag = int(KRX.trading_days_between(last_bar.date(), now.date()))
        except Exception:  # noqa: BLE001
            lag = None
    chart = {"key": "chart", "name": "차트·거래량 (일봉)", "at": None if last_bar is None else str(last_bar.date()), "n": len(bars or {}),
             "unit": "종목", "lag": lag, "status": "off" if last_bar is None else "ok" if (lag or 0) <= 1 else "stale",
             "why": None if (lag or 0) <= 1 else f"일봉이 {lag}거래일 밀렸어요 — 시세를 새로 받아야 AI 가 오늘 판단을 할 수 있어요"}
    try:
        from .analysts.analysts import assign_roles
        roles = assign_roles(app.settings)
    except Exception:  # noqa: BLE001
        roles = {}
    llm = {"key": "llm", "name": "AI 모델 (뉴스·경제 해석)", "status": "ok" if roles else "off", "roles": roles,
           "why": None if roles else "AI 키가 없어 뉴스·경제 해석을 규칙으로 해요 — 무료 Gemini/Groq 키 하나면 LLM 이 읽어요 (설정 → 키)"}
    rows = [chart, *rows, llm]
    live = [r["name"] for r in rows if r["status"] == "ok"]
    off = [r["name"] for r in rows if r["status"] != "ok"]
    if chart["status"] != "ok" or len(live) <= 1:
        head, level = "지금 AI 는 거의 차트(가격)만 보고 있어요", "warn"
    elif len(off) <= 1:
        head, level = "AI 가 뉴스·공시·수급·경제 자료를 함께 보고 있어요", "good"
    else:
        head, level = f"AI 가 보는 자료 {len(live)}/{len(rows)} — {', '.join(off)} 은(는) 지금 안 들어와요", "info"
    return {"rows": rows, "live": live, "off": off, "headline": head, "level": level, "now": now.isoformat()}


__all__ = ["for_record", "coverage", "SOURCES"]

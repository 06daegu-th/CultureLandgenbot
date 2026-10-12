"""v40 투자위원회 — 한 종목을 두고 '왜 사야 하나 / 왜 사면 안 되나 / 무엇이 막나 / 그래서 결론은'을 한 화면에.

구조는 TradingAgents(분석가 → 강세·약세 토론 → 리스크 팀 → 포트폴리오 매니저)를 참고했지만, 코드는 가져오지 않고 이 시스템의
봉인된 판단 기록(ConsensusRecord payload)만 다시 읽어 만든다.
  - 분석가: 그 판단에 실제로 참여한 AI·모델·규칙 (무엇을 썼나 · 모델 · 재료가 비었나 · 과거 적중률)
  - 강세 / 약세: 같은 기록의 근거를 두 편으로 나눈다 (새로 지어내지 않는다)
  - 리스크 팀: NO TRADE 사유 · 실적 발표 예상 변동 · 데이터 신뢰도
  - 최종(PM): 기존 FINAL 그대로. 위원회는 주문 권한이 없다 — 화면에 보여 줄 뿐 주문 경로를 바꾸지 않는다.
시점 고정: 판단 기록이 만들어질 때의 입력만 쓴다 (기록 id · 봉인 여부 · 판단 시각을 함께 보여 준다).
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime

LEAN = 0.03  # 상승 확률이 50%에서 이만큼 넘게 벗어나야 한쪽 편으로 센다


def _track(app, analysts: list[str]) -> dict:
    """분석가별 과거 방향 적중률 (채점된 것만)."""
    if not analysts:
        return {}
    from sqlalchemy import Integer, cast, func, select

    from .data.db import session_scope
    from .data.models import AnalystOpinionRecord
    out = {}
    try:
        with session_scope(app.engine) as s:
            rows = s.execute(select(AnalystOpinionRecord.analyst, func.count(), func.sum(cast(AnalystOpinionRecord.correct, Integer)))
                             .where(AnalystOpinionRecord.analyst.in_(analysts), AnalystOpinionRecord.correct.is_not(None),
                                    AnalystOpinionRecord.category == "direction")
                             .group_by(AnalystOpinionRecord.analyst)).all()
        for an, n, hit in rows:
            out[an] = {"n": int(n or 0), "hit": round((hit or 0) / n, 3) if n else None}
    except Exception:  # noqa: BLE001 - 성적은 보조 정보
        return {}
    return out


def data_confidence(inputs: dict, blocks: list[str]) -> dict:
    """판단에 쓴 재료가 얼마나 채워졌고 신선했나 → 높음 / 보통 / 낮음."""
    srcs = inputs.get("sources") or []
    core = [s for s in srcs if s["key"] in ("chart", "news", "disclosure", "macro", "market")]
    used = sum(bool(s["used"]) for s in core)
    readers = [r for r in inputs.get("readers") or [] if not r.get("abstain")]
    empty = sum(bool(r.get("empty")) for r in readers)
    score = (used / len(core) if core else 0.0) - 0.15 * min(empty, 3)
    stale = [b for b in blocks if any(w in b for w in ("오래됨", "지연", "새 일봉", "실시간 가격 없음", "데이터 부족"))]
    if stale:
        score -= 0.35
    level = "high" if score >= 0.75 else "medium" if score >= 0.45 else "low"
    why = []
    missing = [s["name"] for s in core if not s["used"]]
    if missing:
        why.append("빠진 재료: " + ", ".join(missing))
    if empty:
        why.append(f"재료 없이 낸 의견 {empty}개는 근거에서 뺐어요")
    why += stale[:2]
    return {"level": level, "label": {"high": "높음", "medium": "보통", "low": "낮음"}[level], "score": round(max(score, 0.0), 2),
            "used": used, "of": len(core), "why": why}


def committee(app, symbol: str, now: datetime | None = None) -> dict:
    from .explain import verdict
    now = now or datetime.now(UTC)
    v = verdict(app, symbol, now)
    if not v.get("final"):
        return {"symbol": symbol, "ready": False, "why": v.get("why_none"), "can_analyze": v.get("can_analyze", False)}
    inputs = v.get("inputs") or {}
    readers = inputs.get("readers") or []
    track = _track(app, [r["analyst"] for r in readers])
    analysts, bull, bear = [], [], []
    for r in readers:
        p = r.get("prob_up")
        side = None if p is None or r.get("empty") else "bull" if p >= 0.5 + LEAN else "bear" if p <= 0.5 - LEAN else "neutral"
        t = track.get(r["analyst"]) or {}
        analysts.append({"analyst": r["analyst"], "label": r["label"], "kind": r.get("kind"), "model": r.get("backend") or None,
                         "llm": bool(r.get("llm")), "prob_up": p, "weight": r.get("weight"), "side": side, "empty": bool(r.get("empty")),
                         "abstain": bool(r.get("abstain")), "hit": t.get("hit"), "n_scored": t.get("n"),
                         "summary": (r.get("summary") or "")[:120] or None})
    votes = {x["label"]: x for x in v.get("votes") or []}
    for a in analysts:
        s = a["summary"] or (votes.get(a["label"]) or {}).get("summary")
        if not s or a["side"] not in ("bull", "bear"):
            continue
        (bull if a["side"] == "bull" else bear).append(f"{a['label']}: {s}")
    bull = list(dict.fromkeys(list(v.get("why_buy") or []) + bull))[:5]
    blocks = list(v.get("no_trade") or [])
    bear = list(dict.fromkeys([w for w in v.get("why_not") or [] if w not in blocks] + bear))[:5]
    risk = {"blocks": blocks, "warn": v.get("warn"), "earnings": None}
    try:
        from .prep import earnings_prep
        ep = earnings_prep(app, symbol, now=now, days=14)
        if ep.get("next") and ep.get("expected_move"):
            risk["earnings"] = f"실적 발표 {ep['next']['d_label']} · 예상 변동 ±{ep['expected_move']:.1%} ({ep['move_source']})"
    except Exception:  # noqa: BLE001, S110
        pass
    conf = data_confidence(inputs, blocks)
    probs = [a["prob_up"] for a in analysts if a["prob_up"] is not None and not a["empty"]]
    spread = statistics.pstdev(probs) if len(probs) >= 2 else 0.0
    n_bull = sum(a["side"] == "bull" for a in analysts)
    n_bear = sum(a["side"] == "bear" for a in analysts)
    split = n_bull and n_bear and spread >= 0.06
    final = v["final"]
    pm_lines = [f"최종 {v['label']}" + (f" · {v['horizon']}거래일 뒤 오를 확률 {v['prob_up']:.0%}" if v.get("prob_up") is not None else "")]
    if v.get("changed_by_gate"):
        pm_lines.append(f"원래 의견은 {v['raw']} 였지만 막는 이유가 있어 바꿨어요")
    if split:
        pm_lines.append(f"의견이 갈려요 — 오를 쪽 {n_bull} · 내릴 쪽 {n_bear} (확률 차이 ±{spread:.0%})")
    caution = None
    if final == "BUY" and conf["level"] == "low":
        caution = "데이터 신뢰도가 낮아 사더라도 보류를 권해요 (위원회 권고 · 주문 경로는 기존 안전장치가 결정)"
    elif final == "BUY" and split:
        caution = "의견이 갈린 매수 — 비중을 줄이는 쪽이 안전해요 (권고)"
    trust = v.get("trust") or {}
    if trust.get("key") == "banned":
        caution = (caution + " · " if caution else "") + "AI 성적이 기준 미달이라 참고만 (섀도 모드)"
    llm_used = [a for a in analysts if a["llm"]]
    return {"symbol": symbol, "ready": True, "final": final, "label": v["label"], "prob_up": v.get("prob_up"), "horizon": v.get("horizon"),
            "analysts": analysts, "bull": bull, "bear": bear, "risk": risk, "confidence": conf,
            "pm": {"lines": pm_lines, "caution": caution, "split": bool(split), "n_bull": n_bull, "n_bear": n_bear, "spread": round(spread, 3)},
            "transparency": {"record_id": v.get("id"), "sealed": bool((v.get("used") or {}).get("sealed")), "judged_at": v.get("judged_at"),
                             "basis": v.get("basis"), "models": sorted({a["model"] for a in llm_used if a["model"]}),
                             "n_llm": len(llm_used), "n_rules": len(analysts) - len(llm_used),
                             "sources": [{"name": s["name"], "used": s["used"], "detail": s["detail"]} for s in inputs.get("sources") or []]},
            "trust": trust,
            "note": "위원회는 같은 판단 기록을 강세·약세·리스크로 나눠 보여 줄 뿐 주문 권한이 없어요. 최종 = 기존 FINAL (안전장치 통과 후)."}


def text(c: dict) -> list[str]:
    """채팅용 짧은 요약."""
    if not c.get("ready"):
        return [c.get("why") or "판단 기록이 없어요"]
    out = list(c["pm"]["lines"])
    if c["bull"]:
        out.append("오를 이유: " + " / ".join(c["bull"][:3]))
    if c["bear"]:
        out.append("내릴 이유: " + " / ".join(c["bear"][:3]))
    if c["risk"]["blocks"]:
        out.append("막는 것: " + " / ".join(c["risk"]["blocks"][:3]))
    if c["risk"].get("earnings"):
        out.append(c["risk"]["earnings"])
    out.append(f"데이터 신뢰도 {c['confidence']['label']} ({c['confidence']['used']}/{c['confidence']['of']} 재료)")
    if c["pm"].get("caution"):
        out.append(c["pm"]["caution"])
    return out


__all__ = ["committee", "data_confidence", "text"]

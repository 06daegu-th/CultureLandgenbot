"""AI · 모델 Health — AI 별 성적 · 성능 저하 · 응답 상태, 모델(챔피언·드리프트·보정·노후)을 한 화면에.

전부 저장된 기록에서 계산한다 (호출 없음). 성능 저하는 review/decay 가 12시간마다 감지해 알림까지 보낸다 (desk.model_decay).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from . import ops
from .asof import label

DEC = {"decaying": "bad", "watch": "warn", "stable": "ok", "insufficient": "na"}


def ai_health(app, now: datetime | None = None) -> dict:
    from . import readiness
    from .data.db import session_scope
    from .data.models import AnalystOpinionRecord, LLMCall
    from .explain import LABELS
    now = now or datetime.now(UTC)
    decay = ops.get_state(app.engine, "model_decay")
    dec_ai = decay.get("analysts") or {}
    rows = []

    def aware(t):
        return None if t is None else t if t.tzinfo else t.replace(tzinfo=UTC)

    with session_scope(app.engine) as s:
        from .data.models import ConsensusRecord
        latest = aware(s.scalar(select(func.max(ConsensusRecord.as_of))))
        names = [a for (a,) in s.execute(select(AnalystOpinionRecord.analyst).distinct())]
        for an in names:
            base = select(AnalystOpinionRecord).where(AnalystOpinionRecord.analyst == an)
            last = aware(s.scalar(select(func.max(AnalystOpinionRecord.as_of)).where(AnalystOpinionRecord.analyst == an)))
            last_dir = aware(s.scalar(select(func.max(AnalystOpinionRecord.as_of)).where(AnalystOpinionRecord.analyst == an,
                                                                                     AnalystOpinionRecord.category == "direction")))
            from sqlalchemy import Integer, cast
            n_sc, n_hit = s.execute(select(func.count(), func.sum(cast(AnalystOpinionRecord.correct, Integer)))
                                    .where(AnalystOpinionRecord.analyst == an, AnalystOpinionRecord.correct.is_not(None),
                                           AnalystOpinionRecord.category == "direction")).one()
            n_all = s.scalar(select(func.count()).select_from(base.subquery())) or 0
            abst = s.scalar(select(func.count()).select_from(AnalystOpinionRecord).where(
                AnalystOpinionRecord.analyst == an, AnalystOpinionRecord.prob_up.is_(None),
                AnalystOpinionRecord.as_of >= now - timedelta(days=14))) or 0
            recent_n = s.scalar(select(func.count()).select_from(AnalystOpinionRecord).where(
                AnalystOpinionRecord.analyst == an, AnalystOpinionRecord.as_of >= now - timedelta(days=14))) or 0
            d = dec_ai.get(an) or {}
            rows.append({"analyst": an, "label": LABELS.get(an, an), "n": n_all, "n_scored": int(n_sc or 0),
                         "hit": round((n_hit or 0) / n_sc, 3) if n_sc else None,  # 전체 기간
                         # 초기 · 최근: 성능 저하 판정과 같은 숫자 (하루 여러 건이면 일별 적중률 · 최근 20거래일)
                         "hit_ref": d.get("reference"), "hit_recent": d.get("recent"), "days": d.get("days"),
                         "last": label(last),
                         # 다른 AI 는 계속 판단하는데 이 AI 만 멈췄는가 (Risk AI 는 경고를 낸 날만 기록되므로 제외)
                         "stale": bool(an not in ("risk", "challenger") and latest and last_dir and latest - last_dir > timedelta(days=3)),
                         "abstain_14d": round(abst / recent_n, 3) if recent_n else None,
                         "decay": d.get("status") or "insufficient", "decay_msg": d.get("message")})
        llm = s.execute(select(LLMCall.analyst, LLMCall.provider, LLMCall.status, func.count(), func.avg(LLMCall.latency_ms))
                        .where(LLMCall.ts >= now - timedelta(hours=24))
                        .group_by(LLMCall.analyst, LLMCall.provider, LLMCall.status)).all()
        last_err = s.execute(select(LLMCall.ts, LLMCall.provider, LLMCall.error).where(LLMCall.status == "error")
                             .order_by(LLMCall.ts.desc()).limit(5)).all()
    prov = {}
    for _an, pv, st, n, lat in llm:
        p = prov.setdefault(pv, {"provider": pv, "ok": 0, "error": 0, "cached": 0, "budget": 0, "latency_ms": None})
        p[st if st in p else "error"] += n
        if st == "ok" and lat:
            p["latency_ms"] = int(lat)
    for p in prov.values():
        tot = p["ok"] + p["error"]
        p["error_rate"] = round(p["error"] / tot, 3) if tot else None
    rows.sort(key=lambda r: ({"decaying": 0, "watch": 1}.get(r["decay"], 2), -(r["n_scored"] or 0)))
    rec, _ = app.active_model()
    checks = [readiness.check_model(app, "paper", False), readiness.check_drift(app), readiness.check_calibration(app)]
    ev = ops.get_state(app.engine, "evaluation")
    pw = ops.get_state(app.engine, "prediction_power")
    dr = ops.get_state(app.engine, "drift")
    problems = [f"{r['label']}: 성능 저하 — {r['decay_msg']}" for r in rows if r["decay"] == "decaying"]
    if latest and now - latest > timedelta(days=3):
        problems.append(f"AI 판단이 {(now - latest).days}일째 없음 (마지막 {label(latest)}) — 스케줄러·데이터 수집 확인")
    problems += [f"{r['label']}: 다른 AI 는 판단하는데 이 AI 만 멈춤 (마지막 {r['last']})" for r in rows if r["stale"]]
    problems += [f"{p['provider']}: 24시간 실패율 {p['error_rate']:.0%}" for p in prov.values() if (p["error_rate"] or 0) > 0.3]
    problems += [f"{c['key']}: {c['detail']}" for c in checks if c["status"] == "red"]
    for k in ("consensus", "quant"):
        x = decay.get(k) or {}
        if x.get("status") == "decaying":
            problems.append(f"{x.get('label')}: {x.get('message')}")
    status = "bad" if problems else "warn" if any(c["status"] == "yellow" for c in checks) or any(r["decay"] == "watch" for r in rows) else "ok"
    tiles = _tiles(app, rows, prov, decay, rec, latest, now)
    return {"at": now.isoformat(), "as_of": label(now), "status": status, "problems": problems[:10], "analysts": rows, "tiles": tiles,
            "providers": sorted(prov.values(), key=lambda p: -(p["ok"] + p["error"])),
            "llm_errors": [{"at": label(t), "provider": pv, "error": (e or "")[:160]} for t, pv, e in last_err],
            "model": {"champion": f"{rec.name}@{rec.version}" if rec else None, "trained": label(rec.created_at) if rec else None,
                      "checks": checks, "evaluation": {k: ev.get(k) for k in ("status", "verdict", "n", "hit_rate", "baseline")},
                      "power": {k: (pw.get("forward") or {}).get(k) for k in ("decision", "label", "n", "hit_rate", "more_needed")} | {"bottom_line": pw.get("bottom_line")},
                      "drift": {k: dr.get(k) for k in ("status", "message", "at")},
                      "decay": {k: {kk: (decay.get(k) or {}).get(kk) for kk in ("status", "message", "n", "reference", "recent", "half_life_days")}
                                for k in ("consensus", "quant")} | {"at": decay.get("at")}},
            "has_llm": app.settings.has_llm}


def _tiles(app, rows, prov, decay, rec, latest, now) -> list[dict]:
    """한눈에: Quant · 뉴스 AI · 거시 AI · 공시·실적 AI 정상 여부 + 최근 성능 저하 · 데이터 부족 · API 오류 · 자동 감시."""
    by = {r["analyst"]: r for r in rows}
    out = []

    def ai_tile(key, title):
        r = by.get(key)
        if r is None:
            return {"key": key, "title": title, "status": "na", "detail": "기록 없음 (이 AI 가 꺼져 있거나 아직 판단 전)"}
        if r["decay"] == "decaying":
            return {"key": key, "title": title, "status": "bad", "detail": "성능 저하 — " + (r["decay_msg"] or "")}
        if r["stale"]:
            return {"key": key, "title": title, "status": "bad", "detail": f"판단 멈춤 (마지막 {r['last']})"}
        if r["n_scored"] < 40:
            return {"key": key, "title": title, "status": "warn", "detail": f"채점 {r['n_scored']}건 — 판단하기엔 표본 부족"}
        return {"key": key, "title": title, "status": "warn" if r["decay"] == "watch" else "ok",
                "detail": f"적중 {r['hit']:.0%} · 최근 {r['hit_recent']:.0%}" if r.get("hit_recent") is not None and r.get("hit") is not None else "정상"}
    q = by.get("quant")
    if rec is None:
        out.append({"key": "quant", "title": "Quant 모델", "status": "warn", "detail": "챔피언 없음 — 학습 후보가 검증 게이트 탈락 (차트·Quant AI 기권)"})
    else:
        t = ai_tile("quant", "Quant 모델")
        t["detail"] = f"{rec.name}@{rec.version} · " + t["detail"] if q else f"{rec.name}@{rec.version}"
        out.append(t)
    out += [ai_tile("primary", "뉴스 AI"), ai_tile("nvidia", "거시(경제·시장) AI"), ai_tile("panel", "공시·실적 AI")]
    dec = [r["label"] for r in rows if r["decay"] == "decaying"] + [(decay.get(k) or {}).get("label") for k in ("consensus", "quant")
                                                                   if (decay.get(k) or {}).get("status") == "decaying"]
    out.append({"key": "decay", "title": "최근 성능 저하", "status": "bad" if dec else "ok",
                "detail": ", ".join(x for x in dec if x) if dec else f"저하 신호 없음 · 점검 {label(decay.get('at')) or '기록 없음'}"})
    thin = [r["label"] for r in rows if r["analyst"] not in ("risk",) and r["n_scored"] < 40]
    stale_ai = latest is None or now - latest > timedelta(days=3)
    out.append({"key": "data", "title": "데이터 부족", "status": "bad" if stale_ai else "warn" if thin else "ok",
                "detail": ("AI 판단 자체가 3일 넘게 없음" if stale_ai else "") + (f" 채점 표본 부족: {', '.join(thin)}" if thin else "") or "충분"})
    err = [f"{p['provider']} 실패율 {p['error_rate']:.0%}" for p in prov.values() if (p["error_rate"] or 0) > 0.1]
    out.append({"key": "api", "title": "API 오류", "status": "bad" if any((p["error_rate"] or 0) > 0.3 for p in prov.values()) else "warn" if err else
                ("ok" if prov else "na"), "detail": ", ".join(err) or ("최근 24시간 LLM 오류 없음" if prov else "LLM 호출 없음 (키 없음 또는 휴장)")})
    sn = ops.get_state(app.engine, "sentinel")
    out.append({"key": "sentinel", "title": "자동 감시", "status": sn.get("status") or "na",
                "detail": "; ".join(f"{c['title']}: {c['detail'][:40]}" for c in sn.get("checks") or [] if c["status"] in ("bad", "warn"))[:200]
                or (f"모두 정상 · {sn.get('as_of')}" if sn else "아직 실행 전 (5분마다)")})
    return out


__all__ = ["ai_health"]

"""AI Lab — 여러 AI 가 '어떻게 합쳐져 최종 판단이 되는지'와 '어떤 모델이 어떤 단계에 있는지'를 한 화면에.

  구조   Quant · 뉴스 · 거시 · 공시/실적 · 국면 · Risk  →  Ensemble(가중 합의)  →  Risk Gate(거부권·NO TRADE)  →  최종
  모델   Champion(실제 판단에 쓰임) · Challenger(검증 게이트 통과, 섀도 대기) · Shadow(가상으로만 판단·채점) ·
         Research(사전 등록된 가설 · 묶음 A/B) · Retired/Rejected(탈락 — 숨기지 않는다)
숫자는 모두 저장된 기록에서만. 새 모델은 Research → Challenger → Shadow → Champion 순서로만 올라간다 (건너뛰기 없음).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from . import ops
from .asof import label

STAGE = {"champion": ("Champion", "실제 판단에 쓰이는 모델"), "shadow": ("Shadow", "백테스트 게이트 통과 — 가상으로만 판단·채점 중"),
         "candidate": ("Challenger", "학습된 후보 — 검증 게이트 대기"), "rejected": ("Rejected", "게이트 탈락"),
         "retired": ("Retired", "이전 챔피언")}
LAYERS = [("quant", "Quant (차트·팩터)"), ("primary", "뉴스"), ("nvidia", "거시·시장"), ("panel", "공시·실적"), ("regime", "국면"), ("risk", "Risk")]


def lab_status(app, now: datetime | None = None) -> dict:
    from .data.db import session_scope
    from .data.models import ConsensusRecord, ModelRecord
    from .health import ai_health
    now = now or datetime.now(UTC)
    h = ai_health(app, now)
    by = {r["analyst"]: r for r in h["analysts"]}
    layers = []
    for key, name in LAYERS:
        r = by.get(key)
        if key == "risk" and r is not None:  # Risk AI 는 방향이 아니라 거부권만 낸다 → 적중률 채점 대상 아님
            layers.append({"key": key, "name": name, "status": "bad" if r["stale"] else "ok", "n_scored": 0, "hit": None, "hit_recent": None,
                           "last": r["last"], "detail": f"거부권·위험 경고 전용 (방향 채점 없음) · 기록 {r['n']}건"})
            continue
        st = "na" if r is None else "bad" if r["decay"] == "decaying" or r["stale"] else "warn" if r["decay"] == "watch" or r["n_scored"] < 40 else "ok"
        layers.append({"key": key, "name": name, "status": st, "n_scored": r["n_scored"] if r else 0, "hit": r["hit"] if r else None,
                       "hit_recent": r.get("hit_recent") if r else None, "last": r["last"] if r else None,
                       "detail": "기록 없음 — 꺼져 있거나 판단 전" if r is None else (r.get("decay_msg") or f"채점 {r['n_scored']}건")})
    since = now - timedelta(days=30)
    with session_scope(app.engine) as s:
        acts = dict(s.execute(select(ConsensusRecord.action, func.count()).where(ConsensusRecord.as_of >= since)
                              .group_by(ConsensusRecord.action)).all())
        payloads = [p for (p,) in s.execute(select(ConsensusRecord.payload).where(ConsensusRecord.as_of >= since).limit(5000))]
        models = s.scalars(select(ModelRecord).order_by(ModelRecord.created_at.desc()).limit(40)).all()
        models = [{"id": m.id, "name": m.name, "version": m.version, "status": m.status, "stage": STAGE.get(m.status, (m.status, ""))[0],
                   "stage_desc": STAGE.get(m.status, ("", ""))[1], "created": label(m.created_at),
                   "metrics": {k: (m.metrics or {}).get(k) for k in ("auc", "sharpe", "hit_rate", "brier", "ic") if (m.metrics or {}).get(k) is not None},
                   "shadow": {k: (m.shadow_metrics or {}).get(k) for k in ("n", "hit_rate", "days") if (m.shadow_metrics or {}).get(k) is not None},
                   "notes": (m.notes or "")[:160]} for m in models]
    vetoes = sum(1 for p in payloads if (p or {}).get("vetoes"))
    nt_codes: dict[str, int] = {}
    for p in payloads:
        for c in (p or {}).get("no_trade_codes") or []:
            nt_codes[c] = nt_codes.get(c, 0) + 1
    total = sum(acts.values())
    dec = ops.get_state(app.engine, "model_decay").get("consensus") or {}
    ab = ops.get_state(app.engine, "batch_ab")
    research = []
    for r in (ab.get("rows") or [])[:6]:
        research.append({"kind": "묶음 A/B", "name": str(r.get("name") or r.get("variant") or r.get("key") or "-"),
                         "detail": " · ".join(f"{k} {v}" for k, v in r.items() if k in ("n", "hit_a", "hit_b", "winner", "verdict") and v is not None)})
    pre = ops.get_state(app.engine, "prereg")
    for hyp in (pre.get("hypotheses") or pre.get("rows") or [])[:6]:
        if isinstance(hyp, dict):
            research.append({"kind": "사전 등록 가설", "name": str(hyp.get("name") or hyp.get("id") or "-"),
                             "detail": str(hyp.get("status") or hyp.get("decision") or "진행 중")})
    champ = next((m for m in models if m["status"] == "champion"), None)
    return {"at": now.isoformat(), "as_of": label(now), "layers": layers,
            "ensemble": {"status": {"decaying": "bad", "watch": "warn", "stable": "ok"}.get(dec.get("status"), "na"),
                         "detail": dec.get("message") or "합의 성능 점검 기록 없음", "n_30d": total,
                         "actions_30d": {k: int(v) for k, v in acts.items()}},
            "risk_gate": {"vetoes_30d": vetoes, "no_trade_30d": int(acts.get("NO_TRADE", 0)), "codes": dict(sorted(nt_codes.items(), key=lambda x: -x[1])[:8]),
                          "detail": f"30일간 합의 {total}건 중 NO TRADE {acts.get('NO_TRADE', 0)}건 · 거부권 {vetoes}건"},
            "final": {"champion": f"{champ['name']}@{champ['version']}" if champ else None,
                      "detail": "Quant 챔피언 없음 — Quant AI 기권, 다른 AI 로만 합의" if not champ else f"챔피언 {champ['name']}@{champ['version']} ({champ['created']})"},
            "models": models, "research": research,
            "pipeline": ["Research (가설 사전 등록)", "Challenger (학습·백테스트 게이트)", "Shadow (가상 판단·채점)", "Champion (실제 판단)"],
            "note": "새 모델은 단계를 건너뛰지 않는다 · 탈락한 모델도 기록에 남긴다 · 챔피언 성능이 무너지면 자동 롤백 (Guardian)"}


__all__ = ["lab_status", "STAGE", "LAYERS"]

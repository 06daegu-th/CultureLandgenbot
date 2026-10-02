"""AI 성적 추적 — 숨기지 않고 매일 기록 · 나쁘면 자동으로 SHADOW(채점만) 강등.

지금 실데이터 결과(AI Alpha −1.2%p/건 · Brier Skill 음수 · ECE 10%+)는 'AI 를 믿고 매매할 단계가 아님'이다.
이 숫자를 한 번 보여주고 끝내지 않고 매일 같은 방법(최근 1,000건 전부)으로 다시 재서 추이로 남긴다.

snapshot()  하루 한 번: 정확도 · 기준(오른 비율) · Brier Skill · ECE · AI Alpha · 보정률 · MDD → ai_metrics_hist
trust()     신뢰 단계: UNTRUSTED(하나라도 나쁨) / WATCH(경계선) / CANDIDATE(모두 양호 · 표본 300+) — 이유 목록
demote()    UNTRUSTED 가 3번 연속이면 AI 를 주문에서 뺀다(SHADOW: 판단·채점은 계속) + 알림.
            CANDIDATE 가 20번 연속이면 강등을 풀어 준다 (그래도 실제 사용은 검증 사다리를 다시 통과해야 함).
            QUANT_AI_AUTO_DEMOTE=false 면 기록·경고만.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from . import ops
from .asof import label

HIST = "ai_metrics_hist"
DEMOTION = "ai_demotion"
ECE_MAX = 0.10
DEMOTE_AFTER = 3
RESTORE_AFTER = 20


def trust(m: dict) -> dict:
    if not m or not m.get("n"):
        return {"level": "NO_DATA", "reasons": ["채점된 판단 없음"], "label": "⚪ 판단 전"}
    bad, warn = [], []
    if (m.get("brier_skill") or 0) < 0:
        bad.append(f"Brier Skill {m['brier_skill']:+.3f} — 확률이 '늘 평균을 말하기'보다 못함")
    if (m.get("alpha") or 0) < 0:
        bad.append(f"AI Alpha {m['alpha'] * 100:+.2f}%p/건 — AI 가 고른 판단이 전체 평균보다 나쁨")
    if (m.get("ece") or 0) > ECE_MAX:
        bad.append(f"ECE {m['ece']:.1%} — 확률 70% 라고 해도 실제는 크게 다름 (기준 {ECE_MAX:.0%} 이하)")
    if m.get("accuracy") is not None and m.get("base") is not None and m["accuracy"] < m["base"]:
        bad.append(f"방향 정확도 {m['accuracy']:.1%} < 늘 '오른다'고 할 때 {m['base']:.1%}")
    if m["n"] < 300:
        warn.append(f"표본 {m['n']}건 — 300건 미만은 판단 보류")
    if 0 <= (m.get("brier_skill") or 0) < 0.01 and not bad:
        warn.append("Brier Skill 이 0 에 가까움 — 우연과 구분 어려움")
    level = "UNTRUSTED" if bad else "WATCH" if warn else "CANDIDATE"
    return {"level": level, "reasons": bad + warn,
            "label": {"UNTRUSTED": "🔴 AI 를 믿고 매매할 단계 아님", "WATCH": "🟡 관찰 — 아직 근거 부족", "CANDIDATE": "🟢 검증 후보 (사다리 통과 필요)"}[level]}


def snapshot(app, now: datetime | None = None, n: int = 1000) -> dict:
    from .scorecard import public_report
    now = now or datetime.now(UTC)
    r = public_report(app, n=n)
    m = {"d": now.date().isoformat(), "at": now.isoformat(), "n": r.get("n") or 0, "accuracy": r.get("direction_accuracy"),
         "base": r.get("base_up"), "brier_skill": r.get("brier_skill"), "ece": r.get("ece"), "alpha": r.get("net_alpha_per_pred"),
         "calibration_pct": r.get("calibration_pct"), "mdd": r.get("mdd"), "from": r.get("from"), "to": r.get("to")}
    m["trust"] = trust(m)["level"]
    hist = ops.get_state(app.engine, HIST).get("rows") or []
    hist = [h for h in hist if h["d"] != m["d"]] + [m]
    ops.set_state(app.engine, HIST, {"rows": hist[-400:]})
    demote(app, hist, now)
    return m


def _streak(hist: list[dict], level: str) -> int:
    k = 0
    for h in reversed(hist):
        if h.get("trust") != level:
            break
        k += 1
    return k


def demote(app, hist: list[dict] | None = None, now: datetime | None = None) -> dict:
    from .alerts import push
    now = now or datetime.now(UTC)
    hist = hist if hist is not None else (ops.get_state(app.engine, HIST).get("rows") or [])
    st = ops.get_state(app.engine, DEMOTION)
    auto = os.environ.get("QUANT_AI_AUTO_DEMOTE", "true").lower() != "false"
    bad, good = _streak(hist, "UNTRUSTED"), _streak(hist, "CANDIDATE")
    if not st.get("on") and bad >= DEMOTE_AFTER:
        last = trust(hist[-1])
        if auto:
            st = {"on": True, "since": now.isoformat(), "reason": " · ".join(last["reasons"][:2]), "streak": bad}
            ops.set_state(app.engine, DEMOTION, st)
        push(app.engine, "guardian", "AI 자동 강등 → SHADOW" if auto else "AI 성적 경고 (자동 강등 꺼짐)",
             f"{bad}일 연속 'AI 를 믿을 수 없음': {last['reasons'][0]} — 주문에는 코어 전략만 사용, AI 는 채점만 계속",
             level="warn", link="#scorecard", dedupe=f"ai_demote:{now.date()}")
    elif st.get("on") and good >= RESTORE_AFTER:
        st = {"on": False, "lifted": now.isoformat(), "reason": f"{good}일 연속 검증 후보"}
        ops.set_state(app.engine, DEMOTION, st)
        push(app.engine, "guardian", "AI 강등 해제", f"{good}일 연속 양호 — 실제 사용은 검증 사다리 조건을 다시 통과해야 함",
             level="info", link="#scorecard", dedupe=f"ai_restore:{now.date()}")
    return st


def demoted(engine) -> bool:
    return bool(ops.get_state(engine, DEMOTION).get("on"))


def report(app) -> dict:
    hist = ops.get_state(app.engine, HIST).get("rows") or []
    last = hist[-1] if hist else None
    t = trust(last) if last else trust({})
    first = hist[0] if hist else None
    dm = ops.get_state(app.engine, DEMOTION)
    return {"rows": hist[-120:], "last": last, "trust": t, "demotion": dm | ({"since_label": label(dm["since"])} if dm.get("since") else {}),
            "days": len(hist), "since": first["d"] if first else None,
            "change": None if not first or not last or first is last else {
                k: None if first.get(k) is None or last.get(k) is None else round(last[k] - first[k], 4)
                for k in ("accuracy", "brier_skill", "ece", "alpha")},
            "rule": f"UNTRUSTED {DEMOTE_AFTER}일 연속 → 주문에서 AI 제외(SHADOW) · CANDIDATE {RESTORE_AFTER}일 연속 → 해제 · ECE 기준 {ECE_MAX:.0%}"}


__all__ = ["snapshot", "trust", "demote", "demoted", "report", "HIST", "DEMOTION"]

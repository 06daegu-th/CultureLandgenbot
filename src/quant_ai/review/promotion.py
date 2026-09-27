"""AI 를 켜도 되나? — 코어 전용 운용 중 AI 섀도 결과로 판정.

가상 장부 3개(attr-core: AI 없음 / attr-veto: 코어+AI 거부권 / attr-full: 코어+거부권+위성)를 같은 가격으로
굴린 결과와 AI 합의 확률의 보정 품질(Brier Skill)을 함께 본다. 기준은 미리 정해 두고 바꾸지 않는다:

  1) 증거 양: 섀도 60거래일 이상 · 채점된 합의 200건 이상 — 모자라면 '증거 쌓는 중'
  2) 확률에 정보가 있나: 합의 Brier Skill > 0 (기준율보다 나은 예측)
  3) 돈으로도 나았나: 가상 장부 초과수익 > +1%p 이면서 최대낙폭이 코어보다 2%p 넘게 나쁘지 않음

셋 다 통과한 가장 단순한 단계(거부권 → 위성)만 추천한다. 추천은 알림일 뿐 설정은 사람이 바꾼다.
"""

from __future__ import annotations

import pandas as pd

MIN_DAYS = 60
MIN_SCORED = 200
MIN_EXCESS = 0.01
MAX_DD_WORSE = 0.02

STEPS = (("attr-veto", "AI 거부권만 켜기", "QUANT_CORE_ONLY=false · QUANT_AI_OVERLAY=veto"),
         ("attr-full", "AI 거부권 + 위성 켜기", "QUANT_CORE_ONLY=false"))


def _stats(eq: pd.Series) -> dict:
    eq = eq.dropna()
    return {"return": float(eq.iloc[-1] / eq.iloc[0] - 1),
            "max_drawdown": float((eq / eq.cummax() - 1).min()), "days": int(len(eq))}


def ai_verdict(books: dict[str, pd.Series], consensus: dict | None, shadow: dict | None = None) -> dict:
    """books: 장부 → 일별 평가금액. consensus: calibration.metrics 결과 (n · brier_skill)."""
    consensus = consensus or {}
    core = books.get("attr-core")
    days = int(len(core.dropna())) if core is not None and len(core) else 0
    n = int(consensus.get("n") or 0)
    bss = consensus.get("brier_skill")
    out = {"days": days, "min_days": MIN_DAYS, "scored": n, "min_scored": MIN_SCORED, "brier_skill": bss,
           "shadow": shadow or {}, "books": {}, "steps": []}
    if core is None or days < 2:
        return out | {"status": "no_data", "title": "AI 섀도 기록 없음",
                      "message": "LLM 키를 넣고 사이클이 돌면 하루 한 번 AI 가 판단·채점되고 가상 장부가 쌓입니다."}
    base = _stats(core)
    out["books"]["attr-core"] = base
    for book, label, how in STEPS:
        if book not in books or len(books[book].dropna()) < 2:
            continue
        st = _stats(books[book])
        st["excess"] = st["return"] - base["return"]
        st["dd_diff"] = st["max_drawdown"] - base["max_drawdown"]
        st["passes"] = st["excess"] > MIN_EXCESS and st["dd_diff"] > -MAX_DD_WORSE
        out["books"][book] = st
        out["steps"].append({"book": book, "label": label, "how": how, **st})
    progress = min(days / MIN_DAYS, n / MIN_SCORED, 1.0)
    out["progress"] = round(progress, 3)
    if days < MIN_DAYS or n < MIN_SCORED:
        return out | {"status": "collecting", "title": f"증거 쌓는 중 ({progress:.0%})",
                      "message": f"섀도 {days}/{MIN_DAYS}거래일 · 채점 {n}/{MIN_SCORED}건. "
                                 "기준을 채울 때까지 코어 전용을 유지하세요."}
    if bss is None or bss <= 0:
        return out | {"status": "keep_core", "title": "코어 전용 유지",
                      "message": f"AI 합의 확률이 기준율보다 낫지 않습니다 (Brier Skill {bss if bss is not None else '-'}). "
                                 "AI 는 계속 섀도로 채점만 합니다."}
    passed = [s for s in out["steps"] if s["passes"]]
    if not passed:
        return out | {"status": "keep_core", "title": "코어 전용 유지",
                      "message": "확률에는 정보가 있지만 가상 장부 수익이 코어보다 충분히 낫지 않습니다 "
                                 f"(기준: 초과수익 +{MIN_EXCESS:.0%}p, 낙폭 악화 {MAX_DD_WORSE:.0%}p 이내)."}
    best = passed[0]
    return out | {"status": "promote", "title": f"추천: {best['label']}",
                  "message": f"섀도 {days}거래일 동안 {best['label']} 장부가 코어보다 {best['excess']:+.1%}p, "
                             f"낙폭 차이 {best['dd_diff']:+.1%}p. 설정: {best['how']}", "recommend": best["book"]}


__all__ = ["ai_verdict", "MIN_DAYS", "MIN_SCORED"]

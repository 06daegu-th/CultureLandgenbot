"""검증 사다리 — AI 는 단계를 하나씩 통과해야 실제 돈에 닿는다. 성능이 떨어지면 자동으로 내려온다.

  1 과거 검증 (Backtest)  퀀트 모델을 과거 데이터로 walk-forward 검증. LLM 은 학습 데이터에 미래가 섞여 있어
                          과거로 공정하게 시험할 수 없으므로 2단계부터 시작한다.
  2 Shadow               실제 시장에서 예측만 하고 채점 (돈 없음).
  3 Paper                AI 신호로 가상 주문까지 (가상 장부가 AI 를 쓴다).
  4 소액 Live            실제 돈으로 AI 사용 — 계좌 운용 상한 20만원 (QUANT_LIVE_SMALL_CAPITAL).
  5 Live 확대            자동으로 올라가지 않는다. 조건을 채우면 '확대 가능' 만 알리고 금액은 사람이 정한다.

자동 승인 조건 (소액 Live, 모두 통과):
  ① 채점된 예측 300회 이상         ② 방향 적중률 > 55%        ③ 비용 차감 후 신호 수익 > 0
  ④ 확률 보정 통과 (BSS > 0, ECE ≤ 5%)                     ⑤ 최대 손실 제한 (신호 MDD > −15%, 가상 장부 낙폭 악화 2%p 이내)
  ⑥ 최근 30일에도 성능 유지 (Shadow 30일 이상)              ⑦ 국면이 바뀌어도 붕괴 없음 (국면 2개 이상, 각 적중률 ≥ 50%)
실제 돈은 여기에 더해 사람이 미리 동의해 둔 경우에만 (QUANT_LIVE_ENABLED + 확인 문구) 쓴다.

강등: Paper 이상에서 최근 50회 적중률 < 50%, 최근 30일 신호 수익 < 0, 보정 붕괴(BSS < −2%),
      실전 낙폭 −10% 초과, 또는 자동 킬스위치 HALTED → Shadow 로. 강등 뒤 14일은 재승격하지 않는다.
기준은 미리 정해 두고 결과를 보고 바꾸지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

STAGES = ["backtest", "shadow", "paper", "live_small", "live"]
STAGE_LABELS = {"backtest": "과거 검증", "shadow": "Shadow 예측", "paper": "Paper 가상 주문",
                "live_small": "소액 Live", "live": "Live 확대"}
STAGE_DESC = {
    "backtest": "퀀트 모델을 과거 데이터로 당시 시점처럼 검증",
    "shadow": "실제 시장에서 예측만 하고 결과로 채점 (돈 없음)",
    "paper": "AI 신호로 가상 주문까지 실제처럼",
    "live_small": "실제 돈으로 AI 사용 — 소액 상한",
    "live": "금액 확대는 사람이 결정",
}

MIN_SCORED = 300
MIN_HIT = 0.55
MAX_ECE = 0.05
MAX_SIGNAL_MDD = -0.15
MAX_DD_WORSE = 0.02
MIN_SHADOW_DAYS = 30
MIN_REGIME_N = 20
PAPER_MIN_SCORED = 100
PAPER_MIN_HIT = 0.53
PAPER_MIN_DAYS = 30
LIVE_MIN_DAYS = 60
COOLDOWN_DAYS = 14
DEMOTE_HIT = 0.50
DEMOTE_LIVE_DD = -0.10


def rank(stage: str | None) -> int:
    return STAGES.index(stage) if stage in STAGES else 0


def _cond(key: str, label: str, ok: bool | None, value: str, need: str, detail: str = "") -> dict:
    return {"key": key, "label": label, "status": "pass" if ok else "wait" if ok is None else "fail",
            "value": value, "need": need, "detail": detail}


def _pct(x, d=1):
    return "-" if x is None else f"{x * 100:.{d}f}%"


def conditions(ev: dict) -> list[dict]:
    """ev: collect() 결과 → 사용자가 말한 7개 조건 (+ 표본 부족이면 'wait')."""
    sc = ev.get("summary") or {}
    n = sc.get("n") or 0
    enough = n >= MIN_SCORED
    r30 = ev.get("last_30d") or {}
    regimes = [r for r in ev.get("by_regime") or [] if r["n"] >= MIN_REGIME_N and r["regime"] != "unknown"]
    paper = ev.get("paper") or {}
    net = sc.get("avg_signal_net")
    bss, ece = sc.get("brier_skill"), sc.get("ece")
    mdd = sc.get("mdd")
    dd_ok = mdd is not None and mdd > MAX_SIGNAL_MDD and (paper.get("dd_diff") is None or paper["dd_diff"] > -MAX_DD_WORSE)
    days = ev.get("days_tracked") or 0
    return [
        _cond("n", "① 예측 300회 이상", n >= MIN_SCORED, f"{n}회", f"≥ {MIN_SCORED}회"),
        _cond("hit", "② 방향 적중률 > 55%", (sc.get("hit_rate") or 0) > MIN_HIT if enough else None,
              _pct(sc.get("hit_rate")), f"> {MIN_HIT:.0%}"),
        _cond("net", "③ 비용 차감 후 수익 > 0", (net or 0) > 0 if enough and net is not None else None,
              _pct(net, 2), "> 0", f"신호 {sc.get('n_trades') or 0}건 평균, 왕복 비용 {_pct(sc.get('cost'), 2)} 차감"),
        _cond("calib", "④ 확률 보정 통과", (bss or 0) > 0 and ece is not None and ece <= MAX_ECE if enough else None,
              f"BSS {bss if bss is not None else '-'} · ECE {_pct(ece)}", f"BSS > 0 · ECE ≤ {MAX_ECE:.0%}"),
        _cond("dd", "⑤ 최대 손실 제한", dd_ok if enough else None,
              f"신호 MDD {_pct(mdd)}" + (f" · 장부 낙폭 차 {paper['dd_diff'] * 100:+.1f}%p" if paper.get("dd_diff") is not None else ""),
              f"MDD > {MAX_SIGNAL_MDD:.0%} · 낙폭 악화 {MAX_DD_WORSE:.0%}p 이내"),
        _cond("recent", "⑥ 최근 30일도 성능 유지",
              (days >= MIN_SHADOW_DAYS and (r30.get("hit_rate") or 0) > 0.52 and (r30.get("avg_signal_net") or 0) > 0)
              if days >= MIN_SHADOW_DAYS and (r30.get("n") or 0) >= 20 else None,
              f"{days}일 추적 · 30일 적중 {_pct(r30.get('hit_rate'))} · 수익 {_pct(r30.get('avg_signal_net'), 2)}",
              f"Shadow ≥ {MIN_SHADOW_DAYS}일 · 적중 > 52% · 수익 > 0"),
        _cond("regime", "⑦ 국면이 바뀌어도 유지",
              (len(regimes) >= 2 and all(r["hit_rate"] >= 0.5 for r in regimes)) if len(regimes) >= 2 else None,
              " · ".join(f"{r['regime']} {_pct(r['hit_rate'], 0)}" for r in regimes) or "국면 표본 부족",
              f"국면 2개 이상(각 {MIN_REGIME_N}회) · 각 ≥ 50%"),
    ]


def demotion_reasons(ev: dict, stage: str) -> list[str]:
    if rank(stage) < rank("paper"):
        return []
    out = []
    roll = ev.get("rolling50") or {}
    if (roll.get("n") or 0) >= 50 and (roll.get("hit_rate") or 1) < DEMOTE_HIT:
        out.append(f"최근 50회 적중률 {_pct(roll['hit_rate'])} < {DEMOTE_HIT:.0%}")
    r30 = ev.get("last_30d") or {}
    if (r30.get("n_trades") or 0) >= 10 and (r30.get("avg_signal_net") or 0) < 0:
        out.append(f"최근 30일 비용 차감 후 신호 수익 {_pct(r30['avg_signal_net'], 2)} < 0")
    bss = (ev.get("summary") or {}).get("brier_skill")
    if bss is not None and (ev.get("summary") or {}).get("n", 0) >= 100 and bss < -0.02:
        out.append(f"확률 보정 붕괴 (BSS {bss})")
    if ev.get("halted") and rank(stage) >= rank("live_small"):
        out.append("자동 킬스위치 HALTED")
    if ev.get("integrity_ok") is False and rank(stage) >= rank("live_small"):
        out.append("평가 무결성 실패 (예측 장부 또는 누수 감사)")
    live_dd = (ev.get("live") or {}).get("max_drawdown")
    if rank(stage) >= rank("live_small") and live_dd is not None and live_dd < DEMOTE_LIVE_DD:
        out.append(f"실전 낙폭 {_pct(live_dd)} < {DEMOTE_LIVE_DD:.0%}")
    return out


def decide(ev: dict, state: dict, now: datetime) -> dict:
    """현재 단계 + 증거 → 다음 단계 (한 번에 한 칸만 올라가고, 강등은 Shadow 로 바로)."""
    stage = state.get("stage") or "backtest"
    conds = conditions(ev)
    all_ok = all(c["status"] == "pass" for c in conds)
    cooldown = state.get("cooldown_until")
    cooling = bool(cooldown) and now < datetime.fromisoformat(cooldown)
    sc = ev.get("summary") or {}
    gates = {
        "backtest": {"ok": bool(ev.get("backtest_ok")), "why": ev.get("backtest_note") or ""},
        "shadow": {"ok": (sc.get("n") or 0) >= PAPER_MIN_SCORED and (sc.get("hit_rate") or 0) > PAPER_MIN_HIT
                   and (sc.get("brier_skill") or 0) > 0 and (ev.get("days_tracked") or 0) >= PAPER_MIN_DAYS,
                   "why": f"Paper 로 가려면: 예측 {PAPER_MIN_SCORED}회 · 적중 > {PAPER_MIN_HIT:.0%} · BSS > 0 · "
                          f"Shadow {PAPER_MIN_DAYS}일"},
        "paper": {"ok": all_ok and (ev.get("paper") or {}).get("days", 0) >= PAPER_MIN_DAYS
                  and ((ev.get("paper") or {}).get("excess") or 0) >= 0,
                  "why": f"소액 Live 로 가려면: 7개 조건 모두 + 가상 장부 {PAPER_MIN_DAYS}일 이상 · 코어 대비 손해 없음"},
        "live_small": {"ok": (ev.get("live") or {}).get("days", 0) >= LIVE_MIN_DAYS and all_ok
                       and ((ev.get("live") or {}).get("return") or 0) > 0,
                       "why": f"확대 가능 알림: 소액 Live {LIVE_MIN_DAYS}일 · 수익 > 0 · 조건 유지 (금액은 사람이 정함)"},
    }
    out = {"stage": stage, "changed": None, "conditions": conds, "all_pass": all_ok, "gates": gates,
           "cooling": cooling, "cooldown_until": cooldown}
    reasons = demotion_reasons(ev, stage)
    if reasons:
        return out | {"stage": "shadow", "changed": "demote", "reasons": reasons}
    if stage == "backtest":
        # 퀀트 모델이 과거 검증을 통과했거나, LLM 만으로 운용해도 Shadow 기록은 시작한다
        if gates["backtest"]["ok"] or (sc.get("n") or 0) > 0:
            return out | {"stage": "shadow", "changed": "promote",
                          "reasons": [gates["backtest"]["why"] or "예측 기록 시작 → Shadow 채점"]}
        return out
    if cooling:
        return out | {"reasons": [f"강등 뒤 재승격 대기 ({cooldown[:10]} 까지)"]}
    if ev.get("halted"):
        return out | {"reasons": ["자동 킬스위치 HALTED 중에는 승격하지 않음"]}
    if ev.get("integrity_ok") is False:
        return out | {"reasons": ["예측 장부·누수 감사가 통과해야 승격 (독립 평가 화면 확인)"]}
    if stage == "shadow" and gates["shadow"]["ok"]:
        return out | {"stage": "paper", "changed": "promote", "reasons": ["Shadow 조건 통과 → AI 가 가상 장부 주문에 참여"]}
    if stage == "paper" and gates["paper"]["ok"]:
        if not ev.get("live_consent"):
            return out | {"ready": "live_small",
                          "reasons": ["소액 Live 조건 통과 — 실제 돈 사용에 대한 사전 동의가 없어 대기 "
                                      "(.env: QUANT_LIVE_ENABLED=true · QUANT_LIVE_CONFIRM)"]}
        return out | {"stage": "live_small", "changed": "promote", "reasons": ["7개 조건 모두 통과 → 소액 Live"]}
    if stage == "live_small" and gates["live_small"]["ok"]:
        return out | {"ready": "live", "reasons": ["소액 Live 성과 유지 — 금액 확대를 검토할 수 있습니다 (자동 확대 안 함)"]}
    return out


def step(state: dict, ev: dict, now: datetime | None = None) -> tuple[dict, dict]:
    """상태 갱신: (새 상태, 판정). 이력은 최근 50개."""
    now = now or datetime.now(UTC)
    res = decide(ev, state, now)
    new = {**state, "stage": res["stage"], "last_eval": now.isoformat(),
           "ready": res.get("ready"), "reasons": res.get("reasons", [])}
    if not state.get("stage"):
        new["since"] = now.isoformat()
    if res["changed"]:
        new["since"] = now.isoformat()
        hist = list(state.get("history") or [])
        hist.append({"at": now.isoformat(), "from": state.get("stage") or "backtest", "to": res["stage"],
                     "kind": res["changed"], "reasons": res.get("reasons", [])})
        new["history"] = hist[-50:]
        if res["changed"] == "demote":
            new["cooldown_until"] = (now + timedelta(days=COOLDOWN_DAYS)).isoformat()
    return new, res


__all__ = ["STAGES", "STAGE_LABELS", "STAGE_DESC", "conditions", "decide", "step", "rank", "demotion_reasons"]

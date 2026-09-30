"""매매 계획 — 얼마나(사이징) · 어디서(진입 구간) · 언제 틀렸다고 인정하나(무효화 조건).

예측과 함께 저장되고 예측 장부 해시에 포함된다 → '나중에 결과를 보고 손절선을 고치지 않았다'는 증거.

사이징 (가장 작은 값을 쓴다)
  · 반켈리: f = ½ · 기대수익/분산 (기대수익 = (2p−1) · σ_h · √(2/π), 비용 차감) — 확률이 50% 근처면 0 에 가까움
  · 변동성 목표: 종목 하나가 포트폴리오 일간 변동성에 보태는 몫을 target 이하로 (σ 가 큰 종목은 작게)
  · 종목 한도 (RiskLimits.max_position_weight)
  × 이벤트 배수 (실적 D-1 → 0.5) × 준비 상태 배수 (CAUTION 0.75 · NOT READY 0)
진입 구간: 기준가 − 0.5 ATR ~ '비용 후 기대수익이 0 이 되는 가격' 중 낮은 쪽 (그 위는 추격 매수 금지)
무효화: 가격(2 ATR 아래 또는 10일 저점 아래) · 시간(보유 기간 끝) · 판단(재분석 확률 < 50%) · 이벤트(실적 결과 반대)
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

SQRT_2_PI = math.sqrt(2 / math.pi)


def atr(b: pd.DataFrame, n: int = 14) -> float | None:
    if len(b) < n + 1 or not {"high", "low", "close"} <= set(b.columns):
        return None
    pc = b["close"].shift()
    tr = pd.concat([b["high"] - b["low"], (b["high"] - pc).abs(), (b["low"] - pc).abs()], axis=1).max(axis=1)
    v = float(tr.iloc[-n:].mean())
    return v if np.isfinite(v) and v > 0 else None


def plan(symbol: str, bars: pd.DataFrame, prob_up: float, horizon: int = 5, cost_bps: float = 30.0,
         max_weight: float = 0.10, target_vol: float = 0.004, event_mult: float = 1.0, event_reason: str | None = None,
         readiness_mult: float = 1.0, earnings_in_horizon: bool = False) -> dict | None:
    """cost_bps: 왕복 비용(수수료+세금+슬리피지). target_vol: 종목 하나의 일간 변동성 기여 목표(0.4%p)."""
    if bars is None or len(bars) < 25:
        return None
    close = float(bars["close"].iloc[-1])
    lr = np.log(bars["close"]).diff().dropna()
    sig_d = float(lr.iloc[-20:].std())
    if not np.isfinite(sig_d) or sig_d <= 0:
        return None
    sig_h = sig_d * math.sqrt(horizon)
    edge = 2 * prob_up - 1
    mu_h = edge * sig_h * SQRT_2_PI  # 방향 확률 → 기대 수익 (대칭 가정)
    net = mu_h - cost_bps / 1e4
    kelly = 0.5 * net / (sig_h ** 2) if net > 0 else 0.0
    volw = target_vol / sig_d
    base = max(0.0, min(kelly, volw, max_weight))
    w = base * max(0.0, min(1.0, event_mult)) * max(0.0, min(1.0, readiness_mult))
    binding = "비용 후 기대수익 ≤ 0" if net <= 0 else min(
        (("반켈리", kelly), ("변동성 목표", volw), ("종목 한도", max_weight)), key=lambda x: x[1])[0]
    a = atr(bars) or sig_d * close
    low10 = float(bars["low"].iloc[-10:].min()) if "low" in bars else close * (1 - 2 * sig_d)
    ma20 = float(bars["close"].iloc[-20:].mean())
    chase = close * (1 + max(net, 0.0))  # 이 가격 위에서 사면 비용 후 기대수익이 0
    zone_lo = close - 0.5 * a
    zone_hi = min(close + 0.25 * a, chase) if net > 0 else close
    stop = max(close - 2 * a, low10 * 0.99)
    if stop >= close:
        stop = close - 1.5 * a
    stop_pct = stop / close - 1
    target = close * (1 + mu_h * 2) if mu_h > 0 else None
    rr = ((target - close) / (close - stop)) if target and close > stop else None
    inval = [{"kind": "price", "rule": f"종가 {stop:,.2f} 아래 ({stop_pct:+.1%})", "level": round(stop, 4)},
             {"kind": "time", "rule": f"{horizon}거래일 안에 기대 방향이 안 나오면 재평가", "bars": horizon},
             {"kind": "thesis", "rule": "재분석에서 P(상승) < 50% 또는 Risk AI 거부권"}]
    if earnings_in_horizon:
        inval.append({"kind": "event", "rule": "실적 발표가 예상과 반대(서프라이즈 부호 반대)면 무효"})
    return {
        "symbol": symbol, "ref_price": round(close, 4), "prob_up": round(prob_up, 4), "horizon": horizon,
        "expected_return": round(mu_h, 5), "net_expected": round(net, 5), "cost_bps": round(cost_bps, 1),
        "sizing": {"weight": round(w, 4), "half_kelly": round(kelly, 4), "vol_target": round(volw, 4), "cap": max_weight,
                   "event_mult": event_mult, "event_reason": event_reason, "readiness_mult": readiness_mult,
                   "binding": binding, "daily_vol": round(sig_d, 5)},
        "entry": {"low": round(zone_lo, 4), "high": round(zone_hi, 4), "no_chase_above": round(chase, 4) if net > 0 else None,
                  "support": {"ma20": round(ma20, 4), "low10": round(low10, 4)}, "atr": round(a, 4)},
        "invalidation": inval, "stop": round(stop, 4), "stop_pct": round(stop_pct, 4),
        "target": None if target is None else round(target, 4), "reward_risk": None if rr is None else round(rr, 2),
        "tradeable": w > 0,
    }


def check_invalidation(p: dict, bars_after: pd.DataFrame, new_prob: float | None = None) -> dict:
    """저장된 계획 대비 그 뒤 가격으로 무효화 여부 (복기 · 저널용)."""
    if not p or bars_after is None or not len(bars_after):
        return {"status": "open"}
    hit = bars_after[bars_after["close"] < p["stop"]]
    if len(hit):
        return {"status": "stopped", "at": str(hit.index[0].date()), "price": round(float(hit["close"].iloc[0]), 4)}
    if new_prob is not None and new_prob < 0.5:
        return {"status": "thesis_broken", "prob": new_prob}
    if len(bars_after) >= p.get("horizon", 5):
        return {"status": "expired"}
    return {"status": "open"}


__all__ = ["plan", "atr", "check_invalidation"]

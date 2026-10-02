"""투자 한도 — '원금'과 '최대로 감당할 손실'을 먼저 정하고, 시스템의 모든 한도를 거기서 계산한다.

입력 (화면 '내 투자 한도' 또는 ./run.sh budget):
  principal   이 시스템에 맡길 원금 (원)
  max_loss    전체 기간 최대로 감당할 손실 (원) — 여기에 닿으면 자동 매매 전체 정지(HALTED)
  first_stage 실전 첫 단계(소액 Live)에 쓸 비율 (기본 10%)

계산 (근거를 화면에 같이 보여준다):
  실전 운용 상한      QUANT_LIVE_MAX_CAPITAL      = 원금
  소액 Live 상한      QUANT_LIVE_SMALL_CAPITAL    = 원금 × first_stage
  일 손실 한도        max_daily_loss_pct          = 최대 손실률 ÷ 8  (나쁜 날 8번이면 끝 — 0.5%~3%)
  종목당 비중         max_position_weight         = min(10%, 최대 손실률 ÷ 50%)  (한 종목이 반토막 나도 한도 안)
  1회 주문 상한       max_order_value             = 원금 × 종목당 비중
  1일 VaR95 한도      max_var95                   = 최대 손실률 ÷ 5  (1%~4%)
  업종 비중           max_sector_weight           = min(40%, 종목 비중 × 4)
  원금 대비 손실      guardian 'total_loss'       = max_loss 의 80% 경고 · 100% HALTED
저장은 DB(ops) — 웹·스케줄러·감시 프로세스가 모두 같은 값을 쓴다. .env 에 같은 값을 넣고 싶으면 env_lines 를 복사.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from . import ops

KEY = "budget"


def plan(principal: float, max_loss: float, first_stage: float = 0.10) -> dict:
    principal, max_loss = float(principal), float(max_loss)
    if principal < 100_000:
        raise ValueError("원금은 10만원 이상")
    if not 0 < max_loss < principal:
        raise ValueError("최대 손실은 0 보다 크고 원금보다 작아야 합니다")
    if not 0.01 <= first_stage <= 0.5:
        raise ValueError("소액 단계 비율은 1%~50%")
    loss_pct = max_loss / principal
    pos = min(0.10, loss_pct / 0.5)
    lim = {
        "live_max_capital": round(principal),
        "live_small_capital": round(principal * first_stage),
        "max_daily_loss_pct": round(min(0.03, max(0.005, loss_pct / 8)), 4),
        "max_position_weight": round(max(0.02, pos), 4),
        "max_order_value": round(principal * max(0.02, pos)),
        "max_var95": round(min(0.04, max(0.01, loss_pct / 5)), 4),
        "max_sector_weight": round(min(0.40, max(0.10, pos * 4)), 4),
    }
    why = {
        "live_max_capital": "원금 전체가 상한 — 이보다 많이 운용하지 않음",
        "live_small_capital": f"실전 첫 단계는 원금의 {first_stage:.0%} 만",
        "max_daily_loss_pct": f"최대 손실 {loss_pct:.1%} ÷ 8 — 나쁜 날이 8번 겹쳐야 한도",
        "max_position_weight": f"한 종목이 반토막(−50%) 나도 손실 {pos * 0.5:.1%} ≤ 최대 손실 {loss_pct:.1%}",
        "max_order_value": "원금 × 종목당 비중",
        "max_var95": "하루 '20일에 한 번 꼴' 손실이 최대 손실의 1/5 이하",
        "max_sector_weight": "종목 비중 × 4 (40% 상한)",
    }
    env = [f"QUANT_LIVE_MAX_CAPITAL={lim['live_max_capital']}", f"QUANT_LIVE_SMALL_CAPITAL={lim['live_small_capital']}",
           f"QUANT_MAX_DAILY_LOSS_PCT={lim['max_daily_loss_pct']}", f"QUANT_MAX_POSITION_WEIGHT={lim['max_position_weight']}",
           f"QUANT_MAX_ORDER_VALUE={lim['max_order_value']}", f"QUANT_MAX_VAR95={lim['max_var95']}",
           f"QUANT_MAX_SECTOR_WEIGHT={lim['max_sector_weight']}"]
    return {"principal": round(principal), "max_loss": round(max_loss), "loss_pct": round(loss_pct, 4), "first_stage": first_stage,
            "limits": lim, "why": why, "env_lines": env,
            "plain": [f"원금 {principal:,.0f}원 중 최대 {max_loss:,.0f}원({loss_pct:.1%})까지 잃을 수 있다고 정했습니다.",
                      f"실전은 {lim['live_small_capital']:,.0f}원으로 시작하고, 검증 사다리를 통과해야 늘어납니다.",
                      f"하루 {lim['max_daily_loss_pct']:.1%} 넘게 잃으면 그날 신규 매수 중단, 원금 대비 {max_loss:,.0f}원 손실이면 전체 정지.",
                      f"한 종목은 최대 {lim['max_position_weight']:.0%} ({lim['max_order_value']:,.0f}원)."]}


def save(app, body: dict) -> dict:
    p = plan(body.get("principal"), body.get("max_loss"), float(body.get("first_stage") or 0.10))
    p["at"] = datetime.now(UTC).isoformat()
    ops.set_state(app.engine, KEY, p)
    apply(app)
    return p


def get(app) -> dict:
    return ops.get_state(app.engine, KEY)


def apply(app) -> bool:
    """저장된 한도를 app.settings 에 반영 (프로세스마다 시작할 때 · 저장할 때)."""
    b = get(app)
    lim = b.get("limits")
    if not lim:
        return False
    st = app.settings
    risk = replace(st.risk, max_daily_loss_pct=lim["max_daily_loss_pct"], max_position_weight=lim["max_position_weight"],
                   max_order_value=lim["max_order_value"], max_var95=lim["max_var95"], max_sector_weight=lim["max_sector_weight"])
    app.settings = replace(st, risk=risk, live_max_capital=lim["live_max_capital"], live_small_capital=lim["live_small_capital"])
    return True


def total_loss(app, mode: str) -> dict | None:
    """원금 대비 누적 손실 (가장 최근 평가금액 기준). 한도를 정하지 않았으면 None."""
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import PortfolioSnapshot
    b = get(app)
    if not b.get("principal"):
        return None
    with session_scope(app.engine) as s:
        eq = s.scalar(select(PortfolioSnapshot.equity).where(PortfolioSnapshot.mode == mode)
                      .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.id.desc()))
    if eq is None:
        return {"principal": b["principal"], "max_loss": b["max_loss"], "equity": None, "loss": 0.0, "used": 0.0}
    base = min(b["principal"], app.settings.live_max_capital) if mode == "live" else app.settings.initial_cash
    loss = max(0.0, base - eq)
    scale = b["max_loss"] / b["principal"] * base
    return {"principal": b["principal"], "max_loss": b["max_loss"], "equity": round(eq), "base": round(base),
            "loss": round(loss), "limit": round(scale), "used": round(loss / scale, 4) if scale else 0.0}


__all__ = ["plan", "save", "get", "apply", "total_loss"]

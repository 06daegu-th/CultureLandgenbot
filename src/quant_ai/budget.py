"""투자 한도 — '원금'과 '최대로 감당할 손실'을 먼저 정하고, 시스템의 모든 한도를 거기서 계산한다.

입력 (화면 '내 투자 한도' 또는 ./run.sh budget):
  principal   이 시스템에 맡길 원금 (원)
  max_loss    전체 기간 최대로 감당할 손실 (원) — 여기에 닿으면 자동 매매 전체 정지(HALTED)
  first_stage 실전 첫 단계(소액 Live)에 쓸 비율 (기본 10%)

계산 (근거를 화면에 같이 보여준다):
  실전 운용 상한      QUANT_LIVE_MAX_CAPITAL      = 원금
  소액 Live 상한      QUANT_LIVE_SMALL_CAPITAL    = 원금 × first_stage
  일 손실 한도        max_daily_loss_pct          = max(최대 손실률 ÷ 8, 평소 하루 변동 × 2.5) (0.5%~4%, 최대 손실의 절반 이하)
  종목당 비중         max_position_weight         = min(10%, 최대 손실률 ÷ 50%)  (한 종목이 반토막 나도 한도 안)
  1회 주문 상한       max_order_value             = 원금 × 종목당 비중
  1일 VaR95 한도      max_var95                   = 최대 손실률 ÷ 5  (1%~4%)
  업종 비중           max_sector_weight           = min(40%, 종목 비중 × 4)
  원금 대비 손실      guardian 'total_loss'       = max_loss 의 80% 경고 · 100% HALTED (입출금 반영) → 정지 후 처리(on_stop)
저장은 DB(ops) — 웹·스케줄러·감시 프로세스가 모두 같은 값을 쓴다. .env 에 같은 값을 넣고 싶으면 env_lines 를 복사.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from . import ops

KEY = "budget"


KILL_MULT = 1.5   # 자동 킬스위치 = 일 손실 한도 × 1.5 (pipeline · guardian 과 같은 값)
SIGMA_K = 2.5     # 일 손실 한도 ≥ 평소 하루 변동의 2.5배 — 정상적인 나쁜 날에는 걸리지 않게
STOP_POLICIES = {
    "hold": "보유 유지 — 신규 주문만 멈추고 보유 종목은 그대로 둡니다 (손실 구간도 전략의 일부라는 입장)",
    "reduce": "절반 축소 — 정지와 함께 보유분을 절반으로 줄이는 매도 주문표를 만듭니다 (사람이 확인 후 실행)",
    "liquidate": "전량 정리 — 정지와 함께 전량 매도 주문표를 만듭니다 (사람이 확인 후 실행)",
}


def strategy_vol() -> dict:
    """코어 전략의 평소 하루 변동 — 과거 16년 백테스트 기준값(strategy/health.REFERENCE)에서."""
    from .strategy.health import REFERENCE
    med, p95 = REFERENCE["vol60_median"], REFERENCE["vol60_p95"]
    return {"daily": med / 252 ** 0.5, "daily_p95": p95 / 252 ** 0.5, "annual": med, "annual_p95": p95,
            "worst_drawdown": REFERENCE["worst_drawdown"], "source": "코어 전략 백테스트 2011~2026 (60일 변동성 중앙값)"}


def market_vol(app) -> dict | None:
    """최근 1년 지수 하루 변동 (요즘 시장이 과거 평균보다 거칠면 한도도 넓혀야 정상적인 날에 안 걸린다)."""
    try:
        _, bench, _ = app.market_data()
    except Exception:  # noqa: BLE001 - 데이터가 없으면 기준값만
        return None
    if bench is None or len(bench) < 120:
        return None
    r = bench["close"].astype(float).pct_change().dropna().iloc[-250:]
    return {"daily": float(r.std()), "n": len(r), "to": str(bench.index[-1].date())}


def effective_vol(app) -> tuple[float, str]:
    sv = strategy_vol()
    mv = market_vol(app)
    if mv and mv["daily"] > sv["daily"]:
        return mv["daily"], f"최근 1년 지수 하루 변동 {mv['daily']:.2%} (과거 전략 평균 {sv['daily']:.2%} 보다 큼 → 이쪽 사용)"
    return sv["daily"], sv["source"]


def plan(principal: float, max_loss: float, first_stage: float = 0.10, daily_vol: float | None = None,
         on_stop: str = "hold") -> dict:
    principal, max_loss = float(principal), float(max_loss)
    if principal < 100_000:
        raise ValueError("원금은 10만원 이상")
    if not 0 < max_loss < principal:
        raise ValueError("최대 손실은 0 보다 크고 원금보다 작아야 합니다")
    if not 0.01 <= first_stage <= 0.5:
        raise ValueError("소액 단계 비율은 1%~50%")
    if on_stop not in STOP_POLICIES:
        raise ValueError("정지 후 처리는 hold / reduce / liquidate 중 하나")
    sv = strategy_vol()
    sd = float(daily_vol) if daily_vol else sv["daily"]
    loss_pct = max_loss / principal
    pos = min(0.10, loss_pct / 0.5)
    # 일 손실 한도: '최대 손실 ÷ 8' 과 '평소 하루 변동 × 2.5' 중 큰 값 (단, 최대 손실의 절반 이하 · 0.5%~4%)
    daily = min(0.04, max(0.005, loss_pct / 8, SIGMA_K * sd), loss_pct / 2)
    lim = {
        "live_max_capital": round(principal),
        "live_small_capital": round(principal * first_stage),
        "max_daily_loss_pct": round(daily, 4),
        "max_position_weight": round(max(0.02, pos), 4),
        "max_order_value": round(principal * max(0.02, pos)),
        "max_var95": round(min(0.04, max(0.01, loss_pct / 5, 1.65 * sd * 1.2)), 4),
        "max_sector_weight": round(min(0.40, max(0.10, pos * 4)), 4),
    }
    why = {
        "live_max_capital": "원금 전체가 상한 — 이보다 많이 운용하지 않음",
        "live_small_capital": f"실전 첫 단계는 원금의 {first_stage:.0%} 만",
        "max_daily_loss_pct": (f"max(최대 손실 {loss_pct:.1%} ÷ 8, 평소 하루 변동 {sd:.2%} × {SIGMA_K}) — 정상적인 나쁜 날엔 안 걸리게 · "
                               f"자동 킬스위치는 이것의 {KILL_MULT}배({daily * KILL_MULT:.1%}) = 평소 변동의 {daily * KILL_MULT / sd:.1f}배"),
        "max_position_weight": f"한 종목이 반토막(−50%) 나도 손실 {pos * 0.5:.1%} ≤ 최대 손실 {loss_pct:.1%}",
        "max_order_value": "원금 × 종목당 비중",
        "max_var95": "하루 '20일에 한 번 꼴' 손실 한도 — 최대 손실의 1/5 과 평소 변동 기준 중 큰 값",
        "max_sector_weight": "종목 비중 × 4 (40% 상한)",
    }
    warnings = []
    if loss_pct < -sv["worst_drawdown"]:
        warnings.append(f"이 전략의 과거 최대 낙폭은 {sv['worst_drawdown']:.0%} 입니다. 최대 손실 {loss_pct:.0%} 는 과거에 실제로 닿았던 수준이라 "
                        f"하락장 바닥 근처에서 정지될 수 있습니다 — 정지 후 처리(현재: {STOP_POLICIES[on_stop].split(' —')[0]})를 꼭 정해 두세요.")
    if daily == loss_pct / 2 and SIGMA_K * sd > daily:
        warnings.append(f"최대 손실이 작아 일 손실 한도({daily:.1%})가 평소 하루 변동의 {SIGMA_K}배보다 작습니다 — 정상적인 날에도 매수 중단이 자주 걸릴 수 있습니다.")
    env = [f"QUANT_LIVE_MAX_CAPITAL={lim['live_max_capital']}", f"QUANT_LIVE_SMALL_CAPITAL={lim['live_small_capital']}",
           f"QUANT_MAX_DAILY_LOSS_PCT={lim['max_daily_loss_pct']}", f"QUANT_MAX_POSITION_WEIGHT={lim['max_position_weight']}",
           f"QUANT_MAX_ORDER_VALUE={lim['max_order_value']}", f"QUANT_MAX_VAR95={lim['max_var95']}",
           f"QUANT_MAX_SECTOR_WEIGHT={lim['max_sector_weight']}"]
    return {"principal": round(principal), "max_loss": round(max_loss), "loss_pct": round(loss_pct, 4), "first_stage": first_stage,
            "on_stop": on_stop, "on_stop_text": STOP_POLICIES[on_stop], "stop_policies": STOP_POLICIES,
            "daily_vol": round(sd, 5), "vol_source": sv["source"] if not daily_vol else "직접 지정", "kill_at": round(daily * KILL_MULT, 4),
            "limits": lim, "why": why, "env_lines": env, "warnings": warnings,
            "plain": [f"원금 {principal:,.0f}원 중 최대 {max_loss:,.0f}원({loss_pct:.1%})까지 잃을 수 있다고 정했습니다.",
                      f"실전은 {lim['live_small_capital']:,.0f}원으로 시작하고, 검증 사다리를 통과해야 늘어납니다.",
                      f"하루 {lim['max_daily_loss_pct']:.1%} 넘게 잃으면 그날 신규 매수 중단, {daily * KILL_MULT:.1%} 넘으면 자동 정지.",
                      f"원금 대비(입출금 반영) {max_loss:,.0f}원 손실이면 전체 정지 → {STOP_POLICIES[on_stop]}",
                      f"한 종목은 최대 {lim['max_position_weight']:.0%} ({lim['max_order_value']:,.0f}원)."]}


def replay(app, p: dict | None = None, mode: str = "paper") -> dict:
    """과거 데이터로 '이 한도였으면 몇 번 걸렸을까' — 코어와 비슷하게 움직이는 지수(시총가중 대용) 일간 수익률로 재생.
    · 일 손실 한도(신규 매수 중단) · 자동 킬스위치(×1.5) 가 걸렸을 날 수 (연 평균)
    · 최대 손실 정지: 매년 1월에 시작했다면 그 뒤 언제 닿았는지 (닿은 시작 해의 비율) · 그 뒤 회복했는지
    지수는 전략 그 자체가 아니다 — 변동 크기가 비슷하다는 근사이며, 전략 백테스트 곡선이 있으면 그걸 우선 쓴다."""
    p = p or get(app)
    if not p.get("limits"):
        return {"available": False, "message": "한도를 먼저 정하세요"}
    curve = ops.get_state(app.engine, "core_backtest_curve").get("curve")
    if curve:
        import pandas as pd
        s = pd.Series({pd.Timestamp(k): float(v) for k, v in curve.items()}).sort_index()
        src = "코어 전략 백테스트 자산곡선"
    else:
        _, bench, _ = app.market_data()
        if bench is None or len(bench) < 260:
            return {"available": False, "message": "지수 일봉이 1년 미만이라 재생할 수 없습니다"}
        s = bench["close"].astype(float)
        src = "코스피 시총가중 대용 지수 (전략과 변동 크기가 비슷하다는 근사)"
    r = s.pct_change().dropna()
    lim = p["limits"]["max_daily_loss_pct"]
    years = max(len(r) / 252, 1e-9)
    stop_days = r[r <= -lim]
    kill_days = r[r <= -KILL_MULT * lim]
    loss_pct = p["loss_pct"]
    starts, hits = [], 0
    for y in sorted({t.year for t in s.index})[:-1]:
        seg = s[s.index.year >= y]
        if len(seg) < 60:
            continue
        base = float(seg.iloc[0])
        under = seg[seg <= base * (1 - loss_pct)]
        hit = None if under.empty else under.index[0]
        rec = None
        if hit is not None:
            hits += 1
            after = seg[seg.index > hit]
            back = after[after >= base]
            rec = str(back.index[0].date()) if not back.empty else None
        starts.append({"start": str(seg.index[0].date()), "hit": None if hit is None else str(hit.date()),
                       "recovered": rec, "from_hit_to_end": None if hit is None else round(float(seg.iloc[-1] / seg.loc[hit] - 1), 4)})
    dd = float((s / s.cummax() - 1).min())
    sd = float(r.std())
    out = {"available": True, "source": src, "from": str(s.index[0].date()), "to": str(s.index[-1].date()), "years": round(years, 1),
           "daily_vol": round(sd, 5), "limit": lim, "kill_at": round(lim * KILL_MULT, 4),
           "stop_days": len(stop_days), "stop_per_year": round(len(stop_days) / years, 1),
           "kill_days": len(kill_days), "kill_per_year": round(len(kill_days) / years, 2),
           "kill_dates": [str(t.date()) for t in kill_days.index[-8:]],
           "max_drawdown": round(dd, 4), "starts": starts, "hit_share": round(hits / len(starts), 3) if starts else None}
    msgs = [f"{out['from'][:4]}~{out['to'][:4]} ({out['years']}년) 동안 일 손실 한도 {lim:.1%} 에 닿은 날 {out['stop_days']}일 (연 {out['stop_per_year']}회) · "
            f"자동 정지 {out['kill_at']:.1%} 에 닿은 날 {out['kill_days']}일 (연 {out['kill_per_year']}회)"]
    if starts:
        msgs.append(f"매년 1월에 시작했다면 {len(starts)}번 중 {hits}번 최대 손실 {loss_pct:.0%} 에 닿음 · 같은 기간 최대 낙폭 {dd:.0%}")
    # 자동 정지가 '1년에 한 번 이하' 가 되는 일 손실 한도 (과거 하루 수익률 분포에서)
    q = float(r.quantile(1 / 252)) if len(r) >= 252 else float(r.min())
    out["suggested_limit"] = round(min(0.06, max(lim, -q / KILL_MULT)), 4)
    if out["kill_per_year"] > 1:
        msgs.append(f"⚠ 자동 정지가 1년에 한 번보다 자주 걸립니다 — 일 손실 한도 {out['suggested_limit']:.1%} 이상이면 이 기간 기준 연 1회 이하 "
                    f"(최대 손실을 키우면 자동으로 넓어집니다). 큰 하락이 몰린 기간이면 그 날들은 실제로 멈추는 게 맞을 수도 있습니다.")
    out["plain"] = msgs
    return out


def save(app, body: dict) -> dict:
    vol, src = effective_vol(app)
    p = plan(body.get("principal"), body.get("max_loss"), float(body.get("first_stage") or 0.10), daily_vol=vol,
             on_stop=str(body.get("on_stop") or "hold"))
    p["vol_source"] = src
    p["at"] = datetime.now(UTC).isoformat()
    p["start_equity"] = {m: _last_equity(app, m) for m in ("paper", "shadow", "live")}  # 이 시점부터 손실을 잰다
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


def _last_equity(app, mode: str) -> float | None:
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import PortfolioSnapshot
    with session_scope(app.engine) as s:
        return s.scalar(select(PortfolioSnapshot.equity).where(PortfolioSnapshot.mode == mode)
                        .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.id.desc()))


def total_loss(app, mode: str) -> dict | None:
    """원금 대비 누적 손실 — 입금(+)·출금(-)을 반영한 '넣은 돈' 대비 지금 평가금액. 한도를 정하지 않았으면 None.
    기준 = 한도를 정한 시점의 평가금액(없으면 실전은 min(원금, 실전 상한), 가상은 초기 자금) + 그 뒤 입출금 합계."""
    b = get(app)
    if not b.get("principal"):
        return None
    eq = _last_equity(app, mode)
    if eq is None:
        return {"principal": b["principal"], "max_loss": b["max_loss"], "equity": None, "loss": 0.0, "used": 0.0}
    start = (b.get("start_equity") or {}).get(mode)
    base = start if start else (min(b["principal"], app.settings.live_max_capital) if mode == "live" else app.settings.initial_cash)
    since = str(b.get("at") or "")[:10]
    flows = [f for f in (app.cashflows(mode) if hasattr(app, "cashflows") else []) if str(f.get("date")) >= since]
    net_in = sum(float(f["amount"]) for f in flows)
    invested = base + net_in
    loss = max(0.0, invested - eq)
    scale = b["max_loss"] / b["principal"] * invested if invested > 0 else 0.0
    return {"principal": b["principal"], "max_loss": b["max_loss"], "equity": round(eq), "base": round(base), "net_flows": round(net_in),
            "invested": round(invested), "n_flows": len(flows), "loss": round(loss), "limit": round(scale),
            "used": round(loss / scale, 4) if scale else 0.0, "on_stop": b.get("on_stop", "hold")}


def stop_sheet(app, mode: str) -> dict | None:
    """최대 손실 정지 때 '정지 후 처리'가 축소·정리면 매도 주문표를 만든다 — 자동으로 팔지 않는다 (사람이 확인 후 실행)."""
    b = get(app)
    pol = b.get("on_stop", "hold")
    if pol == "hold":
        return None
    pf = app.load_portfolio(mode)
    frac = 0.5 if pol == "reduce" else 1.0
    rows = [{"symbol": sym, "held": p.qty, "sell_qty": int(p.qty * frac) if frac < 1 else int(p.qty)}
            for sym, p in pf.positions.items() if p.qty]
    rows = [r for r in rows if r["sell_qty"] > 0]
    out = {"mode": mode, "policy": pol, "text": STOP_POLICIES[pol], "rows": rows, "at": datetime.now(UTC).isoformat()}
    ops.set_state(app.engine, f"stop_sheet:{mode}", out)
    return out


__all__ = ["plan", "save", "get", "apply", "total_loss", "replay", "stop_sheet", "strategy_vol", "market_vol", "effective_vol", "STOP_POLICIES", "KILL_MULT"]

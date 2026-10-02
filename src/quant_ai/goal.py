"""목표 달성 계획 — '500만원으로 1억' 같은 목표를 확률로 (몬테카를로) + 월 적립식.

왜 확률인가: 같은 연 7% 라도 순서(언제 폭락하나)에 따라 결과가 크게 달라진다. 평균 한 줄 대신
"10년 안에 도달할 확률 n%", "중간에 −30% 를 겪을 확률 n%" 를 보여 준다.

수익 가정 (연 기대수익 · 연 변동성) — 이 저장소의 16년 실데이터 연구(docs/RESEARCH_KRX.md) 와 공개 장기 통계:
  · 코어 전략          7.6% · 15.7%  (2012~2026 KRX 백테스트, 비용·세금 반영)
  · 국내 지수 ETF       8.3% · 21.4%  (같은 기간 KOSPI, 배당 제외)
  · 미국 S&P500 ETF     9.0% · 17.0%  (1926~ 장기 평균에서 보수적으로 · 환율 효과 제외)
  · 예금                3.0% ·  0.0%
  · AI 위성(검증 전)    가정하지 않는다 — 검증을 통과하기 전까지 계획에 넣지 않는다 (지금 성적은 지수보다 나쁨)
월 수익률은 꼬리가 두꺼운 t-분포(자유도 5)로 뽑는다 (정규분포보다 폭락이 자주 나온다 — 실제 시장에 가깝다).

월 적립식 (dca): 정한 날(휴장이면 다음 거래일)에
  · 모의(paper) 장부: 적립금을 장부 현금에 넣고 입금 기록 → 다음 코어 리밸런싱이 투자 (실제 돈 아님)
  · 실계좌: 돈을 자동으로 옮길 수 없으므로 알림 + 주문표 링크만 (이체는 사람이)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import numpy as np

from . import ops
from .asof import label

KEY = "goal_plan"
KST = ZoneInfo("Asia/Seoul")
PRESETS = {
    "core": {"name": "코어 전략 (이 시스템)", "mu": 0.076, "vol": 0.157, "source": "16년 KRX 백테스트 (비용·세금 반영)"},
    "kospi": {"name": "국내 지수 ETF (KOSPI200)", "mu": 0.083, "vol": 0.214, "source": "2012~2026 KOSPI"},
    "sp500": {"name": "미국 S&P500 ETF", "mu": 0.09, "vol": 0.17, "source": "장기 평균을 보수적으로 (환율 제외)"},
    "deposit": {"name": "예금", "mu": 0.03, "vol": 0.0, "source": "가정"},
}
MAX_YEARS = 30


def simulate(principal: float, monthly: float, goal: float, mu: float, vol: float, years: int = MAX_YEARS,
             n: int = 3000, raise_pct: float = 0.0, seed: int = 7) -> dict:
    """n 개의 가능한 미래. 매달 초 적립 → 한 달 수익. raise_pct: 해마다 적립액 증가율 (예: 0.03)."""
    rng = np.random.default_rng(seed)
    m = years * 12
    if vol > 0:
        dof = 5
        t = rng.standard_t(dof, size=(n, m)) / np.sqrt(dof / (dof - 2))  # 분산 1 로 맞춤
        sig = vol / np.sqrt(12)
        r = np.exp((np.log1p(mu) / 12 - 0.5 * sig ** 2) + sig * t) - 1
    else:
        r = np.full((n, m), (1 + mu) ** (1 / 12) - 1)
    contrib = np.array([monthly * (1 + raise_pct) ** (k // 12) for k in range(m)])
    w = np.empty((n, m))
    v = np.full(n, float(principal))
    peak = v.copy()
    mdd = np.zeros(n)
    for k in range(m):
        v = (v + contrib[k]) * (1 + r[:, k])
        w[:, k] = v
        peak = np.maximum(peak, v)
        mdd = np.minimum(mdd, v / peak - 1)
    hit = w >= goal
    first = np.where(hit.any(axis=1), hit.argmax(axis=1) + 1, m + 1)  # 처음 도달한 달
    by_year = [round(float((first <= 12 * y).mean()), 3) for y in range(1, years + 1)]
    paid = principal + np.cumsum(contrib)
    pct = {q: np.percentile(w, q, axis=0) for q in (10, 50, 90)}
    yearly = [{"year": y, "p10": round(float(pct[10][12 * y - 1])), "p50": round(float(pct[50][12 * y - 1])),
               "p90": round(float(pct[90][12 * y - 1])), "paid": round(float(paid[12 * y - 1]))} for y in range(1, years + 1)]
    reached = first[first <= m]
    return {"by_year": by_year, "yearly": yearly, "median_years": None if len(reached) < n / 2 else round(float(np.median(first) / 12), 1),
            "p_reach": round(float((first <= m).mean()), 3), "mdd_p50": round(float(np.median(mdd)), 3),
            "p_mdd30": round(float((mdd <= -0.30).mean()), 3)}


def required_monthly(principal: float, goal: float, years: int, mu: float, vol: float, prob: float = 0.5) -> int | None:
    """years 안에 prob 확률로 목표에 닿으려면 매달 얼마? (이분 탐색, 1만원 단위)"""
    lo, hi = 0.0, max(goal / (years * 12), 10_000) * 2
    if simulate(principal, hi, goal, mu, vol, years, n=1500)["by_year"][-1] < prob:
        return None
    for _ in range(18):
        mid = (lo + hi) / 2
        if simulate(principal, mid, goal, mu, vol, years, n=1500)["by_year"][-1] >= prob:
            hi = mid
        else:
            lo = mid
    return int(np.ceil(hi / 10_000) * 10_000)


def plan(principal: float, monthly: float, goal: float, target_years: int = 10, strategy: str = "core",
         raise_pct: float = 0.0) -> dict:
    if principal < 0 or monthly < 0 or goal <= 0:
        raise ValueError("원금·적립액은 0 이상, 목표는 0 보다 커야 합니다")
    if strategy not in PRESETS:
        raise ValueError(f"전략은 {', '.join(PRESETS)} 중 하나")
    target_years = max(1, min(int(target_years), MAX_YEARS))
    p = PRESETS[strategy]
    sim = simulate(principal, monthly, goal, p["mu"], p["vol"], MAX_YEARS, raise_pct=raise_pct)
    in_target = sim["by_year"][target_years - 1]
    need50 = required_monthly(principal, goal, target_years, p["mu"], p["vol"], 0.5)
    need80 = required_monthly(principal, goal, target_years, p["mu"], p["vol"], 0.8)
    no_add = (goal / principal) ** (1 / target_years) - 1 if principal > 0 else None
    compare = []
    for k, q in PRESETS.items():  # 고른 방식은 위와 같은 결과를 그대로 (숫자가 서로 어긋나지 않게)
        s = sim if k == strategy else simulate(principal, monthly, goal, q["mu"], q["vol"], MAX_YEARS, n=1500, raise_pct=raise_pct)
        compare.append({"key": k, "name": q["name"], "p_target": s["by_year"][target_years - 1], "median_years": s["median_years"],
                        "mdd_p50": s["mdd_p50"]})
    what_if = []
    for m in sorted({0, 300_000, 500_000, 1_000_000, 1_500_000, int(monthly)}):
        s = sim if m == int(monthly) else simulate(principal, m, goal, p["mu"], p["vol"], MAX_YEARS, n=1500, raise_pct=raise_pct)
        what_if.append({"monthly": m, "p_target": s["by_year"][target_years - 1], "median_years": s["median_years"]})
    ratio = goal / principal if principal else None
    headline = (f"{p['name']}로 매달 {monthly / 1e4:,.0f}만원씩이면 {target_years}년 안에 목표에 닿을 확률 {in_target:.0%}"
                + (f" · 절반의 경우 {sim['median_years']}년" if sim["median_years"] else f" · {MAX_YEARS}년 안에 닿지 못하는 경우가 더 많음"))
    honest = []
    if no_add is not None and no_add > 0.15:
        honest.append(f"추가 입금 없이 {target_years}년 만에 {ratio:,.0f}배가 되려면 해마다 {no_add:.0%} 가 필요합니다 — 세계 최고 펀드도 오래 유지하기 어려운 수준입니다. "
                      "몰빵·레버리지로 노리면 파산 확률이 크게 올라갑니다.")
    honest.append("가장 확실하게 확률을 올리는 것은 적립액과 기간입니다. 수익률은 내 마음대로 안 되지만 적립액은 정할 수 있습니다.")
    if sim["p_mdd30"] > 0.3:
        honest.append(f"이 계획대로 가도 중간에 고점 대비 −30% 이상 떨어지는 구간을 겪을 확률 {sim['p_mdd30']:.0%} — 그때 멈추지 않는 것이 계획의 일부입니다.")
    return {"inputs": {"principal": principal, "monthly": monthly, "goal": goal, "target_years": target_years, "strategy": strategy,
                       "raise_pct": raise_pct},
            "assumption": p | {"key": strategy}, "headline": headline, "p_target": in_target, "sim": sim,
            "need_monthly": {"p50": need50, "p80": need80}, "no_add_cagr": None if no_add is None else round(no_add, 4),
            "compare": compare, "what_if": what_if, "honest": honest,
            "note": "미래 수익률은 가정입니다 — 과거 평균과 변동성으로 3,000가지 미래를 만들어 센 확률이지 보장이 아닙니다. 세금·수수료는 연 수익률 가정에 대략 포함."}


# ------------------------------------------------------------------ 저장 · 진행률
def get(app) -> dict:
    return ops.get_state(app.engine, KEY)


def save(app, body: dict) -> dict:
    def num(k, d=0.0):
        try:
            return float(str(body.get(k, d)).replace(",", "") or d)
        except ValueError:
            raise ValueError(f"{k} 는 숫자로") from None
    cur = get(app)
    g = {"principal": num("principal", 5_000_000), "monthly": num("monthly", 0), "goal": num("goal", 100_000_000),
         "target_years": int(num("target_years", 10)), "strategy": str(body.get("strategy") or "core"), "raise_pct": num("raise_pct", 0),
         "start": cur.get("start") or datetime.now(KST).date().isoformat()}
    d = body.get("dca") or {}
    if d or cur.get("dca"):
        day = int(d.get("day", (cur.get("dca") or {}).get("day", 25)))
        if not 1 <= day <= 28:
            raise ValueError("적립일은 1~28일")
        mode = str(d.get("mode", (cur.get("dca") or {}).get("mode", "paper")))
        if mode not in ("paper", "live"):
            raise ValueError("적립 장부는 paper/live")
        g["dca"] = {"on": bool(d.get("on", (cur.get("dca") or {}).get("on", False))), "day": day, "mode": mode,
                    "amount": float(d.get("amount", g["monthly"]) or g["monthly"]), "last": (cur.get("dca") or {}).get("last")}
    plan(g["principal"], g["monthly"], g["goal"], g["target_years"], g["strategy"], g["raise_pct"])  # 검증
    g["at"] = datetime.now(UTC).isoformat()
    ops.set_state(app.engine, KEY, g)
    return g


def current_assets(app) -> tuple[float, str]:
    from .portfolio_os import _book
    try:
        vals, cash, total, names = _book(app, (get(app).get("dca") or {}).get("mode", "paper"))
    except Exception:  # noqa: BLE001
        return 0.0, "계산 실패"
    return float(total), names.get("_src", "")


def progress(app, now: datetime | None = None) -> dict:
    """홈 목표 진행률: 지금 자산 / 목표 · 계획(중간 경로) 대비 앞서는지 뒤처지는지."""
    g = get(app)
    if not g.get("goal"):
        return {"set": False, "hint": "목표를 정하면 홈에서 진행률을 보여 드립니다 (예: 500만원 → 1억)"}
    now = now or datetime.now(UTC)
    total, src = current_assets(app)
    start = date.fromisoformat(g["start"])
    months = max(0, (now.astimezone(KST).date() - start).days // 30)
    p = PRESETS.get(g["strategy"], PRESETS["core"])
    exp = g["principal"] * (1 + p["mu"]) ** (months / 12) + sum(
        g["monthly"] * (1 + p["mu"]) ** ((months - k) / 12) for k in range(months))
    pct = total / g["goal"] if g["goal"] else 0
    ahead = total - exp
    return {"set": True, "goal": g["goal"], "total": round(total), "source": src, "pct": round(min(pct, 9.99), 4),
            "months": months, "expected": round(exp), "ahead": round(ahead), "on_track": ahead >= -0.05 * exp,
            "text": f"목표 {g['goal'] / 1e8:,.2f}억 중 {pct:.1%} · 계획보다 {'앞섬' if ahead >= 0 else '뒤처짐'} {abs(ahead) / 1e4:,.0f}만원",
            "dca": g.get("dca"), "as_of": label(now)}


# ------------------------------------------------------------------ 월 적립식
def deposit(app, mode: str, amount: float, memo: str = "월 적립") -> dict:
    """모의 장부에 현금 입금 (스냅샷 한 줄 추가) + 입출금 기록 (수익률에서 입금 효과 제외)."""
    from .data.db import session_scope
    from .data.models import PortfolioSnapshot
    if mode == "live":
        raise ValueError("실계좌에는 프로그램이 돈을 넣을 수 없습니다 — 증권사 앱에서 이체하세요")
    pf = app.load_portfolio(mode)
    bars, _ = app._all_bars()
    px = {s_: float(bars[s_]["close"].iloc[-1]) for s_ in pf.positions if s_ in bars and len(bars[s_])}
    pf.cash += float(amount)
    snap = pf.snapshot(px)
    with session_scope(app.engine) as s:
        s.add(PortfolioSnapshot(mode=mode, ts=datetime.now(UTC), **snap))
    app.add_cashflow(mode, float(amount), memo=memo)
    return {"mode": mode, "amount": float(amount), "cash": round(pf.cash)}


def dca_due(g: dict, today: date, trading_day: bool) -> bool:
    d = g.get("dca") or {}
    if not d.get("on") or not trading_day:
        return False
    month = today.strftime("%Y-%m")
    return today.day >= d["day"] and (d.get("last") or "")[:7] != month


def dca_run(app, now: datetime | None = None) -> dict:
    """스케줄러가 매일 부른다. 이번 달 적립일(휴장이면 다음 거래일)에 한 번만."""
    from .alerts import push
    from .clock import MARKETS
    now = now or datetime.now(UTC)
    g = get(app)
    today = now.astimezone(KST).date()
    if not dca_due(g, today, MARKETS["KRX"].is_trading_day(today)):
        return {"done": False}
    d = g["dca"]
    amt = float(d.get("amount") or g.get("monthly") or 0)
    if amt <= 0:
        return {"done": False, "reason": "적립액 0"}
    if d["mode"] == "paper":
        r = deposit(app, "paper", amt)
        msg = f"모의 장부에 {amt / 1e4:,.0f}만원 적립 — 다음 리밸런싱 때 코어 전략대로 투자됩니다"
    else:
        r = {"mode": "live", "amount": amt}
        msg = f"오늘은 적립일 — 증권 계좌로 {amt / 1e4:,.0f}만원을 옮기고 주문표대로 사세요 (프로그램은 돈을 옮기지 않습니다)"
    d["last"] = today.isoformat()
    ops.set_state(app.engine, KEY, g | {"dca": d})
    push(app.engine, "brief", "월 적립일", msg, level="info", link="#goal", dedupe=f"dca:{today.strftime('%Y-%m')}", now=now)
    return {"done": True, **r, "message": msg}


__all__ = ["PRESETS", "simulate", "required_monthly", "plan", "get", "save", "progress", "deposit", "dca_due", "dca_run"]

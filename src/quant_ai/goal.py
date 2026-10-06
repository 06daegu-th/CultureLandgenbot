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

# v21: 계좌별 세금 (2026 기준 단순 추정 — 실제 적용은 금융사·세법 개정 확인)
#  · 일반: 국내 주식·국내주식형 ETF 매매차익 비과세, 배당·분배금 15.4% (연 배당 1.8% 가정 → 해마다 약 0.28% 손해)
#          해외지수 ETF(국내 상장)는 매매차익까지 배당소득 15.4% (팔 때) · 예금 이자 15.4%
#  · ISA(중개형): 3년 의무 · 연 2,000만원 납입 한도 · 순이익 200만원 비과세(서민형 400만) · 넘는 부분 9.9% 분리과세 · 개별 종목 가능
#          (국내 주식 매매차익은 원래 과세 대상이 아니므로 ISA 효과는 배당·이자·해외지수 ETF 에서 난다 — 200만원 비과세는 보수적으로 무시)
#  · 연금저축(+IRP): 연 900만원까지 세액공제 13.2%(총급여 5,500만 초과) ~ 16.5% · 연금으로 받을 때 3.3~5.5% · 개별 종목 불가(ETF만)
#          55세 전에 해지하면 공제받은 돈 + 이익에 16.5% (계획이 10년이면 나이를 먼저 확인)
DIV_YIELD = 0.018
ACCOUNTS = {
    "general": {"name": "일반 계좌", "note": "제한 없음 · 국내 주식 매매차익 비과세 · 배당 15.4%"},
    "isa": {"name": "ISA (중개형)", "note": "3년 이상 유지 · 연 2,000만원 한도 · 이익 200만원까지 비과세, 넘는 부분 9.9%"},
    "pension": {"name": "연금저축 + IRP", "note": "연 900만원까지 13.2% 세액공제(돌려받은 돈도 다시 투자 가정) · 55세 이후 연금으로 받을 때 3.3~5.5% · 개별 종목 불가"},
}


def _tax_terms(account: str, strategy: str) -> dict:
    """연 수익률에서 빠지는 세금(drag) · 끝에 내는 세율(end) · 비과세 한도 · 세액공제율 · 가능 여부."""
    overseas = strategy == "sp500"
    if account == "general":
        if strategy == "deposit":
            return {"drag": PRESETS["deposit"]["mu"] * 0.154, "end": 0.0, "free": 0.0, "credit": 0.0, "ok": True}
        return {"drag": 0.0 if overseas else DIV_YIELD * 0.154, "end": 0.154 if overseas else 0.0, "free": 0.0, "credit": 0.0, "ok": True}
    if account == "isa":
        # ISA 안에서도 국내 주식·국내주식형 ETF 매매차익은 과세 소득이 아니다 → 배당·이자·해외지수 ETF 이익만 9.9% (200만원 넘는 부분)
        if overseas:
            return {"drag": 0.0, "end": 0.099, "free": 2_000_000, "credit": 0.0, "ok": True}
        y = PRESETS["deposit"]["mu"] if strategy == "deposit" else DIV_YIELD
        return {"drag": y * 0.099, "end": 0.0, "free": 0.0, "credit": 0.0, "ok": True}
    # pension
    return {"drag": 0.0, "end": 0.055, "free": 0.0, "credit": 0.132, "ok": strategy != "core",
            "why_not": "연금계좌는 개별 종목을 살 수 없어 코어 전략은 불가 — 지수 ETF 로" if strategy == "core" else None}


def tax_compare(principal: float, monthly: float, goal: float, target_years: int, strategy: str, raise_pct: float = 0.0,
                n: int = 1500) -> list[dict]:
    """같은 돈 · 같은 투자 방식을 일반 / ISA / 연금 계좌에 넣으면 목표 확률이 얼마나 달라지나 (세후 기준)."""
    p = PRESETS[strategy]
    out = []
    paid = principal + sum(monthly * (1 + raise_pct) ** (k // 12) for k in range(target_years * 12))
    for key, a in ACCOUNTS.items():
        t = _tax_terms(key, strategy)
        if not t["ok"]:
            out.append({"key": key, "name": a["name"], "ok": False, "why_not": t.get("why_not"), "note": a["note"]})
            continue
        bonus = min(monthly * 12, 9_000_000) * t["credit"] / 12  # 돌려받은 공제액을 다시 넣는다 (월로 나눠)
        # 목표는 '세금 낸 뒤' 금액: 끝에 내는 세금만큼 세전 목표를 올린다 (낸 돈·비과세 한도는 세금이 없다)
        if t["end"] and strategy != "deposit":
            if key == "pension":
                pre_goal = goal / (1 - t["end"])
            else:
                base = paid + t["free"]
                pre_goal = goal if goal <= base else (goal - t["end"] * base) / (1 - t["end"])
        else:
            pre_goal = goal
        sim = simulate(principal, monthly + bonus, pre_goal, p["mu"] - t["drag"], p["vol"], MAX_YEARS, n=n, raise_pct=raise_pct)
        out.append({"key": key, "name": a["name"], "ok": True, "p_target": sim["by_year"][target_years - 1], "median_years": sim["median_years"],
                    "refund_year": round(bonus * 12), "drag": round(t["drag"], 4), "end_tax": t["end"], "note": a["note"]})
    ok = [x for x in out if x["ok"]]
    if ok:
        best = max(ok, key=lambda x: (x["p_target"], -(x["median_years"] or 99)))
        best["best"] = True
    return out


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
            "tax": tax_compare(principal, monthly, goal, target_years, strategy, raise_pct),
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
    today = datetime.now(KST).date().isoformat()
    g = {"principal": num("principal", 5_000_000), "monthly": num("monthly", 0), "goal": num("goal", 100_000_000),
         "target_years": int(num("target_years", 10)), "strategy": str(body.get("strategy") or "core"), "raise_pct": num("raise_pct", 0),
         "start": today if body.get("restart") else (cur.get("start") or today)}
    ai_cap = body.get("ai_cap", cur.get("ai_cap"))
    if ai_cap not in (None, ""):
        ai_cap = num("ai_cap", ai_cap) if "ai_cap" in body else float(ai_cap)
        if not 0 <= ai_cap <= AI_CAP_MAX:
            raise ValueError(f"AI 비중은 0~{AI_CAP_MAX:.0%}")
        g["ai_cap"] = ai_cap
    if body.get("preset") or cur.get("preset"):
        g["preset"] = str(body.get("preset") or cur.get("preset"))
    d = body.get("dca") or {}
    if d or cur.get("dca"):
        day = int(d.get("day", (cur.get("dca") or {}).get("day", 25)))
        if not 1 <= day <= 28:
            raise ValueError("적립일은 1~28일")
        mode = str(d.get("mode", (cur.get("dca") or {}).get("mode", "paper")))
        if mode not in ("paper", "live"):
            raise ValueError("적립 장부는 paper/live")
        prev = cur.get("dca") or {}
        target = str(d.get("target", prev.get("target", "core")))
        if target not in ("core", "etf"):
            raise ValueError("적립 대상은 core/etf")
        etf = str(d.get("etf", prev.get("etf", "069500")))
        if etf not in ETFS:
            raise ValueError(f"ETF 는 {', '.join(ETFS)} 중 하나")
        g["dca"] = {"on": bool(d.get("on", prev.get("on", False))), "day": day, "mode": mode, "target": target, "etf": etf,
                    "amount": float(d.get("amount", g["monthly"]) or g["monthly"]), "last": prev.get("last")}
    pl = plan(g["principal"], g["monthly"], g["goal"], g["target_years"], g["strategy"], g["raise_pct"])  # 검증
    g["checkpoints"] = pl["sim"]["yearly"][: g["target_years"] + 3]  # 해마다 '이쯤이면 정상' 범위 (진행률 판정용으로 저장)
    g["p_target"] = pl["p_target"]
    g["at"] = datetime.now(UTC).isoformat()
    ops.set_state(app.engine, KEY, g)
    dd = g.get("dca") or {}
    if dd.get("on") and dd.get("mode") == "paper" and dd.get("target") == "etf" and g["principal"] > 0 and not app.cashflows(ETF_BOOK):
        deposit(app, ETF_BOOK, g["principal"], memo="시작 원금")  # ETF 적립 장부는 원금부터 같이 굴린다
        g["seeded"] = etf_buy(app, dd.get("etf") or "069500")
    return g


def current_assets(app) -> tuple[float, str]:
    from .portfolio_os import _book
    d = get(app).get("dca") or {}
    if d.get("mode") == "paper" and d.get("target") == "etf":
        pf = _load_book(app, ETF_BOOK)
        return float(pf.equity(_book_prices(app, pf))), f"ETF 적립 모의 장부 ({ETFS.get(d.get('etf') or '069500')})"
    try:
        vals, cash, total, names = _book(app, d.get("mode", "paper"))
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
    out = {"set": True, "goal": g["goal"], "total": round(total), "source": src, "pct": round(min(pct, 9.99), 4),
           "months": months, "expected": round(exp), "ahead": round(ahead), "on_track": ahead >= -0.05 * exp,
           "text": f"목표 {g['goal'] / 1e8:,.2f}억 중 {pct:.1%} · 계획보다 {'앞섬' if ahead >= 0 else '뒤처짐'} {abs(ahead) / 1e4:,.0f}만원",
           "dca": g.get("dca"), "ai_cap": g.get("ai_cap"), "preset": g.get("preset"), "as_of": label(now)}
    cps = g.get("checkpoints") or []
    if cps:
        out |= band_check(g, cps, months, total, start)
    return out


def band_at(g: dict, cps: list[dict], months: int) -> dict:
    """시작 뒤 months 개월째 '정상 범위' (하위 10% · 중간 · 상위 10%) — 해마다 값 사이를 직선으로 잇는다."""
    pts = [{"m": 0, "p10": g["principal"], "p50": g["principal"], "p90": g["principal"], "paid": g["principal"]}] + \
          [{"m": 12 * c["year"], **{k: c[k] for k in ("p10", "p50", "p90", "paid")}} for c in cps]
    months = max(0, min(months, pts[-1]["m"]))
    for a, b in zip(pts, pts[1:], strict=False):
        if a["m"] <= months <= b["m"]:
            f = (months - a["m"]) / (b["m"] - a["m"])
            return {k: round(a[k] + (b[k] - a[k]) * f) for k in ("p10", "p50", "p90", "paid")}
    return {k: pts[-1][k] for k in ("p10", "p50", "p90", "paid")}


def band_check(g: dict, cps: list[dict], months: int, total: float, start: date) -> dict:
    b = band_at(g, cps, months)
    if months < 1:
        lv, msg = "start", "이제 시작 — 한 달 뒤부터 '정상 범위' 안에 있는지 보여 드려요"
    elif total >= b["p90"]:
        lv, msg = "great", "상위 10% 보다 앞서는 중 — 운이 좋은 구간일 수 있어요, 적립은 그대로"
    elif total >= b["p50"]:
        lv, msg = "good", "계획의 중간보다 앞서는 중"
    elif total >= b["p10"]:
        lv, msg = "ok", "정상 범위 안 (중간보다는 뒤) — 하락장에선 흔한 일, 적립만 멈추지 않으면 돼요"
    else:
        lv, msg = "low", "하위 10% 아래 — 적립을 빼먹었는지 먼저 확인하고, 계속 이러면 적립액이나 기간을 다시 보세요"
    nxt = next((c for c in cps if 12 * c["year"] > months), None)
    y = start.year + (nxt["year"] if nxt else 0)
    return {"band": b, "band_level": lv, "band_text": msg,
            "next_check": {"year": nxt["year"], "date": f"{y}-{start.month:02d}", "p10": nxt["p10"], "p50": nxt["p50"], "paid": nxt["paid"]} if nxt else None}


# ------------------------------------------------------------------ v31 추천 계획
AI_CAP_MAX = 0.30  # AI 자동매매에 맡길 수 있는 최대 비중 (검증 전 AI 에 큰 돈을 맡기지 않게)
RECOMMENDED = {
    "m100": {"title": "200만원 + 매달 100만원 → 1억", "principal": 2_000_000, "monthly": 1_000_000, "goal": 100_000_000,
             "target_years": 7, "strategy": "kospi", "raise_pct": 0.0, "ai_cap": 0.15,
             "dca": {"on": True, "day": 25, "target": "etf", "etf": "069500"},
             "why": ["돈의 대부분은 수익이 아니라 매달 넣는 100만원에서 나와요 (7년 동안 넣는 돈 약 8,600만원)",
                     "본체(85%)는 KODEX 200 에 매달 적립 — 검증이 필요 없는, 가장 싸고 확실한 방법",
                     "AI 자동매매는 관문 5개를 다 넘은 뒤에만 실제 계좌에서, 그것도 전체의 15% 까지만",
                     "중간에 −30% 넘게 빠지는 구간이 거의 확실히 와요 — 그때 적립을 멈추지 않는 게 계획의 절반"]},
}


def order_sheet(app, etf: str, amount: float) -> str:
    lc = last_close(app, etf)
    if not lc:
        return f"{ETFS.get(etf, etf)} ({etf}) {amount / 1e4:,.0f}만원어치 시장가 (가격을 못 받아 주 수는 증권사 앱에서)"
    return f"{ETFS.get(etf, etf)} ({etf}) {int(amount // (lc[0] * 1.001))}주 시장가 ({lc[1]} 종가 {lc[0]:,.0f}원 기준)"


def apply_recommended(app, key: str = "m100", mode: str = "paper", monthly: float | None = None, principal: float | None = None,
                      day: int | None = None, etf: str | None = None) -> dict:
    """추천 계획을 한 번에 저장 (시작일 = 오늘). mode: paper = 모의 ETF 장부로 연습 · live = 실계좌 (알림 + 주문표, 돈은 사람이 옮김)."""
    if key not in RECOMMENDED:
        raise ValueError(f"추천 계획은 {', '.join(RECOMMENDED)} 중 하나")
    if mode not in ("paper", "live"):
        raise ValueError("장부는 paper/live")
    r = RECOMMENDED[key]
    body = {k: r[k] for k in ("principal", "monthly", "goal", "target_years", "strategy", "raise_pct", "ai_cap")}
    if monthly is not None:
        body["monthly"] = float(monthly)
    if principal is not None:
        body["principal"] = float(principal)
    d = dict(r["dca"]) | {"mode": mode, "amount": body["monthly"]}
    if day is not None:
        d["day"] = int(day)
    if etf:
        d["etf"] = etf
    g = save(app, body | {"dca": d, "preset": key, "restart": True})
    out = {"saved": g, "plan": key, "mode": mode}
    if mode == "live":
        out["start_sheet"] = order_sheet(app, d["etf"], body["principal"])
        out["next"] = (f"오늘 할 일: 증권 계좌(가능하면 ISA)에 {body['principal'] / 1e4:,.0f}만원을 넣고 {out['start_sheet']} · "
                       f"매달 {d['day']}일에 {body['monthly'] / 1e4:,.0f}만원 알림과 주문표가 와요")
    else:
        out["next"] = f"모의 ETF 장부에 {body['principal'] / 1e4:,.0f}만원으로 시작했어요 · 매달 {d['day']}일에 {body['monthly'] / 1e4:,.0f}만원씩 자동으로 넣고 사요"
    return out


# ------------------------------------------------------------------ 월 적립식
ETF_BOOK = "etf-dca"  # 월 적립 ETF 전용 모의 장부 (코어가 리밸런싱하는 paper 장부와 섞지 않는다 — 섞으면 코어가 ETF 를 팔아 버림)
ETFS = {"069500": "KODEX 200 (국내 대표 200종목)", "360750": "TIGER 미국S&P500"}


def last_close(app, symbol: str) -> tuple[float, str] | None:
    """(마지막 종가, 날짜). DB 에 없으면 Yahoo(069500.KS)에서 최근 2주를 받아 본다 (인터넷이 막히면 None)."""
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import PriceBar

    def read():
        with session_scope(app.engine) as s:
            for sym in (symbol, f"{symbol}.KS"):
                b = s.scalar(select(PriceBar).where(PriceBar.symbol == sym, PriceBar.interval == "1d").order_by(PriceBar.ts.desc()).limit(1))
                if b is not None and b.close:
                    return float(b.close), str(b.ts.date())
        return None
    got = read()
    if got is None and symbol.isdigit():
        try:
            from datetime import timedelta

            from .data.collectors.prices import YahooPriceSource
            from .data.models import Instrument
            with session_scope(app.engine) as s:
                if s.scalar(select(Instrument).where(Instrument.symbol == f"{symbol}.KS")) is None:
                    s.add(Instrument(symbol=f"{symbol}.KS", market="KRX", name=ETFS.get(symbol, symbol)))
            now = datetime.now(UTC)
            app.ingest_prices(YahooPriceSource(), [f"{symbol}.KS"], now - timedelta(days=14), now)
            got = read()
        except Exception:  # noqa: BLE001 - 인터넷이 막혀도 적립은 현금으로 계속
            got = None
    return got


def _load_book(app, mode: str):
    """ETF 적립 장부는 0원에서 시작 (paper 는 설정의 초기 자금)."""
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import PortfolioSnapshot
    from .trading.portfolio import Portfolio
    if mode == ETF_BOOK:
        with session_scope(app.engine) as s:
            has = s.scalar(select(PortfolioSnapshot.id).where(PortfolioSnapshot.mode == mode).limit(1))
        if has is None:
            return Portfolio(cash=0.0)
    return app.load_portfolio(mode)


def _book_prices(app, pf) -> dict[str, float]:
    bars, _ = app._all_bars()
    px = {}
    for s_ in pf.positions:
        if s_ in bars and len(bars[s_]):
            px[s_] = float(bars[s_]["close"].iloc[-1])
        else:
            lc = last_close(app, s_) if s_ in ETFS else None
            px[s_] = lc[0] if lc else pf.positions[s_].avg_price
    return px


def _save_snapshot(app, mode: str, pf) -> dict:
    from .data.db import session_scope
    from .data.models import PortfolioSnapshot
    snap = pf.snapshot(_book_prices(app, pf))
    with session_scope(app.engine) as s:
        s.add(PortfolioSnapshot(mode=mode, ts=datetime.now(UTC), **snap))
    return snap


def deposit(app, mode: str, amount: float, memo: str = "월 적립") -> dict:
    """모의 장부에 현금 입금 (스냅샷 한 줄 추가) + 입출금 기록 (수익률에서 입금 효과 제외)."""
    if mode == "live":
        raise ValueError("실계좌에는 프로그램이 돈을 넣을 수 없습니다 — 증권사 앱에서 이체하세요")
    pf = _load_book(app, mode)
    pf.cash += float(amount)
    _save_snapshot(app, mode, pf)
    app.add_cashflow(mode, float(amount), memo=memo)
    return {"mode": mode, "amount": float(amount), "cash": round(pf.cash)}


def etf_buy(app, symbol: str = "069500") -> dict:
    """ETF 적립 장부의 현금으로 ETF 를 산다 (1주 단위 · 수수료·미끄러짐 반영). 가격을 못 받으면 현금으로 둔다."""
    from .trading.portfolio import CostModel, Fill, Order, Side
    pf = _load_book(app, ETF_BOOK)
    lc = last_close(app, symbol)
    if lc is None:
        return {"bought": 0, "reason": f"{ETFS.get(symbol, symbol)} 가격을 받지 못함 — 현금으로 두고 다음 날 다시 시도", "cash": round(pf.cash)}
    price, day = lc
    cm = CostModel(app.settings.costs)
    fill_px = cm.fill_price(Side.BUY, price)
    qty = int(pf.cash // (fill_px * (1 + app.settings.costs.commission_bps / 1e4)))
    if qty <= 0:
        return {"bought": 0, "reason": "1주 살 돈이 안 됨 — 다음 적립 때 함께", "cash": round(pf.cash), "price": price}
    order = Order(symbol=symbol, side=Side.BUY, qty=qty, reason="월 적립 ETF", ref_price=price)
    pf.apply(Fill(order=order, ts=datetime.now(UTC), qty=qty, price=fill_px, fee=cm.fee(Side.BUY, fill_px, qty)))
    _save_snapshot(app, ETF_BOOK, pf)
    return {"bought": qty, "price": round(fill_px, 2), "price_date": day, "cost": round(fill_px * qty), "cash": round(pf.cash)}


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
    d0 = g.get("dca") or {}
    pending = None
    if d0.get("on") and d0.get("mode") == "paper" and d0.get("target") == "etf" and MARKETS["KRX"].is_trading_day(today):
        pf = _load_book(app, ETF_BOOK)
        if pf.cash > 0 and (d0.get("last") or "") != today.isoformat():
            pending = etf_buy(app, d0.get("etf") or "069500")  # 지난번에 가격을 못 받아 남은 현금 → 오늘 산다
    if not dca_due(g, today, MARKETS["KRX"].is_trading_day(today)):
        return {"done": False, **({"pending_buy": pending} if pending else {})}
    d = g["dca"]
    amt = float(d.get("amount") or g.get("monthly") or 0)
    if amt <= 0:
        return {"done": False, "reason": "적립액 0"}
    etf = d.get("etf") or "069500"
    if d["mode"] == "paper" and d.get("target") == "etf":
        r = deposit(app, ETF_BOOK, amt)
        b = etf_buy(app, etf)
        r |= {"buy": b}
        msg = (f"ETF 적립 장부에 {amt / 1e4:,.0f}만원 입금 → {ETFS.get(etf, etf)} {b['bought']}주 매수 (주당 약 {b['price']:,.0f}원)"
               if b.get("bought") else f"ETF 적립 장부에 {amt / 1e4:,.0f}만원 입금 — {b['reason']}")
    elif d["mode"] == "paper":
        r = deposit(app, "paper", amt)
        msg = f"모의 장부에 {amt / 1e4:,.0f}만원 적립 — 다음 리밸런싱 때 코어 전략대로 투자됩니다"
    else:
        sheet = f" · 주문표: {order_sheet(app, etf, amt)}" if d.get("target") == "etf" else ""
        r = {"mode": "live", "amount": amt, "sheet": sheet.strip(" ·")}
        msg = f"오늘은 적립일 — 증권 계좌로 {amt / 1e4:,.0f}만원을 옮기고 사세요{sheet} (프로그램은 돈을 옮기지 않습니다)"
    d["last"] = today.isoformat()
    ops.set_state(app.engine, KEY, g | {"dca": d})
    push(app.engine, "brief", "월 적립일", msg, level="info", link="#goal", dedupe=f"dca:{today.strftime('%Y-%m')}", now=now)
    return {"done": True, **r, "message": msg}


__all__ = ["PRESETS", "ACCOUNTS", "ETFS", "ETF_BOOK", "RECOMMENDED", "AI_CAP_MAX", "apply_recommended", "band_at", "order_sheet", "simulate", "required_monthly", "plan", "tax_compare", "get", "save", "progress",
           "deposit", "etf_buy", "last_close", "dca_due", "dca_run"]

"""포트폴리오 리스크 — 종목 한도만이 아니라 '묶음'으로 본 위험.

과거 시뮬레이션(최근 N거래일 실제 수익률 × 지금 비중)으로 계산한다 (분포 가정 없음).

- VaR95 / ES95 (1일): 평범한 나쁜 날 / 최악 5% 날의 평균 손실
- 변동성 · KOSPI 베타 · 최악의 날 · 시장 -10% 시나리오
- 상관관계 군집: 섹터 데이터 없이도 '같이 움직이는 종목 묶음'의 합산 비중 (섹터 집중 대용)
- 유동성: 20일 평균 거래대금 대비 보유 금액 → 참여율 10% 로 청산에 걸리는 일수
- 통화 노출: KRW / USD 비중 (해외 종목 보유 시 환율 위험)
- 위험 기여도: 손실 꼬리(최악 5% 날)에서 각 종목이 차지한 몫
v13 추가
- VaR 여러 방식: 과거 시뮬레이션 95/99 · 정규분포 · Cornish-Fisher(왜도·첨도 보정) · EWMA(최근 변동성 가중) · 10일
- 업종 노출 (섹터 엔진) · 집중도 (HHI · 유효 종목 수 · 상위 1/5 비중)
- 성분 VaR (공분산 기준, 합 = 포트폴리오 VaR) · 상관 행렬 (히트맵용)
- 유동성 조정 VaR: 오늘 전부 판다면 드는 시장 충격 비용을 VaR 에 더한다
- 스트레스: 실제 과거 위기 구간 재생(데이터가 있으면 종목 실제 수익률, 없으면 베타 × 지수) + 가정 시나리오
- 사전 게이트: 주문을 넣기 전에 '넣은 뒤의 포트폴리오'가 업종·묶음·VaR·집중 한도를 넘는지 계산해 수량을 줄인다
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PortfolioRiskLimits:
    max_var95: float = 0.04  # 1일 VaR95 가 평가금액의 4% 를 넘으면 전체 비중 축소
    max_cluster_weight: float = 0.40  # 같이 움직이는 묶음 합산 비중 경고
    max_days_to_liquidate: float = 3.0  # 참여율 10% 로 3일 넘게 걸리면 경고
    cluster_corr: float = 0.6  # 이 상관 이상이면 같은 묶음
    max_sector_weight: float = 0.40  # 한 업종 합산 비중
    max_name_weight: float = 0.15  # 한 종목 비중 (집중도 경고)
    min_effective_n: float = 5.0  # 유효 종목 수가 이보다 작으면 사실상 몇 종목 베팅
    max_stress_loss: float = 0.25  # 가장 나쁜 스트레스 시나리오 손실 경고선


# 실제 과거 위기 구간 (데이터가 이 구간을 덮을 때만 계산)
HISTORICAL_SCENARIOS = (
    ("2008 금융위기", "2008-09-01", "2008-10-24"),
    ("2011 미국 신용등급 강등", "2011-08-01", "2011-08-19"),
    ("2018 10월 급락", "2018-10-01", "2018-10-29"),
    ("2020 코로나 급락", "2020-02-19", "2020-03-19"),
    ("2022 금리 급등 약세장", "2022-01-03", "2022-09-30"),
    ("2024-08 블랙먼데이", "2024-07-31", "2024-08-05"),
)
Z95, Z99 = 1.6449, 2.3263


def _returns(bars: dict[str, pd.DataFrame], symbols, lookback: int) -> pd.DataFrame:
    closes = {s: bars[s]["close"] for s in symbols if s in bars and len(bars[s]) > 20}
    if not closes:
        return pd.DataFrame()
    return pd.DataFrame(closes).sort_index().pct_change(fill_method=None).iloc[-lookback:]


def clusters(corr: pd.DataFrame, threshold: float) -> list[list[str]]:
    """상관계수 ≥ threshold 인 종목끼리 연결 (union-find) → 묶음."""
    syms = list(corr.columns)
    parent = {s: s for s in syms}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            if pd.notna(corr.loc[a, b]) and corr.loc[a, b] >= threshold:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for s in syms:
        groups.setdefault(find(s), []).append(s)
    return sorted(groups.values(), key=len, reverse=True)


def concentration(w: dict[str, float]) -> dict:
    """집중도: 주식 부분만 100% 로 놓고 본 HHI · 유효 종목 수 · 상위 1/5 비중 (평가금액 대비)."""
    v = sorted((x for x in w.values() if x > 0), reverse=True)
    g = sum(v)
    if not v or g <= 0:
        return {"hhi": 0.0, "effective_n": 0.0, "top1": 0.0, "top5": 0.0}
    hhi = sum((x / g) ** 2 for x in v)
    return {"hhi": round(hhi, 4), "effective_n": round(1 / hhi, 2), "top1": round(v[0], 4), "top5": round(sum(v[:5]), 4)}


def sector_exposure(w: dict[str, float], sectors: dict[str, str] | None, names: dict[str, str] | None = None) -> list[dict]:
    by: dict[str, list[str]] = {}
    for s, x in w.items():
        if x > 0:
            by.setdefault((sectors or {}).get(s) or "미분류", []).append(s)
    out = [{"sector": k, "weight": round(sum(w[s] for s in v), 4), "n": len(v),
            "members": [(names or {}).get(s, s) for s in sorted(v, key=lambda s: -w[s])]} for k, v in by.items()]
    return sorted(out, key=lambda x: -x["weight"])


def var_methods(port: pd.Series) -> dict:
    """같은 포트폴리오 수익률로 VaR 을 여러 방식으로 계산 — 방식마다 크게 다르면 꼬리가 두껍다는 뜻."""
    x = port.dropna()
    if len(x) < 30:
        return {}
    mu, sd = float(x.mean()), float(x.std())
    q5, q1 = float(x.quantile(0.05)), float(x.quantile(0.01))
    sk = float(x.skew()) if sd > 0 else 0.0
    ku = float(x.kurt()) if sd > 0 else 0.0  # 초과 첨도
    z = -Z95
    zcf = z + (z * z - 1) * sk / 6 + (z ** 3 - 3 * z) * ku / 24 - (2 * z ** 3 - 5 * z) * sk * sk / 36
    lam, var_e = 0.94, float(x.iloc[:20].var()) if len(x) > 20 else sd * sd
    for r in x.iloc[20:]:
        var_e = lam * var_e + (1 - lam) * float(r) ** 2
    t5, t1 = x[x <= q5], x[x <= q1]
    return {
        "hist95": round(-q5, 5), "hist99": round(-q1, 5),
        "es95": round(-float(t5.mean()), 5), "es99": round(-float(t1.mean()), 5) if len(t1) else round(-q1, 5),
        "normal95": round(-(mu - Z95 * sd), 5), "normal99": round(-(mu - Z99 * sd), 5),
        "cornish_fisher95": round(-(mu + zcf * sd), 5),
        "ewma95": round(Z95 * float(np.sqrt(var_e)), 5),
        "hist95_10d": round(-q5 * np.sqrt(10), 5),
        "skew": round(sk, 3), "excess_kurtosis": round(ku, 3),
        "fat_tail": bool(ku > 3 or (-q1) > 1.3 * (-(mu - Z99 * sd))),
    }


def component_var(R: pd.DataFrame, wv: pd.Series, names: dict[str, str] | None = None) -> list[dict]:
    """성분 VaR (정규 근사): 각 종목이 포트폴리오 VaR 에 보태는 양. 합 = 포트폴리오 VaR."""
    C = R.fillna(0.0).cov().to_numpy()
    w = wv.to_numpy()
    sp = float(np.sqrt(max(w @ C @ w, 0)))
    if sp <= 0:
        return []
    marg = Z95 * (C @ w) / sp
    comp = w * marg
    tot = float(comp.sum()) or 1e-12
    return sorted(({"symbol": s, "name": (names or {}).get(s, s), "weight": round(float(w[i]), 4),
                    "marginal": round(float(marg[i]), 5), "component": round(float(comp[i]), 5),
                    "share": round(float(comp[i] / tot), 4)} for i, s in enumerate(wv.index)), key=lambda x: -x["component"])


def corr_matrix(R: pd.DataFrame, w: dict[str, float], names: dict[str, str] | None = None, top: int = 12) -> dict:
    syms = [s for s in sorted(R.columns, key=lambda s: -w.get(s, 0))][:top]
    if len(syms) < 2:
        return {}
    c = R[syms].corr(min_periods=30)
    return {"symbols": syms, "names": [(names or {}).get(s, s) for s in syms],
            "values": [[None if pd.isna(v) else round(float(v), 2) for v in row] for row in c.to_numpy()]}


def liquidation_cost(w: dict[str, float], bars: dict[str, pd.DataFrame], values: dict[str, float] | None,
                     impact_coef: float = 0.7, cap_bps: float = 150.0) -> dict:
    """오늘 전부 판다고 가정한 시장 충격 비용 (제곱근 법칙, 평가금액 대비)."""
    tot = 0.0
    per = []
    for s, x in w.items():
        b = bars.get(s)
        val = (values or {}).get(s)
        if b is None or not val or "volume" not in b or len(b) < 21:
            continue
        t = b.iloc[-20:]
        adv = float((t["close"] * t["volume"]).mean())
        sig = float(np.log(b["close"]).diff().iloc[-20:].std())
        if adv <= 0 or not np.isfinite(sig):
            continue
        bps = min(cap_bps, impact_coef * sig * np.sqrt(val / adv) * 1e4)
        tot += x * bps / 1e4
        per.append({"symbol": s, "bps": round(float(bps), 1)})
    return {"cost": round(tot, 5), "per": sorted(per, key=lambda r: -r["bps"])[:10]}


def stress_tests(w: dict[str, float], bars: dict[str, pd.DataFrame], bench: pd.DataFrame | None, beta: float | None,
                 sectors: dict[str, str] | None, currencies: dict[str, str], var95: float | None,
                 corr_one_var: float | None, days_liq: float | None) -> list[dict]:
    """과거 위기 재생 + 가정 시나리오. loss 는 평가금액 대비 손실(양수 = 손실)."""
    out = []
    closes = {s: bars[s]["close"] for s in w if s in bars and len(bars[s])}
    bclose = bench["close"] if bench is not None and len(bench) else None
    for name, a, b in HISTORICAL_SCENARIOS:
        t0, t1 = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")
        pnl, covered, proxied = 0.0, 0.0, 0.0
        bret = None
        if bclose is not None:
            bc = bclose[(bclose.index >= t0) & (bclose.index <= t1)]
            if len(bc) >= 2 and bclose.index[0] <= t0 + pd.Timedelta(days=7):
                bret = float(bc.iloc[-1] / bc.iloc[0] - 1)
        for s, x in w.items():
            c = closes.get(s)
            seg = c[(c.index >= t0) & (c.index <= t1)] if c is not None else None
            if seg is not None and len(seg) >= 2 and c.index[0] <= t0 + pd.Timedelta(days=7):
                pnl += x * float(seg.iloc[-1] / seg.iloc[0] - 1)
                covered += x
            elif bret is not None:
                pnl += x * (beta if beta is not None else 1.0) * bret
                proxied += x
        if covered + proxied > 0:
            out.append({"kind": "historical", "name": name, "period": f"{a} ~ {b}", "loss": round(-pnl, 4),
                        "market": None if bret is None else round(bret, 4),
                        "basis": "종목 실제 수익률" if proxied == 0 else "일부 베타×지수 대용" if covered else "베타 × 지수 (종목 이력 없음)"})
    b = beta if beta is not None else 1.0
    g = sum(x for x in w.values() if x > 0)
    out.append({"kind": "hypothetical", "name": "시장 -10%", "loss": round(0.10 * b * g, 4), "basis": f"베타 {b:.2f}"})
    out.append({"kind": "hypothetical", "name": "시장 -20% (약세장)", "loss": round(0.20 * b * g, 4), "basis": f"베타 {b:.2f}"})
    sec = sector_exposure(w, sectors)
    if sec and sec[0]["sector"] != "미분류":
        out.append({"kind": "hypothetical", "name": f"최대 업종({sec[0]['sector']}) -15%", "loss": round(0.15 * sec[0]["weight"], 4),
                    "basis": f"업종 비중 {sec[0]['weight']:.0%}"})
    usd = sum(x for s, x in w.items() if currencies.get(s, "KRW") != "KRW")
    if usd > 0:
        out.append({"kind": "hypothetical", "name": "원화 10% 강세 (달러 자산 환손실)", "loss": round(0.10 * usd, 4),
                    "basis": f"달러 자산 {usd:.0%}"})
    if var95:
        out.append({"kind": "hypothetical", "name": "변동성 2배 (1일 VaR95)", "loss": round(2 * var95, 4), "basis": "현재 VaR × 2"})
    if corr_one_var:
        out.append({"kind": "hypothetical", "name": "상관 붕괴 (모두 같이 움직임, 1일 VaR95)", "loss": round(corr_one_var, 4),
                    "basis": "분산 효과 0"})
    if days_liq:
        out.append({"kind": "hypothetical", "name": "유동성 경색 (거래대금 1/3)", "loss": None,
                    "basis": f"최장 청산 {days_liq * 3:.1f}일로 늘어남"})
    return out


def portfolio_risk(weights: dict[str, float], bars: dict[str, pd.DataFrame], bench: pd.DataFrame | None = None,
                   names: dict[str, str] | None = None, currencies: dict[str, str] | None = None,
                   values: dict[str, float] | None = None, lookback: int = 250,
                   limits: PortfolioRiskLimits | None = None, sectors: dict[str, str] | None = None,
                   impact_coef: float = 0.7) -> dict:
    """weights: 평가금액 대비 비중 {종목: w} (현금은 나머지). values: 보유 금액(유동성 계산용)."""
    limits = limits or PortfolioRiskLimits()
    names = names or {}
    w = {s: float(x) for s, x in weights.items() if x > 0}
    out: dict = {"n_positions": len(w), "gross": round(sum(w.values()), 4), "warnings": [], "limits": limits.__dict__}
    if not w:
        return out | {"var95": 0.0, "es95": 0.0, "vol": 0.0, "beta": 0.0}
    cur_map = {s: (currencies or {}).get(s) or ("KRW" if s.isdigit() or s.endswith((".KS", ".KQ")) else "USD") for s in w}
    out["concentration"] = conc = concentration(w)
    out["sectors"] = sec = sector_exposure(w, sectors, names)
    for x in sec:
        if x["sector"] != "미분류" and x["weight"] > limits.max_sector_weight:
            out["warnings"].append(f"업종 {x['sector']} 비중 {x['weight']:.0%} > 한도 {limits.max_sector_weight:.0%}")
    if conc["top1"] > limits.max_name_weight:
        top = max(w, key=w.get)
        out["warnings"].append(f"한 종목({names.get(top, top)}) 비중 {conc['top1']:.0%} > {limits.max_name_weight:.0%}")
    if len(w) >= 3 and conc["effective_n"] < limits.min_effective_n:
        out["warnings"].append(f"유효 종목 수 {conc['effective_n']:.1f} — 사실상 몇 종목에 몰린 베팅")
    R = _returns(bars, list(w), lookback)
    missing = [s for s in w if s not in R.columns]
    if missing:
        out["warnings"].append(f"가격 이력 부족으로 제외: {', '.join(names.get(m, m) for m in missing)}")
    if R.empty or len(R) < 30:
        return out | {"insufficient": True}
    wv = pd.Series({s: w[s] for s in R.columns})
    port = R.fillna(0.0) @ wv
    q = float(port.quantile(0.05))
    tail = port[port <= q]
    out |= {
        "days": int(len(port)), "as_of": str(port.index[-1]),
        "var95": round(-q, 5), "es95": round(-float(tail.mean()), 5),
        "vol": round(float(port.std() * np.sqrt(252)), 4),
        "worst_day": round(float(port.min()), 5), "worst_day_date": str(port.idxmin().date()),
    }
    out["var"] = var_methods(port)
    # 위험 기여도 (꼬리 손실 중 각 종목 몫)
    contrib = (R.loc[tail.index].fillna(0.0) * wv).mean()
    tot = float(contrib.sum()) or -1e-12
    out["risk_contrib"] = sorted(({"symbol": s, "name": names.get(s, s), "weight": round(w[s], 4),
                                   "share": round(float(contrib[s] / tot), 4)} for s in R.columns),
                                 key=lambda x: -x["share"])
    out["component_var"] = component_var(R, wv, names)
    beta = None
    if bench is not None and len(bench) > 30:
        b = bench["close"].pct_change(fill_method=None).reindex(port.index)
        ok = b.notna()
        if ok.sum() > 30 and b[ok].var() > 0:
            beta = float(np.cov(port[ok], b[ok])[0, 1] / b[ok].var())
            out["beta"] = round(beta, 3)
            out["stress_market_-10pct"] = round(-0.10 * beta, 4)
            out["corr_to_market"] = round(float(np.corrcoef(port[ok], b[ok])[0, 1]), 3)
    # 상관 · 군집
    corr = R.corr(min_periods=30)
    if len(corr) >= 2:
        m = corr.to_numpy()
        iu = np.triu_indices(len(m), 1)
        vals = m[iu]
        ww = np.array([w[a] * w[b] for a, b in zip(corr.columns[iu[0]], corr.columns[iu[1]], strict=True)])
        ok = ~np.isnan(vals)
        out["avg_corr"] = round(float(np.average(vals[ok], weights=ww[ok])) if ok.any() and ww[ok].sum() else 0.0, 3)
    out["corr_matrix"] = corr_matrix(R, w, names)
    groups = [g for g in clusters(corr, limits.cluster_corr) if len(g) >= 2] if len(corr) >= 2 else []
    out["clusters"] = [{"members": [names.get(s, s) for s in g], "symbols": g,
                        "weight": round(sum(w[s] for s in g), 4)} for g in groups]
    for g in out["clusters"]:
        if g["weight"] > limits.max_cluster_weight:
            out["warnings"].append(f"같이 움직이는 묶음({', '.join(g['members'][:3])}…) 비중 {g['weight']:.0%} "
                                   f"> {limits.max_cluster_weight:.0%} — 사실상 한 종목처럼 움직임")
    # 분산 효과: 상관이 모두 1 이라면 VaR 은 종목별 VaR 의 가중합
    solo = sum(w[s] * -float(R[s].dropna().quantile(0.05)) for s in R.columns if R[s].notna().sum() >= 30)
    out["undiversified_var95"] = round(solo, 5)
    out["diversification_benefit"] = round(1 - out["var95"] / solo, 4) if solo > 0 else None
    # 유동성
    liq = []
    for s in w:
        if s not in bars or "volume" not in bars[s] or len(bars[s]) < 20:
            continue
        b = bars[s].iloc[-20:]
        adv = float((b["close"] * b["volume"]).mean())
        val = (values or {}).get(s)
        if adv > 0:
            item = {"symbol": s, "name": names.get(s, s), "adv": round(adv)}
            if val:
                item["days_to_liquidate"] = round(val / (0.10 * adv), 3)
                if item["days_to_liquidate"] > limits.max_days_to_liquidate:
                    out["warnings"].append(f"{names.get(s, s)}: 청산에 {item['days_to_liquidate']:.1f}일 (참여율 10%)")
            liq.append(item)
    out["liquidity"] = sorted(liq, key=lambda x: -x.get("days_to_liquidate", 0))
    lc = liquidation_cost(w, bars, values, impact_coef)
    out["liquidation_cost"] = lc
    out["lvar95"] = round(out["var95"] + lc["cost"], 5)
    # 통화
    cur: dict[str, float] = {}
    for s, x in w.items():
        cur[cur_map[s]] = cur.get(cur_map[s], 0.0) + x
    out["currency"] = {k: round(v, 4) for k, v in cur.items()}
    max_days = max((x.get("days_to_liquidate", 0) for x in liq), default=None)
    out["stress"] = stress_tests(w, bars, bench, beta, sectors, cur_map, out["var95"], solo, max_days)
    worst = max((x for x in out["stress"] if x.get("loss") is not None), key=lambda x: x["loss"], default=None)
    out["worst_stress"] = worst
    if worst and worst["loss"] > limits.max_stress_loss:
        out["warnings"].append(f"스트레스 '{worst['name']}' 손실 {worst['loss']:.0%} > 경고선 {limits.max_stress_loss:.0%}")
    if out["var95"] > limits.max_var95:
        out["warnings"].append(f"1일 VaR95 {out['var95']:.1%} > 한도 {limits.max_var95:.0%}")
    if (out["var"] or {}).get("fat_tail"):
        out["warnings"].append("꼬리가 두꺼움 — 정규분포 VaR 이 위험을 과소평가 (과거 시뮬레이션·CF 값을 보세요)")
    return out


def pretrade_check(weights: dict[str, float], symbol: str, add_weight: float, bars: dict[str, pd.DataFrame],
                   sectors: dict[str, str] | None = None, limits: PortfolioRiskLimits | None = None,
                   lookback: int = 250) -> dict:
    """주문 전 사전 게이트: symbol 을 add_weight 만큼 더 샀을 때 한도를 넘으면 넣을 수 있는 최대 비중을 돌려준다.

    한도: 업종 합산 · 같이 움직이는 묶음 합산 · 포트폴리오 VaR95. (종목 비중 · 총노출은 RiskEngine 이 이미 본다)"""
    limits = limits or PortfolioRiskLimits()
    base = {s: x for s, x in weights.items() if x > 0}
    reasons: list[str] = []
    allowed = max(add_weight, 0.0)
    sec = (sectors or {}).get(symbol)
    if sec:
        used = sum(x for s, x in base.items() if (sectors or {}).get(s) == sec)
        room = max(limits.max_sector_weight - used, 0.0)
        if allowed > room:
            allowed = room
            reasons.append(f"업종 {sec} 합산 {used:.0%} → 한도 {limits.max_sector_weight:.0%}")
    syms = list(dict.fromkeys([*base, symbol]))
    R = _returns(bars, syms, lookback)
    if symbol in R.columns and len(R) >= 30:
        c = R.corr(min_periods=30)
        mates = [s for s in base if s != symbol and s in c.columns and pd.notna(c.loc[s, symbol]) and c.loc[s, symbol] >= limits.cluster_corr]
        if mates:
            used = sum(base[s] for s in mates) + base.get(symbol, 0.0)
            room = max(limits.max_cluster_weight - used, 0.0)
            if allowed > room:
                allowed = room
                reasons.append(f"같이 움직이는 묶음 합산 {used:.0%} → 한도 {limits.max_cluster_weight:.0%}")

        def var_at(x: float) -> float:
            ww = dict(base)
            ww[symbol] = ww.get(symbol, 0.0) + x
            port = R.fillna(0.0) @ pd.Series({s: ww.get(s, 0.0) for s in R.columns})
            return -float(port.quantile(0.05))
        if allowed > 0 and var_at(allowed) > limits.max_var95:
            lo, hi = 0.0, allowed
            if var_at(0.0) > limits.max_var95:
                hi = 0.0
            else:
                for _ in range(20):
                    mid = (lo + hi) / 2
                    lo, hi = (mid, hi) if var_at(mid) <= limits.max_var95 else (lo, mid)
                hi = lo
            reasons.append(f"넣으면 1일 VaR95 {var_at(allowed):.1%} > 한도 {limits.max_var95:.0%}")
            allowed = hi
    return {"ok": allowed >= add_weight - 1e-9, "requested": round(add_weight, 5), "allowed": round(allowed, 5),
            "reasons": reasons}


def var_budget_scale(weights: dict[str, float], bars, limits: PortfolioRiskLimits | None = None,
                     lookback: int = 250) -> tuple[float, float | None]:
    """계획 비중의 VaR95 가 한도를 넘으면 전체를 몇 배로 줄여야 하는지 (1.0 = 그대로)."""
    limits = limits or PortfolioRiskLimits()
    w = {s: x for s, x in weights.items() if x > 0}
    R = _returns(bars, list(w), lookback)
    if R.empty or len(R) < 30:
        return 1.0, None
    port = R.fillna(0.0) @ pd.Series({s: w[s] for s in R.columns})
    var = -float(port.quantile(0.05))
    return (min(1.0, limits.max_var95 / var) if var > 0 else 1.0), var


def adv_values(bars: dict[str, pd.DataFrame], symbols, window: int = 20) -> dict[str, float]:
    """종목별 최근 20일 평균 거래대금 (원)."""
    out = {}
    for s in symbols:
        b = bars.get(s)
        if b is not None and "volume" in b and len(b) >= 5:
            t = b.iloc[-window:]
            v = float((t["close"] * t["volume"]).mean())
            if v > 0:
                out[s] = v
    return out


__all__ = ["PortfolioRiskLimits", "portfolio_risk", "pretrade_check", "var_budget_scale", "adv_values", "clusters",
           "concentration", "sector_exposure", "var_methods", "component_var", "stress_tests", "HISTORICAL_SCENARIOS"]

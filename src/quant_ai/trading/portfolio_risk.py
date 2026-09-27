"""포트폴리오 리스크 — 종목 한도만이 아니라 '묶음'으로 본 위험.

과거 시뮬레이션(최근 N거래일 실제 수익률 × 지금 비중)으로 계산한다 (분포 가정 없음).

- VaR95 / ES95 (1일): 평범한 나쁜 날 / 최악 5% 날의 평균 손실
- 변동성 · KOSPI 베타 · 최악의 날 · 시장 -10% 시나리오
- 상관관계 군집: 섹터 데이터 없이도 '같이 움직이는 종목 묶음'의 합산 비중 (섹터 집중 대용)
- 유동성: 20일 평균 거래대금 대비 보유 금액 → 참여율 10% 로 청산에 걸리는 일수
- 통화 노출: KRW / USD 비중 (해외 종목 보유 시 환율 위험)
- 위험 기여도: 손실 꼬리(최악 5% 날)에서 각 종목이 차지한 몫
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


def portfolio_risk(weights: dict[str, float], bars: dict[str, pd.DataFrame], bench: pd.DataFrame | None = None,
                   names: dict[str, str] | None = None, currencies: dict[str, str] | None = None,
                   values: dict[str, float] | None = None, lookback: int = 250,
                   limits: PortfolioRiskLimits | None = None) -> dict:
    """weights: 평가금액 대비 비중 {종목: w} (현금은 나머지). values: 보유 금액(유동성 계산용)."""
    limits = limits or PortfolioRiskLimits()
    names = names or {}
    w = {s: float(x) for s, x in weights.items() if x > 0}
    out: dict = {"n_positions": len(w), "gross": round(sum(w.values()), 4), "warnings": []}
    if not w:
        return out | {"var95": 0.0, "es95": 0.0, "vol": 0.0, "beta": 0.0}
    R = _returns(bars, list(w), lookback)
    missing = [s for s in w if s not in R.columns]
    if missing:
        out["warnings"].append(f"가격 이력 부족으로 제외: {', '.join(missing)}")
    if R.empty or len(R) < 30:
        return out | {"insufficient": True}
    wv = pd.Series({s: w[s] for s in R.columns})
    port = R.fillna(0.0) @ wv
    q = float(port.quantile(0.05))
    tail = port[port <= q]
    out |= {
        "days": int(len(port)),
        "var95": round(-q, 5), "es95": round(-float(tail.mean()), 5),
        "vol": round(float(port.std() * np.sqrt(252)), 4),
        "worst_day": round(float(port.min()), 5), "worst_day_date": str(port.idxmin().date()),
    }
    # 위험 기여도 (꼬리 손실 중 각 종목 몫)
    contrib = (R.loc[tail.index].fillna(0.0) * wv).mean()
    tot = float(contrib.sum()) or -1e-12
    out["risk_contrib"] = sorted(({"symbol": s, "name": names.get(s, s), "weight": round(w[s], 4),
                                   "share": round(float(contrib[s] / tot), 4)} for s in R.columns),
                                 key=lambda x: -x["share"])
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
    groups = [g for g in clusters(corr, limits.cluster_corr) if len(g) >= 2] if len(corr) >= 2 else []
    out["clusters"] = [{"members": [names.get(s, s) for s in g], "symbols": g,
                        "weight": round(sum(w[s] for s in g), 4)} for g in groups]
    for g in out["clusters"]:
        if g["weight"] > limits.max_cluster_weight:
            out["warnings"].append(f"같이 움직이는 묶음({', '.join(g['members'][:3])}…) 비중 {g['weight']:.0%} "
                                   f"> {limits.max_cluster_weight:.0%} — 사실상 한 종목처럼 움직임")
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
    # 통화
    cur: dict[str, float] = {}
    for s, x in w.items():
        c = (currencies or {}).get(s) or ("KRW" if s.isdigit() or s.endswith((".KS", ".KQ")) else "USD")
        cur[c] = cur.get(c, 0.0) + x
    out["currency"] = {k: round(v, 4) for k, v in cur.items()}
    if out["var95"] > limits.max_var95:
        out["warnings"].append(f"1일 VaR95 {out['var95']:.1%} > 한도 {limits.max_var95:.0%}")
    out["limits"] = limits.__dict__
    return out


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


__all__ = ["PortfolioRiskLimits", "portfolio_risk", "var_budget_scale", "adv_values", "clusters"]

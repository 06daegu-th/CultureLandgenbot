"""이벤트 영향 귀속 (Event impact attribution) — 이벤트가 실제로 가격을 얼마나 움직였고, 예측 오차의 몇 %를 설명하나.

1. 시장 이벤트 (FOMC · 옵션 만기 · 고용 …): 그날 지수 변동폭 ÷ 평소 변동폭 → "FOMC 날은 평소의 1.4배 흔들림"
2. 종목 이벤트 (실적 · 공시 · 주요 뉴스): 비정상 수익률 AR = 종목 − 베타 × 지수 (이벤트 전 120일로 베타 추정)
   당일 AR · [-1,+1] 누적 CAR · |AR| · t 값 → "공급계약 공시 뒤 평균 +1.8%, 우연 아님(t=2.6)"
3. 예측 오차 귀속: 채점된 예측의 보유 기간 안에 큰 이벤트가 있었나 → 이벤트 있던 예측 vs 없던 예측의 적중률,
   빗나간 예측 중 이벤트가 끼어 있던 비율
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..engines.events import FOMC_DATES, kr_options_expiry, us_options_expiry


def _pos(idx: pd.DatetimeIndex, d: date) -> int | None:
    ts = pd.Timestamp(d).tz_localize(idx.tz) if idx.tz is not None else pd.Timestamp(d)
    i = int(idx.searchsorted(ts))
    return i if i < len(idx) else None


def abnormal(bars: pd.DataFrame, bench: pd.DataFrame | None, d: date, est: int = 120, gap: int = 10) -> dict | None:
    r = bars["close"].pct_change(fill_method=None)
    i = _pos(r.index, d)
    if i is None or i < est + gap or i + 1 >= len(r):
        return None
    beta, rm = 1.0, None
    if bench is not None and len(bench) > est:
        rm = bench["close"].pct_change(fill_method=None).reindex(r.index)
        a, b = r.iloc[i - est - gap:i - gap], rm.iloc[i - est - gap:i - gap]
        ok = a.notna() & b.notna()
        if ok.sum() > 30 and b[ok].var() > 0:
            beta = float(np.cov(a[ok], b[ok])[0, 1] / b[ok].var())
    def ar(j):
        m = float(rm.iloc[j]) if rm is not None and pd.notna(rm.iloc[j]) else 0.0
        return float(r.iloc[j]) - beta * m
    sd = float((r.iloc[i - est - gap:i - gap] - (beta * rm.iloc[i - est - gap:i - gap] if rm is not None else 0)).std())
    ar0 = ar(i)
    car = sum(ar(j) for j in range(i - 1, min(i + 2, len(r))))
    return {"ar0": ar0, "car": car, "sd": sd, "z0": ar0 / sd if sd > 0 else None, "beta": beta}


def _summ(xs: list[float]) -> dict:
    a = np.array([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if len(a) == 0:
        return {"n": 0}
    t = float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if len(a) > 2 and a.std(ddof=1) > 0 else None
    return {"n": int(len(a)), "mean": round(float(a.mean()), 5), "abs_mean": round(float(np.abs(a).mean()), 5),
            "pos_share": round(float((a > 0).mean()), 3), "t": None if t is None else round(t, 2)}


def past_market_events(start: date, end: date) -> list[dict]:
    out = [{"date": date.fromisoformat(s), "kind": "fomc", "market": "US"} for s in FOMC_DATES]
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        q = m in (3, 6, 9, 12)
        out.append({"date": kr_options_expiry(y, m), "kind": "quad_witching" if q else "options_expiry", "market": "KR"})
        out.append({"date": us_options_expiry(y, m), "kind": "quad_witching" if q else "options_expiry", "market": "US"})
        first = date(y, m, 1)
        out.append({"date": first + timedelta(days=(4 - first.weekday()) % 7), "kind": "nfp", "market": "US"})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return [e for e in out if start <= e["date"] <= end]


def market_impact(bench: pd.DataFrame | None, events: list[dict], market: str = "KR") -> list[dict]:
    """이벤트 날 지수 |수익률| ÷ 평소(이벤트 없는 날) |수익률| 중앙값. 미국 이벤트는 한국 다음 날에 반영."""
    if bench is None or len(bench) < 60:
        return []
    r = bench["close"].pct_change(fill_method=None).dropna()
    idx = r.index
    by = defaultdict(list)
    hit = set()
    for e in events:
        i = _pos(idx, e["date"] + (timedelta(days=1) if e["market"] == "US" and market == "KR" else timedelta(0)))
        if i is not None and i < len(r):
            by[e["kind"]].append(float(r.iloc[i]))
            hit.add(i)
    normal = np.abs(np.array([v for j, v in enumerate(r.to_numpy()) if j not in hit]))
    base = float(np.median(normal)) if len(normal) else None
    out = []
    for k, xs in by.items():
        a = np.abs(np.array(xs))
        out.append({"kind": k, "n": len(xs), "abs_median": round(float(np.median(a)), 5),
                    "vs_normal": round(float(np.median(a)) / base, 2) if base else None,
                    "mean": round(float(np.mean(xs)), 5), "worst": round(float(np.min(xs)), 5)})
    return sorted(out, key=lambda x: -(x["vs_normal"] or 0))


def stock_impact(bars: dict[str, pd.DataFrame], bench: pd.DataFrame | None, events: list[dict]) -> list[dict]:
    """events: [{symbol, date, kind}] — 종류별 비정상 수익률 요약."""
    by = defaultdict(lambda: {"ar0": [], "car": [], "z": [], "examples": []})
    for e in events:
        b = bars.get(e["symbol"])
        if b is None or len(b) < 150:
            continue
        a = abnormal(b, bench, e["date"])
        if a is None:
            continue
        g = by[e["kind"]]
        g["ar0"].append(a["ar0"])
        g["car"].append(a["car"])
        if a["z0"] is not None:
            g["z"].append(abs(a["z0"]))
        if len(g["examples"]) < 3 or abs(a["car"]) > min(abs(x["car"]) for x in g["examples"]):
            g["examples"] = sorted([*g["examples"], {"symbol": e["symbol"], "date": str(e["date"]), "title": e.get("title"),
                                                      "car": round(a["car"], 4)}], key=lambda x: -abs(x["car"]))[:3]
    out = []
    for k, g in by.items():
        s = _summ(g["car"])
        out.append({"kind": k, **s, "ar0": _summ(g["ar0"]).get("mean"), "abs_z": round(float(np.mean(g["z"])), 2) if g["z"] else None,
                    "significant": bool(s.get("t") is not None and abs(s["t"]) >= 2), "examples": g["examples"]})
    return sorted(out, key=lambda x: -x.get("n", 0))


def prediction_attribution(items: list[dict], events: list[dict], horizon: int = 5) -> dict:
    """items: [{symbol, as_of(date), hit(bool)}] · events: [{date, kind, symbol|None, market}] 중요 이벤트만."""
    by_sym = defaultdict(list)
    mkt = []
    for e in events:
        (by_sym[e["symbol"]] if e.get("symbol") else mkt).append(e)
    with_ev, without, kinds = [], [], defaultdict(lambda: [0, 0])
    for x in items:
        d0 = x["as_of"]
        d1 = d0 + timedelta(days=int(horizon * 1.5) + 1)
        kr = x["symbol"][:1].isdigit()
        hits = [e for e in by_sym.get(x["symbol"], []) if d0 < e["date"] <= d1]
        hits += [e for e in mkt if d0 < e["date"] <= d1 and (e["market"] == ("KR" if kr else "US") or e["kind"] == "fomc")]
        (with_ev if hits else without).append(x["hit"])
        for k in {e["kind"] for e in hits}:
            kinds[k][0] += 1
            kinds[k][1] += int(x["hit"])
    misses = [x for x in items if not x["hit"]]
    miss_with = sum(1 for x, h in zip(items, [bool(v) for v in _flags(items, by_sym, mkt, horizon)]) if not x["hit"] and h)
    return {"n": len(items), "with_event": {"n": len(with_ev), "hit_rate": float(np.mean(with_ev)) if with_ev else None},
            "without_event": {"n": len(without), "hit_rate": float(np.mean(without)) if without else None},
            "miss_share_with_event": round(miss_with / len(misses), 3) if misses else None,
            "by_kind": sorted(({"kind": k, "n": v[0], "hit_rate": v[1] / v[0]} for k, v in kinds.items() if v[0]), key=lambda r: -r["n"])}


def _flags(items, by_sym, mkt, horizon):
    for x in items:
        d0 = x["as_of"]
        d1 = d0 + timedelta(days=int(horizon * 1.5) + 1)
        kr = x["symbol"][:1].isdigit()
        yield any(d0 < e["date"] <= d1 for e in by_sym.get(x["symbol"], [])) or any(
            d0 < e["date"] <= d1 and (e["market"] == ("KR" if kr else "US") or e["kind"] == "fomc") for e in mkt)


__all__ = ["abnormal", "market_impact", "stock_impact", "prediction_attribution", "past_market_events"]

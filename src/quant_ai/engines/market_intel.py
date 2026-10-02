"""시장 인텔리전스: 뉴스 이벤트 묶기 · 시장 상태(RISK ON/OFF) · 크로스에셋 민감도.

1. 뉴스 클러스터링 — 같은 사건을 다룬 기사 30개를 호재 30개로 세지 않는다 → '30 News → 1 Event'.
   제목 문자 2-gram 자카드 유사도로 48시간 안의 기사를 묶는다 (한국어 형태소 분석기 없이 동작).
2. 시장 상태 — 추세 · 시장 폭(breadth) · 모멘텀 · 변동성 · VIX · 낙폭을 0~100 점수로 합쳐
   RISK ON / NEUTRAL / RISK OFF 와 국면 유형(TREND · SIDEWAYS · HIGH VOL)을 판별.
3. 크로스에셋 — 종목(또는 KOSPI)이 NASDAQ · VIX · 달러 · 미 10년물 · 유가 · 원/달러에 얼마나 민감한지
   (60일 상관·베타). 미국 지표는 하루 늦게 반영(미국 장 마감 → 다음 날 한국 장).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ 1. 뉴스 이벤트 클러스터링
_STRIP = re.compile(r"\[[^\]]*\]|\([^)]*\)|[^\w가-힣]+")


def _grams(title: str) -> set[str]:
    t = _STRIP.sub("", title or "").lower()
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def cluster_news(articles: list[dict], window_hours: float = 48, threshold: float = 0.45) -> list[dict]:
    """articles: [{ts, title, sentiment, importance, events, source, symbols?}] → 이벤트 목록 (최신순).

    이벤트 감성은 기사 수로 부풀리지 않고 평균, 중요도는 최대값, 기사 수는 '관심도'로만 표시."""
    def ts_of(a):
        t = a.get("ts") or a.get("published_at")
        return pd.Timestamp(t) if t is not None else pd.Timestamp(0, tz="UTC")
    items = sorted(articles, key=ts_of)
    events: list[dict] = []
    for a in items:
        g, t = _grams(a.get("title", "")), ts_of(a)
        best, best_sim = None, threshold
        for e in events:
            if (t - e["_last"]).total_seconds() > window_hours * 3600:
                continue
            sim = max(_jaccard(g, x) for x in e["_grams"])
            if sim >= best_sim:
                best, best_sim = e, sim
        if best is None:
            events.append({"_grams": [g], "_last": t, "_items": [a], "first_ts": str(t)})
        else:
            best["_grams"].append(g)
            best["_last"] = max(best["_last"], t)
            best["_items"].append(a)
    out = []
    for e in events:
        its = e["_items"]
        rep = max(its, key=lambda x: (x.get("importance") or 0, -len(x.get("title", ""))))
        sents = [x["sentiment"] for x in its if x.get("sentiment") is not None]
        out.append({
            "title": rep.get("title", ""), "ts": str(e["_last"]), "first_ts": e["first_ts"],
            "n_articles": len(its), "sources": sorted({x.get("source") or "" for x in its} - {""}),
            "sentiment": round(float(np.mean(sents)), 3) if sents else None,
            "importance": max((x.get("importance") or 0 for x in its), default=0),
            "events": sorted({ev for x in its for ev in (x.get("events") or [])}),
            "symbols": sorted({s for x in its for s in (x.get("symbols") or [])}),
            "category": news_category(rep.get("title", ""), sorted({ev for x in its for ev in (x.get("events") or [])})),
        })
    return sorted(out, key=lambda e: e["ts"], reverse=True)


CATEGORY_RULES = (
    ("정책", ("금리", "기준금리", "FOMC", "연준", "Fed", "한국은행", "정책", "규제", "정부", "관세")),
    ("실적", ("실적", "영업이익", "매출", "어닝", "컨센서스", "분기")),
    ("공시", ("공시", "유상증자", "자사주", "배당", "합병", "분할", "상장")),
    ("원자재", ("유가", "원유", "WTI", "금값", "구리", "원자재")),
    ("환율", ("환율", "달러", "원/달러", "엔화")),
    ("기업", ("반도체", "AI", "HBM", "수주", "계약", "출시", "투자")),
    ("거시", ("GDP", "CPI", "물가", "고용", "경기", "소비", "수출")),
)


def news_category(title: str, events: list[str] | None = None) -> str:
    for label, keys in CATEGORY_RULES:
        if any(k.lower() in (title or "").lower() for k in keys):
            return label
    if events:
        return "이벤트"
    return "시장"


# ------------------------------------------------------------------ 2. 시장 상태
def _clip01(x: float) -> float:
    return float(min(max(x, 0.0), 1.0))


def market_state(bench: pd.DataFrame | None, bars: dict[str, pd.DataFrame] | None = None,
                 vix: pd.Series | None = None) -> dict:
    """0~100 점수 (높을수록 위험 선호 환경). 각 요소는 그 시점까지의 데이터만 사용."""
    if bench is None or len(bench) < 60:
        return {"score": 50, "label": "NEUTRAL", "type": "UNKNOWN", "components": [], "insufficient": True}
    c = bench["close"].astype(float)
    comps = []
    ma50, ma200 = c.rolling(50).mean().iloc[-1], c.rolling(200, min_periods=60).mean().iloc[-1]
    trend = float(c.iloc[-1] / ma200 - 1)
    comps.append(("추세 (지수 vs 200일선)", _clip01(0.5 + trend / 0.10), f"{trend:+.1%}"))
    comps.append(("단기 추세 (50일선)", 1.0 if c.iloc[-1] > ma50 else 0.0,
                  "위" if c.iloc[-1] > ma50 else "아래"))
    r20 = float(c.iloc[-1] / c.iloc[-21] - 1)
    comps.append(("20일 모멘텀", _clip01(0.5 + r20 / 0.12), f"{r20:+.1%}"))
    lr = np.log(c).diff()
    vol = lr.rolling(20).std()
    vol_pct = float(vol.rolling(252, min_periods=60).rank(pct=True).iloc[-1])
    comps.append(("변동성 (1년 분위)", _clip01(1 - vol_pct), f"{vol.iloc[-1] * np.sqrt(252):.0%}"))
    dd = float(c.iloc[-1] / c.iloc[-252:].max() - 1)
    comps.append(("52주 고점 대비", _clip01(1 + dd / 0.25), f"{dd:+.1%}"))
    if bars:
        above = [float(b["close"].iloc[-1] > b["close"].iloc[-50:].mean()) for s, b in bars.items()
                 if s != "KOSPI" and len(b) >= 50]
        if len(above) >= 10:
            br = float(np.mean(above))
            comps.append(("시장 폭 (50일선 위 종목)", br, f"{br:.0%}"))
    if vix is not None and len(vix.dropna()) >= 5:
        v = vix.dropna()
        lvl = float(v.iloc[-1])
        comps.append(("VIX", _clip01((32 - lvl) / 20), f"{lvl:.1f} ({float(v.iloc[-1] - v.iloc[-6]) if len(v) > 5 else 0:+.1f})"))
    score = round(100 * float(np.mean([x[1] for x in comps])))
    label = "RISK ON" if score >= 60 else "RISK OFF" if score <= 40 else "NEUTRAL"
    slope = float(c.rolling(50).mean().pct_change(10).iloc[-1])
    if vol_pct >= 0.8:
        kind = "HIGH VOL"
    elif abs(trend) > 0.03 and np.sign(trend) == np.sign(slope):
        kind = "TREND ↑" if trend > 0 else "TREND ↓"
    else:
        kind = "SIDEWAYS"
    return {"score": score, "label": label, "type": kind, "as_of": str(c.index[-1].date()),
            "components": [{"name": n, "score": round(s, 3), "value": v} for n, s, v in comps]}


# ------------------------------------------------------------------ 3. 크로스에셋
FACTORS = {  # series_id: (표시 이름, 변화 방식, 미국 지표 → 하루 지연)
    "KOSPI": ("KOSPI", "ret", False),
    "SPY": ("S&P 500 (SPY)", "ret", False),
    "NASDAQCOM": ("NASDAQ", "ret", True),
    "SP500": ("S&P 500", "ret", True),
    "VIXCLS": ("VIX", "diff", True),
    "DTWEXBGS": ("달러지수", "ret", True),
    "DGS10": ("미국채 10년", "diff", True),
    "DCOILWTICO": ("WTI 유가", "ret", True),
    "DEXKOUS": ("원/달러", "ret", True),
}


def _changes(s: pd.Series, how: str) -> pd.Series:
    s = s.astype(float).sort_index()
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize() if pd.DatetimeIndex(s.index).tz is not None \
        else pd.DatetimeIndex(s.index).normalize()
    s = s[~s.index.duplicated(keep="last")]
    return s.pct_change(fill_method=None) if how == "ret" else s.diff()


def cross_asset(target: pd.Series, factors: dict[str, pd.Series], window: int = 60, lag_us: bool = True) -> list[dict]:
    """target: 종목(또는 지수) 종가. factors: {series_id: 값 시계열}. → 요인별 상관·베타·최근 변화.

    lag_us=False: 대상이 미국 종목이면 미국 지표를 같은 날로 맞춘다 (국내 종목일 때만 하루 지연)."""
    y = _changes(target, "ret").dropna()
    out = []
    for sid, series in factors.items():
        if series is None or len(series.dropna()) < 20:
            continue
        name, how, lag = FACTORS.get(sid, (sid, "ret", False))
        x = _changes(series.dropna(), how)
        if lag and lag_us:  # 미국 d 일 종가 → 한국 d+1 일에 반영
            x.index = x.index + pd.tseries.offsets.BDay(1)
        both = pd.concat([y, x], axis=1, join="inner").dropna().iloc[-window:]
        if len(both) < 20:
            continue
        a, b = both.iloc[:, 0], both.iloc[:, 1]
        corr = float(a.corr(b))
        beta = float(np.cov(a, b)[0, 1] / b.var()) if b.var() > 0 else 0.0
        lvl = series.dropna()
        out.append({"id": sid, "name": name, "corr": round(corr, 3), "beta": round(beta, 4), "n": len(both),
                    "last": round(float(lvl.iloc[-1]), 4),
                    "chg_5d": round(float(lvl.iloc[-1] / lvl.iloc[-6] - 1) if how == "ret" and len(lvl) > 5
                                    else float(lvl.iloc[-1] - lvl.iloc[-6]) if len(lvl) > 5 else 0.0, 4),
                    "unit": "%" if how == "ret" else "pt"})
    return sorted(out, key=lambda r: -abs(r["corr"]))


def load_macro(session, series_ids, days: int = 400, as_of: datetime | None = None) -> dict[str, pd.Series]:
    from sqlalchemy import select

    from ..data.models import MacroObservation
    end = (as_of or datetime.now()).date()
    since = end - timedelta(days=days)
    out: dict[str, pd.Series] = {}
    for sid in series_ids:
        rows = session.execute(select(MacroObservation.ts, MacroObservation.value).where(
            MacroObservation.series_id == sid, MacroObservation.ts >= since, MacroObservation.ts <= end)
            .order_by(MacroObservation.ts)).all()
        if rows:
            out[sid] = pd.Series([v for _, v in rows], index=pd.DatetimeIndex([t for t, _ in rows]))
    return out


__all__ = ["cluster_news", "news_category", "market_state", "cross_asset", "load_macro", "FACTORS"]

"""P0-4 기준선 — '그냥 지수 ETF 를 샀다면' 과 비교해 이 시스템을 쓸 이유가 돈으로 있는지 확인한다.

두 가지를 나란히 보여준다.
  ① 16년 연구 (2012~2026 KRX · 비용·세금 반영) — 코어 vs KOSPI (docs/RESEARCH_KRX.md 의 숫자를 그대로)
  ② 실제 운용 장부 vs 'ETF 그림자 장부' — 첫 평가일에 같은 돈으로 지수를 사고, 입금이 있을 때마다 같은 날 같은 돈을
     지수에 더 넣었다고 가정한 장부. 같은 돈 · 같은 날짜 · 같은 입출금이라 차이가 곧 '이 시스템이 만든 차이'다.

정직하게: 지수는 배당 제외 가격지수다. 실제 ETF 는 배당(연 1.5~2%)을 받으므로 이 비교는 코어에 유리하게 기운다.
그래서 코어가 이 기준선조차 못 이기면 'ETF 적립이 더 낫다' 고 그대로 말한다.
"""

from __future__ import annotations

import math

import pandas as pd
from sqlalchemy import select

from .asof import label

ETF_FEE = 0.0015  # 연 보수 (KODEX 200 수준)
ETF_TRADE = 0.0002  # 매수 1회 수수료·스프레드
RESEARCH = {
    "period": "2012~2026 (16년)",
    "core": {"name": "코어 전략", "cagr": 0.076, "vol": 0.157, "sharpe": 0.54, "mdd": -0.434},
    "index": {"name": "KOSPI (지수 ETF 대용)", "cagr": 0.083, "vol": 0.214, "sharpe": 0.48, "mdd": -0.441},
    "source": "docs/RESEARCH_KRX.md 8장 — 연도별 세율·비용 반영 백테스트",
}


def etf_shadow(equity: pd.Series, flows: list[dict], bench_close: pd.Series) -> pd.Series:
    """같은 돈 · 같은 입출금으로 지수를 샀다면의 평가금액 (일별, 장부 날짜에 맞춤)."""
    e = equity.copy()
    e.index = pd.DatetimeIndex(e.index).tz_localize(None) if pd.DatetimeIndex(e.index).tz is not None else pd.DatetimeIndex(e.index)
    e = e.groupby(e.index.normalize()).last()
    b = bench_close.copy()
    b.index = pd.DatetimeIndex(b.index).tz_localize(None) if pd.DatetimeIndex(b.index).tz is not None else pd.DatetimeIndex(b.index)
    b = b.groupby(b.index.normalize()).last().sort_index()
    px = b.reindex(b.index.union(e.index)).ffill().reindex(e.index)
    if px.isna().all():
        return pd.Series(dtype=float)
    px = px.bfill()
    add = pd.Series(0.0, index=e.index)
    for x in flows:
        pos = e.index.searchsorted(pd.Timestamp(x["date"]).normalize())
        if pos < len(e):
            add.iloc[pos] += float(x["amount"])
    units, out, prev = 0.0, [], None
    for t in e.index:
        if prev is not None:
            units *= (1 - ETF_FEE) ** ((t - prev).days / 365)  # 보수는 매일 조금씩
        if units == 0:
            units = float(e.iloc[0]) * (1 - ETF_TRADE) / float(px.loc[t])
        elif add.loc[t]:
            a = float(add.loc[t])
            units += a * (1 - ETF_TRADE if a > 0 else 1) / float(px.loc[t])
        out.append(units * float(px.loc[t]))
        prev = t
    return pd.Series(out, index=e.index)


def _cagr(total: float, days: int) -> float | None:
    if days < 60 or total <= -1:
        return None
    return (1 + total) ** (365 / days) - 1


def compare(app, mode: str = "paper") -> dict:
    from .data.db import session_scope
    from .data.models import PortfolioSnapshot
    from .pipeline import time_weighted_index
    with session_scope(app.engine) as s:
        snaps = s.execute(select(PortfolioSnapshot.ts, PortfolioSnapshot.equity).where(PortfolioSnapshot.mode == mode)
                          .order_by(PortfolioSnapshot.ts, PortfolioSnapshot.id)).all()
    research = {**RESEARCH, "verdict": "16년 동안 수익률은 지수가 조금 높았고(연 8.3% vs 7.6%), 코어는 흔들림이 작았다 (변동성 15.7% vs 21.4%). "
                                       "'지수보다 더 번다' 는 증거는 없다 — 덜 흔들리는 대신 덜 버는 전략."}
    out = {"mode": mode, "research": research, "live": None}
    if len(snaps) < 2:
        out["live"] = {"status": "insufficient", "text": "운용 장부가 아직 없습니다 — 모의투자를 시작하면 다음 날부터 ETF 와 비교합니다"}
        out["recommend"] = _recommend(None)
        return out
    eq = pd.Series([x.equity for x in snaps], index=pd.DatetimeIndex([x.ts for x in snaps]), dtype=float)
    _, bench, _ = app.market_data()
    if bench is None or not len(bench):
        out["live"] = {"status": "insufficient", "text": "지수 데이터가 없어 비교할 수 없습니다 — ./run.sh data 로 데이터를 받아 주세요"}
        out["recommend"] = _recommend(None)
        return out
    flows = app.cashflows(mode)
    etf = etf_shadow(eq, flows, bench["close"])
    mine = eq.groupby(pd.DatetimeIndex(eq.index).tz_localize(None).normalize() if eq.index.tz is not None else eq.index.normalize()).last()
    mine = mine.reindex(etf.index)
    tw_mine = time_weighted_index(eq, flows).reindex(etf.index).ffill()
    tw_etf = time_weighted_index(etf, flows)
    days = int((etf.index[-1] - etf.index[0]).days)
    r_mine, r_etf = float(tw_mine.iloc[-1] / tw_mine.iloc[0] - 1), float(tw_etf.iloc[-1] / tw_etf.iloc[0] - 1)
    gap = float(mine.iloc[-1] - etf.iloc[-1])
    dd = lambda s: float((s / s.cummax() - 1).min())  # noqa: E731
    # 일별 차이로 우연인지 (t) — 표본이 짧으면 판정 안 함
    d = (tw_mine.pct_change() - tw_etf.pct_change()).dropna()
    t = float(d.mean() / d.std() * math.sqrt(len(d))) if len(d) > 5 and d.std() > 0 else None
    status = "insufficient" if len(d) < 60 else ("ahead" if gap > 0 else "behind")
    live = {"status": status, "days": len(etf), "from": label(etf.index[0].to_pydatetime(), with_time=False),
            "to": label(etf.index[-1].to_pydatetime(), with_time=False),
            "mine": round(float(mine.iloc[-1])), "etf": round(float(etf.iloc[-1])), "gap": round(gap),
            "ret_mine": round(r_mine, 4), "ret_etf": round(r_etf, 4), "excess": round(r_mine - r_etf, 4),
            "cagr_mine": _cagr(r_mine, days), "cagr_etf": _cagr(r_etf, days),
            "mdd_mine": round(dd(tw_mine), 4), "mdd_etf": round(dd(tw_etf), 4), "t": None if t is None else round(t, 2),
            "flows": len(flows),
            "curve": [[str(i.date()), round(float(a)), round(float(b))] for i, a, b in zip(etf.index, mine, etf, strict=True)][-400:]}
    live["text"] = (f"{live['from']}부터 {len(etf)}일: 이 시스템 {r_mine:+.1%} · 지수 ETF 였다면 {r_etf:+.1%} → "
                    f"{'ETF 보다 ' + format(abs(gap), ',.0f') + '원 더 있음' if gap >= 0 else 'ETF 보다 ' + format(abs(gap), ',.0f') + '원 적음'}")
    if status == "insufficient":
        live["text"] += f" (60거래일 전에는 판정하지 않음 — 지금 {len(d)}일)"
    elif t is not None and abs(t) < 2:
        live["text"] += " · 차이는 아직 우연과 구분되지 않음"
    out["live"] = live
    out["recommend"] = _recommend(live)
    return out


def _recommend(live: dict | None) -> dict:
    """무엇을 하면 좋은가 — 결과를 보고 기준을 바꾸지 않도록 규칙을 고정한다."""
    if live is None or live["status"] == "insufficient":
        return {"level": "info", "text": "판단 보류 — 코어와 ETF 적립 모두 '목표 계획'에서 확률로 비교해 볼 수 있습니다. "
                                         "16년 연구로는 둘의 수익은 비슷하고 코어가 덜 흔들렸습니다."}
    if live["status"] == "behind" and (live["t"] or 0) <= -2:
        return {"level": "bad", "text": "코어가 지수 ETF 보다 의미 있게 뒤처져 있습니다 — 적립금은 ETF 로 돌리고, 코어 비중을 줄이는 것을 권합니다."}
    if live["status"] == "behind":
        return {"level": "warn", "text": "코어가 ETF 보다 뒤처져 있지만 아직 우연과 구분되지 않습니다 — 계속 비교하되, 3개월 더 뒤처지면 ETF 비중을 늘리세요."}
    return {"level": "good", "text": "코어가 ETF 보다 앞서 있습니다 — 다만 짧은 기간의 우위는 운일 수 있으니 계속 비교합니다."}


__all__ = ["compare", "etf_shadow", "RESEARCH"]

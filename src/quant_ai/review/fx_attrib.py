"""원화 환산 성과 — 미국 장부의 달러 수익을 원화로 바꾸고, 환율 효과를 떼어 본다.

원화 수익 = (1 + 달러 수익) × (1 + 환율 변화) − 1
         = 주식 몫(달러 수익) + 환율 몫(원/달러 변화) + 교차 몫
환율: FRED DEXKOUS (원/달러, 영업일). 장부 날짜에 환율이 없으면 직전 값을 쓴다.
"달러로는 +8% 였지만 원화 강세로 원화 기준 +2%" 같은 차이를 보여 준다. 환전 비용은 포함하지 않는다.
"""

from __future__ import annotations

import pandas as pd


def attribute(equity_usd: pd.Series, usdkrw: pd.Series) -> dict:
    e = equity_usd.dropna()
    fx = usdkrw.dropna()
    if len(e) < 2 or len(fx) < 2:
        return {"insufficient": True, "message": "미국 장부 기록 또는 원/달러 환율(FRED_API_KEY)이 부족합니다"}
    e.index = pd.to_datetime(e.index, utc=True).normalize()
    e = e.groupby(level=0).last()
    fx.index = pd.to_datetime(fx.index, utc=True).normalize()
    f = fx.reindex(e.index.union(fx.index)).sort_index().ffill().reindex(e.index)
    ok = f.notna()
    e, f = e[ok], f[ok]
    if len(e) < 2:
        return {"insufficient": True, "message": "장부 기간과 겹치는 환율이 없습니다"}
    r_usd = float(e.iloc[-1] / e.iloc[0] - 1)
    r_fx = float(f.iloc[-1] / f.iloc[0] - 1)
    r_krw = (1 + r_usd) * (1 + r_fx) - 1
    krw = e * f
    daily = pd.DataFrame({"usd": e.pct_change(), "fx": f.pct_change()}).dropna()
    curve = [{"date": str(t.date()), "usd": round(float(e.loc[t] / e.iloc[0] - 1), 5),
              "krw": round(float(krw.loc[t] / krw.iloc[0] - 1), 5), "fx": round(float(f.loc[t] / f.iloc[0] - 1), 5)}
             for t in e.index]
    return {"start": str(e.index[0].date()), "end": str(e.index[-1].date()), "days": int(len(e)),
            "usd_return": round(r_usd, 5), "fx_return": round(r_fx, 5), "krw_return": round(r_krw, 5),
            "parts": {"stock": round(r_usd, 5), "fx": round(r_fx, 5), "cross": round(r_usd * r_fx, 5)},
            "fx_start": round(float(f.iloc[0]), 2), "fx_end": round(float(f.iloc[-1]), 2),
            "equity_krw_end": round(float(krw.iloc[-1])), "corr_usd_fx": round(float(daily["usd"].corr(daily["fx"])), 3) if len(daily) > 10 else None,
            "curve": curve[-250:],
            "read": (f"달러 기준 {r_usd:+.1%} · 환율 {r_fx:+.1%} → 원화 기준 {r_krw:+.1%}"
                     + (" (환율이 수익을 깎음)" if r_fx < -0.01 else " (환율이 수익을 보탬)" if r_fx > 0.01 else ""))}


def for_app(app, book: str | None = None) -> dict:
    from sqlalchemy import select

    from ..data.db import session_scope
    from ..data.models import PortfolioSnapshot
    from ..engines.market_intel import load_macro
    from ..global_market import MAIN
    book = book or MAIN
    with session_scope(app.engine) as s:
        rows = s.execute(select(PortfolioSnapshot.ts, PortfolioSnapshot.equity).where(PortfolioSnapshot.mode == book)
                         .order_by(PortfolioSnapshot.ts)).all()
        fx = load_macro(s, ["DEXKOUS"], days=1500).get("DEXKOUS")
    if not rows:
        return {"insufficient": True, "book": book, "message": f"{book} 장부 기록이 없습니다"}
    eq = pd.Series([v for _, v in rows], index=pd.DatetimeIndex([t for t, _ in rows]))
    if fx is None:
        return {"insufficient": True, "book": book, "message": "원/달러 환율이 없습니다 (FRED_API_KEY 필요)"}
    return attribute(eq, fx) | {"book": book}


__all__ = ["attribute", "for_app"]

"""실제 결과 자동 매칭 — 예측 하나마다 1 · 5 · 20 거래일 뒤 결과와 시장 대비 초과수익을 붙인다.

정의는 채점과 같다: 신호는 기준 봉 종가 이후에만 알 수 있으므로 다음 봉 시가에 들어가 h 봉 뒤 종가에 나온다.
초과수익 = 종목 수익 − 같은 구간 벤치마크(국내 KOSPI 대용, 미국 SPY) 수익.
기간이 다 지난 것만 채운다 (중간 값을 넣지 않는다). 채점(correct)은 예측 기간(보통 5일) 기준 그대로.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select

from ..data.models import ConsensusRecord

HORIZONS = (1, 5, 20)


def forward(bars: pd.DataFrame | None, as_of, h: int) -> float | None:
    """as_of 이하 마지막 봉 t → (t+h 종가) / (t+1 시가) − 1. 아직 기간이 안 끝났으면 None."""
    if bars is None or bars.empty:
        return None
    ts = pd.Timestamp(as_of)
    idx = bars.index
    if idx.tz is not None:
        ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert(idx.tz)
    elif ts.tz is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    pos = int(idx.searchsorted(ts, side="right")) - 1
    if pos < 0 or pos + h >= len(bars) or pos + 1 >= len(bars):
        return None
    opn = bars["open"].iloc[pos + 1] if "open" in bars else bars["close"].iloc[pos + 1]
    exit_ = bars["close"].iloc[pos + h]
    if not opn or pd.isna(opn) or pd.isna(exit_):
        return None
    return float(exit_ / opn - 1)


def match(session, bars: dict[str, pd.DataFrame], bench: pd.DataFrame | None, max_rows: int = 5000) -> int:
    """아직 결과가 다 붙지 않은 예측에 결과를 붙인다. 붙인 개수."""
    n = 0
    rows = session.scalars(select(ConsensusRecord).order_by(ConsensusRecord.id.desc()).limit(max_rows)).all()
    for r in rows:
        p = dict(r.payload or {})
        out = dict(p.get("outcomes") or {})
        if all(str(h) in out for h in HORIZONS) and "excess" in out:
            continue
        b = bars.get(r.symbol)
        if b is None:
            continue
        changed = False
        for h in HORIZONS:
            if str(h) in out:
                continue
            v = forward(b, r.as_of, h)
            if v is not None:
                out[str(h)] = round(v, 6)
                changed = True
        h = int(p.get("horizon") or 5)
        if "excess" not in out and str(h) in out and bench is not None:
            bm = forward(bench, r.as_of, h)
            if bm is not None:
                out["excess"] = round(out[str(h)] - bm, 6)
                out["bench"] = round(bm, 6)
                changed = True
        if changed:
            p["outcomes"] = out
            r.payload = p
            n += 1
    return n


__all__ = ["forward", "match", "HORIZONS"]

"""옵션 데이터 (미국 종목) — 시장이 가격에 매긴 '예상 변동폭'과 쏠림.

Yahoo 옵션 체인(키 불필요)에서
  · ATM 내재변동성(IV) · 가장 가까운 만기 · 스트래들 가격 → 만기까지 예상 변동폭 (±%)
  · 실적 발표 직후 만기가 있으면 '실적 내재 변동폭' (이벤트 위험·실적 모델이 쓴다)
  · 풋/콜 비율 (거래량 · 미결제약정) · 스큐 (5% 외가 풋 IV − 5% 외가 콜 IV: 클수록 하락 대비 수요)
  · 맥스 페인 (옵션 매도자 손실이 가장 작은 가격)
국내(KOSPI200 옵션) 종목별 데이터는 무료 공개 소스가 없어 제공하지 않는다 — 화면에 그렇게 표시한다.
6시간 캐시 (ops 'options:<종목>'), 실패하면 12시간 쉼.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from ... import ops

log = logging.getLogger(__name__)
TTL = timedelta(hours=6)
FAIL_TTL = timedelta(hours=12)


def fetch_yahoo(symbol: str, max_expiries: int = 4) -> dict:
    import yfinance as yf
    t = yf.Ticker(symbol)
    exps = list(t.options or [])[:max_expiries]
    if not exps:
        raise RuntimeError("옵션 만기 없음")
    spot = float(t.fast_info["last_price"])
    chains = {}
    for e in exps:
        oc = t.option_chain(e)
        chains[e] = {"calls": oc.calls, "puts": oc.puts}
    return {"spot": spot, "chains": chains}


def _iv_at(df: pd.DataFrame, k: float) -> float | None:
    if df is None or df.empty or "impliedVolatility" not in df:
        return None
    d = df.dropna(subset=["impliedVolatility"])
    d = d[d["impliedVolatility"] > 0.01]
    if d.empty:
        return None
    row = d.iloc[(d["strike"] - k).abs().argsort()[:1]]
    return float(row["impliedVolatility"].iloc[0])


def _mid(df: pd.DataFrame, k: float) -> float | None:
    if df is None or df.empty:
        return None
    row = df.iloc[(df["strike"] - k).abs().argsort()[:1]].iloc[0]
    b, a, last = row.get("bid"), row.get("ask"), row.get("lastPrice")
    if b and a and a > 0 and b > 0:
        return float((b + a) / 2)
    return float(last) if last and last > 0 else None


def max_pain(calls: pd.DataFrame, puts: pd.DataFrame) -> float | None:
    if calls.empty and puts.empty:
        return None
    strikes = sorted(set(calls["strike"]).union(puts["strike"]))
    coi = dict(zip(calls["strike"], calls.get("openInterest", pd.Series(0, index=calls.index)).fillna(0)))
    poi = dict(zip(puts["strike"], puts.get("openInterest", pd.Series(0, index=puts.index)).fillna(0)))
    best, val = None, math.inf
    for s in strikes:
        pain = sum(max(0.0, s - k) * oi for k, oi in coi.items()) + sum(max(0.0, k - s) * oi for k, oi in poi.items())
        if pain < val:
            best, val = s, pain
    return float(best) if best is not None else None


def summarize(data: dict, earnings: date | None = None, today: date | None = None) -> dict:
    spot = float(data["spot"])
    today = today or datetime.now(UTC).date()
    rows = []
    for e, ch in data["chains"].items():
        c, p = ch["calls"], ch["puts"]
        ed = date.fromisoformat(e)
        dte = max((ed - today).days, 1)
        iv = [x for x in (_iv_at(c, spot), _iv_at(p, spot)) if x]
        straddle = [x for x in (_mid(c, spot), _mid(p, spot)) if x]
        move = sum(straddle) / spot if len(straddle) == 2 else None
        ivp, ivc = _iv_at(p, spot * 0.95), _iv_at(c, spot * 1.05)
        vol_c = float(c.get("volume", pd.Series(dtype=float)).fillna(0).sum())
        vol_p = float(p.get("volume", pd.Series(dtype=float)).fillna(0).sum())
        oi_c = float(c.get("openInterest", pd.Series(dtype=float)).fillna(0).sum())
        oi_p = float(p.get("openInterest", pd.Series(dtype=float)).fillna(0).sum())
        rows.append({"expiry": e, "dte": dte, "atm_iv": round(sum(iv) / len(iv), 4) if iv else None,
                     "implied_move": None if move is None else round(move, 4),
                     "skew_5pct": None if not (ivp and ivc) else round(ivp - ivc, 4),
                     "pc_volume": round(vol_p / vol_c, 3) if vol_c else None,
                     "pc_oi": round(oi_p / oi_c, 3) if oi_c else None, "max_pain": max_pain(c, p)})
    near = rows[0] if rows else {}
    out = {"spot": spot, "expiries": rows, "atm_iv": near.get("atm_iv"), "implied_move": near.get("implied_move"),
           "skew": near.get("skew_5pct"), "pc_oi": near.get("pc_oi"), "pc_volume": near.get("pc_volume"),
           "max_pain": near.get("max_pain")}
    if earnings:
        after = [r for r in rows if date.fromisoformat(r["expiry"]) >= earnings]
        if after and after[0].get("implied_move"):
            r0 = after[0]
            # 평소 변동(ATM IV)으로 설명되는 몫을 빼고 남는 것 = 실적 하루 몫 (분산 차감)
            base = (r0["atm_iv"] or 0) * math.sqrt(max(r0["dte"] - 1, 0) / 365) * 0.8
            ev = math.sqrt(max(r0["implied_move"] ** 2 - base ** 2, 0)) if r0["atm_iv"] else r0["implied_move"]
            out["earnings_expiry"] = r0["expiry"]
            out["earnings_implied_move"] = round(ev, 4)
    sk = out.get("skew")
    pc = out.get("pc_oi")
    out["read"] = " · ".join(x for x in (
        f"시장 예상 변동 ±{out['implied_move']:.1%} ({near.get('expiry')})" if out.get("implied_move") else None,
        "하락 대비 수요 큼 (스큐↑)" if sk is not None and sk > 0.05 else None,
        "풋 쏠림" if pc is not None and pc > 1.3 else "콜 쏠림" if pc is not None and pc < 0.6 else None) if x)
    return out


def get(engine, symbol: str, earnings: date | None = None, fetch=None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    if symbol[:1].isdigit():
        return {"available": False, "message": "국내 종목별 옵션은 무료 공개 데이터가 없습니다"}
    key = f"options:{symbol}"
    st = ops.get_state(engine, key)
    if st.get("at") and now - datetime.fromisoformat(st["at"]) < (FAIL_TTL if st.get("error") else TTL):
        return st
    try:
        data = (fetch or fetch_yahoo)(symbol)
        out = {"available": True, "at": now.isoformat(), **summarize(data, earnings, now.date())}
    except Exception as e:  # noqa: BLE001
        out = {"available": False, "at": now.isoformat(), "error": f"{type(e).__name__}: {str(e)[:120]}",
               "message": "옵션 데이터를 받지 못했습니다 (12시간 뒤 재시도)"}
    ops.set_state(engine, key, out)
    return out


__all__ = ["get", "summarize", "fetch_yahoo", "max_pain"]

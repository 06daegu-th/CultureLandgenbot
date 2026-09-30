"""실적 서프라이즈 모델 — 다가오는 실적 발표에서 무엇을 기대할 수 있나.

재료
  · 과거 실적: 예상 EPS · 실제 EPS · 서프라이즈(%) (Yahoo earnings_dates, 미국 · 일부 국내)
  · 과거 반응: 발표 다음 날 비정상 수익률(베타 조정) · 발표 후 2~20일 표류(PEAD) · 발표 전 20일 선행 움직임
  · 옵션 내재 변동폭 (있으면)
추정
  · P(상회) = 베타-이항 사후 평균: 사전(미국 Beta(7,3) ≈ 70% · 국내 Beta(5,5)) + 이 종목의 상회 이력
  · 기대 반응 = P(상회) × 상회 때 평균 반응 + (1−P) × 하회 때 평균 반응
  · 예상 크기 = 옵션 내재 변동 → 없으면 과거 |반응| 중앙값
표본이 적으면 사전 분포 쪽으로 당겨진다 — 과신하지 않는다. 방향 예측이 아니라 '크기와 비대칭'을 보는 용도.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from .. import ops

PRIOR = {"US": (7.0, 3.0), "KR": (5.0, 5.0)}
TTL = timedelta(hours=24)


def fetch_yahoo(symbol: str) -> list[dict]:
    import yfinance as yf
    df = yf.Ticker(symbol).get_earnings_dates(limit=16)
    out = []
    if df is None or df.empty:
        return out
    for ts, r in df.iterrows():
        est, rep = r.get("EPS Estimate"), r.get("Reported EPS")
        sur = r.get("Surprise(%)")
        out.append({"date": pd.Timestamp(ts).date().isoformat(),
                    "eps_est": None if pd.isna(est) else float(est), "eps": None if pd.isna(rep) else float(rep),
                    "surprise_pct": None if pd.isna(sur) else float(sur)})
    return out


def reactions(bars: pd.DataFrame | None, bench: pd.DataFrame | None, day: date) -> dict | None:
    if bars is None or len(bars) < 60:
        return None
    c = bars["close"].astype(float)
    idx = c.index
    ts = pd.Timestamp(day).tz_localize(idx.tz) if idx.tz is not None else pd.Timestamp(day)
    i = int(idx.searchsorted(ts))
    # 발표 시각을 모르므로 발표일 종가 → 다음 날 종가 (장 마감 후 발표가 흔함) + 발표일 당일 합산
    if i < 21 or i + 21 >= len(c):
        return None
    rb = None
    if bench is not None:
        b = bench["close"].astype(float).reindex(idx).ffill()
        rb = b
    def ar(a, z):
        r = c.iloc[z] / c.iloc[a] - 1
        if rb is not None and pd.notna(rb.iloc[a]) and pd.notna(rb.iloc[z]) and rb.iloc[a] > 0:
            r -= rb.iloc[z] / rb.iloc[a] - 1
        return float(r)
    return {"reaction": ar(i - 1, min(i + 1, len(c) - 1)), "pre20": ar(i - 21, i - 1), "drift": ar(i + 1, min(i + 20, len(c) - 1))}


def model(history: list[dict], bars: pd.DataFrame | None, bench: pd.DataFrame | None, market: str = "US",
          next_date: date | None = None, implied_move: float | None = None, today: date | None = None) -> dict:
    today = today or datetime.now(UTC).date()
    past = [h for h in history if h.get("date") and date.fromisoformat(h["date"]) < today]
    beats = [h for h in past if h.get("surprise_pct") is not None]
    k = sum(1 for h in beats if h["surprise_pct"] > 0)
    a0, b0 = PRIOR.get(market, PRIOR["US"])
    p_beat = (a0 + k) / (a0 + b0 + len(beats))
    rx = []
    for h in past:
        r = reactions(bars, bench, date.fromisoformat(h["date"]))
        if r:
            rx.append({**h, **r})
    beat_r = [x["reaction"] for x in rx if (x.get("surprise_pct") or 0) > 0]
    miss_r = [x["reaction"] for x in rx if x.get("surprise_pct") is not None and x["surprise_pct"] <= 0]
    all_r = [x["reaction"] for x in rx]
    mb = float(np.mean(beat_r)) if beat_r else None
    mm = float(np.mean(miss_r)) if miss_r else None
    exp_r = p_beat * mb + (1 - p_beat) * mm if mb is not None and mm is not None else None
    size = implied_move or (float(np.median(np.abs(all_r))) if all_r else None)
    pead = [x["drift"] * np.sign(x.get("surprise_pct") or 0) for x in rx if x.get("surprise_pct")]
    upcoming = next_date or next((date.fromisoformat(h["date"]) for h in sorted(history, key=lambda h: h["date"])
                                  if date.fromisoformat(h["date"]) >= today), None)
    return {
        "n_history": len(beats), "beats": k, "p_beat": round(p_beat, 3), "prior": f"Beta({a0:.0f},{b0:.0f})",
        "avg_surprise_pct": round(float(np.mean([h["surprise_pct"] for h in beats])), 2) if beats else None,
        "reaction_on_beat": None if mb is None else round(mb, 4), "reaction_on_miss": None if mm is None else round(mm, 4),
        "expected_reaction": None if exp_r is None else round(exp_r, 4),
        "expected_move": None if size is None else round(size, 4), "move_source": "옵션 내재" if implied_move else "과거 반응 중앙값" if size else None,
        "pead": round(float(np.mean(pead)), 4) if pead else None,
        "pre_run": round(float(np.mean([x["pre20"] for x in rx])), 4) if rx else None,
        "asymmetry": ("하회 때 낙폭이 상회 때 상승보다 큼" if mb is not None and mm is not None and abs(mm) > abs(mb) * 1.3 else
                      "상회 때 반응이 더 큼" if mb is not None and mm is not None and abs(mb) > abs(mm) * 1.3 else "대칭"),
        "next_date": upcoming.isoformat() if upcoming else None,
        "history": [{k2: x.get(k2) for k2 in ("date", "eps_est", "eps", "surprise_pct", "reaction", "drift")} for x in rx][-8:],
        "confidence": "낮음 (이력 4건 미만)" if len(beats) < 4 else "보통" if len(beats) < 10 else "높음",
    }


def get(engine, symbol: str, bars, bench, next_date: date | None = None, implied_move: float | None = None,
        fetch=None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    key = f"earnings_model:{symbol}"
    st = ops.get_state(engine, key)
    hist = st.get("history_raw")
    if not st.get("at") or now - datetime.fromisoformat(st["at"]) > TTL:
        try:
            hist = (fetch or fetch_yahoo)(symbol if not symbol[:1].isdigit() else f"{symbol}.KS")
        except Exception as e:  # noqa: BLE001
            hist = hist or []
            st["error"] = f"{type(e).__name__}"
        st = {"at": now.isoformat(), "history_raw": hist}
        ops.set_state(engine, key, st)
    out = model(hist or [], bars, bench, "KR" if symbol[:1].isdigit() else "US", next_date, implied_move, now.date())
    return out | {"at": st["at"]}


__all__ = ["model", "reactions", "get", "fetch_yahoo", "PRIOR"]

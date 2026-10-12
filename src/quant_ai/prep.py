"""v40 실적 발표 대비 — '언제'에서 끝나지 않고 '얼마나 움직일 수 있고, 내 돈으로는 얼마인가'까지.

- 발표 시각: 종목 일정(Nasdaq 캘린더 · Yahoo · 국내 공시) → 한국시간
- 예상 변동: 옵션 시장이 매긴 실적 변동(있으면) → 없으면 과거 발표 다음 날 움직임 중앙값 → 그것도 없으면 평소 이틀 변동(참고)
- 과거 반응: 발표 전날 종가 → 다음 날 종가 (주가 그대로 · 시장 대비 둘 다) · 상회 비율
- 내 돈: 보유 평가금액 × 예상 변동 (미국 종목은 원화로 환산)
네트워크를 부르지 않는다 — 저장된 일정·옵션·실적 이력·일봉만 쓴다 (화면이 느려지지 않게).
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from . import ops


def _bars(app, symbol: str):
    if symbol[:1].isdigit():
        bars, benches = app._all_bars()
        return bars.get(symbol), benches.get("KR")
    try:
        from .desk import _us_data
        bars, bench, _ = _us_data(app)
        return bars.get(symbol), bench
    except Exception:  # noqa: BLE001
        return None, None


def _past(app, symbol: str) -> list[dict]:
    """저장된 실적 이력 (발표일 · 서프라이즈). 국내는 컨센서스 스냅샷 이력도."""
    st = ops.get_state(app.engine, f"earnings_model:{symbol}") or {}
    hist = list(st.get("history_raw") or [])
    if not hist:
        prof = (ops.get_state(app.engine, f"profile:{symbol}").get("data") or {})
        hist = [{"date": h["date"], "surprise_pct": h.get("surprise_pct")} for h in prof.get("earnings_history") or []]
    if symbol[:1].isdigit():
        try:
            from .data.collectors.kr_consensus import history_for_model
            seen = {h.get("date") for h in hist}
            hist += [h for h in history_for_model(app.engine, symbol) if h.get("date") not in seen]
        except Exception:  # noqa: BLE001, S110
            pass
    return sorted(hist, key=lambda h: h.get("date") or "")


def reactions(bars: pd.DataFrame | None, bench: pd.DataFrame | None, days: list[str]) -> list[dict]:
    """발표일마다 전날 종가 → 다음 날 종가 (그대로 · 시장 대비). 발표 시각을 모르므로 이틀을 묶는다."""
    if bars is None or len(bars) < 30:
        return []
    c = bars["close"].astype(float)
    idx = pd.DatetimeIndex(c.index)
    b = bench["close"].astype(float).reindex(idx).ffill() if bench is not None and len(bench) else None
    out = []
    for d in days:
        try:
            ts = pd.Timestamp(d)
        except ValueError:
            continue
        ts = ts.tz_localize(idx.tz) if idx.tz is not None and ts.tzinfo is None else ts
        i = int(idx.searchsorted(ts))
        if i < 1 or i + 1 >= len(c) or abs((idx[min(i, len(idx) - 1)] - ts).days) > 4:
            continue
        raw = float(c.iloc[i + 1] / c.iloc[i - 1] - 1)
        rel = None
        if b is not None and pd.notna(b.iloc[i - 1]) and pd.notna(b.iloc[i + 1]) and b.iloc[i - 1] > 0:
            rel = raw - float(b.iloc[i + 1] / b.iloc[i - 1] - 1)
        out.append({"date": str(d)[:10], "move": round(raw, 4), "vs_market": None if rel is None else round(rel, 4)})
    return out


def earnings_prep(app, symbol: str, now: datetime | None = None, days: int = 45) -> dict:
    from .radar import kst_text, when
    from .stock import events
    now = now or datetime.now(UTC)
    symbol = symbol.strip().upper() if not symbol.strip()[:1].isdigit() else symbol.strip()
    nxt = next((e for e in events(app, symbol, now, days=days) if e["kind"] == "earnings" and e["scope"] == "종목" and e["d_day"] >= 0), None)
    out: dict = {"symbol": symbol, "next": None}
    if nxt:
        at, approx = when(nxt | {"symbol": symbol, "market": "KR" if symbol[:1].isdigit() else "US"})
        out["next"] = {"date": nxt["date"], "d_label": nxt["d_label"], "d_day": nxt["d_day"], "time": nxt.get("time"),
                       "estimated": bool(nxt.get("estimated")), "kst": kst_text(at, now, approx, nxt["date"]), "at": at.isoformat() if at else None,
                       "eps_estimate": nxt.get("eps_estimate")}
    bars, bench = _bars(app, symbol)
    hist = [h for h in _past(app, symbol) if h.get("date") and h["date"] < now.date().isoformat()][-12:]
    past = reactions(bars, bench, [h["date"] for h in hist])
    sur = {h["date"]: h.get("surprise_pct") for h in hist}
    for p in past:
        p["surprise_pct"] = sur.get(p["date"])
    past = past[-8:]
    beats = [p for p in past if p.get("surprise_pct") is not None]
    out["past"] = past
    out["beat_rate"] = round(sum(p["surprise_pct"] > 0 for p in beats) / len(beats), 3) if beats else None
    moves = [abs(p["move"]) for p in past]
    opt = (ops.get_state(app.engine, f"options:{symbol}") or {})
    implied = opt.get("earnings_implied_move")
    daily = None
    if bars is not None and len(bars) > 25:
        daily = float(bars["close"].astype(float).pct_change().iloc[-60:].std() * np.sqrt(2))
    if implied:
        exp, src = float(implied), "옵션 시장이 매긴 실적 변동"
    elif len(moves) >= 3:
        exp, src = float(np.median(moves)), f"과거 {len(moves)}번 발표 다음 날 움직임 중앙값"
    elif daily:
        exp, src = daily, "평소 이틀 변동 (실적 이력이 없어 참고값)"
    else:
        exp, src = None, None
    out["expected_move"], out["move_source"] = (None if exp is None else round(exp, 4)), src
    out["normal_2d"] = None if daily is None else round(daily, 4)
    out["max_past"] = max(past, key=lambda p: abs(p["move"])) if past else None
    # 내 돈으로
    try:
        from .stockplus import position
        pos = position(app, symbol)
        val = float(pos.get("value") or 0)
    except Exception:  # noqa: BLE001
        pos, val = {}, 0.0
    fx = 1.0
    if val and not symbol[:1].isdigit():
        from .usorder import fx_rate
        fx, _ = fx_rate(app)
    out["holding"] = {"qty": pos.get("total_qty"), "value": round(val, 2), "value_krw": round(val * fx)} if val else None
    out["exposure_krw"] = round(val * fx * exp) if val and exp else None
    lines = []
    n = out["next"]
    if n:
        lines.append(f"발표 {n['d_label']} · {n['kst']}" + (f" ({n['time']})" if n.get("time") else "") + (" · 추정 일정" if n["estimated"] else ""))
    else:
        lines.append(f"앞으로 {days}일 안의 실적 발표 일정이 아직 없어요")
    if exp:
        lines.append(f"예상 변동 ±{exp:.1%} — {src}")
    if past:
        mx = out["max_past"]
        lines.append(f"과거 {len(past)}번 다음 날: 평균 ±{np.mean(moves):.1%} · 가장 크게 {mx['move']:+.1%} ({mx['date']})"
                     + (f" · 예상 상회 {out['beat_rate']:.0%}" if out["beat_rate"] is not None else ""))
    if out["exposure_krw"]:
        lines.append(f"내 보유 기준 하루에 ±약 {out['exposure_krw']:,.0f}원 움직일 수 있어요")
    out["lines"] = lines
    out["note"] = "변동 크기는 과거·옵션 기준 참고값이에요 — 방향(오를지 내릴지)은 알려 주지 않아요"
    out["as_of"] = now.isoformat()
    return out


def market_move(app, kind: str, market: str | None = None) -> dict | None:
    """CPI·FOMC 같은 날 시장이 평소보다 얼마나 움직였나 (event_impact 저장값)."""
    ei = ops.get_state(app.engine, "event_impact") or {}
    for key, lab in (("market_us", "미국 지수") if market == "US" else ("market_kr", "코스피"), ("market_kr", "코스피")):
        x = next((r for r in ei.get(key) or [] if r.get("kind") == kind), None)
        if x and x.get("n", 0) >= 3:
            return {"index": lab, "n": x["n"], "vs_normal": x.get("vs_normal"), "abs_median": x.get("abs_median"), "worst": x.get("worst"),
                    "text": f"{lab} 보통의 {x['vs_normal']:.1f}배 (하루 ±{x['abs_median']:.1%} · 과거 {x['n']}번)" if x.get("vs_normal") else None}
    return None


__all__ = ["earnings_prep", "market_move", "reactions"]

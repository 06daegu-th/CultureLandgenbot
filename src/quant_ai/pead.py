"""실적 이벤트 전략 — 발표 후 표류(PEAD)를 '전진 기록'으로 검증한다 (AI 종목 추천보다 학술 근거가 있는 편).

규칙 (사전 등록 · 코드에 고정 · 바꾸면 새 버전으로 다시 시작)
  · 실적 서프라이즈(예상 대비 실제, EPS 우선 · 없으면 영업이익) ≥ +5% → +1 (사기) · ≤ −5% → −1 (피하기/팔기) · 사이 → 신호 없음
  · 진입: 발표일 다음 거래일 종가 · 청산: 그 뒤 20거래일 · 성과 = 종목 − 지수 (초과수익)
기록
  · 발표가 확인되면 즉시 장부에 '봉인'(해시) — 결과가 나오기 전에 적힌 신호만 '전진(forward)'으로 센다
  · 이미 지나간 발표를 나중에 채운 것은 '사후(backfill)' — 참고로만 따로 보여준다 (가설을 만든 데이터라 판정에 안 씀)
판정 (전진 30건 이상)
  · 방향을 곱한 초과수익의 평균 > 0 이고 t ≥ 2 → '유효 후보' (그래도 소액 위성에서 6개월 이상 더) · 아니면 '근거 없음'
실제 주문에는 쓰지 않는다 — 기록·채점만. 위성 전략으로 쓰려면 이 판정 + 검증 사다리를 통과해야 한다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd

from . import ops
from .asof import label

KEY = "pead_ledger"
VERSION = "pead-v1"
THRESH = 5.0  # % 서프라이즈
HOLD = 20
MIN_FORWARD = 30


def _seal(e: dict) -> str:
    core = {k: e[k] for k in ("id", "symbol", "date", "surprise", "direction", "registered_at", "version")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _surprises(app) -> list[dict]:
    """종목별 실적 서프라이즈 (국내 컨센서스 스냅샷 + Yahoo 실적 이력) — % 단위."""
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import SystemState
    out = []
    with session_scope(app.engine) as s:
        rows = s.execute(select(SystemState.key, SystemState.value).where(
            SystemState.key.like("krcons:%") | SystemState.key.like("profile:%"))).all()
    for key, val in rows:
        sym = key.split(":", 1)[1]
        v = val or {}
        if key.startswith("krcons:"):
            for x in v.get("surprises") or []:
                sp = x.get("eps_surprise_pct") if x.get("eps_surprise_pct") is not None else x.get("surprise_pct")
                if x.get("date") and sp is not None:
                    out.append({"symbol": sym, "date": str(x["date"])[:10], "surprise": float(sp), "metric": "EPS" if x.get("eps_surprise_pct") is not None else (x.get("metric") or "영업이익"),
                                "source": "네이버 컨센서스"})
        else:
            for h in (v.get("data") or {}).get("earnings_history") or []:
                if h.get("date") and h.get("surprise_pct") is not None:
                    out.append({"symbol": sym, "date": str(h["date"])[:10], "surprise": float(h["surprise_pct"]), "metric": "EPS", "source": "Yahoo"})
    return out


def _forward(symbol: str, d: str, now: datetime) -> bool:
    from .clock import MARKETS
    cal = MARKETS["KRX" if symbol[:1].isdigit() else "US"]
    entry = cal.next_trading_day(date.fromisoformat(d))
    return now.astimezone(UTC).date() <= entry


def scan(app, now: datetime | None = None) -> dict:
    """새 실적 서프라이즈를 장부에 봉인 (중복 없이)."""
    now = now or datetime.now(UTC)
    led = ops.get_state(app.engine, KEY)
    rows = led.get("rows") or []
    have = {r["id"] for r in rows}
    added = 0
    for x in _surprises(app):
        eid = f"{x['symbol']}:{x['date']}"
        if eid in have:
            continue
        d = 1 if x["surprise"] >= THRESH else -1 if x["surprise"] <= -THRESH else 0
        e = {"id": eid, "symbol": x["symbol"], "date": x["date"], "surprise": round(x["surprise"], 2), "metric": x["metric"], "source": x["source"],
             "direction": d, "registered_at": now.isoformat(), "version": VERSION,
             # 진입(발표 다음 거래일 종가) 전에 기록됐으면 전진 — 결과를 모르는 상태에서 적힌 신호만
             "forward": _forward(x["symbol"], x["date"], now)}
        e["hash"] = _seal(e)
        rows.append(e)
        have.add(eid)
        added += 1
    ops.set_state(app.engine, KEY, {"rows": rows[-5000:], "version": VERSION, "rule": rule_text()})
    return {"added": added, "total": len(rows)}


def rule_text() -> str:
    return (f"서프라이즈 ≥ +{THRESH:.0f}% → +1, ≤ −{THRESH:.0f}% → −1 · 발표 다음 거래일 종가 진입 · {HOLD}거래일 보유 · "
            f"성과 = 종목 − 지수 · 전진 {MIN_FORWARD}건부터 판정")


def _outcome(b: pd.DataFrame, bench: pd.DataFrame | None, d: date) -> dict | None:
    idx = pd.DatetimeIndex(b.index)
    t = pd.Timestamp(d)
    t = t.tz_localize(idx.tz) if idx.tz is not None else t
    i = int(idx.searchsorted(t, side="right"))  # 발표일 다음 거래일
    if i + HOLD >= len(b):
        return None
    r = float(b["close"].iloc[i + HOLD] / b["close"].iloc[i] - 1)
    br = 0.0
    if bench is not None and len(bench):
        bi = bench["close"].reindex(idx).ffill()
        if pd.notna(bi.iloc[i]) and pd.notna(bi.iloc[i + HOLD]) and bi.iloc[i]:
            br = float(bi.iloc[i + HOLD] / bi.iloc[i] - 1)
    return {"entry": str(pd.Timestamp(idx[i]).date()), "ret": round(r, 4), "bench": round(br, 4), "excess": round(r - br, 4)}


def report(app, now: datetime | None = None) -> dict:
    scan(app, now)
    led = ops.get_state(app.engine, KEY)
    rows = led.get("rows") or []
    bars, benches = app._all_bars()
    scored = []
    for e in rows:
        b = bars.get(e["symbol"])
        if b is None or not e["direction"]:
            continue
        o = _outcome(b, benches.get("KR" if e["symbol"][:1].isdigit() else "US"), date.fromisoformat(e["date"]))
        if o:
            scored.append(e | o | {"signed": round(e["direction"] * o["excess"], 4), "intact": _seal(e) == e.get("hash")})

    def stats(xs):
        if not xs:
            return {"n": 0}
        v = np.array([x["signed"] for x in xs])
        t = float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 2 and v.std(ddof=1) > 0 else None
        return {"n": len(v), "mean_signed_excess": round(float(v.mean()), 4), "hit": round(float((v > 0).mean()), 3), "t": None if t is None else round(t, 2),
                "long_n": sum(1 for x in xs if x["direction"] > 0), "long_mean": round(float(np.mean([x["excess"] for x in xs if x["direction"] > 0])), 4) if any(x["direction"] > 0 for x in xs) else None}
    fwd = stats([x for x in scored if x["forward"]])
    back = stats([x for x in scored if not x["forward"]])
    if fwd["n"] >= MIN_FORWARD:
        decision = "유효 후보 (소액 위성에서 6개월 이상 더 기록)" if fwd["mean_signed_excess"] > 0 and (fwd["t"] or 0) >= 2 else "근거 없음 — 쓰지 않음"
    else:
        decision = f"전진 기록 중 ({fwd['n']}/{MIN_FORWARD}건)"
    pending = [e for e in rows if e["direction"] and not any(s["id"] == e["id"] for s in scored)]
    return {"rule": rule_text(), "version": VERSION, "decision": decision, "forward": fwd, "backfill": back,
            "n_events": len(rows), "n_signals": sum(1 for e in rows if e["direction"]), "tampered": sum(1 for s in scored if not s["intact"]),
            "recent": sorted(scored, key=lambda x: x["date"], reverse=True)[:20], "pending": sorted(pending, key=lambda e: e["date"], reverse=True)[:10],
            "as_of": label(datetime.now(UTC)),
            "note": "주문에는 쓰지 않음 (기록·채점만) · 사후(backfill)는 참고용 · 실적 데이터는 무료 소스라 늦거나 빠질 수 있음"}


__all__ = ["scan", "report", "rule_text", "VERSION"]

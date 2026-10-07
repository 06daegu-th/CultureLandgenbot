"""AI 성적표: 각 AI 의 의견을 저장하고, horizon 이 지나면 실제 결과로 채점한다.

카테고리별로 따로 채점한다.
- direction : prob_up 방향 vs 종목 실제 수익률
- news      : 뉴스 영향 부호 vs 종목 실제 수익률 (뉴스가 있던 경우만)
- macro     : 거시 영향 부호 vs 벤치마크(시장) 실제 수익률
- trend     : 추세 점수 부호 vs 종목 실제 수익률
- risk      : 경고(severity ≥ 0.3 또는 veto)를 낸 경우만, 실제로 하락/급변했는지
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysts.base import DIRECTION, MACRO, NEWS, RISK, TREND, Opinion
from ..data.models import AnalystOpinionRecord, ConsensusRecord
from ..engines.features import forward_return
from .engine import ConsensusSignal, TrackRecord

MIN_SIGNAL = 0.05  # 이보다 약한 점수는 '의견 없음'으로 보고 채점하지 않음
RISK_WARN_LEVEL = 0.3


def evidence_snapshot(ctx) -> dict:
    """Evidence Chain 용: 이 판단을 내릴 때 AI 들이 본 재료를 요약해 합의 기록에 같이 저장한다.
    (나중에 뉴스·지표가 바뀌어도 '그때 무엇을 보고 샀는가' 를 그대로 재현)"""
    keys = ("last_close", "ret_1", "ret_5", "ret_20", "rsi_14", "vol_20", "ma_20_gap", "ma_60_gap", "vol_ratio",
            "jump_sigma", "dist_52w")
    return {
        "price": {k: v for k, v in (ctx.price or {}).items() if k in keys or k == "last_bar"},
        "regime": ctx.regime, "market": {k: ctx.market_state.get(k) for k in ("label", "score", "type")} if ctx.market_state else {},
        "news": [{k: e.get(k) for k in ("title", "ts", "n_articles", "sentiment", "importance", "sources", "category")}
                 for e in (ctx.news or [])[:6]],
        "disclosures": [{k: d.get(k) for k in ("date", "title", "sentiment", "events")} for d in (ctx.disclosures or [])[:5]],
        "macro": ctx.macro, "cross_asset": (ctx.cross_asset or [])[:4],
        "events": [{k: e.get(k) for k in ("ts", "name", "importance")} for e in (ctx.upcoming_events or [])[:5]],
        "data_quality": ctx.data_quality,
        **({"community": {k: ctx.community.get(k) for k in ("source", "posts", "bullish", "bearish", "mood", "label")}}
           if getattr(ctx, "community", None) else {}),
        **({"sector": ctx.sector} if getattr(ctx, "sector", None) else {}),
        **({"flow": ctx.flow} if getattr(ctx, "flow", None) else {}),
        **({"signal": {k: ctx.signal.get(k) for k in ("score", "tier", "bin")}} if getattr(ctx, "signal", None) else {}),
        **({"related": ctx.related[:5]} if getattr(ctx, "related", None) else {}),
        **({"agents": {"news_for_this_stock": (ctx.market_agents or {}).get("news_for_this_stock")}}
           if (getattr(ctx, "market_agents", None) or {}).get("news_for_this_stock") else {}),
    }


def save_consensus(session: Session, sig: ConsensusSignal, opinions: list[Opinion], as_of: datetime,
                   horizon: int, regime: str | None, evidence: dict | None = None,
                   versions: dict | None = None, plan: dict | None = None) -> ConsensusRecord:
    from ..review.scorecard import expected_move
    sigma = ((evidence or {}).get("price") or {}).get("vol_20")
    exp_h, exp_1d = expected_move(sig.prob_up, sigma, horizon)  # 확률 → 크기 (예측 성적표에서 실제와 비교)
    rec = ConsensusRecord(symbol=sig.symbol, as_of=as_of, action=sig.action, prob_up=sig.prob_up,
                          confidence=sig.confidence, conflict=sig.conflict,
                          payload={**sig.to_dict(), "regime": regime, "horizon": horizon,
                                   **({"expected_return": round(exp_h, 5), "expected_1d": round(exp_1d, 5)}
                                      if exp_h is not None else {}),
                                   **({"versions": versions} if versions else {}),
                                   **({"plan": _json_safe(plan)} if plan else {}),
                                   **({"evidence": _json_safe(evidence)} if evidence else {})})
    from ..review.ledger import seal
    seal(rec)  # 예측 장부: 저장 시각 + 내용 해시
    session.add(rec)
    session.flush()
    cal = {c.analyst: c.prob_up for c in sig.contributions if c.prob_raw is not None}
    for op in opinions:
        base = dict(consensus_id=rec.id, analyst=op.analyst, symbol=op.symbol, as_of=as_of, horizon_bars=horizon,
                    confidence=op.confidence, veto=op.veto)
        payload = {"reasons": op.reasons, "risks": op.risks, "summary": op.summary, "backend": op.backend,
                   "veto_reason": op.veto_reason, "error": op.error, "regime": regime, **op.meta,
                   **({"prob_cal": round(cal[op.analyst], 4)} if op.analyst in cal else {})}
        if op.prob_up is not None:
            session.add(AnalystOpinionRecord(category=DIRECTION, prob_up=op.prob_up, payload=payload, **base))
        for cat, score in op.sub_scores.items():
            # 위험 경고는 '경고를 냈을 때'만 채점 (조용히 있던 날까지 적중으로 세면 성적이 부풀려짐)
            if cat == RISK and not (op.veto or -score >= RISK_WARN_LEVEL):
                continue
            if cat == RISK or abs(score) >= MIN_SIGNAL:
                session.add(AnalystOpinionRecord(category=cat, prob_up=0.5 + 0.5 * score,
                                                 payload={**payload, "score": score}, **base))
    return rec


def _json_safe(obj):
    """JSON 컬럼에 넣을 수 있게: NaN/inf → None, numpy → 파이썬, 그 외 → 문자열."""
    import math
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    return str(obj)


def resolve(session: Session, bars_by_symbol: dict[str, pd.DataFrame], benchmark: pd.DataFrame) -> int:
    """아직 채점 안 된 의견 중 결과가 확정된 것을 채점."""
    fwd_cache: dict[tuple[str, int], pd.Series] = {}

    def fwd(symbol: str, h: int) -> pd.Series | None:
        key = (symbol, h)
        if key not in fwd_cache:
            b = benchmark if symbol == "__benchmark__" else bars_by_symbol.get(symbol)
            if b is None:
                return None
            if "open" not in b:
                b = b.assign(open=b["close"])
            fwd_cache[key] = forward_return(b, h)
        return fwd_cache[key]

    def realized(symbol: str, as_of, h: int) -> float | None:
        s = fwd(symbol, h)
        if s is None:
            return None
        ts = pd.Timestamp(as_of)
        ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
        s = s[s.index <= ts]
        if s.empty or pd.isna(s.iloc[-1]):
            return None
        return float(s.iloc[-1])

    n = 0
    pending = session.scalars(select(AnalystOpinionRecord).where(AnalystOpinionRecord.correct.is_(None))).all()
    for op in pending:
        target = "__benchmark__" if op.category == MACRO else op.symbol
        r = realized(target, op.as_of, op.horizon_bars)
        if r is None:
            continue
        op.realized_return = r
        if op.category == RISK:
            # 경고 후 실제 하락했거나 5% 이상 급변했으면 적중
            op.correct = r < 0 or abs(r) >= 0.05
        else:
            op.correct = (op.prob_up >= 0.5) == (r > 0)
        n += 1
    for c in session.scalars(select(ConsensusRecord).where(ConsensusRecord.correct.is_(None))).all():
        h = (c.payload or {}).get("horizon", 5)
        r = realized(c.symbol, c.as_of, h)
        if r is None:
            continue
        c.realized_return = r
        c.correct = (c.prob_up >= 0.5) == (r > 0)
    return n


def scoreboard(session: Session, since: datetime | None = None, regime: str | None = None,
               window_days: int | None = 365, backends: dict[str, str] | None = None,
               until: datetime | None = None) -> dict[tuple[str, str], TrackRecord]:
    """(analyst, category) → TrackRecord.

    - window_days: 최근 N일 성적만 (시장이 바뀌면 과거 성적의 의미도 바뀐다). None = 전체
    - regime: 해당 국면에서의 성적만
    - backends: {analyst: model} — 해당 백엔드로 낸 의견만 집계 (모델 교체 시 성적 분리)
    """
    q = select(AnalystOpinionRecord.analyst, AnalystOpinionRecord.category, AnalystOpinionRecord.prob_up,
               AnalystOpinionRecord.correct, AnalystOpinionRecord.realized_return,
               AnalystOpinionRecord.payload).where(AnalystOpinionRecord.correct.is_not(None))
    now = until or datetime.now(UTC)
    if since is not None:
        q = q.where(AnalystOpinionRecord.as_of >= since)
    elif window_days is not None:
        q = q.where(AnalystOpinionRecord.as_of >= now - timedelta(days=window_days))
    if until is not None:  # Point-in-Time 가중치: 그 시점에 결과가 이미 나와 있던 의견만
        q = q.where(AnalystOpinionRecord.as_of <= until - timedelta(days=9))
    acc: dict[tuple[str, str], list] = defaultdict(lambda: [0, 0, 0.0])
    for analyst, cat, prob, correct, rr, payload in session.execute(q):
        p = payload or {}
        if regime and p.get("regime") != regime:
            continue
        if backends and analyst in backends and p.get("backend") not in (None, backends[analyst]):
            continue
        a = acc[(analyst, cat)]
        a[0] += 1
        a[1] += int(bool(correct))
        a[2] += ((prob if prob is not None else 0.5) - (1.0 if (rr or 0) > 0 else 0.0)) ** 2
    return {k: TrackRecord(n=v[0], hits=v[1], brier=v[2] / v[0] if v[0] else None) for k, v in acc.items()}


def scoreboard_table(board: dict[tuple[str, str], TrackRecord]) -> list[dict]:
    rows = []
    for (analyst, cat), r in sorted(board.items()):
        rows.append({"analyst": analyst, "category": cat, "n": r.n,
                     "accuracy": r.hits / r.n if r.n else None, "brier": r.brier})
    return rows


CATEGORY_LABELS = {DIRECTION: "단기 방향", NEWS: "뉴스 해석", MACRO: "거시경제", TREND: "추세", RISK: "위험 경고"}


def provider_of(model: str | None, provider: str | None = None) -> str:
    """백엔드 모델 이름 → 공급자 표시 이름 (성적표를 '어느 AI 가 무엇을 잘하나' 로 보여주기 위해)."""
    if provider:
        return {"gemini": "Gemini", "nvidia": "NVIDIA", "groq": "Groq", "cloudflare": "Cloudflare",
                "anthropic": "Claude", "claude": "Claude"}.get(provider, provider)
    m = (model or "").lower()
    if not m:
        return "?"
    if "gemini" in m:
        return "Gemini"
    if m.startswith("@cf/"):
        return "Cloudflare"
    if m.startswith("nvidia/"):
        return "NVIDIA"
    if "claude" in m:
        return "Claude"
    if "gpt-oss" in m or "llama" in m:
        return "Groq"
    if m.startswith("sklearn"):
        return "Quant 모델"
    if m.startswith("regime"):
        return "국면 엔진"
    if m.startswith("rules"):
        return "리스크 규칙"
    if m == "heuristic":
        return "휴리스틱 (v35 이전)"
    if m.startswith("heuristic"):
        return "규칙 (키 없음)"
    if m.startswith("signals2"):
        return "차트 신호"
    return model or "?"


def provider_scoreboard(session: Session, window_days: int | None = 365) -> list[dict]:
    """AI(공급자·모델) × 카테고리 성적. 예: Gemini 뉴스 해석 75% (n=120) · Groq 위험 경고 81%."""
    q = select(AnalystOpinionRecord.analyst, AnalystOpinionRecord.category, AnalystOpinionRecord.prob_up,
               AnalystOpinionRecord.correct, AnalystOpinionRecord.realized_return,
               AnalystOpinionRecord.payload).where(AnalystOpinionRecord.correct.is_not(None))
    if window_days is not None:
        q = q.where(AnalystOpinionRecord.as_of >= datetime.now(UTC) - timedelta(days=window_days))
    acc: dict[tuple[str, str, str], list] = defaultdict(lambda: [0, 0, 0.0, set()])
    for analyst, cat, prob, correct, rr, payload in session.execute(q):
        p = payload or {}
        prov = provider_of(p.get("backend"), p.get("provider"))
        a = acc[(prov, p.get("backend") or "?", cat)]
        a[0] += 1
        a[1] += int(bool(correct))
        a[2] += ((prob if prob is not None else 0.5) - (1.0 if (rr or 0) > 0 else 0.0)) ** 2
        a[3].add(analyst)
    rows = [{"provider": prov, "model": model, "category": cat, "label": CATEGORY_LABELS.get(cat, cat),
             "roles": sorted(v[3]), "n": v[0], "accuracy": v[1] / v[0] if v[0] else None,
             "brier": v[2] / v[0] if v[0] else None}
            for (prov, model, cat), v in acc.items()]
    return sorted(rows, key=lambda r: (r["provider"], r["category"]))

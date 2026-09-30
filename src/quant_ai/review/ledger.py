"""예측 장부 (Prediction Ledger) — "결과를 보고 예측을 고치지 않았다" 를 증명한다.

1) 예측이 저장되는 순간 벽시계 시각(created_at)과 내용 해시(row_hash)를 남긴다.
   해시에 들어가는 것: 종목 · 기준 봉(as_of) · 행동 · 확률 · 신뢰도 · 충돌 · 기간 · 기대수익 · 저장 시각 · 버전(모델·프롬프트).
   나중에 채워지는 결과(realized_return · correct)는 해시에 넣지 않는다 — 결과는 독립 평가기가 가격에서 다시 계산한다.
2) 한 시간마다 새 예측들의 해시를 이전 봉인(digest)과 이어 붙여 봉인(anchor)을 만든다.
   중간의 예측 하나를 고치거나 지우거나 끼워 넣으면 그 뒤 봉인이 모두 맞지 않게 된다.
3) 마지막 봉인 해시는 일일 리포트와 함께 텔레그램 등 외부로 보낸다 → 이 컴퓨터 밖에 기록이 남는다.
   (모든 것이 내 컴퓨터 안에만 있으면 봉인까지 다시 만들 수 있다. 외부에 남긴 해시가 그것을 막는다.)
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import func, select

from ..data.models import ConsensusRecord, LedgerAnchor

GENESIS = "0" * 64


def _num(x, nd):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def _iso(t) -> str | None:
    if t is None:
        return None
    t = pd.Timestamp(t)
    return (t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")).isoformat()


def row_content(rec: ConsensusRecord) -> dict:
    p = rec.payload or {}
    return {"symbol": rec.symbol, "as_of": _iso(rec.as_of), "action": rec.action, "prob_up": _num(rec.prob_up, 6),
            "confidence": _num(rec.confidence, 4), "conflict": rec.conflict, "horizon": p.get("horizon"),
            "expected_return": _num(p.get("expected_return"), 6), "created_at": _iso(rec.created_at),
            "versions": p.get("versions"),
            # 매매 계획(진입 구간·무효화·사이징)은 v13 부터 — 없던 기록의 해시는 그대로
            **({"plan": p["plan"]} if p.get("plan") else {})}


def row_hash(rec: ConsensusRecord) -> str:
    raw = json.dumps(row_content(rec), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def seal(rec: ConsensusRecord, now: datetime | None = None) -> None:
    """저장 직전에 호출: 저장 시각 + 내용 해시."""
    rec.created_at = rec.created_at or now or datetime.now(UTC)
    rec.row_hash = row_hash(rec)


def _chain(prev: str, rows: list[tuple[int, str]]) -> str:
    h = hashlib.sha256(prev.encode())
    for rid, rh in rows:
        h.update(f"|{rid}:{rh}".encode())
    return h.hexdigest()


def anchor(session, now: datetime | None = None) -> dict | None:
    """마지막 봉인 이후의 봉인된 예측들로 새 봉인을 만든다 (없으면 None)."""
    last = session.scalar(select(LedgerAnchor).order_by(LedgerAnchor.id.desc()))
    since = last.upto_id if last else 0
    rows = session.execute(select(ConsensusRecord.id, ConsensusRecord.row_hash).where(
        ConsensusRecord.id > since, ConsensusRecord.row_hash.is_not(None)).order_by(ConsensusRecord.id)).all()
    if not rows:
        return None
    prev = last.digest if last else GENESIS
    digest = _chain(prev, [(r[0], r[1]) for r in rows])
    a = LedgerAnchor(ts=now or datetime.now(UTC), upto_id=rows[-1][0], n=len(rows), digest=digest, prev_digest=prev)
    session.add(a)
    session.flush()
    return {"id": a.id, "upto_id": a.upto_id, "n": a.n, "digest": digest, "prev_digest": prev, "ts": a.ts.isoformat()}


def latest_anchor(session) -> dict | None:
    a = session.scalar(select(LedgerAnchor).order_by(LedgerAnchor.id.desc()))
    return None if a is None else {"id": a.id, "upto_id": a.upto_id, "n": a.n, "digest": a.digest,
                                   "prev_digest": a.prev_digest, "ts": a.ts.isoformat()}


def verify(session, sample_limit: int = 50) -> dict:
    """모든 봉인된 예측의 해시를 다시 계산하고, 봉인 사슬을 처음부터 다시 이어 본다."""
    tampered = []
    rows: dict[int, str] = {}
    for rec in session.scalars(select(ConsensusRecord).where(ConsensusRecord.row_hash.is_not(None))
                               .order_by(ConsensusRecord.id)):
        rows[rec.id] = rec.row_hash
        if row_hash(rec) != rec.row_hash:
            tampered.append(rec.id)
    legacy = session.scalar(select(func.count()).select_from(ConsensusRecord)
                            .where(ConsensusRecord.row_hash.is_(None))) or 0
    anchors = session.scalars(select(LedgerAnchor).order_by(LedgerAnchor.id)).all()
    breaks, prev, lo = [], GENESIS, 0
    ids = sorted(rows)
    for a in anchors:
        seg = [(i, rows[i]) for i in ids if lo < i <= a.upto_id]
        if a.prev_digest != prev or _chain(prev, seg) != a.digest or len(seg) != a.n:
            breaks.append({"anchor": a.id, "upto_id": a.upto_id, "at": _iso(a.ts),
                           "why": "앞 봉인과 이어지지 않음" if a.prev_digest != prev else
                                  "기록 수가 다름 (삭제·추가)" if len(seg) != a.n else "내용이 바뀜"})
        prev, lo = a.digest, a.upto_id
    pending = sum(1 for i in ids if i > lo)
    ok = not tampered and not breaks
    last = anchors[-1] if anchors else None
    return {"ok": ok, "sealed": len(rows), "legacy": int(legacy), "anchors": len(anchors), "pending": pending,
            "tampered": tampered[:sample_limit], "breaks": breaks[:sample_limit],
            "last_digest": last.digest if last else None, "last_anchor_at": _iso(last.ts) if last else None,
            "last_upto_id": last.upto_id if last else None,
            "message": ("장부 무결: 봉인 이후 고쳐진 예측 없음" if ok else
                        f"경고: 해시 불일치 {len(tampered)}건 · 사슬 끊김 {len(breaks)}곳")}


def snapshot_id(payload: dict) -> str | None:
    """판단에 쓴 입력(가격·지표·뉴스 요약)의 지문 — 같은 입력이면 같은 ID (재현·감사용)."""
    import hashlib
    import json
    ev = (payload or {}).get("evidence")
    if not ev:
        return None
    return hashlib.sha256(json.dumps(ev, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:10]


def entries(session, limit: int = 50, before_id: int | None = None, names: dict | None = None, symbol: str | None = None,
            result: str | None = None) -> list[dict]:
    q = select(ConsensusRecord).order_by(ConsensusRecord.id.desc()).limit(limit)
    if before_id:
        q = q.where(ConsensusRecord.id < before_id)
    if symbol:
        q = q.where(ConsensusRecord.symbol == symbol)
    if result == "fail":
        q = q.where(ConsensusRecord.correct.is_(False))
    elif result == "success":
        q = q.where(ConsensusRecord.correct.is_(True))
    names = names or {}
    out = []
    for r in session.scalars(q):
        p = r.payload or {}
        v = p.get("versions") or {}
        plan = p.get("plan") or {}
        out.append({"id": r.id, "symbol": r.symbol, "name": names.get(r.symbol, r.symbol), "as_of": _iso(r.as_of),
                    "created_at": _iso(r.created_at), "action": r.action, "prob_up": r.prob_up,
                    "prediction": "UP" if r.prob_up >= 0.5 else "DOWN",
                    "confidence": r.confidence, "horizon": p.get("horizon"), "expected": p.get("expected_return"),
                    "risk": plan.get("stop_pct"),
                    "model": v.get("quant") or v.get("model") or next((f"{k}:{x}" for k, x in v.items() if k not in ("replay",)), None),
                    "snapshot": snapshot_id(p),
                    "realized": r.realized_return, "correct": r.correct, "outcomes": p.get("outcomes"),
                    "result": "PENDING" if r.correct is None else "SUCCESS" if r.correct else "FAIL",
                    "hash": r.row_hash, "hash_ok": (row_hash(r) == r.row_hash) if r.row_hash else None,
                    "versions": v, "trigger": p.get("trigger")})
    return out


__all__ = ["seal", "row_hash", "anchor", "latest_anchor", "verify", "entries", "GENESIS"]

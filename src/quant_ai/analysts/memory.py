"""RAG 메모리: 뉴스·이벤트·과거 AI 판단과 그 결과를 임베딩으로 저장하고 유사 사례를 찾는다.

"지금과 비슷했던 과거 상황에서 무슨 일이 있었나?"를 AI 에게 근거로 제공한다.
임베딩: NVIDIA nemotron embed (키 있을 때) / 로컬 해싱 (오프라인).
규모가 커지면 pgvector 로 옮기면 된다 (스키마는 동일하게 유지).
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.models import MemoryDoc
from .llm_clients import Embeddings, HashingEmbeddings


class Memory:
    def __init__(self, embedder: Embeddings | None = None):
        self.embedder = embedder or HashingEmbeddings()

    def add(self, session: Session, kind: str, text: str, ts: datetime, symbol: str | None = None,
            meta: dict | None = None) -> None:
        vec = self.embedder.embed([text], kind="passage")[0]
        session.add(MemoryDoc(kind=kind, ts=ts, symbol=symbol, text=text, embedding=vec.round(5).tolist(),
                              embed_model=self.embedder.model, meta=meta or {}))

    def search(self, session: Session, query: str, k: int = 5, before: datetime | None = None,
               kinds: tuple[str, ...] | None = None, symbol: str | None = None) -> list[dict]:
        """before 이전 문서만 검색 (백테스트/복기 시 미래 정보 누수 방지)."""
        q = select(MemoryDoc).where(MemoryDoc.embed_model == self.embedder.model)
        if before is not None:
            q = q.where(MemoryDoc.ts < before)
        if kinds:
            q = q.where(MemoryDoc.kind.in_(kinds))
        if symbol:
            q = q.where((MemoryDoc.symbol == symbol) | (MemoryDoc.symbol.is_(None)))
        docs = session.scalars(q.order_by(MemoryDoc.ts.desc()).limit(5000)).all()
        if not docs:
            return []
        qv = self.embedder.embed([query], kind="query")[0]
        mat = np.array([d.embedding for d in docs], float)
        sims = mat @ qv
        top = np.argsort(-sims)[:k]
        return [{"ts": str(docs[i].ts), "kind": docs[i].kind, "symbol": docs[i].symbol,
                 "text": docs[i].text[:400], "similarity": round(float(sims[i]), 3), **(docs[i].meta or {})}
                for i in top]

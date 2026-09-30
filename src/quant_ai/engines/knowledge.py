"""지식 그래프 — 종목 사이의 관계를 데이터로 만든다.

관계 (근거가 되는 숫자와 함께 저장)
  · 동시 언급 : 최근 90일 뉴스에 함께 나온 횟수 (공급망 · 경쟁 · 같은 이슈)
  · 가격 연동 : 최근 120거래일 일간 수익률 상관 ≥ 0.55 (종목마다 가장 강한 6개)
  · 같은 업종 : 섹터 엔진의 업종이 같음
쓰임
  · AI 판단 재료: "연관 종목" 의 최근 움직임과 최신 뉴스 제목 (예: SK하이닉스를 볼 때 삼성전자·마이크론)
  · 화면: 종목 화면의 연관 종목 그래프, 전체 그래프
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import combinations

import numpy as np
import pandas as pd
from sqlalchemy import select

from .. import ops
from ..data.db import session_scope
from ..data.models import Instrument, NewsArticle

CORR_MIN = 0.55
CORR_TOP = 6
NEWS_DAYS = 90


def build(engine, bars: dict[str, pd.DataFrame], sectors: dict[str, str] | None = None, max_symbols: int = 150,
          now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    sectors = sectors or {}
    with session_scope(engine) as s:
        names = {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument)) if i.market != "INDEX"}
        co = Counter()
        last_title: dict[tuple, str] = {}
        for n in s.scalars(select(NewsArticle).where(NewsArticle.published_at >= now - timedelta(days=NEWS_DAYS))):
            syms = sorted({x for x in (n.symbols or []) if x in names})
            if 2 <= len(syms) <= 6:  # 너무 많은 종목이 나열된 기사(시황 요약)는 관계 근거가 약하다
                for a, b in combinations(syms, 2):
                    co[(a, b)] += 1
                    last_title[(a, b)] = n.title
    # 거래대금 상위 종목만 상관 계산 (계산량 제한)
    liq = sorted(((float((b["close"] * b["volume"]).iloc[-20:].mean()), sym) for sym, b in bars.items()
                  if len(b) >= 130 and sym in names), reverse=True)[:max_symbols]
    syms = [x for _, x in liq]
    edges: dict[tuple, dict] = {}
    if len(syms) >= 3:
        rets = pd.DataFrame({sym: bars[sym]["close"].astype(float).pct_change() for sym in syms}).iloc[-120:]
        corr = rets.corr(min_periods=60)
        for a in syms:
            row = corr[a].drop(a).dropna()
            for b, v in row[row >= CORR_MIN].sort_values(ascending=False).head(CORR_TOP).items():
                k = tuple(sorted((a, b)))
                edges.setdefault(k, {})["corr"] = round(float(v), 3)
    for k, c in co.items():
        if c >= 2:
            edges.setdefault(k, {})["co_mention"] = c
            edges[k]["title"] = last_title[k][:120]
    for k, e in edges.items():
        if sectors.get(k[0]) and sectors.get(k[0]) == sectors.get(k[1]):
            e["same_sector"] = sectors[k[0]]
        # 가중치: 상관(0~1) + 동시언급(로그) + 같은 업종 보너스
        e["weight"] = round(e.get("corr", 0) + 0.35 * np.log1p(e.get("co_mention", 0)) + (0.2 if e.get("same_sector") else 0), 3)
    edge_list = [{"a": a, "b": b, **e} for (a, b), e in sorted(edges.items(), key=lambda kv: -kv[1]["weight"])]
    used = {x for e in edge_list for x in (e["a"], e["b"])}
    graph = {"at": now.isoformat(), "nodes": {x: {"name": names.get(x, x), "sector": sectors.get(x)} for x in used},
             "edges": edge_list[:3000], "stats": {"nodes": len(used), "edges": len(edge_list),
                                                  "co_mention_pairs": sum(1 for e in edge_list if e.get("co_mention")),
                                                  "corr_pairs": sum(1 for e in edge_list if e.get("corr"))}}
    ops.set_state(engine, "kgraph", graph)
    return graph["stats"]


def neighbors(graph: dict, symbol: str, k: int = 10) -> list[dict]:
    out = []
    for e in graph.get("edges", []):
        if symbol in (e["a"], e["b"]):
            other = e["b"] if e["a"] == symbol else e["a"]
            out.append({"symbol": other, "name": (graph.get("nodes", {}).get(other) or {}).get("name", other),
                        **{kk: e.get(kk) for kk in ("corr", "co_mention", "same_sector", "weight", "title")}})
    return sorted(out, key=lambda x: -(x["weight"] or 0))[:k]


def ego(graph: dict, symbol: str, k: int = 10) -> dict:
    """종목 중심 그래프: 이웃 k 개 + 이웃끼리의 연결."""
    nb = neighbors(graph, symbol, k)
    keep = {symbol, *(n["symbol"] for n in nb)}
    edges = [e for e in graph.get("edges", []) if e["a"] in keep and e["b"] in keep]
    nodes = {x: graph.get("nodes", {}).get(x) or {"name": x} for x in keep}
    return {"center": symbol, "nodes": nodes, "edges": edges, "neighbors": nb}


def for_context(engine, symbol: str, bars: dict[str, pd.DataFrame], k: int = 5) -> list[dict]:
    """AI 판단 재료: 연관 종목의 최근 움직임 + 관계 근거."""
    g = ops.get_state(engine, "kgraph")
    if not g:
        return []
    out = []
    for n in neighbors(g, symbol, k):
        b = bars.get(n["symbol"])
        r5 = float(b["close"].iloc[-1] / b["close"].iloc[-6] - 1) if b is not None and len(b) > 6 else None
        rel = [x for x in (f"상관 {n['corr']:.2f}" if n.get("corr") else None,
                           f"뉴스 동시언급 {n['co_mention']}회" if n.get("co_mention") else None,
                           f"같은 업종({n['same_sector']})" if n.get("same_sector") else None) if x]
        out.append({"name": n["name"], "relation": " · ".join(rel), "ret_5": round(r5, 4) if r5 is not None else None})
    return out


__all__ = ["build", "neighbors", "ego", "for_context"]

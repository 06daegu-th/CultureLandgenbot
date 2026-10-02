"""NLP 이벤트 추출 — 뉴스·공시 제목에서 '무슨 일이, 얼마 규모로, 누구와' 를 구조화한다.

규칙 기반(한국어 · 영어)이라 LLM 한도를 쓰지 않고, 같은 입력엔 늘 같은 결과(재현 가능)를 낸다.
  · 유형: 공급계약/수주 · 유상증자 · 무상증자 · 자사주 매입·소각 · 배당 · 인수합병 · 분할 · 실적(서프라이즈·흑자전환·적자)
          · 가이던스 · 소송 · 리콜 · 승인(FDA 등) · 경영진 변경 · 거래정지·상장폐지 위험 · 신제품 · 공매도·대주주 매도
  · 금액: "1,234억원" · "1.2조" · "$3.5 billion" · "500만 달러" → 원 단위 (달러는 1,350원 환산 · 근사)
  · 상대방: 제목에 나온 다른 상장사 이름
  · 극성: 유형별 기본(+/−)
  · 중요도: 금액 ÷ 시가총액 (알면) → 1% 넘으면 중요, 10% 넘으면 매우 중요
"""

from __future__ import annotations

import re

USDKRW = 1350.0
# (유형, 한국어, 극성, 기본 중요도, 키워드들)
TYPES = [
    ("delisting_risk", "거래정지·상폐 위험", -1, 0.95, ("거래정지", "상장폐지", "관리종목", "감사의견 거절", "delist", "trading halt")),
    ("rights_issue", "유상증자", -1, 0.8, ("유상증자", "제3자배정", "주주배정", "rights offering", "share offering")),
    ("cb_bw", "전환사채·BW", -1, 0.6, ("전환사채", "신주인수권부사채", "교환사채", "convertible")),
    ("bonus_issue", "무상증자", 1, 0.5, ("무상증자", "stock split", "주식분할", "액면분할")),
    ("buyback", "자사주 매입·소각", 1, 0.6, ("자사주 매입", "자기주식 취득", "자사주 소각", "자기주식 소각", "buyback", "repurchase")),
    ("dividend", "배당", 1, 0.4, ("배당", "dividend")),
    ("mna", "인수합병", 0, 0.8, ("인수", "합병", "경영권", "지분 취득", "acquire", "acquisition", "merger", "takeover")),
    ("spinoff", "분할", 0, 0.6, ("물적분할", "인적분할", "분할 결정", "spin-off", "spinoff")),
    ("contract", "공급계약·수주", 1, 0.7, ("공급계약", "수주", "단일판매", "납품 계약", "공급 계약", "contract", "order win", "deal with")),
    ("earnings_beat", "실적 호조", 1, 0.8, ("어닝 서프라이즈", "사상 최대", "최대 실적", "흑자전환", "흑자 전환", "beats", "beat estimates", "record revenue")),
    ("earnings_miss", "실적 부진", -1, 0.8, ("어닝 쇼크", "적자전환", "적자 전환", "영업손실", "실적 부진", "misses", "miss estimates")),
    ("earnings", "실적 발표", 0, 0.6, ("잠정실적", "영업실적", "실적 발표", "분기 실적", "earnings", "quarterly results")),
    ("guidance_up", "전망 상향", 1, 0.7, ("가이던스 상향", "전망 상향", "목표가 상향", "raises guidance", "raises outlook", "upgrade")),
    ("guidance_down", "전망 하향", -1, 0.7, ("가이던스 하향", "전망 하향", "목표가 하향", "cuts guidance", "lowers outlook", "downgrade")),
    ("lawsuit", "소송·분쟁", -1, 0.5, ("소송", "제소", "특허 침해", "과징금", "lawsuit", "sued", "fine")),
    ("recall", "리콜·사고", -1, 0.6, ("리콜", "화재", "사고", "결함", "recall")),
    ("approval", "승인·허가", 1, 0.7, ("승인", "허가", "품목허가", "fda", "approval", "cleared")),
    ("management", "경영진 변경", 0, 0.4, ("대표이사 변경", "ceo 사임", "사임", "선임", "resign", "appoint")),
    ("insider_sell", "대주주 매도·블록딜", -1, 0.6, ("블록딜", "시간외 대량매매", "대주주 매도", "지분 매각", "block deal", "stake sale")),
    ("product", "신제품·기술", 1, 0.4, ("출시", "공개", "신제품", "개발 성공", "launch", "unveil")),
]
_AMT = [
    (re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*조\s*(\d[\d,]*)?\s*억?\s*원?"), "jo"),
    (re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*억\s*원?"), "eok"),
    (re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(billion|million|bn|mn|b|m)\b", re.I), "usd"),
    (re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|만)\s*달러"), "usd_ko"),
]


def _f(x: str) -> float:
    return float(x.replace(",", ""))


def parse_amount(text: str) -> float | None:
    """원 단위 금액 (가장 큰 것)."""
    best = None
    for rx, kind in _AMT:
        for m in rx.finditer(text or ""):
            try:
                if kind == "jo":
                    v = _f(m.group(1)) * 1e12 + (_f(m.group(2)) * 1e8 if m.group(2) else 0)
                elif kind == "eok":
                    v = _f(m.group(1)) * 1e8
                elif kind == "usd":
                    unit = m.group(2).lower()
                    v = _f(m.group(1)) * (1e9 if unit in ("billion", "bn", "b") else 1e6) * USDKRW
                else:
                    v = _f(m.group(1)) * (1e8 if m.group(2) == "억" else 1e4) * USDKRW
            except ValueError:
                continue
            best = v if best is None or v > best else best
    return best


def extract(title: str, names: dict[str, str] | None = None, subject: str | None = None, market_cap: float | None = None) -> list[dict]:
    """제목 하나 → 이벤트 목록 (보통 0~2개)."""
    t = (title or "").lower()
    t_ns = t.replace(" ", "")  # DART 보고서명은 띄어쓰기가 없다 ("자기주식취득결정")
    out = []
    for key, ko, pol, imp, kws in TYPES:
        hit = next((w for w in kws if w in t or (" " in w and w.replace(" ", "") in t_ns)), None)
        if not hit:
            continue
        if key == "earnings" and any(e["type"].startswith("earnings_") for e in out):
            continue
        if key == "dividend" and "배당락" in t:
            continue
        amt = parse_amount(title) if key in ("contract", "rights_issue", "cb_bw", "buyback", "mna", "dividend", "insider_sell") else None
        mat = None
        if amt and market_cap:
            r = amt / market_cap
            mat = round(r, 4)
            imp = min(1.0, imp + (0.2 if r >= 0.1 else 0.1 if r >= 0.01 else -0.1))
        cps = [s for s, n in (names or {}).items() if n and len(n) >= 2 and n.lower() in t and s != subject][:3]
        out.append({"type": key, "label": ko, "polarity": pol, "importance": round(imp, 2), "keyword": hit,
                    "amount_krw": amt, "materiality": mat, "counterparties": cps})
    return out


def annotate(session, names: dict[str, str], caps: dict[str, float] | None = None, since=None, limit: int = 2000) -> dict:
    """최근 뉴스·공시에 추출 결과를 붙여 요약 (DB 는 읽기만 — 결과는 ops 에 저장)."""
    from sqlalchemy import select

    from ..data.models import Disclosure, NewsArticle
    rows = []
    q = select(NewsArticle).order_by(NewsArticle.published_at.desc()).limit(limit)
    if since is not None:
        q = q.where(NewsArticle.published_at >= since)
    for n in session.scalars(q):
        for sym in (n.symbols or [None])[:3]:
            for e in extract(n.title, names, sym, (caps or {}).get(sym)):
                rows.append({**e, "symbol": sym, "title": n.title[:140], "date": n.published_at.date().isoformat(), "source": "뉴스"})
    q = select(Disclosure).order_by(Disclosure.filed_at.desc()).limit(limit)
    if since is not None:
        q = q.where(Disclosure.filed_at >= since)
    for d in session.scalars(q):
        for e in extract(d.title, names, d.symbol, (caps or {}).get(d.symbol)):
            rows.append({**e, "symbol": d.symbol, "title": d.title[:140],
                         "date": (d.filed_at.date() if hasattr(d.filed_at, "date") else d.filed_at).isoformat(), "source": "공시"})
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["label"]] = by_type.get(r["label"], 0) + 1
    rows.sort(key=lambda r: (r["date"], r["importance"]), reverse=True)
    return {"n": len(rows), "by_type": sorted(by_type.items(), key=lambda x: -x[1]), "events": rows[:400]}


__all__ = ["extract", "parse_amount", "annotate", "TYPES"]

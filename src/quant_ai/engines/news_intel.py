"""News Intelligence.

기본 구현은 한/영 키워드 사전 기반이다 (빠르고, 설명 가능하고, 오프라인에서 동작).
``NewsAnalyzer`` 를 상속해 ``analyze`` 만 바꾸면 LLM 기반 분석기로 교체할 수 있다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import pandas as pd

POSITIVE = {
    "상승": 1.0, "급등": 1.5, "호실적": 1.5, "어닝서프라이즈": 2.0, "최대 실적": 1.5, "수주": 1.2, "흑자전환": 1.5,
    "자사주 매입": 1.2, "자사주 소각": 1.5, "배당 확대": 1.0, "상향": 1.0, "신고가": 1.0, "승인": 1.0, "무상증자": 0.8,
    "beat": 1.5, "surge": 1.5, "record": 1.0, "upgrade": 1.2, "buyback": 1.2, "approval": 1.0, "raises guidance": 1.5,
    "rally": 1.0, "outperform": 1.0,
}
NEGATIVE = {
    "하락": 1.0, "급락": 1.5, "적자": 1.2, "어닝쇼크": 2.0, "하향": 1.0, "유상증자": 1.5, "소송": 1.0, "리콜": 1.2,
    "횡령": 2.0, "배임": 2.0, "상장폐지": 3.0, "거래정지": 2.5, "감사의견 거절": 3.0, "영업정지": 2.0, "신저가": 1.0,
    "miss": 1.5, "plunge": 1.5, "downgrade": 1.2, "lawsuit": 1.0, "recall": 1.2, "fraud": 2.0, "cuts guidance": 1.5,
    "bankruptcy": 3.0, "investigation": 1.2, "selloff": 1.2,
}
EVENTS = {
    "earnings": ["실적", "영업이익", "매출", "earnings", "revenue", "eps"],
    "guidance": ["가이던스", "전망", "guidance", "outlook"],
    "capital": ["유상증자", "무상증자", "전환사채", "자사주", "배당", "buyback", "dividend", "offering"],
    "mna": ["인수", "합병", "매각", "acquisition", "merger", "acquire"],
    "legal": ["소송", "횡령", "배임", "제재", "lawsuit", "investigation", "sec"],
    "contract": ["수주", "공급계약", "계약 체결", "contract", "deal"],
    "macro": ["금리", "환율", "cpi", "fomc", "물가", "고용", "inflation", "fed", "rate"],
    "delisting": ["상장폐지", "거래정지", "감사의견", "delist"],
}
EVENT_WEIGHT = {"delisting": 3.0, "legal": 1.5, "earnings": 1.5, "capital": 1.3, "mna": 1.3,
                "guidance": 1.2, "contract": 1.1, "macro": 1.0}


@dataclass
class NewsAnalysis:
    sentiment: float  # -1 ~ +1
    importance: float  # 0 ~ 1
    events: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)


class NewsAnalyzer:
    def analyze(self, title: str, body: str = "") -> NewsAnalysis:
        # 제목에 가중치 2배
        text_title, text_body = title.lower(), body.lower()
        score, matched = 0.0, []
        for words, sign in ((POSITIVE, 1), (NEGATIVE, -1)):
            for w, weight in words.items():
                hits = 2 * text_title.count(w) + text_body.count(w)
                if hits:
                    score += sign * weight * hits
                    matched.append(w)
        # 부정 표현 "~않" 등은 단순 사전의 한계. LLM 분석기로 교체 시 해결.
        sentiment = math.tanh(score / 3.0)
        text = f"{text_title} {text_body}"
        events = [e for e, kws in EVENTS.items() if any(k in text for k in kws)]
        importance = min(1.0, 0.2 + 0.1 * len(matched) + 0.15 * sum(EVENT_WEIGHT[e] for e in events))
        return NewsAnalysis(sentiment=sentiment, importance=importance, events=events, matched=matched)


def tag_symbols(text: str, aliases: dict[str, list[str]]) -> list[str]:
    """종목 별칭(종목명, 코드, 티커)이 기사에 나오면 태깅. 짧은 영문 티커는 단어 경계로만 매칭."""
    lower = text.lower()
    tagged = []
    for symbol, names in aliases.items():
        for name in names:
            n = name.lower()
            if not n:
                continue
            if n.isascii() and len(n) <= 5:
                if re.search(rf"\b{re.escape(n)}\b", lower):
                    tagged.append(symbol)
                    break
            elif n in lower:
                tagged.append(symbol)
                break
    return tagged


def daily_sentiment(articles: pd.DataFrame, half_life_days: float = 2.0) -> pd.DataFrame:
    """기사 → (date, symbol) 일별 감성 지수. 중요도 가중 + 시간 감쇠.

    articles 컬럼: published_at(UTC), symbol, sentiment, importance
    반환: index=date, columns=symbol
    각 날짜의 값은 그 날짜까지 공개된 기사만 사용한다 (미래 정보 누수 없음).
    """
    if articles.empty:
        return pd.DataFrame()
    df = articles.copy()
    df["date"] = pd.to_datetime(df["published_at"], utc=True).dt.normalize()
    df["w"] = df["sentiment"] * df["importance"]
    if "title" in df:
        # 같은 날 같은 종목의 같은 사건 기사들은 하나로 (중복 보도로 감성이 부풀려지지 않게): 이벤트별 평균
        from .market_intel import cluster_news
        rows = []
        for (d, sym), g in df.groupby(["date", "symbol"]):
            for e in cluster_news([{"ts": str(t), "title": ti, "sentiment": w}
                                   for t, ti, w in zip(g["published_at"], g["title"], g["w"], strict=True)]):
                rows.append({"date": d, "symbol": sym, "w": e["sentiment"] or 0.0})
        df = pd.DataFrame(rows)
    daily = df.pivot_table(index="date", columns="symbol", values="w", aggfunc="sum").sort_index()
    full = pd.date_range(daily.index.min(), daily.index.max(), freq="D", tz="UTC")
    daily = daily.reindex(full).fillna(0.0)
    alpha = 1 - 0.5 ** (1 / half_life_days)
    return daily.ewm(alpha=alpha, adjust=False).mean()

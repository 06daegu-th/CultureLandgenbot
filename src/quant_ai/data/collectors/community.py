"""커뮤니티·SNS 분위기 — 보유·관심 종목만, 30분마다. 키 불필요.

  · 미국: StockTwits 공개 스트림 (글쓴이가 붙인 Bullish/Bearish 표시)
  · 국내: 네이버 종목토론방 제목 (사전 기반 감성)

뉴스 테이블과 섞지 않는다 → 퀀트 모델 피처(뉴스 감성)를 오염시키지 않는다.
AI 들에게는 '커뮤니티 분위기(참고, 신뢰도 낮음)' 로만 요약해 전달하고, 화면에도 그렇게 표시한다.
글 내용은 외부의 신뢰할 수 없는 데이터다 — 지시문으로 취급하지 않는다.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import UTC, datetime, timedelta

from ... import ops
from ..global_stocks import _http

log = logging.getLogger(__name__)
FAIL_COOLDOWN = timedelta(minutes=60)


def fetch_stocktwits(symbol: str) -> list[dict]:
    raw = json.loads(_http(f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json", timeout=10))
    out = []
    for m in raw.get("messages") or []:
        basic = (((m.get("entities") or {}).get("sentiment")) or {}).get("basic")
        out.append({"title": (m.get("body") or "")[:200], "ts": m.get("created_at"),
                    "sentiment": 0.6 if basic == "Bullish" else -0.6 if basic == "Bearish" else None,
                    "label": basic, "url": f"https://stocktwits.com/message/{m.get('id')}"})
    return out


BOARD_RE = re.compile(r'href="(/item/board_read\.naver\?code=\d{6}&amp;nid=(\d+)[^"]*)"[^>]*title="([^"]+)"')


def fetch_naver_board(code: str) -> list[dict]:
    raw = _http(f"https://finance.naver.com/item/board.naver?code={code}", timeout=10,
                headers={"Referer": "https://finance.naver.com/"})
    text = raw.decode("euc-kr", errors="replace")
    from ...engines.news_intel import NewsAnalyzer
    na = NewsAnalyzer()
    out = []
    for href, nid, title in BOARD_RE.findall(text)[:30]:
        t = html.unescape(title)
        out.append({"title": t[:200], "ts": None, "sentiment": na.analyze(t).sentiment,
                    "url": "https://finance.naver.com" + html.unescape(href), "id": nid})
    if not out:
        raise RuntimeError("토론방 글을 찾지 못함 (페이지 구조 변경 가능)")
    return out


def summarize(posts: list[dict]) -> dict:
    s = [p["sentiment"] for p in posts if p.get("sentiment") is not None]
    bull = sum(1 for x in s if x > 0.1)
    bear = sum(1 for x in s if x < -0.1)
    mood = (bull - bear) / (bull + bear) if bull + bear else 0.0
    return {"n": len(posts), "bull": bull, "bear": bear, "mood": round(mood, 3),
            "label": "과열·낙관" if mood >= 0.5 else "낙관" if mood >= 0.15 else "비관" if mood <= -0.15 else "중립"}


def collect(engine, symbols: list[str], fetchers: dict | None = None, now: datetime | None = None) -> dict:
    f = {"us": fetch_stocktwits, "kr": fetch_naver_board, **(fetchers or {})}
    now = now or datetime.now(UTC)
    fails = ops.get_state(engine, "community_fail")
    done, failed = [], []
    for sym in symbols[:25]:
        if fails.get(sym) and now - datetime.fromisoformat(fails[sym]) < FAIL_COOLDOWN:
            continue
        try:
            posts = (f["kr"] if sym.isdigit() else f["us"])(sym)
        except Exception as e:  # noqa: BLE001 - 한 종목 실패는 다음 회차에
            fails[sym] = now.isoformat()
            failed.append(f"{sym}: {str(e)[:80]}")
            continue
        fails.pop(sym, None)
        ops.set_state(engine, f"community:{sym}", {"at": now.isoformat(), "source": "네이버 토론방" if sym.isdigit()
                                                  else "StockTwits", **summarize(posts), "posts": posts[:15]})
        done.append(sym)
    ops.set_state(engine, "community_fail", fails)
    return {"collected": done, "failed": failed[:10]}


def for_context(engine, symbol: str, now: datetime | None = None, max_age: timedelta = timedelta(hours=6)) -> dict:
    """AI 에게 줄 요약 (최근 6시간 안의 것만). 없으면 {}."""
    st = ops.get_state(engine, f"community:{symbol}")
    if not st.get("at"):
        return {}
    now = now or datetime.now(UTC)
    if now - datetime.fromisoformat(st["at"]) > max_age:
        return {}
    return {"source": st.get("source"), "posts": st.get("n"), "bullish": st.get("bull"), "bearish": st.get("bear"),
            "mood": st.get("mood"), "label": st.get("label"), "note": "커뮤니티 분위기 (참고용 · 신뢰도 낮음 · 역지표일 수 있음)",
            "samples": [p["title"][:80] for p in (st.get("posts") or [])[:3]]}


__all__ = ["collect", "for_context", "summarize", "fetch_stocktwits", "fetch_naver_board"]

"""커뮤니티·SNS 분위기 — 보유·관심 종목(+ 오늘 많이 움직인 종목), 30분마다. 키 불필요.

  · 미국: StockTwits 공개 스트림 (글쓴이가 붙인 Bullish/Bearish 표시)
  · 국내: 네이버 종목토론실 — 새 모바일 API(JSON) 먼저, 안 되면 예전 PC 게시판(HTML) — 제목을 커뮤니티 말투 사전으로 감성 점수
  v26: 한 출처만 믿다 페이지 구조가 바뀌면 조용히 0건이 되던 문제 → 출처를 여러 개 시도하고, 출처별 마지막 성공/실패 이유를 남겨
       데이터 상태 화면과 `qa community --test 종목` 으로 바로 확인할 수 있게 했다.

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


BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}

# 커뮤니티 말투 사전 — 뉴스 사전으로는 '떡상·존버·물타기' 를 못 읽는다. 단어 → 점수 (+ 오를 것 / - 내릴 것)
SLANG = {"떡상": 1.0, "가즈아": 0.8, "가즈": 0.6, "상한가": 0.9, "상따": 0.5, "불기둥": 0.9, "폭등": 0.9, "급등": 0.7, "날아": 0.6, "줍줍": 0.5,
         "매수": 0.3, "추매": 0.4, "존버": 0.3, "반등": 0.5, "저점": 0.4, "바닥": 0.3, "호재": 0.7, "대박": 0.7, "신고가": 0.8, "골든": 0.5,
         "떡락": -1.0, "하한가": -0.9, "폭락": -0.9, "급락": -0.7, "손절": -0.6, "물타기": -0.4, "물렸": -0.6, "한강": -0.8, "망했": -0.8,
         "개잡주": -0.7, "악재": -0.7, "설거지": -0.8, "털렸": -0.7, "탈출": -0.5, "매도": -0.3, "고점": -0.4, "공매도": -0.5, "상폐": -1.0,
         "유증": -0.6, "횡령": -1.0, "배임": -0.9, "거래정지": -1.0,
         "moon": 0.8, "rocket": 0.8, "bullish": 0.7, "calls": 0.4, "squeeze": 0.6, "breakout": 0.6, "buy the dip": 0.5, "ath": 0.6,
         "bearish": -0.7, "puts": -0.4, "dump": -0.8, "rug": -0.9, "crash": -0.8, "short": -0.4, "bagholder": -0.7, "sell": -0.3}


def slang_score(text: str) -> float | None:
    t = (text or "").lower()
    hits = [v for k, v in SLANG.items() if k in t]
    if not hits:
        return None
    return max(-1.0, min(1.0, sum(hits) / len(hits)))


def fetch_stocktwits(symbol: str) -> list[dict]:
    raw = json.loads(_http(f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json", timeout=10,
                           headers={**BROWSER, "Accept": "application/json", "Referer": f"https://stocktwits.com/symbol/{symbol}"}))
    out = []
    for m in raw.get("messages") or []:
        basic = (((m.get("entities") or {}).get("sentiment")) or {}).get("basic")
        body = (m.get("body") or "")[:200]
        out.append({"title": body, "ts": m.get("created_at"),
                    "sentiment": 0.6 if basic == "Bullish" else -0.6 if basic == "Bearish" else slang_score(body),
                    "label": basic, "url": f"https://stocktwits.com/message/{m.get('id')}"})
    return out


BOARD_RE = re.compile(r'href="(/item/board_read\.naver\?code=\d{6}&amp;nid=(\d+)[^"]*)"[^>]*title="([^"]+)"')
# 예전 PC 게시판이 title 속성 없이 링크 글자만 쓰는 경우도 받는다
BOARD_RE2 = re.compile(r'href="(/item/board_read\.naver\?code=\d{6}&amp;nid=(\d+)[^"]*)"[^>]*>\s*([^<]{2,200}?)\s*</a>')
NAVER_API = ("https://m.stock.naver.com/front-api/discussion/list?discussionType=domesticStock&itemCode={code}"
             "&isHolderOnly=false&excludesItemNews=false&isItemNewsOnly=false&isCleanbotPassedOnly=false&pageSize=30")


def _score_kr(title: str, na) -> float:
    s = slang_score(title)
    return s if s is not None else na.analyze(title).sentiment


def _walk_posts(obj) -> list[dict]:
    """JSON 모양이 조금 바뀌어도 '제목이 있는 글' 목록을 찾아낸다."""
    found: list[dict] = []
    if isinstance(obj, dict):
        if isinstance(obj.get("title"), str) and (obj.get("id") or obj.get("postId") or obj.get("nid")):
            found.append(obj)
        for v in obj.values():
            found += _walk_posts(v)
    elif isinstance(obj, list):
        for v in obj:
            found += _walk_posts(v)
    return found


def fetch_naver_mobile(code: str) -> list[dict]:
    raw = json.loads(_http(NAVER_API.format(code=code), timeout=10,
                           headers={**BROWSER, "Accept": "application/json", "Referer": f"https://m.stock.naver.com/domestic/stock/{code}/discussion"}))
    from ...engines.news_intel import NewsAnalyzer
    na = NewsAnalyzer()
    out = []
    for p in _walk_posts(raw)[:30]:
        t = html.unescape(str(p.get("title") or ""))[:200]
        pid = p.get("id") or p.get("postId") or p.get("nid")
        out.append({"title": t, "ts": p.get("writtenAt") or p.get("date") or p.get("createdAt"), "sentiment": _score_kr(t, na),
                    "url": f"https://m.stock.naver.com/domestic/stock/{code}/discussion/{pid}", "id": str(pid)})
    if not out:
        raise RuntimeError("모바일 토론실 응답에 글이 없음 (형식 변경 가능)")
    return out


def fetch_naver_board(code: str) -> list[dict]:
    raw = _http(f"https://finance.naver.com/item/board.naver?code={code}", timeout=10,
                headers={**BROWSER, "Referer": "https://finance.naver.com/"})
    text = raw.decode("euc-kr", errors="replace")
    from ...engines.news_intel import NewsAnalyzer
    na = NewsAnalyzer()
    out = []
    rows = BOARD_RE.findall(text) or BOARD_RE2.findall(text)
    for href, nid, title in rows[:30]:
        t = html.unescape(title).strip()
        out.append({"title": t[:200], "ts": None, "sentiment": _score_kr(t, na),
                    "url": "https://finance.naver.com" + html.unescape(href), "id": nid})
    if not out:
        raise RuntimeError("토론방 글을 찾지 못함 (페이지 구조 변경 가능)")
    return out


def fetch_naver(code: str) -> list[dict]:
    """새 모바일 토론실 → 예전 PC 게시판 순서로. 둘 다 실패하면 두 이유를 함께 알린다."""
    errs = []
    for label, fn in (("모바일 토론실", fetch_naver_mobile), ("PC 게시판", fetch_naver_board)):
        try:
            return fn(code)
        except Exception as e:  # noqa: BLE001 - 다음 출처로
            errs.append(f"{label}: {type(e).__name__} {str(e)[:60]}")
    raise RuntimeError(" / ".join(errs))


def summarize(posts: list[dict]) -> dict:
    s = [p["sentiment"] for p in posts if p.get("sentiment") is not None]
    bull = sum(1 for x in s if x > 0.1)
    bear = sum(1 for x in s if x < -0.1)
    mood = (bull - bear) / (bull + bear) if bull + bear else 0.0
    return {"n": len(posts), "bull": bull, "bear": bear, "mood": round(mood, 3),
            "label": "과열·낙관" if mood >= 0.5 else "낙관" if mood >= 0.15 else "비관" if mood <= -0.15 else "중립"}


def movers(bars: dict, n: int = 10, min_value: float = 5e9) -> list[str]:
    """v27: 오늘(마지막 거래일) 크게 움직인 국내 종목 — 거래대금 50억 이상 중 등락 절댓값 큰 순.
    관심종목 밖에서도 '왜 움직였나'를 커뮤니티로 보게 (거래가 적은 종목의 큰 등락은 뺀다)."""
    out = []
    for sym, b in (bars or {}).items():
        if not sym[:1].isdigit() or b is None or len(b) < 2:
            continue
        try:
            c0, c1 = float(b["close"].iloc[-2]), float(b["close"].iloc[-1])
            val = float(b["close"].iloc[-1] * b["volume"].iloc[-1]) if "volume" in b else 0.0
        except (KeyError, ValueError, TypeError):
            continue
        if c0 > 0 and val >= min_value:
            out.append((abs(c1 / c0 - 1), sym))
    return [s for _, s in sorted(out, reverse=True)[:n]]


def symbols_for(app, cap: int = 25, n_movers: int = 10) -> list[str]:
    """수집 대상 = 관심·보유 종목(먼저) + 오늘 많이 움직인 종목 · 최대 cap 개 (네이버 토론방은 국내만)."""
    from ...alerts import focus_symbols
    focus = [s for s in focus_symbols(app) if s[:1].isdigit()]
    try:
        bars = app.market_data()[0]
    except Exception:  # noqa: BLE001 - 일봉이 없으면 관심종목만
        bars = {}
    return list(dict.fromkeys([*focus, *movers(bars, n_movers)]))[:cap]


def collect(engine, symbols: list[str], fetchers: dict | None = None, now: datetime | None = None) -> dict:
    f = {"us": fetch_stocktwits, "kr": fetch_naver, **(fetchers or {})}
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
    # v26: 수집 상태 기록 — 데이터 상태 화면이 '커뮤니티 n종목 · 마지막 성공 · 실패 이유' 를 보여 준다
    st = ops.get_state(engine, "community_status")
    st.update({"at": now.isoformat(), "tried": len(symbols[:25]), "ok": len(done), "failed": failed[:10]})
    if done:
        st["last_ok"] = now.isoformat()
    ops.set_state(engine, "community_status", st)
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


__all__ = ["collect", "for_context", "summarize", "fetch_stocktwits", "fetch_naver_board", "fetch_naver_mobile", "fetch_naver", "slang_score"]

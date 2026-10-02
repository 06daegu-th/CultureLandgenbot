"""대체 데이터 — 가격·뉴스 밖에서 '관심'이 움직이는지.

1. 위키백과 조회수 (Wikimedia 공식 API · 키 불필요): 국내 종목은 한국어판, 미국은 영어판 문서
   최근 7일 평균 vs 이전 60일 → 관심 z점수 · 급증 여부. 조회수 급증은 큰 뉴스·테마의 앞뒤에 나타난다.
2. 네이버 데이터랩 검색어 트렌드 (선택): .env 에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 있으면 종목명 검색량 추이
관심은 방향이 아니다 — 판단 재료에서는 '주목도'로만 쓰고, 급증 + 가격 급등이 겹치면 과열 경고로 쓴다.
하루 한 번, 보유·관심 종목만 (종목당 요청 1~2회).
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta

from ... import ops

log = logging.getLogger(__name__)
UA = {"User-Agent": "quant-ai/1.0 (personal research dashboard)"}


def _get(url: str, headers: dict | None = None, data: bytes | None = None, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})}, method="POST" if data else "GET")  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - https 고정 주소
        return json.loads(r.read())


def wiki_views(title: str, lang: str, end: date, days: int = 90, fetch=None) -> list[tuple[str, int]]:
    start = end - timedelta(days=days)
    t = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{lang}.wikipedia/all-access/user/{t}/daily/"
           f"{start:%Y%m%d}/{end:%Y%m%d}")
    j = (fetch or _get)(url)
    return [(x["timestamp"][:8], int(x.get("views") or 0)) for x in j.get("items", [])]


def naver_trend(keyword: str, end: date, days: int = 90, fetch=None) -> list[tuple[str, float]]:
    cid, sec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and sec):
        return []
    body = json.dumps({"startDate": (end - timedelta(days=days)).isoformat(), "endDate": end.isoformat(), "timeUnit": "date",
                       "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}]}).encode()
    j = (fetch or _get)("https://openapi.naver.com/v1/datalab/search",
                        {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec, "Content-Type": "application/json"}, body)
    res = (j.get("results") or [{}])[0].get("data") or []
    return [(x["period"].replace("-", ""), float(x["ratio"])) for x in res]


def attention(series: list[tuple[str, float]], recent: int = 7) -> dict:
    v = [float(x) for _, x in series]
    if len(v) < recent + 20:
        return {"n": len(v)}
    base, cur = v[:-recent], v[-recent:]
    mu, sd = statistics.fmean(base), statistics.pstdev(base)
    z = (statistics.fmean(cur) - mu) / sd if sd > 0 else 0.0
    return {"n": len(v), "recent_avg": round(statistics.fmean(cur), 1), "base_avg": round(mu, 1), "z": round(z, 2),
            "ratio": round(statistics.fmean(cur) / mu, 2) if mu > 0 else None, "spike": z >= 2.5,
            "last_day": series[-1][0], "series": [round(x, 1) for x in v[-60:]]}


def collect(engine, items: list[tuple[str, str]], fetch=None, now: datetime | None = None, limit: int = 30) -> dict:
    """items: [(종목, 이름)]. 이름으로 위키 문서를 찾는다 (한국 종목 = 한국어판 이름 그대로)."""
    now = now or datetime.now(UTC)
    end = (now - timedelta(days=1)).date()
    done, failed = [], []
    for sym, name in items[:limit]:
        if not name:
            continue
        lang = "ko" if sym[:1].isdigit() else "en"
        out = {"at": now.isoformat(), "symbol": sym, "name": name}
        try:
            out["wiki"] = attention(wiki_views(name, lang, end, fetch=fetch)) | {"lang": lang, "title": name}
        except Exception as e:  # noqa: BLE001
            out["wiki"] = {"error": type(e).__name__}
            failed.append(sym)
        try:
            tr = naver_trend(name, end, fetch=fetch) if lang == "ko" else []
            if tr:
                out["naver"] = attention(tr)
        except Exception as e:  # noqa: BLE001
            out["naver"] = {"error": type(e).__name__}
        ops.set_state(engine, f"alt:{sym}", out)
        done.append(sym)
    return {"collected": len(done), "failed": failed[:10]}


def for_context(engine, symbol: str, max_age: timedelta = timedelta(days=3)) -> dict:
    st = ops.get_state(engine, f"alt:{symbol}")
    if not st.get("at") or datetime.now(UTC) - datetime.fromisoformat(st["at"]) > max_age:
        return {}
    w = st.get("wiki") or {}
    n = st.get("naver") or {}
    out = {}
    if w.get("z") is not None:
        out["wiki_attention_z"] = w["z"]
        out["wiki_spike"] = w.get("spike")
    if n.get("z") is not None:
        out["search_attention_z"] = n["z"]
    return out | ({"note": "관심도(방향 아님) · z ≥ 2.5 = 급증"} if out else {})


__all__ = ["collect", "attention", "wiki_views", "naver_trend", "for_context"]

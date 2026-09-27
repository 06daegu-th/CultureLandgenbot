"""종목 찾기 — 한글 이름·별칭('하이닉스', '삼전', '엔비디아', '애플')으로 국내·해외 종목을 찾는다.

해외 종목은 전략 유니버스(국내 코어)에 섞지 않는다: instruments.market = GLOBAL 로 따로 저장하고,
처음 조회할 때 무료 일봉(Yahoo chart → 실패 시 Stooq CSV)을 받아 DB 에 캐시한다. API 키 불필요.
"""

from __future__ import annotations

import io
import json
import logging
import re
import urllib.request
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import func, select

from .db import session_scope, upsert_bars
from .models import Instrument, PriceBar

log = logging.getLogger(__name__)
GLOBAL_MARKET = "GLOBAL"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

# (티커, 한글 이름, 별칭들) — 많이 찾는 미국 종목·ETF
GLOBAL_STOCKS: list[tuple[str, str, tuple[str, ...]]] = [
    ("NVDA", "엔비디아", ("nvidia", "엔비디아", "엔비")), ("AAPL", "애플", ("apple", "애플", "아이폰")),
    ("MSFT", "마이크로소프트", ("microsoft", "마소", "마이크로소프트")), ("GOOGL", "알파벳(구글)", ("google", "구글", "알파벳", "alphabet")),
    ("AMZN", "아마존", ("amazon", "아마존")), ("META", "메타", ("meta", "메타", "페이스북", "facebook")),
    ("TSLA", "테슬라", ("tesla", "테슬라", "테슬")), ("AVGO", "브로드컴", ("broadcom", "브로드컴")),
    ("TSM", "TSMC", ("tsmc", "대만반도체", "티에스엠씨")), ("AMD", "AMD", ("amd", "에이엠디")),
    ("NFLX", "넷플릭스", ("netflix", "넷플릭스")), ("PLTR", "팔란티어", ("palantir", "팔란티어")),
    ("INTC", "인텔", ("intel", "인텔")), ("QCOM", "퀄컴", ("qualcomm", "퀄컴")), ("MU", "마이크론", ("micron", "마이크론")),
    ("ASML", "ASML", ("asml",)), ("ARM", "ARM", ("arm", "암홀딩스")), ("SMCI", "슈퍼마이크로", ("supermicro", "슈퍼마이크로")),
    ("ORCL", "오라클", ("oracle", "오라클")), ("CRM", "세일즈포스", ("salesforce", "세일즈포스")),
    ("ADBE", "어도비", ("adobe", "어도비")), ("COST", "코스트코", ("costco", "코스트코")), ("WMT", "월마트", ("walmart", "월마트")),
    ("JPM", "JP모건", ("jpmorgan", "제이피모건", "jp모건")), ("V", "비자", ("visa", "비자")), ("MA", "마스터카드", ("mastercard", "마스터카드")),
    ("KO", "코카콜라", ("cocacola", "코카콜라", "코크")), ("PEP", "펩시코", ("pepsi", "펩시")), ("DIS", "디즈니", ("disney", "디즈니")),
    ("NKE", "나이키", ("nike", "나이키")), ("BRK-B", "버크셔 해서웨이", ("berkshire", "버크셔", "버핏")),
    ("LLY", "일라이 릴리", ("eli lilly", "일라이릴리", "릴리")), ("NVO", "노보 노디스크", ("novo", "노보노디스크")),
    ("UNH", "유나이티드헬스", ("unitedhealth", "유나이티드헬스")), ("XOM", "엑슨모빌", ("exxon", "엑슨모빌")),
    ("COIN", "코인베이스", ("coinbase", "코인베이스")), ("MSTR", "스트래티지", ("microstrategy", "마이크로스트래티지", "스트래티지")),
    ("IONQ", "아이온큐", ("ionq", "아이온큐")), ("UBER", "우버", ("uber", "우버")), ("BABA", "알리바바", ("alibaba", "알리바바")),
    ("SPY", "S&P500 ETF (SPY)", ("s&p500", "에스앤피", "snp", "spy")), ("QQQ", "나스닥100 ETF (QQQ)", ("nasdaq", "나스닥", "qqq")),
    ("SOXX", "반도체 ETF (SOXX)", ("반도체etf", "soxx")), ("TQQQ", "나스닥 3배 (TQQQ)", ("tqqq",)),
    ("SCHD", "배당 ETF (SCHD)", ("schd", "슈드")),
]

# 국내 종목 별명 → 정식 이름 (DB 의 이름과 부분일치로도 찾지만 줄임말은 따로)
KR_NICKNAMES = {
    "삼전": "삼성전자", "하이닉스": "SK하이닉스", "하닉": "SK하이닉스", "현차": "현대차", "엘지엔솔": "LG에너지솔루션",
    "엔솔": "LG에너지솔루션", "삼바": "삼성바이오로직스", "셀트": "셀트리온", "포홀": "POSCO홀딩스", "포스코": "POSCO홀딩스",
    "네이버": "NAVER", "카뱅": "카카오뱅크", "한화에어로": "한화에어로스페이스", "현대모비스": "현대모비스",
    "엘지화학": "LG화학", "삼성sdi": "삼성SDI", "sk하이닉스": "SK하이닉스", "엘지전자": "LG전자", "기아차": "기아",
}


def _norm(s: str) -> str:
    return re.sub(r"[\s·().\-]", "", (s or "").lower())


def search(session, query: str, limit: int = 10) -> list[dict]:
    """국내(DB) + 해외(내장 목록) 종목 검색. 점수: 정확 > 앞부분 > 포함 > 별칭."""
    q = _norm(query)
    if not q:
        return []
    target = _norm(KR_NICKNAMES.get(q, KR_NICKNAMES.get(query.strip(), "")))
    out: list[tuple[int, dict]] = []
    for i in session.scalars(select(Instrument).where(Instrument.market != "INDEX")):
        name, sym = _norm(i.name or ""), i.symbol.lower()
        score = (100 if q in (name, sym) or (target and target == name) else 80 if name.startswith(q) or sym.startswith(q)
                 else 60 if q in name else 0)
        if score:
            out.append((score, {"symbol": i.symbol, "name": i.name or i.symbol, "market": i.market,
                                "currency": i.currency}))
    have = {x["symbol"] for _, x in out}
    for sym, name, aliases in GLOBAL_STOCKS:
        if sym in have:
            continue
        keys = [_norm(sym), _norm(name), *[_norm(a) for a in aliases]]
        score = (95 if q in keys else 70 if any(k.startswith(q) for k in keys) and len(q) >= 2
                 else 50 if len(q) >= 2 and any(q in k for k in keys) else 0)
        if score:
            out.append((score, {"symbol": sym, "name": name, "market": GLOBAL_MARKET, "currency": "USD"}))
    out.sort(key=lambda x: (-x[0], len(x[1]["name"])))
    return [x for _, x in out[:limit]]


def find_in_text(session, text: str) -> list[dict]:
    """문장 속 종목 이름 찾기 ('하이닉스 지금 어때?' → SK하이닉스)."""
    t = _norm(text)
    found: dict[str, dict] = {}
    for nick, full in KR_NICKNAMES.items():
        if _norm(nick) in t:
            for x in search(session, full, 1):
                found[x["symbol"]] = x
    codes = set(re.findall(r"(?<!\d)\d{6}(?!\d)", text))  # 종목코드로 물어도 (예: 000660 어때?)
    for i in session.scalars(select(Instrument).where(Instrument.market != "INDEX")):
        n = _norm(i.name or "")
        if (len(n) >= 2 and n in t) or i.symbol in codes:
            found[i.symbol] = {"symbol": i.symbol, "name": i.name, "market": i.market, "currency": i.currency}
    for sym, name, aliases in GLOBAL_STOCKS:
        keys = [_norm(a) for a in aliases if len(_norm(a)) >= 2] + [_norm(name)]
        if any(k and k in t for k in keys) or re.search(rf"\b{re.escape(sym.lower())}\b", text.lower()):
            found.setdefault(sym, {"symbol": sym, "name": name, "market": GLOBAL_MARKET, "currency": "USD"})
    # 짧은 이름이 긴 이름에 포함되는 경우(예: '삼성' ⊂ '삼성전자') 긴 쪽만
    names = {s: _norm(v["name"]) for s, v in found.items()}
    return [v for s, v in found.items() if not any(s != o and names[s] and names[s] in names[o] for o in found)][:5]


# ------------------------------------------------------------------ 해외 일봉 (무료, 키 없음)
def _http(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})  # noqa: S310 - https 고정
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return r.read(8 * 1024 * 1024)


def fetch_yahoo(symbol: str, rng: str = "2y") -> pd.DataFrame:
    raw = json.loads(_http(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1d"
                           "&events=div%2Csplits"))
    res = (raw.get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError(f"Yahoo: {symbol} 결과 없음")
    r = res[0]
    q = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
                       "volume": q["volume"]}, index=pd.to_datetime(r["timestamp"], unit="s", utc=True).normalize())
    if adj:  # 분할·배당 반영 수정주가로 통일
        f = pd.Series(adj, index=df.index) / df["close"]
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * f
    return df.dropna(subset=["close"])


def fetch_stooq(symbol: str) -> pd.DataFrame:
    raw = _http(f"https://stooq.com/q/d/l/?s={symbol.lower().replace('-', '.')}.us&i=d").decode()
    df = pd.read_csv(io.StringIO(raw))
    if df.empty or "Close" not in df:
        raise RuntimeError(f"Stooq: {symbol} 결과 없음")
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.pop("date"), utc=True)
    return df[["open", "high", "low", "close", "volume"]].tail(520)


def ensure_global(engine, symbol: str, name: str | None = None, max_age_hours: float = 12, fetchers=None) -> dict:
    """해외 종목 일봉을 DB 에 캐시 (없거나 오래됐으면 받기). 반환: {ok, rows, source, error}."""
    from .. import ops
    with session_scope(engine) as s:
        last = s.scalar(select(func.max(PriceBar.ts)).where(PriceBar.symbol == symbol, PriceBar.interval == "1d"))
    fetched = ops.get_state(engine, f"global_fetch:{symbol}").get("at")
    if last is not None and fetched and datetime.fromisoformat(fetched) > datetime.now(UTC) - timedelta(hours=max_age_hours):
        return {"ok": True, "rows": 0, "source": "cache"}
    errors = []
    for src, fn in (fetchers or (("yahoo", fetch_yahoo), ("stooq", fetch_stooq))):
        try:
            df = fn(symbol)
            if len(df) < 5:
                raise RuntimeError("일봉이 너무 적음")
            with session_scope(engine) as s:
                n = upsert_bars(s, symbol, df, "1d", src)
                if s.scalar(select(Instrument.id).where(Instrument.symbol == symbol)) is None:
                    s.add(Instrument(symbol=symbol, market=GLOBAL_MARKET, name=name or global_name(symbol) or symbol,
                                     currency="USD"))
            ops.set_state(engine, f"global_fetch:{symbol}", {"at": datetime.now(UTC).isoformat(), "source": src})
            return {"ok": True, "rows": n, "source": src}
        except Exception as e:  # noqa: BLE001 - 다음 소스로
            errors.append(f"{src}: {e}"[:160])
    return {"ok": last is not None, "rows": 0, "source": "stale-cache" if last is not None else None,
            "error": "; ".join(errors)}


def global_name(symbol: str) -> str | None:
    return next((n for s, n, _ in GLOBAL_STOCKS if s == symbol), None)


__all__ = ["GLOBAL_MARKET", "GLOBAL_STOCKS", "search", "find_in_text", "ensure_global", "global_name"]

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
# v19: 영문 회사명 → 국내 종목코드 ('Samsung Electronics' · 'SK Hynix' 로 검색해도 같은 종목)
KR_ENGLISH = {
    "samsung electronics": "005930", "samsung": "005930", "samsung elec": "005930", "sk hynix": "000660", "hynix": "000660",
    "lg energy solution": "373220", "lges": "373220", "samsung biologics": "207940", "hyundai motor": "005380", "hyundai": "005380",
    "kia": "000270", "celltrion": "068270", "posco holdings": "005490", "posco": "005490", "naver": "035420", "kakao": "035720",
    "lg chem": "051910", "samsung sdi": "006400", "kb financial": "105560", "shinhan financial": "055550", "hyundai mobis": "012330",
    "samsung c&t": "028260", "lg electronics": "066570", "sk innovation": "096770", "sk telecom": "017670", "kt": "030200",
    "kepco": "015760", "korea electric power": "015760", "samsung life": "032830", "hana financial": "086790", "woori financial": "316140",
    "kt&g": "033780", "samsung electro-mechanics": "009150", "samsung sds": "018260", "korea zinc": "010130", "hmm": "011200",
    "krafton": "259960", "kakaobank": "323410", "kakao bank": "323410", "kakaopay": "377300", "ncsoft": "036570", "s-oil": "010950",
    "hanwha aerospace": "012450", "hanwha ocean": "042660", "samsung fire": "000810", "lg display": "034220", "lg innotek": "011070",
    "korean air": "003490", "hybe": "352820", "ecopro": "086520", "ecopro bm": "247540", "alteogen": "196170", "hanmi semiconductor": "042700",
    "hd hyundai": "267250", "hd hyundai heavy": "329180", "hyundai rotem": "064350", "doosan enerbility": "034020", "lg corp": "003550",
    "sk square": "402340", "sk hynix inc": "000660", "samsung heavy": "010140", "mirae asset": "006800", "coway": "021240",
}


def _norm(s: str) -> str:
    return re.sub(r"[\s·().\-]", "", (s or "").lower())


def search(session, query: str, limit: int = 10) -> list[dict]:
    """국내(DB) + 해외(내장 목록) 종목 검색. 점수: 정확 > 앞부분 > 포함 > 별칭."""
    q = _norm(query)
    if not q:
        return []
    target = _norm(KR_NICKNAMES.get(q, KR_NICKNAMES.get(query.strip(), "")))
    eng = {_norm(k): v for k, v in KR_ENGLISH.items()}
    code = eng.get(q)
    eng_part = {v for k, v in eng.items() if len(q) >= 3 and k.startswith(q)}
    out: list[tuple[int, dict]] = []
    for i in session.scalars(select(Instrument).where(Instrument.market != "INDEX")):
        name, sym = _norm(i.name or ""), i.symbol.lower()
        score = (100 if q in (name, sym) or (target and target == name) or code == i.symbol else 80 if name.startswith(q) or sym.startswith(q)
                 else 75 if i.symbol in eng_part else 60 if q in name else 0)
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
# 소스 순서: yfinance(브라우저 흉내 → Yahoo 429 차단 회피) → Yahoo chart(query1/2) → Nasdaq → Stooq.
# 실패하면 30분 동안 같은 종목을 다시 두드리지 않는다 (차단을 더 오래 만들지 않도록).
FAIL_COOLDOWN = timedelta(minutes=30)
ETF_SYMBOLS = {"SPY", "QQQ", "SOXX", "TQQQ", "SCHD"}


def _http(url: str, timeout: float = 15.0, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - https 고정
        return r.read(8 * 1024 * 1024)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df[[c for c in ("open", "high", "low", "close", "volume") if c in df]].apply(pd.to_numeric, errors="coerce")
    idx = pd.DatetimeIndex(df.index)
    df.index = (idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=["close"])


def _yf_frame(raw: pd.DataFrame, symbol: str | None = None) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        lv0 = raw.columns.get_level_values(0)
        raw = raw[symbol] if symbol in set(lv0) else raw.xs(symbol, axis=1, level=1)
    raw = raw.rename(columns=str.lower)
    return _clean(raw)


def fetch_yfinance(symbol: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError("yfinance 미설치 (./run.sh 가 자동 설치)") from e
    raw = yf.download(symbol, period="2y", interval="1d", auto_adjust=True, progress=False, threads=False)
    if raw is None or raw.empty:
        raise RuntimeError(f"yfinance: {symbol} 결과 없음")
    return _yf_frame(raw, symbol)


def fetch_yfinance_many(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """여러 종목을 한 번에 (요청 수를 줄여 429 차단을 피한다)."""
    import yfinance as yf
    raw = yf.download(symbols, period="2y", interval="1d", auto_adjust=True, progress=False, threads=True,
                      group_by="ticker")
    out = {}
    for sym in symbols:
        try:
            df = _yf_frame(raw, sym)
            if len(df) >= 5:
                out[sym] = df
        except (KeyError, ValueError):
            continue
    return out


def fetch_yahoo(symbol: str, rng: str = "2y") -> pd.DataFrame:
    errors = []
    for host in ("query1", "query2"):
        try:
            raw = json.loads(_http(f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}"
                                   "&interval=1d&events=div%2Csplits"))
            break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{host}: {e}")
    else:
        raise RuntimeError("; ".join(errors))
    res = (raw.get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError(f"Yahoo: {symbol} 결과 없음")
    r = res[0]
    q = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
                       "volume": q["volume"]}, index=pd.to_datetime(r["timestamp"], unit="s", utc=True))
    if adj:  # 분할·배당 반영 수정주가로 통일
        f = pd.Series(adj, index=df.index) / df["close"]
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * f
    return _clean(df)


def _num(x) -> float | None:
    try:
        return float(str(x).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def fetch_nasdaq(symbol: str) -> pd.DataFrame:
    """Nasdaq 공개 API (키 없음). 분할 반영 가격."""
    end = datetime.now(UTC).date()
    cls = "etf" if symbol in ETF_SYMBOLS else "stocks"
    url = (f"https://api.nasdaq.com/api/quote/{symbol.replace('-', '.')}/historical?assetclass={cls}"
           f"&fromdate={end - timedelta(days=740)}&limit=9999&todate={end}")
    raw = json.loads(_http(url, headers={"Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com",
                                         "Referer": "https://www.nasdaq.com/"}))
    rows = ((((raw or {}).get("data") or {}).get("tradesTable") or {}).get("rows")) or []
    if not rows:
        raise RuntimeError(f"Nasdaq: {symbol} 결과 없음")
    df = pd.DataFrame([{"date": r.get("date"), "open": _num(r.get("open")), "high": _num(r.get("high")),
                        "low": _num(r.get("low")), "close": _num(r.get("close")), "volume": _num(r.get("volume"))}
                       for r in rows])
    df.index = pd.to_datetime(df.pop("date"), format="%m/%d/%Y", utc=True)
    return _clean(df)


def fetch_stooq(symbol: str) -> pd.DataFrame:
    raw = _http(f"https://stooq.com/q/d/l/?s={symbol.lower().replace('-', '.')}.us&i=d").decode(errors="replace")
    if not raw.lower().startswith("date"):
        raise RuntimeError(f"Stooq: {symbol} 결과 없음")
    df = pd.read_csv(io.StringIO(raw))
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.pop("date"), utc=True)
    return _clean(df).tail(520)


DEFAULT_FETCHERS = (("yfinance", fetch_yfinance), ("yahoo", fetch_yahoo), ("nasdaq", fetch_nasdaq), ("stooq", fetch_stooq))


def _save(engine, symbol: str, df: pd.DataFrame, src: str, name: str | None) -> int:
    from .. import ops
    with session_scope(engine) as s:
        n = upsert_bars(s, symbol, df, "1d", src)
        if s.scalar(select(Instrument.id).where(Instrument.symbol == symbol)) is None:
            s.add(Instrument(symbol=symbol, market=GLOBAL_MARKET, name=name or global_name(symbol) or symbol, currency="USD"))
    ops.set_state(engine, f"global_fetch:{symbol}", {"at": datetime.now(UTC).isoformat(), "source": src})
    return n


def ensure_global(engine, symbol: str, name: str | None = None, max_age_hours: float = 12, fetchers=None,
                  force: bool = False) -> dict:
    """해외 종목 일봉을 DB 에 캐시 (없거나 오래됐으면 받기). 반환: {ok, rows, source, error}."""
    from .. import ops
    with session_scope(engine) as s:
        last = s.scalar(select(func.max(PriceBar.ts)).where(PriceBar.symbol == symbol, PriceBar.interval == "1d"))
    st = ops.get_state(engine, f"global_fetch:{symbol}")
    now = datetime.now(UTC)
    if not force and last is not None and st.get("at") and datetime.fromisoformat(st["at"]) > now - timedelta(hours=max_age_hours):
        return {"ok": True, "rows": 0, "source": "cache"}
    if not force and fetchers is None and st.get("fail_at") and datetime.fromisoformat(st["fail_at"]) > now - FAIL_COOLDOWN:
        return {"ok": last is not None, "rows": 0, "source": "stale-cache" if last is not None else None,
                "error": f"최근 실패로 잠시 대기 (30분): {st.get('error', '')}"[:300]}
    errors = []
    for src, fn in (fetchers or DEFAULT_FETCHERS):
        try:
            df = fn(symbol)
            if len(df) < 5:
                raise RuntimeError("일봉이 너무 적음")
            return {"ok": True, "rows": _save(engine, symbol, df, src, name), "source": src}
        except Exception as e:  # noqa: BLE001 - 다음 소스로
            errors.append(f"{src}: {e}"[:160])
    err = "; ".join(errors)
    ops.set_state(engine, f"global_fetch:{symbol}", {**st, "fail_at": now.isoformat(), "error": err[:500]})
    return {"ok": last is not None, "rows": 0, "source": "stale-cache" if last is not None else None, "error": err}


def ensure_many(engine, symbols: list[str], max_age_hours: float = 12) -> dict:
    """여러 종목: 먼저 yfinance 한 번에 받고, 빠진 것만 종목별 폴백 (요청 수 최소화)."""
    import time as _time

    from .. import ops
    now = datetime.now(UTC)
    stale = []
    for sym in symbols:
        st = ops.get_state(engine, f"global_fetch:{sym}")
        if not (st.get("at") and datetime.fromisoformat(st["at"]) > now - timedelta(hours=max_age_hours)):
            stale.append(sym)
    got: dict[str, str] = {}
    if stale:
        try:
            for sym, df in fetch_yfinance_many(stale).items():
                _save(engine, sym, df, "yfinance", None)
                got[sym] = "yfinance"
        except Exception as e:  # noqa: BLE001 - 미설치·차단 → 종목별 폴백
            log.info("yfinance 일괄 받기 실패: %s", e)
    failed = []
    for sym in stale:
        if sym in got:
            continue
        r = ensure_global(engine, sym, fetchers=DEFAULT_FETCHERS[1:])  # yfinance 는 위에서 이미 시도
        if r.get("ok"):
            got[sym] = r.get("source") or "cache"
        else:
            failed.append(sym)
        _time.sleep(0.4)  # 소스별 분당 한도 보호
    return {"fresh": len(symbols) - len(stale), "fetched": len(got), "failed": failed}


def global_name(symbol: str) -> str | None:
    return next((n for s, n, _ in GLOBAL_STOCKS if s == symbol), None)


__all__ = ["GLOBAL_MARKET", "GLOBAL_STOCKS", "search", "find_in_text", "ensure_global", "global_name"]

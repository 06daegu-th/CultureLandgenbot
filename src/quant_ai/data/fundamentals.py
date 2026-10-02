"""종목 상세 — 투자 전에 보는 것들: 다가오는 일정(실적 발표·배당), 핵심 지표, 애널리스트 전망, 실적·재무, 기업 정보, 뉴스.

무료 소스만 쓴다 (키 불필요):
  · 해외: yfinance(Yahoo) → 막히면 Nasdaq 공개 API (실적 발표일·요약 지표)
  · 국내: 네이버 증권(시총·PER·PBR·배당·컨센서스·증권사 리포트) + Yahoo(.KS/.KQ, 기업 정보·분기 실적)
          + DART 공시(IR·배당·주총) + 분기보고서 법정 제출 기한
결과는 ops 상태에 6시간 캐시하고, 실패한 소스는 30분 동안 다시 부르지 않는다.
D-day 는 캐시와 무관하게 요청 시각(KST) 기준으로 매번 다시 계산한다.
"""

from __future__ import annotations

import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from datetime import UTC, date, datetime, timedelta

import pandas as pd

log = logging.getLogger(__name__)
KST = timedelta(hours=9)
PROFILE_TTL = timedelta(hours=6)
FAIL_COOLDOWN = timedelta(minutes=30)
FETCH_TIMEOUT = 25.0


# ------------------------------------------------------------------ 작은 도우미
def _fnum(x) -> float | None:
    """숫자 · '1,234' · '$5.6' · '13.9배' · '2.1%' → float (퍼센트 기호는 떼기만 한다)."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int | float):
        return None if (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else float(x)
    s = re.sub(r"[,$₩원배%\s]", "", str(x))
    if s in ("", "-", "N/A", "NA", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _day(x) -> date | None:
    """epoch 초 · datetime · date · 'YYYY-MM-DD' · 'MM/DD/YYYY' · 'Nov 19, 2025' → date."""
    if x is None:
        return None
    try:
        if isinstance(x, datetime):
            return (x.astimezone(UTC) + KST).date() if x.tzinfo else x.date()
        if isinstance(x, date):
            return x
        if isinstance(x, int | float):
            if isinstance(x, float) and math.isnan(x):
                return None
            return (datetime.fromtimestamp(float(x), UTC) + KST).date()
        if isinstance(x, pd.Timestamp):
            return _day(x.to_pydatetime())
        s = str(x).strip()
        for cand, fmt in ((s, "%b %d, %Y"), (s, "%m/%d/%Y"), (s[:10], "%Y-%m-%d"), (s[:10], "%Y.%m.%d"), (s[:8], "%Y%m%d")):
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
        return pd.Timestamp(s).date()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def today_kst(now: datetime | None = None) -> date:
    return ((now or datetime.now(UTC)).astimezone(UTC) + KST).date()


def kr_amount(text) -> float | None:
    """'423조 8,469억' · '1,234억' · '5,103원' → 원 단위 숫자."""
    if text is None:
        return None
    s = str(text).replace(",", "").replace(" ", "")
    total, found = 0.0, False
    for unit, mul in (("조", 1e12), ("억", 1e8), ("만", 1e4)):
        m = re.search(r"([\d.]+)" + unit, s)
        if m:
            total += float(m.group(1)) * mul
            found = True
    return total if found else _fnum(s)


def is_kr(symbol: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", symbol or ""))


# ------------------------------------------------------------------ 소스 1: yfinance
def fetch_yf(yf_symbol: str) -> dict:
    """Ticker 의 여러 조각을 받는다. 조각별로 실패해도 나머지는 쓴다."""
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError("yfinance 미설치 (./run.sh 가 자동 설치)") from e
    t = yf.Ticker(yf_symbol)
    out: dict = {"errors": []}
    parts = {
        "info": lambda: t.info,
        "calendar": lambda: t.calendar,
        "earnings_dates": lambda: t.get_earnings_dates(limit=12),
        "quarterly": lambda: t.quarterly_income_stmt,
        "news": lambda: t.news,
        "recommendations": lambda: t.recommendations,
    }
    for k, fn in parts.items():
        try:
            out[k] = fn()
        except Exception as e:  # noqa: BLE001 - 조각 하나가 막혀도 계속
            out["errors"].append(f"{k}: {str(e)[:120]}")
            if "Too Many Requests" in str(e) or "429" in str(e):
                break  # 한도 초과면 더 두드리지 않는다
    info = out.get("info") or {}
    if not any(out.get(k) is not None and not _empty(out.get(k)) for k in parts) or (
            isinstance(info, dict) and len(info) <= 3 and _empty(out.get("calendar"))):
        raise RuntimeError("Yahoo: 결과 없음 — " + "; ".join(out["errors"])[:200])
    return out


def _empty(x) -> bool:
    if x is None:
        return True
    if isinstance(x, pd.DataFrame | pd.Series):
        return x.empty
    if isinstance(x, dict | list | tuple):
        return len(x) == 0
    return False


# ------------------------------------------------------------------ 소스 2: Nasdaq (미국, Yahoo 가 막힐 때)
def fetch_nasdaq_profile(symbol: str) -> dict:
    from .global_stocks import ETF_SYMBOLS, _http
    h = {"Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com",
         "Referer": "https://www.nasdaq.com/"}
    sym = symbol.replace("-", ".")
    cls = "etf" if symbol in ETF_SYMBOLS else "stocks"
    out: dict = {"errors": []}
    try:
        out["summary"] = ((json.loads(_http(f"https://api.nasdaq.com/api/quote/{sym}/summary?assetclass={cls}",
                                            headers=h)) or {}).get("data") or {}).get("summaryData") or {}
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"summary: {str(e)[:120]}")
    if cls == "stocks":
        try:
            out["earnings"] = (json.loads(_http(f"https://api.nasdaq.com/api/analyst/{sym}/earnings-date",
                                                headers=h)) or {}).get("data") or {}
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"earnings: {str(e)[:120]}")
    if not out.get("summary") and not out.get("earnings"):
        raise RuntimeError("Nasdaq: 결과 없음 — " + "; ".join(out["errors"])[:200])
    return out


# ------------------------------------------------------------------ 소스 3: 네이버 증권 (국내)
def fetch_naver(code: str) -> dict:
    from .global_stocks import _http
    raw = json.loads(_http(f"https://m.stock.naver.com/api/stock/{code}/integration",
                           headers={"Referer": "https://m.stock.naver.com/"}))
    if not raw or not (raw.get("totalInfos") or raw.get("consensusInfo")):
        raise RuntimeError(f"네이버: {code} 결과 없음")
    return raw


DEFAULT_FETCHERS = {"yfinance": fetch_yf, "nasdaq": fetch_nasdaq_profile, "naver": fetch_naver}


# ------------------------------------------------------------------ 정규화
REC_KO = {"strong_buy": "강력 매수", "buy": "매수", "hold": "보유(중립)", "underperform": "비중 축소",
          "sell": "매도", "none": "-"}


def _kr_rating(mean: float | None) -> str | None:
    """FnGuide/네이버 컨센서스: 5 강력매수 · 4 매수 · 3 중립 · 2 매도 · 1 강력매도."""
    if mean is None:
        return None
    return ("강력 매수" if mean >= 4.5 else "매수" if mean >= 3.5 else "중립" if mean >= 2.5
            else "매도" if mean >= 1.5 else "강력 매도")


def _from_info(info: dict, p: dict) -> None:
    g = info.get
    price = _fnum(g("currentPrice")) or _fnum(g("regularMarketPrice"))
    div_rate = _fnum(g("dividendRate")) or _fnum(g("trailingAnnualDividendRate"))
    # dividendYield 는 yfinance 버전마다 단위(분수/퍼센트)가 달라 배당금÷가격으로 직접 계산한다
    div_yield = (div_rate / price) if div_rate and price else _fnum(g("trailingAnnualDividendYield"))
    dte = _fnum(g("debtToEquity"))
    stats = {
        "price": price, "market_cap": _fnum(g("marketCap")), "per": _fnum(g("trailingPE")),
        "fwd_per": _fnum(g("forwardPE")), "pbr": _fnum(g("priceToBook")), "eps": _fnum(g("trailingEps")),
        "fwd_eps": _fnum(g("forwardEps")), "div_rate": div_rate, "div_yield": div_yield,
        "payout": _fnum(g("payoutRatio")), "beta": _fnum(g("beta")),
        "high52": _fnum(g("fiftyTwoWeekHigh")), "low52": _fnum(g("fiftyTwoWeekLow")),
        "avg_volume": _fnum(g("averageVolume")), "shares": _fnum(g("sharesOutstanding")),
        "revenue": _fnum(g("totalRevenue")), "gross_margin": _fnum(g("grossMargins")),
        "op_margin": _fnum(g("operatingMargins")), "profit_margin": _fnum(g("profitMargins")),
        "roe": _fnum(g("returnOnEquity")), "debt_to_equity": dte / 100 if dte is not None else None,
        "rev_growth": _fnum(g("revenueGrowth")), "earn_growth": _fnum(g("earningsGrowth")),
        "fcf": _fnum(g("freeCashflow")), "cash": _fnum(g("totalCash")), "debt": _fnum(g("totalDebt")),
        "insiders": _fnum(g("heldPercentInsiders")), "institutions": _fnum(g("heldPercentInstitutions")),
        "short_float": _fnum(g("shortPercentOfFloat")), "peg": _fnum(g("trailingPegRatio")),
    }
    for k, v in stats.items():
        if v is not None and p["stats"].get(k) is None:
            p["stats"][k] = v
    comp = {"name": g("longName") or g("shortName"), "sector": g("sector"), "industry": g("industry"),
            "employees": g("fullTimeEmployees"), "website": g("website"), "country": g("country"),
            "city": g("city"), "summary": g("longBusinessSummary"), "exchange": g("fullExchangeName") or g("exchange"),
            "quote_type": g("quoteType")}
    for k, v in comp.items():
        if v and not p["company"].get(k):
            p["company"][k] = v
    if g("currency") and not p.get("currency"):
        p["currency"] = g("currency")
    a = p["analyst"]
    for k, src in (("target_mean", "targetMeanPrice"), ("target_high", "targetHighPrice"),
                   ("target_low", "targetLowPrice"), ("target_median", "targetMedianPrice"),
                   ("n", "numberOfAnalystOpinions"), ("rec_mean", "recommendationMean")):
        if a.get(k) is None and _fnum(g(src)) is not None:
            a[k] = _fnum(g(src))
    if g("recommendationKey") and not a.get("rating"):
        a["rating"] = REC_KO.get(g("recommendationKey"), g("recommendationKey"))
        a["scale"] = "Yahoo 평균 1(강력 매수) ~ 5(매도)"
    # 실적 발표일 (calendar 가 비었을 때의 보조)
    ts = g("earningsTimestampStart") or g("earningsTimestamp")
    if ts:
        te = g("earningsTimestampEnd")
        p["_events"].append({"kind": "earnings", "label": "실적 발표", "date": _day(ts),
                             "date_end": _day(te) if te and _day(te) != _day(ts) else None,
                             "estimated": bool(g("isEarningsDateEstimate")), "source": "Yahoo"})
    for k, lbl in (("exDividendDate", "배당락일"), ("dividendDate", "배당 지급일")):
        if g(k):
            p["_events"].append({"kind": "ex_div" if k == "exDividendDate" else "div_pay", "label": lbl,
                                 "date": _day(g(k)), "source": "Yahoo",
                                 "detail": f"주당 {div_rate:g}(연간)" if div_rate else None})


def _from_calendar(cal, p: dict, cur: str) -> None:
    if isinstance(cal, pd.DataFrame):  # 옛 yfinance: 행=항목, 열=값
        cal = {k: (v.dropna().tolist() if len(v.dropna()) > 1 else (v.dropna().iloc[0] if len(v.dropna()) else None))
               for k, v in cal.T.items()} if not cal.empty else {}
    if not isinstance(cal, dict):
        return
    ed = cal.get("Earnings Date")
    eds = [d for d in (ed if isinstance(ed, list | tuple) else [ed]) if d is not None]
    days = sorted({x for x in (_day(d) for d in eds) if x})
    if days:
        det = []
        if _fnum(cal.get("Earnings Average")) is not None:
            det.append(f"EPS 예상 {_fnum(cal.get('Earnings Average')):,.2f}"
                       + (f" ({_fnum(cal.get('Earnings Low')):,.2f}~{_fnum(cal.get('Earnings High')):,.2f})"
                          if _fnum(cal.get("Earnings Low")) is not None and _fnum(cal.get("Earnings High")) is not None else ""))
        if _fnum(cal.get("Revenue Average")) is not None:
            det.append(f"매출 예상 {money_short(_fnum(cal.get('Revenue Average')), cur)}")
        p["_events"].append({"kind": "earnings", "label": "실적 발표", "date": days[0],
                             "date_end": days[-1] if len(days) > 1 else None,
                             "estimated": len(days) > 1, "detail": " · ".join(det) or None, "source": "Yahoo",
                             "eps_estimate": _fnum(cal.get("Earnings Average")),
                             "revenue_estimate": _fnum(cal.get("Revenue Average"))})
    for k, kind, lbl in (("Ex-Dividend Date", "ex_div", "배당락일"), ("Dividend Date", "div_pay", "배당 지급일")):
        if cal.get(k):
            p["_events"].append({"kind": kind, "label": lbl, "date": _day(cal.get(k)), "source": "Yahoo"})


def _from_earnings_dates(df, p: dict) -> None:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return
    cols = {c.lower(): c for c in df.columns}
    est = cols.get("eps estimate")
    act = cols.get("reported eps")
    sur = next((c for k, c in cols.items() if k.startswith("surprise")), None)
    today = today_kst()
    hist = []
    for ts, row in df.iterrows():
        d = _day(ts)
        if d is None:
            continue
        a = _fnum(row.get(act)) if act else None
        e = _fnum(row.get(est)) if est else None
        if a is None:
            if d >= today:
                p["_events"].append({"kind": "earnings", "label": "실적 발표", "date": d, "source": "Yahoo",
                                     "eps_estimate": e, "detail": f"EPS 예상 {e:,.2f}" if e is not None else None,
                                     "time": _session_of(ts)})
            continue
        s = _fnum(row.get(sur)) if sur else None
        if s is None and e:
            s = (a / e - 1) * 100
        hist.append({"date": d.isoformat(), "eps_estimate": e, "eps_actual": a,
                     "surprise_pct": round(s, 2) if s is not None else None,
                     "beat": (a >= e) if e is not None else None})
    hist.sort(key=lambda x: x["date"])
    if hist:
        p["earnings_history"] = hist[-8:]


def _session_of(ts) -> str | None:
    """미국 동부시간 발표 시각 → 장 시작 전 / 장 마감 후."""
    try:
        t = pd.Timestamp(ts)
        if t.tz is None:
            return None
        h = t.tz_convert("America/New_York").hour
        return "장 시작 전" if h < 10 else "장 마감 후" if h >= 16 else "장중"
    except (ValueError, TypeError):
        return None


QROWS = {"revenue": ("Total Revenue", "Operating Revenue"), "op_income": ("Operating Income",),
         "net_income": ("Net Income", "Net Income Common Stockholders"), "eps": ("Diluted EPS", "Basic EPS")}


def _from_quarterly(df, p: dict) -> None:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return
    rows = []
    for col in sorted(df.columns, key=lambda c: pd.Timestamp(c)):
        r = {"period": pd.Timestamp(col).strftime("%Y-%m")}
        for k, names in QROWS.items():
            r[k] = next((_fnum(df.at[n, col]) for n in names if n in df.index and _fnum(df.at[n, col]) is not None), None)
        if any(r[k] is not None for k in QROWS):
            rows.append(r)
    if rows:
        p["quarterly"] = rows[-6:]


def _from_recs(df, p: dict) -> None:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return
    row = df[df["period"] == "0m"].iloc[0] if "period" in df and (df["period"] == "0m").any() else df.iloc[0]
    trend = {k: int(_fnum(row.get(k)) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
    if sum(trend.values()):
        p["analyst"]["distribution"] = trend


def _from_yf_news(items, p: dict) -> None:
    for it in (items or [])[:12]:
        if not isinstance(it, dict):
            continue
        c = it.get("content") if isinstance(it.get("content"), dict) else it
        title = c.get("title")
        if not title:
            continue
        url = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else None) \
            or ((c.get("clickThroughUrl") or {}).get("url") if isinstance(c.get("clickThroughUrl"), dict) else None) \
            or c.get("link")
        src = ((c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else None) \
            or c.get("publisher")
        ts = c.get("pubDate") or c.get("displayTime") or c.get("providerPublishTime")
        if isinstance(ts, int | float):
            ts = datetime.fromtimestamp(ts, UTC).isoformat()
        p["news"].append({"title": title, "url": url, "source": src or "Yahoo", "ts": ts,
                          "summary": (c.get("summary") or "")[:300] or None})


def _from_nasdaq(raw: dict, p: dict) -> None:
    sd = raw.get("summary") or {}

    def v(k):
        x = sd.get(k)
        return x.get("value") if isinstance(x, dict) else x
    stats = {"market_cap": _fnum(v("MarketCap")), "per": _fnum(v("PERatio")), "fwd_per": _fnum(v("ForwardPE1Yr")),
             "eps": _fnum(v("EarningsPerShare")), "div_rate": _fnum(v("AnnualizedDividend")),
             "avg_volume": _fnum(v("AverageVolume"))}
    y = _fnum(v("Yield"))
    if y is not None:
        stats["div_yield"] = y / 100
    hl = str(v("FiftTwoWeekHighLow") or v("FiftyTwoWeekHighLow") or "")
    if "/" in hl:
        hi, lo = (_fnum(x) for x in hl.split("/", 1))
        stats["high52"], stats["low52"] = hi, lo
    for k, x in stats.items():
        if x is not None and p["stats"].get(k) is None:
            p["stats"][k] = x
    if _fnum(v("OneYrTarget")) is not None and p["analyst"].get("target_mean") is None:
        p["analyst"]["target_mean"] = _fnum(v("OneYrTarget"))
    for k in ("sector", "industry"):
        if v(k.capitalize()) and not p["company"].get(k):
            p["company"][k] = v(k.capitalize())
    for k, kind, lbl in (("ExDividendDate", "ex_div", "배당락일"), ("DividendPaymentDate", "div_pay", "배당 지급일")):
        if _day(v(k)):
            p["_events"].append({"kind": kind, "label": lbl, "date": _day(v(k)), "source": "Nasdaq"})
    e = raw.get("earnings") or {}
    text = " ".join(str(e.get(k) or "") for k in ("reportText", "announcement"))
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text) or re.search(r"([A-Z][a-z]{2} \d{1,2}, \d{4})", text)
    if m and _day(m.group(1)):
        tl = text.lower()
        when = "장 마감 후" if "after market close" in tl else "장 시작 전" if "before market open" in tl else None
        q = re.search(r"fiscal quarter ending ([A-Za-z]+ \d{4})", text, re.I)
        p["_events"].append({"kind": "earnings", "label": "실적 발표", "date": _day(m.group(1)), "time": when,
                             "estimated": "estimated" in tl or "*" in str(e.get("announcement") or ""),
                             "source": "Nasdaq", "detail": f"{q.group(1)} 분기" if q else None})


NAVER_SKIP = {"lastClosePrice", "openPrice", "highPrice", "lowPrice", "accumulatedTradingVolume",
              "accumulatedTradingValue"}


def _from_naver(raw: dict, p: dict) -> None:
    rows = []
    for it in raw.get("totalInfos") or []:
        code, key, val = it.get("code"), it.get("key"), it.get("value")
        if not key or val in (None, "", "-") or code in NAVER_SKIP:
            continue
        rows.append({"label": key, "value": str(val)})
        num = {"marketValue": ("market_cap", kr_amount(val)), "per": ("per", _fnum(val)),
               "cnsPer": ("fwd_per", _fnum(val)), "pbr": ("pbr", _fnum(val)), "eps": ("eps", _fnum(val)),
               "cnsEps": ("fwd_eps", _fnum(val)), "bps": ("bps", _fnum(val)),
               "dividendYieldRatio": ("div_yield", (_fnum(val) or 0) / 100 if _fnum(val) is not None else None),
               "dividend": ("div_rate", _fnum(val)), "highPriceOf52Weeks": ("high52", _fnum(val)),
               "lowPriceOf52Weeks": ("low52", _fnum(val)),
               "foreignRate": ("foreign_rate", (_fnum(val) or 0) / 100 if _fnum(val) is not None else None)}.get(code)
        if num and num[1] is not None:
            p["stats"][num[0]] = num[1]  # 국내는 네이버 값이 우선
    if rows:
        p["stats_text"] = rows
    ci = raw.get("consensusInfo") or {}
    tm, rm = _fnum(ci.get("priceTargetMean")), _fnum(ci.get("recommMean"))
    if tm:
        p["analyst"]["target_mean"] = tm
    if rm:
        p["analyst"]["rec_mean"] = rm
        p["analyst"]["rating"] = _kr_rating(rm)
        p["analyst"]["scale"] = "국내 컨센서스 1(강력 매도) ~ 5(강력 매수)"
    if ci.get("createDate"):
        p["analyst"]["as_of"] = str(ci.get("createDate"))[:10]
    reps = []
    for r in (raw.get("researches") or [])[:8]:
        t = r.get("tit") or r.get("title")
        if t:
            reps.append({"title": t, "broker": r.get("bnm") or r.get("brokerName"),
                         "date": str(r.get("wdt") or r.get("writeDate") or "")[:10] or None})
    if reps:
        p["analyst"]["reports"] = reps
    if raw.get("stockName") and not p["company"].get("name"):
        p["company"]["name"] = raw.get("stockName")


def money_short(v: float | None, cur: str = "USD") -> str:
    if v is None:
        return "-"
    if cur == "KRW":
        return f"{v / 1e12:,.1f}조원" if abs(v) >= 1e12 else f"{v / 1e8:,.0f}억원"
    sign = "$" if cur == "USD" else ""
    for d, u in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= d:
            return f"{sign}{v / d:,.2f}{u}"
    return f"{sign}{v:,.0f}"


# ------------------------------------------------------------------ 국내: 공시 · 법정 기한
def kr_report_deadline(today: date) -> dict:
    """다음 정기보고서 법정 제출 기한 (분기·반기 45일, 사업보고서 90일). 잠정실적은 보통 그 전에 나온다."""
    y = today.year
    cands = [(date(y, 3, 31), "1분기", 45), (date(y, 6, 30), "반기(2분기)", 45), (date(y, 9, 30), "3분기", 45),
             (date(y - 1, 12, 31), f"{y - 1}년 사업(4분기)", 90), (date(y, 12, 31), f"{y}년 사업(4분기)", 90)]
    nxt = min(((q + timedelta(days=d), name, q) for q, name, d in cands if q + timedelta(days=d) >= today),
              key=lambda x: x[0])
    due, name, qend = nxt
    return {"kind": "earnings", "label": f"{name} 실적 공시 기한", "date": due, "estimated": True,
            "source": "자본시장법", "date_start": max(qend + timedelta(days=1), today),
            "detail": "법정 제출 기한 — 대형주는 보통 이보다 먼저 잠정실적을 공시합니다"}


KR_EVENT_WORDS = (("기업설명회", "ir", "기업설명회(IR)"), ("주주총회", "agm", "주주총회"),
                  ("배당", "dividend", "배당 결정"), ("잠정", "earnings_prelim", "잠정실적 공시"),
                  ("분기보고서", "report", "분기보고서"), ("반기보고서", "report", "반기보고서"),
                  ("사업보고서", "report", "사업보고서"), ("유상증자", "offering", "유상증자"),
                  ("자기주식", "buyback", "자사주"))


def kr_disclosure_events(session, symbol: str, days: int = 45) -> list[dict]:
    from sqlalchemy import select

    from .models import Disclosure
    since = today_kst() - timedelta(days=days)
    out = []
    for d in session.scalars(select(Disclosure).where(Disclosure.symbol == symbol, Disclosure.filed_at >= since)
                             .order_by(Disclosure.filed_at.desc()).limit(40)):
        hit = next(((k, lbl) for w, k, lbl in KR_EVENT_WORDS if w in (d.title or "")), None)
        if hit:
            out.append({"kind": hit[0], "label": hit[1], "date": d.filed_at, "source": "DART", "filed": True,
                        "detail": d.title, "url": d.url})
    return out


# ------------------------------------------------------------------ 조립 + 캐시
def _blank(symbol: str) -> dict:
    return {"symbol": symbol, "stats": {}, "company": {}, "analyst": {}, "news": [], "_events": [],
            "earnings_history": [], "quarterly": [], "sources": [], "errors": []}


def _yf_symbol_candidates(symbol: str) -> list[str]:
    return [f"{symbol}.KS", f"{symbol}.KQ"] if is_kr(symbol) else [symbol]


def build_profile(symbol: str, fetchers: dict | None = None, timeout: float = FETCH_TIMEOUT,
                  skip: set[str] | None = None) -> dict:
    """소스들을 병렬로 불러 하나의 프로필로 합친다 (네트워크). skip: 쿨다운 중인 소스."""
    f = {**DEFAULT_FETCHERS, **(fetchers or {})}
    skip = skip or set()
    p = _blank(symbol)
    kr = is_kr(symbol)
    jobs = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        if "yfinance" not in skip:
            def yf_job():
                last = None
                for ys in _yf_symbol_candidates(symbol):
                    try:
                        return ys, f["yfinance"](ys)
                    except Exception as e:  # noqa: BLE001
                        last = e
                raise last or RuntimeError("Yahoo: 결과 없음")
            jobs["yfinance"] = ex.submit(yf_job)
        if kr and "naver" not in skip:
            jobs["naver"] = ex.submit(f["naver"], symbol)
        if not kr and "nasdaq" not in skip:
            jobs["nasdaq"] = ex.submit(f["nasdaq"], symbol)
        got = {}
        for k, fut in jobs.items():
            try:
                got[k] = fut.result(timeout=timeout)
            except FutTimeout:
                p["errors"].append(f"{k}: 시간 초과({timeout:.0f}s)")
            except Exception as e:  # noqa: BLE001 - 소스 하나 실패는 정상 상황
                p["errors"].append(f"{k}: {str(e)[:160]}")
    if "naver" in got:  # 국내는 네이버 먼저 (값 우선)
        _from_naver(got["naver"], p)
        p["sources"].append("네이버 증권")
        p["currency"] = "KRW"
    if "yfinance" in got:
        ys, raw = got["yfinance"]
        p["yf_symbol"] = ys
        cur = (raw.get("info") or {}).get("currency") or ("KRW" if kr else "USD")
        if isinstance(raw.get("info"), dict):
            _from_info(raw["info"], p)
        _from_calendar(raw.get("calendar"), p, cur)
        _from_earnings_dates(raw.get("earnings_dates"), p)
        _from_quarterly(raw.get("quarterly"), p)
        _from_recs(raw.get("recommendations"), p)
        _from_yf_news(raw.get("news"), p)
        p["errors"] += [f"yfinance {e}" for e in raw.get("errors", [])]
        p["sources"].append("Yahoo Finance")
    if "nasdaq" in got:
        _from_nasdaq(got["nasdaq"], p)
        p["sources"].append("Nasdaq")
    p.setdefault("currency", "KRW" if kr else "USD")
    p["failed"] = sorted(set(jobs) - set(got))
    p["events"] = [_ev_json(e) for e in _dedupe_events(p.pop("_events"))]
    return p


def _dedupe_events(evs: list[dict]) -> list[dict]:
    """같은 종류는 가장 믿을 만한 것 하나: 확정 > 추정, 더 가까운 미래."""
    evs = [e for e in evs if e.get("date")]
    out: dict[str, dict] = {}
    today = today_kst()
    for e in evs:
        k = e["kind"]
        cur = out.get(k)
        fut = e["date"] >= today - timedelta(days=1)

        def rank(x):
            return (x["date"] >= today - timedelta(days=1), not x.get("estimated"), bool(x.get("detail")),
                    -abs((x["date"] - today).days))
        if cur is None or (fut and rank(e) > rank(cur)):
            merged = {**(cur or {}), **{k2: v for k2, v in e.items() if v is not None}}
            if cur and cur.get("detail") and not e.get("detail"):
                merged["detail"] = cur["detail"]
            if cur and cur.get("time") and not e.get("time"):
                merged["time"] = cur["time"]
            out[k] = merged
        elif cur is not None:  # 덜 좋은 쪽의 부가 정보만 보탠다
            for k2 in ("detail", "time", "eps_estimate", "revenue_estimate"):
                if not cur.get(k2) and e.get(k2) and abs((e["date"] - cur["date"]).days) <= 7:
                    cur[k2] = e[k2]
    return list(out.values())


def _ev_json(e: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in e.items()}


def with_d_day(events: list[dict], today: date | None = None, past_days: int = 14) -> list[dict]:
    """D-day 를 요청 시점 기준으로 다시 계산하고, 오래 지난 일정은 뺀다. 가까운 순."""
    today = today or today_kst()
    out = []
    for e in events:
        d = _day(e.get("date"))
        if d is None:
            continue
        end = _day(e.get("date_end")) or d
        dd = (d - today).days
        if (end - today).days < -past_days:
            continue
        out.append({**e, "d_day": dd, "past": (end - today).days < 0,
                    "d_label": "오늘" if dd == 0 else f"D-{dd}" if dd > 0 else f"D+{-dd}"})
    return sorted(out, key=lambda x: (x["past"], abs(x["d_day"])))


def stock_profile(engine, symbol: str, refresh: bool = False, fetchers: dict | None = None,
                  now: datetime | None = None) -> dict:
    """캐시(6시간) → 없으면 소스 호출. 실패 소스는 30분 쿨다운. D-day·공시 일정은 매번 새로 붙인다."""
    from .. import ops
    from .db import session_scope
    now = now or datetime.now(UTC)
    key = f"profile:{symbol}"
    cached = ops.get_state(engine, key)
    at = pd.Timestamp(cached["at"]).to_pydatetime() if cached.get("at") else None
    prof = cached.get("data")
    # 소스 하나라도 실패했던 결과는 쿨다운(30분)만큼만 믿는다
    ttl = FAIL_COOLDOWN if prof and prof.get("failed") else PROFILE_TTL
    if refresh or prof is None or at is None or now - at >= ttl:
        cool = ops.get_state(engine, f"profile_fail:{symbol}")
        skip = set() if refresh else {k for k, t in cool.items() if now - pd.Timestamp(t).to_pydatetime() < FAIL_COOLDOWN}
        want = {"yfinance", "naver" if is_kr(symbol) else "nasdaq"}
        if want <= skip:  # 모든 소스가 쉬는 중
            if prof is None:
                prof = {k: v for k, v in _blank(symbol).items() if k != "_events"} | {
                    "events": [], "errors": ["모든 소스가 잠시 쉬는 중 (30분 뒤 다시 시도)"]}
        else:
            new = build_profile(symbol, fetchers, skip=skip)
            if new["sources"] or prof is None:
                prof, at = new, now
                ops.set_state(engine, key, {"at": now.isoformat(), "data": prof})
            else:  # 이번엔 전부 실패: 지난 결과를 유지하고 오류만 알린다
                prof = {**prof, "errors": new["errors"], "stale": True}
            fails = {k: now.isoformat() for k in new["failed"]}
            ops.set_state(engine, f"profile_fail:{symbol}", {**{k: v for k, v in cool.items() if k in skip}, **fails})
    out = dict(prof)
    events = list(out.get("events") or [])
    if is_kr(symbol):
        try:
            with session_scope(engine) as s:
                events += [_ev_json(e) for e in kr_disclosure_events(s, symbol)]
        except Exception as e:  # noqa: BLE001
            log.debug("공시 일정 조회 실패: %s", e)
        if not any(e["kind"] == "earnings" and not e.get("filed") and (_day(e["date"]) or date.min) >= today_kst(now)
                   for e in events):
            events.append(_ev_json(kr_report_deadline(today_kst(now))))
    out["events"] = with_d_day(events, today_kst(now))
    out["fetched_at"] = at.isoformat() if at else None
    s = out.get("stats") or {}
    if s.get("high52") and s.get("low52") and s.get("price"):
        s["pos52"] = (s["price"] - s["low52"]) / (s["high52"] - s["low52"]) if s["high52"] > s["low52"] else None
    return out


def next_events_text(profile: dict, limit: int = 3) -> list[str]:
    """채팅·요약용 한 줄 일정: '실적 발표 2026-11-19 (D-52, 추정, 장 마감 후) — EPS 예상 0.85'."""
    out = []
    for e in [x for x in profile.get("events") or [] if not x.get("past")][:limit]:
        tags = [e["d_label"]] + (["추정"] if e.get("estimated") else []) + ([e["time"]] if e.get("time") else [])
        rng = f"~{e['date_end']}" if e.get("date_end") else ""
        out.append(f"{e['label']} {e['date']}{rng} ({', '.join(tags)})" + (f" — {e['detail']}" if e.get("detail") else ""))
    return out


__all__ = ["stock_profile", "build_profile", "with_d_day", "kr_report_deadline", "next_events_text", "money_short",
           "kr_amount"]

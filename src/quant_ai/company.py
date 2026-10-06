"""v30 회사 이해 화면 — "이 회사가 뭐 하는 회사이고, 돈은 잘 버나, 비싼가, 요즘 무슨 일이 있었나"를 한 화면에.

  한 줄 소개 · 업종 · 거래소 · 상장주식수/시가총액(KRX 원천)
  재무 5년 (DART 단일회사 주요계정 · 연결 우선): 매출 · 영업이익 · 순이익 · 부채비율 · 영업이익률 — 키가 없거나 못 받으면 '정보 없음'
  밸류에이션: PER · PBR · 배당수익률 (받아 둔 종목 정보) · 없으면 DART 순이익과 시가총액으로 계산한 PER
  같은 업종 비교: 시가총액 상위 4곳의 1년 수익률 · 시가총액 · PER(있으면)
  최근 이슈 타임라인: 공시 · 뉴스 · 급등락 · 실적 일정을 날짜순 한 줄씩
  초보자 3줄: 좋은 점 · 걱정할 점 · 앞으로 볼 일정 — 근거 있는 것만 (지어내지 않음)
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from . import ops

log = logging.getLogger(__name__)
DART = "https://opendart.fss.or.kr/api"
ACCOUNTS = {"revenue": ("매출액", "수익(매출액)", "영업수익", "매출"), "op_income": ("영업이익", "영업이익(손실)"),
            "net_income": ("당기순이익", "당기순이익(손실)", "연결당기순이익"), "assets": ("자산총계",),
            "liabilities": ("부채총계",), "equity": ("자본총계",)}
FIN_TTL = timedelta(days=7)


def _amt(x) -> float | None:
    try:
        s = str(x).replace(",", "").strip()
        return float(s) if s and s not in ("-", "nan") else None
    except ValueError:
        return None


def parse_fnltt(payload: dict) -> dict[int, dict]:
    """DART fnlttSinglAcnt 응답 → {연도: {revenue, op_income, net_income, assets, liabilities, equity}} (연결 CFS 우선)."""
    rows = payload.get("list") or []
    if not rows:
        return {}
    has_cfs = any(r.get("fs_div") == "CFS" for r in rows)
    rows = [r for r in rows if r.get("fs_div") == ("CFS" if has_cfs else "OFS")]
    out: dict[int, dict] = {}
    for r in rows:
        nm = (r.get("account_nm") or "").replace(" ", "")
        key = next((k for k, names in ACCOUNTS.items() if nm in [n.replace(" ", "") for n in names]), None)
        if not key:
            continue
        try:
            y = int(r.get("bsns_year"))
        except (TypeError, ValueError):
            continue
        for off, col in ((0, "thstrm_amount"), (1, "frmtrm_amount"), (2, "bfefrmtrm_amount")):
            v = _amt(r.get(col))
            if v is not None:
                out.setdefault(y - off, {}).setdefault(key, v)  # 최신 보고서 값이 먼저 들어간다
    return out


def corp_code(app, sym: str, key: str | None = None, get=None) -> str | None:
    """종목코드 → DART 고유번호: 받아 둔 공시에서 먼저 · 없으면 DART corpCode.xml(한 번 받아 캐시)."""
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import Disclosure
    with session_scope(app.engine) as s:
        c = s.scalar(select(Disclosure.corp_code).where(Disclosure.symbol == sym, Disclosure.corp_code.is_not(None)).limit(1))
    if c:
        return c
    cache = ops.get_state(app.engine, "dart_corp_codes")
    if cache.get("map"):
        return cache["map"].get(sym)
    if not key:
        return None
    raw = (get or _get)(f"{DART}/corpCode.xml?crtfc_key={key}")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xml = z.read(z.namelist()[0]).decode("utf-8", "replace")
    except zipfile.BadZipFile:
        raise RuntimeError(f"DART 고유번호 목록을 받지 못함: {raw[:120]!r}") from None
    import re
    mp = {m.group(2): m.group(1) for m in re.finditer(r"<corp_code>(\d{8})</corp_code>\s*<corp_name>[^<]*</corp_name>\s*(?:<corp_eng_name>[^<]*</corp_eng_name>\s*)?<stock_code>(\d{6})</stock_code>", xml)}
    ops.set_state(app.engine, "dart_corp_codes", {"at": datetime.now(UTC).isoformat(), "map": mp})
    return mp.get(sym)


def _get(url: str) -> bytes:
    from .data.collectors import http
    return http.get(url, timeout=15.0, retries=1)


def financials(app, sym: str, get=None, refresh: bool = False, now: datetime | None = None) -> dict:
    """재무 5년 (DART). 캐시 7일 · 실패해도 화면은 '정보 없음'으로."""
    now = now or datetime.now(UTC)
    st = ops.get_state(app.engine, f"dartfin:{sym}")
    if st.get("at") and not refresh and now - datetime.fromisoformat(st["at"]) < FIN_TTL:
        return st
    if not sym[:1].isdigit():
        return {"rows": [], "error": "국내 종목만 (DART)"}
    key = app.settings.dart_api_key
    if not key:
        return st or {"rows": [], "error": "DART 키가 없어요 — .env 에 DART_API_KEY (opendart.fss.or.kr 무료 발급)"}
    try:
        cc = corp_code(app, sym, key, get)
        if not cc:
            return {"rows": [], "error": "DART 고유번호를 찾지 못함"}
        g = get or _get
        by: dict[int, dict] = {}
        y0 = now.year - 1
        for y in (y0, y0 - 3):  # 한 보고서에 3년치 (당기·전기·전전기) → 두 번이면 6년
            payload = json.loads(g(f"{DART}/fnlttSinglAcnt.json?crtfc_key={key}&corp_code={cc}&bsns_year={y}&reprt_code=11011"))
            if payload.get("status") not in (None, "000"):
                if payload.get("status") == "013" and y == y0:  # 아직 사업보고서가 없으면 한 해 전
                    payload = json.loads(g(f"{DART}/fnlttSinglAcnt.json?crtfc_key={key}&corp_code={cc}&bsns_year={y - 1}&reprt_code=11011"))
                else:
                    continue
            for yy, v in parse_fnltt(payload).items():
                by.setdefault(yy, {}).update({k: x for k, x in v.items() if k not in by.get(yy, {})})
    except Exception as e:  # noqa: BLE001 - 화면은 계속 떠야 한다
        return {**st, "error": f"DART 재무를 받지 못함: {type(e).__name__}: {str(e)[:120]}"} if st else {"rows": [], "error": f"DART 재무를 받지 못함: {type(e).__name__}"}
    rows = []
    for y in sorted(by)[-5:]:
        v = by[y]
        rev, op, ni, li, eq = (v.get(k) for k in ("revenue", "op_income", "net_income", "liabilities", "equity"))
        rows.append({"year": y, **v, "op_margin": op / rev if op is not None and rev else None,
                     "debt_ratio": li / eq if li is not None and eq else None})
    out = {"at": now.isoformat(), "rows": rows, "source": "DART 단일회사 주요계정 (연결 우선)", "corp_code": cc}
    ops.set_state(app.engine, f"dartfin:{sym}", out)
    return out


# ------------------------------------------------------------------ 화면 한 장
def _profile(app, sym: str) -> dict:
    return ops.get_state(app.engine, f"profile:{sym}").get("data") or {}


def _ret_1y(b: pd.DataFrame | None) -> float | None:
    if b is None or len(b) < 30:
        return None
    c = b["close"].astype(float).iloc[-252:]
    return float(c.iloc[-1] / c.iloc[0] - 1)


def timeline(app, sym: str, days: int = 120, now: datetime | None = None, bars: pd.DataFrame | None = None) -> list[dict]:
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import Disclosure, NewsArticle
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    items: list[dict] = []
    with session_scope(app.engine) as s:
        for d in s.scalars(select(Disclosure).where(Disclosure.symbol == sym, Disclosure.filed_at >= since.date()).order_by(Disclosure.filed_at.desc()).limit(20)):
            items.append({"date": str(d.filed_at), "kind": "disclosure", "title": d.title, "detail": (d.summary or "")[:120], "link": f"#d/{d.id}"})
        for n in s.scalars(select(NewsArticle).where(NewsArticle.published_at >= since).order_by(NewsArticle.published_at.desc()).limit(400)):
            if sym in (n.symbols or []):
                items.append({"date": str(n.published_at.date()), "kind": "news", "title": n.title, "detail": n.source or "", "link": f"#n/{n.id}"})
    if bars is not None and len(bars):
        from .stockplus import big_moves
        for m in big_moves(bars["close"].astype(float)):
            if m["date"] < since.date().isoformat():
                continue
            items.append({"date": m["date"], "kind": "move", "title": m["title"], "detail": "평소 움직임보다 크게"})
    for e in _profile(app, sym).get("events") or []:
        if e.get("date"):
            items.append({"date": str(e["date"])[:10], "kind": "event", "title": e.get("label") or "일정", "detail": e.get("detail") or ("추정" if e.get("estimated") else "")})
    items.sort(key=lambda x: x["date"], reverse=True)
    seen, out = set(), []
    for it in items:
        k = (it["date"], it["title"][:30])
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out[:25]


def easy3(fin: dict, val: dict, sig_row: dict | None, ret1y: float | None, upcoming: list[dict], issues: list[dict]) -> dict:
    """초보자 3줄: 좋은 점 · 걱정할 점 · 앞으로 볼 일 — 근거가 있는 것만."""
    good, bad = [], []
    rows = fin.get("rows") or []
    if len(rows) >= 2:
        a, b = rows[-2], rows[-1]
        if a.get("revenue") and b.get("revenue"):
            g = b["revenue"] / a["revenue"] - 1
            if g > 0.05:
                good.append(f"{b['year']}년 매출 {g * 100:+.0f}% 늘었어요")
            elif g < -0.05:
                bad.append(f"{b['year']}년 매출 {g * 100:+.0f}% 줄었어요")
        if b.get("op_income") is not None:
            (good if b["op_income"] > 0 else bad).append(f"{b['year']}년 영업이익 {'흑자' if b['op_income'] > 0 else '적자'}")
        if b.get("debt_ratio") is not None:
            if b["debt_ratio"] > 2:
                bad.append(f"부채비율 {b['debt_ratio'] * 100:.0f}% — 빚이 자본의 2배 넘음")
            elif b["debt_ratio"] < 0.5:
                good.append(f"부채비율 {b['debt_ratio'] * 100:.0f}% — 빚이 적은 편")
    if val.get("per") is not None:
        if val["per"] < 0:
            bad.append("PER 음수 — 최근 1년 순손실")
        elif val["per"] > 40:
            bad.append(f"PER {val['per']:.0f}배 — 이익에 비해 주가가 비싼 편")
        elif val["per"] < 10:
            good.append(f"PER {val['per']:.1f}배 — 이익에 비해 주가가 싼 편")
    if val.get("div_yield") and val["div_yield"] >= 0.03:
        good.append(f"배당수익률 {val['div_yield'] * 100:.1f}%")
    if sig_row:
        for s in sig_row.get("signals") or []:
            if s.get("verified") and abs(s.get("score") or 0) >= 1.5:
                (good if s["score"] > 0 else bad).append(f"{s['label']}: {s['text']}")
    if ret1y is not None and ret1y < -0.3:
        bad.append(f"1년 동안 {ret1y * 100:.0f}% — 크게 내려와 있어요")
    for it in issues[:2]:
        bad.append(f"데이터 확인 필요: {it.get('text', '')[:60]}")
    nxt = [f"{e.get('label') or e.get('title')} {str(e.get('date'))[:10]}" + (f" (D-{e['d_day']})" if e.get("d_day") is not None and e["d_day"] >= 0 else "") for e in upcoming[:2]]
    return {"good": good[:3] or ["뚜렷한 장점 근거가 아직 없어요 (재무·지표를 받으면 채워져요)"],
            "bad": bad[:3] or ["뚜렷한 걱정 근거가 아직 없어요"],
            "next": nxt or ["가까운 일정 정보가 없어요"]}


def view(app, sym: str, now: datetime | None = None) -> dict:
    from .companies import identity
    from .engines.sector import sector_map
    now = now or datetime.now(UTC)
    idt = identity(app, sym)
    prof = _profile(app, sym)
    comp = prof.get("company") or {}
    stats = dict(prof.get("stats") or {})
    facts = ((ops.get_state(app.engine, "krx_facts").get("symbols") or {}).get(sym)) or {}
    fin = financials(app, sym)
    bars_all, _, _ = app.market_data()
    b = bars_all.get(sym)
    sectors = sector_map(app.engine)
    sector = sectors.get(sym) or comp.get("sector") or comp.get("industry")
    mc = stats.get("market_cap") or facts.get("marcap")
    rows = fin.get("rows") or []
    per = stats.get("per")
    per_src = "종목 정보"
    if per is None and mc and rows and rows[-1].get("net_income"):
        per, per_src = mc / rows[-1]["net_income"], f"계산: 시가총액 ÷ {rows[-1]['year']}년 순이익"
    pbr = stats.get("pbr")
    if pbr is None and mc and rows and rows[-1].get("equity"):
        pbr = mc / rows[-1]["equity"]
    val = {"per": per, "per_src": per_src if per is not None else None, "pbr": pbr, "div_yield": stats.get("div_yield"),
           "market_cap": mc, "shares": facts.get("shares") or stats.get("shares"), "foreign_rate": stats.get("foreign_rate"),
           "target_mean": (prof.get("analyst") or {}).get("target_mean"), "rating": (prof.get("analyst") or {}).get("rating")}
    peers = []
    if sector:
        same = [s for s, sec in sectors.items() if sec == sector and s != sym and s in bars_all]
        allf = (ops.get_state(app.engine, "krx_facts").get("symbols") or {})
        same.sort(key=lambda s: -((allf.get(s) or {}).get("marcap") or 0))
        from .signals2 import _names
        nm = _names(app.engine, same[:4] + [sym])
        for s in same[:4]:
            ps = (_profile(app, s).get("stats") or {})
            peers.append({"symbol": s, "name": nm.get(s, s), "ret_1y": _ret_1y(bars_all.get(s)), "market_cap": (allf.get(s) or {}).get("marcap") or ps.get("market_cap"),
                          "per": ps.get("per")})
        if peers:
            peers.insert(0, {"symbol": sym, "name": nm.get(sym, sym), "ret_1y": _ret_1y(b), "market_cap": mc, "per": per, "self": True})
    sig_row = None
    try:
        from .signals2 import for_symbol
        sig_row = for_symbol(app, sym).get("row")
    except Exception:  # noqa: BLE001
        sig_row = None
    try:
        from .data.fundamentals import with_d_day
        upcoming = [e for e in with_d_day(prof.get("events") or []) if (e.get("d_day") or 0) >= 0]
    except Exception:  # noqa: BLE001
        upcoming = []
    if not upcoming and sym[:1].isdigit():
        from .data.fundamentals import kr_report_deadline
        dl = kr_report_deadline(now.date())
        upcoming = [{"label": dl.get("label", "정기보고서 제출 기한"), "date": dl.get("date"), "d_day": (date.fromisoformat(str(dl["date"])) - now.date()).days if dl.get("date") else None}]
    issues = [x for x in (ops.get_state(app.engine, "datacheck").get("issues") or [])
              if x.get("symbol") == sym and x.get("kind") in ("limit", "marcap", "halt", "rows")]  # 대형주 큰 움직임은 자료 오류가 아님
    return {"symbol": sym, "identity": idt, "name": idt.get("name") or comp.get("name") or sym, "sector": sector,
            "summary": comp.get("summary"), "website": comp.get("website"), "employees": comp.get("employees"),
            "financials": fin, "valuation": val, "peers": peers, "ret_1y": _ret_1y(b),
            "timeline": timeline(app, sym, now=now, bars=b), "easy3": easy3(fin, val, sig_row, _ret_1y(b), upcoming, issues),
            "facts_date": facts.get("date"), "profile_at": ops.get_state(app.engine, f"profile:{sym}").get("at")}


__all__ = ["view", "financials", "parse_fnltt", "easy3", "timeline", "corp_code"]

"""v29 데이터 정합성 점검 — "화면의 주가·52주·시가총액을 믿어도 되나"를 매일 확인한다.

1) 최신성   국내 일봉이 몇 거래일 밀렸나 · 일봉 자동 갱신(krx_data 작업)이 돌고 있나 · 자동 갱신 위치(marcap)가 있나
2) 원천 자체 정합성 (인터넷 없이)
   - 가격제한폭(±30%)을 넘는 하루 변동 → 수정주가 오류 의심 (오류)
   - 대형주(거래대금 상위 30)가 하루 ±15% 넘게 움직임 → 실제 시세와 대조 필요 (확인)
   - 고가 < 시가·종가, 저가 > 시가·종가 같은 모순 · 거래량 0이 이어짐(거래정지 의심)
   - 원천의 시가총액 = 상장주식수 × 종가 (1% 넘게 다르면 불일치)
   - 가격 조정 이벤트(액면분할·병합·권리락) → 수정주가로 이어 붙였는지 (기준가 ≠ 전일 종가인 날)
3) 외부 시세와 대조 (인터넷이 되면) — 관심·보유 + 거래대금 상위 종목의 최근 1년 종가를
   네이버 차트(수정주가) → Yahoo 순으로 받아 날짜별로 비교. 0.5% 넘게 다른 날 · 일정한 배율 차이(수정 기준 다름)를 찾는다.
   못 받으면 '대조 못 함'으로 남긴다 (지어내지 않음).
결과는 ops 'datacheck' 에 저장 · 화면 '데이터 점검' · `quant-ai datacheck`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from . import ops

log = logging.getLogger(__name__)

LIMIT = 0.30            # 국내 가격제한폭
BIG_MOVE = 0.15         # 대형주 '확인 필요' 기준
TOP_LIQUID = 30         # 대형주 = 20일 거래대금 상위
REF_TOL = 0.005         # 외부 시세와 0.5% 넘게 다르면 불일치
REF_SAMPLE = 15         # 외부 대조 종목 수
STATE_KEY = "datacheck"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


# ------------------------------------------------------------------ 외부 시세 (파서는 네트워크 없이 시험 가능)
def parse_naver_chart(raw: bytes | str) -> list[tuple[str, float]]:
    """fchart.stock.naver.com 일봉 XML → [(YYYY-MM-DD, 종가)] (네이버 차트는 수정주가)."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    out = []
    for m in re.finditer(r'data="(\d{8})\|([^"]*)"', text):
        parts = m.group(2).split("|")
        try:
            close = float(parts[3])
        except (IndexError, ValueError):
            continue
        d = m.group(1)
        if close > 0:
            out.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", close))
    return out


def _http_get(url: str) -> bytes:
    from .data.collectors import http
    return http.get(url, timeout=10.0, retries=1, headers=UA)


def reference_closes(sym: str, get: Callable[[str], bytes] | None = None) -> tuple[str, list[tuple[str, float]]]:
    """(출처, [(날짜, 종가)]). 네이버 → Yahoo(.KS/.KQ). 둘 다 실패하면 예외."""
    import json

    from .data.collectors.indices import parse_yahoo
    get = get or _http_get
    errs = []
    try:
        rows = parse_naver_chart(get(f"https://fchart.stock.naver.com/sise.nhn?symbol={sym}&timeframe=day&count=300&requestType=0"))
        if rows:
            return "네이버 차트", rows
        errs.append("네이버: 빈 응답")
    except Exception as e:  # noqa: BLE001 - 다음 출처로
        errs.append(f"네이버: {type(e).__name__}")
    for suf in (".KS", ".KQ"):
        try:
            raw = json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}{suf}?range=1y&interval=1d"))
            rows = [(d, float(c)) for d, c in parse_yahoo(raw)]
            if rows:
                return "Yahoo", rows
        except Exception as e:  # noqa: BLE001
            errs.append(f"Yahoo{suf}: {type(e).__name__}")
    raise ConnectionError(" · ".join(errs))


def compare(ours: pd.Series, ref: list[tuple[str, float]], tol: float = REF_TOL) -> dict:
    """같은 날짜끼리 종가 비교 → 불일치 날 · 일정한 배율 차이(수정주가 기준이 다름)."""
    s = ours.copy()
    s.index = [str(pd.Timestamp(i).date()) for i in s.index]
    s = s[~pd.Index(s.index).duplicated(keep="last")]
    r = pd.Series(dict(ref), dtype=float)
    common = sorted(set(s.index) & set(r.index))
    if not common:
        return {"n": 0, "verdict": "unknown", "text": "겹치는 날짜 없음"}
    d = (s[common].astype(float) / r[common] - 1)
    bad = d[d.abs() > tol]
    med = float(d.median())
    rows = [{"date": k, "ours": round(float(s[k]), 2), "ref": round(float(r[k]), 2), "diff": round(float(d[k]), 4)} for k in bad.index[-8:]]
    if abs(med) > 0.02 and float(d.std(ddof=0) or 0) < 0.01:
        verdict, text = "bad", f"모든 날이 일정하게 {med * 100:+.1f}% 차이 — 수정주가 기준이 다름 (분할·권리락 반영 확인)"
    elif len(bad) == 0:
        verdict, text = "ok", f"{len(common)}일 모두 일치 (차이 {tol * 100:.1f}% 이내)"
    elif len(bad) <= max(2, len(common) // 100):
        verdict, text = "warn", f"{len(common)}일 중 {len(bad)}일 다름 — 그날만 확인"
    else:
        verdict, text = "bad", f"{len(common)}일 중 {len(bad)}일 다름 — 일봉을 다시 받으세요"
    return {"n": len(common), "n_bad": int(len(bad)), "max_diff": round(float(d.abs().max()), 4), "median": round(med, 4),
            "verdict": verdict, "text": text, "bad": rows, "from": common[0], "to": common[-1]}


# ------------------------------------------------------------------ 원천 자체 점검
def scan_bars(bars: dict[str, pd.DataFrame], names: dict, facts: dict | None = None, top: set | None = None) -> list[dict]:
    from .data.quality import validate_bars
    facts = facts or {}
    top = top or set()
    issues: list[dict] = []
    latest = max((pd.Timestamp(b.index[-1]) for b in bars.values() if b is not None and len(b)), default=None)
    for sym, b in bars.items():
        if b is None or len(b) < 10 or not sym[:1].isdigit():
            continue
        if latest is not None and pd.Timestamp(b.index[-1]) < latest - pd.Timedelta(days=14):
            continue  # 상장폐지·합병 등으로 이미 끝난 종목 (지금 거래되는 종목만 점검)
        c = b["close"].astype(float)
        ret = c.pct_change(fill_method=None)
        adj_days = {e["date"] for e in (facts.get(sym) or {}).get("adj_events") or []}
        nm = names.get(sym, sym)
        for ts, r in ret.iloc[5:].items():  # 상장 직후 며칠은 제한폭이 다름
            if pd.isna(r):
                continue
            day = str(pd.Timestamp(ts).date())
            if abs(r) > LIMIT + 0.005:
                issues.append({"symbol": sym, "name": nm, "date": day, "level": "bad", "kind": "limit", "ret": round(float(r), 4),
                               "text": f"하루 {r * 100:+.1f}% — 가격제한폭(±30%)을 넘음" + (" (그날 가격 조정 이벤트 있음)" if day in adj_days else " · 수정주가 오류 의심")})
        if sym in top:  # 대형주는 최근 1년 가장 크게 움직인 하루만 (실제 시세와 대조할 날)
            y = ret[ret.index >= c.index[-1] - pd.Timedelta(days=370)].dropna()
            if len(y) and abs(y).max() > BIG_MOVE:
                ts = y.abs().idxmax()
                r = float(y[ts])
                issues.append({"symbol": sym, "name": nm, "date": str(pd.Timestamp(ts).date()), "level": "warn", "kind": "bigmove", "ret": round(r, 4),
                               "text": f"대형주가 하루 {r * 100:+.1f}% (최근 1년 최대) — 실제 시세와 대조해 보세요"})
        _, rep = validate_bars(b[["open", "high", "low", "close", "volume"]] if {"open", "high", "low"} <= set(b.columns) else
                               b.assign(open=c, high=c, low=c)[["open", "high", "low", "close", "volume"]], sym)
        if rep.dropped:
            issues.append({"symbol": sym, "name": nm, "date": None, "level": "warn", "kind": "rows",
                           "text": "버린 행: " + ", ".join(f"{k} {v}" for k, v in rep.dropped.items())})
        v = b["volume"].astype(float).iloc[-5:] if "volume" in b else pd.Series(dtype=float)
        if len(v) == 5 and (v <= 0).all():
            issues.append({"symbol": sym, "name": nm, "date": str(pd.Timestamp(b.index[-1]).date()), "level": "warn", "kind": "halt",
                           "text": "최근 5거래일 거래량 0 — 거래정지 의심"})
        f = facts.get(sym) or {}
        if f.get("marcap_mismatch"):
            issues.append({"symbol": sym, "name": nm, "date": f.get("date"), "level": "warn", "kind": "marcap",
                           "text": f"원천 시가총액이 '주식수 × 종가'와 1% 넘게 다른 날 {f['marcap_mismatch']}일"})
    order = {"bad": 0, "warn": 1}
    return sorted(issues, key=lambda x: (order.get(x["level"], 2), x.get("date") or ""), reverse=False)


def facts_for(app, sym: str) -> dict:
    """종목 하나의 시가총액 · 상장주식수 · 가격 조정 이벤트 (없으면 빈 dict)."""
    return ((ops.get_state(app.engine, "krx_facts").get("symbols") or {}).get(sym)) or {}


def stats52(close: pd.Series, high: pd.Series | None = None, low: pd.Series | None = None) -> dict:
    y = close.iloc[-252:]
    hi = (high if high is not None else close).iloc[-252:]
    lo = (low if low is not None else close).iloc[-252:]
    return {"high": round(float(hi.max()), 2), "high_date": str(pd.Timestamp(hi.idxmax()).date()),
            "low": round(float(lo.min()), 2), "low_date": str(pd.Timestamp(lo.idxmin()).date()),
            "ret_1y": round(float(y.iloc[-1] / y.iloc[0] - 1), 4) if len(y) > 1 and y.iloc[0] else None}


# ------------------------------------------------------------------ 전체 실행
def _update_status(app, now: datetime) -> dict:
    from sqlalchemy import select

    from .data.collectors.marcap import default_dir
    from .data.db import session_scope
    from .data.models import JobRun
    with session_scope(app.engine) as s:
        runs = list(s.scalars(select(JobRun).where(JobRun.job == "krx_data").order_by(JobRun.started_at.desc()).limit(5)))
        last = runs[0] if runs else None
        last_ok = next((r for r in runs if r.ok), None)
        rec = {"configured": default_dir() is not None,
               "last_run": last.started_at.isoformat() if last else None, "last_ok": bool(last.ok) if last else None,
               "last_error": (last.error or "")[:200] if last and not last.ok else None,
               "last_success": last_ok.started_at.isoformat() if last_ok else None}
    if not rec["configured"]:
        rec["text"] = "자동 갱신 위치(marcap)가 없어요 — `./run.sh data` 로 한 번 받으면 이후 6시간마다(장 마감 후) 자동 갱신"
    elif last is None:
        rec["text"] = "자동 갱신 작업 기록 없음 — 서버(./run.sh)를 켜 두면 장 마감 후 6시간마다 받아요"
    elif not last.ok:
        rec["text"] = f"마지막 자동 갱신 실패: {rec['last_error'] or '원인 불명'}"
    else:
        rec["text"] = "자동 갱신 정상 (장 마감 후 6시간마다)"
    return rec


def run(app, now: datetime | None = None, get: Callable[[str], bytes] | None = None, online: bool = True,
        sample: int = REF_SAMPLE, store: bool = True) -> dict:
    from .asof import freshness
    now = now or datetime.now(UTC)
    bars, _, _ = app.market_data()
    kr = {s: b for s, b in bars.items() if s[:1].isdigit() and b is not None and len(b)}
    names = {}
    try:
        from .signals2 import _names
        names = _names(app.engine, list(kr))
    except Exception:  # noqa: BLE001
        names = {}
    facts = (ops.get_state(app.engine, "krx_facts").get("symbols")) or {}
    turn = {s: float((b["close"] * b["volume"]).iloc[-20:].mean()) for s, b in kr.items() if "volume" in b}
    top = set(sorted(turn, key=lambda s: -turn[s])[:TOP_LIQUID])
    issues = scan_bars(kr, names, facts, top)
    # 같은 날 대형주 여러 개가 함께 크게 움직였으면 한 종목 자료 오류보다 시장 전체 움직임일 가능성이 크다
    from collections import Counter
    by_day = Counter(x["date"] for x in issues if x["kind"] == "bigmove")
    market_days = [{"date": d, "n": n, "names": [x["name"] for x in issues if x["kind"] == "bigmove" and x["date"] == d][:8]}
                   for d, n in sorted(by_day.items(), reverse=True) if n >= 4]
    md = {m["date"]: m["n"] for m in market_days}
    for x in issues:
        if x["kind"] == "bigmove" and x["date"] in md:
            x["text"] += f" · 같은 날 대형주 {md[x['date']]}개가 함께 크게 움직임 → 시장 전체 움직임일 가능성 (지수와 함께 확인)"
    fr = freshness(app, now)["items"].get("bar_kr") or {}
    upd = _update_status(app, now)
    adj = [{"symbol": s, "name": names.get(s, s), **e} for s, f in facts.items() if s in kr for e in (f.get("adj_events") or [])]
    adj.sort(key=lambda x: x["date"], reverse=True)
    # 외부 대조: 관심·보유 먼저, 그다음 거래대금 상위
    ref_rows: list[dict] = []
    ref_status = "skipped"
    if online:
        try:
            from .alerts import focus_symbols
            mine = [s for s in focus_symbols(app) if s in kr]
        except Exception:  # noqa: BLE001
            mine = []
        pick = list(dict.fromkeys([*mine, *sorted(turn, key=lambda s: -turn[s])]))[:sample]
        for i, sym in enumerate(pick):
            try:
                src, ref = reference_closes(sym, get)
            except Exception as e:  # noqa: BLE001
                ref_rows.append({"symbol": sym, "name": names.get(sym, sym), "verdict": "unknown", "text": f"받지 못함 ({str(e)[:80]})"})
                if i == 1 and all(r["verdict"] == "unknown" for r in ref_rows):
                    break  # 처음 두 종목 모두 실패 = 인터넷 없음 → 나머지는 시도하지 않음 (점검이 오래 걸리지 않게)
                continue
            ref_rows.append({"symbol": sym, "name": names.get(sym, sym), "source": src, **compare(kr[sym]["close"], ref)})
        ok_n = sum(1 for r in ref_rows if r["verdict"] in ("ok", "warn", "bad"))
        ref_status = "unreachable" if ok_n == 0 else "partial" if ok_n < len(ref_rows) else "done"
    focus = {}
    for sym in ("005930", "000660"):
        if sym in kr:
            b = kr[sym]
            f = facts.get(sym) or {}
            focus[sym] = {"name": names.get(sym, sym), "last": float(b["close"].iloc[-1]), "date": str(pd.Timestamp(b.index[-1]).date()),
                          **{"w52": stats52(b["close"], b.get("high"), b.get("low"))}, "marcap": f.get("marcap"), "shares": f.get("shares"),
                          "n_adj_events": f.get("n_adj_events")}
    n_bad = sum(1 for x in issues if x["level"] == "bad") + sum(1 for r in ref_rows if r["verdict"] == "bad")
    n_warn = sum(1 for x in issues if x["level"] == "warn") + sum(1 for r in ref_rows if r["verdict"] == "warn")
    lag = fr.get("lag_days")
    if n_bad or (lag or 0) > 2:
        status = "bad"
    elif n_warn or (lag or 0) > 0 or ref_status in ("unreachable", "skipped") or not upd["configured"]:
        status = "warn"
    else:
        status = "good"
    why = []
    if lag:
        why.append(f"일봉 {lag}거래일 밀림")
    if n_bad:
        why.append(f"오류 {n_bad}건")
    if n_warn:
        why.append(f"확인 {n_warn}건")
    if ref_status in ("unreachable", "skipped"):
        why.append("외부 시세 대조 못 함")
    if not upd["configured"]:
        why.append("자동 갱신 위치 없음")
    headline = " · ".join(why) if why else "일봉이 최신이고 외부 시세와도 맞아요"
    out = {"at": now.isoformat(), "status": status, "headline": headline, "n_symbols": len(kr),
           "freshness": {"date": fr.get("label"), "lag_days": lag, "age": fr.get("age"), "status": fr.get("status")},
           "update": upd, "issues": issues[:200], "market_days": market_days, "n_bad": n_bad, "n_warn": n_warn,
           "adj_events": adj[:40], "n_adj_events": len(adj), "facts_at": ops.get_state(app.engine, "krx_facts").get("at"),
           "reference": {"status": ref_status, "rows": ref_rows,
                         "text": {"done": "외부 시세와 대조 완료", "partial": "일부 종목만 대조함", "unreachable": "외부 시세를 받지 못해 대조 못 함 (인터넷 연결 확인)",
                                  "skipped": "외부 대조를 하지 않음"}[ref_status]},
           "focus": focus,
           "note": "이 점검은 원천(KRX 자료) 자체가 맞는지와 우리가 수정주가로 바르게 이어 붙였는지를 봅니다. 최종 확인은 외부 시세 대조로 합니다."}
    if store:
        ops.set_state(app.engine, STATE_KEY, _json(out))
    return out


def _json(x):
    if isinstance(x, dict):
        return {k: _json(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_json(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    return x


__all__ = ["run", "compare", "parse_naver_chart", "reference_closes", "scan_bars", "facts_for", "stats52"]

"""SEC EDGAR 공시 (미국 원천 데이터 · 키 없음 · 공공 데이터).

- 티커 → CIK: https://www.sec.gov/files/company_tickers.json (일주일에 한 번 갱신해 ops 에 보관)
- 종목별 최근 제출: https://data.sec.gov/submissions/CIK##########.json → 8-K·10-Q·10-K·6-K·20-F·S-1/S-3·13D/G·DEF 14A
- 8-K 항목 2.02(실적 발표)는 'earnings' 이벤트로 — 무료 비공식 실적일보다 확실한 '실제 발표' 기록
SEC 는 연락처가 담긴 User-Agent 를 요구한다 → QUANT_SEC_USER_AGENT="이름 email@example.com" (없으면 기본값, 막힐 수 있음).
초당 10회 제한 — 한 번에 최대 30종목만.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import ops
from ..models import Disclosure
from . import http

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{:010d}.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
FORMS = {"8-K": "주요 사항 보고 (8-K)", "10-Q": "분기 보고서 (10-Q)", "10-K": "연간 보고서 (10-K)", "6-K": "외국 기업 수시 보고 (6-K)",
         "20-F": "외국 기업 연간 보고서 (20-F)", "S-1": "신규 증권 신고 (S-1)", "S-3": "증권 발행 신고 (S-3)",
         "SC 13D": "5% 이상 지분 (13D · 경영 참여)", "SC 13G": "5% 이상 지분 (13G)", "DEF 14A": "주주총회 위임장 (DEF 14A)"}
ITEMS_8K = {"1.01": "중요 계약", "1.03": "파산", "2.01": "인수·매각 완료", "2.02": "실적 발표", "2.05": "구조조정",
            "2.06": "자산 손상", "3.01": "상장 폐지·이전 통지", "4.02": "재무제표 신뢰 불가", "5.02": "임원 변동", "7.01": "공정공시",
            "8.01": "기타 중요 사항"}


def user_agent() -> str:
    return os.environ.get("QUANT_SEC_USER_AGENT") or "quant-ai research admin@example.com"


def _fetch(url: str, fetch_json=None) -> dict:
    if fetch_json:
        return fetch_json(url)
    return http.get_json(url, headers={"User-Agent": user_agent(), "Accept-Encoding": "identity"})


def cik_map(app, fetch_json=None, max_age_days: int = 7) -> dict[str, int]:
    st = ops.get_state(app.engine, "sec_cik_map")
    try:
        fresh = st.get("at") and (datetime.now(UTC) - datetime.fromisoformat(st["at"])).days < max_age_days
    except (TypeError, ValueError):
        fresh = False
    if fresh and st.get("map"):
        return st["map"]
    raw = _fetch(TICKERS_URL, fetch_json)
    mp = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in (raw.values() if isinstance(raw, dict) else raw)}
    ops.set_state(app.engine, "sec_cik_map", {"at": datetime.now(UTC).isoformat(), "map": mp})
    return mp


def parse_submissions(payload: dict, symbol: str, since: date) -> list[dict]:
    rec = (payload.get("filings") or {}).get("recent") or {}
    cik = int(payload.get("cik") or 0)
    out = []
    forms = rec.get("form") or []
    for i, form in enumerate(forms):
        if form not in FORMS:
            continue
        try:
            filed = date.fromisoformat(rec["filingDate"][i])
        except (KeyError, IndexError, ValueError):
            continue
        if filed < since:
            continue
        acc = rec["accessionNumber"][i]
        items = [x.strip() for x in str((rec.get("items") or [""] * len(forms))[i] or "").split(",") if x.strip()]
        labels = [ITEMS_8K[x] for x in items if x in ITEMS_8K]
        title = FORMS[form] + (f" — {' · '.join(labels)}" if labels else "")
        doc = (rec.get("primaryDocument") or [""] * len(forms))[i]
        out.append({"receipt_no": f"SEC-{acc}", "corp_code": str(cik), "corp_name": payload.get("name"), "symbol": symbol,
                    "title": title, "filed_at": filed, "form": form, "items": items,
                    "url": DOC_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc) if doc else
                    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"})
    return out


def collect(app, session: Session, symbols: list[str], since: date, fetch_json=None, max_symbols: int = 30) -> dict:
    """미국 종목(보유·관심)의 최근 SEC 제출 → disclosures 테이블 (source=SEC). 실적 발표(8-K 2.02)는 events 에 earnings."""
    from ...engines.news_intel import NewsAnalyzer
    mp = cik_map(app, fetch_json)
    na = NewsAnalyzer()
    added, missing, failed = 0, [], []
    for sym in [s for s in symbols if not s[:1].isdigit()][:max_symbols]:
        cik = mp.get(sym.upper().replace(".", "-")) or mp.get(sym.upper())
        if not cik:
            missing.append(sym)
            continue
        try:
            rows = parse_submissions(_fetch(SUBMISSIONS_URL.format(cik), fetch_json), sym.upper(), since)
        except Exception as e:  # noqa: BLE001 - 한 종목 실패가 나머지를 막지 않게
            failed.append(f"{sym}: {type(e).__name__}")
            continue
        for r in rows:
            if session.scalar(select(Disclosure.id).where(Disclosure.receipt_no == r["receipt_no"])):
                continue
            res = na.analyze(r["title"], "")
            ev = (["earnings"] if "2.02" in r["items"] else []) + [e for e in res.events if e != "earnings"]
            session.add(Disclosure(source="SEC", receipt_no=r["receipt_no"], corp_code=r["corp_code"], corp_name=r["corp_name"],
                                   symbol=r["symbol"], title=r["title"], filed_at=r["filed_at"], url=r["url"],
                                   sentiment=res.sentiment, events=ev, collected_at=datetime.now(UTC)))
            added += 1
    # 상태 기록(ops 'sec_filings')은 호출한 쪽이 세션을 닫은 뒤에 — 같은 DB 에 쓰기 잠금이 겹치지 않게
    return {"added": added, "missing_cik": missing[:20], "failed": failed[:20], "at": datetime.now(UTC).isoformat()}


__all__ = ["collect", "parse_submissions", "cik_map", "FORMS", "ITEMS_8K"]

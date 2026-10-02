"""다계좌 · 세금 · 배당 — 여러 증권사·계좌(일반·ISA·연금·해외)를 한 곳에서.

계좌는 직접 입력한다 (보유 종목 · 평균단가 · 현금 · 올해 실현손익). 이 시스템의 장부(paper·live·us-paper)는 자동으로 함께 보인다.
세금은 '지금 전부 판다면'과 '올해 배당'을 대략 계산한다 — 신고용이 아니다. 세법은 매년 바뀌므로 RULES 의 수치를 확인할 것.

규칙 (2026년 기준 개인 · 대주주 아님)
  · 국내 주식 일반계좌: 매매차익 비과세 · 매도 시 증권거래세(비용 모델) · 배당소득세 15.4% 원천징수
  · 해외 주식: 양도차익 연 250만원 공제 후 22% (다른 해외 계좌와 합산) · 배당 15.4% 수준 (미국 원천 15%)
  · ISA: 계좌 안에서 손익 통산 → 순이익 200만원(서민형 400만원) 비과세, 넘는 부분 9.9% 분리과세 (해지·만기 시)
  · 연금저축·IRP: 과세 이연 → 연금 수령 시 연금소득세 3.3~5.5% (한도 초과·중도 인출은 16.5%)
  · 금융소득(이자+배당) 연 2,000만원 초과 → 종합과세 대상 (경고)
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime

from . import ops

RULES = {
    "year": 2026,
    "div_tax": 0.154, "us_div_withholding": 0.15, "overseas_cg_rate": 0.22, "overseas_cg_deduction": 2_500_000,
    "isa_exempt": 2_000_000, "isa_exempt_low_income": 4_000_000, "isa_rate": 0.099,
    "pension_rate_low": 0.033, "pension_rate_high": 0.055, "pension_early": 0.165,
    "financial_income_threshold": 20_000_000,
}
TYPES = {"general": "일반 (국내)", "overseas": "일반 (해외)", "isa": "ISA", "pension": "연금저축", "irp": "IRP", "system": "시스템 장부"}
KEY = "accounts"


def load(engine) -> list[dict]:
    return list(ops.get_state(engine, KEY).get("accounts") or [])


def _save(engine, accts: list[dict]) -> None:
    ops.set_state(engine, KEY, {"accounts": accts, "at": datetime.now(UTC).isoformat()})


def _clean_holdings(rows) -> list[dict]:
    out = []
    for h in rows or []:
        sym = str(h.get("symbol") or "").strip().upper()
        if not re.fullmatch(r"[0-9A-Z.\-]{1,12}", sym):
            raise ValueError(f"종목 코드 형식: {sym!r}")
        qty, avg = float(h.get("qty") or 0), float(h.get("avg_price") or 0)
        if qty < 0 or avg < 0:
            raise ValueError("수량·평균단가는 0 이상")
        if qty:
            out.append({"symbol": sym, "qty": qty, "avg_price": avg})
    return out


def upsert(engine, body: dict) -> dict:
    """계좌 추가/수정. body: {id?, name, type, broker?, cash?, holdings: [{symbol, qty, avg_price}], realized_ytd?, low_income?}"""
    typ = body.get("type") or "general"
    if typ not in TYPES or typ == "system":
        raise ValueError(f"계좌 종류: {', '.join(k for k in TYPES if k != 'system')}")
    name = str(body.get("name") or TYPES[typ]).strip()[:40]
    acct = {"id": body.get("id") or uuid.uuid4().hex[:10], "name": name, "type": typ, "broker": str(body.get("broker") or "")[:30],
            "cash": float(body.get("cash") or 0), "holdings": _clean_holdings(body.get("holdings")),
            "realized_ytd": float(body.get("realized_ytd") or 0), "dividends_ytd": float(body.get("dividends_ytd") or 0),
            "low_income": bool(body.get("low_income")), "updated_at": datetime.now(UTC).isoformat()}
    accts = [a for a in load(engine) if a["id"] != acct["id"]] + [acct]
    _save(engine, accts)
    return acct


def delete(engine, acct_id: str) -> bool:
    accts = load(engine)
    new = [a for a in accts if a["id"] != acct_id]
    _save(engine, new)
    return len(new) < len(accts)


def _is_kr(sym: str) -> bool:
    return sym[:1].isdigit()


def evaluate(accts: list[dict], prices: dict[str, float], fx: float, divs: dict[str, dict], today: date | None = None) -> dict:
    """prices: 원래 통화 가격 · fx: 원/달러 · divs: {종목: {rate(주당 연배당), ex_date}}"""
    today = today or datetime.now(UTC).date()
    rows, tot = [], {"value": 0.0, "cost": 0.0, "div_12m": 0.0, "div_tax": 0.0, "tax_now": 0.0}
    overseas_gain_all = 0.0
    fin_income = 0.0
    for a in accts:
        typ = a.get("type", "general")
        hold = []
        v = c = dv = 0.0
        for h in a.get("holdings") or []:
            s = h["symbol"]
            kr = _is_kr(s)
            px = prices.get(s)
            k = 1.0 if kr else fx
            value = (px or h["avg_price"]) * h["qty"] * k
            cost = h["avg_price"] * h["qty"] * k
            d = divs.get(s) or {}
            div = (d.get("rate") or 0) * h["qty"] * k
            hold.append({"symbol": s, "qty": h["qty"], "avg_price": h["avg_price"], "price": px, "priced": px is not None,
                         "currency": "KRW" if kr else "USD", "value": round(value), "cost": round(cost), "gain": round(value - cost),
                         "ret": (value / cost - 1) if cost else None, "div_12m": round(div), "ex_date": d.get("ex_date")})
            v, c, dv = v + value, c + cost, dv + div
        cash = float(a.get("cash") or 0) + float(a.get("cash_usd") or 0) * fx
        gain = v - c
        # 지금 전부 판다면 (대략)
        if typ == "isa":
            net = gain + float(a.get("realized_ytd") or 0) + float(a.get("dividends_ytd") or 0) + dv
            ex = RULES["isa_exempt_low_income"] if a.get("low_income") else RULES["isa_exempt"]
            tax_now = max(net - ex, 0) * RULES["isa_rate"]
            div_tax = 0.0
            note = f"ISA 순이익 {net:,.0f}원 중 {min(max(net, 0), ex):,.0f}원 비과세 · 초과분 9.9% (해지·만기 시)"
        elif typ in ("pension", "irp"):
            tax_now, div_tax = 0.0, 0.0
            note = "과세 이연 — 연금으로 받을 때 3.3~5.5% (중도 인출은 16.5%)"
        else:
            ov_gain = sum(x["gain"] for x in hold if x["currency"] == "USD")
            overseas_gain_all += ov_gain + (float(a.get("realized_ytd") or 0) if typ == "overseas" else 0)
            tax_now = 0.0  # 해외 양도세는 계좌 합산 후 아래에서
            div_tax = dv * RULES["div_tax"]
            fin_income += dv + float(a.get("dividends_ytd") or 0)
            note = "국내 매매차익 비과세 (대주주 아님) · 배당 15.4%" if typ == "general" else "해외 양도차익은 모든 해외 계좌 합산 후 250만원 공제 · 22%"
        rows.append({"id": a.get("id"), "name": a.get("name"), "type": typ, "type_label": TYPES.get(typ, typ), "broker": a.get("broker"),
                     "cash": round(cash), "value": round(v), "total": round(v + cash), "cost": round(c), "gain": round(gain),
                     "ret": (v / c - 1) if c else None, "div_12m": round(dv), "div_tax": round(div_tax), "tax_now": round(tax_now),
                     "note": note, "holdings": sorted(hold, key=lambda x: -x["value"]), "readonly": typ == "system",
                     "realized_ytd": a.get("realized_ytd", 0)})
        tot["value"] += v + cash
        tot["cost"] += c
        tot["div_12m"] += dv
        tot["div_tax"] += div_tax
        tot["tax_now"] += tax_now
    ov_taxable = max(overseas_gain_all - RULES["overseas_cg_deduction"], 0)
    ov_tax = ov_taxable * RULES["overseas_cg_rate"]
    tot["tax_now"] += ov_tax
    tips = []
    if overseas_gain_all > RULES["overseas_cg_deduction"]:
        losers = [h for r in rows for h in r["holdings"] if h["currency"] == "USD" and h["gain"] < 0]
        if losers:
            cut = sum(-h["gain"] for h in losers)
            tips.append(f"해외 양도차익이 공제(250만원)를 넘습니다 — 손실 중인 해외 종목(합계 {cut:,.0f}원)을 연내 매도해 손익을 상계하면 "
                        f"양도세를 최대 {min(cut, ov_taxable) * RULES['overseas_cg_rate']:,.0f}원 줄일 수 있습니다 (다시 사도 됨 · 워시세일 규정 없음)")
    elif 0 < overseas_gain_all:
        tips.append(f"올해 해외 양도차익 {overseas_gain_all:,.0f}원 — 공제 250만원 안이라 지금 팔면 양도세 0원. "
                    f"남은 공제 {RULES['overseas_cg_deduction'] - overseas_gain_all:,.0f}원")
    if fin_income > RULES["financial_income_threshold"] * 0.8:
        tips.append(f"예상 금융소득 {fin_income:,.0f}원 — 2,000만원을 넘으면 종합과세 대상 (배당을 ISA·연금 계좌로 옮기는 것 검토)")
    isa = [r for r in rows if r["type"] == "isa"]
    if not isa and any(r["div_12m"] for r in rows if r["type"] in ("general", "overseas")):
        tips.append("ISA 계좌가 없습니다 — 배당주를 ISA 에 두면 200만원까지 배당·차익이 비과세")
    upcoming = sorted(({"symbol": h["symbol"], "account": r["name"], "ex_date": h["ex_date"], "div": h["div_12m"]}
                       for r in rows for h in r["holdings"] if h.get("ex_date") and str(h["ex_date"]) >= today.isoformat()),
                      key=lambda x: x["ex_date"])
    return {"accounts": rows, "total": {k: round(v) for k, v in tot.items()} | {"gain": round(tot["value"] - tot["cost"] - sum(r["cash"] for r in rows))},
            "overseas": {"gain_ytd_est": round(overseas_gain_all), "deduction": RULES["overseas_cg_deduction"], "taxable": round(ov_taxable),
                         "tax": round(ov_tax)},
            "financial_income_est": round(fin_income), "tips": tips, "dividend_calendar": upcoming[:30], "fx": fx, "rules": RULES}


def summary(app) -> dict:
    """앱 데이터(가격 · 환율 · 배당 · 시스템 장부)로 계좌 평가."""
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import Instrument, SystemState
    from .engines.market_intel import load_macro
    accts = load(app.engine)
    # 시스템 장부도 함께 (읽기 전용)
    sys_books = []
    for mode in ("paper", "live", "us-paper"):
        try:
            pf = app.load_portfolio(mode)
        except Exception:  # noqa: BLE001, S112 - 장부가 없을 수 있다
            continue
        if not pf.positions:
            continue
        us = mode.startswith("us-")
        sys_books.append({"id": f"sys:{mode}", "name": f"{mode.upper()} 장부 (시스템)", "type": "system",
                          "cash": 0.0 if us else pf.cash, "cash_usd": pf.cash if us else 0.0,
                          "holdings": [{"symbol": s, "qty": p.qty, "avg_price": p.avg_price} for s, p in pf.positions.items() if p.qty]})
    bars, _ = app._all_bars()
    prices = {s: float(b["close"].iloc[-1]) for s, b in bars.items() if len(b)}
    with session_scope(app.engine) as s:
        fxs = load_macro(s, ["DEXKOUS"], days=30).get("DEXKOUS")
        names = {i.symbol: i.name for i in s.scalars(select(Instrument))}
        profs = {r.key.split(":", 1)[1]: (r.value or {}).get("data") or {} for r in s.scalars(select(SystemState).where(SystemState.key.like("profile:%")))}
    fx = float(fxs.iloc[-1]) if fxs is not None and len(fxs) else 1350.0
    divs = {}
    for sym, p in profs.items():
        st = p.get("stats") or {}
        ex = next((e.get("date") for e in p.get("events") or [] if e.get("kind") == "ex_div"), None)
        if st.get("div_rate") or ex:
            divs[sym] = {"rate": st.get("div_rate"), "ex_date": ex}
    out = evaluate(accts + sys_books, prices, fx, divs)
    for r in out["accounts"]:
        for h in r["holdings"]:
            h["name"] = names.get(h["symbol"], h["symbol"])
    return out | {"fx_source": "FRED DEXKOUS" if fxs is not None and len(fxs) else "기본값 1,350원 (FRED_API_KEY 없음)",
                  "no_div_info": sorted({h["symbol"] for r in out["accounts"] for h in r["holdings"] if h["symbol"] not in divs})[:20],
                  "types": {k: v for k, v in TYPES.items() if k != "system"}}


__all__ = ["load", "upsert", "delete", "evaluate", "summary", "RULES", "TYPES"]

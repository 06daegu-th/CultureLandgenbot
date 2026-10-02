"""미국 주식 주문표 — 실행 경로가 막혀 있던 미국 쪽을 '사람이 증권사 앱에서 그대로 따라 넣는 표'로 연다.

KIS 해외주식 주문 API 연동은 아직 아니다 (모의 서버로 검증하기 전에는 실주문 경로를 열지 않는다).
대신 이 표는 실제 돈 계산을 정직하게 한다:
  · 목표: 시스템의 미국 가상 장부(us-paper) 비중을 그대로 따라가기 (또는 직접 넣은 목표 비중)
  · 수량: 정수 주 (소수점 주문 미지원 가정) · 매도 먼저 → 매수
  · 비용: 수수료(편도, QUANT_US_COMMISSION_BPS · 기본 25bp) + 환전 스프레드(QUANT_FX_SPREAD_BPS · 기본 25bp, 원→달러가 필요한 금액에만)
  · 세금(추정): 해외주식 양도소득세 22%(지방세 포함) · 연 250만원 기본공제 — 올해 이미 실현한 이익(ytd_gain_krw)과 합산.
    세법은 바뀔 수 있으니 매년 확인 (QUANT_US_TAX_RATE · QUANT_US_TAX_DEDUCTION 로 조정)
  · 환율: FRED DEXKOUS 최신값 (없으면 1,350원 가정 — 표에 표시)
"""

from __future__ import annotations

import csv
import io
import os

TAX_RATE = float(os.environ.get("QUANT_US_TAX_RATE") or 0.22)
TAX_DEDUCTION = float(os.environ.get("QUANT_US_TAX_DEDUCTION") or 2_500_000)


def _bps(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default) / 1e4
    except ValueError:
        return default / 1e4


def fx_rate(app) -> tuple[float, str]:
    from .data.db import session_scope
    from .engines.market_intel import load_macro
    with session_scope(app.engine) as s:
        fx = load_macro(s, ["DEXKOUS"], days=30).get("DEXKOUS")
    return (float(fx.iloc[-1]), "FRED DEXKOUS") if fx is not None and len(fx) else (1350.0, "기본값 1,350원 (FRED_API_KEY 없음 — 실제 환율로 바꿔 계산하세요)")


def after_tax(gain_krw: float, ytd_gain_krw: float = 0.0) -> dict:
    """연간 실현 이익 기준 세금 추정 (손실은 같은 해 이익과 상계)."""
    before = max(0.0, ytd_gain_krw - TAX_DEDUCTION) * TAX_RATE
    after = max(0.0, ytd_gain_krw + gain_krw - TAX_DEDUCTION) * TAX_RATE
    tax = after - before
    return {"gain_krw": round(gain_krw), "tax_krw": round(tax), "net_krw": round(gain_krw - tax), "rate": TAX_RATE, "deduction": TAX_DEDUCTION,
            "deduction_left": round(max(0.0, TAX_DEDUCTION - max(0.0, ytd_gain_krw)))}


def sheet(app, holdings: dict[str, int] | None = None, cash_usd: float = 0.0, cash_krw: float = 0.0,
          targets: dict[str, float] | None = None, avg_cost: dict[str, float] | None = None, ytd_gain_krw: float = 0.0,
          prices: dict[str, float] | None = None) -> dict:
    holdings = {k.upper(): int(v) for k, v in (holdings or {}).items() if int(v) > 0}
    bars, _ = app._all_bars()
    if targets is None:  # 시스템의 미국 가상 장부 비중을 따라간다
        pf = app.load_portfolio("us-paper")
        px = {s: float(bars[s]["close"].iloc[-1]) for s in pf.positions if s in bars and len(bars[s])}
        eq = pf.equity(px) if px or pf.cash else 0
        targets = {s: p.qty * px[s] / eq for s, p in pf.positions.items() if p.qty and s in px and eq} if eq else {}
        src = "시스템 미국 가상 장부(us-paper) 비중"
    else:
        tot = sum(max(0.0, float(v)) for v in targets.values()) or 1
        targets = {k.upper(): max(0.0, float(v)) / tot for k, v in targets.items()}
        src = "직접 입력한 목표 비중"
    if not targets:
        return {"error": "목표 비중이 없습니다 — 미국 가상 장부가 비어 있거나(먼저 미국 사이클 실행) 목표를 직접 넣으세요", "rows": []}
    syms = sorted(set(targets) | set(holdings))
    price = {s: float(bars[s]["close"].iloc[-1]) for s in syms if s in bars and len(bars[s])}
    manual = {k.upper(): float(v) for k, v in (prices or {}).items() if float(v) > 0}
    price |= manual  # 직접 넣은 가격이 우선 (지금 앱에서 보이는 가격)
    missing = [s for s in syms if s not in price]
    fx, fx_src = fx_rate(app)
    comm, spread = _bps("QUANT_US_COMMISSION_BPS", 25), _bps("QUANT_FX_SPREAD_BPS", 25)
    equity = cash_usd + cash_krw / fx + sum(holdings.get(s, 0) * price.get(s, 0) for s in syms)
    rows = []
    for s in syms:
        if s not in price:
            continue
        p = price[s]
        tq = int(targets.get(s, 0) * equity * (1 - comm) // p)
        dq = tq - holdings.get(s, 0)
        if dq == 0 and not holdings.get(s):
            continue
        amt = abs(dq) * p
        row = {"symbol": s, "price": round(p, 2), "held": holdings.get(s, 0), "target_qty": tq, "order_qty": dq,
               "side": "매수" if dq > 0 else "매도" if dq < 0 else "유지", "amount_usd": round(amt, 2), "commission_usd": round(amt * comm, 2),
               "target_weight": round(targets.get(s, 0), 4)}
        if dq < 0 and avg_cost and s in avg_cost:
            row["gain_krw"] = round((p - float(avg_cost[s])) * (-dq) * fx)
        rows.append(row)
    rows.sort(key=lambda r: (r["order_qty"] > 0, -r["amount_usd"]))  # 매도 먼저
    sells = sum(r["amount_usd"] - r["commission_usd"] for r in rows if r["order_qty"] < 0)
    buys = sum(r["amount_usd"] + r["commission_usd"] for r in rows if r["order_qty"] > 0)
    need_usd = max(0.0, buys - sells - cash_usd)
    fx_cost_krw = need_usd * fx * spread
    gain = sum(r.get("gain_krw", 0) for r in rows)
    tax = after_tax(gain, ytd_gain_krw) if gain else None
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["순서", "종목", "구분", "수량", "참고가(USD)", "금액(USD)"])
    for i, r in enumerate([r for r in rows if r["order_qty"]], 1):
        w.writerow([i, r["symbol"], r["side"], abs(r["order_qty"]), r["price"], r["amount_usd"]])
    return {"source": src, "fx": fx, "fx_source": fx_src, "equity_usd": round(equity, 2), "rows": rows, "missing": missing,
            "price_source": "직접 입력" if manual and set(manual) >= set(syms) else "마지막 종가" + (" + 직접 입력" if manual else ""),
            "missing_hint": "미국 시세가 없습니다 — '가격 직접 입력' 칸에 `종목,가격` 을 넣거나 미국 데이터 수집 후 다시" if missing else None,
            "summary": {"sell_usd": round(sells, 2), "buy_usd": round(buys, 2), "need_usd": round(need_usd, 2), "need_krw": round(need_usd * fx),
                        "fx_cost_krw": round(fx_cost_krw), "commission_usd": round(sum(r["commission_usd"] for r in rows), 2),
                        "realized_gain_krw": round(gain), "tax": tax},
            "csv": out.getvalue(),
            "note": ("KIS 해외 주문 API 연동 전 — 이 표를 보고 증권사 앱에서 직접 주문하세요 (지정가 권장 · 미국 정규장 23:30~06:00 KST, 서머타임 22:30~05:00) · "
                     f"세금은 추정(양도세 {TAX_RATE:.0%}, 연 {TAX_DEDUCTION:,.0f}원 공제 — 세법 확인 필요) · 가격은 마지막 종가 또는 직접 입력값")}


__all__ = ["sheet", "after_tax", "fx_rate", "TAX_RATE", "TAX_DEDUCTION"]

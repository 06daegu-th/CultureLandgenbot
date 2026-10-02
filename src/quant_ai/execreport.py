"""거래 비용 실제 보정 · 호가창 분석.

costs()  장부별(실전 live · 섀도 shadow · 가상 paper): 체결률(체결 수량/주문 수량) · 실제 슬리피지(체결가 vs 주문 시점 기준가) ·
         수수료(실제 fee / 체결금액) · 세금(매도 거래세) → 합계 vs 비용 모델의 가정. 가상 장부는 모델로 계산된 값이라
         '실측'은 live(증권사 체결)만 — 그대로 표시한다.
book()   KIS 실시간 호가(10단계)로: 스프레드 · 상위 5호가 잔량(금액) · 매수/매도 잔량 불균형 · 이 수량을 지금 사면
         호가를 몇 단계 먹고 평균 얼마에 사는지(시장 충격) — 호가가 없으면(KIS 미설정·장외) 이유를 말한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import ops
from .asof import label

LOW_ADV_KRW = 3e9  # 20일 평균 거래대금 30억원 미만 = 저유동
SEG_LABEL = {"low_liq": "거래량 적은 종목 (20일 평균 거래대금 30억 미만)", "big_move": "급등락 날 (그날 ±5% 이상)",
             "vi": "VI 가능성 (그날 고가·저가가 전일 대비 ±10% 이상 — 근사)", "normal": "보통"}


def _segment(b: pd.DataFrame | None, at) -> str:
    """주문 시점의 상황 분류 — 일봉으로 근사 (VI 발동 기록은 KIS 실시간으로만 정확히 알 수 있음)."""
    if b is None or len(b) < 3 or at is None:
        return "normal"
    idx = pd.DatetimeIndex(b.index)
    t = pd.Timestamp(at)
    t = t.tz_convert(idx.tz) if idx.tz is not None and t.tzinfo else t.tz_localize(idx.tz) if idx.tz is not None else t.tz_localize(None)
    i = int(idx.searchsorted(t.normalize(), side="right")) - 1
    if i < 1:
        return "normal"
    prev = float(b["close"].iloc[i - 1])
    hi = float(b["high"].iloc[i]) if "high" in b else float(b["close"].iloc[i])
    lo = float(b["low"].iloc[i]) if "low" in b else float(b["close"].iloc[i])
    if prev and max(hi / prev - 1, 1 - lo / prev) >= 0.10:
        return "vi"
    if prev and abs(float(b["close"].iloc[i]) / prev - 1) >= 0.05:
        return "big_move"
    adv = float((b["close"].astype(float) * b["volume"].astype(float)).iloc[max(0, i - 20):i].mean()) if "volume" in b else None
    if adv is not None and adv < LOW_ADV_KRW:
        return "low_liq"
    return "normal"


def segments(app, filled: list, slip: list[float]) -> list[dict]:
    """슬리피지를 상황별로 따로 (저유동 · 급등락 · VI 근사 · 보통) — 평균 하나로 뭉개면 위험한 날의 비용이 가려진다."""
    if not filled:
        return []
    bars, _ = app._all_bars()
    groups: dict[str, list[float]] = {}
    for o, x in zip(filled, slip, strict=False):
        seg = _segment(bars.get(o.symbol), o.created_at) if o.symbol[:1].isdigit() else "normal"
        groups.setdefault(seg, []).append(x)
    return [{"key": k, "label": SEG_LABEL[k], "n": len(v), "mean_bps": round(float(np.mean(v)), 2),
             "p90_bps": round(float(np.percentile(v, 90)), 2) if len(v) >= 5 else None, "enough": len(v) >= 20}
            for k, v in sorted(groups.items(), key=lambda kv: list(SEG_LABEL).index(kv[0]))]


def costs(app, days: int = 90) -> dict:
    from .data.db import session_scope
    from .data.models import FillRecord, OrderRecord
    since = datetime.now(UTC) - timedelta(days=days)
    c = app.settings.costs
    assumed = {"commission_bps": c.commission_bps, "slippage_bps": c.slippage_bps, "sell_tax_bps": c.sell_tax_bps}
    out = []
    with session_scope(app.engine) as s:
        for mode in ("live", "shadow", "paper"):
            orders = s.execute(select(OrderRecord.id, OrderRecord.side, OrderRecord.qty, OrderRecord.filled_qty, OrderRecord.ref_price,
                                      OrderRecord.avg_price, OrderRecord.status, OrderRecord.symbol, OrderRecord.created_at)
                               .where(OrderRecord.mode == mode, OrderRecord.created_at >= since)).all()
            if not orders:
                continue
            ids = [o.id for o in orders]
            fees = dict(s.execute(select(FillRecord.order_id, FillRecord.fee).where(FillRecord.order_id.in_(ids))).all())
            filled = [o for o in orders if o.status in ("filled", "partial") and o.avg_price and o.ref_price]
            want = sum(o.qty for o in orders if o.qty)
            got = sum(o.filled_qty or (o.qty if o.status == "filled" else 0) for o in orders)
            slip = [((o.avg_price - o.ref_price) / o.ref_price * 1e4) * (1 if o.side == "buy" else -1) for o in filled]
            notional = {o.id: (o.filled_qty or o.qty) * o.avg_price for o in filled}
            comm = [fees[o.id] / notional[o.id] * 1e4 for o in filled if o.id in fees and notional[o.id]]
            sells = [o for o in filled if o.side == "sell"]
            out.append({"mode": mode, "orders": len(orders), "fill_rate": round(got / want, 4) if want else None,
                        "n_filled": len(filled), "slippage_bps": round(float(np.mean(slip)), 2) if slip else None,
                        "slippage_p90_bps": round(float(np.percentile(slip, 90)), 2) if len(slip) >= 5 else None,
                        "commission_bps": round(float(np.mean(comm)), 2) if comm else None, "n_sells": len(sells),
                        "measured": mode == "live", "segments": segments(app, filled, slip),
                        "roundtrip_bps": round(2 * (float(np.mean(slip)) if slip else c.slippage_bps) + 2 * (float(np.mean(comm)) if comm else c.commission_bps)
                                               + c.sell_tax_bps, 1)})
    model = ops.get_state(app.engine, "slippage_model").get("model") or {}
    assumed_rt = 2 * (c.slippage_bps + c.commission_bps) + c.sell_tax_bps
    live = next((r for r in out if r["mode"] == "live"), None)
    return {"rows": out, "assumed": assumed | {"roundtrip_bps": round(assumed_rt, 1)}, "calibrated": model or None,
            "verdict": ("실측 체결 없음 — KIS 모의투자로 50건 이상 쌓이면 비용 가정을 실측으로 교체" if not live or not live["n_filled"] else
                        f"실측 왕복 {live['roundtrip_bps']}bp vs 가정 {assumed_rt:.1f}bp — " +
                        ("가정이 낙관적 (실제가 더 비쌈) → 보정 필요" if live["roundtrip_bps"] > assumed_rt * 1.2 else "가정과 비슷")),
            "note": "슬리피지 = (체결가 − 주문 시점 기준가)/기준가 (매수 +, 매도는 부호 반대) · 가상/섀도 장부는 비용 모델로 체결되므로 '검증'이 아니라 참고"}


def book(app, symbol: str, qty: int | None = None) -> dict:
    from .trading.exec_sim import walk_book
    books = ops.get_state(app.engine, "orderbook") or {}
    b = books.get(symbol.split(".")[0])
    if not b:
        why = "KIS 가 설정되지 않아 실시간 호가가 없습니다 (REST 일봉·시세만)" if app.settings.broker != "kis" else "지금 이 종목 호가 구독이 없거나 장외입니다 (보유 상위 종목만 구독)"
        return {"symbol": symbol, "available": False, "message": why}
    asks, bids = [tuple(x) for x in b["asks"]], [tuple(x) for x in b["bids"]]
    a1, b1 = asks[0][0], bids[0][0]
    mid = (a1 + b1) / 2
    ask5 = sum(p * q for p, q in asks[:5])
    bid5 = sum(p * q for p, q in bids[:5])
    imb = (bid5 - ask5) / (bid5 + ask5) if bid5 + ask5 else 0.0
    res = {"symbol": symbol, "available": True, "at": b.get("at"), "as_of": label(b.get("at")), "ask1": a1, "bid1": b1,
           "spread_bps": round((a1 - b1) / mid * 1e4, 2), "ask5_krw": round(ask5), "bid5_krw": round(bid5), "imbalance": round(imb, 3),
           "read": "매수 잔량 우위 (받치는 힘)" if imb > 0.2 else "매도 잔량 우위 (누르는 힘)" if imb < -0.2 else "균형",
           "asks": asks[:10], "bids": bids[:10]}
    if qty:
        got, avg, levels = walk_book("buy", int(qty), {"asks": asks, "bids": bids})
        res["impact"] = {"qty": int(qty), "filled": got, "avg": round(avg, 2) if got else None, "levels": levels,
                         "cost_bps": round((avg - mid) / mid * 1e4, 2) if got else None,
                         "note": "호가 잔량만으로 계산 (숨은 주문·재충전 없음 → 보수적)"}
    return res


__all__ = ["costs", "book"]

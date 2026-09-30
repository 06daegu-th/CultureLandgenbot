"""섹터 엔진 — 종목이 속한 업종이 지금 강한가 약한가.

업종 분류: 무료 공식 분류가 없어 Yahoo 의 sector · industry 를 한국어 업종으로 옮겨 쓴다.
  · 이미 열어 본 종목의 상세(프로필)에서 먼저 채우고,
  · 야간 작업이 나머지 종목을 조금씩(한 번에 8개, 요청 사이 2초) 채운다. 한 번 채우면 30일 동안 쓴다.
업종 통계: 같은 업종 종목을 같은 비중으로 묶어 5·20일 수익률, 폭(20일선 위 비율), 지수 대비 상대강도(RS), 순위.
판단 재료: 종목마다 "업종 · 순위 · 상대강도 · 폭 · 같은 업종 상위 종목 움직임" 을 AI 에게 준다.
업종 한도: 리스크 엔진이 한 업종에 40% 넘게 쏠리지 않게 막는다.
v13: 국내 종목은 공식 WICS 분류(와이즈인덱스, 키 불필요)를 먼저 쓴다 → Yahoo 는 해외·미분류 보충.
     섹터 로테이션(RRG): 업종마다 상대강도 비율(RS-Ratio)과 그 모멘텀(RS-Momentum) → 주도·약화·침체·개선 4분면 + 최근 8주 궤적.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from .. import ops

log = logging.getLogger(__name__)

# industry 키워드(소문자) → 한국어 업종 (먼저 맞는 것)
INDUSTRY_KO = [
    ("semiconductor", "반도체"), ("electronic component", "전자부품"), ("consumer electronics", "전자·가전"),
    ("auto", "자동차"), ("bank", "은행"), ("insurance", "보험"), ("capital markets", "증권"), ("credit", "금융"),
    ("biotech", "바이오"), ("drug", "제약"), ("medical", "의료기기"), ("health", "헬스케어"),
    ("chemical", "화학"), ("steel", "철강"), ("metal", "금속"), ("aerospace", "방산·항공"), ("defense", "방산·항공"),
    ("ship", "조선"), ("marine", "해운"), ("airline", "항공"), ("oil", "에너지"), ("gas", "에너지"),
    ("utilities", "유틸리티"), ("telecom", "통신"), ("internet", "인터넷"), ("software", "소프트웨어"),
    ("entertainment", "엔터·미디어"), ("game", "게임"), ("electronic gaming", "게임"), ("retail", "유통"),
    ("food", "음식료"), ("beverage", "음식료"), ("tobacco", "음식료"), ("construction", "건설"),
    ("engineering", "건설"), ("machinery", "기계"), ("electrical equipment", "전기장비"), ("battery", "2차전지"),
    ("solar", "신재생"), ("real estate", "부동산"), ("reit", "부동산"), ("apparel", "의류"), ("cosmetic", "화장품"),
    ("household", "생활용품"), ("travel", "여행·레저"), ("lodging", "여행·레저"), ("computer", "IT 하드웨어"),
    ("communication equipment", "통신장비"), ("information technology", "IT 서비스"),
]
SECTOR_KO = {"Technology": "기술", "Financial Services": "금융", "Healthcare": "헬스케어", "Industrials": "산업재",
             "Consumer Cyclical": "경기소비재", "Consumer Defensive": "필수소비재", "Energy": "에너지",
             "Basic Materials": "소재", "Communication Services": "커뮤니케이션", "Utilities": "유틸리티",
             "Real Estate": "부동산"}
MAP_TTL = timedelta(days=30)


def to_korean(sector: str | None, industry: str | None) -> str | None:
    ind = (industry or "").lower()
    for key, ko in INDUSTRY_KO:
        if key in ind:
            return ko
    return SECTOR_KO.get(sector or "", sector) if sector else None


def fetch_sector(symbol: str) -> dict:
    """Yahoo 에서 sector · industry 만 (국내는 .KS → .KQ)."""
    import yfinance as yf
    cands = [f"{symbol}.KS", f"{symbol}.KQ"] if symbol.isdigit() else [symbol]
    last = None
    for c in cands:
        try:
            info = yf.Ticker(c).get_info() or {}
        except Exception as e:  # noqa: BLE001
            last = e
            continue
        if info.get("sector") or info.get("industry"):
            return {"sector": info.get("sector"), "industry": info.get("industry")}
    raise RuntimeError(f"업종 정보 없음: {last or symbol}")


def sector_map(engine) -> dict[str, str]:
    """국내는 WICS(공식) 우선, 나머지는 Yahoo 기반 한국어 업종."""
    mp = dict(ops.get_state(engine, "sector_map").get("map", {}))
    mp.update(ops.get_state(engine, "wics_map").get("map", {}))
    return mp


QUADRANTS = {("up", "up"): "주도 (Leading)", ("up", "down"): "약화 (Weakening)",
             ("down", "down"): "침체 (Lagging)", ("down", "up"): "개선 (Improving)"}


def rotation(bars: dict[str, pd.DataFrame], mp: dict[str, str], bench: pd.DataFrame | None, trail: int = 8,
             step: int = 5, min_n: int = 1) -> list[dict]:
    """RRG: 업종 지수(동일가중) ÷ 벤치마크 = RS. RS-Ratio = 100·RS/RS 20일 평균, RS-Mom = 100·Ratio/Ratio 5일 전.
    둘 다 100 위 = 주도, Ratio 위·Mom 아래 = 약화, 둘 다 아래 = 침체, Ratio 아래·Mom 위 = 개선 (시계 방향으로 돈다)."""
    if bench is None or len(bench) < 60:
        return []
    groups: dict[str, list[str]] = {}
    for sym, sec in mp.items():
        if sym in bars and len(bars[sym]) >= 60:
            groups.setdefault(sec, []).append(sym)
    bret = bench["close"].astype(float).pct_change()
    out = []
    for sec, syms in groups.items():
        if len(syms) < min_n:
            continue
        r = pd.DataFrame({s: bars[s]["close"].astype(float).pct_change() for s in syms}).mean(axis=1)
        idx = (1 + r.fillna(0)).cumprod()
        b = (1 + bret.reindex(idx.index).fillna(0)).cumprod()
        rs = idx / b
        ratio = 100 * rs / rs.rolling(20).mean()
        mom = 100 * ratio / ratio.shift(step)
        pts = pd.DataFrame({"x": ratio, "y": mom}).dropna()
        if len(pts) < step * trail:
            continue
        tr = pts.iloc[::-step].iloc[:trail].iloc[::-1]
        x, y = float(pts["x"].iloc[-1]), float(pts["y"].iloc[-1])
        q = QUADRANTS[("up" if x >= 100 else "down", "up" if y >= 100 else "down")]
        out.append({"sector": sec, "n": len(syms), "rs_ratio": round(x, 3), "rs_mom": round(y, 3), "quadrant": q,
                    "trail": [{"x": round(float(a), 3), "y": round(float(c), 3), "date": str(t.date())}
                              for t, a, c in zip(tr.index, tr["x"], tr["y"])]})
    return sorted(out, key=lambda r: (-r["rs_ratio"]))


def fill_map(engine, symbols: list[str], fetch=None, limit: int = 8, pause: float = 2.0,
             now: datetime | None = None) -> dict:
    """업종 지도 채우기: 프로필 캐시 → 없으면 Yahoo (한 번에 limit 개). 실패한 종목은 7일 뒤 다시."""
    now = now or datetime.now(UTC)
    st = ops.get_state(engine, "sector_map")
    mp, raw, fails = dict(st.get("map", {})), dict(st.get("raw", {})), dict(st.get("fails", {}))
    # 1) 이미 받아 둔 종목 상세에서
    from ..data.db import session_scope
    from ..data.models import SystemState
    with session_scope(engine) as s:
        for r in s.query(SystemState).filter(SystemState.key.like("profile:%")):
            sym = r.key.split(":", 1)[1]
            comp = ((r.value or {}).get("data") or {}).get("company") or {}
            ko = to_korean(comp.get("sector"), comp.get("industry"))
            if ko and sym not in mp:
                mp[sym], raw[sym] = ko, {"sector": comp.get("sector"), "industry": comp.get("industry"), "at": now.isoformat()}
    # 2) 나머지는 조금씩
    todo = [x for x in symbols if (x not in mp or now - datetime.fromisoformat(raw.get(x, {}).get("at", "2000-01-01T00:00:00+00:00")) > MAP_TTL)
            and not (fails.get(x) and now - datetime.fromisoformat(fails[x]) < timedelta(days=7))][:limit]
    got = []
    for i, sym in enumerate(todo):
        try:
            info = (fetch or fetch_sector)(sym)
            ko = to_korean(info.get("sector"), info.get("industry"))
            if ko:
                mp[sym], raw[sym] = ko, {**info, "at": now.isoformat()}
                got.append(sym)
        except Exception as e:  # noqa: BLE001
            fails[sym] = now.isoformat()
            log.debug("업종 %s 실패: %s", sym, e)
        if pause and i < len(todo) - 1:
            time.sleep(pause)
    ops.set_state(engine, "sector_map", {"map": mp, "raw": raw, "fails": fails, "at": now.isoformat()})
    return {"mapped": len(mp), "new": got, "tried": len(todo)}


def sector_stats(bars: dict[str, pd.DataFrame], mp: dict[str, str], bench: pd.DataFrame | None = None,
                 names: dict[str, str] | None = None) -> list[dict]:
    names = names or {}
    groups: dict[str, list[str]] = {}
    for sym, sec in mp.items():
        if sym in bars and len(bars[sym]) >= 25:
            groups.setdefault(sec, []).append(sym)
    bret20 = float(bench["close"].iloc[-1] / bench["close"].iloc[-21] - 1) if bench is not None and len(bench) > 21 else 0.0
    out = []
    for sec, syms in groups.items():
        r5, r20, above, movers = [], [], [], []
        for s in syms:
            c = bars[s]["close"].astype(float)
            r5.append(float(c.iloc[-1] / c.iloc[-6] - 1))
            r20.append(float(c.iloc[-1] / c.iloc[-21] - 1))
            above.append(float(c.iloc[-1] > c.iloc[-20:].mean()))
            movers.append({"symbol": s, "name": names.get(s, s), "ret_5": r5[-1]})
        out.append({"sector": sec, "n": len(syms), "ret_5": float(np.mean(r5)), "ret_20": float(np.mean(r20)),
                    "breadth": float(np.mean(above)), "rs_20": float(np.mean(r20)) - bret20,
                    "leaders": sorted(movers, key=lambda m: -m["ret_5"])[:3],
                    "laggards": sorted(movers, key=lambda m: m["ret_5"])[:2]})
    out.sort(key=lambda r: -r["rs_20"])
    for i, r in enumerate(out):
        r["rank"] = i + 1
        r["of"] = len(out)
    return out


def for_context(symbol: str, mp: dict[str, str], stats: list[dict]) -> dict:
    sec = mp.get(symbol)
    if not sec:
        return {}
    row = next((r for r in stats if r["sector"] == sec), None)
    if not row:
        return {"sector": sec}
    return {"sector": sec, "rank": f"{row['rank']}/{row['of']}", "ret_20": round(row["ret_20"], 4),
            "rs_20_vs_index": round(row["rs_20"], 4), "breadth_above_ma20": round(row["breadth"], 2),
            "peers_5d": [{"name": m["name"], "ret_5": round(m["ret_5"], 4)} for m in row["leaders"] if m["symbol"] != symbol][:3]}


__all__ = ["to_korean", "fetch_sector", "fill_map", "sector_map", "sector_stats", "for_context", "rotation", "QUADRANTS"]

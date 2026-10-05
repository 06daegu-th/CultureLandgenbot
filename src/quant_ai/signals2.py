"""v28 신호 엔진 2.0 — 여러 정보를 한 점수로 합쳐 '오늘의 매수 후보 / 비중 축소 후보'를 고른다.

원칙
  1) 신호마다 -3 ~ +3 표준 점수. 자료가 없으면 점수를 만들지 않는다 ('정보 없음' — 지어내지 않음).
  2) 가중치는 '말'이 아니라 '과거 결과'로 정한다:
     가격·거래량 신호 7개는 과거 날짜마다 "이 신호가 높았던 종목이 20거래일 뒤 시장보다 올랐나"(순위 상관 IC)를 재고,
     꾸준히 맞은 신호만 크게, 효과가 없으면 0, 거꾸로 꾸준히 맞으면(예: 단기 반등) 부호를 뒤집는다.
     마지막 120거래일은 가중치 학습에서 빼 두었다가(보지 않은 기간) "이 점수대 종목이 실제로 어땠나"를 잰다.
  3) 공시·뉴스·수급·실적·커뮤니티는 아직 과거 자료가 짧아 '검증 전' — 작게만 반영하고 화면에 그렇게 표시한다.
     매일 결과를 저장(signals2_log)해 두었다가 시간이 지나면 이것도 실제 결과로 채점한다.
  4) 거래대금이 작은 종목(국내 20일 평균 10억 미만)·시세가 끊긴 종목은 후보에서 뺀다.
  5) 자동매매에는 아직 쓰지 않는다 — 전진 기록이 기준을 넘으면 그때 연결한다.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from . import ops

log = logging.getLogger(__name__)

HORIZON = 20          # 평가 기간 (거래일)
HOLDOUT = 120         # 가중치 학습에서 뺀 마지막 기간 (보지 않은 기간으로 점수 보정)
STEP = 5              # 과거 평가 날짜 간격
MIN_NAMES = 20        # 한 날짜에 이만큼 종목이 있어야 순위 상관을 잰다
LIVE_WEIGHT = 0.3     # 검증 전 신호(공시·뉴스·수급·실적·커뮤니티)의 반영 비율
MIN_TURNOVER = {"KR": 1e9, "US": 5e6}  # 후보가 되려면 20일 평균 거래대금 (국내 10억원 · 미국 500만달러)
BINS = [-np.inf, -1.5, -0.5, 0.5, 1.5, np.inf]
BIN_LABEL = ["아주 낮음", "낮음", "보통", "높음", "아주 높음"]
LOG_KEY = "signals2_log"

PRICE_SIGNALS = {  # key: (이름, 설명)
    "trend": ("추세", "주가가 50일·200일 평균 위에 있고 평균선이 위로 향하는지"),
    "mom": ("12개월 오름세", "지난 1년(최근 1개월 제외) 동안 다른 종목보다 많이 올랐는지"),
    "high52": ("52주 고점 근처", "1년 중 가장 높은 가격에 얼마나 가까운지 (신고가 돌파)"),
    "volsurge": ("거래대금 급증", "평소보다 거래가 크게 늘며 오르는지(+) 내리는지(-)"),
    "reversal": ("단기 과열·급락", "최근 5일 너무 빨리 오르면 쉬어갈 가능성(-), 급락하면 반등 가능성(+)"),
    "lowvol": ("덜 출렁임", "최근 60일 가격 출렁임이 작은지 (덜 출렁이는 종목이 길게 보면 유리한 경향)"),
    "sector_rs": ("업종 안 강도", "같은 업종 평균보다 최근 20일 더 올랐는지"),
}
LIVE_SIGNALS = {
    "disclosure": ("공시", "최근 7일 공시 종류 — 과거 같은 종류 공시 뒤 5일 초과수익으로 점수"),
    "news": ("뉴스 분위기", "최근 3일 뉴스의 좋고 나쁨 (중요도 가중)"),
    "flow": ("외국인·기관 수급", "최근 5일 순매수가 평소보다 얼마나 큰지"),
    "earnings": ("실적 서프라이즈", "최근 90일 실적이 예상보다 좋았는지"),
    "community": ("커뮤니티 과열", "토론방이 지나치게 낙관적이면 과열(-), 지나치게 비관적이면 역발상(+)"),
}
PRIOR_W = {"trend": 0.2, "mom": 0.25, "high52": 0.15, "volsurge": 0.05, "reversal": 0.15, "lowvol": 0.15, "sector_rs": 0.05}
DISC_PRIOR = {"유상증자": -1.5, "전환사채·BW": -1.0, "자사주 매입": 1.0, "자사주 소각": 1.5, "공급계약": 1.0,
              "거래정지·관리": -3.0, "소송": -0.5, "무상증자": 0.5}


# ------------------------------------------------------------------ 패널 (날짜 × 종목)
def panels(bars: dict, days: int = 900) -> tuple[pd.DataFrame, pd.DataFrame]:
    close, vol = {}, {}
    for sym, b in (bars or {}).items():
        if b is None or len(b) < 30 or "close" not in b:
            continue
        idx = pd.DatetimeIndex(b.index)
        idx = (idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")).normalize()
        c = pd.Series(b["close"].astype(float).values, index=idx)
        close[sym] = c[~c.index.duplicated(keep="last")]
        v = pd.Series((b["volume"] if "volume" in b else pd.Series(0, index=b.index)).astype(float).values, index=idx)
        vol[sym] = v[~v.index.duplicated(keep="last")]
    if not close:
        return pd.DataFrame(), pd.DataFrame()
    c = pd.DataFrame(close).sort_index().iloc[-days:]
    v = pd.DataFrame(vol).reindex(c.index)
    return c, v


def _clip(df, lo=-3.0, hi=3.0):
    return df.clip(lower=lo, upper=hi)


def price_signals(close: pd.DataFrame, volume: pd.DataFrame, sectors: dict | None = None) -> dict[str, pd.DataFrame]:
    """가격·거래량 신호 7개 (모두 그 날까지의 자료만 — 미래를 보지 않음)."""
    out = {}
    ma50 = close.rolling(50, min_periods=40).mean()
    ma200 = close.rolling(200, min_periods=150).mean()
    out["trend"] = _clip(10 * (close / ma50 - 1) + 10 * (ma50 / ma200 - 1))
    mom = close.shift(21) / close.shift(252) - 1
    out["mom"] = (mom.rank(axis=1, pct=True) - 0.5) * 6
    hi = close.rolling(252, min_periods=120).max()
    out["high52"] = _clip((close / hi - 0.85) / 0.05)
    turnover = close * volume
    t20 = turnover.rolling(20, min_periods=10).mean().shift(1)
    ratio = (turnover / t20).replace([np.inf, -np.inf], np.nan)
    ret1 = close.pct_change(fill_method=None)
    surge = np.log2(ratio.clip(lower=1.0)).clip(upper=3.0)
    out["volsurge"] = (surge.where(ratio >= 1.5, 0.0) * np.sign(ret1)).where(t20.notna())
    r5 = close / close.shift(5) - 1
    out["reversal"] = _clip(-r5 / 0.05)
    lr = np.log(close).diff()
    v60 = lr.rolling(60, min_periods=40).std()
    out["lowvol"] = (0.5 - v60.rank(axis=1, pct=True)) * 6
    r20 = close / close.shift(20) - 1
    if sectors:
        grp = pd.Series({s: sectors.get(s) or "기타" for s in close.columns})
        means = r20.T.groupby(grp).transform("mean").T
    else:
        means = pd.DataFrame(np.repeat(r20.mean(axis=1).values[:, None], r20.shape[1], axis=1), index=r20.index, columns=r20.columns)
    out["sector_rs"] = _clip((r20 - means) / 0.05)
    return out


def forward_excess(close: pd.DataFrame, h: int = HORIZON) -> pd.DataFrame:
    fwd = close.shift(-h) / close - 1
    return fwd.sub(fwd.mean(axis=1), axis=0)  # 그날 전체 평균 대비 (시장 전체가 오른 덕은 뺀다)


def _ic_series(score: pd.DataFrame, fwd: pd.DataFrame, dates) -> pd.Series:
    s = score.loc[dates]
    f = fwd.loc[dates]
    ok = s.notna() & f.notna()
    n = ok.sum(axis=1)
    sr = s.where(ok).rank(axis=1)
    fr = f.where(ok).rank(axis=1)
    ic = sr.corrwith(fr, axis=1)
    return ic[n >= MIN_NAMES].dropna()


def learn_weights(sig: dict[str, pd.DataFrame], close: pd.DataFrame, holdout: int = HOLDOUT, step: int = STEP) -> dict:
    """신호별 과거 성적 (IC) → 가중치. 학습 구간 = 처음 ~ (마지막 holdout+HORIZON 거래일 전)."""
    fwd = forward_excess(close)
    idx = close.index
    end = len(idx) - holdout - HORIZON
    train = idx[260:max(260, end):step]
    stats = {}
    for k, s in sig.items():
        ic = _ic_series(s, fwd, train) if len(train) else pd.Series(dtype=float)
        n = len(ic)
        m = float(ic.mean()) if n else None
        sd = float(ic.std(ddof=1)) if n > 1 else None
        t = (m / sd * math.sqrt(n)) if (m is not None and sd and sd > 0) else None
        if n >= 20 and t is not None and t >= 1.5 and m > 0.01:
            w, verdict = m, "효과 있음"
        elif n >= 20 and t is not None and t <= -2.0 and m < -0.01:
            w, verdict = m, "반대로 작동"  # 높을수록 덜 오름 → 부호를 뒤집어 쓴다
        elif n >= 20:
            w, verdict = 0.0, "효과 확인 안 됨"
        else:
            w, verdict = None, "자료 부족"
        stats[k] = {"ic": None if m is None else round(m, 4), "t": None if t is None else round(t, 2), "n_dates": n, "verdict": verdict, "w": w}
    used = {k: v["w"] for k, v in stats.items() if v["w"]}
    if used:
        tot = sum(abs(w) for w in used.values())
        weights = {k: w / tot for k, w in used.items()}
        basis = "past"
    else:  # 근거가 있는 신호가 하나도 없음 → 학계에서 알려진 기본값 (화면에 '검증 전 기본 가중치'로 표시)
        weights = dict(PRIOR_W)
        basis = "prior"
    return {"stats": stats, "weights": {k: round(v, 4) for k, v in weights.items()}, "basis": basis,
            "train_from": str(train[0].date()) if len(train) else None, "train_to": str(train[-1].date()) if len(train) else None}


def combine(sig: dict[str, pd.DataFrame], weights: dict) -> pd.DataFrame:
    tot = None
    wsum = None
    for k, w in weights.items():
        s = sig.get(k)
        if s is None or not w:
            continue
        part = s * w
        has = s.notna().astype(float) * abs(w)
        tot = part.fillna(0) if tot is None else tot + part.fillna(0)
        wsum = has if wsum is None else wsum + has
    if tot is None:
        return pd.DataFrame()
    return _clip(tot / wsum.replace(0, np.nan))


def calibrate(combined: pd.DataFrame, close: pd.DataFrame, holdout: int = HOLDOUT, step: int = STEP) -> dict:
    """보지 않은 마지막 holdout 기간에서: 점수대별 '20거래일 뒤 시장보다 올랐나' (n · 비율 · 평균)."""
    fwd = forward_excess(close)
    idx = close.index
    lo = max(0, len(idx) - holdout - HORIZON)
    dates = idx[lo:len(idx) - HORIZON:step]
    rows = []
    if len(dates):
        s = combined.reindex(dates).stack()
        f = fwd.reindex(dates).stack()
        df = pd.concat([s.rename("s"), f.rename("f")], axis=1).dropna()
        if len(df):
            df["b"] = pd.cut(df["s"], BINS, labels=False)
            for b in range(len(BIN_LABEL)):
                g = df[df["b"] == b]["f"]
                rows.append({"bin": b, "label": BIN_LABEL[b], "n": int(len(g)), "hit": round(float((g > 0).mean()), 3) if len(g) else None,
                             "mean": round(float(g.mean()), 4) if len(g) else None})
    top = next((r for r in rows if r["bin"] == 4 and r["n"]), None) or next((r for r in rows if r["bin"] == 3 and r["n"]), None)
    bot = next((r for r in rows if r["bin"] == 0 and r["n"]), None) or next((r for r in rows if r["bin"] == 1 and r["n"]), None)
    spread = round(top["mean"] - bot["mean"], 4) if top and bot and top["mean"] is not None and bot["mean"] is not None else None
    if top and top["n"] >= 100 and (top["hit"] or 0) >= 0.55 and (top["mean"] or 0) > 0 and (spread or 0) > 0:
        tier = ("good", "근거 있음", "보지 않은 기간에도 높은 점수 종목이 시장보다 더 자주, 더 많이 올랐어요")
    elif top and top["n"] >= 40 and (top["mean"] or 0) > 0 and (spread or 0) > 0 and (top["hit"] or 0) >= 0.5:
        tier = ("warn", "약한 근거", "보지 않은 기간에 조금 나았지만 우연일 수도 있어요")
    elif top and top["n"] >= 40 and (top["mean"] or 0) > 0 and (spread or 0) > 0:
        tier = ("warn", "약한 근거", f"보지 않은 기간에 높은 점수 종목의 평균은 시장보다 {top['mean'] * 100:+.1f}% 좋았지만, "
                f"시장을 이긴 종목은 {top['hit'] * 100:.0f}%뿐 — 몇 종목이 크게 올라 평균을 끌어올렸어요")
    else:
        tier = ("bad", "근거 부족", "보지 않은 기간에 높은 점수가 더 낫다는 증거가 없어요 — 참고만 하세요")
    return {"bins": rows, "tier": {"key": tier[0], "label": tier[1], "why": tier[2]}, "spread": spread,
            "from": str(dates[0].date()) if len(dates) else None, "to": str(dates[-1].date()) if len(dates) else None}


# ------------------------------------------------------------------ 오늘 자료로만 만드는 신호 (검증 전)
def _recent_disclosures(engine, symbols: set[str], days: int = 7) -> dict[str, list]:
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import Disclosure
    since = (datetime.now(UTC) - timedelta(days=days)).date()
    out: dict[str, list] = {}
    with session_scope(engine) as s:
        for d in s.scalars(select(Disclosure).where(Disclosure.filed_at >= since, Disclosure.symbol.is_not(None))):
            if d.symbol in symbols:
                out.setdefault(d.symbol, []).append({"title": d.title, "date": str(d.filed_at)})
    return out


def _recent_news(engine, symbols: set[str], days: int = 3) -> dict[str, list]:
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import NewsArticle
    since = datetime.now(UTC) - timedelta(days=days)
    out: dict[str, list] = {}
    with session_scope(engine) as s:
        for n in s.scalars(select(NewsArticle).where(NewsArticle.published_at >= since)):
            for sym in n.symbols or []:
                if sym in symbols and n.sentiment is not None:
                    out.setdefault(sym, []).append((float(n.sentiment), float(n.importance or 0.5), n.title))
    return out


def live_signals(app, symbols: list[str], reactions: dict | None = None) -> dict[str, dict]:
    """종목 → {신호: {score, text}} (자료가 있는 것만)."""
    from .analytics import event_type
    syms = set(symbols)
    eng = app.engine
    disc = _recent_disclosures(eng, syms)
    news = _recent_news(eng, syms)
    react = {t["type"]: t for t in (reactions or {}).get("types", [])}
    now = datetime.now(UTC)
    out: dict[str, dict] = {}
    for sym in symbols:
        sig: dict[str, dict] = {}
        best = None
        for d in disc.get(sym, []):
            et = event_type(d["title"])
            if not et:
                continue
            r = (react.get(et) or {}).get("d5") or {}
            if r.get("n", 0) >= 10 and r.get("mean") is not None:
                sc, how = max(-3.0, min(3.0, r["mean"] / 0.02)), f"과거 {et} 공시 {r['n']}번 뒤 5일 평균 {r['mean'] * 100:+.1f}% (시장 대비)"
            elif et in DISC_PRIOR:
                sc, how = DISC_PRIOR[et], f"{et} — 과거 자료가 적어 일반적인 영향으로 추정"
            else:
                continue
            if best is None or abs(sc) > abs(best[0]):
                best = (sc, f"{d['date']} {et}: {how}")
        if best:
            sig["disclosure"] = {"score": round(best[0], 2), "text": best[1]}
        ns = news.get(sym)
        if ns:
            w = sum(x[1] for x in ns) or 1.0
            avg = sum(x[0] * x[1] for x in ns) / w
            sig["news"] = {"score": round(max(-3.0, min(3.0, avg * 3)), 2),
                           "text": f"최근 3일 뉴스 {len(ns)}건 · 분위기 {'좋음' if avg > 0.15 else '나쁨' if avg < -0.15 else '보통'} ({avg:+.2f})"}
        fl = ops.get_state(eng, f"flow:{sym}")
        if fl.get("at") and now - datetime.fromisoformat(fl["at"]) <= timedelta(days=4):
            sm = fl.get("summary") or {}
            z = (sm.get("foreign_z5") or 0) + (sm.get("inst_z5") or 0)
            if sm.get("foreign_z5") is not None or sm.get("inst_z5") is not None:
                sig["flow"] = {"score": round(max(-3.0, min(3.0, z)), 2),
                               "text": f"외국인 {sm.get('foreign_streak', 0):+d}일 · 기관 {sm.get('inst_streak', 0):+d}일 연속 · {sm.get('signal', '')}"}
        kc = ops.get_state(eng, f"krcons:{sym}")
        sp = (kc.get("surprises") or [])[-1:] if kc else []
        if sp:
            s0 = sp[0]
            try:
                age = (now.date() - datetime.fromisoformat(s0["date"]).date()).days
            except (KeyError, ValueError):
                age = 999
            if age <= 90 and s0.get("surprise_pct") is not None:
                pct = float(s0["surprise_pct"]) / 100
                sig["earnings"] = {"score": round(max(-3.0, min(3.0, pct * 15)), 2),
                                   "text": f"{s0.get('label', '')} {'영업이익' if s0.get('metric') == 'op_income' else 'EPS'} 예상 대비 {pct * 100:+.1f}%"}
        cm = ops.get_state(eng, f"community:{sym}")
        if cm.get("at") and now - datetime.fromisoformat(cm["at"]) <= timedelta(hours=24):
            bull, bear = cm.get("bull") or 0, cm.get("bear") or 0
            tot = bull + bear
            if tot >= 20:
                share = bull / tot
                sc = -1.0 if share >= 0.8 else 0.5 if share <= 0.2 else 0.0
                sig["community"] = {"score": sc, "text": f"토론방 낙관 {bull} · 비관 {bear} → {'과열' if sc < 0 else '공포(역발상)' if sc > 0 else '보통'}"}
        if sig:
            out[sym] = sig
    return out


# ------------------------------------------------------------------ 오늘의 후보
def _names(engine, syms) -> dict:
    from sqlalchemy import select

    from .data.db import session_scope
    from .data.models import Instrument
    with session_scope(engine) as s:
        return {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_(list(syms))))}


def _bars_for(app, market: str):
    if market == "US":
        from .global_market import market_data as us_md
        bars, bench, _ = us_md(app)
        return bars, bench
    bars, bench, _ = app.market_data()
    return bars, bench


def _sig_text(k: str, v: float) -> str:
    if k == "trend":
        return "평균선 위 · 오름 추세" if v > 0.5 else "평균선 아래 · 내림 추세" if v < -0.5 else "뚜렷한 추세 없음"
    if k == "mom":
        return "1년 동안 상위권으로 올랐음" if v > 1 else "1년 동안 하위권" if v < -1 else "1년 오름세 중간"
    if k == "high52":
        return "52주 고점 근처 · 신고가 구간" if v > 1.5 else "고점에서 많이 내려와 있음" if v < -1.5 else "고점과 중간 거리"
    if k == "volsurge":
        return "거래가 크게 늘며 오름" if v > 0.5 else "거래가 크게 늘며 내림" if v < -0.5 else "거래량 평소 수준"
    if k == "reversal":
        return "최근 5일 급락 → 반등 여지" if v > 1 else "최근 5일 급등 → 쉬어갈 수 있음" if v < -1 else "최근 5일 보통"
    if k == "lowvol":
        return "덜 출렁이는 편" if v > 1 else "많이 출렁이는 편" if v < -1 else "출렁임 보통"
    if k == "sector_rs":
        return "업종 평균보다 강함" if v > 0.5 else "업종 평균보다 약함" if v < -0.5 else "업종과 비슷"
    return ""


def compute(app, market: str = "KR", n: int = 5, now: datetime | None = None, bars=None, focus: set | None = None,
            rows_out: dict | None = None) -> dict:
    """오늘의 매수 후보 · 비중 축소 후보 · 피할 종목 + 신호별 근거 + 점수 보정 + 전진 기록."""
    from .engines.sector import sector_map
    now = now or datetime.now(UTC)
    market = "US" if market == "US" else "KR"
    if bars is None:
        bars, _ = _bars_for(app, market)
    close, volume = panels(bars)
    if close.empty or len(close) < 60:
        return {"market": market, "error": "일봉이 부족해요 (종목마다 60거래일 이상 필요)", "buy": [], "sell": [], "avoid": []}
    sectors = sector_map(app.engine) if market == "KR" else {}
    sig = price_signals(close, volume, sectors)
    learned = learn_weights(sig, close)
    comb = combine(sig, learned["weights"])
    cal = calibrate(comb, close)
    last = close.index[-1]
    if focus is None:
        try:
            from .alerts import focus_symbols
            focus = set(focus_symbols(app))
        except Exception:  # noqa: BLE001
            focus = set()
    held = set()
    try:
        from .analytics import main_mode
        held = set(app.load_portfolio("us-paper" if market == "US" else main_mode(app)).positions)
    except Exception:  # noqa: BLE001, S110
        pass
    # 후보 자격: 마지막 거래일 시세가 있고 · 거래대금이 충분
    t20 = (close * volume).rolling(20, min_periods=10).mean().iloc[-1]
    fresh = close.iloc[-1].notna()
    eligible = [s for s in close.columns if fresh.get(s) and (t20.get(s) or 0) >= MIN_TURNOVER[market] and pd.notna(comb[s].iloc[-1] if s in comb else np.nan)]
    reactions = {}
    if market == "KR":
        try:
            from .analytics import event_reactions
            reactions = event_reactions(app)
        except Exception:  # noqa: BLE001
            reactions = {}
    live = live_signals(app, eligible, reactions) if eligible else {}
    names = _names(app.engine, eligible)
    lr = np.log(close).diff()
    atr = (lr.abs().rolling(14, min_periods=10).mean() * close).iloc[-1]  # 하루 평균 움직임(원) — 손절선용

    def bin_of(x):
        return int(pd.cut([x], BINS, labels=False)[0])
    rows = []
    for s in eligible:
        base = float(comb[s].iloc[-1])
        lv = live.get(s, {})
        live_avg = (sum(v["score"] for v in lv.values()) / len(lv)) if lv else 0.0
        final = max(-3.0, min(3.0, base + LIVE_WEIGHT * live_avg))
        b = bin_of(final)
        ev = next((r for r in cal["bins"] if r["bin"] == b), None)
        px = float(close[s].iloc[-1])
        prev = close[s].iloc[-2] if len(close) > 1 else np.nan
        parts = []
        for k, (label, desc) in PRICE_SIGNALS.items():
            v = sig[k][s].iloc[-1]
            if pd.isna(v):
                continue
            st = learned["stats"].get(k, {})
            w = learned["weights"].get(k, 0.0)
            parts.append({"key": k, "label": label, "score": round(float(v), 2), "text": _sig_text(k, float(v)), "desc": desc,
                          "weight": round(abs(w), 3), "flip": w < 0, "verified": learned["basis"] == "past" and bool(w),
                          "verdict": st.get("verdict") if learned["basis"] == "past" else "검증 전 기본값"})
        for k, v in lv.items():
            parts.append({"key": k, "label": LIVE_SIGNALS[k][0], "score": v["score"], "text": v["text"], "desc": LIVE_SIGNALS[k][1],
                          "weight": round(LIVE_WEIGHT / max(1, len(lv)), 3), "flip": False, "verified": False, "verdict": "검증 전 (작게 반영)"})
        a = float(atr.get(s)) if pd.notna(atr.get(s)) else None
        rows.append({"symbol": s, "name": names.get(s, s), "score": round(final, 2), "base": round(base, 2), "live": round(live_avg, 2),
                     "last": px, "chg": None if pd.isna(prev) or not prev else round(px / float(prev) - 1, 4),
                     "sector": sectors.get(s), "held": s in held, "focus": s in focus,
                     "turnover20": float(t20.get(s) or 0), "signals": parts,
                     "evidence": ev, "stop": round(px - 2 * a, 2) if a else None, "invalid_above": round(px + 2 * a, 2) if a else None})
    rows.sort(key=lambda r: -r["score"])
    buy = [r for r in rows if r["score"] >= 0.5][:n]
    sell = [r for r in sorted(rows, key=lambda r: r["score"]) if r["score"] <= -0.5 and (r["held"] or r["focus"])][:n]
    avoid = [r for r in sorted(rows, key=lambda r: r["score"]) if r["score"] <= -0.5 and r not in sell][:n]
    regime = _regime(close)
    out = {"market": market, "as_of": str(last.date()), "computed_at": now.isoformat(), "universe": len(close.columns), "eligible": len(eligible),
           "buy": buy, "sell": sell, "avoid": avoid, "weights": learned, "calibration": cal, "regime": regime,
           "live_coverage": {k: sum(1 for v in live.values() if k in v) for k in LIVE_SIGNALS},
           "rule": f"후보 = 거래대금 20일 평균 {'10억원' if market == 'KR' else '500만달러'} 이상 · 마지막 거래일 시세 있음 · 점수 +0.5 이상(매수) / -0.5 이하(축소·피하기)",
           "note": "가격 신호 가중치는 과거 결과로 정함 · 공시·뉴스·수급·실적·커뮤니티는 검증 전이라 작게 반영 · 자동매매에는 아직 쓰지 않음"}
    if rows_out is not None:
        rows_out.update({r["symbol"]: r for r in rows})
    _log_today(app.engine, out, rows)
    out["forward"] = forward_record(app.engine, close, market)
    return out


def _regime(close: pd.DataFrame) -> dict:
    eq = close.pct_change(fill_method=None).mean(axis=1).fillna(0).add(1).cumprod()
    ma = eq.rolling(200, min_periods=120).mean()
    above = bool(eq.iloc[-1] > ma.iloc[-1]) if pd.notna(ma.iloc[-1]) else None
    breadth = float((close.iloc[-1] > close.rolling(20, min_periods=15).mean().iloc[-1]).mean())
    return {"above_200": above, "breadth20": round(breadth, 3),
            "text": ("시장 전체가 200일 평균 위 — 매수 후보를 그대로" if above else "시장 전체가 200일 평균 아래 — 매수는 평소보다 작게") if above is not None else "시장 국면 판단 자료 부족"}


# ------------------------------------------------------------------ 전진 기록 (봉인된 하루치 후보 → 나중에 채점)
def _log_today(engine, out: dict, rows: list[dict]) -> None:
    st = ops.get_state(engine, LOG_KEY)
    days = dict(st.get("days") or {})
    key = f"{out['market']}:{out['as_of']}"
    if key in days:
        return  # 같은 거래일은 처음 계산한 것만 남긴다 (나중에 바꾸지 않음)
    import hashlib
    import json
    rec = {"market": out["market"], "date": out["as_of"], "at": out["computed_at"],
           "buy": [{"symbol": r["symbol"], "score": r["score"], "price": r["last"]} for r in out["buy"]],
           "sell": [{"symbol": r["symbol"], "score": r["score"], "price": r["last"]} for r in out["sell"]],
           "avoid": [{"symbol": r["symbol"], "score": r["score"], "price": r["last"]} for r in out["avoid"]],
           "all": {r["symbol"]: r["score"] for r in rows}}
    rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    days[key] = rec
    keep = sorted(days)[-400:]
    ops.set_state(engine, LOG_KEY, {"days": {k: days[k] for k in keep}})


def forward_record(engine, close: pd.DataFrame, market: str, h: int = HORIZON) -> dict:
    """지난 후보들이 실제로 어땠나 (기록한 날 종가 → h거래일 뒤, 같은 기간 전체 종목 평균 대비)."""
    days = (ops.get_state(engine, LOG_KEY).get("days") or {})
    idx = close.index
    res = {"buy": [], "sell": [], "avoid": []}
    pending = 0
    for _key, rec in sorted(days.items()):
        if rec.get("market") != market:
            continue
        d = pd.Timestamp(rec["date"], tz="UTC")
        pos = idx.searchsorted(d)
        if pos >= len(idx) or pos + h >= len(idx):
            pending += 1
            continue
        t0, t1 = idx[pos], idx[pos + h]
        mkt = float((close.loc[t1] / close.loc[t0] - 1).mean())
        for side in res:
            for p in rec.get(side) or []:
                s = p["symbol"]
                if s in close and pd.notna(close.at[t0, s]) and pd.notna(close.at[t1, s]):
                    r = float(close.at[t1, s] / close.at[t0, s] - 1)
                    res[side].append(r - mkt)
    out = {"horizon": h, "pending_days": pending, "days": sum(1 for r in days.values() if r.get("market") == market)}
    for side, xs in res.items():
        good = (lambda x: x > 0) if side == "buy" else (lambda x: x < 0)
        out[side] = {"n": len(xs), "hit": round(sum(1 for x in xs if good(x)) / len(xs), 3) if xs else None,
                     "mean_excess": round(float(np.mean(xs)), 4) if xs else None}
    return out


def for_symbol(app, symbol: str) -> dict:
    """종목 화면용: 이 종목의 신호 분해 (후보가 아니어도)."""
    market = "KR" if symbol[:1].isdigit() else "US"
    full = cached(app, market)
    for side in ("buy", "sell", "avoid"):
        for r in full.get(side) or []:
            if r["symbol"] == symbol:
                return {"market": market, "as_of": full.get("as_of"), "row": r, "tier": full["calibration"]["tier"], "side": side}
    row = (full.get("_rows") or {}).get(symbol)
    if row:
        return {"market": market, "as_of": full.get("as_of"), "row": row, "tier": full["calibration"]["tier"], "side": None}
    return {"market": market, "as_of": full.get("as_of"), "row": None, "tier": full.get("calibration", {}).get("tier"),
            "why": "후보 계산 대상이 아니에요 (거래대금이 작거나 일봉이 부족)"}


_CACHE: dict[str, tuple[float, dict]] = {}


def cached(app, market: str = "KR", ttl: float = 600.0) -> dict:
    import time
    hit = _CACHE.get(market)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    out = compute_all(app, market)
    _CACHE[market] = (time.monotonic(), out)
    return out


def compute_all(app, market: str = "KR") -> dict:
    """compute + 모든 대상 종목 행(종목 화면용) — 화면 API 는 이것을 캐시해서 쓴다."""
    rows: dict = {}
    out = compute(app, market, rows_out=rows)
    out["_rows"] = rows
    return out


__all__ = ["compute", "compute_all", "cached", "for_symbol", "price_signals", "learn_weights", "combine", "calibrate", "forward_record",
           "panels", "live_signals"]

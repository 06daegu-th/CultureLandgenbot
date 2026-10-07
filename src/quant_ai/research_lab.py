"""전략 연구소: 빠른 행렬 기반 walk-forward 로 여러 전략을 공정하게 비교한다.

과최적화 방지 프로토콜
1. 개발 구간(dev)과 검증 구간(holdout)을 나눈다. 설정 선택은 **dev 성과로만** 한다.
2. 선택이 끝난 뒤 holdout 을 한 번 계산한다 (선택에 쓰지 않음).
3. 시도한 모든 설정 수로 DSR 을 계산한다.

전략 구조 (순위 기반 롱온리)
- 매 리밸런싱일 t 종가에 점수 계산 → t+1 시가에 체결
- 점수 상위 top_k 동일가중, 기존 보유 종목은 순위 buffer_k 안이면 유지 (회전율 억제)
- 비용: 편도 수수료+슬리피지, 매도 시 거래세. 상장폐지 보유분은 할인 청산.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

import numpy as np
import pandas as pd

from .backtest.backtester import performance
from .backtest.stats import deflated_sharpe
from .data.collectors.marcap import KRXDataset
from .engines.factors import price_factors
from .engines.features import technical_features
from .engines.prediction import MODEL_FACTORIES
from .engines.regime import EXPOSURE_MULTIPLIER, Regime, regime_series

TECH = ["ret_1", "ret_5", "ret_20", "vol_20", "vol_ratio", "jump_sigma", "rsi_14", "macd_hist", "bb_pctb",
        "dist_ma20", "dist_ma60", "atr_14", "volume_z", "high_20_dist", "low_20_dist"]
FACTORS = ["log_mcap", "turnover_20", "illiq_20", "mom_12_1", "mom_6_1", "dist_52w", "vol_60", "max_ret_20"]


def factor_features(b: pd.DataFrame) -> pd.DataFrame:
    """학계·실무에서 검증된 팩터 (t 종가까지 정보만)."""
    c = b["close"]
    lr = np.log(c).diff()
    f = pd.DataFrame(index=b.index)
    f["log_mcap"] = np.log(b["marcap"].clip(lower=1))
    f["turnover_20"] = (b["amount"] / b["marcap"]).rolling(20).mean()
    f["illiq_20"] = np.log1p((lr.abs() / (b["amount"] / 1e8).clip(lower=1e-3)).rolling(20).mean())
    pf = price_factors(c)  # 실시간 코어 전략과 같은 함수
    for k in pf.columns:
        f[k] = pf[k]
    f["max_ret_20"] = lr.rolling(20).max()  # 복권형 종목(최대수익) — 역효과 알려짐
    return f


@dataclass
class Panel:
    dates: pd.DatetimeIndex
    symbols: list[str]
    feats: dict[str, pd.DataFrame]  # 이름 → (날짜 × 종목)
    open: pd.DataFrame
    close: pd.DataFrame
    eligible: pd.DataFrame
    last_date: pd.Series  # 종목별 마지막 데이터 일자
    raw_close: pd.DataFrame | None = None  # v32: 그날 실제 1주 가격 (수정 전) — 소액 백테스트


def build_panel(ds: KRXDataset) -> Panel:
    dates = ds.eligible.index
    feats: dict[str, dict[str, pd.Series]] = {}
    for sym, b in ds.bars.items():
        f = pd.concat([technical_features(b), factor_features(b)], axis=1)
        for col in f.columns:
            feats.setdefault(col, {})[sym] = f[col]
    syms = list(ds.bars)
    panel_feats = {k: pd.DataFrame(v).reindex(index=dates, columns=syms) for k, v in feats.items()}
    op = pd.DataFrame({s: b["open"] for s, b in ds.bars.items()}).reindex(index=dates, columns=syms)
    cl = pd.DataFrame({s: b["close"] for s, b in ds.bars.items()}).reindex(index=dates, columns=syms)
    last = pd.Series({s: b.index.max() for s, b in ds.bars.items()})
    raw = None
    if all("close_raw" in b for b in ds.bars.values()):
        raw = pd.DataFrame({s: b["close_raw"] for s, b in ds.bars.items()}).reindex(index=dates, columns=syms)
    return Panel(dates, syms, panel_feats, op, cl, ds.eligible.reindex(columns=syms, fill_value=False), last, raw)


def cs_rank(df: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """날짜별 횡단면 백분위 순위 - 0.5 (유니버스 종목끼리만 비교)."""
    return df.where(mask).rank(axis=1, pct=True) - 0.5


@dataclass
class StrategyConfig:
    name: str
    features: list[str] = field(default_factory=lambda: list(TECH))
    rank_features: bool = True
    label: str = "relative"  # relative(같은 날 중앙값 초과) / absolute(0 초과)
    horizon: int = 20
    model: str = "logistic"  # logistic / gbm / factor
    factor_weights: dict[str, float] | None = None  # model=factor 일 때 점수 = Σ w·rank
    rebalance_every: int = 20
    top_k: int = 20
    buffer_k: int = 40
    gross: float = 1.0
    regime_scaling: bool = False
    vol_target: float | None = None  # 연 변동성 목표 (예: 0.15). 보유 묶음의 최근 60일 변동성이 크면 비중 축소
    vol_lookback: int = 60
    max_corr: float | None = None  # 이미 고른 종목과 상관계수가 이보다 높은 후보는 건너뜀 (쏠림 방지)
    corr_lookback: int = 120
    trend_ma: int | None = None  # 지수가 N일 이동평균 아래면 노출을 trend_off_gross 로 축소 (월 1회 판단)
    trend_off_gross: float = 0.5
    train_window: int = 750
    retrain_every: int = 20
    min_train_dates: int = 250


# KRX 매도 증권거래세(+농특세) 연도별 변경 이력 (시행일, bps). 과거 백테스트에 현재 세율을 쓰면 비용을 과소평가한다.
KRX_SELL_TAX_HISTORY: tuple[tuple[str, float], ...] = (
    ("1900-01-01", 30.0), ("2019-06-03", 25.0), ("2021-01-01", 23.0), ("2023-01-01", 20.0),
    ("2024-01-01", 18.0), ("2025-01-01", 15.0), ("2026-01-01", 20.0),
)


@dataclass
class Costs:
    commission_bps: float = 1.5
    slippage_bps: float = 5.0
    sell_tax_bps: float = 18.0
    delist_haircut: float = 0.30
    sell_tax_schedule: tuple[tuple[str, float], ...] | None = None  # 있으면 sell_tax_bps 대신 날짜별 세율

    @classmethod
    def historical(cls, **kw) -> Costs:
        return cls(sell_tax_schedule=KRX_SELL_TAX_HISTORY, **kw)

    def sell_tax_series(self, dates: pd.DatetimeIndex) -> np.ndarray:
        if not self.sell_tax_schedule:
            return np.full(len(dates), self.sell_tax_bps)
        eff = pd.DatetimeIndex([pd.Timestamp(d) for d, _ in self.sell_tax_schedule])
        if dates.tz is not None:
            eff = eff.tz_localize(dates.tz)
        pos = eff.searchsorted(dates, side="right") - 1
        return np.array([self.sell_tax_schedule[max(i, 0)][1] for i in pos])


def walk_forward_scores(p: Panel, cfg: StrategyConfig, embargo: int = 1) -> pd.DataFrame:
    """날짜 × 종목 점수. t 일 점수는 t-horizon-embargo 까지 라벨이 확정된 데이터로만 학습한 모델."""
    mask = p.eligible
    X = {k: (cs_rank(p.feats[k], mask) if cfg.rank_features else p.feats[k].where(mask)) for k in cfg.features}
    if cfg.model == "factor":
        w = cfg.factor_weights or {}
        score = sum(X[k] * v for k, v in w.items())
        return score.where(mask)

    fwd = p.close.shift(-cfg.horizon) / p.open.shift(-1) - 1
    fwd = fwd.where(mask)
    if cfg.label == "relative":
        y = fwd.gt(fwd.median(axis=1), axis=0).astype(float).where(fwd.notna())
    else:
        y = (fwd > 0).astype(float).where(fwd.notna())
    long = pd.concat({k: v.stack(future_stack=True) for k, v in X.items()}, axis=1)
    long["y"] = y.stack(future_stack=True)
    long = long[mask.stack(future_stack=True).reindex(long.index, fill_value=False)]
    di = {d: i for i, d in enumerate(p.dates)}
    long["di"] = [di[d] for d in long.index.get_level_values(0)]

    scores = pd.DataFrame(np.nan, index=p.dates, columns=p.symbols)
    start = cfg.min_train_dates + cfg.horizon + embargo
    model = None
    for i in range(start, len(p.dates), cfg.retrain_every):
        cutoff = i - cfg.horizon - embargo
        tr = long[(long["di"] <= cutoff) & (long["di"] > cutoff - cfg.train_window)].dropna(subset=["y"])
        if tr["y"].nunique() == 2:
            model = MODEL_FACTORIES[cfg.model]()
            model.fit(tr[cfg.features], tr["y"])
        if model is None:
            continue
        te = long[(long["di"] >= i) & (long["di"] < i + cfg.retrain_every)]
        if len(te):
            prob = model.predict_proba(te[cfg.features])[:, 1]
            s = pd.Series(prob, index=te.index).unstack()
            scores.loc[s.index, s.columns] = s.values
    return scores.where(mask)


def risk_veto_mask(p: Panel, vol_spike: float = 3.0, jump_sigma: float = 6.0) -> pd.DataFrame:
    """실시간 Risk AI 의 하드 규칙 중 과거 데이터로 재현 가능한 것 (t 종가까지 정보).

    - 단기 변동성 급증: 5일/20일 변동성 비율 ≥ vol_spike
    - 가격 이상 급변: |당일 로그수익률| / 20일 변동성 ≥ jump_sigma
    """
    return ((p.feats["vol_ratio"] >= vol_spike) | (p.feats["jump_sigma"] >= jump_sigma)).fillna(False)


def simulate(p: Panel, scores: pd.DataFrame, cfg: StrategyConfig, costs: Costs,
             regime: pd.Series | None = None, veto: pd.DataFrame | None = None,
             index_close: pd.Series | None = None) -> dict:
    """t 종가 점수 → t+1 시가 체결, 시가→시가 수익률로 보유, 비중은 가격 변동에 따라 표류.

    veto: True 인 (날짜, 종목)은 신규 편입 금지 (보유 중이면 유지 — 실시간 Risk AI 와 동일).
    index_close: trend_ma 사용 시 지수 종가 (p.dates 로 정렬).
    """
    OP = p.open.to_numpy(float)
    S = scores.to_numpy(float)
    V = veto.reindex(index=p.dates, columns=p.symbols, fill_value=False).to_numpy(bool) if veto is not None else None
    T, N = OP.shape
    C = p.close.to_numpy(float)
    CR = np.full((T, N), np.nan)
    CR[1:] = C[1:] / C[:-1] - 1  # 종가→종가 (t 종가까지 정보만 사용)
    ret = np.full((T, N), np.nan)
    ret[:-1] = OP[1:] / OP[:-1] - 1  # d 시가 → d+1 시가
    last_idx = np.array([p.dates.searchsorted(p.last_date[s]) for s in p.symbols])
    buy_cost = (costs.commission_bps + costs.slippage_bps) / 1e4
    sell_cost_by_day = buy_cost + costs.sell_tax_series(p.dates) / 1e4
    first = int(np.argmax(np.isfinite(S).any(axis=1)))
    below_trend = None
    if cfg.trend_ma and index_close is not None:
        ic = index_close.reindex(p.dates).ffill()
        below_trend = (ic < ic.rolling(cfg.trend_ma, min_periods=cfg.trend_ma // 2).mean()).to_numpy()

    def too_correlated(i: int, picks: list[int], d: int) -> bool:
        if not cfg.max_corr or not picks or d <= cfg.corr_lookback:
            return False
        win = np.nan_to_num(CR[d - cfg.corr_lookback:d], nan=0.0)
        x = win[:, i]
        if x.std() == 0:
            return False
        ys = win[:, picks]
        sd = ys.std(axis=0)
        corr = ((x - x.mean())[:, None] * (ys - ys.mean(axis=0))).mean(axis=0) / (x.std() * np.where(sd > 0, sd, np.inf))
        return bool(np.max(corr) > cfg.max_corr)
    w = np.zeros(N)
    value = 1.0
    values, dates, turnovers, exposures = [], [], [], []
    for d in range(first + 1, T - 1):
        # ---- 리밸런싱: d-1 종가 점수로 d 시가에 체결
        if (d - first - 1) % cfg.rebalance_every == 0:
            s = S[d - 1]
            ok = np.isfinite(s) & np.isfinite(OP[d])
            target = np.zeros(N)
            if ok.sum() > 0:  # 가능한 종목이 top_k 보다 적으면 있는 만큼만 (전액 현금으로 가지 않음)
                order = np.argsort(-np.where(ok, s, -np.inf))
                rank = np.empty(N, int)
                rank[order] = np.arange(N)
                keep = [i for i in np.flatnonzero(w > 0) if ok[i] and rank[i] < cfg.buffer_k]
                picks = list(keep)
                for i in order:
                    if len(picks) >= cfg.top_k:
                        break
                    if ok[i] and i not in picks and not (V is not None and V[d - 1, i]) \
                            and not too_correlated(i, picks, d):
                        picks.append(i)
                g = cfg.gross
                if below_trend is not None and below_trend[d - 1]:
                    g *= cfg.trend_off_gross
                if cfg.regime_scaling and regime is not None:
                    r = regime.iloc[d - 1] if d - 1 < len(regime) else None
                    g *= EXPOSURE_MULTIPLIER[Regime(r)] if isinstance(r, str) else 1.0
                if cfg.vol_target and d > cfg.vol_lookback:
                    # 사전(ex-ante) 변동성: 지금 고른 종목 동일가중 묶음의 과거 60일 일간 수익률
                    hist = CR[d - cfg.vol_lookback:d][:, picks]
                    basket = np.nanmean(hist, axis=1)
                    vol = float(np.nanstd(basket) * np.sqrt(252))
                    if vol > 0:
                        g *= min(1.0, cfg.vol_target / vol)
                target[picks] = g / len(picks)
            tradable = np.isfinite(OP[d])
            target = np.where(tradable, target, w)  # 거래 불가 종목은 비중 유지
            delta = target - w
            cost = np.sum(np.clip(delta, 0, None)) * buy_cost + np.sum(np.clip(-delta, 0, None)) * sell_cost_by_day[d]
            turnovers.append(np.abs(delta).sum() / 2)
            value *= 1 - cost
            w = target
        # ---- 상장폐지: 데이터가 끝난 보유 종목은 할인 청산
        dead = (w > 0) & (last_idx <= d)
        if dead.any():
            value *= 1 - np.sum(w[dead]) * costs.delist_haircut
            w = np.where(dead, 0.0, w)
        r = np.nan_to_num(ret[d], nan=0.0)  # 거래정지 등은 0 수익
        port = float(np.dot(w, r))
        value *= 1 + port
        w = w * (1 + r) / (1 + port) if (1 + port) > 0 else w
        values.append(value)
        dates.append(p.dates[d + 1])
        exposures.append(w.sum())
    eq = pd.Series(values, index=pd.DatetimeIndex(dates))
    return {"equity": eq, "turnover": float(np.mean(turnovers)) if turnovers else 0.0,
            "annual_turnover": float(np.sum(turnovers) / max(len(eq) / 252, 1e-9)),
            "avg_exposure": float(np.mean(exposures)) if exposures else 0.0}


# ------------------------------------------------------------------ v32 소액 현실 백테스트
def _month_starts(dates: pd.DatetimeIndex) -> set[int]:
    """매달 첫 거래일의 위치."""
    m = dates.to_period("M")
    return {i for i in range(len(dates)) if i == 0 or m[i] != m[i - 1]}


def money_stats(values: pd.Series, flows: pd.Series) -> dict:
    """입금이 섞인 계좌 성과: 최종 금액 · 넣은 돈 · 시간가중 수익(입금 효과 제외) · 연환산 · 최대 하락 · 돈가중 수익(IRR, 연)."""
    flows = flows.reindex(values.index, fill_value=0.0)
    prev = values.shift(1)
    r = ((values - flows) / prev - 1).iloc[1:].replace([np.inf, -np.inf], np.nan).fillna(0.0)  # 입금은 그날 시가 전에 들어온 것으로
    twr = (1 + r).cumprod()
    years = len(r) / 252
    total = float(twr.iloc[-1] - 1) if len(twr) else 0.0
    cf = flows.groupby(flows.index.to_period("M")).sum()
    cf = cf[cf != 0]
    irr = None
    if len(cf):
        t = np.array([(p.to_timestamp() - values.index[0].tz_localize(None).to_period("M").to_timestamp()).days / 365.25 for p in cf.index])
        T = (values.index[-1].tz_localize(None) - values.index[0].tz_localize(None)).days / 365.25
        lo, hi = -0.99, 3.0
        for _ in range(80):  # 이분법: Σ cf·(1+i)^(T-t) = 최종 금액
            mid = (lo + hi) / 2
            fv = float(np.sum(cf.to_numpy() * (1 + mid) ** (T - t)))
            lo, hi = (mid, hi) if fv < float(values.iloc[-1]) else (lo, mid)
        irr = round((lo + hi) / 2, 4)
    return {"final": round(float(values.iloc[-1])), "paid": round(float(flows.sum())), "twr_total": round(total, 4),
            "twr_cagr": round(float((1 + total) ** (1 / years) - 1), 4) if years > 0 and total > -1 else None,
            "mdd": round(float((twr / twr.cummax() - 1).min()), 4) if len(twr) else 0.0, "irr": irr,
            "sharpe": round(float(r.mean() / r.std() * np.sqrt(252)), 3) if r.std() > 0 else 0.0}


def simulate_cash(p: Panel, scores: pd.DataFrame, cfg: StrategyConfig, costs: Costs, start: str, end: str | None,
                  principal: float, monthly: float, index_close: pd.Series | None = None, trim: bool = True) -> dict:
    """원 단위 · 1주 단위 · 매달 적립 백테스트 — 소액 계좌가 실제로 할 수 있는 매매만.

    - start 이후 첫 거래일에 principal, 그 뒤 매달 첫 거래일에 monthly 입금
    - 매달 첫 거래일(입금일)에 리밸런싱: 전날 종가 점수로 그날 시가에 체결
    - 보유 종목은 순위 buffer_k 안이면 유지 · trim=True 면 목표 금액보다 10% 넘게 크면 넘는 만큼 판다 (같은 비중으로 되돌리기)
    - 빈자리는 점수 높은 순으로 채우되, 종목당 목표 금액(평가금액 × 노출 / top_k)으로 **1주도 못 사면 건너뛴다**
    - 1주 가격은 그날의 실제(수정 전) 가격 · 남는 돈은 현금 (이자 0) · 상장폐지는 할인 청산
    """
    if p.raw_close is None:
        raise ValueError("실제 1주 가격(close_raw)이 없는 데이터예요 — 데이터를 다시 만들어 주세요")
    dates = p.dates
    lo = int(dates.searchsorted(pd.Timestamp(start, tz=dates.tz) if dates.tz is not None else pd.Timestamp(start)))
    hi = len(dates) - 1 if end is None else int(dates.searchsorted(pd.Timestamp(end, tz=dates.tz) if dates.tz is not None else pd.Timestamp(end), side="right")) - 1
    OP, CL = p.open.to_numpy(float), p.close.to_numpy(float)
    RAW = p.raw_close.to_numpy(float)
    S = scores.to_numpy(float)
    N = len(p.symbols)
    last_idx = np.array([dates.searchsorted(p.last_date[s]) for s in p.symbols])
    buy_cost = (costs.commission_bps + costs.slippage_bps) / 1e4
    sell_cost = buy_cost + costs.sell_tax_series(dates) / 1e4
    below = None
    if cfg.trend_ma and index_close is not None:
        ic = index_close.reindex(dates).ffill()
        below = (ic < ic.rolling(cfg.trend_ma, min_periods=cfg.trend_ma // 2).mean()).to_numpy()
    months = _month_starts(dates[lo:hi + 1])
    sh = np.zeros(N)  # 수정주가 기준 주식 수 (가치 = sh × 수정가)
    cash = 0.0
    vals, flows, idx, cash_w = [], [], [], []
    skipped_unaffordable, trades = 0, 0
    last_px = CL[max(lo, 1) - 1].copy()
    for d in range(max(lo, 1), hi + 1):
        flow = 0.0
        if (d - lo) in months or d == max(lo, 1):
            flow = principal if not idx else monthly
            cash += flow
            # ---- 리밸런싱 (d-1 종가 점수 → d 시가)
            s = S[d - 1]
            ok = np.isfinite(s) & np.isfinite(OP[d]) & np.isfinite(RAW[d - 1])
            order = np.argsort(-np.where(ok, s, -np.inf))
            rank = np.empty(N, int)
            rank[order] = np.arange(N)
            held = np.flatnonzero(sh > 0)
            for i in held:  # 순위 밖 → 전량 매도
                if not (ok[i] and rank[i] < cfg.buffer_k) and np.isfinite(OP[d, i]):
                    cash += sh[i] * OP[d, i] * (1 - sell_cost[d])
                    sh[i] = 0.0
                    trades += 1
            equity = cash + float(np.nansum(sh * np.nan_to_num(OP[d])))
            g = cfg.gross * (cfg.trend_off_gross if below is not None and below[d - 1] else 1.0)
            per = equity * g / cfg.top_k
            if trim:
                for i in np.flatnonzero(sh > 0):
                    over = sh[i] * OP[d, i] - per
                    if np.isfinite(OP[d, i]) and over > per * 0.1:
                        raw_px = RAW[d - 1, i] * OP[d, i] / CL[d - 1, i]
                        n = int(over // raw_px)  # 1주 단위로만 판다
                        if n >= 1:
                            sold = min(sh[i], n * raw_px / OP[d, i])
                            cash += sold * OP[d, i] * (1 - sell_cost[d])
                            sh[i] -= sold
                            trades += 1
            n_held = int((sh > 0).sum())

            def buy(i: int, budget: float, d: int = d) -> bool:
                nonlocal cash, trades
                raw_px = RAW[d - 1, i] * OP[d, i] / CL[d - 1, i]  # 오늘 시가의 실제 가격
                n = int(min(budget, cash) // (raw_px * (1 + buy_cost)))
                if n < 1:
                    return False
                cash -= n * raw_px * (1 + buy_cost)
                sh[i] += n * raw_px / OP[d, i]  # 실제 n주 = 수정가 기준 수량
                trades += 1
                return True
            for i in order:
                if n_held >= cfg.top_k or not ok[i]:  # 점수 순 정렬이라 ok 가 아닌 종목부터는 볼 필요 없음
                    break
                if sh[i] > 0:
                    continue
                if not buy(i, per):
                    skipped_unaffordable += 1
                    continue
                n_held += 1
            # 계속 보유하는 종목도 목표 금액의 90% 아래면 새로 들어온 돈으로 채운다 (파는 쪽은 비용 때문에 하지 않음)
            for i in sorted(np.flatnonzero(sh > 0), key=lambda j: rank[j]):
                gap = per - sh[i] * OP[d, i]
                if gap > per * 0.1:
                    buy(i, gap)
        dead = (sh > 0) & (last_idx <= d) & (last_idx < len(dates) - 1)  # 자료 끝(오늘)까지 있는 종목은 상장폐지가 아니다
        if dead.any():
            cash += float(np.sum(sh[dead] * np.nan_to_num(CL[d - 1, dead]))) * (1 - costs.delist_haircut)
            sh[dead] = 0.0
        last_px = np.where(np.isfinite(CL[d]), CL[d], last_px)  # 거래정지 등으로 값이 없으면 마지막 가격
        vals.append(cash + float(np.sum(sh * np.nan_to_num(last_px))))
        cash_w.append(cash / vals[-1] if vals[-1] > 0 else 1.0)
        flows.append(flow)
        idx.append(dates[d])
    v = pd.Series(vals, index=pd.DatetimeIndex(idx))
    f = pd.Series(flows, index=v.index)
    return {"values": v, "flows": f, "stats": money_stats(v, f) | {"avg_cash": round(float(np.mean(cash_w)), 3) if cash_w else 1.0},
            "skipped_unaffordable": skipped_unaffordable, "trades": trades}


def simulate_cash_index(index_close: pd.Series, dates: pd.DatetimeIndex, start: str, end: str | None, principal: float,
                        monthly: float, fee_annual: float = 0.0015, trade_cost: float = 0.0005) -> dict:
    """지수 ETF 적립 (같은 날 같은 돈) — 운용보수 · 매수 비용 반영, ETF 는 1주가 싸서 소수 주로 근사."""
    ic = index_close.reindex(dates).ffill()
    lo = int(dates.searchsorted(pd.Timestamp(start, tz=dates.tz) if dates.tz is not None else pd.Timestamp(start)))
    hi = len(dates) - 1 if end is None else int(dates.searchsorted(pd.Timestamp(end, tz=dates.tz) if dates.tz is not None else pd.Timestamp(end), side="right")) - 1
    months = _month_starts(dates[lo:hi + 1])
    units, vals, flows, idx = 0.0, [], [], []
    daily_fee = (1 - fee_annual) ** (1 / 252)
    for d in range(max(lo, 1), hi + 1):
        flow = 0.0
        if (d - lo) in months or d == max(lo, 1):
            flow = principal if not idx else monthly
            units += flow * (1 - trade_cost) / float(ic.iloc[d - 1])
        units *= daily_fee
        vals.append(units * float(ic.iloc[d]))
        flows.append(flow)
        idx.append(dates[d])
    v = pd.Series(vals, index=pd.DatetimeIndex(idx))
    f = pd.Series(flows, index=v.index)
    return {"values": v, "flows": f, "stats": money_stats(v, f)}


def small_capital_configs() -> list[StrategyConfig]:
    """v32 소액 비교 후보 (결과를 보기 전에 고정 — 사전 등록)."""
    fw = {"mom_12_1": 1.0, "vol_60": -1.0, "dist_52w": 1.0}
    feats = ["mom_12_1", "vol_60", "dist_52w"]
    return [
        StrategyConfig("factor-top3-trend200", features=feats, model="factor", factor_weights=fw, top_k=3, buffer_k=6, trend_ma=200),
        StrategyConfig("factor-top5-trend200", features=feats, model="factor", factor_weights=fw, top_k=5, buffer_k=10, trend_ma=200),
        StrategyConfig("factor-top10-trend200", features=feats, model="factor", factor_weights=fw, top_k=10, buffer_k=20, trend_ma=200),
        StrategyConfig("factor-top5", features=feats, model="factor", factor_weights=fw, top_k=5, buffer_k=10),
        # 지금 AI 자동매매 규칙과 성격이 비슷한 대조군: 단기(20일) 추세 + 20일 고점 근접 상위 5 · 월 1회 교체
        StrategyConfig("short-trend-top5", features=["ret_20", "high_20_dist"], model="factor",
                       factor_weights={"ret_20": 1.0, "high_20_dist": 1.0}, top_k=5, buffer_k=5),
    ]


def small_capital_study(ds: KRXDataset, principal: float = 2_000_000, monthly: float = 1_000_000, dev_start: str = "2012-01-01",
                        dev_end: str = "2020-12-31", configs: list[StrategyConfig] | None = None, costs: Costs | None = None,
                        prior_trials: int = 10, mix: float = 0.15, say=print) -> dict:
    """소액 적립 계좌로 후보 전략 vs 지수 ETF 적립 비교.

    절차 (과최적화 방지): 개발 구간에서만 고른다(규칙: ETF 적립보다 시간가중 수익이 높고 최대 하락이 -45% 보다 얕은 것 중 Sharpe 최고)
    → 고른 뒤 검증 구간을 한 번 계산 → 시도 횟수(이전 연구 포함)로 DSR 보정. 'mix' 는 계획(ETF 85% + 전략 15%) 결과도 같이."""
    costs = costs or Costs.historical()
    configs = configs or small_capital_configs()
    p = build_panel(ds)
    idx_close = ds.benchmark["close"]
    hold_start = (pd.Timestamp(dev_end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    periods = {"dev": (dev_start, dev_end), "holdout": (hold_start, None)}
    out: dict = {"principal": principal, "monthly": monthly, "periods": {k: [a, b] for k, (a, b) in periods.items()}, "results": [], "etf": {}}
    for k, (a, b) in periods.items():
        out["etf"][k] = simulate_cash_index(idx_close, p.dates, a, b, principal, monthly)["stats"]
    for cfg in configs:
        sc = walk_forward_scores(p, cfg)
        row = {"name": cfg.name, "top_k": cfg.top_k, "trend_ma": cfg.trend_ma}
        for k, (a, b) in periods.items():
            sim = simulate_cash(p, sc, cfg, costs, a, b, principal, monthly, idx_close)
            mixed = simulate_cash(p, sc, cfg, costs, a, b, principal * mix, monthly * mix, idx_close)
            etf_part = simulate_cash_index(idx_close, p.dates, a, b, principal * (1 - mix), monthly * (1 - mix))
            mv = mixed["values"] + etf_part["values"].reindex(mixed["values"].index).ffill()
            mf = mixed["flows"] + etf_part["flows"].reindex(mixed["flows"].index, fill_value=0.0)
            row[k] = sim["stats"] | {"skipped_unaffordable": sim["skipped_unaffordable"], "trades": sim["trades"]}
            row[f"{k}_mix"] = money_stats(mv, mf)
            if k == "dev":
                rets = (sim["values"] - sim["flows"]) / sim["values"].shift(1) - 1
                row["dev_dsr"] = round(float(deflated_sharpe(rets.dropna().iloc[1:], prior_trials + len(configs))), 3)
        say(f"  {cfg.name:<24} dev 시간가중 {row['dev']['twr_cagr']:+.1%}/년 MDD {row['dev']['mdd']:.0%} · "
            f"검증 {row['holdout']['twr_cagr']:+.1%}/년 MDD {row['holdout']['mdd']:.0%} 최종 {row['holdout']['final']:,}원 "
            f"(못 산 횟수 {row['holdout']['skipped_unaffordable']} · 평균 현금 {row['holdout']['avg_cash']:.0%})")
        out["results"].append(row)
    e = out["etf"]
    say(f"  {'지수 ETF 적립':<24} dev {e['dev']['twr_cagr']:+.1%}/년 MDD {e['dev']['mdd']:.0%} · 검증 {e['holdout']['twr_cagr']:+.1%}/년 "
        f"MDD {e['holdout']['mdd']:.0%} 최종 {e['holdout']['final']:,}원")
    ok = [r for r in out["results"] if (r["dev"]["twr_cagr"] or -1) > (e["dev"]["twr_cagr"] or 0) and r["dev"]["mdd"] > -0.45]
    chosen = max(ok, key=lambda r: r["dev"]["sharpe"]) if ok else None
    out["chosen"] = chosen["name"] if chosen else None
    out["n_trials"] = prior_trials + len(configs)
    if chosen:
        h, eh = chosen["holdout"], e["holdout"]
        beat = (h["twr_cagr"] or -1) > (eh["twr_cagr"] or 0)
        out["verdict"] = {"beat_etf_holdout": beat, "excess_cagr": round((h["twr_cagr"] or 0) - (eh["twr_cagr"] or 0), 4),
                          "dsr": chosen["dev_dsr"],
                          "text": (f"개발 구간으로 고른 '{chosen['name']}' 이 보지 않은 기간에 지수 ETF 적립보다 "
                                   f"연 {abs((h['twr_cagr'] or 0) - (eh['twr_cagr'] or 0)):.1%}p {'높았다' if beat else '낮았다'}"
                                   f" (최대 하락 {h['mdd']:.0%} vs {eh['mdd']:.0%}) · DSR {chosen['dev_dsr']:.2f}")}
    else:
        out["verdict"] = {"beat_etf_holdout": False, "text": "개발 구간에서 지수 ETF 적립을 이긴 후보가 없음 — 지수 ETF 적립이 정답"}
    return out


def benchmark_equity(p: Panel, kospi: pd.DataFrame, index: pd.DatetimeIndex) -> dict[str, pd.Series]:
    k = kospi["close"].reindex(index).ffill()
    closes = p.close
    r = closes.pct_change(fill_method=None).where(p.eligible.shift(1, fill_value=False)).mean(axis=1).fillna(0.0)
    ew = (1 + r).cumprod().reindex(index).ffill()
    return {"kospi": k / k.iloc[0], "universe_ew": ew / ew.iloc[0]}


def window(eq: pd.Series, start: str | None, end: str | None) -> pd.Series:
    s = eq.loc[start:end] if (start or end) else eq
    return s / s.iloc[0] if len(s) else s


def summarize(eq: pd.Series, benches: dict[str, pd.Series], start=None, end=None) -> dict:
    e = window(eq, start, end)
    out = {"strategy": performance(e)}
    for k, b in benches.items():
        bw = window(b.reindex(eq.index).ffill(), start, end)
        out[k] = performance(bw)
        active = e.pct_change().dropna() - bw.pct_change().dropna()
        out[f"ir_vs_{k}"] = float(active.mean() / active.std() * np.sqrt(252)) if active.std() > 0 else 0.0
    return out


def daily_ic(scores: pd.DataFrame, p: Panel, horizon: int, start=None, end=None) -> dict:
    fwd = p.close.shift(-horizon) / p.open.shift(-1) - 1
    s, f = scores.loc[start:end], fwd.loc[start:end]
    ics = s.rank(axis=1).corrwith(f.where(s.notna()).rank(axis=1), axis=1).dropna()
    ics = ics.iloc[::horizon]  # 겹치지 않는 표본으로 t-stat 과대추정 방지
    if len(ics) < 3:
        return {"ic": None, "ic_t": None}
    return {"ic": float(ics.mean()), "ic_t": float(ics.mean() / ics.std(ddof=1) * np.sqrt(len(ics)))}


def run_lab(ds: KRXDataset, configs: list[StrategyConfig], dev_end: str = "2020-12-31",
            costs: Costs | None = None, prior_trials: int = 0, say=print) -> dict:
    costs = costs or Costs.historical()
    p = build_panel(ds)
    reg = regime_series(ds.benchmark)["regime"].reindex(p.dates)
    results = []
    for cfg in configs:
        scores = walk_forward_scores(p, cfg)
        ic = ds.benchmark["close"] if cfg.trend_ma else None
        sim = simulate(p, scores, cfg, costs, reg, index_close=ic)
        stress = simulate(p, scores, cfg, replace(costs, commission_bps=costs.commission_bps * 2,
                                                  slippage_bps=costs.slippage_bps * 2), reg, index_close=ic)
        benches = benchmark_equity(p, ds.benchmark, sim["equity"].index)
        dev = summarize(sim["equity"], benches, None, dev_end)
        dev_stress = summarize(stress["equity"], {}, None, dev_end)["strategy"]
        r = {"config": asdict(cfg), "dev": dev, "dev_stress": dev_stress,
             "dev_ic": daily_ic(scores, p, cfg.horizon, None, dev_end),
             "turnover": sim["annual_turnover"], "avg_exposure": sim["avg_exposure"],
             "_sim": sim, "_stress": stress, "_scores": scores, "_benches": benches}
        say(f"  [dev] {cfg.name:<26} Sharpe {dev['strategy']['sharpe']:5.2f} | CAGR {dev['strategy']['cagr']:6.1%} "
            f"| MDD {dev['strategy']['max_drawdown']:6.1%} | IR vs EW {dev['ir_vs_universe_ew']:5.2f} "
            f"| 비용2배 Sharpe {dev_stress['sharpe']:5.2f} | 연회전 {sim['annual_turnover']:.1f}배 "
            f"| IC {r['dev_ic']['ic'] or 0:.3f} (t={r['dev_ic']['ic_t'] or 0:.1f})")
        results.append(r)
    n_trials = prior_trials + len(configs)
    # ---- 선택: dev 구간 성과로만 (비용 2배에서도 양수인 것 중 Sharpe 최고)
    ok = [r for r in results if r["dev_stress"]["sharpe"] > 0] or results
    chosen = max(ok, key=lambda r: r["dev"]["strategy"]["sharpe"])
    say(f"\n  ▶ dev 기준 선택: {chosen['config']['name']} (시도 {n_trials}회)")
    # ---- holdout: 선택 이후 처음 계산
    for r in results:
        start = (pd.Timestamp(dev_end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        r["holdout"] = summarize(r["_sim"]["equity"], r["_benches"], start, None)
        r["holdout_stress"] = summarize(r["_stress"]["equity"], {}, start, None)["strategy"]
        r["holdout_ic"] = daily_ic(r["_scores"], p, r["config"]["horizon"], start, None)
        rets = window(r["_sim"]["equity"], None, dev_end).pct_change().dropna()
        r["dev_dsr"] = deflated_sharpe(rets, n_trials)
        r["full"] = summarize(r["_sim"]["equity"], r["_benches"])
        r["yearly"] = _yearly(r["_sim"]["equity"], r["_benches"])
    return {"results": results, "chosen": chosen["config"]["name"], "n_trials": n_trials, "dev_end": dev_end,
            "panel": p}


def _yearly(eq: pd.Series, benches: dict[str, pd.Series]) -> dict:
    out = {}
    for name, s in {"strategy": eq, **benches}.items():
        s = s.reindex(eq.index).ffill()
        y = s.resample("YE").last()
        prev = s.iloc[0]
        out[name] = {}
        for ts, v in y.items():
            out[name][str(ts.year)] = float(v / prev - 1)
            prev = v
    return out


def default_configs() -> list[StrategyConfig]:
    all_f = TECH + FACTORS
    return [
        # 0) 기존 방식 재현: 절대 라벨, 5일, 매일 리밸런싱, 5종목, 50% 노출
        StrategyConfig("baseline-daily-top5", features=TECH, rank_features=False, label="absolute", horizon=5,
                       rebalance_every=1, top_k=5, buffer_k=5, gross=0.5),
        # 1) 순위 포트폴리오 + 상대 라벨 + 월간 리밸런싱 (기술지표만)
        StrategyConfig("rank-tech-h20-m", features=TECH),
        # 2) + 검증된 팩터
        StrategyConfig("rank-all-h20-m", features=all_f),
        # 3) + 국면에 따른 노출 조절
        StrategyConfig("rank-all-h20-m-regime", features=all_f, regime_scaling=True),
        # 4) 주간 리밸런싱 버전
        StrategyConfig("rank-all-h5-w", features=all_f, horizon=5, rebalance_every=5),
        # 5) ML 없는 고전 팩터 (모멘텀 + 저변동성 + 52주 고점) — ML 이 정말 도움이 되는지 대조군
        StrategyConfig("factor-mom-lowvol", features=["mom_12_1", "vol_60", "dist_52w"], model="factor",
                       factor_weights={"mom_12_1": 1.0, "vol_60": -1.0, "dist_52w": 1.0}),
        # 6) 5) + 변동성 타깃팅 15% (모멘텀 급락 방어, Barroso & Santa-Clara 2015) — dev 에서 탈락
        StrategyConfig("factor-mom-lowvol-vt15", features=["mom_12_1", "vol_60", "dist_52w"], model="factor",
                       factor_weights={"mom_12_1": 1.0, "vol_60": -1.0, "dist_52w": 1.0}, vol_target=0.15),
        # 7) 5) + 지수 200일선 추세 필터 (사전등록 시험 통과 → 실시간 코어 기본값, RESEARCH_KRX.md 8장)
        StrategyConfig("factor-mom-lowvol-trend200", features=["mom_12_1", "vol_60", "dist_52w"], model="factor",
                       factor_weights={"mom_12_1": 1.0, "vol_60": -1.0, "dist_52w": 1.0}, trend_ma=200),
    ]


def save_lab_report(res: dict, out_dir, data_info: dict) -> str:
    """연구 결과를 JSON 으로 저장 (대시보드 '실데이터 연구' 화면에서 표시)."""
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in res["results"]:
        eq = r["_sim"]["equity"]
        rows.append({
            "name": r["config"]["name"], "config": r["config"], "dev": r["dev"], "dev_stress": r["dev_stress"],
            "dev_ic": r["dev_ic"], "dev_dsr": r["dev_dsr"], "holdout": r["holdout"], "holdout_stress": r["holdout_stress"],
            "holdout_ic": r["holdout_ic"], "full": r["full"], "yearly": r["yearly"], "turnover": r["turnover"],
            "avg_exposure": r["avg_exposure"],
            "equity": [[str(t.date()), float(v)] for t, v in eq.iloc[::5].items()],
            "benchmarks": {k: [[str(t.date()), float(v)] for t, v in b.reindex(eq.index).ffill().iloc[::5].items()]
                           for k, b in r["_benches"].items()},
        })
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = out / f"lab-{stamp}.json"
    path.write_text(json.dumps({"kind": "lab", "created_at": stamp, "data": data_info, "dev_end": res["dev_end"],
                                "chosen": res["chosen"], "n_trials": res["n_trials"], "results": rows},
                               ensure_ascii=False, default=float), encoding="utf-8")
    return str(path)

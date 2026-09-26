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

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .backtest.backtester import performance
from .backtest.stats import deflated_sharpe
from .data.collectors.marcap import KRXDataset
from .engines.features import technical_features
from .engines.prediction import MODEL_FACTORIES
from .engines.regime import EXPOSURE_MULTIPLIER, Regime, regime_series

TECH = ["ret_1", "ret_5", "ret_20", "vol_20", "vol_ratio", "rsi_14", "macd_hist", "bb_pctb",
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
    f["mom_12_1"] = c.shift(21) / c.shift(252) - 1  # 최근 1개월 제외 12개월 모멘텀
    f["mom_6_1"] = c.shift(21) / c.shift(126) - 1
    f["dist_52w"] = c / c.rolling(252, min_periods=120).max() - 1
    f["vol_60"] = lr.rolling(60).std()
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
    return Panel(dates, syms, panel_feats, op, cl, ds.eligible.reindex(columns=syms, fill_value=False), last)


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
    train_window: int = 750
    retrain_every: int = 20
    min_train_dates: int = 250


@dataclass
class Costs:
    commission_bps: float = 1.5
    slippage_bps: float = 5.0
    sell_tax_bps: float = 18.0
    delist_haircut: float = 0.30


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


def simulate(p: Panel, scores: pd.DataFrame, cfg: StrategyConfig, costs: Costs,
             regime: pd.Series | None = None) -> dict:
    """t 종가 점수 → t+1 시가 체결, 시가→시가 수익률로 보유, 비중은 가격 변동에 따라 표류."""
    OP = p.open.to_numpy(float)
    S = scores.to_numpy(float)
    T, N = OP.shape
    ret = np.full((T, N), np.nan)
    ret[:-1] = OP[1:] / OP[:-1] - 1  # d 시가 → d+1 시가
    last_idx = np.array([p.dates.searchsorted(p.last_date[s]) for s in p.symbols])
    buy_cost = (costs.commission_bps + costs.slippage_bps) / 1e4
    sell_cost = buy_cost + costs.sell_tax_bps / 1e4
    first = int(np.argmax(np.isfinite(S).any(axis=1)))
    w = np.zeros(N)
    value = 1.0
    values, dates, turnovers, exposures = [], [], [], []
    for d in range(first + 1, T - 1):
        # ---- 리밸런싱: d-1 종가 점수로 d 시가에 체결
        if (d - first - 1) % cfg.rebalance_every == 0:
            s = S[d - 1]
            ok = np.isfinite(s) & np.isfinite(OP[d])
            target = np.zeros(N)
            if ok.sum() >= cfg.top_k:
                order = np.argsort(-np.where(ok, s, -np.inf))
                rank = np.empty(N, int)
                rank[order] = np.arange(N)
                keep = [i for i in np.flatnonzero(w > 0) if ok[i] and rank[i] < cfg.buffer_k]
                picks = list(keep)
                for i in order:
                    if len(picks) >= cfg.top_k:
                        break
                    if ok[i] and i not in picks:
                        picks.append(i)
                g = cfg.gross
                if cfg.regime_scaling and regime is not None:
                    r = regime.iloc[d - 1] if d - 1 < len(regime) else None
                    g *= EXPOSURE_MULTIPLIER[Regime(r)] if isinstance(r, str) else 1.0
                target[picks] = g / len(picks)
            tradable = np.isfinite(OP[d])
            target = np.where(tradable, target, w)  # 거래 불가 종목은 비중 유지
            delta = target - w
            cost = np.sum(np.clip(delta, 0, None)) * buy_cost + np.sum(np.clip(-delta, 0, None)) * sell_cost
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
    costs = costs or Costs()
    p = build_panel(ds)
    reg = regime_series(ds.benchmark)["regime"].reindex(p.dates)
    results = []
    for cfg in configs:
        scores = walk_forward_scores(p, cfg)
        sim = simulate(p, scores, cfg, costs, reg)
        stress = simulate(p, scores, cfg, Costs(costs.commission_bps * 2, costs.slippage_bps * 2,
                                                costs.sell_tax_bps, costs.delist_haircut), reg)
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
    ]

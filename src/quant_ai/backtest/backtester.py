"""Walk-forward Backtester.

미래 정보 누수를 막는 규칙:
1. t 일 종가에 신호를 만들고, t+1 일 시가에 체결한다.
2. t 일에 재학습할 때는 라벨이 t 일 종가까지 확정된 행만 쓴다
   (행 s 의 라벨은 s+horizon 종가에 확정 → s <= t - horizon, 여기에 embargo 1봉 추가).
3. 주문은 Paper/Live 와 같은 ExecutionEngine + RiskEngine 을 탄다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import CostModelConfig, RiskLimits
from ..engines.features import build_dataset, feature_columns
from ..engines.prediction import Predictor, classification_metrics
from ..engines.regime import EXPOSURE_MULTIPLIER, Regime, equal_weight_index, regime_series
from ..trading.broker import MarketQuote, PaperBroker
from ..trading.execution import ExecutionEngine, signals_from_probs
from ..trading.journal import Journal
from ..trading.portfolio import CostModel, Portfolio
from ..trading.risk import RiskEngine
from .stats import bootstrap_sharpe_ci, calibration_table, probabilistic_sharpe


@dataclass
class BacktestConfig:
    horizon: int = 5
    model_kind: str = "logistic"  # 기본은 보정이 잘 되는 로지스틱, "gbm" 선택 가능
    train_window: int | None = 750  # 학습에 쓰는 최근 날짜 수 (None = 전체 누적)
    min_train_dates: int = 250
    retrain_every: int = 20
    embargo: int = 1
    min_prob: float = 0.55
    top_k: int | None = 5
    initial_cash: float = 10_000_000
    use_regime: bool = True
    delist_haircut: float = 0.30  # 상장폐지 보유분 강제 청산 시 할인 (정리매매 등 보수적 가정)
    risk: RiskLimits = field(default_factory=RiskLimits)
    costs: CostModelConfig = field(default_factory=CostModelConfig)


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series
    predictions: pd.DataFrame
    journal: Journal
    metrics: dict
    model: Predictor | None


def performance(equity: pd.Series, periods: int = 252) -> dict:
    rets = equity.pct_change().dropna()
    if rets.empty:
        return {"total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "ann_vol": 0.0}
    years = len(rets) / periods
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    vol = float(rets.std() * np.sqrt(periods))
    lo, hi = bootstrap_sharpe_ci(rets, periods)
    return {
        "total_return": total,
        "cagr": float((1 + total) ** (1 / years) - 1) if years > 0 and total > -1 else -1.0,
        "ann_vol": vol,
        "sharpe": float(rets.mean() * periods / vol) if vol > 0 else 0.0,
        "sharpe_ci95": [lo, hi],
        "psr": probabilistic_sharpe(rets),  # P(진짜 Sharpe > 0)
        "max_drawdown": float((equity / equity.cummax() - 1).min()),
        "n_periods": int(len(rets)),
    }


class Backtester:
    def __init__(self, config: BacktestConfig | None = None):
        self.cfg = config or BacktestConfig()

    def run(self, bars_by_symbol: dict[str, pd.DataFrame], benchmark: pd.DataFrame | None = None,
            sentiment: pd.DataFrame | None = None, eligible: pd.DataFrame | None = None,
            progress=None) -> BacktestResult:
        """eligible: index=일자, columns=종목, bool. 그 시점에 실제로 투자 대상이었던 종목만
        학습·예측·신규매수에 쓴다 (생존편향·선택편향 제거). None 이면 전 종목."""
        cfg = self.cfg
        bench = benchmark if benchmark is not None else equal_weight_index(bars_by_symbol)
        reg = regime_series(bench)
        data = build_dataset(bars_by_symbol, cfg.horizon,
                             regime=reg["score"] if cfg.use_regime else None, sentiment=sentiment)
        if eligible is not None:
            stacked = eligible.stack()
            ok = stacked[stacked].index
            data = data[data.index.isin(ok)]
        feats = feature_columns(data)
        last_bar = {s: b.index.max() for s, b in bars_by_symbol.items()}
        data_end = max(last_bar.values())
        delisted_exits = 0
        dates = data.index.get_level_values(0).unique().sort_values()
        opens = pd.DataFrame({s: b["open"] for s, b in bars_by_symbol.items()}).reindex(dates)
        closes = pd.DataFrame({s: b["close"] for s, b in bars_by_symbol.items()}).reindex(dates)
        mark = closes.ffill()

        pf = Portfolio(cash=cfg.initial_cash)
        journal = Journal("backtest")
        risk = RiskEngine(cfg.risk)
        engine = ExecutionEngine(PaperBroker(pf, CostModel(cfg.costs)), risk, journal)

        model: Predictor | None = None
        last_train = -10**9
        equity_points: dict[pd.Timestamp, float] = {}
        exposures: list[float] = []
        pred_rows = []
        start = cfg.min_train_dates + cfg.horizon + cfg.embargo

        for i in range(start, len(dates) - 1):
            t, t_next = dates[i], dates[i + 1]

            # ---- (재)학습: 라벨이 확정된 구간만
            if model is None or i - last_train >= cfg.retrain_every:
                cutoff = i - cfg.horizon - cfg.embargo
                lo = 0 if cfg.train_window is None else max(0, cutoff - cfg.train_window)
                train = data.loc[dates[lo]:dates[cutoff]].dropna(subset=["label"])
                if train["label"].nunique() == 2:
                    model = Predictor(cfg.model_kind, cfg.horizon).fit(train[feats], train["label"])
                    last_train = i
            if model is None:
                continue

            # ---- t 종가 기준 예측
            today = data.xs(t, level=0, drop_level=False)
            probs = model.predict_proba(today[feats])
            regime_label = reg["regime"].get(t)
            for (ts, sym), p, fr, lab in zip(today.index, probs, today["fwd_ret"], today["label"]):
                pred_rows.append((ts, sym, float(p), fr, lab, regime_label))

            # ---- t+1 시가 체결
            mult = EXPOSURE_MULTIPLIER[Regime(regime_label)] if (cfg.use_regime and regime_label) else 1.0
            prob_map = {sym: float(p) for (_, sym), p in zip(today.index, probs)}
            signals = signals_from_probs(prob_map, cfg.min_prob, cfg.risk.max_position_weight, cfg.top_k)
            for s in signals:
                s.target_weight *= mult
            open_px = opens.loc[t_next]
            ref = open_px.fillna(mark.loc[t])  # 거래 안 된 종목은 직전 종가로 평가만
            quotes = {s: MarketQuote(last=float(px)) for s, px in ref.items() if pd.notna(px)}
            tradable = {s for s in quotes if pd.notna(open_px.get(s))}
            # 상장폐지(데이터 종료) 종목 보유분: 마지막 가격에서 할인(보수적)해 강제 청산
            for sym, pos in list(pf.positions.items()):
                if pos.qty and last_bar[sym] < t_next and last_bar[sym] < data_end - pd.Timedelta(days=7):
                    quotes[sym] = MarketQuote(last=float(mark.loc[t, sym]) * (1 - cfg.delist_haircut))
                    tradable.add(sym)
                    delisted_exits += 1
            risk.start_day(t_next.date(), pf.equity({s: q.last for s, q in quotes.items()}))
            engine.rebalance(signals, quotes, t_next, mult, tradable=tradable)

            marks = mark.loc[t_next].dropna().to_dict()
            for sym, pos in pf.positions.items():
                if pos.qty and sym not in marks:
                    marks[sym] = pos.avg_price
            equity_points[t_next] = pf.equity(marks)
            exposures.append(pf.market_value(marks) / equity_points[t_next] if equity_points[t_next] > 0 else 0.0)
            if progress and i % 250 == 0:
                progress(t_next, equity_points[t_next])

        equity = pd.Series(equity_points, name="equity").sort_index()
        if equity.empty:
            raise ValueError("백테스트 구간이 너무 짧습니다 (min_train_dates 이상의 데이터 필요)")
        bench_px = bench["close"].reindex(equity.index).ffill()
        benchmark_eq = cfg.initial_cash * bench_px / bench_px.iloc[0]

        preds = pd.DataFrame(pred_rows, columns=["ts", "symbol", "prob_up", "fwd_ret", "label", "regime"])
        scored = preds.dropna(subset=["label"])
        metrics = {
            "strategy": performance(equity),
            "benchmark": performance(benchmark_eq),
            "prediction": classification_metrics(scored["prob_up"], scored["label"], scored["fwd_ret"],
                                                 ts=scored["ts"]),
            "calibration": calibration_table(scored["prob_up"], scored["label"]) if len(scored) else [],
            "n_orders": len(journal.of_kind("order")),
            "n_fills": sum(1 for e in journal.of_kind("order") if e.data["status"] == "filled"),
            "fees_paid": pf.fees_paid,
            "n_days": len(equity),
            "delisted_exits": delisted_exits,
            "avg_exposure": float(np.mean(exposures)) if exposures else None,
        }
        metrics["excess_return"] = metrics["strategy"]["total_return"] - metrics["benchmark"]["total_return"]
        metrics["daily_returns"] = [float(x) for x in equity.pct_change().dropna()]  # DSR 계산용
        return BacktestResult(equity, benchmark_eq, preds, journal, metrics, model)

"""실제 KRX 데이터 연구: 생존편향 없는 walk-forward 검증 → (선택) 모델 등록.

    quant-ai research krx --marcap-dir ./marcap/data --start 2010 --end 2026 --top 100 --register

정직성 원칙
- 시도한 설정 수(n_trials)를 모두 세고 DSR 로 보정한다. 가장 좋은 설정만 골라 보여주지 않는다.
- 벤치마크는 두 개: KOSPI(시총가중) 와 '같은 유니버스 동일가중' (종목 선택 실력만 보려면 후자가 공정).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest.backtester import BacktestConfig, Backtester, performance
from .backtest.stats import deflated_sharpe
from .data.collectors.marcap import KRXDataset, build_krx_dataset

log = logging.getLogger("quant_ai.research")


@dataclass
class Trial:
    name: str
    model_kind: str = "logistic"
    horizon: int = 5
    top_k: int = 5
    min_prob: float = 0.55
    max_position_weight: float = 0.10


DEFAULT_TRIALS = [
    Trial("logistic-h5-top5"),
    Trial("gbm-h5-top5", model_kind="gbm"),
    Trial("logistic-h20-top10", horizon=20, top_k=10),
]


def universe_equal_weight(ds: KRXDataset) -> pd.Series:
    """매일 그 시점 유니버스 종목을 동일가중 보유했을 때의 자산곡선 (종목 선택력 비교용)."""
    closes = pd.DataFrame({s: b["close"] for s, b in ds.bars.items()}).reindex(ds.eligible.index)
    rets = closes.pct_change(fill_method=None)
    elig = ds.eligible.shift(1).fillna(False).astype(bool)  # 전일 기준 보유
    r = rets.where(elig).mean(axis=1).fillna(0.0)
    return (1 + r).cumprod()


def yearly(equity: pd.Series) -> dict[str, float]:
    y = equity.resample("YE").last()
    first = equity.iloc[0]
    out = {}
    prev = first
    for ts, v in y.items():
        out[str(ts.year)] = float(v / prev - 1)
        prev = v
    return out


def run_trial(ds: KRXDataset, trial: Trial, base: BacktestConfig, progress=None) -> dict:
    cfg = replace(base, model_kind=trial.model_kind, horizon=trial.horizon, top_k=trial.top_k,
                  min_prob=trial.min_prob, risk=replace(base.risk, max_position_weight=trial.max_position_weight))
    t0 = time.time()
    res = Backtester(cfg).run(ds.bars, ds.benchmark, eligible=ds.eligible, progress=progress)
    m = res.metrics
    rets = m.pop("daily_returns")
    ew = universe_equal_weight(ds).reindex(res.equity.index).ffill()
    ew_eq = cfg.initial_cash * ew / ew.iloc[0]
    stress_cfg = replace(cfg, costs=replace(cfg.costs, commission_bps=cfg.costs.commission_bps * 2,
                                            slippage_bps=cfg.costs.slippage_bps * 2))
    stress = Backtester(stress_cfg).run(ds.bars, ds.benchmark, eligible=ds.eligible)
    preds = res.predictions.dropna(subset=["label"])
    ic_year = {}
    for yr, g in preds.groupby(pd.to_datetime(preds["ts"]).dt.year):
        daily = [x["prob_up"].corr(x["fwd_ret"], method="spearman") for _, x in g.groupby("ts") if len(x) >= 5]
        ic_year[str(yr)] = float(np.nanmean(daily)) if daily else None
    active = res.equity.pct_change().dropna() - ew_eq.pct_change().dropna()
    return {
        "trial": asdict(trial),
        "seconds": round(time.time() - t0, 1),
        "metrics": m,
        "universe_ew": performance(ew_eq),
        "stress": stress.metrics["strategy"],
        "information_ratio_vs_ew": float(active.mean() / active.std() * np.sqrt(252)) if active.std() > 0 else 0.0,
        "yearly": {"strategy": yearly(res.equity), "kospi": yearly(res.benchmark), "universe_ew": yearly(ew_eq)},
        "ic_by_year": ic_year,
        "daily_returns": rets,
        "equity": [[str(t.date()), float(v)] for t, v in res.equity.iloc[::5].items()],
        "benchmark": [[str(t.date()), float(v)] for t, v in res.benchmark.iloc[::5].items()],
        "universe_ew_curve": [[str(t.date()), float(v)] for t, v in ew_eq.iloc[::5].items()],
        "model": res.model,
    }


def run_krx_research(marcap_dir: str | Path, start_year: int, end_year: int, top_n: int = 100,
                     trials: list[Trial] | None = None, out_dir: str | Path = "artifacts/research",
                     base: BacktestConfig | None = None, prior_trials: int = 0, say=print) -> dict:
    trials = trials or DEFAULT_TRIALS
    base = base or BacktestConfig()
    say(f"① 데이터 구성: {start_year}~{end_year}, 월말 시총 상위 {top_n} (상장폐지 포함)")
    ds = build_krx_dataset(marcap_dir, start_year, end_year, top_n)
    say(f"   종목 {len(ds.bars)}개 (유니버스를 거쳐간 전체), 거래일 {len(ds.eligible)}일")
    results = []
    for i, tr in enumerate(trials, 1):
        say(f"② [{i}/{len(trials)}] {tr.name} walk-forward 백테스트 + 비용 2배 스트레스…")
        r = run_trial(ds, tr, base, progress=lambda t, eq: say(f"     {t.date()} 자산 {eq:,.0f}"))
        s = r["metrics"]["strategy"]
        say(f"   → Sharpe {s['sharpe']:.2f} (95% {s['sharpe_ci95'][0]:.2f}~{s['sharpe_ci95'][1]:.2f}), "
            f"CAGR {s['cagr']:.1%}, MDD {s['max_drawdown']:.1%}, IC {r['metrics']['prediction']['ic']:.3f} "
            f"(t={r['metrics']['prediction'].get('ic_t') or 0:.1f}), 스트레스 Sharpe {r['stress']['sharpe']:.2f} "
            f"| KOSPI Sharpe {r['metrics']['benchmark']['sharpe']:.2f}, 유니버스EW Sharpe {r['universe_ew']['sharpe']:.2f}")
        results.append(r)
    n_trials = prior_trials + len(trials)
    for r in results:
        r["dsr"] = deflated_sharpe(r["daily_returns"], n_trials)
        r["n_trials"] = n_trials
    best = max(results, key=lambda r: r["metrics"]["strategy"]["sharpe"])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report = {
        "created_at": stamp, "data": {"source": "FinanceData/marcap (KRX)", "start": start_year, "end": end_year,
                                      "top_n": top_n, "symbols": len(ds.bars), "days": len(ds.eligible)},
        "n_trials": n_trials, "best": best["trial"]["name"],
        "trials": [{k: v for k, v in r.items() if k not in ("daily_returns", "model")} for r in results],
    }
    path = out / f"krx-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    say(f"③ 리포트 저장: {path}")
    report["_best_result"] = best
    report["_dataset"] = ds
    return report


def register_best(app, report: dict) -> tuple:
    """최고 설정을 전체(유니버스 기간) 데이터로 재학습해 레지스트리에 등록 → 게이트 평가."""
    from .engines.features import build_dataset, feature_columns
    from .engines.prediction import Predictor
    from .engines.regime import regime_series

    best, ds = report["_best_result"], report["_dataset"]
    tr = best["trial"]
    data = build_dataset(ds.bars, tr["horizon"], regime=regime_series(ds.benchmark)["score"])
    stacked = ds.eligible.stack()
    data = data[data.index.isin(stacked[stacked].index)].dropna(subset=["label"])
    model = Predictor(tr["model_kind"], tr["horizon"]).fit(data[feature_columns(data)], data["label"])
    metrics = {**best["metrics"], "stress": best["stress"], "dsr": best["dsr"], "n_trials": best["n_trials"],
               "universe_ew": best["universe_ew"], "information_ratio_vs_ew": best["information_ratio_vs_ew"],
               "data": report["data"]}
    metrics.pop("daily_returns", None)
    rec = app.registry.register_candidate(model, f"krx-{tr['name']}", metrics,
                                          {**tr, "universe": f"KRX top{report['data']['top_n']}",
                                           "train_end": str(data.index.get_level_values(0).max())})
    gate = app.registry.evaluate_candidate(rec.id)
    return rec, gate

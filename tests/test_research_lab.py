"""전략 연구소 엔진: 미래 정보 누수 없음, 비용 계산, 회전율 버퍼."""

import numpy as np
import pandas as pd
import pytest

from quant_ai.data.collectors.marcap import build_krx_dataset
from quant_ai.research_lab import (
    TECH,
    Costs,
    StrategyConfig,
    build_panel,
    simulate,
    walk_forward_scores,
)
from tests.test_marcap import fake_marcap


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    d = tmp_path_factory.mktemp("lab")
    fake_marcap(d, n_codes=12, days=700)
    ds = build_krx_dataset(d, 2020, 2020, top_n=10, min_amount=1e6)
    return build_panel(ds)


CFG = StrategyConfig("t", features=TECH[:6], horizon=5, rebalance_every=5, top_k=3, buffer_k=6,
                     min_train_dates=120, retrain_every=40)


def test_scores_do_not_use_future(panel):
    s1 = walk_forward_scores(panel, CFG)
    cut = panel.dates[450]
    # 미래 가격을 망가뜨려도 cut 이전 점수는 그대로여야 함
    import copy
    p2 = copy.copy(panel)
    p2.close = panel.close.copy()
    p2.open = panel.open.copy()
    p2.close.loc[cut:] *= np.random.default_rng(1).uniform(0.5, 2.0, p2.close.loc[cut:].shape)
    p2.open.loc[cut:] *= 1.7
    s2 = walk_forward_scores(p2, CFG)
    early = panel.dates[panel.dates < cut - pd.Timedelta(days=30)]
    pd.testing.assert_frame_equal(s1.loc[early], s2.loc[early])


def test_scores_only_for_eligible(panel):
    s = walk_forward_scores(panel, CFG)
    assert not (s.notna() & ~panel.eligible).any().any()


def test_costs_reduce_returns_and_buffer_reduces_turnover(panel):
    s = walk_forward_scores(panel, CFG)
    free = simulate(panel, s, CFG, Costs(0, 0, 0, 0.3))
    paid = simulate(panel, s, CFG, Costs(10, 20, 20, 0.3))
    assert paid["equity"].iloc[-1] < free["equity"].iloc[-1]
    tight = StrategyConfig(**{**CFG.__dict__, "buffer_k": CFG.top_k})
    loose = StrategyConfig(**{**CFG.__dict__, "buffer_k": 10})
    assert simulate(panel, s, loose, Costs())["annual_turnover"] <= simulate(panel, s, tight, Costs())["annual_turnover"]


def test_gross_exposure_respected(panel):
    s = walk_forward_scores(panel, CFG)
    half = StrategyConfig(**{**CFG.__dict__, "gross": 0.5})
    sim = simulate(panel, s, half, Costs())
    assert 0.3 < sim["avg_exposure"] <= 0.55

"""KRX marcap 로더: 수정주가 복원, point-in-time 유니버스, 상장폐지 처리."""

import numpy as np
import pandas as pd
import pytest

from quant_ai.backtest.backtester import BacktestConfig, Backtester
from quant_ai.data.collectors.marcap import (
    adjusted_bars,
    build_krx_dataset,
    cap_weighted_index,
    point_in_time_universe,
)


def fake_marcap(tmp_path, n_codes=12, days=420, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=days)
    rows = []
    for k in range(n_codes):
        code = f"{k + 1:05d}0"
        px, stocks = 10_000.0 * (k + 1), 1_000_000
        last_day = days if k != 3 else 250  # 종목 3 은 250일째 상장폐지
        for i, d in enumerate(dates[:last_day]):
            prev = px
            r = rng.normal(0.0005, 0.02)
            if k == 0 and i == 200:  # 종목 0: 50:1 액면분할
                prev, stocks = prev / 50, stocks * 50
                px = px / 50
            px = px * (1 + r)
            rows.append({"Date": d, "Code": code, "Name": f"종목{k}", "Open": prev, "High": max(prev, px) * 1.01,
                         "Low": min(prev, px) * 0.99, "Close": px, "Changes": px - prev, "Volume": 1e5,
                         "Amount": px * 1e5 * (5 if k < 8 else 0.001), "Marcap": px * stocks,
                         "Market": "KOSPI" if k % 2 == 0 else "KOSDAQ"})
    # 우선주·KONEX 는 제외되어야 함
    rows.append({**rows[0], "Code": "000015", "Name": "우선주"})
    rows.append({**rows[0], "Code": "999990", "Market": "KONEX"})
    df = pd.DataFrame(rows)
    df.to_parquet(tmp_path / "marcap-2020.parquet")
    return df


def test_split_is_adjusted_away(tmp_path):
    df = fake_marcap(tmp_path)
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize("UTC")
    b = adjusted_bars(df, ["000010"])["000010"]
    rets = b["close"].pct_change().dropna()
    assert rets.abs().max() < 0.15  # 원시 가격은 -98% 지만 수정주가는 정상 범위
    raw = df[df.Code == "000010"].set_index("Date")["Close"]
    assert b["close"].iloc[-1] == pytest.approx(raw.iloc[-1])  # 최근 가격은 원시 가격과 일치


def test_universe_is_point_in_time_and_filters(tmp_path):
    df = fake_marcap(tmp_path)
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize("UTC")
    df = df[df["Code"].str.endswith("0") & df["Market"].isin(["KOSPI", "KOSDAQ"])]
    elig = point_in_time_universe(df, top_n=5, min_amount=1e8)
    first_month = elig.index.tz_convert(None).to_period("M") == elig.index.tz_convert(None).to_period("M")[0]
    assert not elig[first_month].any().any()  # 첫 달은 전월 정보가 없으니 매매 대상 없음
    assert (elig.sum(axis=1).iloc[40:] <= 5).all()
    assert not elig.get("000090", pd.Series(False)).any()  # 거래대금 부족 종목 제외


def test_build_dataset_excludes_preferred_and_konex(tmp_path):
    fake_marcap(tmp_path)
    ds = build_krx_dataset(tmp_path, 2020, 2020, top_n=6, min_amount=1e6)
    assert "000015" not in ds.bars and "999990" not in ds.bars
    assert ds.benchmark["close"].notna().all()


def test_cap_weighted_index_matches_single_stock(tmp_path):
    df = fake_marcap(tmp_path, n_codes=1)
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize("UTC")
    df = df[df.Code == "000010"]
    idx = cap_weighted_index(df, "KOSPI")["close"]
    adj = adjusted_bars(df, ["000010"])["000010"]["close"]
    r1, r2 = idx.pct_change().dropna(), adj.pct_change().dropna()
    common = r1.index.intersection(r2.index)
    assert len(common) > 300 and np.allclose(r1.loc[common], r2.loc[common], atol=1e-9)


def test_backtest_respects_eligibility_and_exits_delisted(tmp_path):
    fake_marcap(tmp_path, days=520)
    ds = build_krx_dataset(tmp_path, 2020, 2020, top_n=8, min_amount=1e6)
    cfg = BacktestConfig(min_train_dates=120, retrain_every=40, min_prob=0.0, top_k=8)
    res = Backtester(cfg).run(ds.bars, ds.benchmark, eligible=ds.eligible)
    preds = res.predictions
    for ts, sym in zip(pd.to_datetime(preds["ts"]), preds["symbol"], strict=True):
        assert ds.eligible.loc[ts, sym]  # 비대상 종목은 예측/매매하지 않음
    orders = [e.data for e in res.journal.of_kind("order") if e.symbol == "000040" and e.data["status"] == "filled"]
    buys = sum(o["qty"] for o in orders if o["side"] == "buy")
    sells = sum(o["qty"] for o in orders if o["side"] == "sell")
    assert buys == sells  # 상장폐지 종목은 결국 전량 청산

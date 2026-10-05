"""v28: 신호 엔진 2.0 (과거 결과로 정한 가중치 · 보지 않은 기간 점수 보정 · 봉인된 전진 기록) · 매수/매도 후보 화면."""

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops
from quant_ai import signals2 as S2

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    d = tmp_path_factory.mktemp("v28")
    return QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))


def _close(n_days=700, n_sym=40, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n_days, tz="UTC")
    rets = rng.normal(0.0003, 0.02, (n_days, n_sym))
    cols = [f"{i:06d}" for i in range(1, n_sym + 1)]
    return pd.DataFrame(10000 * np.exp(np.cumsum(rets, axis=0)), index=idx, columns=cols)


def _bars(close, vol=1e6, thin=()):
    return {s: pd.DataFrame({"close": close[s].values, "volume": [10.0 if s in thin else vol] * len(close)}, index=close.index)
            for s in close.columns}


# ------------------------------------------------------------------ 신호 · 가중치 학습 · 점수 보정
def test_panels_and_price_signals_shapes():
    close = _close(400, 25)
    c, v = S2.panels(_bars(close))
    assert c.shape == close.shape and v.shape == close.shape and str(c.index.tz) == "UTC"
    sig = S2.price_signals(c, v, {s: ("A" if i % 2 else "B") for i, s in enumerate(c.columns)})
    assert set(sig) == set(S2.PRICE_SIGNALS)
    for df in sig.values():
        assert df.shape == c.shape
        x = df.iloc[-1].dropna()
        assert len(x) and x.between(-3, 3).all()
    # 짧은 종목(30봉 미만)은 패널에서 빠진다
    short = {"X": pd.DataFrame({"close": [1.0] * 10, "volume": [1.0] * 10}, index=pd.bdate_range("2024-01-01", periods=10))}
    assert S2.panels(short)[0].empty


def test_learn_weights_finds_real_signal_ignores_noise_and_flips_reversed():
    close = _close()
    fwd = S2.forward_excess(close)
    rng = np.random.default_rng(1)
    noise = lambda: pd.DataFrame(rng.normal(0, 1, close.shape), index=close.index, columns=close.columns)  # noqa: E731
    sig = {"good": fwd * 50 + noise(), "bad": -(fwd * 50) + noise(), "noise": noise()}
    res = S2.learn_weights(sig, close)
    st = res["stats"]
    assert res["basis"] == "past"
    assert st["good"]["verdict"] == "효과 있음" and st["good"]["ic"] > 0.1
    assert st["bad"]["verdict"] == "반대로 작동" and res["weights"]["bad"] < 0
    assert st["noise"]["verdict"] == "효과 확인 안 됨" and "noise" not in res["weights"]
    assert abs(sum(abs(w) for w in res["weights"].values()) - 1) < 1e-3
    # 학습 구간은 보지 않은 기간(마지막 HOLDOUT+HORIZON) 앞에서 끝난다
    assert pd.Timestamp(res["train_to"], tz="UTC") < close.index[-S2.HOLDOUT - S2.HORIZON]


def test_learn_weights_falls_back_to_prior_when_nothing_works():
    close = _close()
    rng = np.random.default_rng(2)
    sig = {k: pd.DataFrame(rng.normal(0, 1, close.shape), index=close.index, columns=close.columns) for k in S2.PRICE_SIGNALS}
    res = S2.learn_weights(sig, close)
    assert res["basis"] == "prior" and res["weights"] == S2.PRIOR_W


def test_combine_and_calibrate_tiers():
    close = _close()
    fwd = S2.forward_excess(close)
    rng = np.random.default_rng(3)
    good = S2._clip(fwd * 60 + pd.DataFrame(rng.normal(0, 0.3, close.shape), index=close.index, columns=close.columns))
    comb = S2.combine({"a": good, "b": good * 0 + np.nan}, {"a": 1.0, "b": 0.5})
    assert comb.notna().values.mean() > 0.9  # 자료 없는 신호는 무시하고 가중 평균
    cal = S2.calibrate(comb, close)
    assert [b["label"] for b in cal["bins"]] == S2.BIN_LABEL
    assert cal["tier"]["key"] == "good" and cal["spread"] > 0
    rand = pd.DataFrame(rng.normal(0, 1.5, close.shape), index=close.index, columns=close.columns)
    assert S2.calibrate(rand, close)["tier"]["key"] in ("bad", "warn")
    bad = S2.calibrate(-good, close)
    assert bad["tier"]["key"] == "bad" and bad["spread"] < 0


# ------------------------------------------------------------------ 오늘의 후보
def test_compute_candidates_filters_and_seals_log(app):
    close = _close(500, 30)
    thin = {"000001", "000002"}
    bars = _bars(close, thin=thin)
    t0 = datetime(2026, 10, 5, 9, tzinfo=UTC)
    rows: dict = {}
    out = S2.compute(app, "KR", bars=bars, focus={"000010"}, now=t0, rows_out=rows)
    assert out["universe"] == 30 and out["eligible"] == 28  # 거래대금 작은 2종목 제외
    assert not thin & set(rows)
    for r in out["buy"]:
        assert r["score"] >= 0.5 and r["stop"] < r["last"] < r["invalid_above"]
        keys = {p["key"] for p in r["signals"]}
        assert keys <= set(S2.PRICE_SIGNALS) | set(S2.LIVE_SIGNALS) and r["evidence"]["label"] in S2.BIN_LABEL
    assert [r["score"] for r in out["buy"]] == sorted((r["score"] for r in out["buy"]), reverse=True)
    assert all(r["held"] or r["focus"] for r in out["sell"])
    assert not {r["symbol"] for r in out["sell"]} & {r["symbol"] for r in out["avoid"]}
    assert out["calibration"]["tier"]["key"] in ("good", "warn", "bad") and out["regime"]["text"]
    assert set(out["live_coverage"]) == set(S2.LIVE_SIGNALS) and "자동매매" in out["note"]
    # 같은 거래일 다시 계산해도 처음 봉인한 기록은 바뀌지 않는다
    key = f"KR:{out['as_of']}"
    first = ops.get_state(app.engine, S2.LOG_KEY)["days"][key]
    assert first["at"] == t0.isoformat() and len(first["hash"]) == 16 and len(first["all"]) == 28
    S2.compute(app, "KR", bars=bars, focus=set(), now=t0 + timedelta(hours=3))
    assert ops.get_state(app.engine, S2.LOG_KEY)["days"][key] == first
    # 일봉이 짧으면 지어내지 않고 오류
    assert S2.compute(app, "KR", bars=_bars(_close(40, 25)), focus=set())["error"]


def test_forward_record_scores_after_horizon(app):
    close = _close(80, 5)
    d0 = close.index[10]
    t1 = close.index[10 + S2.HORIZON]
    mkt = float((close.loc[t1] / close.loc[d0] - 1).mean())
    win = max(close.columns, key=lambda s: close.at[t1, s] / close.at[d0, s])
    ops.set_state(app.engine, S2.LOG_KEY, {"days": {
        f"KR:{d0.date()}": {"market": "KR", "date": str(d0.date()), "buy": [{"symbol": win}], "sell": [], "avoid": []},
        f"KR:{close.index[-3].date()}": {"market": "KR", "date": str(close.index[-3].date()), "buy": [{"symbol": win}]},
        "US:2020-01-01": {"market": "US", "date": "2020-01-01", "buy": []}}})
    fr = S2.forward_record(app.engine, close, "KR")
    assert fr["days"] == 2 and fr["pending_days"] == 1
    assert fr["buy"]["n"] == 1 and fr["buy"]["hit"] == 1.0
    assert abs(fr["buy"]["mean_excess"] - (close.at[t1, win] / close.at[d0, win] - 1 - mkt)) < 1e-4
    assert fr["sell"]["n"] == 0 and fr["sell"]["hit"] is None


def test_live_signals_from_states_and_disclosure_prior(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Disclosure
    now = datetime.now(UTC)
    with session_scope(app.engine) as s:
        s.add(Disclosure(source="DART", receipt_no="v28-1", symbol="000100", title="주식소각결정", filed_at=now.date(), url="u"))
    ops.set_state(app.engine, "flow:000100", {"at": now.isoformat(), "summary": {"foreign_z5": 1.2, "inst_z5": 2.5, "foreign_streak": 3,
                                                                                 "inst_streak": 2, "signal": "동반 순매수"}})
    ops.set_state(app.engine, "krcons:000100", {"surprises": [{"date": str(now.date()), "surprise_pct": 10.0, "metric": "op_income", "label": "3Q"}]})
    ops.set_state(app.engine, "community:000100", {"at": now.isoformat(), "bull": 45, "bear": 5})
    ops.set_state(app.engine, "flow:000200", {"at": (now - timedelta(days=10)).isoformat(), "summary": {"foreign_z5": 3}})  # 오래된 자료
    live = S2.live_signals(app, ["000100", "000200"], {})
    v = live["000100"]
    assert v["disclosure"]["score"] == S2.DISC_PRIOR["자사주 소각"] and "자사주 소각" in v["disclosure"]["text"]
    assert v["flow"]["score"] == 3.0  # -3 ~ +3 으로 자름
    assert v["earnings"]["score"] == 1.5 and "영업이익" in v["earnings"]["text"]
    assert v["community"]["score"] == -1.0 and "과열" in v["community"]["text"]
    assert "000200" not in live
    # 과거 반응 자료가 충분하면 그것으로 점수
    live2 = S2.live_signals(app, ["000100"], {"types": [{"type": "자사주 소각", "d5": {"n": 30, "mean": 0.01}}]})
    assert live2["000100"]["disclosure"]["score"] == 0.5 and "30번" in live2["000100"]["disclosure"]["text"]


def test_for_symbol_and_api(app, monkeypatch):
    from quant_ai.web.api import DashboardAPI
    row = {"symbol": "000300", "score": 1.0}
    full = {"market": "KR", "as_of": "2026-10-02", "buy": [row], "sell": [], "avoid": [],
            "calibration": {"tier": {"key": "warn", "label": "약한 근거"}}, "_rows": {"000300": row, "000400": {"symbol": "000400", "score": 0.1}}}
    monkeypatch.setitem(S2._CACHE, "KR", (time.monotonic(), full))
    assert S2.for_symbol(app, "000300")["side"] == "buy"
    assert S2.for_symbol(app, "000400")["side"] is None and S2.for_symbol(app, "000400")["row"]["score"] == 0.1
    assert S2.for_symbol(app, "000999")["row"] is None
    api = DashboardAPI(app)
    assert "_rows" not in api.signals2("kr") and api.signals2("kr")["buy"] == [row]
    assert api.signals2_stock(" 000300 ")["row"] == row
    with pytest.raises(ValueError):
        api.signals2_stock("  ")


# ------------------------------------------------------------------ 화면 연결
def test_picks_screen_wiring():
    t2 = (STATIC / "toss2.js").read_text()
    t1 = (STATIC / "toss.js").read_text()
    css = (STATIC / "toss.css").read_text()
    assert "TV.picks" in t2 and "function tPickCard" in t2 and "function tPicksHome" in t2 and "function tStockSignal" in t2
    assert "/api/signals2" in t2 and "/api/signals2/stock" in t2
    assert 'id="th-picks2"' in t1 and "tPicksHome(" in t1 and 'id="tsk-sum-sig"' in t1 and "tStockSignal(" in t1
    assert '["picks"' in (STATIC / "easy.js").read_text() and '["picks"' in (STATIC / "app.js").read_text()
    # v27 비교 화면의 같은 이름 클래스와 겹치지 않게 (예전: 후보 카드가 옆으로 붙어 나옴)
    assert "t2-cmp-pick" in t2 and ".t2-cmp-pick" in css and ".t2-pick {" in css
    srv = (ROOT / "src/quant_ai/web/server.py").read_text()
    assert '"/api/signals2"' in srv and '"/api/signals2/stock"' in srv
    assert 'sch.add("signals2"' in (ROOT / "src/quant_ai/scheduler.py").read_text()

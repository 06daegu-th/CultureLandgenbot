"""AI 섀도(코어 전용에서도 하루 한 번 AI 판단·채점) + AI 켜기 판정."""

from dataclasses import replace
from datetime import timedelta

import pandas as pd
import pytest

from quant_ai.review.promotion import MIN_DAYS, MIN_SCORED, ai_verdict


def _eq(ret: float, days: int = MIN_DAYS + 5, dd: float = 0.0) -> pd.Series:
    idx = pd.bdate_range("2026-01-01", periods=days)
    path = [1 + ret * i / (days - 1) for i in range(days)]
    if dd:
        path[days // 2] = path[days // 2 - 1] * (1 - dd)
    return pd.Series(path, index=idx) * 1e7


def test_verdict_no_data_and_collecting():
    assert ai_verdict({}, None)["status"] == "no_data"
    v = ai_verdict({"attr-core": _eq(0.05, days=10), "attr-veto": _eq(0.08, days=10)}, {"n": 30, "brier_skill": 0.1})
    assert v["status"] == "collecting" and 0 < v["progress"] < 1


def test_verdict_keep_core_without_skill_or_edge():
    books = {"attr-core": _eq(0.05), "attr-veto": _eq(0.20)}
    assert ai_verdict(books, {"n": MIN_SCORED, "brier_skill": -0.01})["status"] == "keep_core"  # 확률에 정보 없음
    same = {"attr-core": _eq(0.05), "attr-veto": _eq(0.055), "attr-full": _eq(0.04)}
    assert ai_verdict(same, {"n": MIN_SCORED, "brier_skill": 0.02})["status"] == "keep_core"  # 초과수익 부족


def test_verdict_promotes_simplest_passing_step_and_respects_drawdown():
    books = {"attr-core": _eq(0.05), "attr-veto": _eq(0.08), "attr-full": _eq(0.15)}
    v = ai_verdict(books, {"n": MIN_SCORED, "brier_skill": 0.03})
    assert v["status"] == "promote" and v["recommend"] == "attr-veto"  # 더 단순한 단계부터
    risky = {"attr-core": _eq(0.05), "attr-veto": _eq(0.10, dd=0.3)}
    assert ai_verdict(risky, {"n": MIN_SCORED, "brier_skill": 0.03})["status"] == "keep_core"  # 낙폭 악화


@pytest.fixture()
def core_only_app(tmp_path, monkeypatch):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    fake_marcap(tmp_path, n_codes=12, days=400)
    st = replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/t.db", artifacts_dir=tmp_path / "a",
                 core_only=True, max_data_age_days=0)
    monkeypatch.setattr(Settings, "has_llm", property(lambda self: True))  # 휴리스틱 AI 로 LLM 대신
    app = QuantAI(st)
    app.ingest_krx(tmp_path, years=0, top_n=10, end_year=2020)
    return app


def test_core_only_runs_ai_shadow_once_per_bar_without_touching_real_book(core_only_app):
    from quant_ai import ops
    from quant_ai.config import Mode
    from quant_ai.strategy.core_satellite import CoreSatelliteConfig
    from quant_ai.trading.broker import MarketQuote
    app = core_only_app
    calls = []
    real = app.decide
    app.decide = lambda **kw: calls.append(kw) or real(**kw)
    cfg = CoreSatelliteConfig(core_top_k=4, core_buffer_k=6, satellite_k=2, shortlist_k=6, use_ai=False)
    bars, _, _ = app.market_data()
    days = max(bars.values(), key=len).index[-3:]
    for t in days:
        q = {s: MarketQuote(last=float(b.loc[t, "close"])) for s, b in bars.items() if t in b.index}
        for hour in (1, 2):  # 같은 일봉으로 두 번 → AI 는 한 번만
            r = app.run_core_satellite(Mode.PAPER, as_of=t.to_pydatetime(), quotes=q, cfg=cfg,
                                       ts=t.to_pydatetime() + timedelta(hours=hour))
    assert len(calls) == len(days)
    st = ops.get_state(app.engine, "ai-shadow:paper")
    assert st["analyzed"] > 0 and st["bar"].startswith(str(days[-1].date()))
    assert r["plan"].satellite == [] and not r["plan"].vetoed  # 실제 장부는 AI 를 보지 않는다
    for book in ("attr-core", "attr-veto", "attr-full"):
        assert app.load_portfolio(book).positions, book
    v = app.ai_verdict()
    assert v["status"] in ("collecting", "no_data") and v["core_only"] and v["ai_shadow"]


def test_ai_shadow_off_without_llm(tmp_path):
    from quant_ai.config import Settings
    st = Settings.from_env({})
    assert not st.has_llm and st.ai_shadow
    assert not Settings.from_env({"QUANT_AI_SHADOW": "false"}).ai_shadow
    assert Settings.from_env({"QUANT_AI_OVERLAY": "veto"}).ai_overlay == "veto"
    assert Settings.from_env({"GEMINI_API_KEY": "x"}).has_llm

"""확률 보정: 지표 · Platt 적합 · 앙상블 적용 · DB 에서 AI 별 적합 · 공급자별 성적표."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from quant_ai.analysts.base import DIRECTION, NEWS, Opinion
from quant_ai.ensemble.calibration import Platt, calibration_report, fit_calibrators, load_calibrators, metrics
from quant_ai.ensemble.engine import EnsembleEngine


def _overconfident(n=4000, seed=0):
    """실제 확률 q 인데 AI 는 더 극단적으로 말함 (logit × 2.5)."""
    rng = np.random.default_rng(seed)
    q = rng.uniform(0.35, 0.65, n)
    y = (rng.uniform(size=n) < q).astype(float)
    said = 1 / (1 + np.exp(-2.5 * np.log(q / (1 - q))))
    return said, y, q


def test_metrics_detect_overconfidence():
    said, y, q = _overconfident()
    good, bad = metrics(q, y), metrics(said, y)
    assert good["ece"] < 0.03 < bad["ece"]
    assert bad["brier"] > good["brier"] and bad["logloss"] > good["logloss"]
    assert sum(b["n"] for b in bad["curve"]) == len(y)
    assert metrics([], [])["n"] == 0


def test_platt_shrinks_overconfident_ai():
    said, y, _ = _overconfident()
    cal = Platt.fit(said, y)
    assert 0.2 < cal.a < 0.7  # 과신 → 기울기 축소 (이론값 1/2.5 = 0.4)
    after = [cal.apply(p) for p in said]
    assert metrics(after, y)["ece"] < metrics(said, y)["ece"] / 2
    assert Platt().apply(0.7) == pytest.approx(0.7) and Platt().apply(None) is None


def test_ensemble_uses_calibrated_probability_and_keeps_raw():
    ops = [Opinion("primary", "X", 0.80, 0.8), Opinion("nvidia", "X", 0.62, 0.6)]
    raw = EnsembleEngine().combine("X", ops)
    cal = EnsembleEngine().combine("X", ops, calibrators={"primary": Platt(a=0.3, b=0.0)})
    c = next(x for x in cal.contributions if x.analyst == "primary")
    assert c.prob_raw == 0.80 and c.prob_up < 0.65
    assert cal.prob_up < raw.prob_up  # 과신하던 AI 의 목소리가 줄어듦
    assert next(x for x in cal.contributions if x.analyst == "nvidia").prob_raw is None


@pytest.fixture()
def session(tmp_path):
    from quant_ai.data.db import init_db, make_engine, session_scope
    eng = make_engine(f"sqlite:///{tmp_path}/c.db")
    init_db(eng)
    with session_scope(eng) as s:
        yield s


def _add_opinions(s, analyst, probs, outcomes, backend, provider=None, cat=DIRECTION):
    from quant_ai.data.models import AnalystOpinionRecord
    now = datetime.now(UTC)
    for i, (p, y) in enumerate(zip(probs, outcomes, strict=True)):
        s.add(AnalystOpinionRecord(analyst=analyst, symbol="X", as_of=now - timedelta(hours=i), horizon_bars=5,
                                   category=cat, prob_up=float(p), confidence=0.5, veto=False,
                                   payload={"backend": backend, **({"provider": provider} if provider else {})},
                                   realized_return=0.01 if y else -0.01, correct=(p >= 0.5) == bool(y)))
    s.flush()


def test_fit_calibrators_from_db_and_report(session):
    said, y, _ = _overconfident(n=400)
    _add_opinions(session, "primary", said, y, "gemini-2.5-pro", "gemini")
    _add_opinions(session, "nvidia", said[:20], y[:20], "nvidia/nemotron")  # 표본 부족 → 보정 안 함
    fitted = fit_calibrators(session)
    assert set(fitted) == {"primary"} and fitted["primary"]["a"] < 0.8
    assert fitted["primary"]["after"]["ece"] < fitted["primary"]["before"]["ece"]
    cals = load_calibrators(fitted)
    assert isinstance(cals["primary"], Platt)
    rep = calibration_report(session, fitted=fitted)
    assert rep["analysts"]["primary"]["raw"]["n"] == 400 and rep["analysts"]["primary"]["calibrator"]


def test_provider_scoreboard_names_each_ai(session):
    from quant_ai.ensemble.tracker import provider_of, provider_scoreboard
    _add_opinions(session, "primary", [0.7] * 10, [1] * 8 + [0] * 2, "gemini-2.5-pro", "gemini", cat=NEWS)
    _add_opinions(session, "risk", [0.3] * 10, [0] * 6 + [1] * 4, "openai/gpt-oss-120b", "groq")
    rows = provider_scoreboard(session)
    gem = next(r for r in rows if r["provider"] == "Gemini")
    assert gem["label"] == "뉴스 해석" and gem["accuracy"] == pytest.approx(0.8) and gem["roles"] == ["primary"]
    assert next(r for r in rows if r["provider"] == "Groq")["accuracy"] == pytest.approx(0.6)
    assert provider_of("@cf/google/gemma-3-12b-it") == "Cloudflare" and provider_of("sklearn:hgb") == "Quant 모델"

"""v36 'AI 가 정말 보고 판단하나' + 화면 신뢰: 역할에 맞는 규칙 AI · 차트 신호 · 자료 표 · 오래된 가격 · 차트 눈금."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quant_ai import ops

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/quant_ai/web/static"


def _ctx(**kw):
    from quant_ai.analysts.base import MarketContext
    base = dict(symbol="005930", name="삼성전자", market="KOSPI", as_of=datetime(2026, 9, 23, tzinfo=UTC), horizon_days=5,
                price={"ret_5": 0.01, "ret_20": 0.09}, regime={})
    return MarketContext(**(base | kw))


# ------------------------------------------------------------------ 1. 규칙 AI: 자기 역할의 재료만 · 없으면 기권
def test_news_rule_ai_abstains_without_news_and_never_uses_momentum():
    from quant_ai.analysts.analysts import HeuristicAnalyst
    a = HeuristicAnalyst("primary")
    op = a.analyze(_ctx(price={"ret_5": 0.2, "ret_20": 0.5}))  # 주가는 크게 올랐지만 뉴스 0건
    assert op.prob_up is None and op.confidence == 0 and "재료 없음" in op.summary and "뉴스" in op.reasons[0]
    good = a.analyze(_ctx(news=[{"sentiment": 0.8, "importance": 0.9}, {"sentiment": 0.6, "importance": 0.5}]))
    assert good.prob_up > 0.6 and good.confidence <= a.MAX_CONF and "2건" in good.reasons[0] and good.backend == "heuristic-v36"
    # 이미 5일 동안 크게 오른 호재는 선반영 → 절반만
    pre = a.analyze(_ctx(news=[{"sentiment": 0.8, "importance": 0.9}, {"sentiment": 0.6, "importance": 0.5}], price={"ret_5": 0.12}))
    assert 0.5 < pre.prob_up < good.prob_up and any("반영" in r for r in pre.reasons)
    # 커뮤니티 과열은 약한 반대 신호
    hot = a.analyze(_ctx(news=[{"sentiment": 0.5, "importance": 0.5}], community={"mood": 0.9, "posts": 40}))
    calm = a.analyze(_ctx(news=[{"sentiment": 0.5, "importance": 0.5}]))
    assert hot.prob_up < calm.prob_up and any("커뮤니티" in r for r in hot.reasons)


def test_macro_rule_ai_uses_market_state_regime_and_macro():
    from quant_ai.analysts.analysts import HeuristicAnalyst
    a = HeuristicAnalyst("nvidia")
    assert a.analyze(_ctx()).prob_up is None
    on = a.analyze(_ctx(market_state={"score": 75, "label": "RISK ON"}, regime={"regime": "bull_quiet"}, macro={"_risk_appetite": 0.4}))
    off = a.analyze(_ctx(market_state={"score": 25, "label": "RISK OFF"}, regime={"regime": "bear_volatile"}, macro={"_risk_appetite": -0.4}))
    assert on.prob_up > 0.55 > 0.45 > off.prob_up and on.confidence <= a.MAX_CONF
    assert any("시장 상태" in r for r in on.reasons) and any("경제지표" in r for r in on.reasons)
    # 자료가 부족하다고 표시된 시장 상태는 쓰지 않는다
    assert a.analyze(_ctx(market_state={"score": 50, "insufficient": True})).prob_up is None


def test_chart_signal_ai_uses_holdout_hit_rate_not_words():
    from quant_ai.analysts.analysts import SignalAnalyst, build_analysts
    from quant_ai.config import Settings
    a = SignalAnalyst()
    assert a.analyze(_ctx()).prob_up is None
    sg = {"score": 1.84, "tier": {"key": "warn", "label": "약한 근거"}, "bin": {"label": "아주 높음", "n": 665, "hit": 0.43, "mean": 0.031},
          "top": [{"label": "추세", "text": "평균선 위"}]}
    op = a.analyze(_ctx(signal=sg))
    assert op.prob_up < 0.5  # 점수는 높아도 '보지 않은 기간'에 시장을 이긴 비율이 43% 면 그대로 말한다
    assert abs(op.prob_up - (0.5 - 0.07 * 665 / 865)) < 1e-9 and op.confidence == a.CONF["warn"]
    assert any("665번 중 43%" in r for r in op.reasons) and "약한 근거" in op.summary
    # 학습 모델이 없으면(검증 탈락 포함) 차트 신호가 대신 판단에 들어간다
    names = [x.name for x in build_analysts(replace(Settings.from_env({}), llm_providers={}, anthropic_enabled=False), None)]
    assert "chart" in names and names.index("chart") == names.index("quant") + 1


# ------------------------------------------------------------------ 2. 이번 판단에 들어간 자료 / 빠진 자료
def test_for_record_reports_used_and_missing_inputs():
    from quant_ai.aiinputs import for_record
    p = {"evidence": {"price": {"last_bar": "2026-09-23"}, "regime": {"regime": "sideways"}, "market": {"label": "RISK ON"},
                      "signal": {"score": 1.8, "tier": {"label": "약한 근거"}}},
         "contributions": [{"analyst": "primary", "prob_up": None, "backend": "heuristic-v36", "summary": "재료 없음 — 뉴스 0건"},
                           {"analyst": "nvidia", "prob_up": 0.55, "backend": "heuristic-v36", "summary": "규칙"},
                           {"analyst": "chart", "prob_up": 0.45, "backend": "signals2", "summary": "가격 신호"},
                           {"analyst": "quant", "prob_up": None, "backend": "sklearn", "summary": "기권: 학습된 모델 없음"}]}
    r = for_record(p)
    src = {s["key"]: s for s in r["sources"]}
    assert src["chart"]["used"] and "+1.8" in src["chart"]["detail"] and not src["news"]["used"] and "0건" in src["news"]["detail"]
    assert r["chart_only"] and r["n_used"] == 0 and "차트 위주" in r["headline"] and "AI 키가 없어" in r["headline"] and not r["llm"]
    rd = {x["analyst"]: x for x in r["readers"]}
    assert rd["primary"]["empty"] and rd["quant"]["abstain"] and rd["quant"]["kind"].startswith("쉼") and rd["chart"]["kind"] == "검증된 가격 신호"
    # LLM 이 읽고 뉴스·공시·수급이 있으면 '여러 자료를 함께'
    full = for_record({"evidence": {"price": {"last_bar": "2026-09-23"}, "news": [{"title": "a", "n_articles": 3}],
                                    "disclosures": [{"title": "공급계약"}], "flow": {"streak": 3}, "macro": {"DGS10": {"last": 4}}},
                       "contributions": [{"analyst": "primary", "prob_up": 0.6, "backend": "gemini-2.5-flash"}]})
    assert full["llm"] and full["level"] == "good" and full["n_used"] == 4 and "기사 3건" in {s["key"]: s for s in full["sources"]}["news"]["detail"]


def test_coverage_says_what_is_off_and_why(tmp_path):
    from quant_ai.aiinputs import coverage
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    app = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/c.db", artifacts_dir=tmp_path / "a",
                          llm_providers={}, anthropic_enabled=False))
    c = coverage(app)
    rows = {r["key"]: r for r in c["rows"]}
    assert set(rows) == {"chart", "news", "disclosure", "macro", "community", "flow", "llm"}
    assert rows["news"]["status"] == "off" and "24시간" in rows["news"]["why"] and rows["llm"]["status"] == "off"
    assert c["level"] == "warn" and "차트" in c["headline"]
    ops.set_state(app.engine, "community:005930", {"at": datetime.now(UTC).isoformat(), "n": 5})
    assert {r["key"]: r for r in coverage(app)["rows"]}["community"]["status"] == "ok"


# ------------------------------------------------------------------ 3. 예전 '모멘텀 휴리스틱' 성적·보정을 물려받지 않는다
def test_legacy_heuristic_records_are_not_used_for_calibration():
    from quant_ai.ensemble.calibration import _legacy_consensus, load_calibrators
    old = {"versions": {"code": "0.35.0", "roles": {"primary": {"backend": "HeuristicAnalyst"}}}}
    assert _legacy_consensus(old)
    assert not _legacy_consensus({"versions": {"code": "0.36.0", "roles": {"primary": {"backend": "HeuristicAnalyst"}}}})
    assert not _legacy_consensus({"versions": {"code": "0.20.0", "roles": {"primary": {"backend": "gemini"}}}})
    assert load_calibrators({"_meta": {"v": 36}, "primary": {"a": 1.0, "b": 0.0, "n": 60}}).keys() == {"primary"}


# ------------------------------------------------------------------ 4. 오래된 가격으로 사지 않는다 · 같은 '밀림' 계산
@pytest.fixture(scope="module")
def mapp(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v36")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


def test_autopilot_run_now_refuses_stale_prices(mapp):
    from quant_ai import autopilot as AP
    from quant_ai.asof import stamp
    now = datetime(2026, 10, 7, 1, 30, tzinfo=UTC)
    full = {"_rows": {}, "as_of": "2026-09-23", "regime": {"above_200": True}, "calibration": {}}
    lag = AP.price_lag(full, now)
    assert lag == stamp(datetime(2026, 9, 23, tzinfo=UTC), "bar_kr", now)["lag_days"] >= 1  # 화면 위 '밀림'과 같은 숫자
    r = AP.run(mapp, now=now, force=True, full=full)  # '지금 한 번 실행' 도 막는다
    assert "밀림" in r["skipped"] and r["lag"] == lag and "오래된 가격" in r["skipped"]
    st = ops.get_state(mapp.engine, AP.STATE)
    assert "시세를 새로 받으면" in st["last_skip"] and st.get("last_run") != "2026-10-07"  # 수동 실행은 '오늘 결정함'으로 남기지 않음
    assert AP.price_lag({"as_of": "nope"}, now) == 99


def test_stock_header_price_age_and_live_reference(mapp):
    from quant_ai import toss
    sym = next(s for s in mapp._all_bars()[0] if s[:1].isdigit())
    b = mapp._all_bars()[0][sym]
    st = toss.stock(mapp, sym)
    assert st["price_age"]["status"] == "old" and "밀림" in st["price_age"]["age"]
    last, prev = float(b["close"].iloc[-1]), float(b["close"].iloc[-2])
    # 실시간 시세가 마지막 일봉 '다음 날' 것이면 등락 기준 = 마지막 일봉 종가 (v35 는 그 전날 종가로 계산)
    nxt = (b.index[-1] + timedelta(days=1)).replace(hour=3)
    ops.set_state(mapp.engine, "live_quotes", {sym: {"price": last * 1.02, "at": nxt.isoformat()}})
    lv = toss.stock(mapp, sym)["live"]
    assert lv["prev_close"] == last and abs(lv["chg_pct"] - 0.02) < 1e-9
    # 같은 날 시세면 기준 = 그 전날 종가
    ops.set_state(mapp.engine, "live_quotes", {sym: {"price": last, "at": b.index[-1].replace(hour=5).isoformat()}})
    assert toss.stock(mapp, sym)["live"]["prev_close"] == prev
    ops.set_state(mapp.engine, "live_quotes", {})


def test_home_picks_take_latest_record_for_same_day(mapp):
    from quant_ai import toss
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    t = datetime(2020, 12, 30, tzinfo=UTC)
    with session_scope(mapp.engine) as s:
        for p in (0.59, 0.47):  # 같은 날 판단을 다시 하면 나중 것이 맞다
            s.add(ConsensusRecord(symbol="999999", as_of=t, action="HOLD", prob_up=p, confidence=50, conflict="low", payload={}))
    pk = {x["symbol"]: x for x in toss._picks(mapp, n=50)}
    assert pk["999999"]["prob_up"] == 0.47 and pk["999999"]["as_of"] == "2020-12-30 일봉"


# ------------------------------------------------------------------ 5. 표시: 날짜 · 차트 눈금 · 쉬운 오류
def test_daily_bar_label_has_no_fake_time():
    from quant_ai.asof import label
    assert label(datetime(2026, 9, 23, tzinfo=UTC)) == "2026-09-23 일봉"
    assert label(datetime(2026, 9, 23, 5, tzinfo=UTC)) == "2026-09-23 14:00 KST"
    assert label(datetime(2026, 9, 23, tzinfo=UTC), with_time=False) == "2026-09-23"


def test_screens_show_inputs_staleness_and_clean_axes():
    app_js = (STATIC / "app.js").read_text()
    toss = (STATIC / "toss.js").read_text()
    toss2 = (STATIC / "toss2.js").read_text()
    assert 'priceFormatter: (p) => p <= 0 ? ""' in app_js  # 축이 0 아래로 내려가도 '0.000' 눈금 없음
    assert "function tAiInputs(" in toss and "${tAiInputs(v.inputs)}" in toss and "function tAiCoverage(" in toss
    assert "tsk-stale" in toss and "지금 실제 주가와 다를 수 있어요" in toss and "live.chg ??" in toss
    assert "52주 최고 (장중)" in toss and "오를 확률" in toss
    assert toss2.count("tAiCoverage(") >= 2 and "pv.stale" in toss2 and "오늘은 사지 않았어요" in toss2
    css = (STATIC / "toss.css").read_text()
    assert ".ai-inputs" in css and ".tsk-stale" in css and ".ai-cov" in css


def test_verdict_drops_reasons_from_ais_without_inputs(mapp):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import ConsensusRecord
    from quant_ai.explain import verdict
    sym = next(s for s in mapp._all_bars()[0] if s[:1].isdigit())
    payload = {"reasons": ["[primary] 20일 모멘텀 상승 (+9.2%)", "[chart] 추세: 평균선 위"], "horizon": 5,
               "contributions": [{"analyst": "primary", "prob_up": 0.64, "weight": 0.5, "backend": "heuristic",
                                  "summary": "오프라인 휴리스틱 의견"},
                                 {"analyst": "chart", "prob_up": 0.56, "weight": 0.5, "backend": "signals2", "summary": "검증된 가격 신호 점수 +1.8"}],
               "evidence": {"price": {"last_bar": "2020-12-30"}}}
    with session_scope(mapp.engine) as s:
        s.add(ConsensusRecord(symbol=sym, as_of=datetime(2030, 1, 1, tzinfo=UTC), action="HOLD", prob_up=0.6, confidence=50,
                              conflict="low", payload=payload))
    v = verdict(mapp, sym)
    assert not any("모멘텀" in w or "뉴스 AI" in w for w in v["why_buy"]) and any("차트 신호" in w for w in v["why_buy"])
    assert v["inputs"]["chart_only"] and {r["analyst"] for r in v["inputs"]["readers"] if r["empty"]} == {"primary"}

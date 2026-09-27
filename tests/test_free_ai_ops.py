"""무료 AI 공급자 구성(역할 배정·모델 폴백·일 한도) · 오래된 데이터 차단 · 입출금 보정 · marcap 동기화 · doctor · run.sh."""

import io
import json
import subprocess
import urllib.error
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_ai.analysts.analysts import assign_roles, build_analysts
from quant_ai.analysts.llm_clients import FREE_PROVIDERS, LLMError, OpenAICompatClient
from quant_ai.config import Settings

ALL_KEYS = {"GEMINI_API_KEY": "g-secret", "NVIDIA_API_KEY": "nvapi-secret", "GROQ_API_KEY": "gsk-secret",
            "CLOUDFLARE_API_TOKEN": "cf-secret", "CLOUDFLARE_ACCOUNT_ID": "acc123"}


# ------------------------------------------------------------------ 역할 배정
def test_roles_all_free_providers_are_diverse():
    roles = assign_roles(Settings.from_env(ALL_KEYS))
    assert roles == {"primary": "gemini", "nvidia": "nvidia", "risk": "groq", "panel": "cloudflare"}


def test_roles_single_free_key_and_claude():
    only = assign_roles(Settings.from_env({"GEMINI_API_KEY": "g"}))
    assert only == {"primary": "gemini", "risk": "gemini"}  # 두 번째 의견은 휴리스틱, 리스크는 재사용
    with_claude = assign_roles(Settings.from_env({"ANTHROPIC_API_KEY": "a", "GROQ_API_KEY": "q"}))
    assert with_claude["primary"] == "claude" and with_claude["nvidia"] == "groq"
    assert assign_roles(Settings.from_env({})) == {}


def test_roles_override_and_invalid_ignored():
    st = Settings.from_env({**ALL_KEYS, "QUANT_AI_ROLES": "primary=groq, second=gemini, risk=openai"})
    roles = assign_roles(st)
    assert roles["primary"] == "groq" and roles["nvidia"] == "gemini" and roles["risk"] != "openai"


def test_provider_settings_models_and_limits():
    st = Settings.from_env({**ALL_KEYS, "QUANT_GEMINI_MODELS": "gemini-x-pro, gemini-x-flash",
                            "QUANT_GROQ_DAILY_LIMIT": "50"})
    assert st.llm_providers["gemini"]["models"] == ("gemini-x-pro", "gemini-x-flash")
    assert st.llm_providers["groq"]["daily"] == 50
    assert st.llm_providers["cloudflare"]["account"] == "acc123"
    assert "secret" not in repr(st)  # 키가 로그·예외 메시지로 새지 않도록


def test_dashboard_role_info_never_contains_keys():
    from quant_ai.web.api import ai_roles_info
    info = ai_roles_info(Settings.from_env(ALL_KEYS))
    assert [r["role"] for r in info] == ["primary", "nvidia", "risk", "panel"]
    assert "secret" not in json.dumps(info) and all(r["free"] for r in info)


def test_build_analysts_includes_panel_and_risk_llm():
    names = [a.name for a in build_analysts(Settings.from_env(ALL_KEYS), None)]
    assert names == ["primary", "nvidia", "panel", "quant", "regime", "risk"]
    risk = build_analysts(Settings.from_env(ALL_KEYS), None)[-1]
    assert risk.llm is not None and risk.llm.client.provider == "groq"
    assert [a.name for a in build_analysts(Settings.from_env({}), None)] == ["primary", "nvidia", "quant", "regime",
                                                                               "risk"]


# ------------------------------------------------------------------ 모델 폴백 · 분당 간격
def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"{}"))


def _ok(model):
    return {"choices": [{"message": {"content": json.dumps({"ok": True, "m": model})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_model_fallback_on_quota_and_missing_model(monkeypatch):
    c = OpenAICompatClient.from_spec(FREE_PROVIDERS["gemini"], "k", ("pro", "flash", "lite"), rpm=0)
    calls = []

    def send(url, body):
        calls.append(body["model"])
        if body["model"] == "pro":
            raise _http_error(429)  # 무료 한도 초과
        if body["model"] == "flash":
            raise _http_error(404)  # 없어진 모델
        return _ok(body["model"])
    monkeypatch.setattr(c, "_send", send)
    assert c.complete_json("s", "u", {})["m"] == "lite"
    assert c.model == "lite" and c.backend_id == "pro"  # 성적표는 설정한 1순위 모델 기준으로 유지
    calls.clear()
    c.complete_json("s", "u2", {})
    assert calls == ["lite"]  # 오늘 소진된 모델은 다시 시도하지 않음


def test_all_models_exhausted_raises(monkeypatch):
    c = OpenAICompatClient.from_spec(FREE_PROVIDERS["groq"], "k", ("a", "b"), rpm=0)
    monkeypatch.setattr(c, "_send", lambda url, body: (_ for _ in ()).throw(_http_error(429)))
    with pytest.raises(LLMError, match="사용 가능한 모델 없음"):
        c.complete_json("s", "u", {})


def test_cloudflare_needs_account_and_builds_url():
    with pytest.raises(LLMError):
        OpenAICompatClient.from_spec(FREE_PROVIDERS["cloudflare"], "k")
    c = OpenAICompatClient.from_spec(FREE_PROVIDERS["cloudflare"], "k", account="acc")
    assert c.base_url == "https://api.cloudflare.com/client/v4/accounts/acc/ai/v1"
    assert c.model.startswith("@cf/google/gemma")


def test_rate_limit_spacing(monkeypatch):
    import quant_ai.analysts.llm_clients as lc
    sleeps = []
    monkeypatch.setattr(lc.time, "sleep", lambda s: sleeps.append(s))
    lc._LAST_CALL.pop("groq", None)
    c = OpenAICompatClient.from_spec(FREE_PROVIDERS["groq"], "k", ("m",), rpm=30)
    monkeypatch.setattr(c, "_send", lambda url, body: _ok("m"))
    c.complete_json("s", "1", {})
    c.complete_json("s", "2", {})
    assert sleeps and 1.5 < sleeps[-1] <= 2.0  # 분당 30회 → 2초 간격


# ------------------------------------------------------------------ 무료 일 한도 (GuardedLLM)
class FakeLLM:
    provider, model, last_usage = "gemini", "gemini-2.5-pro", (100, 50)

    def __init__(self):
        self.n = 0

    def complete_json(self, system, user, schema):
        self.n += 1
        return {"ok": True}


def test_free_daily_cap_stops_calls_but_cache_still_works(tmp_path):
    from quant_ai.analysts.guard import GuardedLLM, estimate_cost
    from quant_ai.data.db import init_db, make_engine
    eng = make_engine(f"sqlite:///{tmp_path}/g.db")
    init_db(eng)
    inner = FakeLLM()
    g = GuardedLLM(inner, eng, "primary", daily_budget_usd=0.0, cache_ttl_minutes=60, daily_requests=2)
    g.complete_json("s", "a", {})
    g.complete_json("s", "b", {})
    with pytest.raises(LLMError, match="무료 일 한도"):
        g.complete_json("s", "c", {})
    assert g.complete_json("s", "a", {}) == {"ok": True} and inner.n == 2  # 캐시는 한도와 무관
    assert estimate_cost("gemini", "gemini-2.5-pro", (10**6, 10**6)) == 0.0  # 무료 → 예산 0 이어도 막히지 않음


# ------------------------------------------------------------------ 오래된 데이터 차단
def test_data_age_days_counts_missing_weekdays():
    from quant_ai.pipeline import QuantAI
    kst9 = lambda d: datetime(*d, 0, 30, tzinfo=UTC)  # noqa: E731 - 한국 09:30
    assert QuantAI.data_age_days(pd.Timestamp("2026-09-25", tz="UTC"), kst9((2026, 9, 28))) == 0  # 금 → 월
    assert QuantAI.data_age_days(pd.Timestamp("2026-09-23", tz="UTC"), kst9((2026, 9, 28))) == 2  # 목·금 누락
    assert QuantAI.data_age_days(pd.Timestamp("2026-09-18", tz="UTC"), kst9((2026, 9, 28))) == 5


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("ops")
    fake_marcap(d, n_codes=12, days=420)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a"))
    a.ingest_krx(d, years=0, top_n=10, end_year=2020)
    return a


def test_stale_data_skips_trading_and_alerts_once(app):
    from quant_ai.config import Mode
    from quant_ai.strategy.core_satellite import CoreSatelliteConfig
    n0 = len(app.notifier.sent)
    cfg = CoreSatelliteConfig(core_top_k=3, core_buffer_k=5, use_ai=False)
    r = app.run_core_satellite(Mode.PAPER, ts=datetime.now(UTC), cfg=cfg)
    assert r["plan"] is None and "갱신되지 않음" in r["skipped"] and not r["fills"]
    app.run_core_satellite(Mode.PAPER, ts=datetime.now(UTC), cfg=cfg)
    assert len(app.notifier.sent) == n0 + 1  # 같은 날 반복 알림 없음
    sheet = app.order_sheet({}, 10_000_000, use_ai=False, cfg=cfg)  # 수동 주문표는 경고만
    assert "영업일 전" in sheet.notes[0]


# ------------------------------------------------------------------ 입출금 보정
def test_time_weighted_index_ignores_deposits():
    from quant_ai.pipeline import time_weighted_index
    idx = pd.bdate_range("2026-01-05", periods=4, tz="UTC")
    eq = pd.Series([100.0, 110.0, 211.0, 211.0], index=idx)  # 3일째 100 입금 + 1 수익
    twr = time_weighted_index(eq, [{"date": "2026-01-07", "amount": 100.0}])
    assert list(np.round(twr.to_numpy(), 4)) == [100.0, 110.0, 111.0, 111.0]


def test_health_uses_cashflows(app):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import PortfolioSnapshot
    t0 = datetime(2025, 1, 2, 7, tzinfo=UTC)
    vals = np.r_[np.linspace(1e7, 1.02e7, 40), np.linspace(0.62e7, 0.63e7, 40)]  # 400만 원 출금
    with session_scope(app.engine) as s:
        for i, v in enumerate(vals):
            s.add(PortfolioSnapshot(mode="cf-test", ts=t0 + timedelta(days=i), cash=float(v), equity=float(v),
                                    positions={}))
    raw = app.strategy_health("cf-test", with_ic=False, notify=False)
    assert raw["status"] == "critical"  # 출금을 손실로 착각
    app.add_cashflow("cf-test", -4_000_000, (t0 + timedelta(days=40)).date(), "생활비 출금")
    fixed = app.strategy_health("cf-test", with_ic=False, notify=False)
    dd = next(c for c in fixed["checks"] if c["name"] == "drawdown")
    assert fixed["status"] == "ok" and dd["value"] > -0.02 and fixed["cashflows"] == 1
    with pytest.raises(ValueError):
        app.add_cashflow("cf-test", 0)


# ------------------------------------------------------------------ marcap 동기화 (git 명령)
def test_sync_marcap_clones_then_updates_sparse_years(tmp_path):
    from quant_ai.data.collectors.marcap import sync_marcap
    cmds = []
    d = tmp_path / "m" / "data"
    sync_marcap(d, years=3, today=date(2027, 1, 2), run=cmds.append)
    assert cmds[0][:2] == ["git", "clone"] and "--sparse" in cmds[0]
    assert cmds[-1][-3:] == ["/data/marcap-2025.parquet", "/data/marcap-2026.parquet", "/data/marcap-2027.parquet"]
    (tmp_path / "m" / ".git").mkdir(parents=True)
    cmds.clear()
    sync_marcap(d, years=3, today=date(2027, 1, 2), run=cmds.append)
    assert cmds[0][3:5] == ["pull", "--ff-only"] and cmds[1][3] == "sparse-checkout"


# ------------------------------------------------------------------ doctor · cashflow CLI · run.sh
def test_doctor_reports_stale_data(app, capsys):
    from quant_ai.cli import main
    with pytest.raises(SystemExit) as e:
        main(["--db", app.settings.database_url, "doctor"])
    out = capsys.readouterr().out
    assert e.value.code == 1 and "❌ 주가 데이터" in out and "✅ DB" in out


def test_cashflow_cli(app, capsys):
    from quant_ai.cli import main
    main(["--db", app.settings.database_url, "cashflow", "2500000", "--mode", "cli-cf", "--memo", "월급"])
    main(["--db", app.settings.database_url, "cashflow", "-500000", "--mode", "cli-cf"])
    out = capsys.readouterr().out
    assert "순입금 +2,000,000원" in out and "월급" in out


def test_run_sh_is_valid_bash():
    root = Path(__file__).resolve().parents[1]
    assert subprocess.run(["bash", "-n", str(root / "run.sh")], check=False).returncode == 0
    out = subprocess.run(["bash", str(root / "run.sh"), "help"], capture_output=True, text=True, check=True).stdout
    assert "./run.sh setup" in out and "./run.sh doctor" in out
    assert not (root / "main.py").exists()

"""실사용에서 나온 문제 회귀 테스트: AI 판단 병렬·즉시 저장 · 서킷 브레이커 · 해외 시세 폴백 · 종목 즉시 분석 · doctor."""

import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_ai import ops


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("field")
    fake_marcap(d, n_codes=12, days=400)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a",
                        max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=10, end_year=2020)
    return a


class SlowLLM:
    """느린 무료 LLM 흉내: 호출마다 delay 초. OpenAI 호환 클라이언트와 같은 속성."""

    def __init__(self, provider, delay=0.3, fail=False):
        self.provider, self.model, self.delay, self.fail = provider, f"{provider}-m", delay, fail
        self.models = (self.model,)
        self.last_usage = None
        self.call_lock = threading.Lock()
        self.calls = 0

    backend_id = property(lambda self: self.model)

    def complete_json(self, system, user, schema):
        self.calls += 1
        time.sleep(self.delay)
        if self.fail:
            from quant_ai.analysts.llm_clients import LLMError
            raise LLMError(f"{self.provider} 호출 실패: timed out")
        return {"ok": True, "summary": "테스트", "prob_up": 0.6, "confidence": 0.5, "reasons": ["r"], "risks": [],
                "sub_scores": {}, "veto": False}


def test_decide_runs_providers_in_parallel_and_saves_each_symbol(app, monkeypatch):
    """문제: AI 4곳을 한 줄로 부르고 전 종목이 끝나야 저장 → 수십 분 동안 '판단 0건'."""
    from quant_ai import pipeline
    from quant_ai.analysts import analysts as an
    from quant_ai.analysts.guard import GuardedLLM
    real = an.build_analysts
    clients = [SlowLLM(p) for p in ("gemini", "nvidia", "cloudflare")]

    def fake_build(settings, predictor, version=None, engine=None):
        base = real(settings, predictor, version, engine)
        llms = [an.LLMAnalyst(role, GuardedLLM(c, engine, role, daily_requests=None))
                for role, c in zip(("primary", "nvidia", "panel"), clients, strict=True)]
        return llms + [x for x in base if x.name not in ("primary", "nvidia")]
    monkeypatch.setattr(pipeline, "build_analysts", fake_build)
    syms = list(app.market_data()[0])[:4]
    t0 = time.monotonic()
    ds = app.decide(symbols=syms, scenarios=False)
    took = time.monotonic() - t0
    assert len(ds) == 4 and all(d.consensus_id for d in ds)
    assert took < 4 * 3 * 0.3 * 0.75, took  # 한 줄로 부르면 3.6초 → 공급자 동시 호출로 훨씬 빠름
    assert all(c.calls == 4 for c in clients)
    prog = ops.get_state(app.engine, "ai_progress:KR")
    assert prog["done"] == 4 and prog["total"] == 4 and not prog["running"]


def test_circuit_breaker_skips_dead_provider(app):
    from quant_ai.analysts import guard
    from quant_ai.analysts.guard import GuardedLLM
    from quant_ai.analysts.llm_clients import LLMError
    guard._BREAKER.clear()
    dead = SlowLLM("deadprov", delay=0.0, fail=True)
    g = GuardedLLM(dead, app.engine, "risk", daily_requests=None, cache_ttl_minutes=0)
    for i in range(3):
        with pytest.raises(LLMError):
            g.complete_json("s", f"u{i}", {})
    assert guard.breaker_open("deadprov") and dead.calls == 3
    with pytest.raises(LLMError, match="건너뜀"):
        g.complete_json("s", "u9", {})
    assert dead.calls == 3  # 더 부르지 않음 → 다른 AI 로 계속
    guard._BREAKER.clear()


def test_analyze_single_symbol_on_demand_and_watchlist(app):
    """문제: 코어 후보가 아닌 종목(예: SK하이닉스)은 AI 판단이 영영 없었다."""
    from quant_ai.actions import add_watch, analyze_symbol, start_action, watch_symbols
    sym = sorted(app.market_data()[0])[-1]
    r = analyze_symbol(app, sym)
    assert r["symbol"] == sym and r["action"] in ("BUY", "SELL", "HOLD", "NO_TRADE") and r["consensus_id"]
    assert sym in watch_symbols(app)
    for i in range(25):
        add_watch(app, f"{i:06d}")
    assert len(watch_symbols(app)) == 20  # 최근 20개만
    with pytest.raises(ValueError):
        analyze_symbol(app, "NVDA")  # 해외는 합의 대상 아님
    st = start_action(app, "analyze", {"symbol": sym})
    assert st["name"] == f"analyze:{sym}"


# ------------------------------------------------------------------ 해외 시세
def _df(n=60):
    idx = pd.bdate_range("2026-01-01", periods=n, tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9, "close": np.linspace(100, 120, n), "volume": 1e6}, index=idx)


def test_yahoo_429_falls_back_and_failures_are_cached(app, monkeypatch):
    from quant_ai.data import global_stocks as gs
    calls = []

    def blocked(sym):
        calls.append(sym)
        raise RuntimeError("HTTP Error 429: Too Many Requests")
    monkeypatch.setattr(gs, "DEFAULT_FETCHERS", (("yfinance", blocked), ("yahoo", blocked), ("nasdaq", lambda s: _df()),
                                                 ("stooq", blocked)))
    r = gs.ensure_global(app.engine, "AAPL")
    assert r["ok"] and r["source"] == "nasdaq"
    # 전부 실패 → 30분 동안 같은 종목을 다시 두드리지 않는다
    monkeypatch.setattr(gs, "DEFAULT_FETCHERS", (("yahoo", blocked), ("stooq", blocked)))
    calls.clear()
    bad = gs.ensure_global(app.engine, "MSFT")
    assert not bad["ok"] and "429" in bad["error"] and len(calls) == 2
    again = gs.ensure_global(app.engine, "MSFT")
    assert "잠시 대기" in again["error"] and len(calls) == 2


def test_nasdaq_and_stooq_parsers(monkeypatch):
    import json

    from quant_ai.data import global_stocks as gs
    rows = [{"date": f"{m:02d}/15/2026", "close": f"${100 + m}.50", "volume": "1,234,567", "open": "$100.00",
             "high": "$110.00", "low": "$90.00"} for m in range(1, 10)]
    monkeypatch.setattr(gs, "_http", lambda url, timeout=15.0, headers=None: json.dumps(
        {"data": {"tradesTable": {"rows": rows}}}).encode())
    df = gs.fetch_nasdaq("NVDA")
    assert len(df) == 9 and df["close"].iloc[-1] == 109.5 and df["volume"].iloc[0] == 1234567
    monkeypatch.setattr(gs, "_http", lambda url, timeout=15.0, headers=None: b"No data")
    with pytest.raises(RuntimeError, match="Stooq"):
        gs.fetch_stooq("NVDA")


def test_yfinance_batch_download(monkeypatch, app):
    import sys
    import types

    from quant_ai.data import global_stocks as gs
    frames = {s: _df().rename(columns=str.title) for s in ("NVDA", "TSLA")}
    raw = pd.concat(frames, axis=1)  # yfinance group_by="ticker" 모양: (종목, 컬럼)
    fake = types.SimpleNamespace(download=lambda tickers, **kw: raw)
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    got = gs.fetch_yfinance_many(["NVDA", "TSLA"])
    assert set(got) == {"NVDA", "TSLA"} and got["TSLA"]["close"].iloc[-1] == 120.0
    for s in ("NVDA", "TSLA"):
        ops.set_state(app.engine, f"global_fetch:{s}", {})
    r = gs.ensure_many(app.engine, ["NVDA", "TSLA"])
    assert r["fetched"] == 2 and not r["failed"]


# ------------------------------------------------------------------ doctor
def test_doctor_ai_is_fast_and_dedupes_providers(app, monkeypatch, capsys):
    """문제: 점검이 느린 공급자에서 수 분간 멈춘 것처럼 보였다 (긴 타임아웃·재시도·분당 대기)."""
    from quant_ai import cli
    from quant_ai.analysts import analysts as an
    made = []

    class C(SlowLLM):
        timeout, retries, min_interval, max_tokens = 60.0, 2, 7.5, 8192

    def fake_make(settings, prov):
        c = C(prov, delay=0.0, fail=prov == "groq")
        made.append(c)
        return c
    monkeypatch.setattr(an, "assign_roles", lambda st: {"primary": "gemini", "nvidia": "nvidia", "risk": "gemini",
                                                         "panel": "groq"})
    monkeypatch.setattr(an, "make_llm_client", fake_make)
    rows = cli._doctor_ai(app)
    assert [r[0] for r in rows] == ["ok", "ok", "ok", "fail"]
    assert len(made) == 3  # gemini 는 한 번만
    assert all(c.timeout == 40.0 and c.retries == 1 and c.min_interval == 0.0 for c in made)
    assert "네트워크" in rows[3][2]
    assert "확인 중" in capsys.readouterr().out


def test_keyboard_interrupt_is_friendly(monkeypatch, capsys):
    from quant_ai import cli
    monkeypatch.setattr(cli, "cmd_guardian", lambda args: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(SystemExit) as e:
        cli.main(["guardian"])
    assert e.value.code == 130 and "중단했습니다" in capsys.readouterr().err
    assert datetime.now(UTC) - timedelta(seconds=1) < datetime.now(UTC)


# ------------------------------------------------------------------ 실제 키로 나온 오류들 (Gemini 404 · NVIDIA 503 · Groq 1010)
def _http_error(code, body=b""):
    import io
    import urllib.error
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))


def test_retired_models_are_replaced_by_discovery(monkeypatch):
    from quant_ai.analysts import llm_clients as lc
    lc._DISCOVERED.clear()
    c = lc.OpenAICompatClient.from_spec(lc.FREE_PROVIDERS["gemini"], "k")
    sent = []

    def send(url, body):
        sent.append(body["model"])
        if body["model"].startswith("gemini-2.5"):
            raise _http_error(404, b'{"error":{"message":"models/gemini-2.5-pro is not found"}}')
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}
    monkeypatch.setattr(c, "_send", send)
    monkeypatch.setattr(c, "_get", lambda path: {"data": [{"id": f"models/{m}"} for m in (
        "gemini-3-flash", "gemini-3-pro-preview", "gemini-embedding-001", "gemini-3-flash-lite", "gemini-2.0-flash-tts")]})
    c.min_interval = 0
    assert c.complete_json("s", "u", {}) == {"ok": True}
    assert sent[:3] == ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
    assert c.model == "gemini-3-pro-preview" and c.models[0] == "gemini-3-pro-preview"
    sent.clear()
    c.complete_json("s", "u", {})
    assert sent == ["gemini-3-pro-preview"]  # 다음 호출부터 바로 새 모델
    assert lc.rank_models("gemini", ["models/gemini-3-flash", "models/gemini-2.5-pro", "models/text-embedding-004"]) == \
        ["gemini-2.5-pro", "gemini-3-flash"]


def test_overloaded_model_falls_back_without_blacklisting(monkeypatch):
    from quant_ai.analysts import llm_clients as lc
    from quant_ai.config import Settings
    st = Settings.from_env({"NVIDIA_API_KEY": "k", "QUANT_NVIDIA_MODEL": "nvidia/nemotron-3-super-120b-a12b"})
    models = st.llm_providers["nvidia"]["models"]
    assert models[0] == "nvidia/nemotron-3-super-120b-a12b" and len(models) >= 3  # 지정 모델 + 대체 후보
    c = lc.OpenAICompatClient.from_spec(lc.FREE_PROVIDERS["nvidia"], "k", models)
    c.min_interval = 0
    calls = []

    def send(url, body):
        calls.append(body["model"])
        if body["model"] == models[0]:
            raise _http_error(503, b'{"error":{"message":"Service temporarily overloaded"}}')
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}
    monkeypatch.setattr(c, "_send", send)
    c.complete_json("s", "u", {})
    assert calls == [models[0], models[1]] and c.model == models[1]
    assert models[0] not in c.exhausted  # 과부하는 일시적 → 다음 호출에 다시 시도


def test_requests_carry_program_user_agent(monkeypatch):
    import urllib.request

    from quant_ai.analysts import llm_clients as lc
    seen = {}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"{\\"ok\\": true}"}}]}'

    def urlopen(req, timeout=None):
        seen.update({k.lower(): v for k, v in req.header_items()})
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    c = lc.OpenAICompatClient.from_spec(lc.FREE_PROVIDERS["groq"], "k")
    c.min_interval = 0
    c.complete_json("s", "u", {})
    assert seen["user-agent"].startswith("quant-ai/")  # 'Python-urllib' 은 Cloudflare 1010 으로 차단됨


def test_doctor_hints_for_real_errors():
    from quant_ai.cli import _ai_hint
    assert "1010" in _ai_hint('HTTP 403 {"type":"https://developers.cloudflare.com/.../cloudflare-1xxx-errors/error-1010"}')
    assert "Workers AI" in _ai_hint('HTTP 403 {"errors":[{"message":"AiError: Ai: This account is not allowed'
                                    ' to access this model"}]} cloudflare')
    assert "과부하" in _ai_hint('HTTP 503 {"error":{"message":"Service temporarily overloaded"}}')
    assert "키 값" in _ai_hint("HTTP 400 Please pass a valid API key")

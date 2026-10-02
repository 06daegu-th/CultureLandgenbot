"""v17: '.env 에 DART/FRED 키를 넣었는데 화면엔 키 없음' — 재현 + 수정 검증.

원인 5가지를 각각 재현한다: 시작 후에 키를 넣음 · 스케줄러가 시작 때만 작업 등록 · .env 형식(BOM/export/중복 빈 줄/다른 이름) ·
이미 들어온 빈 환경변수 · 틀린 키인데 '아직 없음'만 보임."""

import os
from dataclasses import replace

import pytest

from quant_ai import ops

DART = "a" * 40
FRED = "b" * 32


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from quant_ai import keys
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    for k in ("DART_API_KEY", "FRED_API_KEY", "ECOS_API_KEY", "OPENDART_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("# 비어 있는 .env\n", encoding="utf-8")
    monkeypatch.setenv("QUANT_ENV_FILE", str(env))
    keys._STATE.update(mtime=None, path=None, checked=0.0)
    keys.LOADED_FROM_DOTENV.clear()
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{tmp_path}/t.db", artifacts_dir=tmp_path / "a"))
    a._env = env
    yield a
    for k in ("DART_API_KEY", "FRED_API_KEY", "ECOS_API_KEY"):
        os.environ.pop(k, None)


def _write(app, text):
    from quant_ai import keys
    app._env.write_text(text, encoding="utf-8")
    keys._STATE["checked"] = 0.0
    st = os.stat(app._env)
    os.utime(app._env, (st.st_atime, st.st_mtime + 1))  # 같은 초 안에 고쳐도 '바뀜'으로 인식되게


def test_key_added_while_running_is_picked_up_without_restart(app):
    from quant_ai.actions import setup_status
    steps = {s["key"]: s for s in setup_status(app)["steps"]}
    assert steps["disclosures"]["detail"].startswith("키 없음") and "줄이 없음" in steps["disclosures"]["detail"]
    _write(app, f"DART_API_KEY={DART}\nFRED_API_KEY={FRED}\n")  # 서버 실행 중에 .env 수정
    steps = {s["key"]: s for s in setup_status(app)["steps"]}
    assert app.settings.dart_api_key == DART and app.settings.fred_api_key == FRED
    assert "키 확인됨" in steps["disclosures"]["detail"] and steps["disclosures"]["action"] == "warmup"
    assert ops.get_state(app.engine, "keys_reload")["changed"] == ["DART_API_KEY", "FRED_API_KEY"]
    _write(app, "DART_API_KEY=\n")  # 지우면 다시 없음
    from quant_ai.keys import refresh
    assert refresh(app)["changed"] == ["DART_API_KEY", "FRED_API_KEY"] and app.settings.dart_api_key is None


def test_env_format_quirks(app):
    from quant_ai.keys import diagnose, parse, refresh
    text = f"﻿export DART_API_KEY = \"{DART}\"  \r\nFRED_KEY={FRED}  # 다른 이름\r\nDART_API_KEY=\r\nECOS_API_KEY\n"
    vals, lines, issues = parse(text)
    assert vals["DART_API_KEY"] == DART and lines["DART_API_KEY"] == [1, 3] and any("BOM" in x for x in issues)
    assert any("'=' 가 없음" in x for x in issues)
    _write(app, text)
    refresh(app)
    assert app.settings.dart_api_key == DART and app.settings.fred_api_key == FRED  # FRED_KEY 별칭도 인식
    d = {k["key"]: k for k in diagnose(app)["keys"]}
    assert d["DART_API_KEY"]["status"] == "warn" and any("도커" in t for t in d["DART_API_KEY"]["tips"])  # 마지막 줄이 빈 값
    assert any("FRED_KEY" in t for t in d["FRED_API_KEY"]["tips"])
    assert d["DART_API_KEY"]["masked"].endswith(DART[-4:]) and DART not in str(diagnose(app))  # 값 전체는 절대 노출 안 함
    _write(app, "DART_API_KEY=abc123\n")
    refresh(app)
    d = {k["key"]: k for k in diagnose(app)["keys"]}
    assert d["DART_API_KEY"]["format_ok"] is False and any("길이 6자" in t for t in d["DART_API_KEY"]["tips"])


def test_empty_env_var_does_not_block_dotenv(tmp_path, monkeypatch):
    """도커·셸에서 DART_API_KEY= (빈 값)이 이미 들어와 있어도 .env 값을 쓴다 (예전엔 setdefault 라 빈 값이 이김)."""
    from quant_ai.cli import load_dotenv
    f = tmp_path / ".env"
    f.write_text(f"DART_API_KEY={DART}\nOPENDART_API_KEY=\nFRED_API_KEY={FRED}\n", encoding="utf-8")
    monkeypatch.setenv("DART_API_KEY", "")
    monkeypatch.setenv("FRED_API_KEY", "shell-value")
    load_dotenv(str(f))
    assert os.environ["DART_API_KEY"] == DART and os.environ["FRED_API_KEY"] == "shell-value"  # 셸에 값이 있으면 그것이 우선


def test_wrong_key_shows_reason_not_just_empty(app):
    from quant_ai import actions
    from quant_ai.actions import setup_status
    from quant_ai.keys import probe
    _write(app, f"DART_API_KEY={DART}\nFRED_API_KEY={FRED}\n")

    def fake(url, params):
        if "opendart" in url:
            return {"status": "010", "message": "등록되지 않은 키입니다."}
        raise RuntimeError('HTTP 400: {"error_code":400,"error_message":"Bad Request.  The value for variable api_key is not registered."}')
    r = probe(app, fetch_json=fake)["results"]
    assert not r["dart"]["ok"] and "등록되지 않은 키" in r["dart"]["message"]
    assert not r["fred"]["ok"] and "FRED: 키가 유효하지 않음" in r["fred"]["message"]
    assert r["ecos"]["status"] == "missing"
    steps = {s["key"]: s for s in setup_status(app)["steps"]}
    assert "마지막 수집 실패" in steps["disclosures"]["detail"] and "opendart" in steps["disclosures"]["detail"]
    ok = probe(app, "dart", fetch_json=lambda u, p: {"status": "013", "message": "조회된 데이터가 없습니다."})["results"]["dart"]
    assert ok["ok"] and ops.get_state(app.engine, "source_errors").get("dart") is None  # 성공하면 오류 기록 지움
    assert actions  # import 확인


def test_http_4xx_not_retried_and_key_masked(monkeypatch):
    import urllib.error

    from quant_ai.data.collectors import http
    calls = []

    def boom(req, timeout):
        calls.append(1)
        import io
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, io.BytesIO(b'{"error_message":"api_key not registered"}'))
    monkeypatch.setattr(http.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as e:
        http.get("https://api.stlouisfed.org/fred/series", {"api_key": "SECRET123"}, retries=3)
    assert len(calls) == 1 and "not registered" in str(e.value)

    def down(req, timeout):
        raise OSError("Name or service not known")
    monkeypatch.setattr(http.urllib.request, "urlopen", down)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError) as e:
        http.get("https://opendart.fss.or.kr/api/list.json", {"crtfc_key": "SECRET123"}, retries=2)
    assert "SECRET123" not in str(e.value) and "crtfc_key=***" in str(e.value)


def test_fred_one_bad_series_does_not_stop_others(app):
    from quant_ai.data.collectors.macro import FredCollector
    from quant_ai.data.db import session_scope

    def fetch(url, params):
        if params["series_id"] == "BAD":
            raise RuntimeError("HTTP 400: series does not exist")
        return {"observations": [{"date": "2026-09-01", "value": "4.1"}]}
    fc = FredCollector(FRED, series=("DGS10", "BAD", "DGS2"), fetch_json=fetch)
    from datetime import date
    with session_scope(app.engine) as s:
        assert fc.collect(s, date(2026, 1, 1)) == 2
    assert list(fc.errors) == ["BAD"]


def test_scheduler_registers_data_jobs_even_without_keys(app):
    from quant_ai.config import Mode
    from quant_ai.scheduler import build_default_scheduler
    sch = build_default_scheduler(app, Mode.PAPER)
    names = {j.name for j in sch.jobs}
    assert {"disclosures", "dart_summary", "macro", "keys_reload"} <= names
    dart = next(j for j in sch.jobs if j.name == "disclosures")
    assert dart.fn(None) is None  # 키 없으면 조용히 건너뜀 (실패 아님)

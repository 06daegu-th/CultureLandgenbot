""".env 키 — 서버를 다시 시작하지 않아도 반영 · 왜 안 되는지 진단 · 실제로 되는지 시험.

문제였던 것 (".env 에 DART/FRED 키를 넣었는데 '키 없음'"):
  1. 설정은 서버가 시작될 때 한 번만 읽었다 → 실행 중에 .env 를 고치면 다시 시작하기 전까지 반영 안 됨
  2. 스케줄러는 시작할 때 키가 있어야 공시·거시 수집 작업을 등록했다 → 나중에 키를 넣어도 자동 수집이 영영 안 켜짐
  3. `.env` 형식: `export KEY=…` · 맨 앞 BOM(메모장 UTF-8) · `KEY = 값` · 같은 키가 두 줄(위에 값, 아래 예시의 빈 줄 —
     docker-compose 는 '마지막 줄'을 써서 빈 값이 됨) · 다른 이름(OPENDART_API_KEY 등)
  4. 셸/도커에서 빈 값(`DART_API_KEY=`)이 이미 환경변수로 들어오면 .env 값을 덮어쓰지 않았다
  5. 키가 틀려도(DART '등록되지 않은 키', FRED 400) 화면에는 '아직 없음'만 — 이유가 안 보였다

refresh()  .env 가 바뀌었으면(수정 시각) 다시 읽어 app.settings 의 데이터 키를 갱신 (값은 로그·화면에 절대 표시하지 않음)
diagnose() 키마다: 불러옴 여부 · .env 의 몇 번째 줄 · 형식(길이) · 중복 줄 · 비슷한 이름 · 마지막 수집 오류 → 한국어 조치
probe()    DART·FRED·ECOS 에 가장 작은 요청 1번 → 키가 실제로 유효한지 (등록 안 된 키 · 한도 초과 · 네트워크 차단 구분)
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from . import ops

# env 이름 → (Settings 필드, 이름표, 형식 정규식, 형식 설명, 다른 흔한 이름)
DATA_KEYS = {
    "DART_API_KEY": ("dart_api_key", "공시 (DART)", r"^[0-9a-fA-F]{40}$", "40자리 영문·숫자(16진수)",
                     ("OPENDART_API_KEY", "OPEN_DART_API_KEY", "OPENDART_KEY", "DART_KEY", "DART_API")),
    "FRED_API_KEY": ("fred_api_key", "거시·해외지수 (FRED)", r"^[0-9a-z]{32}$", "32자리 소문자·숫자",
                     ("FRED_KEY", "FRED_API", "FRED_TOKEN")),
    "ECOS_API_KEY": ("ecos_api_key", "한국은행 (ECOS)", r"^[0-9A-Za-z]{16,40}$", "20자 안팎 영문·숫자",
                     ("BOK_API_KEY", "ECOS_KEY", "BOK_ECOS_API_KEY")),
}
_STATE: dict = {"mtime": None, "path": None, "checked": 0.0}
_LOCK = threading.Lock()
LOADED_FROM_DOTENV: set[str] = set()  # cli.load_dotenv 가 채움 — 셸에서 직접 넣은 값은 .env 로 덮어쓰지 않기 위해


# ------------------------------------------------------------------ .env 찾기 · 읽기
def env_file() -> Path | None:
    cands = []
    if os.environ.get("QUANT_ENV_FILE"):
        cands.append(Path(os.environ["QUANT_ENV_FILE"]).expanduser())
    cands.append(Path.cwd() / ".env")
    root = Path(__file__).resolve().parents[2]  # src/quant_ai → 프로젝트 폴더 (설치형이 아닐 때)
    cands.append(root / ".env")
    for p in cands:
        if p.is_file():
            return p
    return None


def parse(text: str) -> tuple[dict[str, str], dict[str, list[int]], list[str]]:
    """(값, 키 → 줄 번호들, 형식 문제). 같은 키가 여러 줄이면 값이 있는 마지막 줄을 쓴다."""
    vals: dict[str, str] = {}
    lines: dict[str, list[int]] = {}
    issues: list[str] = []
    if text.startswith("﻿"):
        text = text[1:]
        issues.append("파일 맨 앞 BOM(메모장 UTF-8 저장 흔적) — 처리함")
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", line):
                issues.append(f"{i}번째 줄 '{line}' 에 '=' 가 없음 (KEY=값 형식이어야 함)")
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if v[:1] in ('"', "'"):
            q = v[0]
            v = v[1:].split(q, 1)[0]
        else:
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        lines.setdefault(k, []).append(i)
        if v:
            vals[k] = v
    return vals, lines, issues


def load_into_environ(path: Path | None = None) -> set[str]:
    """시작할 때: .env 값을 환경변수로. 이미 값이 있으면 유지하되, '빈 값'은 .env 값으로 채운다."""
    p = path or env_file()
    if p is None:
        return set()
    vals, _, _ = parse(p.read_text(encoding="utf-8", errors="replace"))
    done = set()
    for k, v in vals.items():
        if not os.environ.get(k):
            os.environ[k] = v
            done.add(k)
    LOADED_FROM_DOTENV.update(done)
    for name, (_, _, _, _, aliases) in DATA_KEYS.items():  # 다른 이름으로 넣은 키도 받아 준다
        if not os.environ.get(name):
            alias = next((a for a in aliases if vals.get(a) or os.environ.get(a)), None)
            if alias:
                os.environ[name] = vals.get(alias) or os.environ[alias]
                LOADED_FROM_DOTENV.add(name)
    _STATE.update(path=str(p), mtime=p.stat().st_mtime)
    return done


def _line_value(text: str, lineno: int) -> str:
    raw = text.lstrip("\ufeff").splitlines()[lineno - 1]
    vals, _, _ = parse(raw)
    return next(iter(vals.values()), "")


# ------------------------------------------------------------------ 실행 중 반영
def refresh(app, force: bool = False, min_interval: float = 5.0) -> dict:
    """.env 가 바뀌었으면 데이터 키를 다시 읽어 app.settings 에 반영. 바뀐 키 이름 목록을 돌려준다 (값은 안 돌려줌)."""
    now = time.monotonic()
    if not force and now - _STATE["checked"] < min_interval:
        return {"changed": [], "skipped": True}
    with _LOCK:
        _STATE["checked"] = now
        p = env_file()
        if p is None:
            return {"changed": [], "env_file": None}
        mtime = p.stat().st_mtime
        if not force and _STATE.get("path") == str(p) and _STATE.get("mtime") == mtime:
            return {"changed": [], "env_file": str(p)}
        vals, _, _ = parse(p.read_text(encoding="utf-8", errors="replace"))
        changes, names = {}, []
        for name, (fld, _, _, _, aliases) in DATA_KEYS.items():
            new = vals.get(name) or next((vals[a] for a in aliases if vals.get(a)), None)
            cur = getattr(app.settings, fld, None)
            shell = os.environ.get(name) if name not in LOADED_FROM_DOTENV else None
            if shell and shell == cur:
                continue  # 셸·도커에서 직접 넣은 값이 우선
            if (new or None) != (cur or None):
                changes[fld] = new or None
                names.append(name)
                if new:
                    os.environ[name] = new
                    LOADED_FROM_DOTENV.add(name)
                else:
                    os.environ.pop(name, None)
        if changes:
            app.settings = replace(app.settings, **changes)
            ops.set_state(app.engine, "keys_reload", {"at": datetime.now(UTC).isoformat(), "changed": names})
        _STATE.update(path=str(p), mtime=mtime)
        return {"changed": names, "env_file": str(p)}


# ------------------------------------------------------------------ 진단
def note_error(engine, source: str, error: str | None) -> None:
    """수집 결과를 기록 (오류면 이유, 성공이면 지움) — 화면이 '아직 없음' 대신 이유를 보여준다."""
    st = ops.get_state(engine, "source_errors")
    if error:
        st[source] = {"at": datetime.now(UTC).isoformat(), "error": str(error)[:300]}
    else:
        st.pop(source, None)
    ops.set_state(engine, "source_errors", st)


def explain_error(source: str, err: str) -> str:
    e = err or ""
    if "010" in e or "등록되지 않은" in e:
        return "DART: 등록되지 않은 키 — opendart.fss.or.kr 에서 발급받은 인증키를 그대로 복사했는지 확인"
    if "011" in e or "사용할 수 없는" in e:
        return "DART: 사용할 수 없는 키 (정지·만료) — 재발급 필요"
    if "020" in e or "요청 제한" in e:
        return "DART: 일일 요청 한도 초과 — 내일 자동으로 다시 시도"
    if "api_key" in e.lower() and ("not" in e.lower() or "invalid" in e.lower() or "registered" in e.lower()):
        return "FRED: 키가 유효하지 않음 — fredaccount.stlouisfed.org 의 API Keys 에서 32자리 키 확인"
    if "400" in e and source == "fred":
        return "FRED: 요청 거부(400) — 대부분 키 오류"
    if any(x in e for x in ("timed out", "Temporary failure", "Name or service", "urlopen error", "Connection", "403", "407")):
        return "네트워크 연결 실패 — 인터넷·방화벽·프록시 확인 (키 문제 아님)"
    return e[:160]


def diagnose(app) -> dict:
    p = env_file()
    text = p.read_text(encoding="utf-8", errors="replace") if p else ""
    vals, lines, issues = parse(text) if p else ({}, {}, [])
    errs = ops.get_state(app.engine, "source_errors")
    out = []
    for name, (fld, title, pat, fmt, aliases) in DATA_KEYS.items():
        loaded = getattr(app.settings, fld, None)
        in_file = vals.get(name)
        alias = next((a for a in aliases if vals.get(a)), None)
        ln = lines.get(name) or []
        tips, status = [], "ok"
        val = loaded or in_file or (vals.get(alias) if alias else None)
        fmt_ok = bool(re.match(pat, val)) if val else None
        if not val:
            status = "missing"
            if ln:
                tips.append(f".env {', '.join(map(str, ln))}번째 줄에 {name}= 이 있지만 값이 비어 있음")
            else:
                tips.append(f".env 에 {name}= 줄이 없음" + (f" ({p})" if p else " — .env 파일도 찾지 못함 (프로젝트 폴더에 .env 필요)"))
        else:
            if not loaded:
                status = "warn"
                tips.append("값은 .env 에 있는데 서버에 아직 반영 안 됨 → '키 다시 읽기'")
            if alias and not in_file:
                tips.append(f"'{alias}' 라는 이름으로 들어 있음 — 자동으로 {name} 로 인식함 (가능하면 이름을 {name} 로)")
            if len(ln) > 1:
                last_empty = not _line_value(text, ln[-1])
                tips.append(f"{name} 줄이 {len(ln)}개 ({', '.join(map(str, ln))}번째) — 하나만 남기세요"
                            + (" · 마지막 줄이 비어 있어 도커(docker-compose)에서는 '키 없음'이 됨" if last_empty else ""))
                if last_empty:
                    status = "warn"
            if fmt_ok is False:
                status = "warn"
                tips.append(f"형식이 예상과 다름: 길이 {len(val)}자 · {fmt} 이어야 함 (앞뒤 공백·따옴표·다른 키를 붙여넣었는지)")
        src = {"DART_API_KEY": "dart", "FRED_API_KEY": "fred", "ECOS_API_KEY": "ecos"}[name]
        err = errs.get(src)
        if err and val:
            status = "bad"
            tips.append("마지막 수집 실패: " + explain_error(src, err["error"]))
        out.append({"key": name, "title": title, "status": status, "loaded": bool(loaded), "in_env_file": bool(in_file or alias),
                    "lines": ln, "length": len(val) if val else 0, "format_ok": fmt_ok, "tips": tips,
                    "last_error": err, "masked": ("•" * 6 + val[-4:]) if val and len(val) > 8 else None})
    return {"env_file": str(p) if p else None, "issues": issues, "keys": out,
            "reload": ops.get_state(app.engine, "keys_reload"),
            "note": "키 값은 화면·로그에 표시하지 않습니다 (끝 4자리만) · .env 를 고치면 5초 안에 자동 반영 (서버 재시작 불필요)"}


# ------------------------------------------------------------------ 실제 시험
def probe(app, which: str | None = None, fetch_json=None) -> dict:
    """가장 작은 요청 1번으로 키가 실제로 되는지."""
    from datetime import date, timedelta

    from .data.collectors import http
    fetch_json = fetch_json or (lambda url, params: http.get_json(url, params, timeout=8, retries=1))
    refresh(app)
    st = app.settings
    res = {}
    targets = [which] if which else ["dart", "fred", "ecos"]
    for t in targets:
        t0 = time.monotonic()
        try:
            if t == "dart":
                if not st.dart_api_key:
                    res[t] = {"ok": False, "status": "missing", "message": "키 없음"}
                    continue
                d = date.today()
                p = fetch_json("https://opendart.fss.or.kr/api/list.json", {"crtfc_key": st.dart_api_key, "bgn_de": (d - timedelta(days=3)).strftime("%Y%m%d"),
                                                                           "end_de": d.strftime("%Y%m%d"), "page_count": 1})
                code = p.get("status")
                ok = code in ("000", "013")
                res[t] = {"ok": ok, "status": code, "message": "정상" if ok else explain_error("dart", f"{code} {p.get('message')}")}
            elif t == "fred":
                if not st.fred_api_key:
                    res[t] = {"ok": False, "status": "missing", "message": "키 없음"}
                    continue
                p = fetch_json("https://api.stlouisfed.org/fred/series", {"series_id": "DGS10", "api_key": st.fred_api_key, "file_type": "json"})
                ok = bool(p.get("seriess"))
                res[t] = {"ok": ok, "status": "200" if ok else "?", "message": "정상" if ok else explain_error("fred", str(p.get("error_message")))}
            elif t == "ecos":
                if not st.ecos_api_key:
                    res[t] = {"ok": False, "status": "missing", "message": "키 없음 (선택)"}
                    continue
                p = fetch_json(f"https://ecos.bok.or.kr/api/KeyStatisticList/{st.ecos_api_key}/json/kr/1/1", {})
                ok = "KeyStatisticList" in p
                res[t] = {"ok": ok, "status": "200" if ok else (p.get("RESULT") or {}).get("CODE"),
                          "message": "정상" if ok else str((p.get("RESULT") or {}).get("MESSAGE") or "응답 이상")}
            else:
                raise ValueError("dart / fred / ecos")
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}" + (f" ← {e.__cause__}" if e.__cause__ else "")
            res[t] = {"ok": False, "status": "error", "message": explain_error(t, msg)}
        res[t]["ms"] = int((time.monotonic() - t0) * 1000)
        note_error(app.engine, t, None if res[t]["ok"] or res[t]["status"] == "missing" else res[t]["message"])
    return {"at": datetime.now(UTC).isoformat(), "results": res}


__all__ = ["env_file", "parse", "load_into_environ", "refresh", "diagnose", "probe", "note_error", "explain_error", "DATA_KEYS"]

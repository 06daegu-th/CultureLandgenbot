"""사용자 PC 점검 보고서 — `./run.sh report` (= `qa report`).

목적: 이 프로그램을 만든 쪽이 사용자의 PC 를 직접 볼 수 없으므로, 문제를 고치는 데 필요한 사실만 모아
'그대로 복사해서 보내도 되는' 글 하나로 만든다.

절대 넣지 않는 것: 키·비밀번호·토큰 값, 계좌번호, 금액. 키는 '있음/없음'만.
마지막에 한 번 더 걸러 낸다 — .env 에 들어 있는 모든 값(6자 이상)이 글 어디에든 나오면 *** 로 바꾸고,
키처럼 보이는 긴 문자열·계좌번호 모양·이메일·텔레그램 토큰 모양도 지운다.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, ops
from .asof import label

KEY_NAMES = ["GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "CEREBRAS_API_KEY", "NVIDIA_API_KEY",
             "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DART_API_KEY", "FRED_API_KEY", "ECOS_API_KEY",
             "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT", "KIS_ENV", "QUANT_TELEGRAM_TOKEN", "QUANT_TELEGRAM_CHAT_ID",
             "QUANT_DISCORD_WEBHOOK", "QUANT_WEB_PASSWORD_HASH", "QUANT_TOTP_SECRET", "QUANT_MARCAP_DIR", "DATABASE_URL"]
SAFE_VALUE = {"KIS_ENV"}  # 값 자체가 비밀이 아닌 것 (demo/real) — 그래도 정해진 값만 보여 준다

_PATTERNS = [
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{25,}\b"), "<텔레그램 토큰 모양 지움>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<이메일 지움>"),
    (re.compile(r"\b\d{8}-?\d{2}\b"), "<계좌번호 모양 지움>"),
    (re.compile(r"(?i)(key|token|secret|password|appkey|appsecret|crtfc_key|api_key)=([^&\s\"']+)"), r"\1=***"),
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "<긴 키 모양 지움>"),
    (re.compile(r"postgres(ql)?(\+\w+)?://[^\s]+"), "postgresql://<접속 정보 지움>"),
]


def _env_values() -> list[str]:
    vals = set()
    try:
        from .keys import env_file, parse
        p = env_file()
        if p:
            vals |= {v for v in parse(p.read_text(encoding="utf-8", errors="ignore"))[0].values() if v and len(v) >= 6}
    except Exception:  # noqa: BLE001, S110 - 보고서 만들기는 계속
        pass
    vals |= {os.environ[k] for k in KEY_NAMES if os.environ.get(k) and len(os.environ[k]) >= 6 and k not in SAFE_VALUE}
    return sorted(vals, key=len, reverse=True)


def scrub(text: str) -> str:
    """키·토큰·계좌번호·이메일·DB 접속 정보를 지운다 (보고서 전체에 마지막으로 한 번)."""
    for v in _env_values():
        text = text.replace(v, "***")
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


def _git() -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        r = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%h %cs"], capture_output=True, text=True, timeout=5)  # noqa: S603, S607
        return r.stdout.strip() or "git 기록 없음 (zip 으로 받은 경우)"
    except Exception:  # noqa: BLE001
        return "git 없음"


def build(app, net: bool = True, now: datetime | None = None) -> str:
    from sqlalchemy import func, select

    from .asof import freshness
    from .center import ops_status
    from .data.db import session_scope
    from .data.models import Disclosure, JobRun, NewsArticle, PortfolioSnapshot, PriceBar
    now = now or datetime.now(UTC)
    L: list[str] = []
    add = L.append

    def sec(title: str) -> None:
        add("")
        add(f"## {title}")

    def guard(title, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - 한 부분이 실패해도 나머지는 쓴다
            add(f"- ({title} 확인 실패: {type(e).__name__}: {str(e)[:160]})")

    add("# Quant AI 점검 보고서 (키·계좌번호·금액 없음 — 그대로 보내도 됩니다)")
    add(f"- 만든 시각: {label(now)}")
    add(f"- 버전: {__version__} · 코드: {_git()}")
    add(f"- 컴퓨터: {platform.system()} {platform.release()} · {platform.machine()} · Python {sys.version.split()[0]}")
    st = app.settings
    add(f"- 운영 모드: {st.mode.value} · 증권사: {st.broker}{' · KIS ' + str(os.environ.get('KIS_ENV') or 'demo') if st.broker == 'kis' else ''}"
        f" · DB: {'PostgreSQL' if st.database_url.startswith('postgres') else 'SQLite'}")

    sec("키 (값은 넣지 않음 — 있음/없음만)")

    def keys_part():
        from .keys import env_file, parse
        p = env_file()
        in_file = set(parse(p.read_text(encoding="utf-8", errors="ignore"))[0]) if p else set()
        add(f"- .env 파일: {'있음' if p else '없음 (프로젝트 폴더에 .env 필요)'}")
        have = [k for k in KEY_NAMES if os.environ.get(k) or k in in_file]
        miss = [k for k in KEY_NAMES if k not in have]
        add(f"- 있음: {', '.join(have) or '없음'}")
        add(f"- 없음: {', '.join(miss) or '없음'}")
        if os.environ.get("KIS_ENV") in ("demo", "real"):
            add(f"- KIS_ENV = {os.environ['KIS_ENV']}")
    guard("키", keys_part)

    sec("24시간 운영 · 데이터")

    def ops_part():
        o = ops_status(app, now)
        hb = o["heartbeat_age_s"]
        add(f"- 24시간 운영: {'켜짐' if o['running'] else '꺼짐'}" + (f" (마지막 신호 {hb / 60:.0f}분 전)" if hb is not None else " (신호 기록 없음)"))
        add(f"- 주가 데이터: {o['bar'].get('label') or '없음'}" + (f" · {o['bar']['lag_days']}거래일 밀림" if o["bar"].get("lag_days") else ""))
        add(f"- 최근 24시간 뉴스 {o['news_24h']}건 · 작업 실패 {o['job_failures_24h']}건 · 업종 분류 {'-' if o.get('sector_coverage') is None else format(o['sector_coverage'], '.0%')}")
        for it in o["issues"]:
            add(f"  · 문제: {it['text']} → {it['fix']}")
        fr = freshness(app, now)
        for k, v in (fr.get("items") or {}).items():
            add(f"  · {v.get('name', k)}: {v.get('label') or '없음'} ({v.get('age') or '-'})")
    guard("운영", ops_part)

    sec("DB 크기")

    def db_part():
        with session_scope(app.engine) as s:
            n = lambda m: s.scalar(select(func.count()).select_from(m)) or 0  # noqa: E731
            add(f"- 일봉 {n(PriceBar):,}줄 · 종목 {s.scalar(select(func.count(func.distinct(PriceBar.symbol)))) or 0}개 · 뉴스 {n(NewsArticle):,} · 공시 {n(Disclosure):,} · 장부 기록 {n(PortfolioSnapshot):,}")
    guard("DB", db_part)

    sec("최근 실패한 작업 (최대 10개)")

    def jobs_part():
        with session_scope(app.engine) as s:
            rows = s.scalars(select(JobRun).where(JobRun.ok.is_(False)).order_by(JobRun.started_at.desc()).limit(10)).all()
            if not rows:
                add("- 없음")
            for r in rows:
                add(f"- {label(r.started_at)} · {r.job}: {str(r.error or '')[:200]}")
    guard("작업", jobs_part)

    sec("실전 검증 진행 (KIS 모의 · 실시간 · 체결)")

    def val_part():
        from .validation import progress
        for it in progress(app, now).get("items", []):
            add(f"- {it['title']}: {round((it.get('progress') or 0) * 100)}% · {it.get('detail', '')}")
            if it.get("next"):
                add(f"  · 다음: {it['next']}")
    guard("검증", val_part)

    sec("외부 연결 (뉴스·공시·시세 사이트에 닿는지)")

    def net_part():
        from . import netcheck
        r = netcheck.run(app) if net else netcheck.last(app)
        if not r:
            add("- 기록 없음 (`qa report` 를 --no-net 없이 실행하면 시험합니다)")
            return
        add(f"- 요약: {r.get('headline')} ({r.get('ok')}/{r.get('total')} 정상)")
        for x in r.get("rows", []):
            add(f"  · {x['title']}: {x['status']} · {x.get('detail', '')} ({x.get('ms') or 0}ms)")
    guard("외부 연결", net_part)

    sec("종목 로고")

    def logo_part():
        import json as _json
        d = Path(app.settings.artifacts_dir) / "logos"
        if not d.exists():
            add("- 아직 받은 로고 없음 (`./run.sh logos` 로 미리 받기)")
            return
        got, failed, why = {}, 0, {}
        for f in d.glob("*.json"):
            try:
                m = _json.loads(f.read_text())
            except Exception:  # noqa: BLE001, S112 - 깨진 기록은 건너뜀
                continue
            if m.get("file"):
                got[m.get("source", "?")] = got.get(m.get("source", "?"), 0) + 1
            elif m.get("failed_at"):
                failed += 1
                for t in m.get("tried") or []:
                    why[t] = why.get(t, 0) + 1
        add(f"- 받은 로고: {sum(got.values())}개 ({', '.join(f'{k} {v}' for k, v in got.items()) or '-'}) · 실패 {failed}개")
        for t, n_ in sorted(why.items(), key=lambda x: -x[1])[:6]:
            add(f"  · 실패 이유: {t} ({n_}개)")
    guard("로고", logo_part)

    sec("목표 · 월 적립 (금액은 넣지 않음)")

    def goal_part():
        g = ops.get_state(app.engine, "goal_plan")
        if not g:
            add("- 목표 저장 안 됨")
            return
        d = g.get("dca") or {}
        add(f"- 목표 저장됨 · 기간 {g.get('target_years')}년 · 방식 {g.get('strategy')}")
        add(f"- 월 적립: {'켜짐' if d.get('on') else '꺼짐'} · 장부 {d.get('mode', '-')} · 대상 {d.get('target', 'core')} · 매달 {d.get('day', '-')}일 · 마지막 {d.get('last') or '-'}")
    guard("목표", goal_part)

    add("")
    add("— 이 보고서를 대화창에 붙여 넣어 주세요. 키 값은 들어 있지 않습니다 (혹시 남았을까 봐 마지막에 한 번 더 지웠습니다).")
    return scrub("\n".join(L))


def write(app, out: Path | None = None, net: bool = True) -> tuple[Path, str]:
    text = build(app, net=net)
    out = out or Path("data") / f"report-{datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out, text


__all__ = ["build", "write", "scrub"]

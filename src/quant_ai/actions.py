"""운영 동작 — 준비 상태 점검 · 워밍업(첫 데이터 채우기) · 서버 상태 · DB 정리 · 백그라운드 실행.

대시보드 버튼과 채팅 AI 가 같은 함수를 쓴다. 파괴적 동작(DB 정리)은 미리보기(dry_run)가 기본이며,
실제 삭제는 사람이 버튼을 눌러야 한다 (채팅 AI 는 제안만 한다).
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import sys
import threading
import time
import traceback
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select, text

from . import ops
from .data.db import session_scope
from .data.models import (
    ConsensusRecord,
    Instrument,
    JobRun,
    LLMCall,
    NewsArticle,
    PortfolioSnapshot,
    PriceBar,
    QuoteSnapshot,
    Tick,
)

log = logging.getLogger(__name__)
STARTED_AT = datetime.now(UTC)


# ====================================================================== 준비 상태
def setup_status(app) -> dict:
    """'데이터 없음' 대신: 무엇이 준비됐고 무엇이 비었는지, 어떻게 채우는지."""
    from .analytics import data_confidence
    st = app.settings
    conf = data_confidence(app)
    items = {i["key"]: i for i in conf["items"]}
    with session_scope(app.engine) as s:
        n_bars = s.scalar(select(func.count()).select_from(PriceBar)) or 0
        n_cons = s.scalar(select(func.count()).select_from(ConsensusRecord)) or 0
        n_scored = s.scalar(select(func.count()).select_from(ConsensusRecord).where(ConsensusRecord.correct.is_not(None))) or 0
        n_snap = s.scalar(select(func.count()).select_from(PortfolioSnapshot)) or 0
        last_job = s.scalar(select(func.max(JobRun.started_at)))
    from .analysts.analysts import assign_roles
    roles = assign_roles(st)
    prog = ops.get_state(app.engine, "ai_progress:KR")
    steps = [
        {"key": "prices", "label": "국내 주가 데이터", "done": n_bars > 0,
         "detail": f"일봉 {n_bars:,}개 · 마지막 {items['prices']['last'] or '-'}" if n_bars else "없음",
         "fix": "터미널에서 ./run.sh data (약 1~3분)"},
        {"key": "ai_keys", "label": "AI 키", "done": st.has_llm,
         "detail": ", ".join(f"{r}={p}" for r, p in roles.items()) if roles else "없음 → 휴리스틱 AI 로 동작",
         "fix": ".env 에 GEMINI_API_KEY 등 (docs/ENV_KEYS.md)"},
        {"key": "news", "label": "뉴스", "done": items["news"]["status"] == "ok",
         "detail": f"{conf['news_count']}건 · 마지막 {items['news']['last'] or '-'}", "fix": "아래 '지금 채우기' 또는 장중 5분마다 자동",
         "action": "warmup"},
        {"key": "decisions", "label": "AI 판단 기록", "done": n_cons > 0,
         "detail": f"{n_cons}건 (채점 {n_scored}건)" + (
             f" · 지금 판단 중 {prog.get('done', 0)}/{prog.get('total', 0)}" if prog.get("running") else ""), "fix": "'지금 채우기' 를 누르면 코어 후보를 AI 가 한 번 판단 (무료 한도 내)",
         "action": "warmup"},
        {"key": "book", "label": "운용 장부 (가상/모의)", "done": n_snap > 0, "detail": f"스냅샷 {n_snap}개",
         "fix": "./run.sh 로 사이클 1회 (리밸런싱 날이 아니면 주문 없음)"},
        {"key": "disclosures", "label": "공시 (DART)", "done": items["disclosures"]["status"] == "ok",
         "detail": items["disclosures"]["last"] or ("키 없음" if not st.dart_api_key else "아직 없음"),
         "fix": "DART_API_KEY (무료) 후 '지금 채우기'", "optional": True, "action": "warmup" if st.dart_api_key else None},
        {"key": "macro", "label": "거시·해외지수 (FRED)", "done": items["macro"]["status"] == "ok",
         "detail": items["macro"]["last"] or ("키 없음" if not st.fred_api_key else "아직 없음"),
         "fix": "FRED_API_KEY (무료) 후 '지금 채우기'", "optional": True, "action": "warmup" if st.fred_api_key else None},
        {"key": "scheduler", "label": "24시간 스케줄러", "done": bool(last_job and last_job.replace(tzinfo=last_job.tzinfo or UTC)
                                                                    > datetime.now(UTC) - timedelta(hours=1)),
         "detail": f"마지막 작업 {str(last_job)[:16]}" if last_job else "실행 기록 없음", "fix": "./run.sh (자동 모드) 가 함께 실행"},
    ]
    need = [x for x in steps if not x["done"] and not x.get("optional")]
    return {"ready": not need, "steps": steps, "confidence": conf["score"], "confidence_label": conf["label"],
            "action": get_action("warmup")}


# ====================================================================== 워밍업
def warmup(app, progress=None) -> dict:
    """비어 있는 화면을 채운다: 뉴스·공시·거시 수집 → 이벤트 반응 DB → AI 판단(코어 후보, 오늘 안 했으면) → 자동 감시."""
    say = progress or (lambda m: None)
    st = app.settings
    out: dict = {}
    if st.news_feeds:
        say("뉴스 수집 중…")
        try:
            from .data.collectors.news import NewsCollector
            with session_scope(app.engine) as s:
                out["news"] = NewsCollector(st.news_feeds).collect(s)
        except Exception as e:  # noqa: BLE001
            out["news_error"] = str(e)[:200]
    if st.dart_api_key:
        say("공시 수집 중…")
        try:
            from .data.collectors.disclosures import DartCollector
            with session_scope(app.engine) as s:
                out["disclosures"] = DartCollector(st.dart_api_key).collect(s, date.today() - timedelta(days=14), date.today())
        except Exception as e:  # noqa: BLE001
            out["disclosures_error"] = str(e)[:200]
    if st.fred_api_key:
        say("거시 지표 수집 중…")
        try:
            from .data.collectors.macro import FredCollector
            with session_scope(app.engine) as s:
                out["macro"] = FredCollector(st.fred_api_key).collect(s, date.today() - timedelta(days=400))
        except Exception as e:  # noqa: BLE001
            out["macro_error"] = str(e)[:200]
    with session_scope(app.engine) as s:
        has_bars = s.scalar(select(PriceBar.id).limit(1)) is not None
    if has_bars:
        say("공시 반응 통계 계산 중…")
        try:
            from .analytics import event_reactions
            out["event_types"] = len(event_reactions(app, refresh=True)["types"])
        except Exception as e:  # noqa: BLE001
            out["event_error"] = str(e)[:200]
        say("AI 판단 중 (코어 후보 · 무료 한도 보호를 위해 하루 한 번)…")
        try:
            out["ai"] = ai_snapshot(app)
        except Exception as e:  # noqa: BLE001
            out["ai_error"] = str(e)[:300]
        say("자동 감시 점검 중…")
        try:
            out["guardian"] = app.guardian(act=False)["state"]
        except Exception as e:  # noqa: BLE001
            out["guardian_error"] = str(e)[:200]
    else:
        out["hint"] = "주가 데이터가 없습니다 — 터미널에서 ./run.sh data"
    return out


def ai_snapshot(app, k: int | None = None) -> dict:
    """주문 없이 코어 후보 + 보유 종목을 AI 가 판단 (새 일봉마다 한 번). 판단 저널·근거 추적·성적표가 채워진다."""
    from .strategy.core_satellite import CoreSatelliteConfig
    cfg = CoreSatelliteConfig()
    bars, _, _ = app.market_data()
    if not bars:
        return {"skipped": "주가 데이터 없음"}
    last_ts = max(b.index.max() for b in bars.values())
    key = "ai-snapshot"
    done = {ops.get_state(app.engine, k).get("bar") for k in (key, "ai-shadow:paper", "ai-shadow:shadow", "ai-shadow:live")}
    if str(last_ts) in done:
        return {"skipped": f"이미 판단함 ({pd_date(last_ts)} 일봉)"}
    from pathlib import Path as _P
    try:  # 백그라운드 채우기와 스케줄러가 동시에 같은 판단을 시작하지 않게 (무료 한도 2배 사용·DB 충돌 방지)
        with ops.trading_lock(app.engine, "ai-snapshot", _P(app.settings.artifacts_dir) / "locks"):
            return _ai_snapshot(app, bars, last_ts, cfg, k, key)
    except ops.LockBusy:
        return {"skipped": "다른 곳에서 이미 AI 판단 중"}


def _ai_snapshot(app, bars, last_ts, cfg, k, key) -> dict:
    from .strategy.core_satellite import core_scores
    if ops.get_state(app.engine, key).get("bar") == str(last_ts):
        return {"skipped": "방금 다른 곳에서 판단을 마침"}
    scores = core_scores(bars, app.universe_at(last_ts), cfg.factor_weights)
    held = set(app.load_portfolio(app.settings.mode.value if app.settings.mode.value in ("paper", "shadow", "live")
                                  else "paper").positions)
    watched = [s for s in watch_symbols(app) if s in bars]  # 사용자가 본·물어본 종목도 매일 판단
    syms = list(dict.fromkeys([*scores.index[:k or cfg.shortlist_k], *[s for s in held if s in bars], *watched]))
    decisions = app.decide(symbols=syms, scenarios=False)
    ops.set_state(app.engine, key, {"bar": str(last_ts), "at": datetime.now(UTC).isoformat(), "n": len(decisions)})
    acts: dict[str, int] = {}
    for d in decisions:
        acts[d.signal.action] = acts.get(d.signal.action, 0) + 1
    return {"analyzed": len(decisions), "actions": acts}


def pd_date(ts) -> str:
    return str(ts)[:10]


# ====================================================================== 서버 상태
def _db_size(app) -> int | None:
    url = str(app.engine.url)
    try:
        if url.startswith("sqlite"):
            p = app.engine.url.database
            return os.path.getsize(p) if p and os.path.exists(p) else None
        with app.engine.connect() as c:
            return int(c.execute(text("SELECT pg_database_size(current_database())")).scalar())
    except Exception:  # noqa: BLE001
        return None


def server_status(app) -> dict:
    from .analysts.guard import llm_usage_summary
    from .analytics import data_confidence
    now = datetime.now(UTC)
    t0 = time.monotonic()
    db_ok = True
    try:
        with app.engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    db_ms = round((time.monotonic() - t0) * 1000, 1)
    with session_scope(app.engine) as s:
        jobs = {}
        for r in s.scalars(select(JobRun).where(JobRun.started_at >= now - timedelta(days=2))
                           .order_by(JobRun.started_at.desc()).limit(400)):
            j = jobs.setdefault(r.job, {"job": r.job, "last": str(r.started_at)[:19], "ok": r.ok, "error": (r.error or "")[:160],
                                        "runs": 0, "fails": 0})
            j["runs"] += 1
            j["fails"] += int(r.ok is False)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        llm_today = s.execute(select(LLMCall.provider, LLMCall.status, func.count()).where(LLMCall.ts >= start)
                              .group_by(LLMCall.provider, LLMCall.status)).all()
    quota = {}
    for prov, status, n in llm_today:
        q = quota.setdefault(prov, {"provider": prov, "used": 0, "cached": 0, "errors": 0,
                                    "limit": app.settings.llm_providers.get(prov, {}).get("daily")})
        if status in ("ok", "error"):
            q["used"] += n
        if status == "cached":
            q["cached"] += n
        if status in ("error", "budget"):
            q["errors"] += n
    for prov, cfg in app.settings.llm_providers.items():
        quota.setdefault(prov, {"provider": prov, "used": 0, "cached": 0, "errors": 0, "limit": cfg.get("daily")})
    disk = shutil.disk_usage(Path(app.settings.artifacts_dir).resolve().anchor or "/")
    ks = ops.get_state(app.engine, "kill_switch")
    g = ops.get_state(app.engine, "guardian")
    last_job = max((j["last"] for j in jobs.values()), default=None)
    sched_alive = bool(last_job and datetime.fromisoformat(last_job).replace(tzinfo=UTC) > now - timedelta(minutes=20))
    return {
        "time": now.isoformat(), "uptime_min": round((now - STARTED_AT).total_seconds() / 60, 1),
        "version": _version(), "python": sys.version.split()[0], "os": f"{platform.system()} {platform.release()}",
        "mode": app.settings.mode.value, "broker": app.settings.broker, "kis_env": app.settings.kis_env,
        "core_only": app.settings.core_only,
        "db": {"ok": db_ok, "latency_ms": db_ms, "kind": app.engine.url.get_backend_name(), "size_bytes": _db_size(app)},
        "disk": {"free_gb": round(disk.free / 1e9, 1), "total_gb": round(disk.total / 1e9, 1)},
        "scheduler": {"alive": sched_alive, "last_job": last_job,
                      "failing": [j for j in jobs.values() if j["ok"] is False][:6], "jobs": sorted(jobs.values(), key=lambda x: x["job"])},
        "llm_quota": sorted(quota.values(), key=lambda x: x["provider"]), "llm_week": llm_usage_summary(app.engine),
        "kill_switch": ks, "guardian": {"state": g.get("state"), "checked_at": g.get("checked_at")},
        "data_confidence": data_confidence(app)["score"],
        "actions": {k: {kk: v.get(kk) for kk in ("running", "started_at", "finished_at", "error")} for k, v in _ACTIONS.items()},
    }


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("quant-ai")
    except Exception:  # noqa: BLE001
        return "dev"


# ====================================================================== DB 정리
# 감사 기록(주문·체결·판단·의견·복기·모델)은 절대 지우지 않는다. 지우는 것은 다시 만들 수 있거나 오래된 운영 로그뿐.
CLEAN_RULES = [
    ("llm_cache", "LLM 캐시 기록 (7일 지난 cached 로그)", LLMCall, lambda n: (LLMCall.status == "cached") & (LLMCall.ts < n - timedelta(days=7))),
    ("llm_old", "LLM 호출 로그 (180일 지난 것)", LLMCall, lambda n: LLMCall.ts < n - timedelta(days=180)),
    ("jobs", "작업 실행 기록 (30일 지난 것)", JobRun, lambda n: JobRun.started_at < n - timedelta(days=30)),
    ("news", "뉴스 (1년 지난 것)", NewsArticle, lambda n: NewsArticle.published_at < n - timedelta(days=365)),
    ("quotes", "호가 스냅샷 (30일 지난 것)", QuoteSnapshot, lambda n: QuoteSnapshot.ts < n - timedelta(days=30)),
    ("ticks", "체결 틱 (30일 지난 것)", Tick, lambda n: Tick.ts < n - timedelta(days=30)),
]


def db_maintenance(app, dry_run: bool = True) -> dict:
    """정리 미리보기(dry_run) 또는 실행. 가상 장부의 장중 스냅샷은 30일이 지나면 하루 마지막 것만 남긴다."""
    now = datetime.now(UTC)
    before = _db_size(app)
    rows = []
    with session_scope(app.engine) as s:
        for key, label, model, cond in CLEAN_RULES:
            n = s.scalar(select(func.count()).select_from(model).where(cond(now))) or 0
            if n and not dry_run:
                s.execute(delete(model).where(cond(now)))
            rows.append({"key": key, "label": label, "rows": int(n)})
        # 장부 스냅샷 압축: 30일 지난 날짜는 (장부, 날짜)별 마지막 한 개만
        old = s.execute(select(PortfolioSnapshot.id, PortfolioSnapshot.mode, PortfolioSnapshot.ts).where(
            PortfolioSnapshot.ts < now - timedelta(days=30)).order_by(PortfolioSnapshot.ts, PortfolioSnapshot.id)).all()
        keep: dict[tuple, int] = {}
        for sid, mode, ts in old:
            keep[(mode, str(ts)[:10])] = sid
        drop = [sid for sid, *_ in old if sid not in set(keep.values())]
        if drop and not dry_run:
            for i in range(0, len(drop), 500):
                s.execute(delete(PortfolioSnapshot).where(PortfolioSnapshot.id.in_(drop[i:i + 500])))
        rows.append({"key": "snapshots", "label": "장부 스냅샷 (30일 지난 날의 장중 기록 → 하루 1개)", "rows": len(drop)})
        counts = {m.__tablename__: int(s.scalar(select(func.count()).select_from(m)) or 0)
                  for m in (PriceBar, ConsensusRecord, LLMCall, NewsArticle, PortfolioSnapshot, JobRun, Instrument)}
    vacuum = None
    if not dry_run:
        vacuum = _vacuum(app)
        ops.set_state(app.engine, "db_maintenance", {"at": now.isoformat(), "rows": rows, "before": before,
                                                     "after": _db_size(app)})
    total = sum(r["rows"] for r in rows)
    return {"dry_run": dry_run, "rows": rows, "total": total, "size_before": before,
            "size_after": None if dry_run else _db_size(app), "vacuum": vacuum, "tables": counts,
            "kept": "주문·체결·AI 판단·의견·복기·모델 기록은 감사용으로 보존 (지우지 않음)",
            "last": ops.get_state(app.engine, "db_maintenance")}


def _vacuum(app) -> str:
    try:
        if app.engine.url.get_backend_name() == "sqlite":
            with app.engine.connect() as c:
                c.exec_driver_sql("VACUUM")
                c.exec_driver_sql("ANALYZE")
            return "VACUUM · ANALYZE 완료"
        with app.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.exec_driver_sql("VACUUM ANALYZE")
        return "VACUUM ANALYZE 완료"
    except Exception as e:  # noqa: BLE001
        return f"VACUUM 실패: {e}"[:200]


# ====================================================================== 백그라운드 실행
_ACTIONS: dict[str, dict] = {}
_LOCK = threading.Lock()
ACTION_LABELS = {"us_cycle": "미국 장부 갱신", "warmup": "데이터·AI 판단 채우기", "db_clean": "DB 정리", "guardian": "자동 감시 점검",
                 "ai_snapshot": "AI 판단 지금 실행", "event_reactions": "공시 반응 통계 갱신"}


def get_action(name: str) -> dict:
    a = _ACTIONS.get(name)
    label = ACTION_LABELS.get(name) or (f"{name.split(':', 1)[1]} AI 분석" if name.startswith("analyze:") else name)
    return {"name": name, "label": label, **({k: v for k, v in a.items() if k != "thread"} if a else {})}


WATCH_MAX = 20


def watch_symbols(app) -> list[str]:
    """별표 관심종목(사용자가 고정) + 최근 본 종목(최근 20개)."""
    star = ops.get_state(app.engine, "starred").get("symbols", [])
    return list(dict.fromkeys([*star, *ops.get_state(app.engine, "watch_symbols").get("symbols", [])]))


def add_watch(app, symbol: str) -> None:
    """사용자가 보거나 분석을 요청한 종목 → 매일 AI 판단·알림 대상 (최근 20개). 해외 티커는 미국 사이클이 판단."""
    syms = [x for x in ops.get_state(app.engine, "watch_symbols").get("symbols", []) if x != symbol] + [symbol]
    ops.set_state(app.engine, "watch_symbols", {"symbols": syms[-WATCH_MAX:]})


def analyze_symbol(app, symbol: str, say=None) -> dict:
    """한 종목을 지금 AI 들이 판단 (주문 없음). 국내 종목만 — 해외는 전략·합의 대상이 아니다."""
    import re as _re
    if not _re.fullmatch(r"\d{6}", symbol or ""):
        raise ValueError("국내 종목코드(6자리)만 AI 합의 분석을 합니다")
    add_watch(app, symbol)
    if say:
        say(f"{symbol} AI 판단 중…")
    ds = app.decide(symbols=[symbol], scenarios=True)
    if not ds:
        return {"symbol": symbol, "skipped": "주가 데이터가 없는 종목"}
    d = ds[0]
    return {"symbol": symbol, "action": d.signal.action, "prob_up": round(d.signal.prob_up, 4),
            "confidence": d.signal.confidence, "consensus_id": d.consensus_id}


EVENT_COOLDOWN = timedelta(hours=3)
EVENT_DAILY_CAP = 20


def event_reanalyze(app, now: datetime | None = None, max_symbols: int = 3) -> dict:
    """새 공시·중요 뉴스가 뜬 보유·관심·코어 국내 종목을 그 자리에서 다시 판단 (주문 없음).

    무료 한도 보호: 종목당 3시간에 한 번, 하루 20회, 한 번에 3종목. 같은 일봉의 판단이 여러 번이어도
    예측 성적표는 마지막 하나만 센다 (표본 부풀리기 없음)."""
    from sqlalchemy import func, select

    from .alerts import focus_symbols, push
    from .data.models import ConsensusRecord, Disclosure, NewsArticle
    now = now or datetime.now(UTC)
    cur = ops.get_state(app.engine, "event_cursor")
    with session_scope(app.engine) as s:
        max_n = s.scalar(select(func.max(NewsArticle.id))) or 0
        max_d = s.scalar(select(func.max(Disclosure.id))) or 0
        if not cur:  # 처음: 지금까지의 것은 건너뛴다
            ops.set_state(app.engine, "event_cursor", {"n": max_n, "d": max_d, "day": now.date().isoformat(), "count": 0})
            return {"reanalyzed": [], "first_run": True}
        focus = {k for k in focus_symbols(app) if k.isdigit()}
        triggers: dict[str, str] = {}
        for d in s.scalars(select(Disclosure).where(Disclosure.id > cur.get("d", 0)).order_by(Disclosure.id)):
            if d.symbol in focus:
                triggers.setdefault(d.symbol, f"공시: {d.title}"[:160])
        for n in s.scalars(select(NewsArticle).where(NewsArticle.id > cur.get("n", 0)).order_by(NewsArticle.id).limit(500)):
            if (n.importance or 0) >= 0.7 or n.events:
                for sym in n.symbols or []:
                    if sym in focus:
                        triggers.setdefault(sym, f"뉴스: {n.title}"[:160])
    last = ops.get_state(app.engine, "event_last")
    count = cur.get("count", 0) if cur.get("day") == now.date().isoformat() else 0
    todo = [sym for sym in triggers
            if not last.get(sym) or now - datetime.fromisoformat(last[sym]) >= EVENT_COOLDOWN][:max(0, min(max_symbols, EVENT_DAILY_CAP - count))]
    done = []
    if todo:
        decisions = app.decide(symbols=todo, scenarios=False)
        names = {}
        with session_scope(app.engine) as s:
            from .data.models import Instrument
            names = {i.symbol: i.name for i in s.scalars(select(Instrument).where(Instrument.symbol.in_(todo)))}
            for d in decisions:
                rec = s.get(ConsensusRecord, d.consensus_id) if d.consensus_id else None
                if rec is not None:
                    rec.payload = {**(rec.payload or {}), "trigger": triggers[d.symbol]}
        for d in decisions:
            last[d.symbol] = now.isoformat()
            done.append(d.symbol)
            push(app.engine, "event", f"이벤트 분석: {names.get(d.symbol, d.symbol)} → {d.signal.action}",
                 f"{triggers[d.symbol]} · 상승 확률 {d.signal.prob_up:.0%}", level="info", symbol=d.symbol,
                 link=f"#analysis/{d.symbol}", dedupe=f"evt:{d.consensus_id}", now=now)
        ops.set_state(app.engine, "event_last", last)
    ops.set_state(app.engine, "event_cursor", {"n": max_n, "d": max_d, "day": now.date().isoformat(),
                                               "count": count + len(done)})
    return {"reanalyzed": done, "triggers": len(triggers)}


def start_action(app, name: str, params: dict | None = None) -> dict:
    """버튼 → 백그라운드 스레드 (같은 동작은 동시에 하나만)."""
    params = params or {}
    if name == "analyze":
        sym = str(params.get("symbol", ""))[:12]
        key = f"analyze:{sym}"
        return _run(key, lambda say: analyze_symbol(app, sym, say))
    from . import desk
    from .analytics import event_reactions
    from .global_market import run_cycle as us_cycle
    from .global_market import sync as us_sync
    fns = {"us_cycle": lambda say: (say("미국 일봉 받는 중…"), {"sync": us_sync(app), "cycle": us_cycle(app)})[1],"warmup": lambda say: warmup(app, say), "db_clean": lambda say: db_maintenance(app, dry_run=False),
           "guardian": lambda say: {"state": app.guardian()["state"]}, "ai_snapshot": lambda say: ai_snapshot(app),
           "event_reactions": lambda say: {"types": len(event_reactions(app, refresh=True)["types"])},
           "ladder": lambda say: {k: v for k, v in app.ladder().items() if k in ("stage", "changed", "reasons", "ready")},
           "price_watch": lambda say: _price_watch_now(app),
           "evaluation": lambda say: _brief(app.evaluation(), ("status", "verdict", "n", "hit_rate", "p_value")),
           "drift": lambda say: _brief(app.drift(), ("status", "message")),
           "retrain": lambda say: app.auto_retrain(force=True),
           "agents": lambda say: _run_agents(app, say),
           "graph": lambda say: {**app.build_graph(), "sectors": app.sector_fill(limit=12)},
           "morning_brief": lambda say: _report(app, "morning"),
           "daily_report": lambda say: _report(app, "daily"),
           "backup": lambda say: _backup(app),
           # v13
           "readiness": lambda say: _brief(desk.readiness(app), ("status", "blockers")),
           "event_calendar": lambda say: {"events": len(desk.event_calendar(app)["events"])},
           "event_impact": lambda say: {"stock_events": desk.event_impact(app)["n_stock_events"]},
           "kis_validate": lambda say: _brief(desk.kis_validate(app), ("ok", "failed", "env")),
           "slippage_cal": lambda say: _brief(desk.slippage_calibrate(app)["model"], ("n", "fixed_bps", "impact_coef", "verdict")),
           "model_decay": lambda say: _brief(desk.model_decay(app), ("status", "message")),
           "prediction_power": lambda say: {"bottom_line": desk.prediction_power(app)["bottom_line"]},
           "wics": lambda say: desk.wics(app, force=True),
           "altdata": lambda say: desk.alt_collect(app),
           "event_extract": lambda say: {"n": desk.extract_events(app)["n"]},
           "batch_ab": lambda say: {"rows": len(desk.batch_ab(app)["rows"])},
           "notary": lambda say: {k: v for k, v in desk.notarize(app).items() if k in ("ok", "skipped", "upto_id", "results")},
           "prereg": lambda say: _prereg_new(app)}
    if name not in fns:
        raise ValueError(f"알 수 없는 동작: {name}")
    return _run(name, fns[name])


def _prereg_new(app) -> dict:
    from .review.power import preregister
    return {k: v for k, v in preregister(app, force=True).items() if k in ("version", "h1", "hash")}


def _brief(d: dict, keys) -> dict:
    return {k: d.get(k) for k in keys}


def _run_agents(app, say) -> dict:
    from .agents import macro_agent, news_agent, sector_agent
    say("뉴스 에이전트…")
    n = news_agent(app)
    say("매크로 에이전트…")
    m = macro_agent(app)
    say("섹터 에이전트…")
    sv = sector_agent(app)
    return {"news": n.get("source"), "macro": m.get("source"), "sector": sv.get("source")}


def _report(app, kind: str) -> dict:
    from .reports import run_if_due
    return run_if_due(app, kind, force=True) or {}


def _backup(app) -> dict:
    from .data.backup import backup
    return backup(app.settings.database_url, app.settings.artifacts_dir)


def _price_watch_now(app) -> dict:
    from .alerts import price_watch
    return price_watch(app, markets={"KR", "US"})  # 버튼: 장이 닫혀 있어도 지금 시세 한 번 받기


def _run(name: str, fn) -> dict:
    with _LOCK:
        cur = _ACTIONS.get(name)
        if cur and cur.get("running"):
            return get_action(name)
        st = {"running": True, "started_at": datetime.now(UTC).isoformat(), "progress": "시작", "result": None, "error": None}
        _ACTIONS[name] = st

    def say(msg: str) -> None:
        st["progress"] = msg

    def run() -> None:
        try:
            st["result"] = fn(say)
            st["progress"] = "완료"
        except Exception as e:  # noqa: BLE001
            st["error"] = f"{type(e).__name__}: {e}"[:400]
            log.warning("동작 %s 실패: %s", name, traceback.format_exc())
        finally:
            st["running"] = False
            st["finished_at"] = datetime.now(UTC).isoformat()

    threading.Thread(target=run, name=f"action-{name}", daemon=True).start()
    return get_action(name)


__all__ = ["setup_status", "warmup", "ai_snapshot", "server_status", "db_maintenance", "start_action", "get_action"]

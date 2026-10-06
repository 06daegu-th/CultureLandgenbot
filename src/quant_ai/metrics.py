"""v30 관측성 — Prometheus 형식 지표 (/metrics). 장애를 로그가 아니라 숫자로 일찍 알기 위해.

  quant_http_requests_total{path}      화면 API 호출 수
  quant_http_latency_ms_sum/max{path}  응답 시간 합·최대 (평균 = sum / total)
  quant_job_runs_24h{job,ok}           24시간 작업 성공/실패 수
  quant_job_last_ok_age_seconds{job}   마지막 성공 뒤 지난 시간
  quant_data_lag_days                  국내 일봉이 몇 거래일 밀렸나
  quant_kill_switch                    긴급 정지 (1 = 켜짐)
  quant_autopilot_equity               AI 자동매매 가상 장부 평가금액
같은 PC(127.0.0.1)에서는 로그인 없이, 밖에서는 로그인(또는 토큰)이 있어야 본다.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

_LOCK = threading.Lock()
_HTTP: dict[str, list[float]] = {}  # path → [count, sum_ms, max_ms]


def observe(path: str, ms: float) -> None:
    if not path.startswith("/api/"):
        return
    key = path[:60]
    with _LOCK:
        c = _HTTP.setdefault(key, [0.0, 0.0, 0.0])
        c[0] += 1
        c[1] += ms
        c[2] = max(c[2], ms)


def http_stats() -> dict[str, dict]:
    with _LOCK:
        return {k: {"n": int(v[0]), "avg_ms": round(v[1] / v[0], 1) if v[0] else 0.0, "max_ms": round(v[2], 1)} for k, v in _HTTP.items()}


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render(app, now: datetime | None = None) -> str:
    from sqlalchemy import func, select

    from . import ops
    from .data.db import session_scope
    from .data.models import JobRun
    now = now or datetime.now(UTC)
    out = ["# HELP quant_http_requests_total 화면 API 호출 수", "# TYPE quant_http_requests_total counter"]
    with _LOCK:
        items = sorted(_HTTP.items())
    out += [f'quant_http_requests_total{{path="{_esc(k)}"}} {int(v[0])}' for k, v in items]
    out += ["# TYPE quant_http_latency_ms_sum counter"] + [f'quant_http_latency_ms_sum{{path="{_esc(k)}"}} {v[1]:.1f}' for k, v in items]
    out += ["# TYPE quant_http_latency_ms_max gauge"] + [f'quant_http_latency_ms_max{{path="{_esc(k)}"}} {v[2]:.1f}' for k, v in items]
    with session_scope(app.engine) as s:
        rows = s.execute(select(JobRun.job, JobRun.ok, func.count()).where(JobRun.started_at >= now - timedelta(hours=24))
                         .group_by(JobRun.job, JobRun.ok)).all()
        last_ok = s.execute(select(JobRun.job, func.max(JobRun.started_at)).where(JobRun.ok.is_(True)).group_by(JobRun.job)).all()
    out.append("# TYPE quant_job_runs_24h gauge")
    for job, ok, n in rows:
        out.append(f'quant_job_runs_24h{{job="{_esc(job)}",ok="{"true" if ok else "false"}"}} {int(n)}')
    out.append("# TYPE quant_job_last_ok_age_seconds gauge")
    for job, at in last_ok:
        if at is not None:
            at = at if at.tzinfo else at.replace(tzinfo=UTC)
            out.append(f'quant_job_last_ok_age_seconds{{job="{_esc(job)}"}} {max(0.0, (now - at).total_seconds()):.0f}')
    try:
        from .asof import freshness
        lag = (freshness(app, now)["items"].get("bar_kr") or {}).get("lag_days")
        if lag is not None:
            out += ["# TYPE quant_data_lag_days gauge", f"quant_data_lag_days {int(lag)}"]
    except Exception:  # noqa: BLE001, S110 - 지표 하나 실패가 전체를 막지 않게
        pass
    try:
        out += ["# TYPE quant_kill_switch gauge", f"quant_kill_switch {1 if app.kill_switch_on() else 0}"]
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        from .autopilot import BOOK
        pf = app.load_portfolio(BOOK)
        eq = pf.equity({s: p.avg_price for s, p in pf.positions.items()})  # 매수가 기준 (실시간 평가는 화면에서)
        out += ["# TYPE quant_autopilot_equity gauge", f"quant_autopilot_equity {eq:.0f}"]
        st = ops.get_state(app.engine, "autopilot")
        if st.get("last_run"):
            out.append(f'quant_autopilot_last_run{{date="{_esc(st["last_run"])}"}} 1')
    except Exception:  # noqa: BLE001, S110
        pass
    out.append(f"quant_metrics_generated_unixtime {time.time():.0f}")
    return "\n".join(out) + "\n"


__all__ = ["observe", "render", "http_stats"]

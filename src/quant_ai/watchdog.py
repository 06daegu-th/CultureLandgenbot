"""감시견 (watchdog) — 24시간 스케줄러를 밖에서 지켜본다.

`quant-ai watchdog --mode paper` 가 `quant-ai run --mode paper` 를 자식 프로세스로 띄우고 30초마다 확인한다.
  · 프로세스가 죽었으면 → 다시 띄운다 (연속 실패 시 10초 → 최대 10분 백오프)
  · 살아 있는데 심장박동(ops 'heartbeat')이 5분 넘게 멈췄으면 → 멈춘 것으로 보고 종료 후 다시 띄운다
  · 재시작할 때마다 기록(ops 'watchdog')과 알림 · 다시 뜬 스케줄러는 recovery.startup 으로 놓친 일을 따라잡는다
결정 로직(decide)은 순수 함수라 테스트에서 시간·프로세스 없이 검증한다.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from datetime import UTC, datetime

from . import ops

log = logging.getLogger(__name__)
HEARTBEAT_STALE_S = 300
CHECK_S = 30
GRACE_S = 180  # 막 띄운 뒤에는 첫 심장박동까지 기다린다


def decide(alive: bool, started_s: float, now_s: float, heartbeat_age_s: float | None) -> str:
    """'ok' | 'restart_dead' | 'restart_hung'."""
    if not alive:
        return "restart_dead"
    if now_s - started_s < GRACE_S:
        return "ok"
    if heartbeat_age_s is None or heartbeat_age_s > HEARTBEAT_STALE_S:
        return "restart_hung"
    return "ok"


def backoff(fails: int) -> float:
    return min(600.0, 10.0 * (2 ** max(fails - 1, 0)))


def heartbeat_age(engine, now: datetime | None = None) -> float | None:
    hb = ops.get_state(engine, "heartbeat")
    if not hb.get("at"):
        return None
    return ((now or datetime.now(UTC)) - datetime.fromisoformat(hb["at"])).total_seconds()


def record(engine, event: str, detail: str = "") -> None:
    st = ops.get_state(engine, "watchdog")
    hist = (st.get("history") or [])[-49:] + [{"at": datetime.now(UTC).isoformat(), "event": event, "detail": detail[:200]}]
    ops.set_state(engine, "watchdog", {"history": hist, "restarts": int(st.get("restarts") or 0) + (event.startswith("restart")),
                                       "last": hist[-1]})
    if event.startswith("restart"):
        try:
            from .alerts import push
            push(engine, "ops", "감시견: 스케줄러 재시작", f"{event} · {detail}"[:200], level="warn", link="#readiness")
        except Exception as e:  # noqa: BLE001
            log.warning("알림 실패: %s", e)


def run(app, mode: str, argv: list[str] | None = None) -> None:  # pragma: no cover - 무한 루프 · 프로세스
    cmd = argv or [sys.executable, "-m", "quant_ai.cli", "run", "--mode", mode]
    fails = 0
    proc = subprocess.Popen(cmd)  # noqa: S603 - 고정 인자
    started = time.monotonic()
    record(app.engine, "start", " ".join(cmd[-3:]))
    last_sentinel = 0.0
    while True:
        time.sleep(CHECK_S)
        if time.monotonic() - last_sentinel >= 300:  # 스케줄러가 죽어도 자동 감시는 여기서 계속 (DB·데이터·AI·증권사)
            last_sentinel = time.monotonic()
            try:
                from .sentinel import check
                check(app)
            except Exception as e:  # noqa: BLE001
                log.warning("자동 감시 실패: %s", e)
        act = decide(proc.poll() is None, started, time.monotonic(), heartbeat_age(app.engine))
        if act == "ok":
            fails = 0
            continue
        if act == "restart_hung":
            proc.terminate()
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                proc.kill()
        fails += 1
        wait = backoff(fails)
        record(app.engine, act, f"종료 코드 {proc.returncode} · {wait:.0f}초 뒤 재시작")
        time.sleep(wait)
        proc = subprocess.Popen(cmd)  # noqa: S603
        started = time.monotonic()


__all__ = ["decide", "backoff", "heartbeat_age", "record", "run"]

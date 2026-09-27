"""운영 기능: 공유 상태(킬스위치), 작업 기록, 매매 동시실행 잠금, 알림.

서비스 환경에서는 스케줄러·웹·CLI 가 서로 다른 프로세스(또는 서버)에서 돈다.
그래서 킬스위치는 파일이 아니라 DB 에 두고, 매매 사이클은 잠금으로 한 번에 하나만 실행한다.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .data.db import session_scope
from .data.models import JobRun, SystemState

log = logging.getLogger("quant_ai.ops")
UTC = UTC


# ------------------------------------------------------------------ 공유 상태
def get_state(engine: Engine, key: str, default: dict | None = None) -> dict:
    with session_scope(engine) as s:
        row = s.get(SystemState, key)
        return dict(row.value) if row else (default or {})


def set_state(engine: Engine, key: str, value: dict) -> None:
    with session_scope(engine) as s:
        row = s.get(SystemState, key)
        now = datetime.now(UTC)
        if row is None:
            s.add(SystemState(key=key, value=value, updated_at=now))
        else:
            row.value, row.updated_at = value, now


def kill_switch_on(engine: Engine) -> bool:
    return bool(get_state(engine, "kill_switch").get("on"))


def halted(engine: Engine) -> bool:
    """HALTED = 자동 감시(guardian)가 심각한 이상으로 매매를 완전히 멈춘 상태 (매도 포함 신규 주문 없음)."""
    ks = get_state(engine, "kill_switch")
    return bool(ks.get("on") and ks.get("halt"))


def set_kill_switch(engine: Engine, on: bool, reason: str = "", by: str = "manual", halt: bool = False,
                    conditions: list | None = None) -> None:
    set_state(engine, "kill_switch", {"on": on, "halt": bool(on and halt), "reason": reason, "by": by,
                                      "at": datetime.now(UTC).isoformat(), "conditions": conditions or []})


def record_health(engine: Engine, key: str, ok: bool, error: str | None = None, **extra) -> None:
    """외부 의존성(증권사·리스크 엔진 등) 호출 결과 → 연속 실패 횟수. 자동 킬스위치 조건이 읽는다."""
    try:
        st = get_state(engine, f"health:{key}")
        now = datetime.now(UTC).isoformat()
        st = ({**st, "ok_at": now, "fails": 0, "error": None} if ok
              else {**st, "fail_at": now, "fails": int(st.get("fails", 0)) + 1, "error": (error or "")[:300]})
        set_state(engine, f"health:{key}", {**st, **extra})
    except Exception as e:  # noqa: BLE001 - DB 장애 시에도 원래 작업은 계속 (DB 조건이 따로 잡는다)
        log.warning("상태 기록 실패 (%s): %s", key, e)


# ------------------------------------------------------------------ 작업 기록
@contextlib.contextmanager
def record_job(engine: Engine, job: str) -> Iterator[None]:
    with session_scope(engine) as s:
        run = JobRun(job=job, started_at=datetime.now(UTC))
        s.add(run)
        s.flush()
        run_id = run.id
    try:
        yield
    except Exception as exc:
        with session_scope(engine) as s:
            r = s.get(JobRun, run_id)
            r.finished_at, r.ok, r.error = datetime.now(UTC), False, f"{type(exc).__name__}: {exc}"[:2000]
        raise
    with session_scope(engine) as s:
        r = s.get(JobRun, run_id)
        r.finished_at, r.ok = datetime.now(UTC), True


# ------------------------------------------------------------------ 매매 잠금
class LockBusy(RuntimeError):
    pass


_local_locks: dict[str, threading.Lock] = {}


@contextlib.contextmanager
def trading_lock(engine: Engine, name: str, lock_dir: Path) -> Iterator[None]:
    """같은 모드의 매매 사이클이 동시에 두 번 돌지 않게 한다 (이중 주문 방지).

    PostgreSQL: pg_try_advisory_lock (여러 서버 간에도 유효)
    그 외: 프로세스 내 Lock + 파일 잠금(fcntl)
    """
    key = int(hashlib.sha256(f"quant_ai:{name}".encode()).hexdigest()[:15], 16)
    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar():
                raise LockBusy(f"{name} 매매 사이클이 이미 실행 중")
            try:
                yield
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        return
    lk = _local_locks.setdefault(name, threading.Lock())
    if not lk.acquire(blocking=False):
        raise LockBusy(f"{name} 매매 사이클이 이미 실행 중")
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
        with open(lock_dir / f"{name}.lock", "w") as fh:
            try:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except ImportError:  # pragma: no cover - Windows
                pass
            except BlockingIOError as exc:
                raise LockBusy(f"{name} 매매 사이클이 다른 프로세스에서 실행 중") from exc
            yield
    finally:
        lk.release()


# ------------------------------------------------------------------ 알림
class Notifier:
    """Discord / Slack / Telegram 웹훅 알림. 실패해도 매매 흐름을 막지 않는다."""

    def __init__(self, discord: str | None = None, slack: str | None = None,
                 telegram_token: str | None = None, telegram_chat: str | None = None, min_level: str = "info"):
        self.discord, self.slack = discord, slack
        self.tg_token, self.tg_chat = telegram_token, telegram_chat
        self.levels = ["info", "warn", "critical"]
        self.min_level = min_level
        self.sent: list[tuple[str, str]] = []  # 테스트/대시보드용 최근 기록

    @classmethod
    def from_env(cls, env: dict | None = None) -> Notifier:
        e = os.environ if env is None else env
        return cls(e.get("QUANT_DISCORD_WEBHOOK"), e.get("QUANT_SLACK_WEBHOOK"),
                   e.get("QUANT_TELEGRAM_TOKEN"), e.get("QUANT_TELEGRAM_CHAT_ID"),
                   e.get("QUANT_NOTIFY_LEVEL", "info"))

    @property
    def enabled(self) -> bool:
        return bool(self.discord or self.slack or (self.tg_token and self.tg_chat))

    def _post(self, url: str, payload: dict) -> None:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",  # noqa: S310
                                     headers={"Content-Type": "application/json", "User-Agent": "quant-ai"})
        with urllib.request.urlopen(req, timeout=10):  # noqa: S310 - 설정된 https 웹훅만 호출
            pass

    def send(self, message: str, level: str = "info") -> None:
        if self.levels.index(level) < self.levels.index(self.min_level):
            return
        icon = {"info": "ℹ️", "warn": "⚠️", "critical": "🚨"}[level]
        text_msg = f"{icon} [Quant AI] {message}"[:1900]
        self.sent.append((level, text_msg))
        self.sent = self.sent[-100:]
        targets = []
        if self.discord:
            targets.append((self.discord, {"content": text_msg}))
        if self.slack:
            targets.append((self.slack, {"text": text_msg}))
        if self.tg_token and self.tg_chat:
            targets.append((f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                            {"chat_id": self.tg_chat, "text": text_msg}))
        for url, payload in targets:
            if not url.startswith("https://"):
                log.warning("https 가 아닌 웹훅은 무시: %s", url[:30])
                continue
            try:
                self._post(url, payload)
            except Exception as exc:  # noqa: BLE001 - 알림 실패가 매매를 막으면 안 됨
                log.warning("알림 전송 실패: %s", exc)

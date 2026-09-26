"""24시간 스케줄러: 장중/장외에 따라 다른 작업을 돌린다.

장중 (어느 시장이든 OPEN)
    실시간 가격 · 호가 · 체결 수집 / 뉴스 / 시장 상태 / (Paper·Shadow·Live) 판단→매매
장외
    뉴스 · 공시 · 경제지표 · 글로벌 시장 · 실적 수집 / 결과 채점 / 복기 /
    모델 평가 · 백테스트 · 학습 데이터 생성 · 후보 모델 학습 / Shadow 평가 / 다음 날 시나리오 생성
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .clock import MARKETS, MarketCalendar, Phase, any_market_open

log = logging.getLogger("quant_ai.scheduler")


@dataclass
class Job:
    name: str
    fn: Callable[[datetime], object]
    interval_s: float
    when: str = "always"  # always / open / closed
    last_run: float = field(default=-1e18)
    failures: int = 0

    def due(self, now_s: float, market_open: bool) -> bool:
        if self.when == "open" and not market_open:
            return False
        if self.when == "closed" and market_open:
            return False
        # 연속 실패 시 지수 백오프 (최대 1시간)
        backoff = min(3600, self.interval_s * (2 ** min(self.failures, 6))) if self.failures else self.interval_s
        return now_s - self.last_run >= backoff


class Scheduler:
    def __init__(self, markets: dict[str, MarketCalendar] = MARKETS):
        self.markets = markets
        self.jobs: list[Job] = []
        self.status: dict[str, dict] = {}

    def add(self, name: str, fn: Callable[[datetime], object], interval_s: float, when: str = "always") -> None:
        self.jobs.append(Job(name, fn, interval_s, when))

    def phases(self, now: datetime) -> dict[str, str]:
        return {k: m.phase(now).value for k, m in self.markets.items()}

    def tick(self, now: datetime | None = None, now_s: float | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        now_s = time.monotonic() if now_s is None else now_s
        is_open = any_market_open(now, self.markets)
        ran = []
        for job in self.jobs:
            if not job.due(now_s, is_open):
                continue
            job.last_run = now_s
            try:
                job.fn(now)
                job.failures = 0
                self.status[job.name] = {"ok": True, "at": now.isoformat()}
                ran.append(job.name)
            except Exception as exc:  # noqa: BLE001 - 한 작업 실패가 루프를 멈추면 안 됨
                job.failures += 1
                self.status[job.name] = {"ok": False, "at": now.isoformat(), "error": str(exc)}
                log.exception("작업 실패: %s", job.name)
        return ran

    def run_forever(self, poll_s: float = 5.0) -> None:  # pragma: no cover - 무한 루프
        log.info("스케줄러 시작: %s", [j.name for j in self.jobs])
        while True:
            self.tick()
            time.sleep(poll_s)


def build_default_scheduler(app, mode) -> Scheduler:
    """QuantAI 앱과 모드로 기본 작업표 구성."""
    from datetime import date, timedelta

    from .config import Mode
    from .data.collectors.disclosures import DartCollector
    from .data.collectors.macro import FredCollector
    from .data.collectors.news import NewsCollector
    from .data.db import session_scope

    st = app.settings
    sch = Scheduler()

    if st.news_feeds:
        def news(now):
            with session_scope(app.engine) as s:
                NewsCollector(st.news_feeds).collect(s)
        sch.add("news", news, 300, "open")
        sch.add("news_offhours", news, 1800, "closed")
    if st.dart_api_key:
        def dart(now):
            with session_scope(app.engine) as s:
                DartCollector(st.dart_api_key).collect(s, date.today() - timedelta(days=1), date.today())
        sch.add("disclosures", dart, 600, "always")
    if st.fred_api_key:
        def fred(now):
            with session_scope(app.engine) as s:
                FredCollector(st.fred_api_key).collect(s, date.today() - timedelta(days=30))
        sch.add("macro", fred, 3 * 3600, "closed")

    if mode in (Mode.PAPER, Mode.SHADOW, Mode.LIVE):
        def cycle(now):
            decisions = app.decide()
            app.trade(decisions, mode, ts=now)
        sch.add("decide_and_trade", cycle, 15 * 60, "open")
    elif mode in (Mode.RESEARCH, Mode.PREDICT):
        sch.add("decide", lambda now: app.decide(), 30 * 60, "always")

    sch.add("review", lambda now: app.review(now.date()), 6 * 3600, "closed")
    sch.add("shadow_eval", lambda now: app.evaluate_shadow_models(), 12 * 3600, "closed")
    sch.add("retrain_candidate", lambda now: app.train_candidate(), 24 * 3600, "closed")
    return sch


def market_phase_now() -> dict[str, str]:
    now = datetime.now(timezone.utc)
    return {k: m.phase(now).value for k, m in MARKETS.items()}


__all__ = ["Scheduler", "Job", "Phase", "build_default_scheduler", "market_phase_now"]

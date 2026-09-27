"""24시간 스케줄러: 장중/장외에 따라 다른 작업을 돌린다.

장중 (어느 시장이든 OPEN)
    실시간 가격 · 호가 · 체결 수집 / 뉴스 / 시장 상태 / (Paper·Shadow·Live) 판단→매매
장외
    뉴스 · 공시 · 경제지표 · 글로벌 시장 · 실적 수집 / 결과 채점 / 복기 /
    모델 평가 · 백테스트 · 학습 데이터 생성 · 후보 모델 학습 / Shadow 평가 / 다음 날 시나리오 생성
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path

from .clock import MARKETS, MarketCalendar, Phase, any_market_open

KRX_TRADE_START, KRX_TRADE_END = dtime(9, 10), dtime(15, 10)

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
    def __init__(self, markets: dict[str, MarketCalendar] = MARKETS, notifier=None, engine=None):
        self.markets = markets
        self.jobs: list[Job] = []
        self.status: dict[str, dict] = {}
        self.notifier = notifier
        self.engine = engine  # 있으면 모든 실행을 job_runs 에 기록 (대시보드 운영 패널)

    def add(self, name: str, fn: Callable[[datetime], object], interval_s: float, when: str = "always") -> None:
        self.jobs.append(Job(name, fn, interval_s, when))

    def phases(self, now: datetime) -> dict[str, str]:
        return {k: m.phase(now).value for k, m in self.markets.items()}

    def tick(self, now: datetime | None = None, now_s: float | None = None) -> list[str]:
        now = now or datetime.now(UTC)
        now_s = time.monotonic() if now_s is None else now_s
        is_open = any_market_open(now, self.markets)
        ran = []
        for job in self.jobs:
            if not job.due(now_s, is_open):
                continue
            job.last_run = now_s
            try:
                if self.engine is not None:
                    from .ops import record_job
                    with record_job(self.engine, job.name):
                        job.fn(now)
                else:
                    job.fn(now)
                job.failures = 0
                self.status[job.name] = {"ok": True, "at": now.isoformat()}
                ran.append(job.name)
            except Exception as exc:  # noqa: BLE001 - 한 작업 실패가 루프를 멈추면 안 됨
                job.failures += 1
                self.status[job.name] = {"ok": False, "at": now.isoformat(), "error": str(exc)}
                log.exception("작업 실패: %s", job.name)
                if self.notifier is not None and job.failures in (1, 3, 10):
                    self.notifier.send(f"작업 '{job.name}' 실패 {job.failures}회 연속: {exc}",
                                       "critical" if job.failures >= 3 else "warn")
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
    from .clock import load_holidays
    sch = Scheduler(load_holidays(Path(st.artifacts_dir) / "holidays.json"), notifier=app.notifier, engine=app.engine)

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

    if mode in (Mode.PAPER, Mode.SHADOW, Mode.LIVE) and st.strategy == "core_satellite":
        # 코어는 20거래일마다, AI 거부권·긴급청산·위성은 매 사이클 점검 (일봉 기반이라 한 시간에 한 번이면 충분)
        krx = sch.markets.get("KRX", MARKETS["KRX"])

        def core_satellite(now):
            local = krx.local(now).time()
            # 국내 정규장 안에서만, 시가 직후 급변(09:00~09:10)과 종가 동시호가(15:20~) 는 피한다
            if krx.phase(now) is not Phase.OPEN or not (KRX_TRADE_START <= local <= KRX_TRADE_END):
                return
            app.run_core_satellite(mode, ts=now)
        sch.add("core_satellite", core_satellite, 3600, "open")

    marcap_dir = os.environ.get("QUANT_MARCAP_DIR")
    if marcap_dir:
        from .data.collectors.marcap import sync_marcap

        def krx_data(now):
            # FinanceData/marcap 은 매일 장 마감 후 갱신 → 받아서 DB 반영 (수정주가 재계산 포함). 없으면 처음 받기
            try:
                sync_marcap(marcap_dir)
            except (subprocess.SubprocessError, OSError) as e:  # 네트워크 장애 → 기존 파일로 계속
                log.warning("marcap 갱신 실패: %s", e)
            app.ingest_krx(marcap_dir, years=3)
        sch.add("krx_data", krx_data, 6 * 3600, "closed")

        def krx_bootstrap(now):
            # 처음 켰을 때 DB 가 비어 있으면 장중이라도 한 번 적재 (없으면 코어 사이클이 계속 실패)
            from sqlalchemy import select

            from .data.models import PriceBar
            with session_scope(app.engine) as s:
                empty = s.scalar(select(PriceBar.id).limit(1)) is None
            if empty:
                krx_data(now)
        sch.add("krx_bootstrap", krx_bootstrap, 24 * 3600, "always")
        sch.jobs.insert(0, sch.jobs.pop())  # 매매 작업보다 먼저 실행
    elif mode in (Mode.PAPER, Mode.SHADOW, Mode.LIVE):
        def cycle(now):
            decisions = app.decide()
            app.trade(decisions, mode, ts=now)
        sch.add("decide_and_trade", cycle, 15 * 60, "open")
    elif mode in (Mode.RESEARCH, Mode.PREDICT):
        sch.add("decide", lambda now: app.decide(), 30 * 60, "always")

    def review(now):
        r = app.review(now.date())
        s = r.summary or {}
        acc = s.get("consensus_accuracy")
        app.notifier.send(f"일일 복기: 채점 {s.get('n_resolved', 0)}건"
                          + (f", 합의 정확도 {acc:.0%}" if acc is not None else "")
                          + "".join(f"\n· {x}" for x in (r.lessons or [])[:5]))
    sch.add("review", review, 6 * 3600, "closed")
    if mode in (Mode.PAPER, Mode.SHADOW, Mode.LIVE) and hasattr(app, "strategy_health"):
        # 장 마감 후 하루 한 번 정도: 성과가 과거 검증 범위를 벗어나면(상태 변화 시) 알림
        sch.add("strategy_health", lambda now: app.strategy_health(mode.value), 12 * 3600, "closed")
    if mode in (Mode.PAPER, Mode.SHADOW, Mode.LIVE) and hasattr(app, "ai_verdict"):
        sch.add("ai_verdict", lambda now: app.ai_verdict(), 12 * 3600, "closed")  # 추천 단계가 바뀌면 알림
    sch.add("shadow_eval", lambda now: app.evaluate_shadow_models(), 12 * 3600, "closed")
    sch.add("retrain_candidate", lambda now: app.train_candidate(), 24 * 3600, "closed")
    return sch


def market_phase_now() -> dict[str, str]:
    now = datetime.now(UTC)
    return {k: m.phase(now).value for k, m in MARKETS.items()}


__all__ = ["Scheduler", "Job", "Phase", "build_default_scheduler", "market_phase_now"]

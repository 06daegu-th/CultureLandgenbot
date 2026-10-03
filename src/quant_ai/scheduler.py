"""24시간 스케줄러: 장중/장외에 따라 다른 작업을 돌린다.

장중 (어느 시장이든 OPEN)
    실시간 가격 · 호가 · 체결 수집 / 뉴스 / 시장 상태 / (Paper·Shadow·Live) 판단→매매 / 급등락 알림
항상
    알림 스캔(새 AI 신호·공시·중요 뉴스·채점 결과·실적 D-1) / 시장 흐름(매시간) / 새 공시·뉴스 → 해당 종목 즉시 재분석
장외
    뉴스 · 공시 · 경제지표 · 글로벌 시장 · 실적 수집 / 결과 채점 / 복기 /
    모델 평가 · 백테스트 · 학습 데이터 생성 · 후보 모델 학습 / Shadow 평가 / 다음 날 시나리오 생성 /
    검증 사다리(승격·강등) — 모델 자체는 장중에 바꾸지 않고, 검증된 것만 다음 사이클부터 쓴다
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
                if self.engine is not None and job.failures in (3, 10):
                    try:
                        from .alerts import push
                        push(self.engine, "job", f"작업 '{job.name}' {job.failures}회 연속 실패", str(exc)[:300],
                             level="bad", link="#server", dedupe=f"job:{job.name}:{job.failures}:{now.date()}")
                    except Exception:  # noqa: BLE001, S110 - 알림 실패가 루프를 멈추면 안 됨
                        pass
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
    # 키는 실행 중에 .env 에서 다시 읽힌다 (keys.refresh) → 작업은 항상 등록하고, 키가 없을 때만 건너뛴다
    from .keys import note_error
    from .keys import refresh as _keys_refresh

    def dart(now):
        key = app.settings.dart_api_key
        if not key:
            return
        try:
            with session_scope(app.engine) as s:
                DartCollector(key).collect(s, date.today() - timedelta(days=1), date.today())
            note_error(app.engine, "dart", None)
        except Exception as e:
            note_error(app.engine, "dart", str(e))
            raise
    sch.add("disclosures", dart, 600, "always")

    def sec(now):  # 미국 원천 공시 (SEC EDGAR · 키 없음) — 보유·관심 미국 종목 + 미국 유니버스 앞부분
        from .alerts import focus_symbols
        from .data.collectors import sec as SEC
        from .global_market import universe
        syms = list(dict.fromkeys([x for x in focus_symbols(app) if not x[:1].isdigit()] + universe()))
        SEC.cik_map(app)  # 티커 표 먼저 (따로 저장)
        with session_scope(app.engine) as s:
            res = SEC.collect(app, s, syms, date.today() - timedelta(days=30))
        from . import ops as _ops
        _ops.set_state(app.engine, "sec_filings", res)
    sch.add("sec_filings", sec, 6 * 3600, "always")
    # 보유·관심·코어 종목의 새 공시는 원문을 받아 요약 (한 번에 5건)
    from .data.collectors.dart_docs import summarize_pending
    sch.add("dart_summary", lambda now: summarize_pending(app, app.settings.dart_api_key) if app.settings.dart_api_key else None, 1800, "always")

    def fred(now):
        key = app.settings.fred_api_key
        if not key:
            return
        fc = FredCollector(key)
        try:
            with session_scope(app.engine) as s:
                fc.collect(s, date.today() - timedelta(days=30))
            note_error(app.engine, "fred", f"일부 시리즈 실패: {', '.join(fc.errors)}" if fc.errors else None)
        except Exception as e:
            note_error(app.engine, "fred", str(e))
            raise
    sch.add("macro", fred, 3 * 3600, "closed")
    sch.add("keys_reload", lambda now: _keys_refresh(app), 60, "always")  # .env 를 고치면 1분 안에 반영
    from .news_llm import extract_pending as _news_extract
    sch.add("news_extract", lambda now: _news_extract(app, now=now), 1800, "always")  # 뉴스 구조화(규칙 전부 + LLM 최근 40건) · 같은 소식 묶기
    from . import pead as _pead
    from .center import weekly_alert as _weekly
    sch.add("pead_scan", lambda now: _pead.scan(app, now), 6 * 3600, "always")  # 실적 서프라이즈 → 이벤트 전략 장부에 봉인 (전진 기록)
    sch.add("weekly_schedule", lambda now: _weekly(app, now), 6 * 3600, "always")  # 월요일: 이번 주 보유 종목 일정 알림
    sch.add("pead_notary", lambda now: _pead.notarize(app), 24 * 3600, "always")  # 이벤트 전략 장부 digest 외부 공증 (DB 를 고쳐도 드러나게)
    from .aitrack import snapshot as _ai_track
    sch.add("ai_track", lambda now: _ai_track(app, now), 24 * 3600, "closed")  # AI 성적 매일 기록 · 나쁘면 자동 SHADOW

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

    elif mode in (Mode.PAPER, Mode.SHADOW, Mode.LIVE):
        # 합의 전략(QUANT_STRATEGY=consensus)일 때만: AI 합의 신호로 직접 매매
        def cycle(now):
            decisions = app.decide()
            app.trade(decisions, mode, ts=now)
        sch.add("decide_and_trade", cycle, 15 * 60, "open")
    elif mode in (Mode.RESEARCH, Mode.PREDICT):
        sch.add("decide", lambda now: app.decide(), 30 * 60, "always")

    # 주가 자동 갱신: 설정이 없어도 ./run.sh data 가 받아 둔 기본 위치가 있으면 쓴다 (없으면 데이터가 낡아 매매 중단)
    marcap_dir = os.environ.get("QUANT_MARCAP_DIR") or next(
        (str(p / "data") for p in (Path(os.environ.get("QUANT_HOME") or Path.home() / ".quant-ai") / "data" / "marcap",
                                   Path("data/marcap")) if (p / ".git").exists()), None)
    if marcap_dir and mode in (Mode.PAPER, Mode.SHADOW, Mode.LIVE, Mode.RESEARCH, Mode.PREDICT):
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
    if mode in (Mode.PAPER, Mode.SHADOW, Mode.LIVE) and hasattr(app, "guardian"):
        # 자동 킬스위치: 5분마다 10개 조건 점검 → critical 이면 HALTED (+ champion 이상이면 롤백)
        sch.add("guardian", lambda now: app.guardian(mode.value, market_open=sch.phases(now).get("KRX") == "open"),
                300, "always")

        def ai_snapshot(now):
            # 장외에만 켜 둔 경우에도 AI 판단 기록이 쌓이도록 (새 일봉마다 한 번, 장중 섀도가 이미 했으면 건너뜀)
            from .actions import ai_snapshot as snap
            if st.has_llm and st.ai_shadow:
                snap(app)
        sch.add("ai_snapshot", ai_snapshot, 6 * 3600, "closed")

        def us_book(now):
            # 미국 코어 + AI 장부 (가상매매): 새 일봉이 생기면 한 번. 네트워크 실패 시 다음 회차에 다시
            from .global_market import run_cycle, sync
            if os.environ.get("QUANT_US", "true").lower() != "false":
                sync(app)
                run_cycle(app, ts=now)
        sch.add("us_cycle", us_book, 3 * 3600, "always")

        def event_db(now):
            from .analytics import event_reactions
            event_reactions(app, refresh=True)
        sch.add("event_reactions", event_db, 24 * 3600, "closed")
    if mode in (Mode.PAPER, Mode.SHADOW, Mode.LIVE, Mode.RESEARCH, Mode.PREDICT) and hasattr(app, "ladder"):
        # 24시간 관찰: 급등락 감시(장중) · 알림 스캔 · 시장 흐름(매시간) · 새 공시/중요 뉴스 즉시 재분석
        from .actions import event_reanalyze
        from .alerts import alert_scan, market_pulse, price_watch
        sch.add("price_watch", lambda now: price_watch(app, now), 150, "open")
        sch.add("alert_scan", lambda now: alert_scan(app, now), 120, "always")
        sch.add("market_pulse", lambda now: market_pulse(app, now), 3600, "always")
        sch.add("event_reanalyze", lambda now: event_reanalyze(app, now), 600, "always")
        if os.environ.get("QUANT_COMMUNITY", "true").lower() != "false":
            from .alerts import focus_symbols
            from .data.collectors.community import collect as community
            sch.add("community", lambda now: community(app.engine, list(focus_symbols(app))), 1800, "always")
        # 야간: 검증 사다리 평가 (승격은 한 칸씩 · 강등은 즉시 Shadow 로) → 다음 사이클부터 적용
        sch.add("ladder", lambda now: app.ladder(), 6 * 3600, "closed")
        # 예측 장부 봉인(매시간) · 결과 매칭(1·5·20일, 장외) · 독립 평가(야간)
        sch.add("ledger_anchor", lambda now: app.ledger_anchor(), 3600, "always")
        sch.add("match_outcomes", lambda now: app.match_outcomes(), 3 * 3600, "closed")
        sch.add("evaluation", lambda now: app.evaluation(), 12 * 3600, "closed")
        # 시장 전체를 보는 에이전트 (한 번의 호출로 시장 정리 → 종목 AI 들의 재료) · 업종 지도 · 지식 그래프
        from .agents import macro_agent, news_agent, sector_agent
        sch.add("news_agent", lambda now: news_agent(app, now), 3600, "open")
        sch.add("news_agent_offhours", lambda now: news_agent(app, now), 3 * 3600, "closed")
        sch.add("macro_agent", lambda now: macro_agent(app, now), 12 * 3600, "always")
        sch.add("sector_fill", lambda now: app.sector_fill(), 2 * 3600, "closed")
        sch.add("sector_agent", lambda now: sector_agent(app, now), 12 * 3600, "always")
        sch.add("knowledge_graph", lambda now: app.build_graph(), 6 * 3600, "closed")
        # 외국인·기관 수급 (장 마감 후 하루 한 번, 보유·관심·코어)
        from .alerts import focus_symbols as _focus
        from .data.collectors.investor_flow import collect as flow_collect
        sch.add("investor_flow", lambda now: flow_collect(app.engine, list(_focus(app))), 6 * 3600, "closed")
        # 아침 브리핑(평일 08:30 KST) · 일일 리포트(16:10 KST) — 5분마다 시간 창을 확인하고 하루 한 번만
        from .reports import run_if_due
        sch.add("morning_brief", lambda now: run_if_due(app, "morning", now), 300, "always")
        sch.add("daily_report", lambda now: run_if_due(app, "daily", now), 300, "always")
        # KIS 실시간 체결 (웹소켓): 장중에만 연결 → 급등락·규칙 알림이 초 단위로 (QUANT_KIS_WS=false 로 끔)
        if st.broker == "kis" and os.environ.get("QUANT_KIS_WS", "true").lower() != "false":
            from .trading.kis_ws import ensure_running
            sch.add("kis_ws", lambda now: ensure_running(app, sch.phases(now).get("KRX") == "open"), 60, "always")
        # DB 자동 백업 (하루 1개 · 7개 보관 · 무결성 확인)
        from .data.backup import backup
        sch.add("backup", lambda now: backup(st.database_url, st.artifacts_dir), 24 * 3600, "closed")
        # v13 — 심장박동 · 이벤트 캘린더 · 매매 준비 점검 · 노후 · 예측력 · 슬리피지 보정 · 이벤트 영향 · 공증 …
        from . import desk
        from .recovery import heartbeat
        sch.add("heartbeat", lambda now: heartbeat(app.engine, now), 60, "always")
        sch.add("event_calendar", lambda now: desk.event_calendar(app, now=now), 3600, "always")
        sch.add("readiness", lambda now: desk.readiness(app), 900, "open")
        sch.add("readiness_offhours", lambda now: desk.readiness(app), 3600, "closed")
        sch.add("model_decay", lambda now: desk.model_decay(app), 12 * 3600, "closed")
        sch.add("prediction_power", lambda now: desk.prediction_power(app), 6 * 3600, "always")
        sch.add("slippage_cal", lambda now: desk.slippage_calibrate(app), 24 * 3600, "closed")
        sch.add("event_impact", lambda now: desk.event_impact(app), 24 * 3600, "closed")
        sch.add("event_extract", lambda now: desk.extract_events(app), 6 * 3600, "always")
        sch.add("wics", lambda now: desk.wics(app), 24 * 3600, "closed")
        sch.add("altdata", lambda now: desk.alt_collect(app), 24 * 3600, "closed")
        sch.add("batch_ab", lambda now: desk.batch_ab(app), 24 * 3600, "closed")
        sch.add("my_journal", lambda now: desk.my_journal(app), 6 * 3600, "closed")
        if st.notary != "off":
            sch.add("notary", lambda now: desk.notarize(app), 24 * 3600, "always")
        # 국내 실적 컨센서스 스냅샷(서프라이즈 이력) · 한은 금통위 일정/기준금리 · VKOSPI
        from .alerts import focus_symbols as _f13
        from .data.collectors import bok as _bok
        from .data.collectors import kr_consensus as _krc
        from .data.collectors import vkospi as _vk
        sch.add("kr_consensus", lambda now: _krc.collect(app.engine, list(_f13(app))), 24 * 3600, "closed")
        sch.add("bok", lambda now: (_bok.schedule(app.engine), _bok.base_rate(app.engine, app.settings.ecos_api_key)), 24 * 3600, "always")
        sch.add("vkospi", lambda now: _vk.get(app.engine, app.market_data()[1]), 12 * 3600, "always")
        # Truth Center (매시간) · 1차(KRX) 일봉이 늦으면 2차(Yahoo)로 빈 날만 채움
        from . import truth as _truth
        from .data.sources import gap_fill as _gap
        sch.add("truth", lambda now: _truth.report(app, mode.value if mode.value in ("paper", "shadow", "live") else None, now), 3600, "always")
        sch.add("gap_fill", lambda now: _gap(app, list(_f13(app))), 3 * 3600, "closed")
        # v16 — 자동 감시(데이터·AI·스케줄러·DB·증권사) 5분 · 투자 논리(목표·무효화·점검일) 알림 30분 · 쉬운 위험 기록 하루
        from . import sentinel as _sentinel
        from . import thesis as _thesis
        from .center import risk_simple as _rs
        sch.add("sentinel", lambda now: _sentinel.check(app, now), 300, "always")
        sch.add("thesis_watch", lambda now: _thesis.alert(app), 1800, "always")
        sch.add("risk_hist", lambda now: _rs(app, mode.value if mode.value in ("paper", "shadow", "live") else "paper"), 6 * 3600, "always")
        from . import datahealth as _dh
        from . import failure_lab as _fl
        sch.add("data_health", lambda now: _dh.report(app, now), 3600, "always")
        sch.add("failure_lab", lambda now: _fl.analyze(app, now=now), 24 * 3600, "closed")
        if st.broker == "kis":
            # 하루 한 번 장중: KIS 검증 스위트 (모의 = 주문·취소 경로까지 · QUANT_KIS_FILL_TEST=true 면 1주 실제 체결로 슬리피지 실측)
            fill_test = os.environ.get("QUANT_KIS_FILL_TEST", "").lower() == "true" and st.kis_env == "demo"

            def kis_validate(now):
                krx = sch.markets.get("KRX", MARKETS["KRX"])
                local = krx.local(now).time()
                if krx.phase(now) is Phase.OPEN and KRX_TRADE_START <= local <= KRX_TRADE_END:
                    desk.kis_validate(app, fill=fill_test, market_open=True)
            sch.add("kis_validate", kis_validate, 24 * 3600, "open")
    if mode in (Mode.PAPER, Mode.SHADOW, Mode.LIVE) and hasattr(app, "ai_verdict"):
        sch.add("ai_verdict", lambda now: app.ai_verdict(), 12 * 3600, "closed")  # 추천 단계가 바뀌면 알림
    sch.add("shadow_eval", lambda now: app.evaluate_shadow_models(), 12 * 3600, "closed")
    if hasattr(app, "auto_retrain"):
        # 야간: 드리프트 점검 → (드리프트 · 성과 하락 · 7일 경과 시) 재학습 후보 변형 생성 → 게이트 → Shadow
        sch.add("drift", lambda now: app.drift(), 12 * 3600, "closed")
        sch.add("retrain_candidate", lambda now: app.auto_retrain(now), 24 * 3600, "closed")
    else:
        sch.add("retrain_candidate", lambda now: app.train_candidate(), 24 * 3600, "closed")
    from . import goal
    sch.add("dca", lambda now: goal.dca_run(app, now), 3600, "always")  # v19: 월 적립일이면 한 번 (모의 장부 입금 · 실계좌는 알림)
    from .governance import guard_scheduler
    guard_scheduler(sch, app)  # 상용 모드면 상용 불가 소스(네이버·Yahoo 등) 수집을 실행 시점에 멈춘다
    return sch


def market_phase_now() -> dict[str, str]:
    now = datetime.now(UTC)
    return {k: m.phase(now).value for k, m in MARKETS.items()}


__all__ = ["Scheduler", "Job", "Phase", "build_default_scheduler", "market_phase_now"]

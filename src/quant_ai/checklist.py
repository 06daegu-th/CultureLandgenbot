"""'초고퀄리티 최종 버전' 완성 기준 — 항목마다 구현 위치 · 검증 테스트 · 지금 상태.

상태
  live   = 지금 실행 중인 시스템에서 확인됨 (근거 숫자 표시)
  impl   = 구현 · 테스트 통과, 지금은 관찰할 데이터가 없음 (예: 실주문이 없어 부분체결 0건)
  setup  = 구현됨, 사용자 설정이 필요 (키 · 증권사)
  warn / bad = 확인했더니 문제 있음
docs/FINAL_CHECKLIST.md 는 이 목록에서 만든다 (python -m quant_ai.checklist).
"""

from __future__ import annotations

from datetime import UTC, datetime

from . import ops

# (영역, 항목, 무엇을, 어디, 테스트)
ITEMS = [
    ("DATA", "정확한 timestamp", "모든 기록 UTC 저장 · 화면 KST · 기준 시각(AS-OF) 칩", "asof.py", "test_asof_labels_and_bar_staleness_respect_calendar"),
    ("DATA", "primary/secondary source", "KRX 1차 · Yahoo 2차(빈 날만, 겹치는 날 비율로 정렬) · 실시간 KIS→네이버→Yahoo", "data/sources.py", "test_secondary_source_fills_only_gap_days"),
    ("DATA", "stale detection", "휴장일을 반영한 'N거래일 밀림' · SLA 별 fresh/stale/old", "asof.py · readiness.py", "test_readiness_seven_gates_and_buy_block"),
    ("DATA", "data quality", "급변·0값·결측·정지 검사 (수집 때 + Truth Center 재검사)", "data/quality.py · truth.py", "test_truth_center_sections"),
    ("DATA", "corporate actions", "KRX 전일대비로 수정주가 복원 · 미설명 ±45% 급변 탐지", "data/collectors/marcap.py · truth.py", "test_split_is_adjusted_away"),
    ("DATA", "PIT", "월말 시총 상위 유니버스 (상장폐지 포함) · 누수 감사 L6", "pipeline.universe_at · review/leakage.py", "test_leakage_audit_catches_future_news_and_feature_lookahead"),
    ("DATA", "Per-stock trust", "종목별 데이터 신뢰도: 기준일(거래일 밀림) · 1차/2차 출처 · 품질 · 거래정지 의심 · AI 판단 나이", "stock.trust", "test_trust_flags_stale_secondary_and_halt"),
    ("MARKET", "KRX calendar", "holidays XKRX (대체공휴일·선거일·연말)", "clock.py", "test_exchange_calendar_holidays_and_special_sessions"),
    ("MARKET", "US calendar", "holidays XNYS", "clock.py", "test_exchange_calendar_holidays_and_special_sessions"),
    ("MARKET", "holiday", "휴장 이름 표시 · 휴장일 주문 없음", "clock.py", "test_exchange_calendar_holidays_and_special_sessions"),
    ("MARKET", "early close", "미국 조기 폐장 13:00 · KRX 새해 10시 개장", "clock.session", "test_exchange_calendar_holidays_and_special_sessions"),
    ("MARKET", "market open/closed", "시장별 장전·장중·장외 · 상단 시장 시계", "clock.phase · desk.js", "test_market_clock_next_open_close"),
    ("MARKET", "next open", "다음 개장 시각 · 남은 시간", "clock.next_open", "test_market_clock_next_open_close"),
    ("MARKET", "next close", "다음 폐장 시각 · 남은 시간", "clock.next_close", "test_market_clock_next_open_close"),
    ("MARKET", "timezone", "Asia/Seoul · America/New_York (서머타임 자동)", "clock.py", "test_market_clock_next_open_close"),
    ("EVENT", "earnings", "Yahoo·Nasdaq·DART 일정 · D-day · 소스 충돌 탐지", "data/fundamentals.py · truth.py", "test_event_calendar_builds_every_kind_with_sources"),
    ("EVENT", "FOMC", "연준 공표 일정", "engines/events.py", "test_event_calendar_builds_every_kind_with_sources"),
    ("EVENT", "CPI", "FRED 공식 발표일 (키 있으면)", "engines/events.py", "test_fred_release_dates_parse"),
    ("EVENT", "NFP", "FRED 공식일 → 없으면 첫째 금요일(추정 표시)", "engines/events.py", "test_event_calendar_builds_every_kind_with_sources"),
    ("EVENT", "dividend", "배당락·지급일 · 계좌별 배당 일정", "engines/events.py · accounts.py", "test_account_tax_rules"),
    ("EVENT", "disclosure", "DART 공시 · 원문 요약 · 이벤트 추출", "engines/events.py · event_extract.py", "test_event_extraction_types_amounts_materiality"),
    ("EVENT", "options", "KOSPI200 만기·동시만기 · 미국 만기·쿼드러플 위칭 · 옵션 내재변동", "engines/events.py · options.py", "test_event_calendar_builds_every_kind_with_sources"),
    ("EVENT", "rebalance", "KOSPI200 정기변경 · MSCI 리뷰 · S&P 분기", "engines/events.py", "test_event_calendar_builds_every_kind_with_sources"),
    ("EVENT", "estimated flag", "규칙으로 추정한 날짜는 '추정' 표시", "engines/events.py", "test_event_calendar_builds_every_kind_with_sources"),
    ("EVENT", "event risk", "이벤트 위험 점수 · 실적 D-1 매수 ×0.5 · 금통위/FOMC 고베타 ×0.75", "engines/events.py · risk.py", "test_risk_engine_gates_block_and_shrink_buys_only"),
    ("AI", "Quant", "차트·Quant AI (학습 모델)", "analysts/analysts.py", "test_multi_ai"),
    ("AI", "News", "뉴스 AI · 뉴스 에이전트", "analysts · agents.py", "test_agents_graph"),
    ("AI", "Macro", "경제·시장 AI · 매크로 에이전트", "analysts · agents.py", "test_agents_graph"),
    ("AI", "Risk", "Risk AI 거부권 (희석되지 않음)", "analysts · ensemble/engine.py", "test_risk_llm_cannot_override_rule_veto"),
    ("AI", "Earnings", "공시·실적 AI · 실적 서프라이즈 모델", "analysts · engines/earnings.py", "test_earnings_model_posterior_and_reactions"),
    ("AI", "Ensemble", "성적 가중 합의 · 충돌도", "ensemble/engine.py", "test_track_record_shifts_weight"),
    ("AI", "Calibration", "Platt 보정 · 신뢰도 곡선 · Readiness 관문", "ensemble/calibration.py", "test_calibration"),
    ("AI", "Drift", "PSI + KS · 이력", "engines/drift.py", "test_drift_ks_confirms_psi"),
    ("AI", "Per-AI decay", "AI 별 성능 저하 자동 감지 (같은 날 판단은 일별로 묶어 검정) → 알림 · AI Health", "review/decay.py · health.py", "test_per_ai_decay_alert_and_health"),
    ("AI", "Forward evaluation", "사전 등록 + SPRT 전진 검증 · 독립 평가기", "review/power.py · evaluator.py", "test_preregistration_is_sealed_and_forward_test_counts_only_after"),
    ("MODEL", "Walk-forward", "20일마다 재학습 · 표본 외", "backtest/backtester.py", "test_backtest_runs_and_trains_only_on_past"),
    ("MODEL", "Purged", "라벨 기간이 시험 구간과 겹치는 학습 표본 제거", "backtest/backtester.py (cutoff = t − horizon − embargo)", "test_purged_walk_forward_never_trains_on_test_labels"),
    ("MODEL", "Embargo", "라벨 뒤 1봉 간격 · 누수 감사 L5", "backtest/backtester.py", "test_purged_walk_forward_never_trains_on_test_labels"),
    ("MODEL", "DSR", "시도 횟수 보정 샤프", "backtest/stats.py", "test_psr_dsr_distinguish_luck_from_skill"),
    ("MODEL", "PSR", "확률적 샤프", "backtest/stats.py", "test_psr_dsr_distinguish_luck_from_skill"),
    ("MODEL", "Bootstrap", "신호 수익 부트스트랩 구간 · 파산 위험 블록 부트스트랩", "review/evaluator.py · trading/ruin.py", "test_statistics_helpers"),
    ("MODEL", "Champion", "게이트 통과 모델만 챔피언", "registry/model_registry.py", "test_gate_rejects_unlucky_and_fragile_models"),
    ("MODEL", "Challenger", "도전자 모델 섀도 채점", "pipeline.decide · evaluate_shadow_models", "test_challenger_scored_silently_and_promoted_only_through_gate"),
    ("MODEL", "Rollback", "챔피언 성과 이상 시 자동 롤백", "pipeline.check_champion · guardian", "test_champion_rollback_restores_previous"),
    ("PORTFOLIO", "Correlation", "상관 행렬 · 묶음(클러스터)", "trading/portfolio_risk.py", "test_portfolio_risk_v2_has_every_lens"),
    ("PORTFOLIO", "Sector", "WICS 우선 업종 노출 · 사전 게이트", "trading/portfolio_risk.py", "test_pretrade_gate_caps_sector_cluster_and_var"),
    ("PORTFOLIO", "Concentration", "HHI · 유효 종목 수", "trading/portfolio_risk.py", "test_portfolio_risk_v2_has_every_lens"),
    ("PORTFOLIO", "VaR", "과거·정규·CF·EWMA·10일 · 성분 VaR", "trading/portfolio_risk.py", "test_portfolio_risk_v2_has_every_lens"),
    ("PORTFOLIO", "ES", "ES 95/99", "trading/portfolio_risk.py", "test_portfolio_risk_v2_has_every_lens"),
    ("PORTFOLIO", "Stress", "실제 위기 재생 + 가정 시나리오", "trading/portfolio_risk.py", "test_portfolio_risk_v2_has_every_lens"),
    ("PORTFOLIO", "Risk-of-Ruin", "블록 부트스트랩 1년", "trading/ruin.py", "test_risk_of_ruin_monte_carlo"),
    ("PORTFOLIO", "Position sizing", "반켈리 · 변동성 목표 · 한도 × 이벤트 × 준비 상태", "trading/trade_plan.py", "test_trade_plan_sizing_entry_and_invalidation"),
    ("TRADING", "KIS", "시세·주문·체결·취소·잔고 · 검증 스위트", "trading/kis.py · kis_check.py", "test_kis_validation_suite_steps"),
    ("TRADING", "WebSocket", "H0STCNT0 체결 · PINGPONG · 재접속", "trading/kis_ws.py", "test_kis_realtime_session_with_local_server"),
    ("TRADING", "Orderbook", "H0STASP0 10단계 호가", "trading/kis_ws.py", "test_parse_orderbook_and_latest_book"),
    ("TRADING", "Partial fill", "호가 잔량 부분체결 · 미체결 잔량 취소", "trading/kis.py · exec_sim.py", "test_shadow_broker_fills_against_depth"),
    ("TRADING", "Cancel", "제한 시간 뒤 잔량 취소 · 재시작 시 정리", "trading/kis.py", "test_partial_fill_remainder_is_cancelled"),
    ("TRADING", "Reconciliation", "증권사 잔고 = 진실 · 불일치 기록", "trading/kis.py · pipeline.reconcile_live", "test_db_drift_is_corrected_from_broker_balance"),
    ("TRADING", "Idempotency", "client_order_id 유일 · 재시작 복구", "pipeline.recover_orders", "test_restart_recovers_and_cancels_open_order"),
    ("TRADING", "Slippage", "실측 → 고정분·충격 계수 보정 (50건+)", "trading/slippage.py", "test_slippage_calibration_recovers_and_shrinks"),
    ("TRADING", "Market impact", "제곱근 법칙 · 상한 150bp", "trading/portfolio.py", "test_square_root_market_impact"),
    ("SAFETY", "Kill switch", "수동 · 자동 10조건 · HALTED", "trading/guardian.py", "test_guardian_halts_on_duplicate_orders_and_blocks_all_trading"),
    ("SAFETY", "Readiness", "7관문 + Truth Center 연결", "readiness.py", "test_readiness_seven_gates_and_buy_block"),
    ("SAFETY", "Fail closed", "점검 없음·실패·캘린더 없음 → 신규 매수 차단", "readiness.buy_block", "test_fail_closed_when_readiness_or_calendar_missing"),
    ("SAFETY", "Pre-trade gate", "업종·묶음·VaR 사전 게이트 · 이벤트 배수", "trading/risk.py", "test_risk_engine_gates_block_and_shrink_buys_only"),
    ("SAFETY", "Pre-trade simulator", "종목 페이지에서 실제 매매와 같은 게이트로 '지금 사면?' 시험 (주문 없음)", "stock.pretrade", "test_pretrade_mirrors_trading_gates"),
    ("SAFETY", "Daily loss", "일 손실 한도 · 1.5배면 자동 정지", "trading/risk.py · pipeline", "test_risk_blocks_buys_after_daily_loss_but_allows_sells"),
    ("SAFETY", "Broker failure", "연속 실패 → HALTED · BROKER 관문", "guardian · readiness", "test_guardian_conditions_volatility_loss_broker"),
    ("SAFETY", "Calendar failure", "휴장일 캘린더 없으면 EVENT 빨강", "truth.market_clock", "test_fail_closed_when_readiness_or_calendar_missing"),
    ("SAFETY", "Data failure", "일봉 N일 밀림 → 매매 중단 · DATA 관문", "pipeline._stale_guard · readiness", "test_readiness_seven_gates_and_buy_block"),
    ("OPERATIONS", "Scheduler", "장중/장외 작업표 · 백오프", "scheduler.py", "test_scheduler_has_24h_observation_jobs"),
    ("OPERATIONS", "Watchdog", "스케줄러 죽음·멈춤(심장박동 5분) → 재시작", "watchdog.py", "test_watchdog_decisions"),
    ("OPERATIONS", "Restart", "재시작 복구 · 따라잡기", "recovery.py", "test_recovery_startup_catch_up_and_source_health"),
    ("OPERATIONS", "Backup", "SQLite 백업 · 무결성 · 7개 보관", "data/backup.py", "test_sqlite_backup_is_compressed_verified_and_rotated"),
    ("OPERATIONS", "DB migration", "Alembic 0001~0006 (0006: 수집 시각·요약 시각) · ensure_columns", "migrations · data/db.py", "test_old_database_gets_new_columns"),
    ("OPERATIONS", "Health check", "/api/health · DB 점검", "web/server.py · recovery.db_check", "test_web_security_headers_and_health"),
    ("OPERATIONS", "Alerts", "토스트·알림센터·텔레그램·웹 푸시", "alerts.py · ops.Notifier · webpush.py", "test_alert_routing_to_external_channels"),
    ("UX", "Stock search", "이름·별칭·티커 검색 · 최근 본 종목 · '/' 단축키", "web/static/pro.js · truth.js", "test_search_korean_aliases_and_global"),
    ("UX", "Chart", "캔들 + AI 진입 구간·손절·목표·추격 금지 선 + 과거 AI BUY/SELL 표시", "web/static/app.js · truth.js chartPlanLines", "e2e 스크린샷"),
    ("UX", "Events", "D-day · 이벤트 캘린더 · 오늘 할 일에 보유·관심 종목 D-3", "web/static/desk.js · ux.today", "test_event_calendar_builds_every_kind_with_sources"),
    ("UX", "Market clock", "상단 시장 시계 (장중·장외·휴장 · 개장/폐장까지 남은 시간, 1초마다)", "web/static/truth.js", "test_market_clock_next_open_close"),
    ("UX", "AS-OF", "모든 화면 기준 시각 칩", "asof.py · web/static/desk.js", "test_asof_labels_and_bar_staleness_respect_calendar"),
    ("UX", "AI explanation", "AI 별 의견 · 근거 추적(Evidence Chain) · 봉인된 판단 그대로", "explain.for_symbol", "test_explain_for_symbol_uses_sealed_record"),
    ("UX", "Why BUY", "찬성·반대(영향 순)·기준·무효화 가격", "explain.py", "test_explain_why_actions"),
    ("UX", "Why SELL", "찬성·반대·기준·BUY 가 되려면", "explain.py", "test_explain_why_actions"),
    ("UX", "Why NO TRADE", "막은 것(거부권·충돌·관문·이벤트) · 무엇이 바뀌면", "explain.py", "test_explain_why_actions"),
    ("UX", "Why bought / not bought", "코어 순위·점수 · 거부 · 매수 불가 · 주문·차단 기록으로 실제 매매 설명", "stock.story", "test_story_explains_bought_and_not_bought"),
    ("UX", "News digest", "뉴스·공시 자동 요약 (7일 톤 · 중요 헤드라인 · 공시 원문 요약 · 추출 이벤트)", "stock.digest", "test_digest_and_events"),
    ("UX", "Portfolio risk", "리스크 2.0 화면 · 종목 비교의 상관 행렬", "web/static/desk.js · ux.compare", "test_portfolio_risk_v2_has_every_lens"),
    ("UX", "System status", "Truth Center · 완성 기준 · 매매 준비 · 관제실", "truth.py · checklist.py", "test_truth_center_sections"),
    ("UX", "Watchlist · holdings", "관심종목 별표(매일 AI 판단) · 종목 페이지에 내 보유(장부+계좌) · 종목별 과거 AI 적중률", "ux.py", "test_star_holdings_track_today_compare"),
    ("UX", "Today · compare", "오늘 할 일 · 시장 한눈에 · 종목 비교(2~4개)", "ux.py · web/static/truth.js", "test_star_holdings_track_today_compare"),
    ("UX", "Customization", "홈 편집(숨기기·순서) · 외부 알림 종류별 채널 · 조용한 시간 (서버 저장)", "prefs.py · alerts._route", "test_prefs_validate_and_route_notifications"),
    ("UX", "Convenience", "모바일 · 다크/라이트 · 키보드 단축키(?) · 최근 검색", "web/static", "e2e 스크린샷"),
    # ---- v16
    ("DATA", "Data provenance", "뉴스·공시 수집 시각(collected_at)·공시 요약 시각 · 종목 페이지 신선도(초 단위, SLA 초과 경고)", "stockplus.freshness · migrations 0006", "test_header_situation_freshness_position"),
    ("DATA", "DATA HEALTH", "분야별 점수 % + 전체 · 주가 50% 미만 또는 장중 가격 15분 지연 → TRADING BLOCKED", "datahealth.py", "test_data_health_blocks_and_explain_gate"),
    ("SAFETY", "Fail-Closed matrix", "AI 다운=기권 · 뉴스 다운=신규 매수 절반 · 증권사/DB/시세 다운=차단", "failmode.py", "test_failmode_news_down_halves_new_buys"),
    ("SAFETY", "Data quality gate", "AI BUY 여도 데이터 품질 LOW 면 '거래하지 않음' (판단은 기록 그대로)", "explain.for_symbol", "test_data_health_blocks_and_explain_gate"),
    ("SAFETY", "Audit log", "긴급 정지·설정·계좌·알림 규칙·투자 논리·성향·모의 주문 — 추가만 가능", "governance.audit", "test_governance_audit_prefs_theme_widgets"),
    ("AI", "Verifiable AI", "지금 확률과 같은 구간의 과거 판단 N회 · 실제 상승 % · Calibration GOOD/FAIR/POOR", "scorecard.verify_now", "test_scorecard_verify_now_and_public_report"),
    ("AI", "Public scorecard", "최근 1000건 전부 · 정확도·Brier Skill·보정·Net Alpha·MDD·90일 안정성·실패 사례 공개", "scorecard.public_report", "test_scorecard_verify_now_and_public_report"),
    ("AI", "Failure research", "틀린 예측 원인 후보 태그(시장·실적·뉴스·국면·거래량·금리환율·데이터) + 약점 패턴", "failure_lab.py", "test_lab_validation_portfolio_personal_failure"),
    ("AI", "AI Lab", "여러 AI → 합의 → Risk Gate → 최종 · 모델 단계 Research→Challenger→Shadow→Champion", "lab.py", "test_lab_validation_portfolio_personal_failure"),
    ("UX", "Stock OS", "현재 상황 한 줄 · OS 헤더(가격·AI·실적 D-n·뉴스 톤·공시·수급·밸류·위험·내 보유) · 섹션 숨기기/순서", "stockplus.header/situation · os.js", "test_header_situation_freshness_position"),
    ("UX", "News v2 · AI digest", "톤 · 과거 같은 톤 뉴스 뒤 평균 움직임 · 중요 공시 강조 · AI 요약(외부 텍스트 지시 무시)", "stockplus.news/ai_digest · insight.news_impact", "test_news_v2_tone_important_disclosure_and_ai_digest"),
    ("UX", "Chart AI overlay", "매수 관심구간·목표·무효화 · 지지/저항 · 뉴스·공시·실적 표시", "stockplus.overlay", "test_overlay_lines_and_marks"),
    ("UX", "Action Center · briefing", "확인할 종목 3 · 주의 이벤트 · AI 신호 변경 · 위험 증가 · 공시 · 오늘의 AI 브리핑 ①~⑤", "center.action_center · portfolio_os.briefing", "test_action_center_watchlist_groups"),
    ("UX", "Why no trade", "막히거나 줄어든 매수 · 건너뛴 사이클 · 실행 안 된 AI BUY 를 원인별로", "notrade.py", "test_notrade_categories_and_report"),
    ("UX", "Manual paper ticket", "검색→종목→[모의 주문] 3탭 · 같은 리스크 엔진 · 전략 장부와 분리", "ticket.py", "test_manual_ticket_fail_closed_and_place"),
    ("PORTFOLIO", "Thesis", "왜 샀나·무엇이 틀리면 판다·목표·무효화·점검일 → 30분마다 감시·알림", "thesis.py", "test_thesis_validation_breach_and_alert"),
    ("PORTFOLIO", "Portfolio OS", "내 자산·주식/현금·업종 집중·최대 위험 한 문장 · 전략·위기 시뮬레이션", "portfolio_os.py", "test_lab_validation_portfolio_personal_failure"),
    ("OPERATIONS", "Validation tracker", "KIS 모의·WebSocket·실측 슬리피지·Forward·장기 — 진행률과 다음 할 일", "validation.py", "test_lab_validation_portfolio_personal_failure"),
    ("OPERATIONS", "Sentinel", "데이터 수집·AI 작업·스케줄러·DB·증권사 5분 감시 · 나빠짐/회복 알림", "sentinel.py", "test_sentinel_and_briefing_text"),
]


def _live(app) -> dict:
    """실행 중 확인 가능한 항목의 상태 (항목 이름 → (status, detail))."""
    from .clock import clock_status
    out: dict[str, tuple[str, str]] = {}
    now = datetime.now(UTC)
    cs = clock_status(now)
    k, u = cs["markets"]["KRX"], cs["markets"]["US"]
    cal_ok = cs["calendar_ok"]
    for name in ("KRX calendar", "US calendar", "holiday"):
        out[name] = ("live" if cal_ok else "bad", cs["calendar_source"])
    out["market open/closed"] = ("live", f"KRX {k['phase']} · US {u['phase']}")
    out["next open"] = ("live", f"KRX {k['next_open_kst']} · US {u['next_open_kst']}")
    out["next close"] = ("live", f"KRX {k['next_close_kst']} · US {u['next_close_kst']}")
    out["timezone"] = ("live", f"{k['tz']} {k['local_time']} · {u['tz']} {u['local_time']}")
    out["Market clock"] = out["market open/closed"]
    tr = ops.get_state(app.engine, "truth")
    for sec in tr.get("sections") or []:
        for c in sec["checks"]:
            st = {"ok": "live", "warn": "warn", "bad": "bad", "na": "impl"}[c["status"]]
            for key, item in (("fresh_bar_kr", "stale detection"), ("quality", "data quality"), ("corporate_actions", "corporate actions"),
                              ("pit", "PIT"), ("secondary", "primary/secondary source"), ("idempotency", "Idempotency"),
                              ("partial", "Partial fill"), ("slippage", "Slippage"), ("reconcile", "Reconciliation"),
                              ("validation", "KIS"), ("ws", "WebSocket")):
                if c["key"] == key:
                    out[item] = (st, c["detail"])
    rd = ops.get_state(app.engine, "readiness")
    if rd:
        out["Readiness"] = ("live", f"{rd.get('status')} · {rd.get('as_of')}")
        out["Fail closed"] = ("live", "게이트 적용 장부에서 점검 없음/실패 시 매수 차단" + (" · 지금 차단 중" if rd.get("status") == "NOT_READY" else ""))
    cal = ops.get_state(app.engine, "event_calendar")
    kinds = {e["kind"] for e in cal.get("events") or [] if e.get("d_day", -1) >= 0}
    for item, ks in (("FOMC", {"fomc"}), ("NFP", {"nfp"}), ("options", {"options_expiry", "quad_witching"}), ("rebalance", {"index_rebalance"}),
                     ("earnings", {"earnings"}), ("dividend", {"ex_div", "div_pay"}), ("disclosure", {"disclosure"}), ("CPI", {"cpi"})):
        if kinds & ks:
            out[item] = ("live", f"다가오는 {sum(1 for e in cal.get('events') or [] if e['kind'] in ks and e.get('d_day', -1) >= 0)}건")
    if cal.get("events"):
        est = sum(1 for e in cal["events"] if e.get("estimated"))
        out["estimated flag"] = ("live", f"추정 표시 {est}건")
        out["event risk"] = ("live", f"보유·관심 {len(cal.get('risk') or [])}종목 점수")
    hb = ops.get_state(app.engine, "heartbeat")
    if hb.get("at"):
        age = (now - datetime.fromisoformat(hb["at"])).total_seconds()
        out["Scheduler"] = ("live" if age < 300 else "warn", f"마지막 심장박동 {age / 60:.0f}분 전")
    wd = ops.get_state(app.engine, "watchdog")
    if wd:
        out["Watchdog"] = ("live", f"재시작 {wd.get('restarts', 0)}회 · 마지막 {wd.get('last', {}).get('event')}")
    rc = ops.get_state(app.engine, "recovery")
    if rc:
        out["Restart"] = ("live" if (rc.get("db") or {}).get("ok") else "bad", f"직전 정지 {rc.get('downtime_s') or 0:.0f}초 · DB {'정상' if (rc.get('db') or {}).get('ok') else '문제'}")
    ev = ops.get_state(app.engine, "evaluation")
    if ev:
        out["Forward evaluation"] = ("live" if ev.get("status") != "worse" else "warn", ev.get("verdict") or "")
    dr = ops.get_state(app.engine, "drift")
    if dr:
        out["Drift"] = ("live" if dr.get("status") in ("stable",) else "warn", dr.get("message") or "")
    rec, _ = app.active_model()
    out["Champion"] = ("live", f"{rec.name}@{rec.version}") if rec else ("warn", "챔피언 없음 (후보가 게이트 탈락)")
    ks = ops.get_state(app.engine, "kill_switch")
    out["Kill switch"] = ("live", "ON — " + (ks.get("reason") or "") if ks.get("on") else "대기 (조건 감시 중)")
    star = ops.get_state(app.engine, "starred").get("symbols") or []
    out["Watchlist · holdings"] = ("live", f"관심종목 ★ {len(star)}개 · 매일 AI 판단 대상")
    # v16
    dh = ops.get_state(app.engine, "data_health")
    if dh.get("at"):
        out["DATA HEALTH"] = ("live" if dh.get("trading") == "OK" else "warn", f"{dh.get('overall')}% · {dh.get('trading')}" + (f" — {dh['block_reason']}" if dh.get("block_reason") else ""))
    sn = ops.get_state(app.engine, "sentinel")
    if sn.get("at"):
        bad = [c["title"] for c in sn.get("checks") or [] if c["status"] == "bad"]
        out["Sentinel"] = ("live" if not bad else "warn", "정상" if not bad else "문제: " + ", ".join(bad))
    fl = ops.get_state(app.engine, "failure_lab")
    if fl.get("n"):
        out["Failure research"] = ("live", f"틀린 예측 연구 {fl['n']}건 · 약점 패턴 {len(fl.get('findings') or [])}개")
    try:
        from .validation import progress
        v = progress(app)
        out["Validation tracker"] = ("live" if v["overall"] >= 0.8 else "warn", v["headline"])
    except Exception as e:  # noqa: BLE001
        out["Validation tracker"] = ("warn", f"{type(e).__name__}: {e}")
    if app.settings.broker != "kis":
        for item in ("KIS", "WebSocket", "Orderbook", "Reconciliation"):
            out.setdefault(item, ("setup", "KIS 키 설정 후 확인 (모의투자)"))
    return out


def evaluate(app) -> dict:
    try:
        live = _live(app)
    except Exception as e:  # noqa: BLE001
        live = {}
        err = f"{type(e).__name__}: {e}"
    else:
        err = None
    rows = []
    for area, name, what, where, test in ITEMS:
        st, detail = live.get(name, ("impl", ""))
        rows.append({"area": area, "item": name, "what": what, "where": where, "test": test, "status": st, "detail": detail})
    by = {}
    for r in rows:
        a = by.setdefault(r["area"], {"n": 0, "live": 0, "impl": 0, "setup": 0, "warn": 0, "bad": 0})
        a["n"] += 1
        a[r["status"]] += 1
    return {"at": datetime.now(UTC).isoformat(), "items": rows, "areas": by, "error": err,
            "total": {"n": len(rows), "done": sum(1 for r in rows if r["status"] in ("live", "impl")),
                      "setup": sum(1 for r in rows if r["status"] == "setup"),
                      "problems": sum(1 for r in rows if r["status"] in ("warn", "bad"))}}


def markdown() -> str:
    L = ["# 완성 기준 체크리스트", "", "각 항목: 무엇을 · 어디 · 검증 테스트. 실행 중 상태는 대시보드 **Truth Center → 완성 기준** 에서 실시간으로 본다.", ""]
    area = None
    for a, name, what, where, test in ITEMS:
        if a != area:
            L += ["", f"## {a}", "", "| 항목 | 무엇을 | 어디 | 검증 |", "|---|---|---|---|"]
            area = a
        L.append(f"| ✓ {name} | {what} | `{where}` | `{test}` |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":  # pragma: no cover
    print(markdown(), end="")

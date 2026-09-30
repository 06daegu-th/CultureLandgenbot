# 완성 기준 체크리스트

각 항목: 무엇을 · 어디 · 검증 테스트. 실행 중 상태는 대시보드 **Truth Center → 완성 기준** 에서 실시간으로 본다.


## DATA

| 항목 | 무엇을 | 어디 | 검증 |
|---|---|---|---|
| ✓ 정확한 timestamp | 모든 기록 UTC 저장 · 화면 KST · 기준 시각(AS-OF) 칩 | `asof.py` | `test_asof_labels_and_bar_staleness_respect_calendar` |
| ✓ primary/secondary source | KRX 1차 · Yahoo 2차(빈 날만, 겹치는 날 비율로 정렬) · 실시간 KIS→네이버→Yahoo | `data/sources.py` | `test_secondary_source_fills_only_gap_days` |
| ✓ stale detection | 휴장일을 반영한 'N거래일 밀림' · SLA 별 fresh/stale/old | `asof.py · readiness.py` | `test_readiness_seven_gates_and_buy_block` |
| ✓ data quality | 급변·0값·결측·정지 검사 (수집 때 + Truth Center 재검사) | `data/quality.py · truth.py` | `test_truth_center_sections` |
| ✓ corporate actions | KRX 전일대비로 수정주가 복원 · 미설명 ±45% 급변 탐지 | `data/collectors/marcap.py · truth.py` | `test_split_is_adjusted_away` |
| ✓ PIT | 월말 시총 상위 유니버스 (상장폐지 포함) · 누수 감사 L6 | `pipeline.universe_at · review/leakage.py` | `test_leakage_audit_catches_future_news_and_feature_lookahead` |
| ✓ Per-stock trust | 종목별 데이터 신뢰도: 기준일(거래일 밀림) · 1차/2차 출처 · 품질 · 거래정지 의심 · AI 판단 나이 | `stock.trust` | `test_trust_flags_stale_secondary_and_halt` |

## MARKET

| 항목 | 무엇을 | 어디 | 검증 |
|---|---|---|---|
| ✓ KRX calendar | holidays XKRX (대체공휴일·선거일·연말) | `clock.py` | `test_exchange_calendar_holidays_and_special_sessions` |
| ✓ US calendar | holidays XNYS | `clock.py` | `test_exchange_calendar_holidays_and_special_sessions` |
| ✓ holiday | 휴장 이름 표시 · 휴장일 주문 없음 | `clock.py` | `test_exchange_calendar_holidays_and_special_sessions` |
| ✓ early close | 미국 조기 폐장 13:00 · KRX 새해 10시 개장 | `clock.session` | `test_exchange_calendar_holidays_and_special_sessions` |
| ✓ market open/closed | 시장별 장전·장중·장외 · 상단 시장 시계 | `clock.phase · desk.js` | `test_market_clock_next_open_close` |
| ✓ next open | 다음 개장 시각 · 남은 시간 | `clock.next_open` | `test_market_clock_next_open_close` |
| ✓ next close | 다음 폐장 시각 · 남은 시간 | `clock.next_close` | `test_market_clock_next_open_close` |
| ✓ timezone | Asia/Seoul · America/New_York (서머타임 자동) | `clock.py` | `test_market_clock_next_open_close` |

## EVENT

| 항목 | 무엇을 | 어디 | 검증 |
|---|---|---|---|
| ✓ earnings | Yahoo·Nasdaq·DART 일정 · D-day · 소스 충돌 탐지 | `data/fundamentals.py · truth.py` | `test_event_calendar_builds_every_kind_with_sources` |
| ✓ FOMC | 연준 공표 일정 | `engines/events.py` | `test_event_calendar_builds_every_kind_with_sources` |
| ✓ CPI | FRED 공식 발표일 (키 있으면) | `engines/events.py` | `test_fred_release_dates_parse` |
| ✓ NFP | FRED 공식일 → 없으면 첫째 금요일(추정 표시) | `engines/events.py` | `test_event_calendar_builds_every_kind_with_sources` |
| ✓ dividend | 배당락·지급일 · 계좌별 배당 일정 | `engines/events.py · accounts.py` | `test_account_tax_rules` |
| ✓ disclosure | DART 공시 · 원문 요약 · 이벤트 추출 | `engines/events.py · event_extract.py` | `test_event_extraction_types_amounts_materiality` |
| ✓ options | KOSPI200 만기·동시만기 · 미국 만기·쿼드러플 위칭 · 옵션 내재변동 | `engines/events.py · options.py` | `test_event_calendar_builds_every_kind_with_sources` |
| ✓ rebalance | KOSPI200 정기변경 · MSCI 리뷰 · S&P 분기 | `engines/events.py` | `test_event_calendar_builds_every_kind_with_sources` |
| ✓ estimated flag | 규칙으로 추정한 날짜는 '추정' 표시 | `engines/events.py` | `test_event_calendar_builds_every_kind_with_sources` |
| ✓ event risk | 이벤트 위험 점수 · 실적 D-1 매수 ×0.5 · 금통위/FOMC 고베타 ×0.75 | `engines/events.py · risk.py` | `test_risk_engine_gates_block_and_shrink_buys_only` |

## AI

| 항목 | 무엇을 | 어디 | 검증 |
|---|---|---|---|
| ✓ Quant | 차트·Quant AI (학습 모델) | `analysts/analysts.py` | `test_multi_ai` |
| ✓ News | 뉴스 AI · 뉴스 에이전트 | `analysts · agents.py` | `test_agents_graph` |
| ✓ Macro | 경제·시장 AI · 매크로 에이전트 | `analysts · agents.py` | `test_agents_graph` |
| ✓ Risk | Risk AI 거부권 (희석되지 않음) | `analysts · ensemble/engine.py` | `test_risk_llm_cannot_override_rule_veto` |
| ✓ Earnings | 공시·실적 AI · 실적 서프라이즈 모델 | `analysts · engines/earnings.py` | `test_earnings_model_posterior_and_reactions` |
| ✓ Ensemble | 성적 가중 합의 · 충돌도 | `ensemble/engine.py` | `test_track_record_shifts_weight` |
| ✓ Calibration | Platt 보정 · 신뢰도 곡선 · Readiness 관문 | `ensemble/calibration.py` | `test_calibration` |
| ✓ Drift | PSI + KS · 이력 | `engines/drift.py` | `test_drift_ks_confirms_psi` |
| ✓ Per-AI decay | AI 별 성능 저하 자동 감지 (같은 날 판단은 일별로 묶어 검정) → 알림 · AI Health | `review/decay.py · health.py` | `test_per_ai_decay_alert_and_health` |
| ✓ Forward evaluation | 사전 등록 + SPRT 전진 검증 · 독립 평가기 | `review/power.py · evaluator.py` | `test_preregistration_is_sealed_and_forward_test_counts_only_after` |

## MODEL

| 항목 | 무엇을 | 어디 | 검증 |
|---|---|---|---|
| ✓ Walk-forward | 20일마다 재학습 · 표본 외 | `backtest/backtester.py` | `test_backtest_runs_and_trains_only_on_past` |
| ✓ Purged | 라벨 기간이 시험 구간과 겹치는 학습 표본 제거 | `backtest/backtester.py (cutoff = t − horizon − embargo)` | `test_purged_walk_forward_never_trains_on_test_labels` |
| ✓ Embargo | 라벨 뒤 1봉 간격 · 누수 감사 L5 | `backtest/backtester.py` | `test_purged_walk_forward_never_trains_on_test_labels` |
| ✓ DSR | 시도 횟수 보정 샤프 | `backtest/stats.py` | `test_psr_dsr_distinguish_luck_from_skill` |
| ✓ PSR | 확률적 샤프 | `backtest/stats.py` | `test_psr_dsr_distinguish_luck_from_skill` |
| ✓ Bootstrap | 신호 수익 부트스트랩 구간 · 파산 위험 블록 부트스트랩 | `review/evaluator.py · trading/ruin.py` | `test_statistics_helpers` |
| ✓ Champion | 게이트 통과 모델만 챔피언 | `registry/model_registry.py` | `test_gate_rejects_unlucky_and_fragile_models` |
| ✓ Challenger | 도전자 모델 섀도 채점 | `pipeline.decide · evaluate_shadow_models` | `test_challenger_scored_silently_and_promoted_only_through_gate` |
| ✓ Rollback | 챔피언 성과 이상 시 자동 롤백 | `pipeline.check_champion · guardian` | `test_champion_rollback_restores_previous` |

## PORTFOLIO

| 항목 | 무엇을 | 어디 | 검증 |
|---|---|---|---|
| ✓ Correlation | 상관 행렬 · 묶음(클러스터) | `trading/portfolio_risk.py` | `test_portfolio_risk_v2_has_every_lens` |
| ✓ Sector | WICS 우선 업종 노출 · 사전 게이트 | `trading/portfolio_risk.py` | `test_pretrade_gate_caps_sector_cluster_and_var` |
| ✓ Concentration | HHI · 유효 종목 수 | `trading/portfolio_risk.py` | `test_portfolio_risk_v2_has_every_lens` |
| ✓ VaR | 과거·정규·CF·EWMA·10일 · 성분 VaR | `trading/portfolio_risk.py` | `test_portfolio_risk_v2_has_every_lens` |
| ✓ ES | ES 95/99 | `trading/portfolio_risk.py` | `test_portfolio_risk_v2_has_every_lens` |
| ✓ Stress | 실제 위기 재생 + 가정 시나리오 | `trading/portfolio_risk.py` | `test_portfolio_risk_v2_has_every_lens` |
| ✓ Risk-of-Ruin | 블록 부트스트랩 1년 | `trading/ruin.py` | `test_risk_of_ruin_monte_carlo` |
| ✓ Position sizing | 반켈리 · 변동성 목표 · 한도 × 이벤트 × 준비 상태 | `trading/trade_plan.py` | `test_trade_plan_sizing_entry_and_invalidation` |

## TRADING

| 항목 | 무엇을 | 어디 | 검증 |
|---|---|---|---|
| ✓ KIS | 시세·주문·체결·취소·잔고 · 검증 스위트 | `trading/kis.py · kis_check.py` | `test_kis_validation_suite_steps` |
| ✓ WebSocket | H0STCNT0 체결 · PINGPONG · 재접속 | `trading/kis_ws.py` | `test_kis_realtime_session_with_local_server` |
| ✓ Orderbook | H0STASP0 10단계 호가 | `trading/kis_ws.py` | `test_parse_orderbook_and_latest_book` |
| ✓ Partial fill | 호가 잔량 부분체결 · 미체결 잔량 취소 | `trading/kis.py · exec_sim.py` | `test_shadow_broker_fills_against_depth` |
| ✓ Cancel | 제한 시간 뒤 잔량 취소 · 재시작 시 정리 | `trading/kis.py` | `test_partial_fill_remainder_is_cancelled` |
| ✓ Reconciliation | 증권사 잔고 = 진실 · 불일치 기록 | `trading/kis.py · pipeline.reconcile_live` | `test_db_drift_is_corrected_from_broker_balance` |
| ✓ Idempotency | client_order_id 유일 · 재시작 복구 | `pipeline.recover_orders` | `test_restart_recovers_and_cancels_open_order` |
| ✓ Slippage | 실측 → 고정분·충격 계수 보정 (50건+) | `trading/slippage.py` | `test_slippage_calibration_recovers_and_shrinks` |
| ✓ Market impact | 제곱근 법칙 · 상한 150bp | `trading/portfolio.py` | `test_square_root_market_impact` |

## SAFETY

| 항목 | 무엇을 | 어디 | 검증 |
|---|---|---|---|
| ✓ Kill switch | 수동 · 자동 10조건 · HALTED | `trading/guardian.py` | `test_guardian_halts_on_duplicate_orders_and_blocks_all_trading` |
| ✓ Readiness | 7관문 + Truth Center 연결 | `readiness.py` | `test_readiness_seven_gates_and_buy_block` |
| ✓ Fail closed | 점검 없음·실패·캘린더 없음 → 신규 매수 차단 | `readiness.buy_block` | `test_fail_closed_when_readiness_or_calendar_missing` |
| ✓ Pre-trade gate | 업종·묶음·VaR 사전 게이트 · 이벤트 배수 | `trading/risk.py` | `test_risk_engine_gates_block_and_shrink_buys_only` |
| ✓ Pre-trade simulator | 종목 페이지에서 실제 매매와 같은 게이트로 '지금 사면?' 시험 (주문 없음) | `stock.pretrade` | `test_pretrade_mirrors_trading_gates` |
| ✓ Daily loss | 일 손실 한도 · 1.5배면 자동 정지 | `trading/risk.py · pipeline` | `test_risk_blocks_buys_after_daily_loss_but_allows_sells` |
| ✓ Broker failure | 연속 실패 → HALTED · BROKER 관문 | `guardian · readiness` | `test_guardian_conditions_volatility_loss_broker` |
| ✓ Calendar failure | 휴장일 캘린더 없으면 EVENT 빨강 | `truth.market_clock` | `test_fail_closed_when_readiness_or_calendar_missing` |
| ✓ Data failure | 일봉 N일 밀림 → 매매 중단 · DATA 관문 | `pipeline._stale_guard · readiness` | `test_readiness_seven_gates_and_buy_block` |

## OPERATIONS

| 항목 | 무엇을 | 어디 | 검증 |
|---|---|---|---|
| ✓ Scheduler | 장중/장외 작업표 · 백오프 | `scheduler.py` | `test_scheduler_has_24h_observation_jobs` |
| ✓ Watchdog | 스케줄러 죽음·멈춤(심장박동 5분) → 재시작 | `watchdog.py` | `test_watchdog_decisions` |
| ✓ Restart | 재시작 복구 · 따라잡기 | `recovery.py` | `test_recovery_startup_catch_up_and_source_health` |
| ✓ Backup | SQLite 백업 · 무결성 · 7개 보관 | `data/backup.py` | `test_sqlite_backup_is_compressed_verified_and_rotated` |
| ✓ DB migration | Alembic 0001~0005 · ensure_columns | `migrations · data/db.py` | `test_old_database_gets_new_columns` |
| ✓ Health check | /api/health · DB 점검 | `web/server.py · recovery.db_check` | `test_web_security_headers_and_health` |
| ✓ Alerts | 토스트·알림센터·텔레그램·웹 푸시 | `alerts.py · ops.Notifier · webpush.py` | `test_alert_routing_to_external_channels` |

## UX

| 항목 | 무엇을 | 어디 | 검증 |
|---|---|---|---|
| ✓ Stock search | 이름·별칭·티커 검색 · 최근 본 종목 · '/' 단축키 | `web/static/pro.js · truth.js` | `test_search_korean_aliases_and_global` |
| ✓ Chart | 캔들 + AI 진입 구간·손절·목표·추격 금지 선 + 과거 AI BUY/SELL 표시 | `web/static/app.js · truth.js chartPlanLines` | `e2e 스크린샷` |
| ✓ Events | D-day · 이벤트 캘린더 · 오늘 할 일에 보유·관심 종목 D-3 | `web/static/desk.js · ux.today` | `test_event_calendar_builds_every_kind_with_sources` |
| ✓ Market clock | 상단 시장 시계 (장중·장외·휴장 · 개장/폐장까지 남은 시간, 1초마다) | `web/static/truth.js` | `test_market_clock_next_open_close` |
| ✓ AS-OF | 모든 화면 기준 시각 칩 | `asof.py · web/static/desk.js` | `test_asof_labels_and_bar_staleness_respect_calendar` |
| ✓ AI explanation | AI 별 의견 · 근거 추적(Evidence Chain) · 봉인된 판단 그대로 | `explain.for_symbol` | `test_explain_for_symbol_uses_sealed_record` |
| ✓ Why BUY | 찬성·반대(영향 순)·기준·무효화 가격 | `explain.py` | `test_explain_why_actions` |
| ✓ Why SELL | 찬성·반대·기준·BUY 가 되려면 | `explain.py` | `test_explain_why_actions` |
| ✓ Why NO TRADE | 막은 것(거부권·충돌·관문·이벤트) · 무엇이 바뀌면 | `explain.py` | `test_explain_why_actions` |
| ✓ Why bought / not bought | 코어 순위·점수 · 거부 · 매수 불가 · 주문·차단 기록으로 실제 매매 설명 | `stock.story` | `test_story_explains_bought_and_not_bought` |
| ✓ News digest | 뉴스·공시 자동 요약 (7일 톤 · 중요 헤드라인 · 공시 원문 요약 · 추출 이벤트) | `stock.digest` | `test_digest_and_events` |
| ✓ Portfolio risk | 리스크 2.0 화면 · 종목 비교의 상관 행렬 | `web/static/desk.js · ux.compare` | `test_portfolio_risk_v2_has_every_lens` |
| ✓ System status | Truth Center · 완성 기준 · 매매 준비 · 관제실 | `truth.py · checklist.py` | `test_truth_center_sections` |
| ✓ Watchlist · holdings | 관심종목 별표(매일 AI 판단) · 종목 페이지에 내 보유(장부+계좌) · 종목별 과거 AI 적중률 | `ux.py` | `test_star_holdings_track_today_compare` |
| ✓ Today · compare | 오늘 할 일 · 시장 한눈에 · 종목 비교(2~4개) | `ux.py · web/static/truth.js` | `test_star_holdings_track_today_compare` |
| ✓ Customization | 홈 편집(숨기기·순서) · 외부 알림 종류별 채널 · 조용한 시간 (서버 저장) | `prefs.py · alerts._route` | `test_prefs_validate_and_route_notifications` |
| ✓ Convenience | 모바일 · 다크/라이트 · 키보드 단축키(?) · 최근 검색 | `web/static` | `e2e 스크린샷` |

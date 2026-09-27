# Quant AI 아키텍처

## 1. 전체 흐름

```text
                         ┌────────────────────────────┐
                         │ Market Data (PostgreSQL)    │
                         │ 시세·호가·체결·뉴스·공시·거시 │
                         └─────────────┬──────────────┘
                                       │  MarketContext (as_of 이후 정보 차단)
                                       │  + RAG: 과거 유사 사례 · 과거 판단과 결과
      ┌──────────────┬─────────────────┼─────────────────┬──────────────────┐
      ↓              ↓                 ↓                 ↓                  ↓
  Primary AI      NVIDIA AI        Quant Model       Market Regime       Risk AI
  종합 분석        독립 검증         숫자·통계          국면              반대 논거 찾기
  (Claude)        뉴스·거시·시나리오 (champion 모델)   (규칙)            규칙 veto + LLM
      │              │                 │                 │                  │
      └──────────────┴────────┬────────┴─────────────────┴──────────────────┘
                              ↓   서로의 의견을 보지 않음 (독립성)
                   ┌──────────────────────┐
                   │   Ensemble Engine     │  로그오즈 가중 결합
                   │   · 과거 성적 가중      │  가중치 = 사전 × 수축 적중률 × 자기 확신
                   │   · 충돌 탐지          │  분산 + 강한 반대 의견
                   │   · veto 는 희석 불가   │
                   └──────────┬───────────┘
                              ↓
               BUY / SELL / HOLD / NO_TRADE  (신호일 뿐, 주문 아님)
                              ↓
                   ┌──────────────────────┐
                   │   Risk Gate           │  킬스위치 · 일 손실 · 종목 비중 · 총노출×국면배수
                   │   (RiskEngine)        │  · 1회 주문액 · 주문 횟수 · 현금 · 공매도 금지
                   └──────────┬───────────┘
                              ↓  Order Intent
                   ┌──────────────────────┐
                   │   Execution Engine    │  목표 비중 → 주문 수량 (매도 먼저)
                   └──────────┬───────────┘
                ┌─────────────┼──────────────┐
                ↓             ↓              ↓
           PaperBroker   ShadowBroker    LiveBroker
           가상 체결      호가 기준 '만약'  증권사 API (안전장치 통과 시만)
                              ↓
                   Trade Journal (모든 신호·주문·거부 사유)
                              ↓
          장 마감 후: 채점 → AI 성적표 → 복기 → 교훈을 RAG 메모리에 저장
```

## 1-1. 코어-위성 (실데이터 연구 이후 기본 전략)

실제 KRX 데이터 연구([RESEARCH_KRX.md](RESEARCH_KRX.md)) 결과, 머신러닝이 단순 팩터를 이기지 못했다.
그래서 **돈의 대부분은 검증된 팩터 코어가 굴리고, 멀티 AI 는 역할을 좁혀 작게 시작**한다.

| 구성 | 비중 | 결정 주체 | AI 역할 |
|---|---|---|---|
| 코어 | 80% | 팩터 점수 (연구소와 같은 `engines/factors.py`) | 종목 고유 위험 거부권, 상장폐지 즉시 청산 |
| 위성 | 20% | 멀티 AI 합의 BUY (신뢰도 ≥ 60) | 종목 선택 자체 |

- 코어 매수는 AI 확률 최소치 검사를 받지 않는다 (AI 가 고른 종목이 아니므로). 위성만 받는다.
- **시장 전체 위험(위기 국면·FOMC)은 코어를 건드리지 않는다.** 실데이터 재생에서 위기 국면 판정이 코어 20종목을
  전부 청산하는 문제가 발견되어 분리했다 — 국면 기반 노출 조절은 연구에서 성과를 악화시켰다.
- AI 기여도: `attr-core`(AI 없음) / `attr-veto`(+거부권) / `attr-full`(+위성) 가상 장부를 같은 가격·같은 코어로 운용해
  "AI 가 수익을 더했는가"를 전진(forward) 데이터로 측정한다.
- **추세 필터:** 코어 리밸런싱 날 KOSPI 가 200일 이동평균 아래면 코어 비중 × 0.5 (나머지 현금). 결정은 다음 리밸런싱까지
  유지된다 (`cs:{mode}.trend_scale`). 연구소 `simulate(index_close=...)` 와 같은 규칙 ([RESEARCH_KRX.md](RESEARCH_KRX.md) 8장).
- **소액 계좌 대체:** 1주 가격 > (평가금액 × 코어비중 / 20) × 1.5 인 종목은 다음 순위로 대체해 20종목을 채운다.

### 실전 운용 보조

| 모듈 | 역할 |
|---|---|
| `strategy/order_sheet.py` · `QuantAI.order_sheet()` | 보유 종목·현금 → 매도/매수 수량·지정가 가이드·비용. 다른 증권사·ISA 수동 매매용 (CLI `orders`, `POST /api/order-sheet`, 대시보드 '리밸런싱 주문표') |
| `strategy/health.py` · `QuantAI.strategy_health()` | 실제 자산곡선을 16년 백테스트 분포(`REFERENCE`)와 비교: 낙폭·1년 수익·KOSPI 대비·변동성·회복 기간·팩터 IC t값 → 정상/주의/위험. 스케줄러 `strategy_health` 가 상태 변화 시 알림 (CLI `checkup`) |
| `research_lab.Costs.historical()` | 연도별 실제 증권거래세(0.30% → 0.15% → 0.20%)로 백테스트 비용 계산 |
| `QuantAI.add_cashflow()` · `time_weighted_index()` | 입출금 기록 → 건강검진은 시간가중수익률로 판정 (CLI `cashflow`) |
| `QuantAI._stale_guard()` | 주가 데이터가 `QUANT_MAX_DATA_AGE_DAYS` 영업일보다 오래되면 실시간 사이클 매매를 건너뛰고 하루 한 번 알림 |
| `cli doctor` · `run.sh` | 실행 전 점검(설정·키·DB·데이터 최신성·marcap·AI 응답·KIS·알림), 설치~운영 명령 모음 |
| `data/collectors/marcap.sync_marcap()` | 최근 5년 파일만 sparse clone / 갱신 (해 바뀌면 새해 파일 추가). 스케줄러 `krx_data`·`krx_bootstrap` |

## 1-2. 무료 멀티 AI 구성

키가 있는 공급자만 역할에 자동 배정한다 (`analysts.assign_roles`). 역할마다 다른 회사 모델 → 오류가 겹치지 않게.

| 역할 (analyst 이름) | 선호 순서 | 비고 |
|---|---|---|
| Primary (`primary`) | Claude(유료 키 있을 때) → Gemini → Groq → NVIDIA → Cloudflare | 종합 판단 |
| Second (`nvidia`) | NVIDIA → Groq → Gemini → Cloudflare | 독립 검증. 성적표 호환을 위해 이름 유지 |
| Risk (`risk`) | Groq → NVIDIA → Gemini → Cloudflare | 공급자가 모자라면 다른 역할과 공유 |
| Panel (`panel`) | Cloudflare → Groq → NVIDIA → Gemini | 남는 공급자가 있을 때만. 앙상블 사전 가중치 0.7 |

- `OpenAICompatClient` 하나로 모든 공급자 처리 (`FREE_PROVIDERS`: base URL · 모델 목록 · 기본 일 한도 · 분당 한도).
- 429(한도 초과)·404(모델 없음) → 목록의 다음 모델, 그 모델은 그날 제외. 성적표는 1순위 모델 이름(`backend_id`)으로 유지해
  대체 모델이 답해도 성적이 초기화되지 않는다.
- `GuardedLLM`: 캐시(기본 12시간 — 일봉이라 같은 날 같은 입력은 재사용) · 유료 공급자 비용 예산 · **무료 공급자 일 호출 한도**
  (넘으면 그 AI 만 기권, 시스템은 계속) · 감사 로그.
- AI 에는 종목 가격·국면·뉴스·공시·거시 요약만 보낸다. 계좌·보유 정보는 보내지 않는다.

## 1-3. 신뢰 계층 (실제 돈을 굴리기 위한 것)

| 모듈 | 역할 |
|---|---|
| `trading/journal.py` · `ExecutionEngine` · `KISBroker.recover` · `QuantAI.recover_orders` | 멱등 키(`client_order_id`) → 제출 전 기록(pending) → 주문번호 즉시 기록(submitted) → 확정. 재시작 시 미완료 주문 조회·취소, 번호 없는 주문은 unknown + 킬스위치 |
| `ensemble/calibration.py` | 신뢰도 곡선 · Brier · LogLoss · ECE, AI 별 Platt 보정 (review 때 적합, decide 때 적용) |
| `trading/portfolio_risk.py` · `RiskEngine.adv` · `_apply_var_budget` | VaR/ES · 베타 · 스트레스 · 상관 군집 · 유동성 · 통화 / 주문 ≤ ADV 5% / 계획 VaR 한도 |
| `engines/market_intel.py` | 뉴스 이벤트 클러스터링 · RISK ON/OFF 점수 · 크로스에셋 상관·베타 |
| `ensemble/tracker.evidence_snapshot` · `review/evidence.py` | 판단 시점 재료 저장 · Evidence Chain · AI Journal · NO TRADE 가치 |
| `data/quality.py` · `QuantAI._stale_guard` | 미래 시각 · 거래정지 · 급변 · 공백 / 낡은 데이터로 매매 금지 |
| `data/db.ensure_columns` · `migrations/versions/0002` | 예전 DB 자동 업그레이드 (SQLite) · Alembic (PostgreSQL) |

## 2. 왜 이렇게 설계했나 (제안 반영 + 개선점)

| 제안 | 구현 | 추가 개선 |
|---|---|---|
| NVIDIA 를 '두 번째 의견'으로 | `nvidia` 애널리스트, 독립 검증 프롬프트 | 같은 입력·다른 역할 프롬프트, 서로의 결론 비공개 |
| 역할 분리 (종합/검증/숫자/반대) | `primary`/`nvidia`/`quant`/`risk` + `regime` | Risk AI 는 **규칙 veto(하드) + LLM(소프트)** 이중 구조. LLM 이 규칙 veto 를 뒤집지 못함 |
| CONSENSUS SIGNAL + 충돌 탐지 | `EnsembleEngine.combine` | 단순 평균 대신 **로그오즈 가중**, 방향 의견 2개 미만이면 NO_TRADE |
| 과거 성능 반영 | `ensemble/tracker.py` | **수축 추정**(표본 30개 전까지 50% 로 당김) — 운 좋은 10연속 적중에 휘둘리지 않음 |
| 모델별·카테고리별 성적표 | 방향·뉴스·거시·추세·위험경고 별 채점 | 거시 판단은 종목이 아니라 **시장 수익률**로 채점, 위험경고는 **경고를 낸 경우만** 채점 |
| AI 에게 주문 권한 X | 애널리스트는 `Opinion` 만 반환 | 테스트로 강제(`test_ai_never_touches_broker_directly`), 구조화 출력 스키마로 자유 텍스트 명령 차단 |
| RAG (nemotron embed) | `analysts/memory.py` | 키 없으면 로컬 해싱 임베딩. 검색도 `as_of` 이전 문서만 (백테스트 누수 방지). 복기 교훈도 메모리에 저장 |
| 검증된 모델만 교체 | `registry/model_registry.py` | candidate → 백테스트 게이트 → shadow(**challenger 로 조용히 채점**) → Shadow 게이트 → champion |

### 추가로 넣은 것
- **Challenger 모델**: 새 모델은 앙상블에 참여하지 않고 매일 예측만 기록 → 실전 성적이 쌓이면 자동 승격 판정.
- **데이터 품질 veto**: 시세 정지, 6σ 이상 급변은 Risk AI 가 하드 veto ("API 데이터 이상?" 체크).
- **이벤트 임박 veto**: `artifacts/events.json` 의 중요 이벤트(FOMC, 실적발표 등) 24시간 이내면 신규 진입 금지.
- **국면 노출 배수**: CRISIS 0 / 변동성 하락 0.25 / … / 안정 상승 1.0 — 리스크 엔진의 총노출 한도에 곱해짐.
- **킬스위치**: 대시보드 버튼 한 번으로 신규 매수 즉시 중단 (매도·위험 축소는 허용).
- **Live 다중 안전장치**: `QUANT_LIVE_ENABLED` + 확인 문구 + 소액 상한 + Shadow 통과 champion — 하나라도 없으면 예외.

## 3. 미래 정보 누수(look-ahead) 방지 규칙
1. 피처는 t 종가까지만 사용 (테스트: 미래 데이터를 바꿔도 과거 피처 불변).
2. 신호는 t 종가에 생성, 체결은 t+1 시가.
3. 재학습 시 라벨이 확정된 행만 (s + horizon ≤ t) + embargo 1봉.
4. 뉴스 감성 피처는 하루 지연 반영.
5. RAG 검색, 컨텍스트 조립 모두 `as_of` 이전 정보만.
6. 외부 데이터(실적 등)는 **공개 시점** 기준 as-of merge (`features.asof_merge`).

## 4. 모드

| 모드 | 하는 일 | 주문 |
|---|---|---|
| research | 멀티 AI 합의 신호 생성 | ✗ |
| predict | + 다음 거래일 강세/기준/약세 시나리오 | ✗ |
| paper | 가상매매 | PaperBroker |
| shadow | 실주문 파이프라인 그대로, 호가 기준 '만약' 체결 기록 | ShadowBroker |
| live | 실계좌 (소액부터) | LiveBroker — 증권사 API 구현 필요 |
| review | 채점·복기·교훈 도출 | ✗ |

## 5. 24시간 스케줄 (`scheduler.py`)

| 장중 (KRX 09:00–15:30 KST / US 09:30–16:00 ET) | 장외 |
|---|---|
| 뉴스 5분, 판단→매매 15분 | 뉴스 30분, 공시 10분, 경제지표 3시간 |
| (실시간 가격·호가·체결은 `RealtimeFeed` 구현 후 연결) | 결과 채점·복기 6시간, Shadow 평가 12시간, 후보 모델 재학습 24시간 |

## 6. 비용 · 운영 메모
- LLM 호출은 종목 × 판단 주기만큼 발생한다. 관심 종목(`QUANT_WATCHLIST`)을 좁히고, 장중 주기를 조절할 것.
- Primary 는 `claude-opus-5` + adaptive thinking + 서버측 refusal fallback(`fallbacks="default"`) 사용.
  비용을 줄이려면 `QUANT_PRIMARY_EFFORT=medium` 부터 시도.
- NVIDIA Developer Program 무료 엔드포인트는 프로토타이핑·연구·테스트 용도로 안내되어 있다.
  실제 돈이 걸린 Live 에 쓰기 전 최신 약관(Production 은 AI Enterprise 라이선스 필요 여부)을 확인할 것.

## 7. 운영 · 보안 계층

| 계층 | 구현 |
|---|---|
| LLM 가드 | 입력 해시 캐시(TTL) · 일 예산(USD) 초과 시 기권 · `llm_calls` 감사 로그(모델·지연·토큰·비용·응답) |
| 프롬프트 주입 방어 | 외부 텍스트를 '비신뢰 데이터'로 명시 · 길이 제한 · JSON 스키마 구조화 출력 · 값 클리핑 · 다른 AI 와 앙상블 |
| 매매 사이클 | 모드별 잠금(PostgreSQL advisory lock) · 당일 주문 수 영속 · 소액 상한(budget ratio) · 종목별 오류 격리 |
| 킬스위치 | DB 공유 상태(`system_state`) · 대시보드/CLI/자동(일 손실 1.5배) · 모든 변경 알림 |
| Live | 증권사 잔고 = 진실의 원천 · 보호 지정가 · 체결 폴링 · 잔량 취소 |
| 모델 게이트 | Brier(기저율 대비) · 횡단면 IC · IC t-stat · PSR · DSR · 비용 2배 스트레스 · MDD · champion 대비 개선 · Shadow |
| 데이터 | 수집 시 품질검사 → 문제 행 제거 · 경고는 운영 화면/알림 |
| 웹 | Host 화이트리스트(DNS rebinding) · JSON+Origin 검사(CSRF) · CSP/X-Frame-Options · 토큰 · 경로 탐색 차단 |
| 관측 | `job_runs` · `/api/health` · 운영 화면 · 웹훅 알림 · JSON 로그 |

## 8. 다음 단계
1. KIS 모의투자 계좌로 실제 검증 → 실시간 웹소켓(`RealtimeFeed`), 해외주식 주문.
2. 실적·재무 데이터 수집 → `features.asof_merge` 로 공시일 기준 결합.
3. pgvector 로 RAG 메모리 이전 (대량 뉴스).
4. 멀티모달 보조 AI (차트 이미지·IR 자료) — `Analyst` 를 하나 더 구현해 `build_analysts` 에 추가.
5. 확률 보정(isotonic) 및 국면별 모델 분리.

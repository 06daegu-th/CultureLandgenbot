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

## 7. 다음 단계
1. 증권사 API 연동 (예: 한국투자증권 KIS): `RealtimeFeed`(웹소켓 시세/호가/체결), `LiveBroker.submit`.
2. 실적·재무 데이터 수집 → `features.asof_merge` 로 공시일 기준 결합.
3. pgvector 로 RAG 메모리 이전 (대량 뉴스).
4. 멀티모달 보조 AI (차트 이미지·IR 자료) — `Analyst` 를 하나 더 구현해 `build_analysts` 에 추가.
5. 확률 보정(isotonic) 및 국면별 모델 분리.

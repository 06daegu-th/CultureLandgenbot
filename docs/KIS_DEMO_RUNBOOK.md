# KIS 모의투자 연결 · 운영 절차서

> 목표: 한국투자증권 **모의투자 계좌**로 코어-위성 전략을 실제 주문 경로로 돌려
> 슬리피지·체결·장애 대응을 검증하고, AI 기여도를 전진(forward) 성과로 쌓는다.
> 실제 돈은 들어가지 않는다. 실전 전환은 맨 아래 체크리스트를 모두 통과한 뒤에만.

## 0. 어디서 돌릴까

| 위치 | 적합성 |
|---|---|
| **본인 PC / 상시 켜진 미니PC / 클라우드 VM (권장)** | `docker compose up -d` 로 24시간. 재부팅 시 자동 재시작 |
| Claude Code 클라우드 세션 | 개발·테스트용. 일정 시간 미사용 시 컨테이너가 회수되고, 기본 네트워크 정책이 KIS 도메인을 막을 수 있음 |

## 1. 준비물 (한국투자증권)

1. 한국투자증권 계좌 개설 (비대면 가능)
2. KIS Developers(apiportal.koreainvestment.com) → **Open API 서비스 신청** → **모의투자 신청**
3. 모의투자용 **App Key / App Secret** 발급, **모의투자 계좌번호**(8자리-2자리) 확인
4. 모의투자 초기 자금은 증권사 설정값 (전략의 `QUANT_INITIAL_CASH` 는 가상 장부용이며, LIVE 는 증권사 잔고를 그대로 사용)

## 2. 환경 설정 (`.env` — 채팅·저장소에 키를 올리지 말 것)

```bash
QUANT_BROKER=kis
KIS_ENV=demo                      # 모의투자. 실전은 real (아래 체크리스트 통과 후)
KIS_APP_KEY=...                   # 모의투자 App Key
KIS_APP_SECRET=...
KIS_ACCOUNT=12345678-01
QUANT_STRATEGY=core_satellite
# 권장 시작: AI 오버레이 없이 팩터 코어 100% (LLM 비용 0). AI 는 나중에 켠다
QUANT_CORE_ONLY=true
QUANT_MARCAP_DIR=/data/marcap/data  # 장 마감 후 자동 git pull + DB 갱신
QUANT_DISCORD_WEBHOOK=https://...   # 체결·긴급청산·장부불일치·작업실패 알림 (권장)
```

네트워크: `openapivts.koreainvestment.com:29443`(모의), `openapi.koreainvestment.com:9443`(실전),
`github.com`(marcap 데이터) 에 나갈 수 있어야 한다.

## 3. 최초 점검 (장중에 실행)

`run.sh` 로 한 번에: `./run.sh setup` → `.env` 입력 → `./run.sh data` → `./run.sh doctor --kis --notify` →
`./run.sh kis-check` → `./run.sh cycle` → `./run.sh start` (또는 Docker `./run.sh up`). 아래는 같은 일을 직접 할 때.

```bash
quant-ai collect krx --marcap-dir /data/marcap/data --years 3 --top 100   # 실제 KRX 데이터 적재
quant-ai kis-check                    # 토큰 → 잔고 → 삼성전자 현재가/호가 (주문 없음)
quant-ai kis-check --test-order       # 모의 전용: 체결 안 될 가격 1주 주문 → 조회 → 즉시 취소
quant-ai cycle --mode live --core-only   # 코어 전용 1사이클: 잔고대조 → 팩터 코어 → 리스크 → 주문 → 체결확인
quant-ai cycle --mode live            # (나중에) 코어-위성: + AI 거부권·위성·AI 기여도 장부
```

`QUANT_LIVE_MAX_CAPITAL`(소액 상한)은 **실전 계좌에만** 적용된다. 모의투자는 계좌 전체(예: 1억 원)로 운용한다.

### 리허설 결과 (실제 KRX 데이터 + 로컬 가짜 KIS 서버, 2026-09-23 종가 기준, 모의계좌 1억 원)

실제 모의계좌 대신 가짜 서버를 쓴 것 외에는 위 명령과 똑같은 경로다 (`tests/kis_mock.py`).

| 단계 | 결과 |
|---|---|
| `kis-check --test-order` | 토큰 → 잔고 → 현재가·호가 → 체결 안 될 가격 1주 주문 → 조회 → 취소 정상 |
| 1차 `cycle --core-only` | 코어 20종목 리밸런싱, 20건 체결, 투자 비중 98.5% (현금 153만 원) |
| 2차 `cycle --core-only` | 이미 목표 비중 → 추가 주문 0건 (중복 매수 없음) |
| 장부 대조 | DB 장부 = 증권사 잔고, 모의 TR(`VTTC0012U` 매수 · `VTTC0013U` 취소)만 사용 |

코어 20종목: 신한지주, 하나금융지주, KT&G, KB금융, S-Oil, 우리금융지주, 에이피알, 한국타이어앤테크놀로지, GS, 대한항공,
DB손해보험, 셀트리온, 아모레퍼시픽, 삼성화재, LG유플러스, KT, 가온전선, 코웨이, LG, HMM (KOSPI ≥ 200일선 → 축소 없음).

`cycle` 출력의 코어 20종목과 증권사 앱(모의투자)의 보유 종목이 일치하는지 눈으로 확인한다.

## 4. 상시 운영

```bash
docker compose up -d --build        # PostgreSQL + 마이그레이션 + 스케줄러 + 대시보드
# 또는
quant-ai run --mode live            # KIS_ENV=demo 면 실전 안전장치 없이 모의 계좌로 실행
```

스케줄 (KST):

| 작업 | 시간 | 내용 |
|---|---|---|
| core_satellite | 장중 09:10~15:10, 1시간마다 | 잔고 대조 → 코어(20거래일마다 리밸런싱) · AI 거부권 · 긴급청산 · 위성 → 주문 |
| krx_data | 장외 6시간마다 | marcap `git pull` → 수정주가·유니버스 갱신 |
| review | 장외 | 채점·복기·교훈 → 알림 |
| strategy_health | 장외 12시간마다 | 실제 성과가 과거 검증 범위를 벗어나면 (상태 변화 시) 알림 — `quant-ai checkup` |
| shadow_eval / retrain | 장외 | 모델 게이트 |

시가 직후(09:00~09:10) 급변과 종가 동시호가(15:20~) 에는 주문하지 않는다.

## 5. 매일 확인할 것 (대시보드)

- **코어-위성** 화면: 코어 구성, 거부·긴급청산, 위성, **AI 기여도 장부 비교**
- **운영 · 시스템**: 작업 실패, LLM 비용, 데이터 품질, 킬스위치
- **거래 · 리스크 로그**: 거부 사유, 브로커 오류, **장부 불일치(reconcile)** 기록
- 증권사 모의투자 앱과 대시보드 보유 종목이 다르면 → 시스템이 자동으로 증권사 기준으로 맞추고 알림을 보낸다. 반복되면 원인 조사.

## 6. 안전장치 요약

- 주문은 **보호 지정가** (매수: 매도1호가 +0.5% 이내, 호가단위 반올림). 10초 내 미체결 잔량은 **자동 취소**.
- 계획 전에 **증권사 잔고로 장부 동기화** — DB 가 틀어져도 없는 주식을 팔거나 중복 매수하지 않음.
- 같은 장부의 매매 사이클은 동시에 하나만 (DB 잠금).
- 킬스위치(대시보드/CLI) → 신규 매수 즉시 중단. 일 손실 한도 1.5배 초과 시 자동 ON.
- 모의 TR(`VTTC…`) 만 사용되는지 테스트로 보증 (`tests/test_kis_live.py`).

## 7. 알려진 한계

- 모의투자 체결은 실제 시장과 다를 수 있다 (체결 우선순위·슬리피지). 슬리피지 추정은 참고용.
- 1주 가격이 종목당 목표 금액의 1.5배를 넘는 종목은 다음 순위로 자동 대체된다. 그래도 정수 주 반올림 때문에 3,000만 원 미만 계좌는 비중 오차가 커진다 ([INVESTOR_GUIDE.md](INVESTOR_GUIDE.md) 2장).
- 해외주식 주문, 실시간 웹소켓 시세는 미구현 (국내 REST 시세 사용).
- KIS 응답 필드(`askp1` 등)는 공식 예제 기준으로 구현했으나 **첫 실행에서 `kis-check` 출력으로 확인**할 것.

## 8. 실전(KIS_ENV=real) 전환 체크리스트

- [ ] 모의투자 **최소 3개월** 무사고 운영 (작업 실패·장부 불일치·주문 오류 알림 원인 모두 해소)
- [ ] 모의 실측 슬리피지로 백테스트 재실행 → 여전히 양의 Sharpe
- [ ] (AI 를 켤 경우만) AI 기여도 장부: `attr-veto`·`attr-full` 이 `attr-core` 대비 6개월 이상 나쁘지 않음 — 아니면 `QUANT_CORE_ONLY=true` 유지
- [ ] `QUANT_LIVE_ENABLED=true`, `QUANT_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY`, `QUANT_LIVE_MAX_CAPITAL`=감당 가능한 소액
- [ ] Shadow 게이트를 통과한 champion 모델 존재 (Quant 애널리스트용)
- [ ] 알림 채널 연결, 킬스위치 동작 확인
- [ ] 본인 계좌 외 제3자 자금 운용 금지 (투자일임업 등록 필요 — PRODUCTION_READINESS.md 4장)

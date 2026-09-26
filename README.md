# Quant AI — 멀티 AI 합의 기반 퀀트 시스템

여러 AI 가 **서로 모른 채 독립적으로** 시장을 분석하고, 앙상블 엔진이 **과거 성적으로 가중**해 합의 신호를 만든다.
AI 에게 주문 권한은 없다. 신호는 반드시 **리스크 게이트**를 통과해야 주문이 되며, 매일 장이 끝나면
**어떤 AI 가 왜 틀렸는지 자동 복기**하고 그 성적이 다음 날 가중치에 반영된다.

![dashboard](docs/dashboard.png)

> 스크린샷은 `quant-ai demo` 의 **가상 데이터**입니다 (실제 시세·뉴스 아님).

```text
Market Data ─┬─▶ Primary AI (Claude · 종합 분석)        ─┐
             ├─▶ NVIDIA AI (Nemotron · 독립 검증)        ─┤
             ├─▶ Quant Model (champion · 숫자/통계)      ─┼─▶ Ensemble ─▶ Risk Gate ─▶ Execution ─▶ Broker
             ├─▶ Market Regime (국면)                    ─┤   충돌 탐지      킬스위치      Paper/Shadow/Live
             └─▶ Risk AI (사지 말아야 할 이유 · veto)     ─┘   성적 가중      손실·비중 한도
장 마감 후 ─▶ 실제 결과로 채점 ─▶ AI 성적표(방향·뉴스·거시·추세·위험) ─▶ 복기 교훈 ─▶ RAG 메모리
```

- 설계: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 실제 KRX 데이터 연구: [docs/RESEARCH_KRX.md](docs/RESEARCH_KRX.md)
- **KIS 모의투자 연결·운영 절차: [docs/KIS_DEMO_RUNBOOK.md](docs/KIS_DEMO_RUNBOOK.md)**
- **서비스 출시 준비도 점검 · 부족한 점 · 법규제 · 로드맵: [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md)**

## 구성 (요청한 16단계 대응)

| # | 단계 | 위치 |
|---|---|---|
| 1 | 국내 + 해외 시세 수집 | `data/collectors/prices.py` (Yahoo, CSV, 실시간 `RealtimeFeed` 인터페이스) |
| 2 | 뉴스/공시/경제 데이터 | `collectors/news.py` (RSS), `disclosures.py` (DART), `macro.py` (FRED) |
| 3 | PostgreSQL 저장 | `data/models.py` (JSONB), `docker-compose.yml` |
| 4 | Market Regime Engine | `engines/regime.py` |
| 5 | News Intelligence | `engines/news_intel.py` + LLM 애널리스트 |
| 6 | Technical/Fundamental Feature | `engines/features.py` (공시일 기준 as-of merge) |
| 7 | Prediction Engine | `engines/prediction.py`, `engines/scenario.py` |
| 8 | Backtester | `backtest/backtester.py` (walk-forward, purge/embargo, 비용·세금) |
| 9 | Paper Trading | `trading/broker.py: PaperBroker` |
| 10 | Shadow Trading | `ShadowBroker` (호가 기준 체결, 잔량 부족 시 부분체결) |
| 11 | Risk Engine | `trading/risk.py` + Risk AI (`analysts/analysts.py`) |
| 12 | Broker Execution | `trading/execution.py` (모든 모드 공통) |
| 13 | Trade Journal | `trading/journal.py` (신호·주문·거부 사유 전부) |
| 14 | 자동 복기 | `review/review.py`, `ensemble/tracker.py` |
| 15 | 검증된 모델만 교체 | `registry/model_registry.py` (candidate→shadow→champion) |
| 16 | 소액 Live | `trading/kis.py` 한국투자증권(모의/실전) — 보호 지정가·체결확인·잔량취소·잔고동기화, 소액 상한 |

### 운영 기능
- **LLM 가드**: 응답 캐시 · 일 비용 예산 · 모든 호출 감사 로그 (`analysts/guard.py`)
- **검증 통계**: PSR · DSR(다중검정 보정) · Sharpe 부트스트랩 CI · 비용 2배 스트레스 · 확률 보정표
- **안전장치**: DB 공유 킬스위치 · 자동 정지(급락) · 치명 위험 시 강제 청산 · 매매 사이클 잠금 · 영속 주문 한도
- **관측**: 작업 실행 기록, `/api/health`, 운영 화면, Discord/Slack/Telegram 알림, JSON 로그
- **배포**: Dockerfile, docker-compose(PostgreSQL+마이그레이션+스케줄러+웹), Alembic, GitHub Actions CI

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[ai,dev]"          # PostgreSQL: .[postgres]  /  Yahoo 시세: .[yahoo]

quant-ai demo                       # 가상 데이터로 전체 파이프라인 1회 실행 (약 30초)
quant-ai serve                      # http://127.0.0.1:8050
pytest                              # 테스트 67개 (PostgreSQL: QUANT_TEST_DATABASE_URL 지정 시 통합 테스트 포함)
```

### 실제 데이터로

```bash
cp .env.example .env                # 키 입력 (ANTHROPIC_API_KEY, NVIDIA_API_KEY, DART_API_KEY, FRED_API_KEY …)
docker compose up -d --build        # PostgreSQL + 마이그레이션 + 스케줄러 + 대시보드 (또는 아래처럼 수동)
alembic upgrade head                # 스키마 마이그레이션
quant-ai collect prices --symbols 005930.KS,000660.KS,NVDA,AAPL,^KS11 --years 5
quant-ai collect macro && quant-ai collect disclosures
quant-ai train                      # walk-forward 검증 → 게이트 통과 시 shadow
quant-ai decide                     # 멀티 AI 합의 신호 확인 (주문 없음)
quant-ai run --mode shadow          # 24시간: 장중 판단/Shadow 매매, 장외 수집·채점·복기·재학습
quant-ai kis-check --test-order     # 한국투자증권 모의투자 점검 (docs/KIS_DEMO_RUNBOOK.md)
quant-ai kill on                    # 킬스위치 (모든 프로세스 공유)
quant-ai health                     # 헬스체크
```

### 코어-위성 전략 (기본 전략)

```text
코어 80%  검증된 팩터 (모멘텀 + 저변동성 + 52주 고점, 20거래일 리밸런싱, 상위 20 동일가중)
          └ AI 역할: 종목 고유 위험 거부권 (신규 편입 대신 다음 순위) · 상장폐지 시 즉시 청산
위성 20%  멀티 AI 합의 BUY 상위 5 (AI 종목선택을 작은 비중으로 실전 검증)
          └ 시장 전체 위험(위기 국면·FOMC 임박)에는 위성 신규 매수 중단
AI 기여도  가상 장부 3개(코어만 / +거부권 / +위성)를 같은 가격으로 나란히 굴려 비교
```

```bash
quant-ai collect krx --marcap-dir marcap/data --years 3 --top 100   # 실제 KRX 데이터 적재
quant-ai cycle --mode paper                                         # 한 사이클
quant-ai replay --days 60                                           # 최근 60거래일 재생
quant-ai run --mode shadow                                          # 24시간 (QUANT_STRATEGY=core_satellite)
```

### 실제 KRX 데이터로 연구 (생존편향 제거)

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/FinanceData/marcap.git
cd marcap && git sparse-checkout set --no-cone $(for y in $(seq 2010 2026); do printf "/data/marcap-$y.parquet "; done) && cd ..
quant-ai research krx --marcap-dir marcap/data --start 2010 --top 100 --register
```

- 월말 시가총액 상위 N 을 **그 시점 기준**으로 선정 (나중에 상장폐지된 종목 포함), 보통주만
- KRX 전일대비(기준가 대비)로 **수정주가 복원** (액면분할·병합 자동 반영)
- 시도한 모든 설정을 공개하고 DSR 로 다중검정 보정, KOSPI·유니버스 동일가중 두 벤치마크와 비교
- 결과는 대시보드 **실데이터 연구** 화면에서 확인

API 키가 없으면 해당 AI 는 오프라인 휴리스틱으로 대체되어 시스템은 계속 동작한다 (대시보드 설정 화면에 표시).

## 안전 원칙
- AI → 신호 → 앙상블 → **리스크 게이트** → 실행 엔진 → 증권사. AI 는 `broker.buy()` 를 호출할 수 없다.
- Risk AI 의 규칙 veto (이벤트 임박, 변동성 급증, 데이터 이상, 위기 국면, 상장폐지 공시)는 다른 AI 가 뒤집을 수 없다.
- Live 는 `QUANT_LIVE_ENABLED=true` + `QUANT_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY` + 소액 상한 + Shadow 검증 champion 이 모두 있어야 켜진다.
- NVIDIA 무료 엔드포인트는 연구·테스트 용도로 안내되어 있으니 Live 전에 최신 약관을 확인할 것.
- 이 프로젝트는 투자 조언이 아니며, 백테스트/가상 성과는 실제 수익을 보장하지 않는다.

---
<sub>`main.py` 는 이 저장소의 이전 디스코드 봇 코드이며 Quant AI 와 무관합니다.</sub>

# `.env` 값 — 어디서 발급하나

`./run.sh` 가 처음 실행될 때 `.env.example` 을 복사해 `.env` 를 만든다 (권한 600, git 에 올라가지 않음).
값을 넣은 뒤 다시 `./run.sh` 를 실행하면 반영된다.
**키는 `.env` 에만 넣고 채팅·이슈·스크린샷에 붙여 넣지 않는다.** 넣은 뒤 `./run.sh doctor` 로 확인
(`--ai` 는 각 AI 에 짧은 테스트 요청, `--kis` 는 잔고 조회, `--notify` 는 알림 테스트).

## 우선순위

| 단계 | 필요한 것 | 없으면 |
|---|---|---|
| 1. 코어 전략 (필수) | KRX 데이터 — `./run.sh data` 가 자동으로 받음 | 전략 자체가 안 돈다 |
| 2. 모의투자 자동매매 | KIS 모의투자 키 3개 | 가상매매(PAPER)·주문표만 가능 |
| 3. 알림 (강력 권장) | Discord / Slack / Telegram 중 하나 | 체결·장애를 모름 |
| 4. 무료 멀티 AI (선택) | Gemini · Groq · NVIDIA · Cloudflare 중 원하는 만큼 | 휴리스틱 (코어 전용이면 영향 없음) |
| 5. 추가 데이터 (선택) | DART · FRED · 뉴스 RSS | AI 가 공시·거시·뉴스를 못 봄 |

## 1. KRX 데이터 — 키 없음

`./run.sh data` 가 [FinanceData/marcap](https://github.com/FinanceData/marcap) 에서 최근 5년 파일만 받아
`QUANT_MARCAP_DIR` 을 채우고 DB 에 적재한다. 이후에는 스케줄러가 매일 갱신한다 (Docker 는 컨테이너가 직접 받음).

## 2. 한국투자증권 (KIS) — `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT`

1. 한국투자증권 계좌 개설 (앱 비대면)
2. 홈페이지·앱에서 **모의투자 신청** → 모의투자 계좌번호 → `KIS_ACCOUNT=12345678-01`
3. **KIS Developers** (apiportal.koreainvestment.com) → API 신청 → **App Key / App Secret**
   - 모의투자 키와 실전 키는 **따로** 발급된다. 지금은 모의투자 키 + `KIS_ENV=demo`
4. `QUANT_BROKER=kis`, `QUANT_MODE=live` (KIS_ENV=demo 이면 실제 돈이 아닌 모의계좌로 주문)

## 3. 알림 (하나 이상)

| 변수 | 발급 |
|---|---|
| `QUANT_DISCORD_WEBHOOK` | 디스코드 서버 설정 → 연동 → 웹후크 → 새 웹후크 → URL 복사 |
| `QUANT_SLACK_WEBHOOK` | api.slack.com/apps → Create App → Incoming Webhooks 켜기 → Add New Webhook |
| `QUANT_TELEGRAM_TOKEN` · `QUANT_TELEGRAM_CHAT_ID` | @BotFather 에 `/newbot` → 토큰. 봇에 메시지를 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates` 의 `chat.id` |

## 4. 무료 멀티 AI — 한도가 있는 무료 등급만으로 구성

키가 있는 공급자만 자동으로 역할에 배정되고, 역할마다 **다른 회사의 모델**이 맡도록 한다 (관점 분산).

| 역할 | 기본 공급자 | 기본 모델 (앞에서부터 시도) | 발급 |
|---|---|---|---|
| Primary (종합 판단) | **Google Gemini** | gemini-pro-latest → flash-latest → flash-lite-latest (항상 최신 세대, 막히면 목록에서 자동 탐색) | aistudio.google.com → Get API key → `GEMINI_API_KEY` |
| Second (독립 검증) | **NVIDIA NIM** | nemotron-3-super-120b | build.nvidia.com → 로그인 → Get API Key (`nvapi-…`) → `NVIDIA_API_KEY` |
| Risk (거부권) | **Groq** | openai/gpt-oss-120b → llama-3.3-70b-versatile | console.groq.com → API Keys → `GROQ_API_KEY` |
| Panel (교차검증) | **Cloudflare Workers AI** | @cf/openai/gpt-oss-120b → llama-3.3-70b → gemma-3-12b (계정에서 막힌 모델은 자동으로 건너뜀) | dash.cloudflare.com → AI → Workers AI → **Use REST API** → "Workers AI" 권한 토큰 → `CLOUDFLARE_API_TOKEN`, 같은 화면의 Account ID → `CLOUDFLARE_ACCOUNT_ID` |

- 키가 하나뿐이어도 된다: Gemini 하나면 Primary 와 Risk 를 Gemini 가 맡고, Second 는 휴리스틱.
- 유료 Claude 키(`ANTHROPIC_API_KEY`)가 있으면 Primary 는 Claude, 나머지 무료 공급자가 다른 역할을 맡는다.
- 역할을 직접 정하려면 `QUANT_AI_ROLES=primary=gemini,second=groq,risk=nvidia,panel=cloudflare`

**무료 한도 관리 (자동)**
- 같은 날 같은 입력(일봉·뉴스 변화 없음)은 재호출하지 않는다 (`QUANT_LLM_CACHE_MINUTES=720`).
  코어-위성은 후보 약 40종목을 보므로 역할당 하루 약 40~80회.
- 공급자가 한도 초과(429)나 없는 모델(404)을 돌려주면 **다음 모델로 자동 전환**하고, 그 모델은 그날 다시 쓰지 않는다.
- 시스템이 스스로 멈추는 하루 호출 수: gemini 300 · groq 800 · nvidia 1000 · cloudflare 150
  (`QUANT_<공급자>_DAILY_LIMIT` 로 조정). 넘으면 그 AI 만 기권하고 나머지로 계속 돈다.
- 분당 호출 간격도 공급자별로 둔다 (gemini 8회/분 등).
- 무료 한도·모델 이름은 공급자가 수시로 바꾼다. 모델이 없어졌다면 `QUANT_GEMINI_MODELS=` 등에 새 이름을 넣는다
  (`./run.sh doctor --ai` 가 역할별로 실제 응답을 확인한다).

**주의:** 무료 등급은 대부분 개발·테스트 용도이며, 입력 내용이 서비스 개선에 쓰일 수 있다 (예: Gemini 무료 등급).
개인 계좌 정보는 AI 에 보내지 않는다 (AI 는 종목 가격·뉴스·공시 요약만 받는다). 실전 전에 각 약관을 확인한다.

## 5. 추가 데이터 (무료)

| 변수 | 발급 | 쓰임 |
|---|---|---|
| `DART_API_KEY` | opendart.fss.or.kr → 인증키 신청 | 공시 (상장폐지 등 위험 감지 → 긴급 청산) |
| `FRED_API_KEY` | fred.stlouisfed.org → My Account → API Keys | 미국 금리·환율·VIX 등 거시 |
| `QUANT_NEWS_FEEDS` | 키 없음. 언론사 RSS 주소를 쉼표로 | 뉴스 |

## 6. 직접 정하는 값

| 변수 | 값 |
|---|---|
| `DATABASE_URL` | 기본 `sqlite:///quant_ai.db` (설치 불필요). PostgreSQL 주소가 있는데 서버가 꺼져 있으면 `./run.sh` 가 SQLite 로 전환하고 `.env` 백업을 남긴다. Docker 는 자동으로 PostgreSQL |
| `POSTGRES_PASSWORD` | Docker 사용 시 비밀번호 (`change-me` 그대로면 `./run.sh up` 이 무작위로 만들어 저장) |
| `QUANT_WEB_TOKEN` | 대시보드 비밀번호: `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `QUANT_SERVICE_LEVEL` | 서비스 단계 표시 (기본 `personal` = 본인 계좌·본인 PC). 다른 값이면 규제·라이선스 화면이 상용 불가 데이터 소스를 경고 — docs/COMPLIANCE.md |
| `QUANT_CORE_ONLY` | `true` 권장 (AI 기여도가 검증되기 전) |
| `QUANT_MAX_DATA_AGE_DAYS` | 데이터가 이 영업일 수보다 오래되면 자동매매 중단 (기본 5) |
| `QUANT_SELL_TAX_BPS` | 매도세 (2026년 20 = 0.20%) — 세법 바뀌면 수정 |
| `QUANT_LIVE_*` | 실전 전환 때만 (docs/KIS_DEMO_RUNBOOK.md 8장) |

## 7. V17 추가 값 (모두 선택)

| 변수 | 값 |
|---|---|
| `QUANT_WEB_PASSWORD_HASH` | 로그인 비밀번호 해시 — `./run.sh auth-setup --password` 가 만들어 준다 (비밀번호 자체는 저장하지 않음) |
| `QUANT_WEB_TOTP_SECRET` | 2단계 인증(구글 OTP 등) 비밀 — `auth-setup` 이 QR 주소와 함께 만든다 |
| `QUANT_WEB_VIEWER_TOKEN` | 읽기 전용 토큰 (조회만, 버튼·주문 불가) — `auth-setup --viewer` |
| `QUANT_AI_AUTO_DEMOTE` | 기본 `true` — AI 가 3일 연속 UNTRUSTED 면 주문에서 자동 제외 (SHADOW) |
| `QUANT_AI_QUIET_THROTTLE` | 기본 `false` — 조용한 장 규칙이 검증되면 켜는 AI 매수 축소 |
| `QUANT_US_COMMISSION_BPS` | 미국 주문표 수수료 (편도, 기본 25 = 0.25%) — 내 증권사 수수료로 |
| `QUANT_FX_SPREAD_BPS` | 환전 스프레드 (기본 25 = 0.25%, 환율 우대 반영해서) |
| `QUANT_US_TAX_RATE` | 해외주식 양도세율 (기본 0.22) — 세법 바뀌면 수정 |
| `QUANT_US_TAX_DEDUCTION` | 연 기본공제 (기본 2500000) |
| `QUANT_TRUSTED_PROXIES` | 리버스 프록시 주소(쉼표). 이 주소에서 온 요청만 X-Forwarded-For·Proto 를 믿는다 (로그인 잠금이 실제 접속 IP 기준이 되게) |
| `QUANT_WEB_SECURE_COOKIE` | `1` 이면 세션 쿠키에 항상 Secure — 외부 공개 시 HTTPS 와 함께 필수 |
| `QUANT_SEC_USER_AGENT` | SEC EDGAR 수집용 연락처 (예: `홍길동 me@example.com`) — SEC 정책상 필요 |

'내 투자 한도'(원금·최대 손실)는 `.env` 가 아니라 화면(`#budget`)이나 `./run.sh budget ... --save` 로 정한다 — DB 에 저장되고 다음 시작 때도 적용된다.

## 8. V18 추가 값 (모두 선택)

| 변수 | 값 |
|---|---|
| `QUANT_LOGOS` | 기본 켜짐 — 회사 로고를 공개 이미지(미국: FMP · 그 외: 회사 홈페이지 파비콘)에서 받아 `artifacts/logos` 에 저장. `off` 면 네트워크를 쓰지 않고 이니셜 아이콘만. 원하는 로고는 `artifacts/logos/custom/<종목코드>.png` 로 직접 넣으면 그게 우선 |

뉴스·공시 **번역·쉬운 설명**은 새 키가 필요 없다 — 이미 넣은 LLM 키(`GEMINI_API_KEY` 등)를 쓴다. LLM 키가 없으면 규칙 기반 설명(용어 풀이·중요 숫자·과거 비슷한 뉴스 뒤 평균 움직임)만 나오고 번역은 비어 있다.

## 9. V19 추가 값 (모두 선택)

| 변수 | 값 |
|---|---|
| `QUANT_LOGO_SOURCES` | 로고 출처와 순서 (기본 `toss,alpha,fmp,favicon`). 국내: 토스증권·알파스퀘어 공개 아이콘(실제 회사 로고) → 홈페이지 아이콘, 미국: FMP. 예: `favicon` 만 쓰려면 `QUANT_LOGO_SOURCES=favicon` |
| `QUANT_EVENT_GATE` | 기본 `smart` — 실적 발표 D-1 이내는 신규 매수 보류(×0), D-3 이내 ×0.75. `reduce`(예전: D-1 ×0.5) · `block`(걸리면 모두 ×0) · `off` |

화면 설정(쉬운 화면 / 전체 화면, '처음이라면' 안내 숨기기)은 `.env` 가 아니라 설정 화면이나 왼쪽 메뉴 아래 [쉬운 | 전체] 버튼으로 바꾼다 — 서버에 저장돼 PC·휴대폰이 같다.


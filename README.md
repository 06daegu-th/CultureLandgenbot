# Quant AI — 멀티 AI 합의 기반 퀀트 시스템

여러 AI 가 **서로 모른 채 독립적으로** 시장을 분석하고, 앙상블 엔진이 **과거 성적으로 가중**해 합의 신호를 만든다.
AI 에게 주문 권한은 없다. 신호는 반드시 **리스크 게이트**를 통과해야 주문이 되며, 매일 장이 끝나면
**어떤 AI 가 왜 틀렸는지 자동 복기**하고 그 성적이 다음 날 가중치에 반영된다.

![dashboard](docs/dashboard.png)

> 실제 KRX 데이터(2026-09-23 기준)로 가상매매 장부를 돌린 화면입니다. LLM 키 없이 휴리스틱 AI 로 실행했고,
> 뉴스·해외지표 칸은 키(RSS·FRED)를 넣으면 채워집니다.

**이 플랫폼이 답하는 질문**
- 데이터가 과거 시점에서 정확했나
- AI 확률이 실제 확률과 맞나
- AI 가 코어 대비 추가 수익을 만들었나
- 비용을 빼도 남나
- 실제 주문에서도 같은가
- 반복되나

국내·미국 모두 이 **증명 체인 6단계**로 판정해 홈 최상단에 보여준다.
자동 킬스위치 10개 조건(→ HALTED), champion 자동 롤백, 사이트 채팅 AI 도 들어 있다.

> **v17 사용 원칙 — AI 는 '실수 방지 도구'다.** 먼저 `#budget` 에서 원금·최대 손실을 정하면 모든 한도가 자동으로 정해지고,
> 누적 손실이 그 한도에 닿으면 전체 정지한다. 대부분은 코어(지수형)로, AI 신호는 소액 위성에서 6개월 이상 전진 기록이 기준을 넘을 때만.
> AI 성적이 3일 연속 기준 미달이면 시스템이 스스로 주문에서 뺀다. 미국 주식은 아직 **수동 주문표**(`#usorder`)다.
> 뉴스는 '읽는 목록' 대신 **뉴스 보드**(`#news`)·**증시 지도**(`#map`)·**그날 재현**(`#replay`)으로 본다.

> **v31 — 목표 계획.** 목표 화면의 '추천 계획' 한 번이면 **200만원 + 매달 100만원 → 1억(7년)** 이 설정된다 (지수 ETF 적립 · 7년 안 확률 약 68% · AI 자동매매는 검증을 넘은 뒤 전체의 15% 까지만).
> 해마다 '이쯤이면 정상' 범위와 비교해 계획대로 가는지 알려 주고, 실제 계좌에서 AI 는 자기가 산 종목만 사고판다 (적립 ETF 는 건드리지 않음). `quant goal-plan apply|show`.

> **v21 — 차분한 화면.** 쉬운 화면은 우리말(매수·관망·쉬어가기)과 색 점으로만 보여 주고, 홈 맨 위에는 내 자산과 목표가 나온다.
> 목표 화면에서 **일반 · ISA · 연금저축** 중 어디에 넣는 게 유리한지 확률로 비교하고, 월 적립을 **지수 ETF** 로 바로 살 수 있다(모의 장부 자동 · 실계좌는 주문표 알림).
> 문제가 생기면 `./run.sh report` 결과를 그대로 보내면 된다 (키·계좌번호·금액은 들어가지 않는다).

> **v30 — AI 자동매매 · 목표 현실성 · 회사 이해 · 로고 큐 · 지표.**
> **AI 자동매매**(`./run.sh autopilot` · 화면 'AI 자동매매'): 신호 엔진 2.0 점수로 매일 장중 한 번 스스로 사고판다 — 점수 +0.8 이상 매수 · 0 이하·손절선·20거래일 경과 시 매도 · 최대 5종목 · 종목당 18% · 시장이 200일선 아래면 자리 절반 · 더 좋은 후보가 있으면 하나 교체 · 결정과 이유를 매일 봉인.
> 가상 100만원 장부로는 지금 바로 자동 운용하고, **실제 계좌는 관문 5개**(전진 기록 · 가상 장부 40거래일+ 코스피 초과 · KIS 전 과정 검증 · 증명 프로젝트(실계좌) · 사용자 켬)를 모두 넘어야 연결된다.
> **목표 현실성**(`./run.sh autopilot goal`): 같은 규칙을 '보지 않은 기간'에 돌린 일별 수익으로 6·12개월을 4,000번 시뮬레이션해 100만원 → 1억 확률 · 두 배 확률 · -15% 정지 확률 · 중간값을 그대로 보여 준다.
> **회사 이해**(종목 화면 '회사' 탭): 초보자 3줄(좋은 점·걱정할 점·앞으로 볼 일정) · 한 줄 소개 · DART 재무 5년(매출·영업이익·순이익·영업이익률·부채비율) · PER/PBR/배당 · 같은 업종 비교 · 이슈 타임라인.
> 그 밖: 종목 차트 **코스피와 비교**(점선) · **로고 관리**(진짜 로고 없는 종목 → 바로 올리기) · `/metrics`(응답 시간·작업 실패·데이터 밀림 — Prometheus) · 무거운 화면 4분마다 미리 계산 · 쉬운 화면 위 막대 정리('확인할 것').

> **v29 — 1단계 마무리: 데이터 정합성 · KIS 전 과정 · 100만원 증명 프로젝트 · 차트.**
> **데이터 점검**(`./run.sh datacheck` · 화면 '데이터 점검'): 일봉이 몇 거래일 밀렸나 · 자동 갱신이 돌고 있나 · 가격제한폭(±30%) 초과 · 대형주 하루 ±15% · 같은 날 대형주 여럿이 함께 움직인 날 · 거래정지 의심 · 원천 시가총액 = 주식수 × 종가 · 액면분할·권리락을 수정주가로 이어 붙였나 · 인터넷이 되면 네이버 차트/Yahoo 종가와 날짜별 대조. 종목 화면 시가총액이 이제 KRX 원천으로 나온다.
> **KIS 전 과정**(`./run.sh kis-check --suite --e2e`, 모의투자 장중): 이 시스템의 주문 경로 그대로 주문 → 주문번호 즉시 기록 → 체결 → 장부(DB) → 증권사 잔고와 대조 → 같은 주문 다시 내면 막히는지 → 되팔기 → 재시작 복구까지 1주로 확인.
> **증명 프로젝트**(`./run.sh proof-project start` · 화면 '증명 프로젝트'): 원금 100만원 · 누적 -15% 전체 정지 · 종목당 20% · 최대 5종목 · 하루 -3% 신규 매수 중단 — 규칙과 성공 기준(실주문 300건 · 코스피 대비 연 +5%p · 최대 낙폭 -15% · 예측력 검정)을 시작할 때 봉인하고, 매일 장 마감 뒤 결과를 해시 체인으로 이어 붙인다. 공개를 켜면 `/proof` 에서 로그인 없이 수익률 곡선 · 기준 · 봉인 검증을 볼 수 있다(금액·계좌번호는 기본 비공개).
> **차트**: 가격 콤마 · 기간 시작가(1일은 어제 종가) 점선 · 누른 채 끌면 구간 수익률 · 라이브러리 로고 대신 글로 출처 표기.

> **v28 — 신호 엔진 2.0 · 'AI 추천' 화면 (매수 후보 / 비중 축소 / 피할 종목).** 가격·거래량 신호 7개(추세 · 12개월 오름세 · 52주 고점 · 거래대금 급증 · 단기 과열 · 덜 출렁임 · 업종 안 강도)를 -3~+3 점수로 만들고,
> 가중치는 **과거 결과**(신호 점수와 20거래일 뒤 시장 대비 수익의 순위 상관)로 정한다 — 효과 없는 신호는 0, 거꾸로 맞는 신호는 부호를 뒤집는다. 마지막 120거래일은 학습에서 빼 두었다가 '점수대별 실제 결과'와 신뢰 등급(근거 있음 · 약한 근거 · 근거 부족)을 잰다.
> 공시·뉴스·수급·실적·커뮤니티는 아직 과거 자료가 짧아 '검증 전'으로 작게만 반영. 후보마다 신호별 근거 · 같은 점수대 과거 결과 · 손절선을 보여 주고, 매일 첫 계산을 해시로 봉인해 20거래일 뒤 채점한다(전진 기록).
> **자동매매에는 아직 연결하지 않았다** — 전진 기록이 기준을 넘으면 그때 연결한다. 화면: 메뉴 'AI 추천' · 홈 '오늘의 매수 후보' · 종목 화면 '신호 점수'.

> **v27 — 나머지 화면도 하나하나 토스식으로.** 메뉴의 50개 화면(AI 성적표 · 신뢰 센터 · 틀린 이유 · 예측력 검정 · 기록장 · 위험 관리 · 투자 한도 · 계좌·세금 · 주문 내역 · 국내/미국 주문표 · 일정 · 증시 지도 · 그날 다시 보기 · 종목 비교 · 관계도 · 데이터 상태 · 매매해도 되나 · 감시실 · 안전 센터 · 설정 · 내 목표 · 투자일지 …)을
> 공통 스킨이 아니라 화면마다 새로 그렸다(`toss2.js`): 맨 위 한 줄 결론(큰 글씨) → 숫자 몇 개 → 막대 비교 → 행 목록 · 표 대신 행 · 어려운 말(Sharpe·VaR·MDD·Brier·PSI…)은 **눌러서 쉬운 설명**. 예전 화면은 각 화면 아래 '전문가용 전체 화면'으로 그대로 남아 있다.
> 함께: **진짜 지수**(코스피·코스닥·나스닥·S&P500·다우, Yahoo→네이버→stooq · 30분마다 · 못 받으면 예전 대용값) · 종목 차트 **1일(5분봉)** · **내 PC 로컬 LLM**(Ollama·LM Studio — 키 없이 번역·요약·채팅, `QUANT_LOCAL_LLM_URL`) · **미국 종목 모의 매수**(달러 장부 · 원화 환산) ·
> 화면 머리와 첫 카드 제목 중복 제거 · **종목 로고 직접 넣기**(국내 종목) · 커뮤니티 수집을 **오늘 많이 움직인 종목**까지 · PC 에서 오른쪽 아래 AI 버튼 → 위 막대로.

> **v26 — 나머지 화면까지 같은 말투.** 토스식으로 새로 만들지 않은 47개 화면에도 공통 스킨(화면 머리 ← 제목 · 둥근 카드 · 단색 탭/버튼 · 넉넉한 표 · 숫자도 본문 글꼴)과
> 영어 꼬리표 우리말화(PAPER→모의 · SHADOW→그림자 · HALTED→자동 정지 · Champion→현재 모델 · sideways→횡보 …)를 입혔다.
> 로고: 미국 인기 종목 158개 로고를 프로젝트에 넣고(us-stock-logos, MIT) 나머지 미국 4천여 종목은 jsDelivr CDN 에서 받으며, 진짜 로고가 없으면 건물 그림 대신 **이름 첫 글자**.
> 커뮤니티: 네이버 새 모바일 토론실(JSON) → 예전 게시판 순서로 시도 · 커뮤니티 말투 사전(떡상·존버·손절·한강…) · 수집 상태를 데이터 상태 화면에 · `./run.sh community --test 005930,NVDA`.
> 뉴스: 관심·보유 종목마다 구글뉴스 검색으로 **종목별 기사**를 30분마다 모은다 (덜 알려진 종목도 뉴스가 붙게). 처음 여는 화면들은 서버가 켜질 때 미리 계산하고, 홈 자료는 지난 값을 바로 주고 뒤에서 새로 계산한다.

> **v25 — 토스 같은 앱 화면.** 쉬운 화면(기본)이 화면 하나 = API 하나(`/api/t/*`)로 다시 만들어졌다: **홈**(인사 · 지수 카드 · AI가 본 오늘의 종목 · 오늘의 주요 이벤트 D-day · 내 보유 현황 · 할 일/주의 · 결론),
> **종목**(큰 가격 · AI 판단·실적 D-day 카드 · 종합/차트/뉴스/공시/실적/분석 탭 · 1주~3년 면적 차트(손가락을 올리면 그 날 가격) · 시세 표 · 관심종목 · **모의 매수 시트**), **뉴스·공시 한 목록**(전체/뉴스/공시/일정 · 한국/미국/AI/반도체/내 종목),
> **기사 전체 화면**(원문 / 한국어 번역 / AI 요약 · 이 뉴스가 중요한 이유 · 관련 종목 등락), **포트폴리오**(총 자산 · 보유/자산 구성/거래내역), **시장**(장 상태 · 지수 · 많이 오른/내린/거래대금), **AI 분석 리포트**(직전 판단 대비 · 근거 · 주의 · 판단 흐름),
> **알림 설정**(종목별 ±% · 뉴스/공시 · 일정/AI 스위치), **더보기**. 메뉴는 홈 · 관심종목 · 포트폴리오 · 시장 · 뉴스·공시 · 일정 · AI 분석 · 알림 · 더보기, 휴대폰 아래 탭 5개. '전체' 화면과 각 화면의 '전문가용' 단추로 예전 화면도 그대로 쓸 수 있다.

> **v24 — 토스처럼 딱딱 보이게.** 홈에 **다가오는 일정**(휴장·옵션만기·FOMC·CPI·내 종목 실적을 D-day 하나로), 관심종목은 토스식 한 줄(로고·이름·AI 판단 / 가격·등락).
> 로고는 이름이 덜 알려진 종목까지: 국내 토스→알파스퀘어→**네이버(SVG 정화)**, 해외 FMP→**companiesmarketcap**→**EODHD** · `./run.sh logos --all` 로 전 종목 미리 받기(켤 때 자동) · 화면 어디든 종목 링크에 로고 자동.
> **AI 신뢰 센터**(`#aitrust`): 지금 믿어도 되나 → 실제 기록 → 봉인·독립 평가 → 약점 → 다음 단계. 화면은 기기 설정(라이트/다크)을 따른다. 국기 이모지도 글자 표시로.

> **v23 — 토스처럼 쉽게 · 금융 서비스답게.** 삼성전자·SK하이닉스·엔비디아·애플 등 주요 53개 종목은 **진짜 로고를 프로젝트에 내장**(네트워크 없이도 표시)하고,
> 종목 하나의 신분증(기업 마스터: 이름·영문명·거래소·통화·업종·ISIN·로고)을 검색·관심·포트폴리오·뉴스·종목 화면이 같이 쓴다.
> 홈은 **시장 → 오늘 확인할 것 3 · 주의할 것 3 → 내 자산 · AI 상태 → 시장 핵심 · 관심종목 판단 → 오늘의 결론** 순서.
> 뉴스는 **So What**(무슨 뜻 → 관련 기업 → 내 보유 영향 → AI 판단 변화 → 결론), 색은 국내 관례(매수·상승 빨강 / 매도·하락 파랑 / 경고 주황 / 정상 회색)로 통일.
> 통신이 끊겨 주문 접수 여부를 모르면 '실패'로 단정하지 않고 신규 매수를 멈춘다 (증권사 잔고로 맞춘 뒤 다시 판단 → 중복 주문 없음).

> **v22 — 로고 · 서비스 준비도.** 로고가 안 뜨던 원인(인증서 없음 · 이미지 서버 차단 · 7일 재시도 금지 · 화면 멈춤)을 고쳤고,
> `.env` 에 `QUANT_LOGO_DEV_TOKEN` (logo.dev 무료 키, 선택)을 넣으면 대부분 상장사 로고를 정식 API 로 받는다. 화면의 그림 이모지는 모두 색 점으로 정리했다.
> 남에게 서비스하려면 무엇이 막히는지(규제 · 데이터 라이선스 · 다중 사용자 · 보안 · 운영) 전부 정리: [`docs/SERVICE_READINESS.md`](docs/SERVICE_READINESS.md)
>
> **v20 — 목표를 확률로.** 왼쪽 메뉴 **🎯 내 목표**에서 "지금 원금 · 매달 적립 · 목표 금액 · 기간"을 넣으면 "10년 안에 닿을 확률 49%" 처럼 보여 준다.
> 같은 조건에서 코어 · 국내 지수 ETF · S&P500 · 예금을 비교하고, 확률 50%·80% 에 필요한 월 적립액도 알려 준다. 월 적립은 정한 날 자동이다 (모의 장부는 자동 입금, 실계좌는 알림만).
> '그냥 지수 ETF 를 샀다면' 과도 매일 비교한다. AI 는 **시장 대비로 채점**한다. 시장이 다 오를 때 맞힌 것은 실력으로 치지 않는다.
> `./run.sh install-service` 를 한 번 실행하면 PC 를 켤 때마다 24시간 운영이 자동으로 켜진다.
>
> **v19 — 처음이면 홈의 '처음이라면' 순서만 따라 하세요.** 기본은 쉬운 화면(메뉴 6개)이고, 홈 맨 위에는 오늘 할 일 3개와
> "AI 지금 믿을 만한가?"만 나온다. 종목 화면은 AI 최종 판단 하나(🟢 BUY / 🟡 HOLD / 🔴 SELL / ⚪ NO TRADE)를 크게 보여 주고,
> 데이터가 오래됐거나 실적 발표 전날이면 BUY 를 NO TRADE 로 막는다. 왼쪽 아래 [전체] 를 누르면 모든 메뉴가 나온다.
>
> **v18 — 홈은 5칸부터.** 오늘 시장 · 내 자산 · AI 상태(🟢검증됨/🟡검증 중/🔴사용 금지) · 중요한 뉴스 · 오늘 할 일.
> 뉴스·공시를 누르면 원문 → 번역 → 쉬운 설명 → 주가 영향 → 영향 받을 종목(🔴🟠🟡) 순서로 보여 준다. 종목마다 회사 로고가 붙는다.
> `.env` 에 키를 넣었는데 "키 없음" 이면 **옛 폴더의 서버가 떠 있던 것**이다. 이제 `./run.sh` 가 알아서 끄고 새로 띄운다 (`./run.sh stop` 으로 직접 끌 수도 있다).

- **현황·결과·한계: [docs/PLATFORM_STATUS.md](docs/PLATFORM_STATUS.md)** (V17: 8-8장 · V18: 8-9장 · V19: 8-10장)
- **지금 할 일과 다음 개선: [docs/ROADMAP.md](docs/ROADMAP.md)**

```text
Market Data ─┬─▶ Primary AI (Gemini 무료 / Claude · 종합)  ─┐
             ├─▶ Second AI (NVIDIA Nemotron · 독립 검증) ─┤
             ├─▶ Panel AI (Cloudflare Gemma · 교차검증)  ─┤
             ├─▶ Quant Model (champion · 숫자/통계)      ─┼─▶ Ensemble ─▶ Risk Gate ─▶ Execution ─▶ Broker
             ├─▶ Market Regime (국면)                    ─┤   충돌 탐지      킬스위치      Paper/Shadow/Live
             └─▶ Risk AI (Groq gpt-oss · veto)           ─┘   성적 가중      손실·비중 한도
장 마감 후 ─▶ 실제 결과로 채점 ─▶ AI 성적표(방향·뉴스·거시·추세·위험) ─▶ 복기 교훈 ─▶ RAG 메모리
```

- 설계: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 실제 KRX 데이터 연구: [docs/RESEARCH_KRX.md](docs/RESEARCH_KRX.md)
- **실전 투자자 가이드 (기대 수익·최소 투자금·계좌·세금·월간 루틴·멈춤 규칙): [docs/INVESTOR_GUIDE.md](docs/INVESTOR_GUIDE.md)**
- **KIS 모의투자 연결·운영 절차: [docs/KIS_DEMO_RUNBOOK.md](docs/KIS_DEMO_RUNBOOK.md)**
- **`.env` 키 발급처 (KIS · 무료 AI · 알림 · 데이터): [docs/ENV_KEYS.md](docs/ENV_KEYS.md)**
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
- **무료 멀티 AI**: Gemini · NVIDIA · Groq · Cloudflare Gemma 를 역할별 자동 배정, 한도 초과 시 다음 모델로 전환 (`analysts/llm_clients.py`)
- **신뢰 계층**: 주문 멱등성·재시작 복구 · AI 별 확률 보정 · 포트폴리오 VaR/ES·상관 군집·유동성 · Evidence Chain · AI Journal · NO TRADE 사유별 가치 · 슬리피지 실측
- **LLM 가드**: 응답 캐시 · 일 비용 예산 · 공급자별 무료 일 한도 · 모든 호출 감사 로그 (`analysts/guard.py`)
- **검증 통계**: PSR · DSR(다중검정 보정) · Sharpe 부트스트랩 CI · 비용 2배 스트레스 · 확률 보정표
- **안전장치**: DB 공유 킬스위치 · 자동 정지(급락) · 치명 위험 시 강제 청산 · 매매 사이클 잠금 · 영속 주문 한도 · 낡은 데이터로 매매 금지
- **관측**: 작업 실행 기록, `/api/health`, 운영 화면, Discord/Slack/Telegram 알림, JSON 로그
- **배포**: Dockerfile, docker-compose(PostgreSQL+마이그레이션+스케줄러+웹), Alembic, GitHub Actions CI

## 빠른 시작 — `./run.sh` 하나로

```bash
./run.sh
```

설치(Python 3.11+ 가상환경) → DB 확인 (PostgreSQL 이 없으면 설치가 필요 없는 SQLite 로 자동 전환) →
KRX 데이터 받기·갱신 → 점검 → 대시보드(http://127.0.0.1:8050, 브라우저 자동 열림) + 24시간 운영까지 자동.
두 번째 실행부터는 설치·데이터를 건너뛰고 바로 시작한다. 종료는 Ctrl+C.

- `.env` 에 **KIS 모의투자 키 3개**를 넣으면 다음 실행부터 모의계좌로 자동매매 (없으면 가상매매) — 발급처: [docs/ENV_KEYS.md](docs/ENV_KEYS.md)
- 무료 AI 키(Gemini·Groq·NVIDIA·Cloudflare)는 선택. 기본은 코어 전용(`QUANT_CORE_ONLY=true`)
- 대시보드가 먼저 뜨고, 뉴스·AI 판단·미국 장부는 뒤에서 채워진다 (`logs/warmup.log`, 홈의 '시작 체크리스트')
- **새 버전으로 바꿀 때**: 새 폴더에서 `./run.sh` 를 실행하면 직전 버전 폴더의 `.env`(키)·`quant_ai.db`(기록)를 자동으로 가져온다.
  - 가상환경·주가 데이터는 `~/.quant-ai` 에 한 번만 설치하고 모든 버전이 함께 쓴다.
  - 디스크가 부족하면 `./run.sh clean-old` 로 옛 버전 폴더의 설치 파일을 정리한다(.env·DB 는 보존).
- 개별 명령
  - `./run.sh doctor --ai --kis --notify` · `./run.sh chat` (채팅 AI)
  - `./run.sh proof [--market US]` (증명 체인) · `./run.sh guardian` (킬스위치 조건)
  - `./run.sh us` (미국 장부) · `./run.sh db-clean` · `./run.sh kis-check` · `./run.sh orders …`
  - `./run.sh up` (Docker) · `./run.sh help`

가상 데이터 데모: `./run.sh demo && ./run.sh serve` · 테스트: `./run.sh test`

### 실제 데이터로

```bash
cp .env.example .env                # 키 입력 (docs/ENV_KEYS.md)
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
          └ 추세 필터: 리밸런싱 날 KOSPI < 200일선이면 코어 비중 절반 (사전등록 시험 통과, 최대 낙폭 -55% → -43%)
          └ 소액 계좌: 1주 가격이 목표 금액보다 훨씬 비싼 종목은 다음 순위로 대체
          └ AI 역할: 종목 고유 위험 거부권 (신규 편입 대신 다음 순위) · 상장폐지 시 즉시 청산
위성 20%  멀티 AI 합의 BUY 상위 5 (AI 종목선택을 작은 비중으로 실전 검증)
          └ 시장 전체 위험(위기 국면·FOMC 임박)에는 위성 신규 매수 중단
AI 기여도  가상 장부 3개(코어만 / +거부권 / +위성)를 같은 가격으로 나란히 굴려 비교
```

```bash
quant-ai collect krx --marcap-dir marcap/data --years 5 --top 100   # 실제 KRX 데이터 적재 (순위 모델은 12개월 모멘텀 때문에 5년 권장)
quant-ai cycle --mode paper                                         # 한 사이클
quant-ai replay --days 60                                           # 최근 60거래일 재생
quant-ai run --mode shadow                                          # 24시간 (QUANT_STRATEGY=core_satellite)
quant-ai orders --cash 30000000 --holdings my.csv --no-ai --out orders.csv   # 다른 증권사·ISA 수동 매매용 주문표
quant-ai checkup --mode paper                                       # 전략 건강검진 (손실이 과거 범위 안인가)
```

16년 실데이터 기준 기대치는 **KOSPI 와 비슷한 수익, 더 작은 변동성**이다 (연 7.6% vs 8.3%, 변동성 15.7% vs 21.4%).
지수를 이긴다는 증거는 없다 — 자세한 숫자와 실전 운용 규칙은 [투자자 가이드](docs/INVESTOR_GUIDE.md).

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

### 매매 준비 · 실제 예측력 (v13)

```bash
quant-ai readiness                                   # 7관문 (DATA·BROKER·MODEL·RISK·EVENT·DRIFT·CALIBRATION)
quant-ai kis-check --suite                           # KIS 모의투자 검증 8단계 (장중이면 주문·취소 경로까지)
quant-ai kis-check --suite --fill                    # + 모의투자 1주 실제 체결 → 슬리피지 실측
quant-ai power-study --marcap-dir marcap/data        # 실제 KRX 과거 데이터로 코어 점수 예측력 사후 검증
```

- NOT READY 면 live·shadow 장부의 **신규 매수가 막힌다** (매도·위험 축소는 항상 허용). `QUANT_READINESS_GATE=live|all|off`
- 실적 발표 D-1 이내 매수는 ×0.5 (`QUANT_EVENT_GATE=reduce|block|off`)
- "실제 시장 예측력" 은 사전 등록한 기준으로, 등록 이후 봉인된 예측만 순차 검정(SPRT)해서 판정한다 — 대시보드 **실제 예측력** 화면

### Truth Center · 완성 기준 · 사용자 화면 (v14)

```bash
quant-ai watchdog --mode paper       # 스케줄러를 감시견 아래에서 실행 (죽음·멈춤 → 재시작). ./run.sh 기본값
python -m quant_ai.checklist         # 완성 기준 체크리스트 (docs/FINAL_CHECKLIST.md)
```

- **Truth Center** (`#truth`): 시장 시계 · 데이터 · 이벤트 · 증권사 · 포트폴리오 위험 · 체결이 사실인가를 먼저 확인. 🔴 항목은 매매 준비 관문을 빨강으로 만든다 (fail-closed).
- 매매 준비 점검이 없거나 오래됐거나 실패하면, 게이트가 적용되는 장부의 신규 매수를 막는다. 휴장일 캘린더·이벤트 캘린더가 없어도 막는다.
- 국내 일봉 1차 소스(KRX)가 늦으면 Yahoo 로 **빈 날만** 채우고(겹치는 날 종가 비율로 맞춤), 1차가 갱신되면 1차 값으로 덮어쓴다.
- 종목 페이지: **왜 BUY / SELL / HOLD / NO TRADE** · 차트 위 AI 진입 구간·손절·목표 선 · 내 보유(장부 + 계좌) · 이 종목 과거 AI 적중률 · 관심종목 ★
- 홈: **오늘 할 일** · 시장 한눈에 · 상단 시장 시계(장중·장외·휴장, 개장/폐장까지 남은 시간)
- **종목 비교** (`#compare/005930,000660`) · 키보드 단축키(`?` 로 목록, `/` 검색, `g t` Truth Center) · 최근 검색

### 올인원 종목 · AI Health · 내 설정 (v15)

- 종목 페이지: 섹션 이동 · **데이터 신뢰도** · D-day 띠 · **왜 샀나/왜 안 샀나** · **사전 리스크 게이트**("지금 사면?" 통과/축소/차단, 주문 없음) · **뉴스·공시 자동 요약**
- **AI · 모델 Health** (`#aihealth`): AI 별 적중(전체·초기·최근 20거래일) · 성능 저하 자동 감지(일별 검정 → 알림) · LLM 응답 상태
- 홈 **⚙ 홈 편집**(숨기기·순서) · 설정의 **외부 알림 설정**(종류별 텔레그램/디스코드·웹 푸시 · 조용한 시간) — 서버 저장, 모든 기기 공통

### Stock OS · 검증 가능한 AI · 데이터 OS (v16)

- 종목 페이지: **현재 상황 한 줄**(🟢/🟡/🔴) · **OS 헤더**(AI 확률·실적 D-n·뉴스 톤·공시·수급·밸류·위험·내 보유) · 가격/뉴스/공시/AI **신선도(초 단위)** · 차트 위 지지/저항·뉴스·공시·실적 표시 · 뉴스 v2(톤·예상 영향·AI 요약·영향 분석) · 실적 · 종목 리스크 · **과거 동일 조건 N회 검증** · **투자 논리** · **모의 주문**(수동 장부) · 섹션 숨기기/순서
- `#action` 오늘 할 일 · `#watch` 관심종목 그룹 · `#notrade` 거래 안 한 이유 · `#scorecard` **공개 AI 성적표**(1000건 전부, 실패 포함) · `#ailab` AI Lab·실패 연구
- `#datahealth` **DATA HEALTH**(가격 지연 15분 → 매수 차단) · Sentinel · Fail-Closed · `#pos` Portfolio OS·위기 시뮬레이션 · `#profile` 투자 성향·실수 패턴 · `#validation` 실전 검증 진행표·비용 실측 · `#governance` 규제 단계·보안·라이선스·감사 로그
- 문서: [COMPLIANCE](docs/COMPLIANCE.md) · [SECURITY](docs/SECURITY.md) · [DATA_LICENSES](docs/DATA_LICENSES.md) · [SAAS_ARCHITECTURE](docs/SAAS_ARCHITECTURE.md)

## 테스트

- `pip install -e ".[dev]"` 뒤 `pytest` — 전체 통과 · 건너뜀 0개가 출시 기준 (PostgreSQL 이 설치돼 있으면 임시 DB 를 직접 띄워 통합 테스트까지). 테스트 개수는 문서에 손으로 적지 않습니다 — GitHub Actions CI 결과가 기준입니다
- 문서의 테스트 수가 실제와 다르면 테스트가 실패한다 (`tests/test_docs_counts.py`) · CI 는 건너뜀이 하나라도 있으면 실패 (`QUANT_NO_SKIP=1`)

## 안전 원칙
- AI → 신호 → 앙상블 → **리스크 게이트** → 실행 엔진 → 증권사. AI 는 `broker.buy()` 를 호출할 수 없다.
- Risk AI 의 규칙 veto (이벤트 임박, 변동성 급증, 데이터 이상, 위기 국면, 상장폐지 공시)는 다른 AI 가 뒤집을 수 없다.
- Live 는 `QUANT_LIVE_ENABLED=true` + `QUANT_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY` + 소액 상한 + Shadow 검증 champion 이 모두 있어야 켜진다.
- NVIDIA 무료 엔드포인트는 연구·테스트 용도로 안내되어 있으니 Live 전에 최신 약관을 확인할 것.
- 이 프로젝트는 투자 조언이 아니며, 백테스트/가상 성과는 실제 수익을 보장하지 않는다.

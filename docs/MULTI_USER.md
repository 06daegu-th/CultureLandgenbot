# 여러 사용자 서비스 (v34) — 설계 · 켜는 법 · 운영 · 남은 일

> 한 줄 요약: `QUANT_SERVICE_MODE=multi` 로 켜면 **회원 가입·로그인**이 생기고, 목표·계좌·관심종목·알림·일지·모의투자가
> **사람마다 따로** 저장된다. 시장 자료·AI 계산은 운영자가 한 번 하고 모두가 같이 본다. 기본값은 지금까지와 같은 **혼자 쓰는 모드**.
> 법률 내용은 법률 자문이 아니다 — 공개 전 금융규제·개인정보 변호사 검토가 필요하다.

## 1. 구조

```
브라우저 ── HTTPS(Caddy·nginx) ── 웹 서버 (여러 개 가능)            스케줄러 (1개)
                                   │ 요청마다: 세션 → 회원 → 문맥     │ 수집 · AI · 운영자 매매 · 회원별 적립일 작업
                                   ▼                                  ▼
                         ┌──────────── PostgreSQL ────────────┐
                         │ 공용: 시세·뉴스·공시·AI 판단·연구     │
                         │ 회원: users · user_sessions · tokens │
                         │ 사람 칸: system_state 'u:{id}:키'    │ ← 개인 데이터 (QUANT_DATA_KEY 로 암호화)
                         │ owner 칸: alerts · alert_rules ·     │
                         │          user_journal               │
                         │ 사람 장부: manual@{id} · etf-dca@{id}│ ← 모의투자 · 월 적립 모의 장부
                         └─────────────────────────────────────┘
```

| 무엇 | 어떻게 사람마다 나누나 | 코드 |
|---|---|---|
| 목표 계획 · 내 계좌 · 관심종목 · 설정 · 투자 성향 · 투자 근거 · 휴대폰 알림 구독 · AI 채팅 · 사용량 | 저장 키 앞에 사람 번호 (`u:1z:goal_plan`) — `ops.get_state/set_state` 가 요청 문맥을 보고 자동으로 | `tenancy.py` · `ops.py` |
| 가격 알림 규칙 · 투자 일지 · 개인 알림 | 행에 `owner` 칸 | `alerts.py` · `review/my_journal.py` |
| 모의투자 · 월 적립 장부 | 장부 이름에 사람 번호 (`manual@1z`) — 회원이 어떤 장부를 달라고 해도 자기 장부로 | `tenancy.personal_book` · `pipeline.load_portfolio` |
| 화면 캐시 | 캐시 키 앞에 사람 번호 (공용 계산만 같이 씀 · **모르는 키는 사람마다** 가 기본) | `web/api.py _Cache` |
| 운영자 장부 · AI 자동매매 · 긴급 정지 · 키 · 서버 | 회원은 접근 불가 (허용 목록에 없는 API 는 403) | `service.MEMBER_GET/POST` |

**소유자(owner)** 계정은 혼자 쓰던 때의 데이터를 그대로 쓴다 — 서비스로 바꿔도 기록이 사라지지 않는다.
요청 문맥이 없는 곳(스케줄러·CLI)은 예전과 똑같이 동작하고, 회원별 작업(적립일 알림·모의 적립)은 `members.for_each_member` 가 사람마다 문맥을 바꿔 돈다.

## 2. 켜는 법

```bash
# 1) .env
QUANT_SERVICE_MODE=multi
QUANT_DATA_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">   # 잃어버리면 개인 데이터를 못 연다 — 따로 보관
QUANT_PUBLIC_URL=https://invest.example.com
QUANT_SMTP_HOST=... QUANT_SMTP_FROM=...       # 없으면 메일은 '보낼 편지함'에만 (작은 베타용)
QUANT_TRUSTED_PROXIES=127.0.0.1  QUANT_WEB_SECURE_COOKIE=1

# 2) DB (PostgreSQL 은 마이그레이션 · SQLite 는 자동)
alembic upgrade head            # 0009_members: users · user_sessions · user_tokens · owner 칸

# 3) 소유자(운영자) 계정
./run.sh users owner --email you@example.com

# 4) 실행 → 로그인 → 왼쪽 '운영 콘솔'에서 초대 링크 만들기
./run.sh serve
```

HTTPS 리버스 프록시 예 (Caddy):

```
invest.example.com {
    reverse_proxy 127.0.0.1:8050
}
```

웹 서버를 여러 개 띄워도 된다 (세션이 DB 에 있음). 스케줄러는 하나만.
`QUANT_WEB_ALLOWED_HOSTS=invest.example.com` 도 넣는다 (Host 헤더 검사).

## 3. 계정 · 보안

| 항목 | 내용 |
|---|---|
| 가입 | 가입 방식 open · invite(기본) · closed · 필수 동의 4개(약관 · 개인정보 · 투자 위험 · 만 14세) + 선택(마케팅) · 동의 버전·시각·IP 저장 · IP 당 1시간 5번 |
| 비밀번호 | scrypt 해시 · 10자 이상 · 숫자만/반복/흔한 비밀번호/이메일 아이디 금지 |
| 로그인 | 계정별 5번 실패 → 15분 잠금 (DB · 서버 여러 대 공통) · IP 별 20번 · 없는 이메일도 같은 시간·같은 문장 |
| 2단계 인증 | 회원마다 OTP 앱 (비밀값 암호화 저장 · 같은 코드 재사용 거부) |
| 세션 | 값이 아니라 sha256 만 DB 에 · HttpOnly · SameSite=Strict · HTTPS 면 Secure · 12시간(활동하면 연장, 최대 30일) · 기기 20개까지 · 다른 기기 모두 로그아웃 |
| 비밀번호 찾기 | 있든 없든 같은 응답 · 1시간 링크 · 한 번만 · 바꾸면 모든 기기 로그아웃 |
| 이메일 확인 | 3일 링크 · SMTP 가 있으면 기본 필수 |
| 정지 | 운영자가 정지하면 그 회원 세션 즉시 삭제 |
| 약관 변경 | `members.TERMS_VERSION` 을 올리면 다음 요청에서 재동의(428) 화면 |
| 감사 기록 | 로그인·가입·정책·회원 변경·내보내기·탈퇴 — 이메일 대신 회원 번호만 |
| CSRF | JSON 만 받음 · 다른 출처(Origin) 거부 · SameSite=Strict |
| 속도 제한 | 회원마다 초당 8번(순간 80번) |

## 4. 투자 정보 범위 (중요)

| 범위 | 회원이 보는 것 | 언제 |
|---|---|---|
| **none (기본)** | 시세 · 지수 · 뉴스 · 공시 · 일정 · 증시 지도 · 목표 계획 · 내 계좌 · 모의투자 · 가격 알림 · 투자 일지 · AI 채팅(시세·용어) — **AI 매수·매도 후보 · 종목별 AI 판단 · AI 성적은 없음** | 법률 검토 전 |
| info | 위 + 모든 회원에게 같은 AI 분석(정보) | 유사투자자문업 신고 등 요건 확인 후 운영 콘솔에서 켬 |

어느 범위에서도 회원 한 사람에게 맞춘 매매 권유(1:1 자문)와 대신 주문(일임)은 열지 않는다.
none 일 때는 서버가 API 를 막고(403) · 응답에서 AI 칸을 빼고 · 화면이 메뉴와 칸을 숨긴다 (세 겹). 브라우저 테스트(`tests_ui/test_members.py`)가 확인한다.

## 5. 요금제 · 한도

| | 무료 | 프로 |
|---|---|---|
| 관심종목 | 30 | 300 |
| 가격 알림 | 10 | 100 |
| 내 계좌 | 3 | 20 |
| AI 채팅 (하루) | 5 | 100 |
| AI 설명 (하루) | 10 | 100 |
| 모의 주문 (하루) | 50 | 500 |
| 투자 일지 (하루) | 30 | 300 |

운영 콘솔에서 회원별로 프로 + 기간(일) 지정. **결제(PG) 연동은 없다** — 사업자 등록·PG 계약 후 붙인다 (`service.PLANS` 와 `users.plan/plan_until` 이 붙일 자리).

## 6. 개인정보

* 내보내기: 내 계정 → '내 데이터 내려받기' (목표·계좌·관심종목·알림·일지·모의 주문·로그인 기기 — 비밀번호 해시·OTP·푸시 키 제외)
* 탈퇴: 이메일 + 비밀번호 확인 → 사람 칸 · owner 행 · 사람 장부 · 세션 · 토큰 · 계정 즉시 삭제
* 암호화: `QUANT_DATA_KEY` 가 있으면 개인 데이터(사람 칸)와 OTP 비밀값을 Fernet(AES-128-CBC + HMAC)으로 저장
* 약관·처리방침 초안: `web/static/legal/terms.html` · `privacy.html` — **[대괄호]를 채우고 전문가 검토 후 공개**

## 7. CLI

```
./run.sh users owner --email a@b.com          소유자 만들기 (QUANT_OWNER_PASSWORD 가 있으면 묻지 않음)
./run.sh users list [--email 일부]            회원 목록
./run.sh users invite [--email x] [--plan pro] [--uses 10] [--days 7]
./run.sh users set --email x --plan pro --days 30 | --status disabled | --role admin | --unlock
./run.sh users delete --email x [--yes]       회원과 데이터 삭제
./run.sh users cleanup                        만료된 세션·토큰 정리 (스케줄러가 하루 한 번)
```

## 8. 솔직한 한계 (출시 전 남은 일)

| 남은 일 | 왜 | 누가 |
|---|---|---|
| 금융규제 검토 (유사투자자문업 신고 여부 · 문구) | 법 | 변호사 |
| 상용 데이터 계약 (KRX·지수·뉴스·로고) | 지금 출처 대부분 개인용 — `docs/DATA_LICENSES.md` | 운영자 |
| 결제(PG) · 환불 규정 · 사업자 등록 | 유료화 | 운영자 + 개발 |
| 약관·처리방침 확정 · 개인정보 보호책임자 지정 | 개인정보보호법 | 운영자 + 전문가 |
| 부하 시험 | 웹 서버는 파이썬 표준 서버(스레드) — 수백 명 동시 접속까지는 여러 개 띄워 나누는 방식. 수천 명 이상이면 ASGI 서버로 옮겨야 함 | 개발 |
| 보안 점검 (외부 모의 해킹) | 자체 테스트만 함 | 외부 업체 |
| 실제 메일 발송 확인 | 이 개발 환경은 인터넷이 막혀 SMTP 를 실제로 보내 보지 못함 | 운영자 |
| 베타 (10~20명 · 몇 주) | 진짜 사용자의 막힘은 써 봐야 나옴 | 운영자 |

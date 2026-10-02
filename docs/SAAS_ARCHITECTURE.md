# SaaS 구조 (설계) — 지금은 단일 프로세스 개인용

지금: `quant-ai serve` 하나가 웹 + 스케줄러 + 수집 + AI + 매매 + 감시를 모두 돌립니다 (SQLite 또는 Postgres).
여러 사용자에게 제공하려면 아래처럼 나눕니다. **장애가 한 곳에서 나도 돈은 안전**해야 한다는 원칙(Fail-Closed)이 나누는 기준입니다.

```
사용자 ── API Gateway (TLS · 속도 제한 · WAF)
            │
            ├─ Auth (로그인 · MFA · 세션 · RBAC)
            ├─ Web/API (읽기 전용 화면 · 설정 쓰기 · 감사 로그)
            │
   메시지 큐 / 이벤트 버스
            │
   ┌────────┼──────────┬────────────┬─────────────┬───────────┐
Market Data  News/Disc  AI Workers    Trading       Watchdog
(시세·일봉)  (수집·요약) (합의·기권)   (리스크 게이트 (Sentinel·
 1차/2차                 LLM 한도      → 주문 → 대조)  킬스위치)
   └────────┴──────────┴────────────┴─────────────┘
                     Postgres (암호화) · 객체 저장소(백업·리포트) · Secret Manager
```

| 서비스 | 지금 코드 | 떨어지면 |
|---|---|---|
| Market Data | `data/collectors/*` · `data/sources.py` · `kis_ws.py` | 신규 매수 **차단** (DATA HEALTH → Truth → 매매 준비 관문) |
| News / Disclosure | `collectors/news.py` · `disclosures.py` · `dart_docs.py` | 신규 매수 **절반** (`failmode.news_down`) |
| AI Workers | `analysts/*` · `agents.py` · `ensemble/*` | AI 기권 → 코어는 계속 (AI 오버레이만 빠짐) |
| Trading | `pipeline._trade` · `trading/*` | 주문 없음 · 기존 포지션 유지 |
| Broker 연결 | `trading/kis.py` | **차단** + HALTED (guardian) |
| DB | `data/db.py` | **차단** — 기록 못 하면 주문하지 않는다 |
| Watchdog | `watchdog.py` · `sentinel.py` · `trading/guardian.py` | 스케줄러 밖 별도 프로세스 — 멈춤 자체를 감지 |
| Scheduler | `scheduler.py` | Sentinel 이 '스케줄러 멈춤' 알림 |

## Fail-Closed 매트릭스 (구현됨 — `failmode.py`)

| 장애 | 기존 포지션 | 신규 매수 | 매도·위험 축소 |
|---|---|---|---|
| AI 서버·LLM | 유지 | 코어 계속 · AI 기권 | 가능 |
| 뉴스 수집 | 유지 | **절반 제한** | 가능 |
| 증권사(KIS) | 증권사 기준 | **차단** | 차단 (HALTED) |
| DB | 유지 | **차단** | 차단 |
| 시세·일봉 (장중 가격 15분 지연 포함) | 유지 | **차단** | 가능 |
| 휴장·이벤트 캘린더 | 유지 | **차단** | 가능 |
| 매매 준비 점검 실패 | 유지 | **차단** | 가능 |

## 순서 제안

1. Postgres + Alembic (이미 지원) → 2. 수집·AI·매매를 별도 워커 프로세스로 (같은 코드, 다른 진입점) → 3. Auth·RBAC·감사 로그 저장소 분리 →
4. 상용 데이터 계약 → 5. 멀티 테넌트 (사용자별 장부·설정·키 분리) → 6. 구독

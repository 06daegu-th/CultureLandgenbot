# KB증권 OpenAPI 연결 (v40 · 읽기 전용)

## 근거
요청 경로와 필드는 **KB증권 공식 GitHub 샘플**에 있는 것만 썼어요. 추측해서 만든 것은 없어요.
- 샘플: `github.com/kbsecurities/kb-openapi`
- 공식 가이드: https://openapi.kbsec.com/guide_b2c

| 기능 | TR | 경로 | 상태 |
| --- | --- | --- | --- |
| 토큰 | - | `POST /oauth2/token` | 연결됨 |
| 주식현재가 | IVU10140 | `POST /api/v1/ivu10140` `{excg_clsf, shrt_cd}` | 연결됨 |
| 장운영상태 | SZQM0771 | `POST /api/v1/szqm0771` `{}` | 연결됨 (코드값은 원래 값 그대로 표시) |
| 환율종합 | IVA60190 | `POST /api/v1/iva60190` `{}` | 연결됨 |
| 잔고·보유·체결·주문·취소 | - | 샘플의 `endpoints.yaml`이 비어 있음 | **막힘 — 공식 가이드 확인 필요** |

- 헤더: `Content-Type`, `appKey`, `Authorization: bearer <token>`(소문자 bearer).
- 본문: `{"dataHeader": {"ipAddr": "", "macAddr": ""}, "dataBody": {...}}`.
- 웹소켓은 KB 쪽에서 아직 개발 중이라 쓰지 않아요.

## 설정
```
QUANT_BROKER=kb
KB_OPENAPI_APP_KEY=...
KB_OPENAPI_APP_SECRET=...
```
키 값은 화면, 로그, 점검 결과 어디에도 표시하지 않아요.

## 점검
- `./run.sh kb-check`: 설정 → 토큰 → 현재가(호가 신선도) → 장 상태 → 환율 → 계좌·주문(공식 가이드 확인 필요).
- 화면: 체결 품질 · 증권사 점검 화면에서 확인할 수 있어요.
- `doctor`도 KB 키가 있는지, 주문이 막혀 있는지 보여 줘요.

## 안전장치
- `QUANT_BROKER=kb`에서 실전 주문은 `LiveBroker`가 거부해요. 주문 경로가 아예 없어요.
- 잔고 조회가 없어서 계좌 대조를 할 수 없어요. 그래서 홈 상태줄에 "장부 대조 불가"로 표시하고, 실계좌 매수는 막혀 있어요.

## 다음 단계 (공식 가이드 확인 뒤)
1. 잔고·체결 TR을 가이드 전문 규격으로 추가하고, 장부와 대조해 불일치하면 매수를 막아요.
2. 모의 검증: 접수, 부분 체결, 취소, 응답 유실, 재시작 복구. 응답이 유실된 주문은 다시 보내지 않고 조회로 확인해요.
3. 소액 실전: 주문당·계좌당 한도, 별도 LIVE 승인, 긴급 정지.

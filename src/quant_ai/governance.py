"""규제 · 데이터 라이선스 · 보안 · 감사 로그 — 서비스화할 때 '나중에 붙이면 안 되는' 것들을 지금부터 코드로.

⚠️ 법률 자문이 아니다. 서비스로 제공하기 전에 금융규제 전문 변호사와 검토해야 한다 (투자자문·일임업 인가, 마이데이터 허가,
   유사투자자문 신고, 개인정보, 데이터 이용계약). 여기서는 '어떤 기능이 어느 단계인지'를 코드에 표시해 두어,
   서비스 단계를 바꿀 때 무엇을 켜고 끌지 한눈에 보이게 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from . import ops
from .asof import label

# ------------------------------------------------------------------ 서비스 단계
LEVELS = [
    ("information", "정보 제공", "시세·뉴스·공시·일정·재무를 보여준다 (판단 없음)"),
    ("analysis", "분석", "지표·위험·AI 확률을 누구에게나 같게 보여준다 (특정인 맞춤 아님)"),
    ("personalized", "개인화 분석", "내 보유·성향·위험 한도에 맞춘 분석·경고"),
    ("advice", "투자 조언", "특정인에게 '이 종목을 사라/팔아라' 권유"),
    ("order", "주문 연결", "판단을 증권사 주문으로 실행 (일임·자동매매)"),
]
FEATURES = {
    "시세·차트·일정·뉴스·공시": "information", "데이터 출처·신뢰도": "information",
    "AI 확률·판단 이유·성적표·예측 장부": "analysis", "종목 비교·실적·수급·위험 지표": "analysis",
    "내 보유 위험·Portfolio OS·오늘 할 일": "personalized", "성향 맞춤 종목 목록": "personalized", "투자 논리·저널 실수 분석": "personalized",
    "사전 리스크 게이트 (지금 사면?)": "personalized", "AI BUY/SELL 을 '행동'으로 권유": "advice",
    "가상/섀도 장부 자동매매": "order", "KIS 모의·실전 자동 주문": "order",
}
LEGAL = {
    "information": "일반 정보 제공 — 출처 표기·데이터 이용계약 준수",
    "analysis": "불특정 다수 대상 분석 — 유사투자자문업 신고 여부 검토 대상",
    "personalized": "개인 맞춤 분석 — 개인정보·마이데이터 규제, 자문업 해당 여부 검토",
    "advice": "투자자문업 인가 대상일 수 있음 — 전문가 검토 전 제공 금지",
    "order": "투자일임업·주문 대리 — 인가·내부통제·보안 요건 (본인 계좌 개인 사용과 구분)",
}


def service_level(app) -> dict:
    cur = getattr(app.settings, "service_level", "personal")
    return {"current": cur, "current_label": "개인 사용 (본인 계좌 · 본인 PC)" if cur == "personal" else cur,
            "levels": [{"key": k, "name": n, "desc": d, "legal": LEGAL[k],
                        "features": [f for f, lv in FEATURES.items() if lv == k]} for k, n, d in LEVELS],
            "note": "지금 구성은 '본인이 본인 계좌에 쓰는 개인용'. 다른 사람에게 제공하는 순간 위 단계별 인가·신고·계약이 필요할 수 있음 — 법률 자문 필수"}


# ------------------------------------------------------------------ 데이터 라이선스
LICENSES = [
    {"source": "FinanceData/marcap (KRX 일별 시세 모음)", "used": "국내 일봉·시총 (코어 전략 · 백테스트)", "personal": "가능 (공개 저장소)",
     "commercial": "원천은 KRX 데이터 — 상용 제공은 KRX 정보데이터시스템/데이터 상품 계약 필요", "risk": "high"},
    {"source": "Yahoo Finance (yfinance)", "used": "미국 일봉 · 종목 상세 · 실적 이력 · 보조 가격", "personal": "개인·연구 용도",
     "commercial": "Yahoo 약관상 상업적 재배포 불가로 알려짐 — 상용은 유료 데이터 벤더로 교체", "risk": "high"},
    {"source": "네이버 금융 (공개 페이지)", "used": "실시간 시세 폴링 · 수급 · 국내 컨센서스 · 토론방", "personal": "개인 사용 (공개 페이지 조회)",
     "commercial": "크롤링 기반 — 상용 제공 불가로 봐야 함 · 공식 계약/대체 소스 필요", "risk": "high"},
    {"source": "DART OpenAPI (금융감독원)", "used": "공시 목록 · 원문", "personal": "가능 (API 키)",
     "commercial": "공공데이터 — 이용약관·출처 표기 준수 시 활용 가능한 편, 호출 한도 확인", "risk": "low"},
    {"source": "SEC EDGAR (미국 증권거래위원회)", "used": "미국 공시 (8-K·10-Q·10-K 등 · 실적 발표 8-K 2.02)", "personal": "가능 (키 없음 · 연락처 User-Agent)",
     "commercial": "미국 정부 공공 데이터 — 활용 가능 · 초당 10회 제한 · 공정 사용 정책 준수", "risk": "low"},
    {"source": "FRED (세인트루이스 연준)", "used": "거시지표 · 발표 일정", "personal": "가능 (API 키)",
     "commercial": "일부 시리즈는 원 저작권자 제한 — 시리즈별 확인", "risk": "medium"},
    {"source": "ECOS (한국은행)", "used": "기준금리 · 금통위", "personal": "가능 (API 키)", "commercial": "공공데이터 — 약관·출처 표기", "risk": "low"},
    {"source": "KIS Open API (한국투자증권)", "used": "실시간 시세·호가·주문·잔고", "personal": "본인 계좌 사용",
     "commercial": "시세 재배포는 별도 계약 · 타인 주문 대행은 일임/인가 문제", "risk": "high"},
    {"source": "Wikipedia 조회수 · 네이버 데이터랩", "used": "대체 데이터(관심도)", "personal": "가능", "commercial": "약관·API 조건 확인", "risk": "medium"},
    {"source": "LLM API (Gemini·NVIDIA·Groq 등 무료 구간)", "used": "AI 분석·요약", "personal": "무료 구간 약관 내",
     "commercial": "무료 구간은 상용·대량 사용 제한이 흔함 — 유료 요금제·데이터 처리 약관 확인", "risk": "medium"},
]


def licenses(app) -> dict:
    usage = getattr(app.settings, "service_level", "personal")
    high = [x["source"] for x in LICENSES if x["risk"] == "high"]
    return {"usage": usage, "rows": LICENSES, "blocked_sources": blocked_sources(app.settings),
            "blocked_jobs": ops.get_state(app.engine, "license_blocked") if getattr(app, "engine", None) is not None else {},
            "warning": None if usage == "personal" else f"상용 모드 — 상용 불가 소스 {len(high)}개는 자동으로 수집을 멈춤 (가드): {', '.join(high)}",
            "path": ["개발용(무료) 데이터", "상용 데이터 계약 (KRX·벤더)", "라이선스 범위 확인 (사용자 수·재배포)", "사용자 수 증가", "비용 관리"],
            "note": "여기 적힌 약관 해석은 참고용 — 각 제공자의 최신 약관과 계약서가 우선 (법률 검토 필요)"}


# ------------------------------------------------------------------ 라이선스 가드 (상용 모드에서 상용 불가 소스 자동 차단)
# 소스 → 상용(서비스) 사용 가능 여부. personal 이 아니면 False 인 소스는 수집·조회를 하지 않는다 (기존에 저장된 값은 그대로 표시)
COMMERCIAL_OK = {"krx_marcap": False, "yahoo": False, "naver": False, "stocktwits": False, "wiseindex": False,
                 "wiki": False, "datalab": False, "dart": True, "fred": True, "ecos": True, "kis": True, "rss": True,
                 "sec": True}
JOB_SOURCES = {"price_watch": ("naver", "yahoo"), "community": ("naver", "stocktwits"), "investor_flow": ("naver",),
               "kr_consensus": ("naver",), "gap_fill": ("yahoo",), "us_cycle": ("yahoo",), "wics": ("wiseindex",),
               "altdata": ("wiki", "datalab"), "sector_fill": ("yahoo",), "vkospi": ("krx_marcap",),
               "sec_filings": ("sec",)}


def source_allowed(settings, source: str) -> bool:
    if getattr(settings, "service_level", "personal") == "personal":
        return True
    return COMMERCIAL_OK.get(source, False)


def blocked_sources(settings) -> list[str]:
    return [k for k in COMMERCIAL_OK if not source_allowed(settings, k)]


def guard_scheduler(sch, app) -> list[str]:
    """스케줄러의 수집 작업을 라이선스 가드로 감싼다 — 실행 시점의 서비스 단계로 판단 (설정을 바꾸면 바로 적용)."""
    wrapped = []
    for job in sch.jobs:
        srcs = JOB_SOURCES.get(job.name)
        if not srcs:
            continue
        fn = job.fn

        def run(now, _fn=fn, _srcs=srcs, _name=job.name):
            bad = [x for x in _srcs if not source_allowed(app.settings, x)]
            if bad:
                st = ops.get_state(app.engine, "license_blocked")
                st[_name] = {"at": datetime.now(UTC).isoformat(), "sources": bad}
                ops.set_state(app.engine, "license_blocked", st)
                return {"skipped": "license", "sources": bad}
            return _fn(now)
        job.fn = run
        wrapped.append(job.name)
    return wrapped


# ------------------------------------------------------------------ 감사 로그 · 보안 현황
AUDIT_KEY = "audit_log"
AUDIT_MAX = 2000


def audit(engine, action: str, detail: str = "", actor: str = "web", ip: str | None = None) -> None:
    """민감한 조작 기록 (긴급 정지 · 설정 · 계좌 · 알림 규칙 · 투자 논리 · 모의 주문). 수정 API 없음 — 추가만."""
    st = ops.get_state(engine, AUDIT_KEY)
    rows = (st.get("rows") or [])[-(AUDIT_MAX - 1):]
    rows.append({"at": datetime.now(UTC).isoformat(), "actor": actor, "ip": ip, "action": action, "detail": str(detail)[:300]})
    ops.set_state(engine, AUDIT_KEY, {"rows": rows})


def audit_log(engine, limit: int = 200) -> list[dict]:
    rows = ops.get_state(engine, AUDIT_KEY).get("rows") or []
    return [r | {"as_of": label(r["at"])} for r in rows[-limit:][::-1]]


def security(app) -> dict:
    import os
    token = bool(os.environ.get("QUANT_WEB_TOKEN"))
    pw = bool(os.environ.get("QUANT_WEB_PASSWORD_HASH") or os.environ.get("QUANT_WEB_PASSWORD"))
    mfa = bool(os.environ.get("QUANT_WEB_TOTP_SECRET"))
    viewer = bool(os.environ.get("QUANT_WEB_VIEWER_TOKEN"))
    items = [
        ("비밀 키 보관", "partial", ".env 파일에만 (저장소·DB·화면에 표시 안 함) — 서비스화 시 Secret Manager 로 이전"),
        ("웹 접근 제어", "ok" if token else "partial", "원격 접속 시 토큰 필수 · 로컬(127.0.0.1)만 무토큰 허용" + (" · 토큰 설정됨" if token else "")),
        ("DNS 리바인딩 · CSRF", "ok", "허용 호스트 목록 · 쓰기 요청은 JSON + 같은 출처(Origin)만"),
        ("보안 헤더", "ok", "CSP · X-Frame-Options DENY · nosniff · 정적 경로 탈출 차단"),
        ("감사 로그", "ok", f"민감한 조작 {len(ops.get_state(app.engine, AUDIT_KEY).get('rows') or [])}건 기록 (추가만 가능)"),
        ("예측 장부 위변조 방지", "ok", "해시 봉인 · 해시 사슬 · 외부 공증(OpenTimestamps · 선택)"),
        ("백업 · 복구", "ok", "SQLite 백업 7개 보관 · 무결성 검사 · 재시작 따라잡기"),
        ("로그인 · 2단계 인증(MFA)", "ok" if (pw and mfa) else "partial" if (pw or token) else "missing",
         ("비밀번호 + OTP 6자리 · 실패 5번 잠금 · HttpOnly 세션" if pw and mfa else "비밀번호만 (OTP 미설정)" if pw else
          "토큰만" if token else "없음 (이 PC 전용 접속)") + " — ./run.sh auth-setup"),
        ("역할 권한(RBAC)", "partial" if viewer else "missing",
         "관리자 / 읽기 전용(QUANT_WEB_VIEWER_TOKEN: 조회만, 쓰기 403)" if viewer else "역할 1개 — 읽기 전용 토큰 미설정 · 사용자별 계정은 서비스화 때"),
        ("DB 암호화", "missing", "SQLite 평문 — 서비스화 시 디스크/컬럼 암호화 (계좌·거래내역)"),
        ("침투 테스트 · 취약점 관리", "missing", "미실시 — 서비스 전 외부 점검 필요"),
        ("AI 보안 (프롬프트 주입)", "partial", "외부 텍스트(뉴스·공시)는 '데이터'로만 넣고 지시문을 따르지 않게 프롬프트 고정 · AI 는 주문 권한 없음"),
    ]
    return {"items": [{"item": a, "status": b, "detail": c} for a, b, c in items],
            "score": round(sum({"ok": 1, "partial": 0.5, "missing": 0}[b] for _, b, _ in items) / len(items) * 100),
            "note": "개인 사용 기준 점검 — 금융 서비스 수준(MFA·RBAC·암호화·침투 테스트)은 아직 아님"}


__all__ = ["service_level", "licenses", "source_allowed", "blocked_sources", "guard_scheduler", "audit", "audit_log", "security", "LEVELS", "FEATURES", "LICENSES"]

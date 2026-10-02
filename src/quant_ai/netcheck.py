"""외부 연결 점검 — 데이터 소스마다 가장 작은 요청 1번 → 되는지 · 얼마나 걸리는지 · 안 되면 왜인지.

네트워크가 되는 환경으로 옮긴 직후, 또는 '데이터가 안 채워져요' 일 때 한 번 누르면 된다.
결과는 원인별로 나눈다: 정상 / 키 문제 / 네트워크·방화벽·프록시 차단 / 서버 오류 / 응답 형식 변경.
키가 필요한 소스(DART·FRED·ECOS)는 keys.probe 를 쓰고, 키 값은 결과에 남기지 않는다.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from . import ops
from .asof import label

# 이름 → (설명, URL, 응답 확인 함수) — 공개 엔드포인트만 (키 없음)
PUBLIC = {
    "krx_marcap": ("KRX 일봉 모음 (GitHub marcap)", "https://raw.githubusercontent.com/FinanceData/marcap/master/README.md", lambda b: len(b) > 100),
    "naver_quote": ("네이버 실시간 시세", "https://polling.finance.naver.com/api/realtime/domestic/stock/005930", lambda b: b"005930" in b),
    "naver_mobile": ("네이버 종목 정보 (수급·컨센서스)", "https://m.stock.naver.com/api/stock/005930/basic", lambda b: b"stockName" in b or b"005930" in b),
    "yahoo": ("Yahoo 일봉·해외 시세", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d", lambda b: b"chart" in b),
    "rss": ("뉴스 RSS (연합뉴스)", "https://www.yna.co.kr/rss/economy.xml", lambda b: b"<rss" in b[:500] or b"<?xml" in b[:100]),
    "kis_demo": ("KIS 모의투자 서버", "https://openapivts.koreainvestment.com:29443", lambda b: True),
}


def classify(err: str) -> tuple[str, str]:
    e = err.lower()
    if any(x in e for x in ("403", "407", "forbidden", "proxy", "tunnel")):
        return "blocked", "네트워크 정책·프록시·방화벽이 막음 (키 문제 아님)"
    if any(x in e for x in ("name or service", "temporary failure", "getaddrinfo", "nodename")):
        return "dns", "주소를 찾지 못함 (DNS·인터넷 연결)"
    if "timed out" in e or "timeout" in e:
        return "timeout", "응답 없음 (느린 네트워크·차단)"
    if "429" in e:
        return "rate", "요청이 너무 많음 (잠시 뒤 자동 재시도)"
    if any(x in e for x in ("http 5", " 500", " 502", " 503", " 504")):
        return "server", "상대 서버 오류"
    if "certificate" in e or "ssl" in e:
        return "tls", "보안 인증서 문제 (회사망 프록시 등)"
    return "error", err[:160]


def run(app, fetch=None) -> dict:
    from .data.collectors import http
    from .keys import probe
    fetch = fetch or (lambda url: http.get(url, timeout=6, retries=1))
    out = []
    for key, (title, url, ok_fn) in PUBLIC.items():
        t0 = time.monotonic()
        try:
            body = fetch(url)
            ok = bool(ok_fn(body))
            out.append({"key": key, "title": title, "status": "ok" if ok else "format", "ms": int((time.monotonic() - t0) * 1000),
                        "detail": "정상" if ok else "연결은 됐지만 응답 모양이 다름 (사이트 구조 변경 가능성)"})
        except Exception as e:  # noqa: BLE001
            msg = f"{e}" + (f" ← {e.__cause__}" if e.__cause__ else "")
            kind, why = classify(msg)
            if key == "kis_demo" and ("http 4" in msg.lower() or "404" in msg or "400" in msg):
                kind, why = "ok", "서버 응답함 (인증 전 요청이라 4xx 는 정상)"
            out.append({"key": key, "title": title, "status": kind, "ms": int((time.monotonic() - t0) * 1000), "detail": why})
    keyed = probe(app)["results"]
    names = {"dart": "공시 (DART · 키)", "fred": "거시 (FRED · 키)", "ecos": "한국은행 (ECOS · 키)"}
    for k, r in keyed.items():
        out.append({"key": k, "title": names[k], "status": "ok" if r["ok"] else "missing" if r["status"] == "missing" else "key_or_net",
                    "ms": r.get("ms"), "detail": r["message"]})
    n_ok = sum(1 for x in out if x["status"] == "ok")
    blocked = sum(1 for x in out if x["status"] in ("blocked", "dns", "timeout", "tls"))
    head = ("모든 외부 소스 정상" if n_ok == len([x for x in out if x["status"] != "missing"]) else
            f"네트워크가 막힌 환경 — {blocked}개 소스 연결 불가 (키·코드 문제 아님)" if blocked >= 4 else
            f"{len(out) - n_ok}개 소스 확인 필요")
    res = {"at": datetime.now(UTC).isoformat(), "as_of": label(datetime.now(UTC)), "rows": out, "ok": n_ok, "total": len(out), "headline": head}
    ops.set_state(app.engine, "netcheck", res)
    return res


def last(app) -> dict:
    return ops.get_state(app.engine, "netcheck")


__all__ = ["run", "last", "classify", "PUBLIC"]

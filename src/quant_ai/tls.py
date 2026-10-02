"""HTTPS 인증서 — macOS 의 python.org 파이썬처럼 시스템 인증서를 못 찾는 환경에서도 외부 소스(뉴스·공시·로고·시세)가 열리게.

기본 인증서(시스템 · SSL_CERT_FILE)에 certifi 묶음을 '더한' 컨텍스트를 urllib 기본값으로 쓴다.
회사·학교 프록시처럼 자체 인증서를 쓰는 환경은 SSL_CERT_FILE 로 그 인증서를 주면 그대로 함께 쓰인다 (검증을 끄지는 않는다).
"""

from __future__ import annotations

import ssl

_installed = False


def context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()  # 시스템 인증서 + SSL_CERT_FILE/SSL_CERT_DIR
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:  # noqa: BLE001, S110 - certifi 가 없으면 시스템 인증서만
        pass
    return ctx


def install() -> bool:
    """urllib(http.client)의 기본 HTTPS 컨텍스트를 위 컨텍스트로 바꾼다. 여러 번 불러도 한 번만."""
    global _installed
    if _installed:
        return True
    try:
        ssl._create_default_https_context = context  # noqa: SLF001 - urllib 이 쓰는 공개되지 않은 기본값
        _installed = True
    except Exception:  # noqa: BLE001
        return False
    return True

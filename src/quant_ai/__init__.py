"""Quant AI: 시장을 수집·분석·예측하고, 가상/그림자/실매매 후 스스로 복기하는 퀀트 시스템."""

__version__ = "0.36.0"

# 외부 HTTPS 를 쓰기 전에 인증서 묶음 준비 (macOS python.org 판에서 '인증서 확인 실패'로 뉴스·로고가 전부 안 오던 문제)
from . import tls as _tls  # noqa: E402

_tls.install()

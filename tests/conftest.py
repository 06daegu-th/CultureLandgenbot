"""테스트는 외부 네트워크를 쓰지 않는다: 종목 상세 소스(Yahoo·Nasdaq·네이버)는 기본으로 '연결 안 됨'."""

import pytest


def _offline(*_a, **_k):
    raise RuntimeError("offline (tests)")


@pytest.fixture(autouse=True)
def _no_profile_network(monkeypatch):
    from quant_ai.data import fundamentals
    monkeypatch.setattr(fundamentals, "DEFAULT_FETCHERS", {k: _offline for k in fundamentals.DEFAULT_FETCHERS})

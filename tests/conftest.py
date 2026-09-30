"""테스트는 외부 네트워크를 쓰지 않는다: 종목 상세·실시간 시세·커뮤니티 소스는 기본으로 '연결 안 됨'."""

import pytest


def _offline(*_a, **_k):
    raise RuntimeError("offline (tests)")


@pytest.fixture(autouse=True)
def _no_profile_network(monkeypatch):
    from quant_ai.data import fundamentals
    monkeypatch.setattr(fundamentals, "DEFAULT_FETCHERS", {k: _offline for k in fundamentals.DEFAULT_FETCHERS})
    from quant_ai.data import live_quotes
    from quant_ai.data.collectors import community
    monkeypatch.setattr(live_quotes, "fetch_kr", lambda codes: {})
    monkeypatch.setattr(live_quotes, "fetch_us", lambda syms: {})
    monkeypatch.setattr(community, "fetch_stocktwits", _offline)
    monkeypatch.setattr(community, "fetch_naver_board", _offline)
    from quant_ai.data.collectors import dart_docs, investor_flow
    from quant_ai.engines import sector
    monkeypatch.setattr(investor_flow, "fetch_naver", _offline)
    monkeypatch.setattr(dart_docs, "fetch_document", _offline)
    monkeypatch.setattr(sector, "fetch_sector", _offline)
    # v13 외부 소스
    from quant_ai.data.collectors import altdata, options, wics
    from quant_ai.engines import earnings
    from quant_ai.review import notary
    monkeypatch.setattr(options, "fetch_yahoo", _offline)
    monkeypatch.setattr(earnings, "fetch_yahoo", _offline)
    monkeypatch.setattr(altdata, "_get", _offline)
    monkeypatch.setattr(wics, "_get", _offline)
    monkeypatch.setattr(notary, "_post", _offline)

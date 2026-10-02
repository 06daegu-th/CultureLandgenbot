"""v18: 종목 로고 · 뉴스/공시 상세(원문·번역·쉬운 설명·중요도·영향 종목·AI 판단 변화) · 종목 '오늘 중요한 것' · 홈 5칸."""

import json
import threading
from dataclasses import replace
from datetime import timedelta
from http.client import HTTPConnection

import pandas as pd
import pytest

from quant_ai import ops

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from quant_ai.config import Settings
    from quant_ai.pipeline import QuantAI
    from tests.test_marcap import fake_marcap
    d = tmp_path_factory.mktemp("v18")
    fake_marcap(d, n_codes=6, days=320)
    a = QuantAI(replace(Settings.from_env({}), database_url=f"sqlite:///{d}/t.db", artifacts_dir=d / "a", max_data_age_days=0))
    a.ingest_krx(d, years=0, top_n=5, end_year=2020)
    return a


@pytest.fixture(scope="module")
def server(app):
    from http.server import ThreadingHTTPServer

    from quant_ai.web.api import DashboardAPI
    from quant_ai.web.server import make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardAPI(app), None, {"127.0.0.1", "localhost"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _get(port, path):
    c = HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.read(), dict(r.getheaders())


def _syms(app):
    return sorted(s for s in app.market_data()[0] if s[:1].isdigit())


# ------------------------------------------------------------------ 로고
def test_logo_sources_cache_fallback_and_safety(app):
    from quant_ai import logos
    calls = []

    def fetch(url):
        calls.append(url)
        if "financialmodelingprep" in url and "NVDA" in url:
            return PNG
        if "samsung.com" in url:
            return PNG
        if "evil" in url:
            return b"<svg onload=alert(1)>" + b" " * 400  # 외부 SVG 는 받지 않는다
        raise OSError("blocked")

    d, t, src = logos.get(app, "NVDA", "NVIDIA", fetch=fetch)
    assert t == "image/png" and src == "fmp" and d == PNG
    assert logos.get(app, "NVDA", fetch=fetch)[2] == "fmp" and len(calls) == 1  # 저장된 것 재사용
    d, t, src = logos.get(app, "005930", "삼성전자", fetch=fetch)
    assert src == "favicon" and "samsung.com" in calls[-1]
    ops.set_state(app.engine, "profile:EVIL", {"data": {"company": {"website": "https://evil.example"}}})
    d, t, src = logos.get(app, "EVIL", "Evil Corp", fetch=fetch)
    assert src == "monogram" and t == "image/svg+xml" and b"onload" not in d and b"EV" in d
    n = len(calls)
    assert logos.get(app, "EVIL", fetch=fetch)[2] == "monogram" and len(calls) == n  # 실패는 7일간 다시 시도 안 함
    mono = logos.monogram("000020", "종목1")
    assert b"\xec\xa2\x85" in mono  # '종'
    (logos._dir(app) / "custom" / "000030.png").write_bytes(PNG)
    assert logos.get(app, "000030", fetch=fetch)[2] == "custom"
    with pytest.raises(ValueError):
        logos.get(app, "../../etc", fetch=fetch)


def test_logo_route_is_public_and_cached(app, server, monkeypatch):
    monkeypatch.setenv("QUANT_LOGOS", "off")
    st, body, h = _get(server, "/api/logo/ZZZZ?n=Zeta%20Corp")
    assert st == 200 and h["Content-Type"] == "image/svg+xml" and body.startswith(b"<svg") and "max-age" in h["Cache-Control"]
    assert _get(server, "/api/logo/..%2F..")[0] in (200, 400)


# ------------------------------------------------------------------ 뉴스·공시 상세
def _add_news(app, rows):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import NewsArticle
    ids = []
    with session_scope(app.engine) as s:
        for i, (title, body, at, syms, src, cl) in enumerate(rows):
            a = NewsArticle(source=src, url=f"https://n.example/{i}/{abs(hash(title))}", published_at=at, title=title, body=body,
                            symbols=syms, sentiment=0.5, importance=0.8, cluster=cl, events=["earnings"])
            s.add(a)
            s.flush()
            ids.append(a.id)
    return ids


def test_news_detail_times_terms_level_chain_and_explain(app):
    from quant_ai import newsdetail
    sym = _syms(app)[0]
    at = pd.Timestamp(app.market_data()[0][sym].index[-15]).to_pydatetime().replace(hour=19, minute=32)
    ids = _add_news(app, [("Nvidia beats EPS estimates, raises guidance on HBM demand",
                           "EPS $1.12 vs $0.98 expected. Revenue $35.1 billion, up 94%. Export control risk remains.", at, [sym], "Reuters", "cx1"),
                          ("엔비디아 실적 예상 상회", "", at + timedelta(minutes=20), [sym], "연합뉴스", "cx1")])
    d = newsdetail.news_detail(app, ids[0])
    assert d["lang"] == "en" and d["time"]["text"] == f"{d['time']['et'][:-3]} ET · 한국시간 {d['time']['kst'][5:16].replace('-', '/')}"
    assert {"EPS", "guidance", "HBM", "export control"} <= {t["term"] for t in d["terms"]}
    assert "$1.12" in " ".join(n["value"] for n in d["numbers"]) and "94%" in " ".join(n["value"] for n in d["numbers"])
    assert d["level"]["icon"] in ("🔴", "🟠") and d["chain"][0] == {"symbol": sym, "name": d["chain"][0]["name"], "level": "🔴", "why": "기사에 직접 나옴"}
    assert d["siblings"][0]["source"] == "연합뉴스" and d["explain"]["by"] == "rule" and "AI 설명" in d["explain"]["note"]
    assert d["related"][0]["symbol"] == sym and "before_5d" in d["related"][0]

    class Fake:
        def complete_json(self, system, text, schema):
            assert "외부 데이터" in system and "Nvidia" in text
            return {"ko_title": "엔비디아, EPS 예상 상회·가이던스 상향", "ko_text": "EPS 1.12달러로 예상 0.98달러를 넘었다.",
                    "easy": "돈을 예상보다 많이 벌었고 앞으로도 더 벌 거라고 했어요.", "impact": "단기 긍정적일 수 있음",
                    "numbers": [{"label": "EPS", "value": "$1.12"}, "bad"], "tone": 0.7}
    r = newsdetail.explain_news(app, ids[0], client=Fake())
    assert r["by"] == "llm" and r["translation"].startswith("EPS") and r["numbers"] == [{"label": "EPS", "value": "$1.12"}]
    d2 = newsdetail.news_detail(app, ids[0])
    assert d2["explain"]["ko_title"].startswith("엔비디아") and d2["numbers"][0]["label"] == "EPS"  # 저장된 설명 재사용
    assert newsdetail.explain(app, "x", "", client=None)["error"].startswith("LLM 키")
    assert newsdetail.lang("トヨタ決算") == "ja" and newsdetail.lang("삼성전자 실적") == "ko"
    lo = newsdetail.level(0.2, "other", 0.0)
    hi = newsdetail.level(0.9, "earnings", 0.8, 4, 1.0, True)
    assert lo["icon"] == "⚪" and hi["icon"] == "🔴"
    found = newsdetail.search(app, "nvidia HBM", days=3650)
    assert [x["id"] for x in found] == [ids[0]] and newsdetail.search(app, "없는단어zz", 3650) == []


def test_disclosure_detail_and_routes(app, server):
    from quant_ai.data.db import session_scope
    from quant_ai.data.models import Disclosure
    sym = _syms(app)[0]
    with session_scope(app.engine) as s:
        d = Disclosure(source="DART", receipt_no="R18", symbol=sym, title="단일판매·공급계약체결 (계약금액 1,234억원, 매출액 대비 15.2%)",
                       filed_at=pd.Timestamp(app.market_data()[0][sym].index[-5]).date(), url="https://dart.example/R18", sentiment=0.4,
                       events=["contract"], summary="공급계약 1,234억원")
        s.add(d)
        s.flush()
        did = d.id
    c = HTTPConnection("127.0.0.1", server, timeout=30)
    c.request("GET", f"/api/disclosure/{did}")
    body = json.loads(c.getresponse().read())
    assert body["kind"] == "disclosure" and body["important"] and "1,234억원" in " ".join(n["value"] for n in body["numbers"])
    assert {"공급계약"} <= {t["term"] for t in body["terms"]} and "한국 공시일" in body["time"]["text"]
    c.request("GET", "/api/news-search?q=nvidia&days=3650")
    assert json.loads(c.getresponse().read())["results"]
    c.request("GET", "/api/news/999999")
    assert json.loads(c.getresponse().read())["error"] == "뉴스 없음"

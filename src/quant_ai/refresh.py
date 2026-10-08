"""v37 '지금 최신으로' — 버튼 하나로 화면에 쓰는 자료를 모두 새로 받고, 무엇이 됐고 무엇이 안 됐는지 그대로 알려 준다.

순서 (앞 단계가 실패해도 다음 단계는 계속):
  일봉(국내·미국) → 지금 시세 → 지수 → 뉴스 → 공시 → 경제지표 → 커뮤니티 → 외국인·기관 수급 → 신호 엔진 → AI 판단
각 단계: ok(받음) · skip(키·설정이 없어 건너뜀 + 켜는 법) · fail(쉬운 말 이유 + 기술 내용은 detail).
AI 판단은 관심·보유 종목만(최대 20개) — 무료 AI 한도를 아끼고, 주문은 내지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from . import ops

log = logging.getLogger(__name__)
STATE = "refresh_last"
AI_MAX = 20


def friendly(e: BaseException | str) -> str:
    """기술 오류 → 사람이 할 수 있는 말."""
    m = str(e)
    low = m.lower()
    if any(k in low for k in ("urlerror", "get 실패", "timed out", "timeout", "tunnel", "connection", "name or service", "network is unreachable",
                              "temporary failure", "max retries", "proxy")):
        return "인터넷 연결이 안 되거나 상대 서버에 닿지 못했어요"
    if any(k in m for k in ("401", "403", "Unauthorized", "Forbidden")) or "invalid" in low and "key" in low:
        return "키가 틀렸거나 권한이 없어요 — 설정 → 키에서 확인"
    if any(k in m for k in ("429", "Too Many")):
        return "요청이 너무 많다고 거절됐어요 — 잠시 뒤 다시"
    if any(k in m for k in ("500", "502", "503", "504")):
        return "상대 서버가 응답하지 않았어요 — 잠시 뒤 다시"
    if "git" in low and ("not found" in low or "no such file" in low):
        return "일봉을 받는 데 필요한 git 이 없어요 — ./run.sh doctor 로 확인"
    return "받지 못했어요"


def _focus(app) -> list[str]:
    from .alerts import focus_symbols
    try:
        return list(focus_symbols(app))
    except Exception:  # noqa: BLE001
        return []


def steps(app, fetchers: dict | None = None) -> list[tuple[str, str, Callable[[], dict | str | None]]]:
    """(key, 이름, 실행) — 실행이 dict 를 주면 ok, {'skip': 이유} 면 건너뜀, 예외면 실패."""
    f = fetchers or {}
    st = app.settings

    def bars_kr():
        if "bars_kr" in f:
            return f["bars_kr"](app)
        from .data.collectors.marcap import default_dir, sync_marcap
        d = default_dir()
        if not d:
            return {"skip": "국내 일봉 자동 받기가 아직 설정되지 않았어요 — 처음 한 번 PC 에서 ./run.sh data"}
        sync_marcap(d)
        app.ingest_krx(d, years=3)
        return {"dir": "marcap"}

    def bars_us():
        us = [s for s in _focus(app) if not s[:1].isdigit()]
        if not us:
            return {"skip": "관심·보유 미국 종목이 없어요"}
        if "bars_us" in f:
            r = f["bars_us"](app, us)
        else:
            from .data.global_stocks import ensure_many
            from .global_market import BENCH
            r = ensure_many(app.engine, [BENCH, *us])  # 버튼은 내 종목만 (유니버스 전체는 24시간 운영이 받는다)
        failed = list((r or {}).get("failed") or [])
        if failed and len(failed) >= len(us) + 1:
            raise RuntimeError("URLError: 미국 일봉 0건")
        return {"symbols": len(us), "failed": len(failed)}

    def quotes():
        from .alerts import price_watch
        r = price_watch(app, fetchers=f.get("quotes"), markets={"KR", "US"})
        if not r.get("checked"):
            raise RuntimeError("시세 0건 — URLError(network)" if _focus(app) else "관심·보유 종목 없음")
        return {"checked": r["checked"]}

    def indices():
        from .data.collectors.indices import collect_indices, load_indices
        r = collect_indices(app.engine, get=f.get("indices")) or {}
        if not r.get("ok") and r.get("failed"):
            errs = [(v or {}).get("error") for k, v in load_indices(app.engine).items() if k in r["failed"]]
            raise RuntimeError(next((e for e in errs if e), "지수 0건"))
        return {"n": len(r.get("ok") or [])}

    def news():
        if not st.news_feeds and "news" not in f:
            return {"skip": "뉴스 피드 설정이 비어 있어요 (.env QUANT_NEWS_FEEDS)"}
        from .data.db import session_scope
        n = 0
        if "news" in f:
            n = f["news"](app)
        else:
            from .data.collectors.news import NewsCollector, collect_stock_news
            with session_scope(app.engine) as s:
                n = NewsCollector(st.news_feeds).collect(s)
            kr = [x for x in _focus(app) if x[:1].isdigit()][:30]
            if kr:
                with session_scope(app.engine) as s:
                    r = collect_stock_news(s, kr)
                n = (n if isinstance(n, int) else 0) + int((r or {}).get("added") or 0)
        return {"added": n}

    def disclosures():
        from .keys import refresh
        refresh(app, force=True)
        if not app.settings.dart_api_key and "disclosures" not in f:
            return {"skip": "DART 키가 없어요 — 무료 발급 후 .env 의 DART_API_KEY (설정 → 키)"}
        if "disclosures" in f:
            return {"added": f["disclosures"](app)}
        from .data.collectors.disclosures import DartCollector
        from .data.db import session_scope
        with session_scope(app.engine) as s:
            n = DartCollector(app.settings.dart_api_key).collect(s, date.today() - timedelta(days=7), date.today())
        return {"added": n}

    def macro():
        if not app.settings.fred_api_key and "macro" not in f:
            return {"skip": "FRED 키가 없어요 — 무료 발급 후 .env 의 FRED_API_KEY (설정 → 키)"}
        if "macro" in f:
            return {"added": f["macro"](app)}
        from .data.collectors.macro import FredCollector
        from .data.db import session_scope
        fc = FredCollector(app.settings.fred_api_key)
        with session_scope(app.engine) as s:
            n = fc.collect(s, date.today() - timedelta(days=30))
        if fc.errors and not n:
            raise RuntimeError("; ".join(fc.errors))
        return {"added": n}

    def community():
        from .data.collectors.community import collect, symbols_for
        syms = symbols_for(app)
        if not syms:
            return {"skip": "관심·보유 종목이 없어요"}
        ops.set_state(app.engine, "community_fail", {})  # 직접 누른 것이니 '최근 실패 → 잠시 쉼'을 풀고 다시 시도
        r = collect(app.engine, syms, fetchers=f.get("community")) or {}
        if not r.get("collected"):
            raise RuntimeError("; ".join(r.get("failed") or []) or "커뮤니티 0건")
        return {"collected": len(r["collected"]), "failed": len(r.get("failed") or [])}

    def flow():
        from .data.collectors.investor_flow import collect
        kr = [x for x in _focus(app) if x[:1].isdigit()][:30]
        if not kr:
            return {"skip": "관심·보유 국내 종목이 없어요"}
        ops.set_state(app.engine, "flow_fail", {})
        r = collect(app.engine, kr, fetch=f.get("flow"), pause=0 if f.get("flow") else 0.3) or {}
        if not r.get("collected"):
            raise RuntimeError("; ".join(r.get("failed") or []) or "수급 0건")
        return {"collected": r["collected"], "failed": len(r.get("failed") or [])}

    def signals():
        from . import signals2 as S2
        S2._CACHE.clear()
        out = S2.cached(app, "KR")
        if out.get("error"):
            return {"skip": out["error"]}
        return {"as_of": out.get("as_of"), "buy": len(out.get("buy") or [])}

    def ai():
        kr = [x for x in _focus(app) if x[:1].isdigit()][:AI_MAX]
        if not kr:
            return {"skip": "관심·보유 국내 종목이 없어요 — 종목에 ★ 를 누르면 AI 가 매일 판단해요"}
        ds = app.decide(symbols=kr, persist=True)
        return {"judged": len(ds)}

    return [("bars_kr", "국내 일봉", bars_kr), ("bars_us", "미국 일봉", bars_us), ("quotes", "지금 시세 (관심·보유)", quotes),
            ("indices", "지수", indices), ("news", "뉴스", news), ("disclosures", "공시 (DART)", disclosures),
            ("macro", "경제지표 (FRED)", macro), ("community", "커뮤니티", community), ("flow", "외국인·기관 수급", flow),
            ("signals", "신호 엔진 다시 계산", signals), ("ai", "AI 판단 (관심·보유)", ai)]


NETWORK = {"bars_kr", "bars_us", "quotes", "indices", "news", "disclosures", "macro", "community", "flow"}
FRESH_INPUTS = {"bars_kr", "bars_us", "quotes", "news", "disclosures", "macro", "community", "flow"}


def precheck(app, key: str) -> str | None:
    """인터넷과 상관없이 먼저 알 수 있는 '건너뛸 이유' (키·설정·대상 종목 없음) — 인터넷이 안 될 때도 이것부터 알려 준다."""
    st = app.settings
    focus = _focus(app)
    if key == "bars_kr":
        from .data.collectors.marcap import default_dir
        return None if default_dir() else "국내 일봉 자동 받기가 아직 설정되지 않았어요 — 처음 한 번 PC 에서 ./run.sh data"
    if key == "bars_us" and not any(not x[:1].isdigit() for x in focus):
        return "관심·보유 미국 종목이 없어요"
    if key == "news" and not st.news_feeds:
        return "뉴스 피드 설정이 비어 있어요 (.env QUANT_NEWS_FEEDS)"
    if key == "disclosures" and not st.dart_api_key:
        return "DART 키가 없어요 — 무료 발급 후 .env 의 DART_API_KEY (설정 → 키)"
    if key == "macro" and not st.fred_api_key:
        return "FRED 키가 없어요 — 무료 발급 후 .env 의 FRED_API_KEY (설정 → 키)"
    if key == "flow" and not any(x[:1].isdigit() for x in focus):
        return "관심·보유 국내 종목이 없어요"
    return None


def online(probe=None) -> tuple[bool, str | None]:
    """인터넷이 되나 — 작은 요청 2개 중 하나라도 되면 됨 (각 5초). 안 되면 나머지 단계를 기다리지 않는다."""
    if probe is not None:
        return probe()
    from .data.global_stocks import _http
    err = None
    for url in ("https://polling.finance.naver.com/api/realtime/domestic/stock/005930",
                "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d&interval=1d"):
        try:
            _http(url, timeout=5)
            return True, None
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:200]
    return False, err


def run(app, say: Callable[[str], None] | None = None, fetchers: dict | None = None, only: set[str] | None = None) -> dict:
    say = say or (lambda m: None)
    started = datetime.now(UTC)
    rows = []
    todo = [s for s in steps(app, fetchers) if not only or s[0] in only]
    say("인터넷 연결 확인 중…")
    net_ok, net_err = online((fetchers or {}).get("online"))
    for i, (key, name, fn) in enumerate(todo, 1):
        say(f"{i}/{len(todo)} {name} 받는 중…")
        t0 = datetime.now(UTC)
        pre = precheck(app, key) if key in NETWORK and not net_ok and not (fetchers or {}).get(key) else None
        if pre:
            rows.append({"key": key, "name": name, "status": "skip", "why": pre, "sec": 0.0})
            continue
        if key in NETWORK and not net_ok and not (fetchers or {}).get(key):
            rows.append({"key": key, "name": name, "status": "fail", "why": "인터넷 연결이 안 되거나 상대 서버에 닿지 못했어요",
                         "detail": net_err or "", "sec": 0.0})
            continue
        if key == "ai" and not any(r["status"] == "ok" and r["key"] in FRESH_INPUTS for r in rows):
            rows.append({"key": key, "name": name, "status": "skip", "sec": 0.0,
                         "why": "새로 들어온 자료가 없어 다시 판단하지 않았어요 (같은 자료로 다시 판단해도 결과가 같고 무료 AI 한도만 써요)"})
            continue
        try:
            r = fn() or {}
            if isinstance(r, dict) and r.get("skip"):
                rows.append({"key": key, "name": name, "status": "skip", "why": r["skip"]})
            else:
                rows.append({"key": key, "name": name, "status": "ok", "result": r if isinstance(r, dict) else {"value": r}})
        except Exception as e:  # noqa: BLE001 - 한 단계 실패가 다음 단계를 막지 않게
            log.info("새로 받기 %s 실패: %s", key, e)
            rows.append({"key": key, "name": name, "status": "fail", "why": friendly(e), "detail": f"{type(e).__name__}: {e}"[:200]})
        rows[-1]["sec"] = round((datetime.now(UTC) - t0).total_seconds(), 1)
    try:  # 지금 무엇이 최신인지 (화면 카드와 같은 계산)
        from .aiinputs import coverage
        cov = coverage(app)
    except Exception as e:  # noqa: BLE001
        cov = {"error": str(e)[:200]}
    ok = sum(r["status"] == "ok" for r in rows)
    fail = [r for r in rows if r["status"] == "fail"]
    net = sum("인터넷" in (r.get("why") or "") for r in fail)
    if not fail:
        head = f"모두 새로 받았어요 ({ok}개)" + (f" · {len(rows) - ok}개는 설정이 없어 건너뜀" if ok < len(rows) else "")
    elif net >= max(3, len(fail) - 1):
        head = "인터넷에 연결되지 않아 대부분 받지 못했어요 — 연결을 확인하고 다시 눌러 주세요"
    else:
        head = f"{ok}개 받음 · {len(fail)}개 실패 — 아래 이유를 확인해 주세요"
    out = {"online": net_ok, "at": started.isoformat(), "sec": round((datetime.now(UTC) - started).total_seconds(), 1), "steps": rows,
           "ok": ok, "fail": len(fail), "skip": sum(r["status"] == "skip" for r in rows), "headline": head, "coverage": cov}
    ops.set_state(app.engine, STATE, {k: v for k, v in out.items() if k != "coverage"})
    return out


def last(app) -> dict:
    return ops.get_state(app.engine, STATE)


__all__ = ["run", "steps", "friendly", "last"]

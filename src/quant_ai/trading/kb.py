"""KB증권 OpenAPI (B2C) — 읽기 전용 연결.

근거: KB증권 공식 GitHub 샘플 (github.com/kbsecurities/kb-openapi)
  - example/python/auth_example.py     POST /oauth2/token · dataHeader{ipAddr,macAddr} + dataBody{appKey,appSecret,grantType}
  - example/python/common.py           헤더 Content-Type · appKey · Authorization: bearer <token> (소문자 bearer)
  - example/python/investment_info_example.py · samples.generated.json
        IVU10140 주식현재가  POST /api/v1/ivu10140  {excg_clsf, shrt_cd}
        SZQM0771 장운영상태  POST /api/v1/szqm0771  {}
        IVA60190 환율종합    POST /api/v1/iva60190  {}
  - 환경변수 이름도 샘플과 같다: KB_OPENAPI_APP_KEY · KB_OPENAPI_APP_SECRET · KB_OPENAPI_BASE_URL

하지 않는 것 (공식 샘플에 없어서 추측하지 않는다):
  잔고 · 보유 종목 · 체결 내역 · 주문 · 취소 — 샘플의 config/brokers/kb/endpoints.yaml 에서 경로가 비어 있다.
  → 이 기능들은 KBNotVerified 로 멈춘다. 공식 개발 가이드(openapi.kbsec.com/guide_b2c)의 전문 규격을 확인한 뒤
    여기에 TR 을 추가하고 모의 검증을 거쳐야 한다. 그때까지 QUANT_BROKER=kb 의 실전 주문은 LiveBroker 가 막는다.
  웹소켓(실시간)도 KB 쪽이 개발 중이라 쓰지 않는다.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .broker import MarketQuote

BASE_URL = "https://developer.kbsec.com:32484"
SOURCE = "KB증권 공식 GitHub 샘플 (kbsecurities/kb-openapi)"
GUIDE = "https://openapi.kbsec.com/guide_b2c"
TR = {  # 공식 샘플에 있는 것만
    "token": "/oauth2/token",
    "quote": "/api/v1/ivu10140",
    "market_status": "/api/v1/szqm0771",
    "fx": "/api/v1/iva60190",
}
NOT_IN_SAMPLE = ("balance", "holdings", "fills", "place_order", "cancel_order")
MIN_INTERVAL = 0.2  # 초 — 샘플이 '과도하게 반복 호출하면 계정이 제한될 수 있다'고 경고한다
KST = ZoneInfo("Asia/Seoul")

Transport = Callable[[str, dict, dict], dict]


class KBError(RuntimeError):
    def __init__(self, code: str, msg: str):
        super().__init__(f"[{code}] {msg}")
        self.code = code


class KBNotVerified(KBError):
    """공식 샘플에 없는 기능 — 추측해서 부르지 않는다."""

    def __init__(self, feature: str):
        super().__init__("not_verified", f"{feature}: KB 공식 샘플에 요청 경로·필드가 없어요 — 공식 개발 가이드({GUIDE}) 확인 필요")
        self.feature = feature


def urllib_transport(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)  # noqa: S310 - 고정 KB 도메인
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise KBError(str(exc.code), str(exc.reason)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KBError("json", f"JSON 이 아닌 응답: {raw[:120]!r}") from exc


def _f(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


@dataclass
class KBQuote:
    code: str
    name: str | None
    last: float
    prev_close: float | None
    change_pct: float | None
    bid: float | None
    ask: float | None
    volume: float | None
    received: str | None  # 호가수신시간 HHMMSS (KST)
    exchange: str

    def market_quote(self) -> MarketQuote:
        return MarketQuote(last=self.last, bid=self.bid, ask=self.ask)

    def age_seconds(self, now: datetime | None = None) -> float | None:
        """호가 수신 시각이 오늘 KST 기준 몇 초 전인가 (모르면 None)."""
        t = (self.received or "").strip()
        if len(t) != 6 or not t.isdigit():
            return None
        now = (now or datetime.now(UTC)).astimezone(KST)
        at = now.replace(hour=int(t[:2]), minute=int(t[2:4]), second=int(t[4:]), microsecond=0)
        return (now - at).total_seconds()


class KBClient:
    def __init__(self, app_key: str, app_secret: str, base_url: str | None = None, transport: Transport | None = None):
        base = (base_url or BASE_URL).rstrip("/")
        host = urllib.parse.urlparse(base).hostname
        if base != BASE_URL and host not in ("127.0.0.1", "localhost"):
            raise ValueError("KB_OPENAPI_BASE_URL 재정의는 로컬 테스트 서버(127.0.0.1)만 허용")
        if not base.startswith("https://") and host not in ("127.0.0.1", "localhost"):
            raise ValueError("KB OpenAPI 는 HTTPS 만 허용")
        self.app_key, self.app_secret, self.base = app_key, app_secret, base
        self.transport = transport or urllib_transport
        self._token: tuple[str, datetime] | None = None
        self._last = 0.0
        self._lock = threading.Lock()
        self.calls = 0

    @classmethod
    def from_env(cls, env: dict | None = None, transport: Transport | None = None) -> KBClient:
        e = os.environ if env is None else env
        miss = [k for k in ("KB_OPENAPI_APP_KEY", "KB_OPENAPI_APP_SECRET") if not e.get(k)]
        if miss:
            raise KBError("config", f"환경변수 누락: {', '.join(miss)}")
        return cls(e["KB_OPENAPI_APP_KEY"], e["KB_OPENAPI_APP_SECRET"], e.get("KB_OPENAPI_BASE_URL") or None, transport)

    def _throttle(self) -> None:
        with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    @staticmethod
    def _body(r: dict) -> dict:
        b = r.get("dataBody", r) if isinstance(r, dict) else None
        if not isinstance(b, dict):
            raise KBError("format", "응답에 dataBody 가 없어요")
        return b

    def token(self) -> str:
        now = datetime.now(UTC)
        if self._token and self._token[1] - now > timedelta(minutes=5):
            return self._token[0]
        self._throttle()
        self.calls += 1
        r = self.transport(f"{self.base}{TR['token']}", {"Content-Type": "application/json"},
                           {"dataHeader": {"ipAddr": "", "macAddr": ""},
                            "dataBody": {"appKey": self.app_key, "appSecret": self.app_secret, "grantType": "client_credentials"}})
        b = self._body(r)
        tok = b.get("access_token") or b.get("accessToken")
        if not tok:
            hdr = r.get("dataHeader") or {}
            raise KBError("token", str(hdr.get("resultMessage") or hdr.get("msg") or b.get("msg") or "access_token 없음")[:160])
        try:
            ttl = int(b.get("expires_in") or 3600)
        except (TypeError, ValueError):
            ttl = 3600
        self._token = (tok, now + timedelta(seconds=ttl))
        return tok

    def call(self, feature: str, data_body: dict) -> dict:
        if feature in NOT_IN_SAMPLE or feature not in TR:
            raise KBNotVerified(feature)
        headers = {"Content-Type": "application/json", "appKey": self.app_key, "Authorization": f"bearer {self.token()}"}
        self._throttle()
        self.calls += 1
        r = self.transport(f"{self.base}{TR[feature]}", headers, {"dataHeader": {"ipAddr": "", "macAddr": ""}, "dataBody": data_body})
        return self._body(r)

    # ------------------------------------------------------------ 읽기 (샘플에 있는 TR)
    def quote(self, code: str, exchange: str = "1") -> KBQuote:
        """IVU10140 주식현재가. exchange: 0 통합 · 1 KRX · 2 NXT."""
        b = self.call("quote", {"excg_clsf": exchange, "shrt_cd": code})
        last = _f(b.get("now_prc"))
        if not last:
            raise KBError("quote", str(b.get("msg") or "현재가(now_prc) 없음")[:160])
        return KBQuote(code=code, name=(b.get("is_nm") or "").strip() or None, last=last, prev_close=_f(b.get("bdy_cls_prc")),
                       change_pct=None if _f(b.get("up_dwn_r_p2")) is None else _f(b.get("up_dwn_r_p2")) / 100,
                       bid=_f(b.get("b_sq1_askprc")), ask=_f(b.get("s_sq1_askprc")), volume=_f(b.get("acml_vlm")),
                       received=(b.get("askprc_rcp_tm") or None), exchange={"0": "통합", "1": "KRX", "2": "NXT"}.get(exchange, exchange))

    def market_status(self) -> dict:
        """SZQM0771 장운영상태 — 코드값의 뜻은 공식 가이드에서 확인해야 해서 원래 값을 그대로 돌려준다."""
        b = self.call("market_status", {})
        return {k: b.get(k) for k in ("now_dt", "now_tm", "bsnss_dt", "next_biz_dt", "stk_mkoprt_ccd", "ksdq_mkoprt_ccd", "on_clsng_f")}

    def fx(self) -> list[dict]:
        """IVA60190 환율종합 — 반복부 배열 이름이 샘플에 없어 dataBody 안 첫 번째 목록을 쓴다."""
        b = self.call("fx", {})
        rows = next((v for v in b.values() if isinstance(v, list)), [])
        return [{"currency": (r.get("crncy_cd") or "").strip(), "name": r.get("crncy_cd_nm"), "close": _f(r.get("cls_prc_p4")),
                 "change_pct": None if _f(r.get("bdy_cmpr_r_p2")) is None else _f(r.get("bdy_cmpr_r_p2")) / 100,
                 "received": r.get("fnl_rcp_tm")} for r in rows if isinstance(r, dict)]

    # ------------------------------------------------------------ 샘플에 없는 것 — 추측하지 않는다
    def balance(self):
        raise KBNotVerified("balance")

    def fills(self, *a, **k):
        raise KBNotVerified("fills")

    def place_order(self, *a, **k):
        raise KBNotVerified("place_order")

    def cancel(self, *a, **k):
        raise KBNotVerified("cancel_order")


def configured(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return bool(e.get("KB_OPENAPI_APP_KEY") and e.get("KB_OPENAPI_APP_SECRET"))


def check(app=None, symbol: str = "005930", client: KBClient | None = None, env: dict | None = None,
          now: datetime | None = None, max_age: float = 120.0) -> dict:
    """읽기 전용 점검 단계 — 설정 → 토큰 → 현재가(신선도) → 장 상태 → 환율 → 계좌/주문(공식 가이드 확인 필요).
    키 값은 어디에도 남기지 않는다. 결과는 ops 'kb_validation' 에 저장."""
    now = now or datetime.now(UTC)
    steps: list[dict] = []

    def step(key, title, fn):
        t0 = time.monotonic()
        try:
            detail = fn()
            steps.append({"key": key, "title": title, "ok": True, "detail": detail, "ms": round((time.monotonic() - t0) * 1000)})
            return True
        except KBNotVerified as e:
            steps.append({"key": key, "title": title, "ok": None, "detail": str(e), "ms": 0})
            return True
        except Exception as e:  # noqa: BLE001
            steps.append({"key": key, "title": title, "ok": False, "detail": f"{type(e).__name__}: {e}"[:240],
                          "ms": round((time.monotonic() - t0) * 1000)})
            return False

    c = {"client": client}

    def s_cfg():
        if c["client"] is None:
            c["client"] = KBClient.from_env(env)
        return "앱 키·시크릿 설정됨 (값은 표시하지 않아요)"

    def s_tok():
        tok = c["client"].token()
        return f"토큰 발급 OK ({len(tok)}자)"

    def s_quote():
        q = c["client"].quote(symbol)
        age = q.age_seconds(now)
        c["quote"] = q
        fresh = "" if age is None else (f" · 호가 {int(age)}초 전" if 0 <= age <= max_age else f" · 호가 시각 {q.received} (오래됨 또는 장외)")
        return f"{q.name or symbol} {q.last:,.0f}원 · 매수1 {f'{q.bid:,.0f}' if q.bid else '-'} / 매도1 {f'{q.ask:,.0f}' if q.ask else '-'}{fresh}"

    def s_mkt():
        m = c["client"].market_status()
        return f"영업일 {m.get('bsnss_dt') or '-'} · 주식장 코드 {m.get('stk_mkoprt_ccd') or '-'} (코드 뜻은 공식 가이드 확인)"

    def s_fx():
        rows = c["client"].fx()
        usd = next((r for r in rows if "USD" in (r["currency"] or "")), None)
        return f"USD {usd['close']:,.2f}" if usd and usd.get("close") else f"{len(rows)}개 통화"

    def s_acct():
        c["client"].balance()

    ok = step("config", "설정", s_cfg)
    ok = ok and step("token", "토큰", s_tok)
    ok = ok and step("quote", "현재가", s_quote)
    if ok:
        step("market", "장 상태", s_mkt)
        step("fx", "환율", s_fx)
        step("account", "잔고·체결·주문", s_acct)
    passed = all(s["ok"] is not False for s in steps) and any(s["key"] == "quote" and s["ok"] for s in steps)
    out = {"at": now.isoformat(), "ok": passed, "steps": steps, "symbol": symbol, "source": SOURCE, "guide": GUIDE,
           "read_only": True, "orders": "막힘 — 공식 샘플에 주문·잔고 TR 이 없어 연결하지 않았어요",
           "calls": c["client"].calls if c.get("client") else 0}
    if app is not None:
        from .. import ops
        ops.set_state(app.engine, "kb_validation", out)
    return out


def status(app) -> dict:
    """화면·doctor 용 요약 (네트워크 없음)."""
    from .. import ops
    v = ops.get_state(app.engine, "kb_validation") or {}
    return {"configured": configured(), "selected": getattr(app.settings, "broker", "none") == "kb", "last": v or None,
            "read_only": True, "orders_enabled": False, "source": SOURCE, "guide": GUIDE}


__all__ = ["KBClient", "KBError", "KBNotVerified", "KBQuote", "check", "configured", "status", "TR", "NOT_IN_SAMPLE"]

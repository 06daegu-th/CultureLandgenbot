"""KIS 실시간 체결가 (웹소켓) — 급등락·규칙 알림을 2.5분 폴링에서 초 단위로.

흐름: 접속키 발급(/oauth2/Approval) → ws://ops.koreainvestment.com (실전 21000 · 모의 31000) 접속 →
      관심·보유·코어·규칙 종목을 H0STCNT0(국내 주식 체결가)로 구독 (한 세션 최대 40종목) →
      체결이 들어오면 메모리에 모았다가 3초마다 alerts.ingest_quotes 로 한 번에 넘긴다 (DB 부담 제한).
장이 닫히면 연결을 끊고, 끊기면 지수 백오프(5초 → 최대 5분)로 다시 붙는다.
표시·알림 전용이다 — 주문은 이 값을 쓰지 않는다 (주문 직전 REST 시세로 다시 확인).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.request
from datetime import UTC, datetime

log = logging.getLogger(__name__)
WS_URL = {"real": "ws://ops.koreainvestment.com:21000", "demo": "ws://ops.koreainvestment.com:31000"}
TR_TRADE = "H0STCNT0"
MAX_SUBS = 40
FLUSH_S = 3.0
SIGN_DOWN = {"4", "5"}  # 하한 · 하락


def parse_trade(msg: str) -> list[dict]:
    """'0|H0STCNT0|002|필드^필드^…' → 체결 목록. 한 메시지에 여러 건이 이어 붙어 올 수 있다 (46필드씩)."""
    parts = msg.split("|", 3)
    if len(parts) < 4 or parts[1] != TR_TRADE:
        return []
    try:
        n = int(parts[2])
    except ValueError:
        n = 1
    f = parts[3].split("^")
    width = len(f) // max(n, 1)
    out = []
    for i in range(max(n, 1)):
        r = f[i * width:(i + 1) * width]
        if len(r) < 14:
            continue
        try:
            price = float(r[2])
            sign = r[3]
            chg, rate = abs(float(r[4])), abs(float(r[5]))
            if sign in SIGN_DOWN:
                chg, rate = -chg, -rate
            out.append({"symbol": r[0], "time": r[1], "price": price, "chg": chg, "chg_pct": rate / 100,
                        "volume": float(r[13]), "src": "kis_ws"})
        except (ValueError, IndexError):
            continue
    return out


def approval_key(base: str, app_key: str, app_secret: str, timeout: float = 10.0) -> str:
    body = json.dumps({"grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret}).encode()
    req = urllib.request.Request(f"{base}/oauth2/Approval", data=body, method="POST",  # noqa: S310 - KIS https 고정
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        key = json.loads(r.read()).get("approval_key")
    if not key:
        raise RuntimeError("KIS 웹소켓 접속키 발급 실패")
    return key


def subscribe_msg(key: str, code: str, subscribe: bool = True) -> str:
    return json.dumps({"header": {"approval_key": key, "custtype": "P", "tr_type": "1" if subscribe else "2",
                                  "content-type": "utf-8"},
                       "body": {"input": {"tr_id": TR_TRADE, "tr_key": code}}})


class KISRealtime:
    """백그라운드 스레드 하나. ensure()를 주기적으로 부르면 필요할 때만 켜고 끈다."""

    def __init__(self, app, symbols_fn, on_quotes=None, url: str | None = None, key_fn=None):
        self.app = app
        self.symbols_fn = symbols_fn  # () -> list[str]  (국내 6자리만 쓴다)
        self.on_quotes = on_quotes  # (quotes: dict) -> None · 기본: alerts.ingest_quotes
        self.url = url
        self.key_fn = key_fn
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._buf: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.status = {"state": "off", "subs": 0, "ticks": 0, "last_tick": None, "error": None, "reconnects": 0}

    # ---- 제어
    def ensure(self, market_open: bool) -> dict:
        alive = self._thread is not None and self._thread.is_alive()
        if market_open and not alive:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="kis-ws", daemon=True)
            self._thread.start()
        elif not market_open and alive:
            self._stop.set()
        return dict(self.status)

    def stop(self) -> None:
        self._stop.set()

    # ---- 내부
    def _key(self) -> str:
        if self.key_fn:
            return self.key_fn()
        from pathlib import Path

        from .kis import KISClient
        c = KISClient.from_env(Path(self.app.settings.artifacts_dir))
        return approval_key(c.base, c.app_key, c.app_secret)

    def _run(self) -> None:
        delay = 5.0
        while not self._stop.is_set():
            try:
                asyncio.run(self._session())
                delay = 5.0
            except Exception as e:  # noqa: BLE001 - 끊기면 다시 붙는다
                self.status.update(state="retry", error=f"{type(e).__name__}: {str(e)[:160]}")
                self.status["reconnects"] += 1
                log.warning("KIS 웹소켓 재접속 대기 %.0fs: %s", delay, e)
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 300.0)
        self.status["state"] = "off"

    async def _session(self) -> None:
        import websockets
        key = self._key()
        url = self.url or WS_URL.get(self.app.settings.kis_env, WS_URL["demo"])
        codes = [s for s in self.symbols_fn() if s.isdigit()][:MAX_SUBS]
        async with websockets.connect(url, ping_interval=None, open_timeout=15, close_timeout=5) as ws:
            for c in codes:
                await ws.send(subscribe_msg(key, c))
                await asyncio.sleep(0.05)
            self.status.update(state="on", subs=len(codes), error=None, since=datetime.now(UTC).isoformat())
            last_flush = time.monotonic()
            while not self._stop.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except TimeoutError:
                    msg = None
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8", "replace")
                if msg:
                    if msg[:1] in ("0", "1"):
                        for t in parse_trade(msg):
                            with self._lock:
                                self._buf[t["symbol"]] = t
                            self.status["ticks"] += 1
                            self.status["last_tick"] = datetime.now(UTC).isoformat()
                    else:
                        try:
                            j = json.loads(msg)
                        except json.JSONDecodeError:
                            j = {}
                        if (j.get("header") or {}).get("tr_id") == "PINGPONG":
                            await ws.send(msg)  # KIS 는 받은 PINGPONG 을 그대로 돌려줘야 연결을 유지한다
                        elif (j.get("body") or {}).get("rt_cd") not in (None, "0"):
                            log.warning("KIS 구독 응답: %s", (j.get("body") or {}).get("msg1"))
                if time.monotonic() - last_flush >= FLUSH_S:
                    self.flush()
                    last_flush = time.monotonic()
            self.flush()

    def flush(self) -> int:
        with self._lock:
            buf, self._buf = self._buf, {}
        if not buf:
            return 0
        try:
            if self.on_quotes:
                self.on_quotes(buf)
            else:
                from ..alerts import ingest_quotes
                ingest_quotes(self.app, buf)
        except Exception as e:  # noqa: BLE001 - 알림 처리 실패가 수신을 멈추면 안 됨
            log.warning("실시간 시세 처리 실패: %s", e)
        return len(buf)


_RUNNER: KISRealtime | None = None


def ensure_running(app, market_open: bool) -> dict:
    """스케줄러가 1분마다 부른다: 장중 + KIS 설정 + websockets 설치 시에만 연결."""
    global _RUNNER
    if app.settings.broker != "kis":
        return {"state": "off", "reason": "KIS 미설정"}
    try:
        import websockets  # noqa: F401
    except ImportError:
        return {"state": "off", "reason": "websockets 미설치 (./run.sh 가 설치)"}
    if _RUNNER is None:
        from ..alerts import watch_list
        _RUNNER = KISRealtime(app, lambda: watch_list(app, {"KR"})[1])
    st = _RUNNER.ensure(market_open)
    from .. import ops
    ops.set_state(app.engine, "kis_ws", {**st, "at": datetime.now(UTC).isoformat()})
    return st


__all__ = ["parse_trade", "subscribe_msg", "approval_key", "KISRealtime", "ensure_running"]

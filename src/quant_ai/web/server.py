"""대시보드 웹 서버 (표준 라이브러리, 개인용).

기본은 127.0.0.1 에만 바인딩한다. 외부에서 접속하려면 QUANT_WEB_TOKEN 을 설정하고
리버스 프록시(HTTPS) 뒤에 두는 것을 권장한다.
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .api import DashboardAPI

STATIC = Path(__file__).parent / "static"
mimetypes.add_type("application/manifest+json", ".webmanifest")
log = logging.getLogger("quant_ai.web")


CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
       "font-src https://fonts.gstatic.com https://cdn.jsdelivr.net; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def make_handler(api: DashboardAPI, token: str | None, allowed_hosts: set[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "QuantAI"
        sys_version = ""

        def log_message(self, fmt, *args):  # noqa: D401 - 조용히
            log.debug(fmt, *args)

        def _authorized(self, qs) -> bool:
            if not token:
                return True
            got = self.headers.get("X-Token") or (qs.get("token") or [""])[0]
            return hmac.compare_digest(got, token)

        def _host_ok(self) -> bool:
            """DNS rebinding 방어: 허용된 Host 로 들어온 요청만 처리."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in allowed_hosts

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            if not self._host_ok():
                return self._send(421, b"misdirected request", "text/plain")
            if url.path == "/api/health":  # 인증 없이 최소 정보 (로드밸런서/모니터링용)
                h = api.health()
                return self._json(h, 200 if h["ok"] else 503)
            if url.path.startswith("/api/"):
                if not self._authorized(qs):
                    return self._json({"error": "unauthorized"}, 401)
                try:
                    if url.path == "/api/dashboard":
                        return self._json(api.dashboard())
                    if url.path == "/api/chart":
                        return self._json(api.chart(qs["symbol"][0], int((qs.get("n") or ["260"])[0])))
                    if url.path == "/api/analysis":
                        return self._json(api.analysis(qs["symbol"][0]))
                    if url.path == "/api/reviews":
                        return self._json(api.reviews())
                    if url.path == "/api/ops":
                        return self._json(api.ops())
                    if url.path == "/api/core-satellite":
                        return self._json(api.core_satellite())
                    if url.path == "/api/research":
                        return self._json(api.research())
                    arg = lambda k, d=None: (qs.get(k) or [d])[0]  # noqa: E731
                    if url.path == "/api/evidence":
                        return self._json(api.evidence(int(arg("id", "0"))))
                    if url.path == "/api/journal":
                        return self._json(api.journal(arg("symbol"), int(arg("limit", "60")), arg("action")))
                    if url.path == "/api/calibration":
                        return self._json(api.calibration())
                    if url.path == "/api/ai-scoreboard":
                        return self._json(api.ai_scoreboard())
                    if url.path == "/api/risk":
                        return self._json(api.risk(arg("mode")))
                    if url.path == "/api/orders":
                        return self._json(api.orders(arg("mode"), int(arg("limit", "200"))))
                    if url.path == "/api/strategy-health":
                        return self._json(api.strategy_health(arg("mode")))
                    if url.path == "/api/net-alpha":
                        return self._json(api.net_alpha(arg("mode"), arg("market", "KR")))
                    if url.path == "/api/guardian":
                        return self._json(api.guardian())
                    if url.path.startswith("/api/an/"):
                        return self._json(api.analytics(url.path.rsplit("/", 1)[-1], arg("mode")))
                    if url.path == "/api/search":
                        return self._json(api.search(arg("q", "")[:60]))
                    if url.path == "/api/verify":
                        return self._json(api.verify())
                    if url.path == "/api/ledger":
                        return self._json(api.ledger(int(arg("before", "0") or 0) or None, arg("symbol", "")[:12], arg("result", "")[:8]))
                    if url.path == "/api/graph":
                        return self._json(api.graph(arg("symbol")))
                    if url.path == "/api/agents":
                        return self._json(api.agents())
                    if url.path == "/api/rules":
                        return self._json(api.rules())
                    if url.path == "/api/push/key":
                        return self._json(api.push_info())
                    if url.path == "/api/reports":
                        return self._json(api.reports())
                    if url.path == "/api/report":
                        return self._json(api.report(arg("file", "")))
                    if url.path == "/api/alerts":
                        return self._json(api.alerts(int(arg("after", "0") or 0)))
                    if url.path == "/api/quotes":
                        return self._json(api.quotes())
                    if url.path == "/api/ladder":
                        return self._json(api.ladder())
                    if url.path == "/api/control":
                        return self._json(api.control())
                    if url.path == "/api/scorecard":
                        return self._json(api.scorecard(arg("market"), int(arg("n", "100") or 100)))
                    if url.path == "/api/profile":
                        return self._json(api.profile(arg("symbol", "")[:12], refresh=arg("refresh", "") == "1"))
                    if url.path == "/api/ensure":
                        return self._json(api.ensure_symbol(arg("symbol", "")[:20]))
                    if url.path == "/api/setup":
                        return self._json(api.setup())
                    if url.path == "/api/keys":
                        return self._json(api.keys())
                    if url.path == "/api/server":
                        return self._json(api.server())
                    if url.path == "/api/db":
                        return self._json(api.db_preview())
                    if url.path == "/api/action":
                        return self._json(api.action(arg("name", "")))
                    if url.path == "/api/chat":
                        return self._json(api.chat_history(arg("sid", "")))
                    if url.path == "/api/freshness":
                        return self._json(api.freshness())
                    if url.path == "/api/readiness":
                        return self._json(api.readiness(arg("mode")))
                    if url.path == "/api/calendar":
                        return self._json(api.calendar())
                    if url.path == "/api/power":
                        return self._json(api.power())
                    if url.path == "/api/execution":
                        return self._json(api.execution())
                    if url.path == "/api/desk":
                        return self._json(api.desk(arg("symbol", "")[:12]))
                    if url.path == "/api/rotation":
                        return self._json(api.rotation())
                    if url.path == "/api/pipeline":
                        return self._json(api.pipeline_status())
                    if url.path == "/api/myjournal":
                        return self._json(api.my_journal())
                    if url.path == "/api/accounts":
                        return self._json(api.accounts())
                    if url.path == "/api/clock":
                        return self._json(api.clock())
                    if url.path == "/api/truth":
                        return self._json(api.truth(arg("refresh", "") == "1"))
                    if url.path == "/api/explain":
                        return self._json(api.explain(arg("symbol", "")[:12]))
                    if url.path == "/api/checklist":
                        return self._json(api.checklist())
                    if url.path == "/api/today":
                        return self._json(api.today())
                    if url.path == "/api/holdings":
                        return self._json(api.holdings(arg("symbol", "")[:12]))
                    if url.path == "/api/compare":
                        return self._json(api.compare(arg("symbols", "")[:60]))
                    if url.path == "/api/star":
                        return self._json(api.starred())
                    if url.path == "/api/stock":
                        return self._json(api.stock(arg("symbol", "")[:12], arg("mode", "paper")[:8]))
                    if url.path == "/api/pretrade":
                        return self._json(api.pretrade(arg("symbol", "")[:12], arg("weight", "")[:6], arg("mode", "paper")[:8]))
                    if url.path == "/api/health/ai":
                        return self._json(api.ai_health())
                    if url.path == "/api/prefs":
                        return self._json(api.prefs())
                    # ---- v16
                    sym = arg("symbol", "")[:12]
                    if url.path.startswith("/api/stock/"):
                        return self._json(api.stock_part(url.path.rsplit("/", 1)[-1], sym))
                    if url.path == "/api/news-impact":
                        return self._json(api.news_impact(int(arg("id", "0") or 0)))
                    if url.path == "/api/notrade":
                        return self._json(api.notrade(arg("mode"), int(arg("days", "30") or 30), sym))
                    if url.path == "/api/ai-card":
                        return self._json(api.ai_card(sym))
                    if url.path == "/api/ai-verify":
                        return self._json(api.ai_verify(sym))
                    if url.path == "/api/ai-alpha":
                        return self._json(api.ai_alpha())
                    if url.path == "/api/ai-track":
                        return self._json(api.ai_track(arg("refresh", "") == "1"))
                    if url.path == "/api/ai-public":
                        return self._json(api.ai_public(int(arg("n", "1000") or 1000)))
                    if url.path == "/api/action-center":
                        return self._json(api.action_center(arg("mode")))
                    if url.path == "/api/watchlist":
                        return self._json(api.watchlist())
                    if url.path == "/api/risk-simple":
                        return self._json(api.risk_simple(arg("mode")))
                    if url.path == "/api/thesis":
                        return self._json(api.thesis(sym or None))
                    if url.path == "/api/sentinel":
                        return self._json(api.sentinel())
                    if url.path == "/api/data-health":
                        return self._json(api.data_health(arg("refresh", "") == "1"))
                    if url.path == "/api/failure-lab":
                        return self._json(api.failure_lab(arg("refresh", "") == "1"))
                    if url.path == "/api/ai-lab":
                        return self._json(api.ai_lab())
                    if url.path == "/api/portfolio-os":
                        return self._json(api.portfolio_os(arg("mode")))
                    if url.path == "/api/briefing":
                        return self._json(api.briefing(arg("mode")))
                    if url.path == "/api/simulate":
                        return self._json(api.simulate(arg("mode")))
                    if url.path == "/api/user-profile":
                        return self._json(api.user_profile())
                    if url.path == "/api/discover":
                        return self._json(api.discover())
                    if url.path == "/api/mistakes":
                        return self._json(api.mistakes())
                    if url.path == "/api/exec-costs":
                        return self._json(api.exec_costs())
                    if url.path == "/api/orderbook":
                        return self._json(api.orderbook(sym, arg("qty", "")[:9]))
                    if url.path == "/api/validation":
                        return self._json(api.validation())
                    if url.path == "/api/governance":
                        return self._json(api.governance())
                    if url.path == "/api/ticket":
                        return self._json(api.ticket(sym, arg("side", "buy")[:4], arg("qty", "")[:9], arg("amount", "")[:14]))
                    if url.path == "/api/ticket/book":
                        return self._json(api.ticket_book())
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                except Exception as exc:  # noqa: BLE001
                    log.exception("API 오류")
                    return self._json({"error": str(exc)}, 500)
                return self._json({"error": "not found"}, 404)
            rel = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            path = (STATIC / rel).resolve()
            if STATIC.resolve() not in path.parents or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, path.read_bytes(), ctype)

        def do_POST(self):  # noqa: N802
            url = urlparse(self.path)
            if not self._host_ok():
                return self._send(421, b"misdirected request", "text/plain")
            if not self._authorized(parse_qs(url.query)):
                return self._json({"error": "unauthorized"}, 401)
            # CSRF 방어: JSON 만 받는다 (다른 사이트의 form POST 는 preflight 없이 JSON 을 보낼 수 없음)
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json({"error": "application/json 필요"}, 415)
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in allowed_hosts:
                return self._json({"error": "cross-origin 거부"}, 403)
            length = min(int(self.headers.get("Content-Length") or 0), 200_000)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "잘못된 JSON"}, 400)
            if url.path == "/api/killswitch":
                api.app.set_kill_switch(bool(body.get("on")), str(body.get("reason", ""))[:200], by="dashboard")
                api._audit("killswitch", f"{'ON' if body.get('on') else 'OFF'} {str(body.get('reason', ''))[:100]}")
                api._cache = None  # 대시보드 캐시 무효화
                return self._json({"kill_switch": api.app.kill_switch_on()})
            if url.path == "/api/order-sheet":
                try:
                    return self._json(api.order_sheet(body))
                except (ValueError, RuntimeError) as e:
                    return self._json({"error": str(e)}, 400)
            if url.path == "/api/strategy-health":
                return self._json(api.strategy_health(refresh=True))
            try:
                if url.path == "/api/us-cycle":  # 미국 장부: 일봉 받기 + 한 사이클 (가상매매)
                    from ..global_market import run_cycle, sync
                    return self._json({"sync": sync(api.app), "cycle": run_cycle(api.app)})
                if url.path == "/api/action":
                    return self._json(api.action(str(body.get("name", "")), start=True,
                                                 params={k: v for k, v in body.items() if k in ("symbol",)}))
                if url.path == "/api/chat":
                    return self._json(api.chat(body))
                if url.path == "/api/rules":
                    return self._json(api.rule_write(body))
                if url.path.startswith("/api/push/"):
                    return self._json(api.push_write(url.path, body))
                if url.path == "/api/notify/test":
                    return self._json(api.notify_test())
                if url.path == "/api/myjournal":
                    return self._json(api.my_journal_write(body))
                if url.path == "/api/accounts":
                    return self._json(api.accounts_write(body))
                if url.path == "/api/star":
                    return self._json(api.star(body))
                if url.path == "/api/prefs":
                    return self._json(api.prefs_write(body))
                if url.path == "/api/keys/reload":
                    return self._json(api.keys_reload())
                if url.path == "/api/keys/probe":
                    return self._json(api.keys_probe(body))
                if url.path == "/api/stock/digest":
                    return self._json(api.stock_digest(body))
                if url.path == "/api/watch-group":
                    return self._json(api.watch_group(body))
                if url.path == "/api/thesis":
                    return self._json(api.thesis_write(body))
                if url.path == "/api/user-profile":
                    return self._json(api.user_profile_write(body))
                if url.path == "/api/ticket":
                    return self._json(api.ticket_place(body))
                if url.path == "/api/chat/clear":
                    from ..assistant import clear
                    clear(api.engine, str(body.get("sid", ""))[:40])
                    return self._json({"ok": True})
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            except Exception as exc:  # noqa: BLE001
                log.exception("API 오류")
                return self._json({"error": str(exc)}, 500)
            self._json({"error": "not found"}, 404)

    return Handler


def serve(app, host: str = "127.0.0.1", port: int = 8050) -> None:
    token = os.environ.get("QUANT_WEB_TOKEN") or None
    if host not in ("127.0.0.1", "localhost") and not token:
        raise SystemExit("외부 바인딩 시 QUANT_WEB_TOKEN 설정이 필요합니다")
    allowed = {"127.0.0.1", "localhost", "::1", host.lower()}
    allowed |= {h.strip().lower() for h in os.environ.get("QUANT_WEB_ALLOWED_HOSTS", "").split(",") if h.strip()}
    httpd = ThreadingHTTPServer((host, port), make_handler(DashboardAPI(app), token, allowed))
    print(f"Quant AI 대시보드: http://{host}:{port}" + ("  (토큰: ?token=...)" if token else ""))
    httpd.serve_forever()

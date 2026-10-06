"""대시보드 웹 서버 (표준 라이브러리, 개인용).

기본은 127.0.0.1 에만 바인딩한다. 외부에서 접속하려면 QUANT_WEB_TOKEN 을 설정하고
리버스 프록시(HTTPS) 뒤에 두는 것을 권장한다.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..auth import Auth
from .api import DashboardAPI

STATIC = Path(__file__).parent / "static"
mimetypes.add_type("application/manifest+json", ".webmanifest")
log = logging.getLogger("quant_ai.web")


CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
       "font-src https://fonts.gstatic.com https://cdn.jsdelivr.net; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def make_handler(api: DashboardAPI, token: str | None, allowed_hosts: set[str], auth: Auth | None = None):
    auth = auth or Auth()
    auth.admin_token = token  # 인자로 받은 관리자 토큰이 우선 (없으면 토큰 인증 없음)

    class Handler(BaseHTTPRequestHandler):
        server_version = "QuantAI"
        sys_version = ""

        def log_message(self, fmt, *args):  # noqa: D401 - 조용히
            log.debug(fmt, *args)

        def _sid(self) -> str | None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "qa_session":
                    return v
            return None

        def _role(self, qs) -> str | None:
            """admin / viewer / None. 인증을 하나도 설정하지 않았으면 (이 PC 전용) admin."""
            if not auth.enabled:
                return "admin"
            got = self.headers.get("X-Token") or (qs.get("token") or [""])[0]
            return auth.role_for_token(got) or auth.session_role(self._sid())

        def _authorized(self, qs) -> bool:
            return self._role(qs) is not None

        def _ip(self) -> str:
            remote = self.client_address[0] if self.client_address else "?"
            if auth is None:
                return remote
            from ..auth import client_ip
            return client_ip(remote, self.headers.get("X-Forwarded-For"), auth.trusted_proxies)

        def _host_ok(self) -> bool:
            """DNS rebinding 방어: 허용된 Host 로 들어온 요청만 처리."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in allowed_hosts

        def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store", csp: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", csp or CSP)
            self.end_headers()
            self.wfile.write(body)
            t0 = getattr(self, "_t0", None)
            if t0 is not None:
                import time as _time

                from ..metrics import observe
                observe(urlparse(self.path).path, (_time.monotonic() - t0) * 1000)
                self._t0 = None

        def _json_cookie(self, obj, cookie: str) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Set-Cookie", cookie)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            import time as _time
            self._t0 = _time.monotonic()  # v30: 응답 시간 지표 (/metrics)
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            if not self._host_ok():
                return self._send(421, b"misdirected request", "text/plain")
            if url.path == "/metrics":  # v30 관측성: 같은 PC 는 로그인 없이 · 밖에서는 로그인/토큰
                local = (self.client_address[0] if self.client_address else "") in ("127.0.0.1", "::1")
                if not (local or self._authorized(qs)):
                    return self._send(401, b"unauthorized", "text/plain")
                from ..metrics import render as _metrics
                return self._send(200, _metrics(api.app).encode(), "text/plain; version=0.0.4; charset=utf-8")
            if url.path == "/api/auth":  # 로그인 화면이 무엇을 물어볼지 (비밀 정보 없음)
                return self._json(auth.info() | {"role": self._role(qs)})
            if url.path == "/api/health":  # 인증 없이 최소 정보 (로드밸런서/모니터링용)
                h = api.health()
                if (self.client_address[0] if self.client_address else "") in ("127.0.0.1", "::1"):
                    h |= api.instance()  # 같은 PC 에서만: 버전·폴더·PID (run.sh 가 옛 서버를 알아보게)
                return self._json(h, 200 if h["ok"] else 503)
            if url.path.startswith("/api/logo/"):  # 종목 로고 (공개 정보 · <img> 로 불러서 토큰 없이)
                from ..logos import get as logo_get
                try:
                    data, ctype, src = logo_get(api.app, url.path.rsplit("/", 1)[-1], (qs.get("n") or [None])[0], block=False)
                except ValueError:
                    return self._json({"error": "bad symbol"}, 400)
                # 진짜 로고는 하루 · 이니셜은 금방 다시 물어본다 (뒤에서 받는 중이면 1분, 없다고 확인됐으면 1시간)
                cc = {"monogram": "public, max-age=3600", "default": "public, max-age=3600", "pending": "no-cache, max-age=60", "custom": "no-cache"}.get(src, "public, max-age=86400")
                # v24: 로고는 그림일 뿐 — 바로 열어도 스크립트가 절대 돌지 않게 (외부 SVG 를 정화한 뒤에도 한 번 더)
                return self._send(200, data, ctype, cc, csp="default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox")
            if url.path in ("/proof", "/proof.json"):  # v29 증명 프로젝트 공개 기록 (공개를 켰을 때만 로그인 없이)
                from .. import proof as _proof
                if not (_proof.is_public(api.app) or self._authorized(qs)):
                    return self._send(404, "공개되지 않은 페이지예요".encode(), "text/plain; charset=utf-8")
                view = _proof.public_view(api.app)
                if url.path.endswith(".json"):
                    return self._json(view)
                return self._send(200, _proof.public_html(view).encode(), "text/html; charset=utf-8", "no-cache",
                                  csp="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'")
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
                    if url.path == "/api/company":  # v23: 기업 신분증 (로고·이름·티커·거래소·업종·ISIN)
                        return self._json(api.company(arg("symbol", "")[:16]))
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
                    if url.path == "/api/netcheck":
                        return self._json(api.netcheck())
                    if url.path == "/api/news-board":
                        return self._json(api.news_board(int(arg("days", "3") or 3), arg("only", "")[:4], arg("symbol", "")[:12]))
                    if url.path == "/api/market-map":
                        return self._json(api.market_map())
                    if url.path == "/api/pead":
                        return self._json(api.pead())
                    if url.path == "/api/replay":
                        return self._json(api.replay(arg("date", "")[:10]))
                    if url.path == "/api/weekly":
                        return self._json(api.weekly())
                    if url.path.startswith("/api/news/") and url.path.rsplit("/", 1)[-1].isdigit():
                        return self._json(api.news_detail(url.path.rsplit("/", 1)[-1]))
                    if url.path.startswith("/api/disclosure/") and url.path.rsplit("/", 1)[-1].isdigit():
                        return self._json(api.disclosure_detail(url.path.rsplit("/", 1)[-1]))
                    if url.path == "/api/news-search":
                        return self._json(api.news_search(arg("q", ""), arg("days", "30")))
                    if url.path == "/api/conflicts":
                        return self._json(api.conflicts())
                    if url.path == "/api/home5":
                        return self._json(api.home5(arg("mode", "") or None))
                    if url.path == "/api/start-guide":
                        return self._json(api.start_guide())
                    if url.path == "/api/goal":
                        return self._json(api.goal({k: arg(k) for k in ("principal", "monthly", "goal", "target_years", "strategy", "raise_pct")}))
                    if url.path == "/api/ops-status":
                        return self._json(api.ops_status())
                    if url.path == "/api/baseline":
                        return self._json(api.baseline(arg("mode", "paper")))
                    if url.path == "/api/t/home":  # v25 토스식 화면
                        return self._json(api.t_home(arg("mode", "paper")[:8]))
                    if url.path == "/api/t/stock":
                        return self._json(api.t_stock(arg("symbol", "")[:12]))
                    if url.path == "/api/t/feed":
                        return self._json(api.t_feed(arg("tab", "all")[:8], arg("region", "all")[:4], arg("topic", "all")[:6]))
                    if url.path == "/api/t/portfolio":
                        return self._json(api.t_portfolio(arg("mode", "paper")[:10]))
                    if url.path == "/api/t/alerts":
                        return self._json(api.t_alerts())
                    if url.path == "/api/t/community":
                        return self._json(api.t_community(arg("symbol", "")[:12]))
                    if url.path == "/api/t/collect":
                        return self._json(api.t_collect())
                    if url.path == "/api/signals2":  # v28: 신호 엔진 2.0 · 매수/매도 후보
                        return self._json(api.signals2(arg("market", "KR")[:2]))
                    if url.path == "/api/datacheck":  # v29: 데이터 정합성 점검
                        return self._json(api.datacheck())
                    if url.path == "/api/proof-project":  # v29: 증명 프로젝트
                        return self._json(api.proof_status())
                    if url.path == "/api/autopilot":  # v30: AI 자동매매
                        return self._json(api.autopilot())
                    if url.path == "/api/logo-queue":  # v30: 로고 없는 종목
                        return self._json(api.logo_queue())
                    if url.path == "/api/company-view":  # v30: 회사 이해
                        return self._json(api.company_view(arg("symbol", "")[:12], arg("refresh", "") == "1"))
                    if url.path == "/api/signals2/stock":
                        return self._json(api.signals2_stock(arg("symbol", "")[:12]))
                    if url.path == "/api/t/intraday":  # v27: 하루 안 움직임 (5분봉)
                        return self._json(api.t_intraday(arg("symbol", "")[:12]))
                    if url.path == "/api/t/market":
                        return self._json(api.t_market())
                    if url.path == "/api/t/quotes":
                        return self._json(api.t_quotes(arg("symbols", "")[:200]))
                    if url.path == "/api/t/report":
                        return self._json(api.t_report(arg("symbol", "")[:12]))
                    if url.path == "/api/ai-trust":  # v24: AI 신뢰 센터 (한 화면)
                        return self._json(api.ai_trust())
                    if url.path == "/api/ai-context":  # v23: 상황별 AI 성적 (뉴스 유형 · 실적 전후 · 종목)
                        return self._json(api.ai_context())
                    if url.path == "/api/ai-plain":
                        return self._json(api.ai_plain(arg("symbol", "")))
                    if url.path == "/api/verdict":
                        return self._json(api.verdict(qs["symbol"][0]))
                    if url.path == "/api/oneline":
                        return self._json(api.oneline(arg("mode", "") or None))
                    if url.path == "/api/budget":
                        return self._json(api.budget(arg("principal", "")[:15], arg("max_loss", "")[:15], arg("on_stop", "")[:10]))
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
                        return self._json(api.portfolio_os(arg("mode"), arg("source", "auto")))
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
                        return self._json(api.ticket_book(arg("mode", "")[:12]))
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
            if url.path == "/api/login":
                sid, msg = auth.login(self._ip(), str(body.get("password", ""))[:200], str(body.get("otp", ""))[:12])
                api._audit("login" if sid else "login_fail", f"{self._ip()} · {msg}")
                if not sid:
                    return self._json({"error": msg}, 401)
                secure = "; Secure" if auth.is_secure(self.client_address[0] if self.client_address else "", self.headers.get("X-Forwarded-Proto")) else ""
                return self._json_cookie({"ok": True, "role": "admin"},
                                         f"qa_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={12 * 3600}{secure}")
            if url.path == "/api/logout":
                auth.logout(self._sid())
                return self._json_cookie({"ok": True}, "qa_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
            role = self._role(parse_qs(url.query))
            if role is None:
                return self._json({"error": "unauthorized"}, 401)
            if role != "admin":  # 읽기 전용 권한(RBAC): 긴급 정지·설정·주문·키 등 쓰기 불가
                return self._json({"error": "읽기 전용 권한입니다 (관리자로 로그인 필요)"}, 403)
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
                if url.path == "/api/t/alerts":
                    return self._json(api.t_alerts_write(body))
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
                if url.path == "/api/netcheck":
                    return self._json(api.netcheck(run=True))
                if url.path == "/api/datacheck":
                    return self._json(api.datacheck(run=True))
                if url.path == "/api/proof-project":
                    return self._json(api.proof_write(body))
                if url.path == "/api/autopilot":
                    return self._json(api.autopilot_write(body))
                if url.path == "/api/news-extract":
                    return self._json(api.news_extract())
                if url.path == "/api/news-explain":
                    return self._json(api.news_explain(body))
                if url.path == "/api/disclosure-explain":
                    return self._json(api.disclosure_explain(body))
                if url.path == "/api/us-sheet":
                    return self._json(api.us_sheet(body))
                if url.path == "/api/budget":
                    return self._json(api.budget_write(body))
                if url.path == "/api/goal":
                    return self._json(api.goal_save(body))
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
                if url.path == "/api/logo-upload":  # v27: 종목 로고 직접 넣기 (국내 종목 로고 보강)
                    return self._json(api.logo_upload(body))
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
    auth = Auth()
    if auth.config_errors():
        raise SystemExit("로그인 설정 오류: " + " · ".join(auth.config_errors()))
    if host not in ("127.0.0.1", "localhost") and not (token or auth.pw_hash):
        raise SystemExit("외부 바인딩 시 로그인(./run.sh auth-setup) 또는 QUANT_WEB_TOKEN 설정이 필요합니다")
    if host not in ("127.0.0.1", "localhost") and not (auth.secure_cookie or auth.trusted_proxies):
        log.warning("외부 접속을 여는데 HTTPS 설정이 없습니다 — 비밀번호·세션이 평문으로 오갑니다. "
                    "HTTPS 리버스 프록시(Caddy·nginx) 뒤에 두고 QUANT_TRUSTED_PROXIES · QUANT_WEB_SECURE_COOKIE=1 을 설정하세요")
    allowed = {"127.0.0.1", "localhost", "::1", host.lower()}
    allowed |= {h.strip().lower() for h in os.environ.get("QUANT_WEB_ALLOWED_HOSTS", "").split(",") if h.strip()}
    api = DashboardAPI(app)
    httpd = ThreadingHTTPServer((host, port), make_handler(api, token, allowed, auth))

    def _warm():  # 첫 화면이 기다리지 않게: 전 종목 일봉·대시보드를 미리 계산해 둔다 (실패해도 무시)
        # v26: 처음 여는 화면들(홈·AI 신뢰·목표·사실 확인·감시실)도 미리 — 첫 방문 2~5초 대기 없애기
        # v30: 켜 둔 동안 4분마다 다시 데워 둔다 (캐시가 식어 '가끔 느린' 첫 화면을 없앰) · AI 자동매매·로고 큐 포함
        import time as _time
        while True:
            for f in (app.market_data, api.dashboard, api.setup, api.today, api.t_home, lambda: api.home5("paper"), api.ai_trust,
                      lambda: api.goal({}), api.truth, api.control, api.signals2, api.autopilot, api.logo_queue):
                try:
                    f()
                except Exception as e:  # noqa: BLE001
                    log.info("예열 실패 %s: %s", getattr(f, "__name__", f), e)
            if os.environ.get("QUANT_WARM_LOOP", "1").lower() in ("0", "false", "off"):
                return
            _time.sleep(240)
    import threading
    threading.Thread(target=_warm, name="warm", daemon=True).start()
    print(f"Quant AI 대시보드: http://{host}:{port}" + ("  (토큰: ?token=...)" if token else ""))
    httpd.serve_forever()

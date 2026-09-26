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
log = logging.getLogger("quant_ai.web")


def make_handler(api: DashboardAPI, token: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "QuantAI/0.1"

        def log_message(self, fmt, *args):  # noqa: D401 - 조용히
            log.debug(fmt, *args)

        def _authorized(self, qs) -> bool:
            if not token:
                return True
            got = self.headers.get("X-Token") or (qs.get("token") or [""])[0]
            return hmac.compare_digest(got, token)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            qs = parse_qs(url.query)
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
            if not self._authorized(parse_qs(url.query)):
                return self._json({"error": "unauthorized"}, 401)
            if url.path == "/api/killswitch":
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                api.app.set_kill_switch(bool(body.get("on")))
                return self._json({"kill_switch": api.app.kill_switch_on()})
            self._json({"error": "not found"}, 404)

    return Handler


def serve(app, host: str = "127.0.0.1", port: int = 8050) -> None:
    token = os.environ.get("QUANT_WEB_TOKEN") or None
    if host not in ("127.0.0.1", "localhost") and not token:
        raise SystemExit("외부 바인딩 시 QUANT_WEB_TOKEN 설정이 필요합니다")
    httpd = ThreadingHTTPServer((host, port), make_handler(DashboardAPI(app), token))
    print(f"Quant AI 대시보드: http://{host}:{port}" + ("  (토큰: ?token=...)" if token else ""))
    httpd.serve_forever()

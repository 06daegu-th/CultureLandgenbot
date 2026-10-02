"""로컬 가짜 KIS 서버 (모의투자 API 형식 흉내) — 실제 네트워크 없이 LIVE 경로 전체를 검증한다.

체결 규칙: 매수 지정가 ≥ 매도1호가 → 매도1호가에 체결, 매도 지정가 ≤ 매수1호가 → 매수1호가에 체결.
fill_ratio < 1 이면 부분체결 후 잔량은 취소 요청 시 취소된다.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class KISMock:
    def __init__(self, prices: dict[str, float], cash: float = 10_000_000, fill_ratio: float = 1.0,
                 spread: float = 0.001):
        self.prices, self.cash, self.fill_ratio, self.spread = dict(prices), cash, fill_ratio, spread
        self.positions: dict[str, list[float]] = {}  # code → [qty, avg]
        self.orders: dict[str, dict] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()

    def bid_ask(self, code):
        p = self.prices[code]
        return p * (1 - self.spread), p * (1 + self.spread)

    def _handler(mock):  # noqa: N805
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                b = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                path = urlparse(self.path).path
                with mock.lock:
                    mock.calls.append(("POST", path, self.headers.get("tr_id")))
                    if path == "/oauth2/tokenP":
                        return self._send({"access_token": "MOCK", "expires_in": 86400})
                    if self.headers.get("authorization") != "Bearer MOCK":
                        return self._send({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "토큰 오류"})
                    if path.endswith("/order-cash"):
                        return self._send(mock._order(body, self.headers.get("tr_id")))
                    if path.endswith("/order-rvsecncl"):
                        o = mock.orders.get(body["ORGN_ODNO"])
                        if o:
                            o["cancelled"] = True
                        return self._send({"rt_cd": "0", "output": {}})
                return self._send({"rt_cd": "1", "msg_cd": "404", "msg1": path})

            def do_GET(self):  # noqa: N802
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                with mock.lock:
                    mock.calls.append(("GET", u.path, self.headers.get("tr_id")))
                    code = q.get("FID_INPUT_ISCD")
                    if code is not None and code not in mock.prices:
                        return self._send({"rt_cd": "1", "msg_cd": "APBK0001", "msg1": "종목코드 오류"})
                    if u.path.endswith("/inquire-price"):
                        return self._send({"rt_cd": "0", "output": {"stck_prpr": str(int(mock.prices[q["FID_INPUT_ISCD"]]))}})
                    if u.path.endswith("/inquire-asking-price-exp-ccn"):
                        bid, ask = mock.bid_ask(q["FID_INPUT_ISCD"])
                        return self._send({"rt_cd": "0", "output1": {"askp1": str(int(ask) + 1), "bidp1": str(int(bid)),
                                                                     "askp_rsqn1": "100000", "bidp_rsqn1": "100000"}})
                    if u.path.endswith("/inquire-daily-ccld"):
                        o = mock.orders[q["ODNO"]]
                        return self._send({"rt_cd": "0", "output1": [{
                            "odno": q["ODNO"], "tot_ccld_qty": str(o["filled"]), "avg_prvs": str(o["price"]),
                            "rmn_qty": "0" if o.get("cancelled") else str(o["qty"] - o["filled"]),
                            "cncl_yn": "Y" if o.get("cancelled") and o["filled"] < o["qty"] else "N"}]})
                    if u.path.endswith("/inquire-balance"):
                        out1 = [{"pdno": c, "hldg_qty": str(int(v[0])), "pchs_avg_pric": f"{v[1]:.2f}"}
                                for c, v in mock.positions.items() if v[0] > 0]
                        return self._send({"rt_cd": "0", "output1": out1,
                                           "output2": [{"dnca_tot_amt": str(int(mock.cash)),
                                                        "prvs_rcdl_excc_amt": str(int(mock.cash))}],
                                           "ctx_area_fk100": "", "ctx_area_nk100": ""})
                return self._send({"rt_cd": "1", "msg_cd": "404", "msg1": u.path})
        return H

    def _order(self, body: dict, tr_id: str) -> dict:
        code, qty, px = body["PDNO"], int(body["ORD_QTY"]), float(body["ORD_UNPR"])
        if not tr_id.startswith("V"):
            return {"rt_cd": "1", "msg_cd": "REAL", "msg1": "모의 서버에 실전 TR 사용"}
        buy = tr_id == "VTTC0012U"
        bid, ask = self.bid_ask(code)
        crosses = px >= int(ask) + 1 if buy else px <= int(bid)
        fill_px = (int(ask) + 1) if buy else int(bid)
        filled = int(qty * self.fill_ratio) if crosses else 0
        if buy and filled * fill_px > self.cash:
            return {"rt_cd": "1", "msg_cd": "APBK0952", "msg1": "주문가능금액 초과"}
        pos = self.positions.setdefault(code, [0, 0.0])
        if not buy and filled > pos[0]:
            return {"rt_cd": "1", "msg_cd": "APBK1680", "msg1": "매도가능수량 초과"}
        if filled:
            if buy:
                pos[1] = (pos[0] * pos[1] + filled * fill_px) / (pos[0] + filled)
                pos[0] += filled
                self.cash -= filled * fill_px
            else:
                pos[0] -= filled
                self.cash += filled * fill_px
        odno = f"{len(self.orders) + 1:010d}"
        self.orders[odno] = {"code": code, "qty": qty, "filled": filled, "price": fill_px, "buy": buy}
        return {"rt_cd": "0", "output": {"ODNO": odno, "KRX_FWDG_ORD_ORGNO": "00950", "ORD_TMD": "100000"}}

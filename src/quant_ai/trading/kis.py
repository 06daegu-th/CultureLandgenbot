"""한국투자증권(KIS) Open API 연동: 국내주식 시세 · 주문 · 체결확인 · 취소 · 잔고.

엔드포인트/TR_ID 는 공식 예제 저장소(koreainvestment/open-trading-api) 기준.
반드시 **모의투자(KIS_ENV=demo)** 로 충분히 검증한 뒤 실전(real)으로 전환할 것.

안전장치
- 시장가 대신 **보호 지정가**(매수: 매도1호가×(1+허용슬리피지), 매도: 매수1호가×(1-…))를 호가단위에 맞춰 낸다.
- 주문 후 체결을 폴링으로 확인하고, 제한시간 내 미체결 잔량은 **취소**한다 (유령 주문 방지).
- 포지션/현금은 DB 스냅샷이 아니라 **증권사 잔고를 진실의 원천**으로 삼는다 (reconcile).
- 요청 간격 제한(실전 0.05초, 모의 0.5초)과 토큰 캐시(24시간, 파일 권한 0600).
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .broker import Broker, MarketQuote
from .portfolio import CostModel, Fill, Order, Portfolio, Position, Side

log = logging.getLogger("quant_ai.kis")
UTC = UTC

BASE_URL = {"real": "https://openapi.koreainvestment.com:9443",
            "demo": "https://openapivts.koreainvestment.com:29443"}
MIN_INTERVAL = {"real": 0.05, "demo": 0.5}
TR = {
    "buy": {"real": "TTTC0012U", "demo": "VTTC0012U"},
    "sell": {"real": "TTTC0011U", "demo": "VTTC0011U"},
    "cancel": {"real": "TTTC0013U", "demo": "VTTC0013U"},
    "balance": {"real": "TTTC8434R", "demo": "VTTC8434R"},
    "ccld": {"real": "TTTC0081R", "demo": "VTTC0081R"},
    "price": {"real": "FHKST01010100", "demo": "FHKST01010100"},
    "orderbook": {"real": "FHKST01010200", "demo": "FHKST01010200"},
}


class KISError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"KIS {code}: {message}")
        self.code = code


def krx_tick(price: float) -> int:
    """KRX 호가단위 (2023.1 유가·코스닥 통합 기준)."""
    for limit, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < limit:
            return tick
    return 1_000


def round_to_tick(price: float, side: Side) -> int:
    t = krx_tick(price)
    return int(math.ceil(price / t) * t) if side is Side.BUY else int(math.floor(price / t) * t)


def krx_code(symbol: str) -> str | None:
    """'005930', '005930.KS', '035720.KQ' → '005930'. 국내 6자리 코드가 아니면 None."""
    code = symbol.split(".")[0]
    return code if len(code) == 6 and code.isalnum() else None


Transport = Callable[[str, str, dict, dict | None, dict | None], dict]


def urllib_transport(method: str, url: str, headers: dict, params: dict | None, body: dict | None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310 - 고정 KIS 도메인
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - 고정된 https KIS 도메인
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:  # noqa: BLE001
            raise KISError(str(exc.code), exc.reason) from exc


class KISClient:
    def __init__(self, app_key: str, app_secret: str, account: str, env: str = "demo",
                 token_cache: Path | None = None, transport: Transport | None = None):
        if env not in BASE_URL:
            raise ValueError("KIS_ENV 는 demo 또는 real")
        cano, _, prdt = account.partition("-")
        if len(cano) != 8 or len(prdt) != 2:
            raise ValueError("KIS_ACCOUNT 형식: 12345678-01")
        self.app_key, self.app_secret, self.cano, self.prdt, self.env = app_key, app_secret, cano, prdt, env
        self.base = BASE_URL[env]
        self.token_cache = token_cache
        self.transport = transport or urllib_transport
        self._token: tuple[str, datetime] | None = None
        self._last = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, artifacts_dir: Path, env: dict | None = None, transport: Transport | None = None) -> KISClient:
        e = os.environ if env is None else env
        missing = [k for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT") if not e.get(k)]
        if missing:
            raise KISError("config", f"환경변수 누락: {', '.join(missing)}")
        kis_env = e.get("KIS_ENV", "demo")
        return cls(e["KIS_APP_KEY"], e["KIS_APP_SECRET"], e["KIS_ACCOUNT"], kis_env,
                   Path(artifacts_dir) / f"kis_token_{kis_env}.json", transport)

    # -------------------------------------------------------------- 공통
    def _throttle(self) -> None:
        with self._lock:
            wait = MIN_INTERVAL[self.env] - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def token(self) -> str:
        now = datetime.now(UTC)
        if self._token and self._token[1] - now > timedelta(minutes=10):
            return self._token[0]
        if self.token_cache and self.token_cache.exists():
            try:
                c = json.loads(self.token_cache.read_text())
                exp = datetime.fromisoformat(c["expires_at"])
                if c.get("app_key_tail") == self.app_key[-4:] and exp - now > timedelta(minutes=10):
                    self._token = (c["token"], exp)
                    return c["token"]
            except (ValueError, KeyError):
                pass
        self._throttle()
        r = self.transport("POST", f"{self.base}/oauth2/tokenP", {"Content-Type": "application/json"}, None,
                           {"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret})
        if "access_token" not in r:
            raise KISError(str(r.get("error_code", "token")), str(r.get("error_description", r))[:200])
        exp = now + timedelta(seconds=int(r.get("expires_in", 86400)))
        self._token = (r["access_token"], exp)
        if self.token_cache:
            self.token_cache.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.token_cache, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump({"token": r["access_token"], "expires_at": exp.isoformat(),
                           "app_key_tail": self.app_key[-4:]}, fh)
        return r["access_token"]

    def _call(self, method: str, path: str, tr: str, params: dict | None = None, body: dict | None = None,
              tr_cont: str = "") -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8", "authorization": f"Bearer {self.token()}",
                   "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": TR[tr][self.env],
                   "custtype": "P", "tr_cont": tr_cont}
        self._throttle()
        r = self.transport(method, f"{self.base}{path}", headers, params, body)
        if str(r.get("rt_cd")) != "0":
            raise KISError(str(r.get("msg_cd", "?")), str(r.get("msg1", r))[:300])
        return r

    # -------------------------------------------------------------- 시세
    def price(self, code: str) -> float:
        r = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", "price",
                       {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
        return float(r["output"]["stck_prpr"])

    def quote(self, code: str) -> MarketQuote:
        r = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn", "orderbook",
                       {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
        o = r.get("output1") or {}
        last = self.price(code)
        f = lambda k: float(o[k]) if o.get(k) not in (None, "", "0") else None  # noqa: E731
        return MarketQuote(last=last, bid=f("bidp1"), ask=f("askp1"), bid_qty=f("bidp_rsqn1"), ask_qty=f("askp_rsqn1"))

    # -------------------------------------------------------------- 주문
    def order(self, code: str, side: Side, qty: int, limit_price: int | None) -> dict:
        body = {"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "PDNO": code,
                "ORD_DVSN": "00" if limit_price else "01",  # 00 지정가 / 01 시장가
                "ORD_QTY": str(int(qty)), "ORD_UNPR": str(int(limit_price or 0)),
                "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if side is Side.SELL else "", "CNDT_PRIC": ""}
        r = self._call("POST", "/uapi/domestic-stock/v1/trading/order-cash",
                       "buy" if side is Side.BUY else "sell", body=body)
        out = r.get("output") or {}
        return {"odno": out.get("ODNO"), "orgno": out.get("KRX_FWDG_ORD_ORGNO"), "time": out.get("ORD_TMD")}

    def cancel(self, odno: str, orgno: str) -> None:
        self._call("POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl", "cancel", body={
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "KRX_FWDG_ORD_ORGNO": orgno, "ORGN_ODNO": odno,
            "ORD_DVSN": "00", "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": "0", "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX"})

    def order_status(self, odno: str, day: datetime | None = None) -> dict:
        d = (day or datetime.now(UTC).astimezone()).strftime("%Y%m%d")
        r = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-daily-ccld", "ccld", {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "INQR_STRT_DT": d, "INQR_END_DT": d,
            "SLL_BUY_DVSN_CD": "00", "PDNO": "", "CCLD_DVSN": "00", "INQR_DVSN": "00", "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": "", "ODNO": odno, "INQR_DVSN_1": "", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            "EXCG_ID_DVSN_CD": "KRX"})
        for row in r.get("output1") or []:
            if str(row.get("odno", "")).lstrip("0") == str(odno).lstrip("0"):
                return {"filled": int(float(row.get("tot_ccld_qty") or 0)),
                        "avg_price": float(row.get("avg_prvs") or 0),
                        "remaining": int(float(row.get("rmn_qty") or 0)),
                        "cancelled": row.get("cncl_yn") == "Y"}
        return {"filled": 0, "avg_price": 0.0, "remaining": None, "cancelled": False}

    # -------------------------------------------------------------- 잔고
    def balance(self) -> tuple[float, dict[str, Position]]:
        positions: dict[str, Position] = {}
        fk = nk = ""
        cash = 0.0
        tr_cont = ""
        for _ in range(20):  # 연속조회
            r = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-balance", "balance", {
                "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "AFHR_FLPR_YN": "N", "OFL_YN": "",
                "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00", "CTX_AREA_FK100": fk, "CTX_AREA_NK100": nk}, tr_cont=tr_cont)
            for row in r.get("output1") or []:
                qty = int(float(row.get("hldg_qty") or 0))
                if qty > 0:
                    positions[row["pdno"]] = Position(qty, float(row.get("pchs_avg_pric") or 0))
            summary = (r.get("output2") or [{}])[0]
            # D+2 예수금(주문 가능 현금에 가까움) 우선
            cash = float(summary.get("prvs_rcdl_excc_amt") or summary.get("dnca_tot_amt") or 0)
            fk, nk = (r.get("ctx_area_fk100") or "").strip(), (r.get("ctx_area_nk100") or "").strip()
            if not nk:
                break
            tr_cont = "N"
        return cash, positions


class KISBroker(Broker):
    """실계좌(또는 KIS 모의투자) 주문. 국내 6자리 종목만 지원 (해외주식은 별도 API)."""

    mode = "live"

    def __init__(self, portfolio: Portfolio, client: KISClient, costs: CostModel | None = None,
                 max_slippage: float = 0.005, fill_timeout_s: float = 10.0, poll_s: float = 1.0):
        super().__init__(portfolio)
        self.client = client
        self.costs = costs or CostModel()
        self.max_slippage = max_slippage
        self.fill_timeout_s = fill_timeout_s
        self.poll_s = poll_s

    def sync_portfolio(self, universe: list[str] | None = None) -> Portfolio:
        """증권사 잔고를 진실의 원천으로 포트폴리오를 덮어쓴다 (DB 스냅샷과 다르면 로그).

        universe 의 심볼('005930.KS' 등)로 증권사 코드('005930')를 되돌려 매핑한다.
        """
        cash, by_code = self.client.balance()
        back = {krx_code(s): s for s in (universe or []) if krx_code(s)}
        positions = {back.get(code, code): pos for code, pos in by_code.items()}
        before = {k: v.qty for k, v in self.portfolio.positions.items() if v.qty}
        after = {k: v.qty for k, v in positions.items()}
        if before and before != after:
            log.warning("포지션 불일치 → 증권사 기준으로 동기화: DB=%s 증권사=%s", before, after)
        self.portfolio.cash = cash
        self.portfolio.positions = positions
        return self.portfolio

    def live_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        out = {}
        for sym in symbols:
            code = krx_code(sym)
            if code:
                try:
                    out[sym] = self.client.quote(code)
                except KISError as exc:
                    log.warning("%s 호가 조회 실패: %s", sym, exc)
        return out

    def submit(self, order: Order, quote: MarketQuote, ts: datetime) -> Fill | None:
        code = krx_code(order.symbol)
        if code is None:
            raise KISError("unsupported", f"{order.symbol}: 국내주식만 지원")
        ref = (quote.ask if order.side is Side.BUY else quote.bid) or quote.last
        limit = ref * (1 + self.max_slippage) if order.side is Side.BUY else ref * (1 - self.max_slippage)
        limit_px = round_to_tick(limit, order.side)
        placed = self.client.order(code, order.side, order.qty, limit_px)
        odno = placed["odno"]
        deadline = time.monotonic() + self.fill_timeout_s
        st = {"filled": 0, "avg_price": 0.0, "remaining": order.qty}
        while time.monotonic() < deadline:
            st = self.client.order_status(odno)
            if st["filled"] >= order.qty or st.get("cancelled"):
                break
            time.sleep(self.poll_s)
        if st["filled"] < order.qty and not st.get("cancelled"):
            try:
                self.client.cancel(odno, placed["orgno"])
                log.info("미체결 잔량 취소: %s %s", order.symbol, odno)
            except KISError as exc:
                log.error("잔량 취소 실패 (수동 확인 필요): %s %s", odno, exc)
            st = self.client.order_status(odno)
        if st["filled"] <= 0:
            return None
        filled = Order(order.symbol, order.side, st["filled"], "limit", limit_px, order.reason, order.prob_up,
                       order.prediction_id)
        fill = Fill(filled, ts, st["filled"], st["avg_price"] or limit_px,
                    self.costs.fee(order.side, st["avg_price"] or limit_px, st["filled"]))
        self.portfolio.apply(fill)
        return fill

"""KIS 모의투자 검증 스위트 — '실제 주문 경로가 정말로 동작하나'를 단계별로 확인하고 기록한다.

단계 (앞 단계가 실패하면 뒤는 건너뜀)
 1 설정       KIS_ENV · 계좌번호 형식 · 키 존재 (값은 절대 출력하지 않음)
 2 토큰       접근 토큰 발급/캐시
 3 잔고       예수금 · 보유 종목 → 이 시스템의 live 장부와 비교 (불일치 = 동기화 필요)
 4 시세·호가  현재가 · 매수1/매도1 · 호가 단위 정합성 · 스프레드
 5 지연       현재가 3회 왕복 시간 (ms)
 6 주문 경로  (모의 · 장중만) 체결되지 않을 가격으로 1주 지정가 → 조회로 접수 확인 → 취소 → 취소 확인
 7 체결 경로  (모의 · 장중 · fill=True 일 때만) 1주 매수(매도1호가) → 체결 확인 → 1주 매도 → 슬리피지·시뮬레이터 비교
 8 실시간     웹소켓 접속키 발급 (websockets 설치 시)
결과는 ops 'kis_validation' 에 저장 → Trading Readiness BROKER 관문이 읽는다. 실전(real) 계좌에서는 6·7 단계를 하지 않는다.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from .kis import KISClient, KISError, krx_tick, round_to_tick
from .portfolio import Side


def _step(steps, key, title, fn):
    t0 = time.monotonic()
    try:
        detail, extra = fn()
        steps.append({"key": key, "title": title, "ok": True, "detail": detail, "ms": round((time.monotonic() - t0) * 1000), **(extra or {})})
        return True
    except _Skip as e:
        steps.append({"key": key, "title": title, "ok": None, "detail": str(e), "ms": 0})
        return True
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 기록하고 멈춘다
        msg = f"{type(e).__name__}: {e}"[:300]
        steps.append({"key": key, "title": title, "ok": False, "detail": msg, "ms": round((time.monotonic() - t0) * 1000)})
        return False


class _Skip(Exception):
    pass


def run(client: KISClient, symbol: str = "005930", market_open: bool = False, order_test: bool = True,
        fill_test: bool = False, db_positions: dict | None = None, ws_key_fn=None, sleep=time.sleep) -> dict:
    steps: list[dict] = []
    ctx: dict = {}
    demo = client.env == "demo"

    def s_config():
        acct = f"{client.cano}-{client.prdt}"
        if not (len(client.cano) == 8 and client.cano.isdigit() and len(client.prdt) == 2):
            raise ValueError("계좌번호 형식이 8자리-2자리가 아님 (KIS_ACCOUNT)")
        if not client.app_key or not client.app_secret:
            raise ValueError("KIS_APP_KEY / KIS_APP_SECRET 없음")
        return f"{'모의투자' if demo else '실전'} · 계좌 ****{acct[-5:]}", {"env": client.env}

    def s_token():
        client.token()
        return "토큰 발급/캐시 정상", None

    def s_balance():
        cash, pos = client.balance()
        ctx["positions"] = pos
        diff = None
        if db_positions is not None:
            mine = {k.split(".")[0]: v for k, v in db_positions.items() if v}
            theirs = {k: p.qty for k, p in pos.items()}
            diff = {k: {"system": mine.get(k, 0), "broker": theirs.get(k, 0)} for k in set(mine) | set(theirs)
                    if mine.get(k, 0) != theirs.get(k, 0)}
        return (f"예수금 {cash:,.0f}원 · 보유 {len(pos)}종목" + (f" · 장부 불일치 {len(diff)}종목" if diff else " · 장부 일치" if diff is not None else "")), \
            {"cash": cash, "n_positions": len(pos), "mismatch": diff or {}}

    def s_quote():
        q = client.quote(symbol)
        ctx["quote"] = q
        if not q.last or q.last <= 0:
            raise ValueError("현재가 0")
        if q.bid and q.ask:
            if q.bid > q.ask:
                raise ValueError(f"호가 역전 bid {q.bid} > ask {q.ask}")
            t = krx_tick(q.last)
            if q.bid % t or q.ask % t:
                raise ValueError(f"호가 단위 불일치 (단위 {t})")
            spr = (q.ask - q.bid) / ((q.ask + q.bid) / 2) * 1e4
            return f"{symbol} {q.last:,.0f} · 매수1 {q.bid:,.0f} / 매도1 {q.ask:,.0f} · 스프레드 {spr:.1f}bp", {"spread_bps": round(spr, 2)}
        return f"{symbol} {q.last:,.0f} (호가 없음 — 장외)", None

    def s_latency():
        ts = []
        for _ in range(3):
            t0 = time.monotonic()
            client.price(symbol)
            ts.append((time.monotonic() - t0) * 1000)
        return f"평균 {sum(ts) / len(ts):.0f}ms (최대 {max(ts):.0f}ms)", {"latency_ms": round(sum(ts) / len(ts))}

    def s_order():
        if not demo:
            raise _Skip("실전 계좌 — 주문 테스트 안 함")
        if not order_test:
            raise _Skip("주문 테스트 끔")
        if not market_open:
            raise _Skip("장외 — 장중에 다시 실행하면 주문·취소 경로까지 확인")
        q = ctx["quote"]
        px = round_to_tick((q.bid or q.last) * 0.9, Side.SELL)
        placed = client.order(symbol, Side.BUY, 1, px)
        if not placed.get("odno"):
            raise KISError("no_odno", "주문번호 없음")
        st1 = client.order_status(placed["odno"])
        client.cancel(placed["odno"], placed.get("orgno") or "")
        sleep(0.5)
        st2 = client.order_status(placed["odno"])
        if st2.get("filled"):
            raise ValueError(f"체결 안 될 가격이었는데 {st2['filled']}주 체결 — 계좌 확인 필요")
        return f"주문 {placed['odno']} 접수 → 조회(잔량 {st1.get('remaining')}) → 취소 확인", {"odno": placed["odno"]}

    def s_fill():
        if not (demo and fill_test and market_open):
            raise _Skip("체결 테스트는 모의투자 · 장중 · 명시적 요청 시에만")
        from .exec_sim import simulate
        q = client.quote(symbol)
        sim = simulate("buy", 1, q.last, q.bid, q.ask, q.bid_qty, q.ask_qty)
        placed = client.order(symbol, Side.BUY, 1, int(q.ask or q.last))
        st = {"filled": 0}
        for _ in range(20):
            st = client.order_status(placed["odno"])
            if st.get("filled"):
                break
            sleep(0.5)
        if not st.get("filled"):
            client.cancel(placed["odno"], placed.get("orgno") or "")
            raise ValueError("매도1호가 지정가가 10초 안에 체결되지 않음")
        mid = ((q.bid or q.last) + (q.ask or q.last)) / 2
        real = (st["avg_price"] - mid) / mid * 1e4
        q2 = client.quote(symbol)
        sell = client.order(symbol, Side.SELL, 1, int(q2.bid or q2.last))
        for _ in range(20):
            if client.order_status(sell["odno"]).get("filled"):
                break
            sleep(0.5)
        return (f"1주 매수 {st['avg_price']:,.0f} (중간가 대비 {real:.1f}bp · 시뮬레이터 {sim.slippage_bps:.1f}bp) → 되팔기"), \
            {"parity": {"real_bps": round(real, 2), "sim_bps": round(sim.slippage_bps or 0, 2)}}

    def s_ws():
        if ws_key_fn is None:
            try:
                import websockets  # noqa: F401
            except ImportError:
                raise _Skip("websockets 미설치 — ./run.sh 가 설치") from None
            from .kis_ws import approval_key
            approval_key(client.base, client.app_key, client.app_secret)
        else:
            ws_key_fn()
        return "실시간 접속키 발급 정상", None

    plan = [("config", "설정", s_config), ("token", "토큰", s_token), ("balance", "잔고 · 장부 대조", s_balance),
            ("quote", "시세 · 호가", s_quote), ("latency", "응답 지연", s_latency), ("order", "주문 · 조회 · 취소", s_order),
            ("fill", "실제 체결 · 슬리피지", s_fill), ("realtime", "실시간 접속", s_ws)]
    ok = True
    for key, title, fn in plan:
        if not ok:
            steps.append({"key": key, "title": title, "ok": None, "detail": "앞 단계 실패로 건너뜀", "ms": 0})
            continue
        ok = _step(steps, key, title, fn)
    failed = [s for s in steps if s["ok"] is False]
    covered = {s["key"] for s in steps if s["ok"]}
    return {"ok": not failed, "env": client.env, "at": datetime.now(UTC).isoformat(), "symbol": symbol,
            "steps": steps, "failed": "; ".join(f"{s['title']}: {s['detail']}" for s in failed)[:300],
            "order_path_verified": "order" in covered, "fill_path_verified": "fill" in covered,
            "parity": next((s["parity"] for s in steps if s.get("parity")), None)}


__all__ = ["run"]

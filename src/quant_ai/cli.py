"""quant-ai 명령줄.

    quant-ai demo                     # 가상 데이터로 전체 파이프라인 + 대시보드 데이터 생성
    quant-ai serve                    # 대시보드 (http://127.0.0.1:8050)
    quant-ai collect prices --source yahoo --symbols 005930.KS,NVDA --years 5
    quant-ai train                    # walk-forward 백테스트 → 후보 모델 등록/게이트
    quant-ai decide                   # 연구/예측 모드: 멀티 AI 합의 신호 (주문 없음)
    quant-ai run --mode paper         # 24시간 스케줄러 (장중/장외 작업)
    quant-ai review                   # 오늘 복기
    quant-ai kill on|off              # 킬스위치 (신규 매수 즉시 중단)
    quant-ai orders --cash 10000000 --holdings my.csv --out orders.csv   # 수동 매매용 주문표
    quant-ai checkup --mode live      # 전략 건강검진 (손실이 과거 검증 범위 안인가)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import UTC, datetime, timedelta

from .config import Mode, Settings


def _app(args):
    from .pipeline import QuantAI

    settings = Settings.from_env()
    if getattr(args, "db", None):
        from dataclasses import replace
        settings = replace(settings, database_url=args.db)
    return QuantAI(settings)


def cmd_demo(args):
    from .demo import run_demo

    app = _app(args)
    run_demo(app, replay_days=args.days)
    print("\n완료. `quant-ai serve` 로 대시보드를 여세요.")


def cmd_serve(args):
    from .web.server import serve

    serve(_app(args), host=args.host, port=args.port)


def cmd_collect(args):
    from .data.collectors import prices as P
    from .data.db import session_scope
    from .data.models import Instrument

    app = _app(args)
    st = app.settings
    now = datetime.now(UTC)
    if args.what == "prices":
        src = {"yahoo": P.YahooPriceSource, "synthetic": P.SyntheticPriceSource}[args.source]()
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
        with session_scope(app.engine) as s:
            have = {i.symbol for i in s.query(Instrument)}
            for sym in syms:
                if sym not in have:
                    market = "INDEX" if sym.startswith("^") else ("KRX" if sym.endswith((".KS", ".KQ")) else "US")
                    s.add(Instrument(symbol=sym, market=market, name=sym))
        n = app.ingest_prices(src, syms, now - timedelta(days=365 * args.years), now)
        print(f"가격 {n}봉 저장")
    elif args.what == "krx":
        if not args.marcap_dir:
            sys.exit("--marcap-dir 필요 (FinanceData/marcap 의 data 폴더)")
        r = app.ingest_krx(args.marcap_dir, years=args.years, top_n=args.top)
        print(f"KRX: 종목 {r['symbols']} · 봉 {r['bars']:,} · 월별 유니버스 {r['months']}개월 · 마지막 {r['last_date']}")
    elif args.what == "news":
        from .data.collectors.news import NewsCollector
        with session_scope(app.engine) as s:
            print(f"뉴스 {NewsCollector(st.news_feeds).collect(s)}건")
    elif args.what == "disclosures":
        from .data.collectors.disclosures import DartCollector
        if not st.dart_api_key:
            sys.exit("DART_API_KEY 필요")
        with session_scope(app.engine) as s:
            print(f"공시 {DartCollector(st.dart_api_key).collect(s, (now - timedelta(days=args.days)).date(), now.date())}건")
    elif args.what == "macro":
        from .data.collectors.macro import FredCollector
        if not st.fred_api_key:
            sys.exit("FRED_API_KEY 필요")
        with session_scope(app.engine) as s:
            print(f"경제지표 {FredCollector(st.fred_api_key).collect(s, (now - timedelta(days=365 * args.years)).date())}건")


def cmd_train(args):
    rec, result, gate = _app(args).train_candidate()
    print(json.dumps(result.metrics, ensure_ascii=False, indent=1, default=float))
    print(f"모델 {rec.name}-{rec.version}: {'shadow 로 승격' if gate.passed else '탈락'}")
    for f in gate.failures:
        print(" -", f)


def cmd_decide(args):
    for d in _app(args).decide():
        s = d.signal
        print(f"\n{d.symbol}  {s.action}  P(up)={s.prob_up:.2f}  신뢰도 {s.confidence:.0f}  충돌 {s.conflict}")
        for c in s.contributions:
            p = "  기권" if c.prob_up is None else f"{c.stance:+.2f}"
            print(f"   {c.analyst:<8} {p}  w={c.weight:.2f}  [{c.backend}]")
        for v in s.vetoes:
            print("   VETO", v)


def cmd_run(args):
    from .scheduler import build_default_scheduler

    app = _app(args)
    mode = Mode(args.mode)
    if mode is Mode.LIVE:
        st = app.settings
        if st.broker == "kis" and st.kis_env == "demo":
            print("KIS 모의투자 계좌로 LIVE 파이프라인 실행 (실제 돈 아님) — 실전 안전장치는 KIS_ENV=real 에서 적용")
        else:
            champ = app.registry.champion()
            st.assert_live_allowed(champ is not None and champ.shadow_metrics is not None)
    build_default_scheduler(app, mode).run_forever()


def cmd_review(args):
    r = _app(args).review()
    print(json.dumps({"summary": r.summary, "lessons": r.lessons}, ensure_ascii=False, indent=1, default=str))


def cmd_kis_check(args):
    """KIS 연결 점검: 토큰 → 잔고 → 현재가/호가 (주문은 내지 않음)."""
    from .trading.kis import KISClient
    st = Settings.from_env()
    c = KISClient.from_env(st.artifacts_dir)
    print(f"환경: {c.env} ({c.base})")
    c.token()
    print("토큰 OK")
    cash, pos = c.balance()
    print(f"예수금(D+2): {cash:,.0f}원, 보유 {len(pos)}종목")
    for code, p in pos.items():
        print(f"  {code} {p.qty}주 @ {p.avg_price:,.0f}")
    q = c.quote(args.symbol)
    print(f"{args.symbol} 현재가 {q.last:,.0f} / 매수1 {q.bid} / 매도1 {q.ask}")
    if args.test_order:
        # 모의투자 전용: 체결되지 않을 가격(매수1호가 -10%)으로 1주 주문 → 접수 확인 → 즉시 취소
        from .trading.kis import round_to_tick
        from .trading.portfolio import Side
        if c.env != "demo":
            sys.exit("--test-order 는 KIS_ENV=demo(모의투자)에서만 허용")
        px = round_to_tick((q.bid or q.last) * 0.9, Side.SELL)
        placed = c.order(args.symbol, Side.BUY, 1, px)
        print(f"테스트 주문 접수: 주문번호 {placed['odno']} ({px:,}원 × 1주, 체결 안 될 가격)")
        print("  주문 상태:", c.order_status(placed["odno"]))
        c.cancel(placed["odno"], placed["orgno"])
        st = c.order_status(placed["odno"])
        print("  취소 후 상태:", st)
        print("✅ 주문·조회·취소 경로 정상" if st["filled"] == 0 else "⚠ 체결됨 — 모의투자 계좌에서 확인 필요")


def cmd_research(args):
    from .research import DEFAULT_TRIALS, register_best, run_krx_research
    trials = [t for t in DEFAULT_TRIALS if not args.trials or t.name in args.trials.split(",")]
    app = _app(args) if args.register else None
    prior = app.registry.n_trials() if app else 0
    rep = run_krx_research(args.marcap_dir, args.start, args.end, args.top, trials, args.out, prior_trials=prior)
    print("\n설정별 결과 (DSR 은 시도 횟수 보정):")
    for t in rep["trials"]:
        s = t["metrics"]["strategy"]
        print(f"  {t['trial']['name']:<22} Sharpe {s['sharpe']:.2f}  PSR {s['psr']:.2f}  DSR {t['dsr']:.2f}  "
              f"CAGR {s['cagr']:.1%}  MDD {s['max_drawdown']:.1%}  IR(vs 유니버스EW) {t['information_ratio_vs_ew']:.2f}")
    if app:
        rec, gate = register_best(app, rep)
        print(f"\n등록: {rec.name}-{rec.version} → {'shadow (게이트 통과)' if gate.passed else '탈락'}")
        for f in gate.failures:
            print("  -", f)


def cmd_lab(args):
    import warnings

    from .data.collectors.marcap import build_krx_dataset
    from .research_lab import default_configs, run_lab, save_lab_report
    warnings.filterwarnings("ignore")
    ds = build_krx_dataset(args.marcap_dir, args.start, args.end, args.top)
    print(f"데이터: {args.start}~{args.end}, 시총 상위 {args.top} (종목 {len(ds.bars)}), dev ≤ {args.dev_end}")
    res = run_lab(ds, default_configs(), dev_end=args.dev_end, prior_trials=args.prior_trials)
    print(f"\nholdout (선택 후 계산, 선택={res['chosen']}):")
    for r in res["results"]:
        h = r["holdout"]
        print(f"  {r['config']['name']:<26} Sharpe {h['strategy']['sharpe']:5.2f} CAGR {h['strategy']['cagr']:6.1%} "
              f"MDD {h['strategy']['max_drawdown']:6.1%} | KOSPI Sharpe {h['kospi']['sharpe']:.2f} CAGR {h['kospi']['cagr']:.1%}")
    path = save_lab_report(res, args.out, {"source": "FinanceData/marcap (KRX)", "start": args.start, "end": args.end,
                                           "top_n": args.top, "symbols": len(ds.bars)})
    print(f"\n리포트: {path}")


def _print_plan(plan, names):
    print(f"코어 {len(plan.core)}종목 ({'리밸런싱' if plan.core_rebalanced else '유지'}):")
    for s in plan.core:
        print(f"  #{plan.ranks.get(s, -1) + 1:<3} {s} {names.get(s, '')}  {plan.weights[s]:.1%}")
    for s, why in plan.vetoed.items():
        print(f"  ⛔ 거부 {s} {names.get(s, '')}: {why}")
    for s, why in plan.exits.items():
        print(f"  🚨 긴급 청산 {s} {names.get(s, '')}: {why}")
    print(f"위성 {len(plan.satellite)}종목:")
    for x in plan.satellite:
        print(f"  {x['symbol']} {names.get(x['symbol'], '')}  신뢰도 {x['confidence']:.0f}  {x['weight']:.1%}")
    for n in plan.notes:
        print("  ·", n)


def _names(app):
    from .data.db import session_scope
    from .data.models import Instrument
    with session_scope(app.engine) as s:
        return {i.symbol: i.name for i in s.query(Instrument)}


def cmd_cycle(args):
    app = _app(args)
    from .strategy.core_satellite import CoreSatelliteConfig
    use_ai = not (args.core_only or app.settings.core_only)
    r = app.run_core_satellite(Mode(args.mode), cfg=CoreSatelliteConfig(use_ai=use_ai))
    _print_plan(r["plan"], _names(app))
    print(f"체결 {len(r['fills'])}건")


def cmd_replay(args):
    """최근 N 거래일을 하루씩 재생: 그날 종가까지의 정보로 판단 → 종가 체결 (가상 장부)."""
    from datetime import timedelta

    from .trading.broker import MarketQuote
    app = _app(args)
    mode = Mode(args.mode)
    bars, _, _ = app.market_data()
    cal = max(bars.values(), key=len).index[-args.days:]
    for i, t in enumerate(cal):
        quotes = {s: MarketQuote(last=float(b.loc[t, "close"]), bid=float(b.loc[t, "close"]) * 0.9995,
                                 ask=float(b.loc[t, "close"]) * 1.0005, bid_qty=1e9, ask_qty=1e9)
                  for s, b in bars.items() if t in b.index}
        r = app.run_core_satellite(mode, as_of=t.to_pydatetime(), ts=t.to_pydatetime() + timedelta(hours=6, minutes=20),
                                   quotes=quotes)
        p = r["plan"]
        print(f"{t.date()} 코어 {len(p.core)}{' (리밸런싱)' if p.core_rebalanced else ''} · 위성 {len(p.satellite)} · "
              f"거부 {len(p.vetoed)} · 긴급청산 {len(p.exits)} · 체결 {len(r['fills'])}")
        if i % 10 == 9 or i == len(cal) - 1:
            rep = app.review(t.date())
            print(f"   복기: 채점 {rep.summary.get('n_resolved', 0)}건")


def cmd_health(args):
    from .web.api import DashboardAPI
    app = _app(args)
    h = DashboardAPI(app).health()
    print(json.dumps(h, ensure_ascii=False))
    sys.exit(0 if h["ok"] else 1)


def cmd_orders(args):
    """다른 증권사·ISA·수동 매매용 리밸런싱 주문표 (이 시스템의 장부·주문과 무관)."""
    from pathlib import Path

    from .strategy.order_sheet import parse_holdings
    text = sys.stdin.read() if args.holdings == "-" else Path(args.holdings).read_text(encoding="utf-8-sig") \
        if args.holdings else ""
    holdings = parse_holdings(text)
    sheet = _app(args).order_sheet(holdings, args.cash, use_ai=not args.no_ai)
    print(sheet.to_text())
    if args.out:
        Path(args.out).write_text(sheet.to_csv(), encoding="utf-8-sig")  # 엑셀에서 한글이 깨지지 않도록 BOM
        print(f"\nCSV 저장: {args.out}")


def cmd_checkup(args):
    """전략 건강검진: 실제 운용 성과가 과거 검증 범위 안인지."""
    from .strategy.health import format_value
    r = _app(args).strategy_health(args.mode, with_ic=not args.no_ic, notify=False)
    icon = {"ok": "🟢", "warn": "🟡", "critical": "🔴", "insufficient": "⚪"}
    print(f"{icon[r['status']]} 전략 건강검진 [{r['mode']}] {r['status'].upper()} · 운용 {r['days']}일 · 기준일 {r['as_of']}")
    for c in r["checks"]:
        vs = format_value(c)
        print(f"  {icon[c['status']]} {c['label']:<22} {vs:>9}   ({c['note']})")
    print(f"\n→ {r['action']}")
    e = r["expectations"]
    print(f"   참고: 과거 연평균 {e['cagr']:.1%}, 1년 보유 시 플러스였던 비율 {e['positive_1y_share']:.0%}, "
          f"KOSPI 를 이긴 비율 {e['beat_kospi_1y_share']:.0%}")
    sys.exit(2 if r["status"] == "critical" else 0)


def cmd_kill(args):
    _app(args).set_kill_switch(args.state == "on", by="cli")
    print(f"킬스위치 {args.state.upper()}")


def load_dotenv(path: str = ".env") -> None:
    """의존성 없이 .env 를 읽어 환경변수 기본값으로 설정 (이미 있는 값은 유지)."""
    import os
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in ('"', "'"):
            v = v[1:].split(v[0], 1)[0]  # 따옴표 안은 그대로 (# 포함 가능)
        else:
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()  # 줄 끝 주석 제거: KIS_ENV=demo  # 모의투자
        if v:
            os.environ.setdefault(k.strip(), v)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    import os
    if os.environ.get("QUANT_LOG_JSON") == "1":  # 서버 배포 시 로그 수집기(ELK/Loki 등)용
        class JsonFormatter(logging.Formatter):
            def format(self, r):
                d = {"ts": self.formatTime(r), "level": r.levelname, "logger": r.name, "msg": r.getMessage()}
                if r.exc_info:
                    d["exc"] = self.formatException(r.exc_info)
                return json.dumps(d, ensure_ascii=False)
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        logging.basicConfig(level=logging.INFO, handlers=[h])
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="quant-ai")
    p.add_argument("--db", help="DATABASE_URL 덮어쓰기")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo")
    d.add_argument("--days", type=int, default=60)
    d.set_defaults(fn=cmd_demo)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8050)
    s.set_defaults(fn=cmd_serve)
    c = sub.add_parser("collect")
    c.add_argument("what", choices=["prices", "krx", "news", "disclosures", "macro"])
    c.add_argument("--marcap-dir", default="")
    c.add_argument("--top", type=int, default=100)
    c.add_argument("--source", default="yahoo", choices=["yahoo", "synthetic"])
    c.add_argument("--symbols", default="")
    c.add_argument("--years", type=int, default=5)
    c.add_argument("--days", type=int, default=7)
    c.set_defaults(fn=cmd_collect)
    sub.add_parser("train").set_defaults(fn=cmd_train)
    sub.add_parser("decide").set_defaults(fn=cmd_decide)
    r = sub.add_parser("run")
    r.add_argument("--mode", default="paper", choices=[m.value for m in Mode])
    r.set_defaults(fn=cmd_run)
    sub.add_parser("review").set_defaults(fn=cmd_review)
    k = sub.add_parser("kill")
    k.add_argument("state", choices=["on", "off"])
    k.set_defaults(fn=cmd_kill)
    kc = sub.add_parser("kis-check", help="한국투자증권 API 연결 점검 (기본: 주문 없음)")
    kc.add_argument("--symbol", default="005930")
    kc.add_argument("--test-order", action="store_true", help="모의투자 전용: 1주 비체결 주문 후 즉시 취소")
    kc.set_defaults(fn=cmd_kis_check)
    sub.add_parser("health").set_defaults(fn=cmd_health)
    od = sub.add_parser("orders", help="리밸런싱 주문표 (다른 증권사·ISA·수동 매매용, 주문은 내지 않음)")
    od.add_argument("--cash", type=float, required=True, help="주문 가능 현금 (원)")
    od.add_argument("--holdings", help="보유 종목 CSV (종목코드,수량). '-' 는 표준입력. 없으면 전액 현금에서 시작")
    od.add_argument("--no-ai", action="store_true", help="AI 거부권·위성 없이 코어 팩터만 (LLM 비용 0)")
    od.add_argument("--out", help="CSV 저장 경로")
    od.set_defaults(fn=cmd_orders)
    ck = sub.add_parser("checkup", help="전략 건강검진 (성과가 과거 검증 범위 안인지)")
    ck.add_argument("--mode", default=None, help="paper / live / attr-core 등 장부 (기본: 현재 모드)")
    ck.add_argument("--no-ic", action="store_true", help="팩터 IC 계산 생략 (빠름)")
    ck.set_defaults(fn=cmd_checkup)
    cy = sub.add_parser("cycle", help="코어-위성 한 사이클 실행 (팩터 코어 + 멀티 AI)")
    cy.add_argument("--mode", default="paper", choices=["paper", "shadow", "live"])
    cy.add_argument("--core-only", action="store_true", help="AI 오버레이 없이 팩터 코어만 (QUANT_CORE_ONLY=true 와 같음)")
    cy.set_defaults(fn=cmd_cycle)
    rp = sub.add_parser("replay", help="최근 N 거래일 코어-위성 재생 (가상 장부 + AI 기여도)")
    rp.add_argument("--days", type=int, default=60)
    rp.add_argument("--mode", default="paper", choices=["paper", "shadow"])
    rp.set_defaults(fn=cmd_replay)
    rs = sub.add_parser("research", help="실제 KRX 데이터 walk-forward 연구 (생존편향 제거)")
    rs.add_argument("dataset", choices=["krx"])
    rs.add_argument("--marcap-dir", required=True, help="FinanceData/marcap 의 data 폴더")
    rs.add_argument("--start", type=int, default=2010)
    rs.add_argument("--end", type=int, default=datetime.now().year)
    rs.add_argument("--top", type=int, default=100)
    rs.add_argument("--trials", default="", help="쉼표로 구분한 설정 이름 (기본: 전부)")
    rs.add_argument("--out", default="artifacts/research")
    rs.add_argument("--register", action="store_true", help="최고 설정을 레지스트리에 등록하고 게이트 평가")
    rs.set_defaults(fn=cmd_research)
    lb = sub.add_parser("lab", help="전략 연구소: 여러 전략 dev/holdout 비교 (빠른 엔진)")
    lb.add_argument("--marcap-dir", required=True)
    lb.add_argument("--start", type=int, default=2010)
    lb.add_argument("--end", type=int, default=datetime.now().year)
    lb.add_argument("--top", type=int, default=100)
    lb.add_argument("--dev-end", default="2020-12-31", help="이 날짜까지로만 전략 선택")
    lb.add_argument("--prior-trials", type=int, default=0, help="이전에 시도한 설정 수 (DSR 보정)")
    lb.add_argument("--out", default="artifacts/research")
    lb.set_defaults(fn=cmd_lab)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

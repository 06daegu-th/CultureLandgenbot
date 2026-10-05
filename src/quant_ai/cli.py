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
    elif args.what == "sectors":
        from . import desk
        from .engines.sector import fill_map, sector_map
        w = desk.wics(app, force=True)
        print(f"WICS 공식 업종: {w.get('mapped', 0)}종목" + (f" (실패: {w['error']})" if w.get("error") else ""))
        bars, _, _ = app.market_data()
        todo = [s_ for s_ in bars if s_ not in sector_map(app.engine)]
        if todo:
            r = fill_map(app.engine, todo, limit=min(len(todo), 120), pause=0.5)
            print(f"나머지 (Yahoo 업종 → 한국어): {len(r['new'])}/{r['tried']}종목")
        mp = sector_map(app.engine)
        print(f"업종 분류: {sum(1 for s_ in bars if s_ in mp)}/{len(bars)}종목")
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
    from .recovery import startup
    try:
        r = startup(app)  # 꺼져 있던 동안 놓친 일 따라잡기 · 멈춘 작업 정리 · DB 점검
        if r.get("downtime_s"):
            print(f"재시작 복구: 꺼져 있던 시간 {r['downtime_s'] / 60:.0f}분 · 정리 {r['stale_jobs']}건 · DB {'정상' if r['db']['ok'] else '점검 필요'}")
    except Exception as e:  # noqa: BLE001 - 복구 실패가 시작을 막으면 안 됨
        print(f"재시작 복구 건너뜀: {e}")
    build_default_scheduler(app, mode).run_forever()


def cmd_review(args):
    r = _app(args).review()
    print(json.dumps({"summary": r.summary, "lessons": r.lessons}, ensure_ascii=False, indent=1, default=str))


def cmd_watchdog(args):
    from .watchdog import run
    run(_app(args), args.mode)


def cmd_power_study(args):
    from .desk import run_signal_study
    r = run_signal_study(_app(args), args.marcap_dir, args.start, top=args.top)
    print(r["verdict"])
    for k in ("dev", "holdout"):
        x = r[k]
        print(f"  {k:<8} IC {x.get('ic_mean')} (t={x.get('ic_t')}) · 무작위 대조 IC {x.get('placebo_ic_mean')} · "
              f"상위5분위 적중 {x.get('top_quintile_hit')} · 비용 후 상위20 초과 {x.get('top20_excess_net')} (t={x.get('top20_excess_net_t')})")
    print(r["caveat"])


def cmd_readiness(args):
    from .desk import readiness
    r = readiness(_app(args), args.mode)
    icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    print(f"{r['status']} ({r['mode']}) · {r['as_of']}")
    for c in r["checks"]:
        print(f"  {icon.get(c['status'], '·')} {c['key']:<11} {c['detail']}")


def cmd_kis_check(args):
    """KIS 연결 점검: 토큰 → 잔고 → 현재가/호가 (주문은 내지 않음). --suite 는 8단계 검증을 기록한다."""
    if getattr(args, "suite", False):
        from .desk import kis_validate
        r = kis_validate(_app(args), fill=args.fill, e2e=args.e2e)
        for st in r.get("steps", []):
            mark = "✅" if st["ok"] else "⏭" if st["ok"] is None else "❌"
            print(f"{mark} {st['title']:<14} {st['detail']}  ({st['ms']}ms)")
        print("결과:", "통과" if r["ok"] else f"실패 — {r.get('failed') or r.get('message')}")
        return
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


def cmd_warmup(args):
    """빈 화면 채우기: 뉴스·공시·거시 수집 → 공시 반응 통계 → AI 판단(코어 후보, 새 일봉마다 한 번) → 자동 감시."""
    from .actions import warmup
    r = warmup(_app(args), progress=lambda m: print("  ·", m, flush=True))
    for k, v in r.items():
        print(f"  {k}: {v}")


def cmd_guardian(args):
    """자동 킬스위치 10개 조건 점검. --act 면 critical 시 HALTED + champion 롤백."""
    r = _app(args).guardian(args.mode, act=args.act)
    icon = {"ok": "🟢", "warn": "🟡", "critical": "🔴", "na": "⚪", "unknown": "⚫"}
    print(f"매매 상태: {r['state']}  [{r['mode']}]")
    for c in r["conditions"]:
        print(f"  {icon.get(c['status'], '?')} {c['name']:<24} {c['detail']}")
    sys.exit(2 if r["state"] == "HALTED" else 0)


def cmd_net_alpha(args):
    """증명 체인: 시점 정확 → 확률 보정 → AI 추가수익 → 비용 후 → 실주문 동일 → 반복."""
    from .analytics import net_alpha_report
    r = net_alpha_report(_app(args), args.mode, args.market.upper())
    icon = {"pass": "🟢", "fail": "🔴", "insufficient": "⚪"}
    print(f"증명 체인 [{r['market']} · {r['mode']}] — {r['verdict']['title']}")
    for st in r["steps"]:
        print(f"  {icon[st['status']]} {st['title']:<18} {st['headline']:>10}   {st['detail']}")
    if r["attribution"]:
        print("\n수익 분해:")
        for a in r["attribution"]:
            print(f"  {a['label']:<30} {a['value']:+.2%}")


def cmd_us(args):
    from .global_market import run_cycle, sync
    app = _app(args)
    r = sync(app)
    print(f"미국 일봉: 성공 {r['ok']} · 실패 {len(r['failed'])}" + (f" ({', '.join(r['failed'][:8])})" if r["failed"] else ""))
    c = run_cycle(app, force=args.force)
    if c.get("skipped"):
        print("  ·", c["skipped"])
    else:
        print(f"  기준일 {c['as_of']} · AI 판단 {c['analyzed']}종목")
        for b, x in c["books"].items():
            print(f"  {b:<14} 체결 {x['fills']}건 · 코어 {', '.join(x['core'][:10])}" + (" (리밸런싱)" if x["rebalanced"] else ""))


def cmd_db_clean(args):
    """DB 정리: 기본은 미리보기. --yes 로 실제 삭제 (주문·판단·복기 기록은 보존)."""
    from .actions import db_maintenance
    r = db_maintenance(_app(args), dry_run=not args.yes)
    for x in r["rows"]:
        print(f"  {x['label']:<44} {x['rows']:>9,}행")
    size = lambda b: f"{(b or 0) / 1e6:,.1f}MB"  # noqa: E731
    print(f"  합계 {r['total']:,}행 · 크기 {size(r['size_before'])}" + (f" → {size(r['size_after'])} ({r['vacuum']})" if args.yes else ""))
    print(f"  {r['kept']}")
    if not args.yes and r["total"]:
        print("  실제로 지우려면: ./run.sh db-clean --yes")


def cmd_chat(args):
    """터미널 채팅: 대시보드 채팅 AI 와 같은 두뇌 (종목·시장·서버·DB·성과)."""
    from .assistant import chat_client, reply
    app = _app(args)
    client, prov = chat_client(app.settings)
    print(f"Quant AI 채팅 — {prov + ' · ' + client.models[0] if client else 'AI 키 없음 (데이터 요약 모드)'}  (종료: exit)")
    sid = "cli"
    msgs = [args.message] if args.message else None
    while True:
        try:
            q = msgs.pop(0) if msgs else input("\n나> ").strip()
        except (EOFError, KeyboardInterrupt, IndexError):
            break
        if not q or q.lower() in ("exit", "quit", "종료"):
            break
        r = reply(app, q, sid)
        print(f"\nAI> {r['answer']}")
        if r["tools_used"]:
            print("   (조회: " + ", ".join(t["tool"] for t in r["tools_used"]) + ")")
        for a in r["actions"]:
            print(f"   ▶ 제안: {a['label']} — 대시보드 버튼 또는 ./run.sh {a['action'].replace('_', '-')}")
        if args.message:
            break


def cmd_cashflow(args):
    """입금(+)·출금(-) 기록 → 건강검진이 입출금을 수익으로 착각하지 않게."""
    from datetime import date as _date
    app = _app(args)
    if args.amount is not None:
        app.add_cashflow(args.mode, args.amount, _date.fromisoformat(args.date) if args.date else None, args.memo or "")
    flows = app.cashflows(args.mode)
    print(f"[{args.mode}] 입출금 기록 {len(flows)}건 · 순입금 {sum(f['amount'] for f in flows):+,.0f}원")
    for f in flows[-20:]:
        print(f"  {f['date']}  {f['amount']:+15,.0f}원  {f.get('memo', '')}")


def _ai_hint(msg: str) -> str:
    """오류 문구 → 사용자가 할 일 한 줄."""
    m = msg.lower()
    if "error-10" in m or "1010" in m or "banned your access" in m:
        return " → 요청 서명 차단(Cloudflare 1010): 최신 버전으로 업데이트하면 해결"
    if "사용 가능한 모델 없음" in msg:
        if "http 429" in m:
            return " → 시도한 모델이 모두 무료 한도 초과: 한국시간 오후 4~5시(UTC 자정)에 초기화, 그 전엔 다른 AI 로 계속"
        return (" → 이 계정에서 쓸 수 있는 모델을 못 찾음: 공급자 콘솔에서 키 등급·사용 가능 모델 확인, "
                "또는 .env 에 QUANT_<공급자>_MODELS=쓸 수 있는 모델명 (그 AI 는 기권하고 나머지로 계속)")
    if "not allowed to access" in m:
        return " → 이 계정에서 막힌 모델: 다른 모델로 자동 전환 (토큰은 정상)"
    if "workers ai" in m or ("cloudflare" in m and "authentication" in m):
        return (" → Cloudflare API 토큰에 'Workers AI' 권한 필요: dash.cloudflare.com → 내 프로필 → API 토큰 → "
                "'Workers AI' 템플릿으로 새 토큰 → .env CLOUDFLARE_API_TOKEN 교체 (계정 ID 도 확인)")
    if "http 503" in m or "overloaded" in m:
        return " → 공급자 일시 과부하: 다른 모델로 자동 전환됨, 잠시 후 다시"
    if any(x in m for x in ("tunnel connection", "timed out", "name or service", "nodename", "connection refused",
                            "network is unreachable", "urlopen error", "ssl")):
        return " → 네트워크·방화벽·VPN 확인 (회사망이면 차단됐을 수 있음)"
    if any(x in m for x in ("api key", "api_key", "unauthorized", "invalid_api_key", "permission", "http 401", "http 403")):
        return " → .env 의 키 값 확인 (앞뒤 공백·따옴표 없이, 발급처: docs/ENV_KEYS.md)"
    if "http 429" in m or "429" in m or "quota" in m or "rate limit" in m:
        return " → 무료 한도 초과: 잠시 후 다시 (자동으로 다음 모델로 전환됨)"
    if "http 404" in m or "model" in m and "not found" in m:
        return " → 모델 이름 확인 (QUANT_<공급자>_MODELS 비우면 기본값)"
    if "http 400" in m:
        return " → 요청 거부: 키가 잘못됐거나 모델이 이 계정에서 안 열려 있음"
    return ""


def _doctor_ai(app) -> list[tuple[str, str, str]]:
    """역할별 AI 에 아주 짧은 요청을 보내 키·모델·네트워크를 확인 (무료 한도 1회씩 사용)."""
    from .analysts.analysts import assign_roles, make_llm_client
    out = []
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    import time as _t
    checked: dict[str, tuple] = {}
    for role, prov in assign_roles(app.settings).items():
        if prov in checked:  # 같은 공급자를 두 역할이 쓰면 한 번만 확인 (무료 한도·시간 절약)
            st_, msg = checked[prov]
            out.append((st_, f"AI {role}", msg + " (위와 같은 공급자)"))
            continue
        print(f"  · AI {role} ({prov}) 확인 중… (최대 40초)", flush=True)
        t0 = _t.monotonic()
        try:
            c = make_llm_client(app.settings, prov)
            # 점검은 짧게: 타임아웃 40초 · 재시도 없음 · 분당 한도 대기 없음 · 짧은 답
            for k, v in (("timeout", 40.0), ("retries", 1), ("min_interval", 0.0), ("max_tokens", 1024)):
                if hasattr(c, k):
                    setattr(c, k, v)
            r = c.complete_json("연결 점검이다.", '{"ok": true} 를 그대로 출력하라.', schema)
            first = (getattr(c, "models", None) or [c.model])[0]
            note = " · 설정 모델 대신 자동 전환" if c.model != first or getattr(c, "_switched", False) else ""
            res = ("ok" if r.get("ok") is True else "warn",
                   f"{prov} · {c.model} 응답 정상 ({_t.monotonic() - t0:.1f}초){note}")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            res = ("fail", f"{prov}: {msg[:260]}{_ai_hint(msg)}")
        checked[prov] = res
        out.append((res[0], f"AI {role}", res[1]))
    return out


def cmd_doctor(args):
    """실행 전 점검: 설정·키·DB·데이터 최신성·marcap·AI·증권사·알림. 키 값은 출력하지 않는다."""
    import os
    from pathlib import Path

    from sqlalchemy import func, select

    from .analysts.analysts import assign_roles
    from .data.db import session_scope
    from .data.models import Instrument, PriceBar
    rows: list[tuple[str, str, str]] = []
    add = lambda st, name, msg: rows.append((st, name, msg))  # noqa: E731
    add("ok" if sys.version_info >= (3, 11) else "fail", "Python", sys.version.split()[0] + " (3.11 이상 필요)")
    add("ok" if Path(".env").exists() else "warn", ".env", "있음" if Path(".env").exists()
        else "없음 → cp .env.example .env 후 값 입력 (./run.sh setup)")
    try:
        app = _app(args)
    except Exception as e:  # noqa: BLE001
        add("fail", "설정/DB", str(e)[:200])
        return _print_doctor(rows)
    st = app.settings
    add("ok", "모드", f"QUANT_MODE={st.mode.value} · 전략={st.strategy}{' (코어 전용)' if st.core_only else ''}")
    from .auth import Auth
    for err in Auth().config_errors():  # v19: 대시보드가 시작을 거부하는 로그인 설정 오류를 미리 알려 준다
        add("fail", "웹 로그인", err)
    try:
        with session_scope(app.engine) as s:
            n_sym = s.scalar(select(func.count()).select_from(Instrument)) or 0
            last = s.scalar(select(func.max(PriceBar.ts)))
        add("ok", "DB", f"연결 OK · 종목 {n_sym}개")
        if last is None:
            add("fail", "주가 데이터", "없음 → ./run.sh data (marcap 받기 + quant-ai collect krx)")
        else:
            age = app.data_age_days(last)
            lim = st.max_data_age_days
            add("ok" if age <= 1 else "warn" if not lim or age <= lim else "fail", "주가 데이터",
                f"마지막 {str(last)[:10]} · {age}영업일 전" + (f" (>{lim} 이면 자동매매 중단)" if lim else ""))
    except Exception as e:  # noqa: BLE001
        add("fail", "DB", str(e)[:200])
    md = os.environ.get("QUANT_MARCAP_DIR")
    if md:
        ok = Path(md).is_dir() and any(Path(md).glob("*.parquet"))
        add("ok" if ok else "fail", "marcap", md if ok else f"{md} 에 parquet 없음 → git clone FinanceData/marcap")
    elif (Path(os.environ.get("QUANT_HOME") or Path.home() / ".quant-ai") / "data" / "marcap" / ".git").exists() \
            or Path("data/marcap/.git").exists():
        add("ok", "marcap", "기본 위치 사용 (스케줄러가 매일 자동 갱신)")
    else:
        add("warn", "marcap", "데이터 폴더 없음 → ./run.sh data 한 번 실행하면 이후 자동 갱신")
    roles = assign_roles(st)
    names = {"claude": "Claude", **{k: k for k in st.llm_providers}}
    if roles:
        add("ok", "AI 구성", " · ".join(f"{r}={names.get(p, p)}" for r, p in roles.items()))
    else:
        add("warn", "AI 구성", "LLM 키 없음 → 휴리스틱 (코어 전용이면 문제 없음). 무료: GEMINI/GROQ/NVIDIA/CLOUDFLARE")
    if args.ai:
        rows += _doctor_ai(app)
    if st.broker == "kis":
        miss = [k for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT") if not os.environ.get(k)]
        add("fail" if miss else "ok", "KIS 키", f"누락: {', '.join(miss)}" if miss else f"설정됨 · KIS_ENV={st.kis_env}")
        if st.kis_env == "real":
            try:
                st.assert_live_allowed(champion_ready=True)
                add("warn", "실전 안전장치", f"통과 · 소액 상한 {st.live_max_capital:,.0f}원 — 실제 돈입니다")
            except Exception as e:  # noqa: BLE001
                add("fail", "실전 안전장치", str(e)[:200])
        if args.kis and not miss:
            try:
                from .trading.kis import KISClient
                cash, pos = KISClient.from_env(st.artifacts_dir).balance()
                add("ok", "KIS 연결", f"잔고 조회 OK · 예수금 {cash:,.0f}원 · 보유 {len(pos)}종목")
            except Exception as e:  # noqa: BLE001
                add("fail", "KIS 연결", str(e)[:200])
    else:
        add("warn", "증권사", "KIS 미연결 → 가상매매(PAPER). .env 에 KIS 모의투자 키 3개를 넣으면 ./run.sh 가 모의계좌로 자동매매")
    add("ok" if app.notifier.enabled else "warn", "알림", "설정됨" if app.notifier.enabled
        else "미설정 → 체결·장애 알림을 못 받음 (Discord/Slack/Telegram 권장)")
    if args.notify and app.notifier.enabled:
        app.notifier.send("✅ Quant AI 알림 테스트 (quant-ai doctor --notify)", "info")
        add("ok", "알림 테스트", "전송함 — 휴대폰에서 확인")
    add("warn" if app.kill_switch_on() else "ok", "킬스위치", "ON (신규 매수 중단 상태)" if app.kill_switch_on() else "OFF")
    _print_doctor(rows)


def _print_doctor(rows):
    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}
    for st_, name, msg in rows:
        print(f"{icon[st_]} {name:<12} {msg}")
    n_fail = sum(r[0] == "fail" for r in rows)
    print(f"\n{'문제 ' + str(n_fail) + '건 — 위 ❌ 부터 해결하세요' if n_fail else '실행 준비 완료'}")
    sys.exit(1 if n_fail else 0)


def cmd_community(args):
    """커뮤니티 수집 점검: 종목 하나로 출처마다 실제로 글을 받아 오는지 보여 준다 (네이버 모바일 토론실 · PC 게시판 · StockTwits)."""
    from .data.collectors import community as C
    syms = [s.strip() for s in (args.test or "005930,NVDA").split(",") if s.strip()]
    for sym in syms:
        fns = [C.fetch_naver_mobile, C.fetch_naver_board] if sym[:1].isdigit() else [C.fetch_stocktwits]
        for fn in fns:
            try:
                posts = fn(sym)
                s = C.summarize(posts)
                print(f"  ✓ {sym} {fn.__name__}: {s['n']}건 · 낙관 {s['bull']} · 비관 {s['bear']} → {s['label']}")
                for p in posts[:3]:
                    print(f"      - {p['title'][:60]}")
            except Exception as e:  # noqa: BLE001 - 점검 결과로 보여 줌
                print(f"  ✕ {sym} {fn.__name__}: {type(e).__name__} {str(e)[:120]}")
    if args.collect:
        app = _app(args)
        syms = C.symbols_for(app)  # v27: 관심·보유 + 오늘 많이 움직인 종목
        print(f"  수집 대상 {len(syms)}종목: {', '.join(syms[:25])}")
        print(C.collect(app.engine, syms))


def cmd_logos(args):
    """로고 미리 받기: 관심·보유·주요 종목 (또는 --symbols). 출처별 개수와 이니셜로 남은 종목을 보여준다."""
    from . import logos
    from .center import watchlist
    app = _app(args)
    if args.symbols:
        syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    elif getattr(args, "all", False):  # v24: 유명하지 않은 종목까지 — 화면에 처음 나올 때 기다리지 않게 미리
        from sqlalchemy import select

        from .data.db import session_scope
        from .data.global_stocks import GLOBAL_STOCKS
        from .data.models import Instrument
        with session_scope(app.engine) as s:
            syms = [i.symbol for i in s.scalars(select(Instrument).where(Instrument.market != "INDEX"))]
        syms += [g[0] for g in GLOBAL_STOCKS]
    else:
        syms = [r["symbol"] for r in watchlist(app)["rows"]]
        for m in ("paper", "shadow", "live", "us-paper"):
            try:
                syms += [s_ for s_, p in app.load_portfolio(m).positions.items() if p.qty]
            except Exception:  # noqa: BLE001, S112
                continue
        bars, _ = app._all_bars()
        syms += sorted(bars)[: args.top]
    print(f"로고 {len(dict.fromkeys(syms))}종목 받는 중… (실패 기억 무시: {'예' if args.retry else '아니오'})")
    r = logos.prefetch(app, syms, force=args.retry)
    names = {"custom": "직접 넣은 파일", "toss": "토스증권 아이콘", "alpha": "알파스퀘어 아이콘", "fmp": "FMP(미국)", "favicon": "홈페이지 아이콘",
             "naver": "네이버 증권 로고", "cmc": "companiesmarketcap", "eodhd": "EODHD(미국)",
             "cache": "이전에 받은 것", "bundled": "내장 로고 (프로젝트에 포함)", "logodev": "logo.dev",
             "default": "기본 기업 아이콘 (못 받음)", "monogram": "이니셜 (못 받음)"}
    for k, v in sorted(r["by_source"].items(), key=lambda x: -x[1]):
        print(f"  {names.get(k, k)}: {v}")
    if r["missing"]:
        print(f"  기본 아이콘으로 남은 종목: {', '.join(r['missing'][:20])}{' …' if len(r['missing']) > 20 else ''}")
        print("  → 인터넷 연결 확인 후 ./run.sh logos --retry · 원하는 그림은 artifacts/logos/custom/<종목코드>.png 로 직접 넣기")


def cmd_datacheck(args):
    """v29 데이터 정합성 점검: 일봉 최신성 · 자동 갱신 · 원천 정합성(제한폭·시가총액·수정주가) · 외부 시세 대조."""
    from .datacheck import run
    r = run(_app(args), online=not args.offline)
    icon = {"good": "🟢", "warn": "🟡", "bad": "🔴"}
    print(f"{icon.get(r['status'], '·')} {r['headline']}")
    f = r["freshness"]
    print(f"  일봉: {f.get('date') or '없음'} ({f.get('age') or '-'}) · 종목 {r['n_symbols']}")
    print(f"  자동 갱신: {r['update']['text']}")
    print(f"  외부 시세 대조: {r['reference']['text']}")
    for row in r["reference"]["rows"]:
        print(f"    {row['symbol']} {row.get('name', '')}: {row.get('text')}" + (f" ({row['source']})" if row.get("source") else ""))
    for x in r["focus"].values():
        w = x["w52"]
        print(f"  {x['name']} {x['last']:,.0f} · 52주 {w['low']:,.0f}({w['low_date']}) ~ {w['high']:,.0f}({w['high_date']})"
              + (f" · 시가총액 {x['marcap'] / 1e12:,.1f}조" if x.get("marcap") else ""))
    for it in r["issues"][: args.limit]:
        print(f"  {'❌' if it['level'] == 'bad' else '⚠'} {it['symbol']} {it['name']} {it.get('date') or ''}: {it['text']}")
    if len(r["issues"]) > args.limit:
        print(f"  … 외 {len(r['issues']) - args.limit}건 (화면 '데이터 점검'에서 전부)")
    print(f"  가격 조정 이벤트(액면분할·권리락 등) {r['n_adj_events']}건 — 수정주가로 이어 붙임")


def cmd_proof_project(args):
    """v29 증명 프로젝트: start (규칙·기준 봉인) · status · record (오늘 기록) · end."""
    from . import proof
    app = _app(args)
    if args.action == "start":
        r = proof.start(app, args.mode, args.principal, args.max_loss, public=args.public)
        print(f"시작: {r['id']} ({r['mode']}) · {r['start']} ~ {r['end']} · 봉인 {r['hash'][:16]}…")
        for x in r["rules"]:
            print(f"  · {x['title']}: {x['value']}")
        print("성공 기준 (바꿀 수 없음): " + " · ".join(c["title"] for c in r["criteria"]))
        return
    if args.action == "end":
        r = proof.end(app, args.reason or "")
        print(f"종료: {r['id']} · {r['final'].get('verdict')}")
        return
    if args.action == "record":
        r = proof.record_day(app, force=True)
        print("오늘 기록:" if r else "기록 안 함 (진행 중 프로젝트 없음 · 휴장일 · 이미 기록됨)", r or "")
        return
    s = proof.status(app)
    if not s.get("active"):
        print("진행 중인 증명 프로젝트가 없어요 — quant-ai proof-project start (기본 100만원 · 실제 계좌)")
        return
    a = s["active"]
    pct = lambda v: "-" if v is None else f"{v * 100:+.2f}%"  # noqa: E731
    print(f"{a['id']} ({a['mode']}) · {s['verdict']}")
    print(f"  누적 {pct(s['cum'])} · 코스피 {pct(s['bench_cum'])} · 최대 낙폭 {pct(s['mdd'])} · 체결 {s['orders']}건 · 기록 {s['n_days']}일")
    print(f"  봉인: {s['chain']['text']} · 공개 페이지 {'켜짐 (/proof)' if s['public'] else '꺼짐'}")
    for c in s["criteria"]:
        print(f"  {'✅' if c['ok'] else '·'} {c['title']}: {proof._crit_value(c)}")


def cmd_ops_status(args):
    """./run.sh status 가 부른다 — 24시간 운영 · 데이터 날짜 · 뉴스 · 작업 실패."""
    from .center import ops_status
    st = ops_status(_app(args))
    hb = st["heartbeat_age_s"]
    print(f"{'✔' if st['running'] else '⚠'} 24시간 운영: {'켜짐' if st['running'] else '꺼짐'}" + (f" (마지막 신호 {hb / 60:.0f}분 전)" if hb is not None else ""))
    b = st["bar"]
    print(f"{'✔' if not b.get('lag_days') else '⚠'} 주가 데이터: {b.get('label') or '없음'}" + (f" · {b['lag_days']}거래일 밀림" if b.get("lag_days") else " · 최신"))
    print(f"  최근 24시간 뉴스 {st['news_24h']}건 · 작업 실패 {st['job_failures_24h']}건")
    for it in st["issues"]:
        print(f"  → {it['text']}: {it['fix']}")


def cmd_report(args):
    """사용자 PC 점검 보고서 — 키·계좌번호·금액 없이, 그대로 보내도 되는 글 (report.py)."""
    from pathlib import Path

    from .report import write
    out, text = write(_app(args), Path(args.out) if args.out else None, net=not args.no_net)
    print(text)
    print(f"\n저장: {out.resolve()}")


def cmd_db_ping(args):
    """run.sh 용: 0 = 연결 + 주가 데이터 있음, 3 = 연결되지만 비어 있음, 1 = 연결 실패. 스키마를 만들지 않는다."""
    import os

    from sqlalchemy import inspect, text

    from .data.db import make_engine
    url = args.db or os.environ.get("DATABASE_URL") or Settings.from_env().database_url
    try:
        eng = make_engine(url, connect_timeout=3)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
            has = "price_bars" in inspect(c).get_table_names() and \
                c.execute(text("SELECT 1 FROM price_bars LIMIT 1")).first() is not None
    except Exception as e:  # noqa: BLE001
        print(f"DB 연결 실패: {str(e).splitlines()[0][:200]}")
        sys.exit(1)
    print("ok" if has else "empty")
    sys.exit(0 if has else 3)


def cmd_kill(args):
    _app(args).set_kill_switch(args.state == "on", by="cli")
    print(f"킬스위치 {args.state.upper()}")


def cmd_auth_setup(args):
    """웹 로그인 설정값 만들기: 비밀번호 해시 + 2단계 인증(TOTP) 비밀값 + 읽기 전용 토큰. .env 에 붙여 넣는다."""
    import getpass
    import secrets

    from .auth import hash_password, new_secret, provisioning_uri, totp
    pw = args.password or getpass.getpass("새 웹 비밀번호 (10자 이상): ")
    if len(pw) < 10:
        sys.exit("비밀번호는 10자 이상")
    sec = new_secret()
    print("# ---- .env 에 아래 줄을 넣으세요 (값은 아무에게도 보여주지 마세요)")
    print(f"QUANT_WEB_PASSWORD_HASH={hash_password(pw)}")
    print(f"QUANT_WEB_TOTP_SECRET={sec}")
    if args.viewer:
        print(f"QUANT_WEB_VIEWER_TOKEN={secrets.token_urlsafe(24)}")
    print("# OTP 앱(Google Authenticator·1Password 등)에 아래 주소를 등록하거나 비밀값을 직접 입력:")
    print(f"# {provisioning_uri(sec)}")
    print(f"# 지금 코드(확인용): {totp(sec)}")


def cmd_budget(args):
    """원금·최대 손실 → 모든 한도 계산 (--save 면 저장해 바로 적용)."""
    from .budget import effective_vol, plan, replay, save
    app = _app(args)
    p = save(app, {"principal": args.principal, "max_loss": args.max_loss, "first_stage": args.first_stage, "on_stop": args.on_stop}) \
        if args.save else plan(args.principal, args.max_loss, args.first_stage, daily_vol=effective_vol(app)[0], on_stop=args.on_stop)
    for x in p["plain"]:
        print(x)
    for w in p.get("warnings") or []:
        print("⚠ " + w)
    r = replay(app, p)
    for x in r.get("plain") or [r.get("message", "")]:
        print("[과거 재생] " + x)
    print("# 한도 (근거)")
    for k, v in p["limits"].items():
        print(f"  {k:<20} {v:>14,}   {p['why'][k]}" if isinstance(v, int) else f"  {k:<20} {v:>14.2%}   {p['why'][k]}")
    print("저장됨 — 웹·스케줄러가 바로 이 한도를 씁니다" if args.save else "미리 보기 — 저장하려면 --save")


def cmd_netcheck(args):
    """외부 데이터 소스 연결 점검 (공개 소스 + DART·FRED·ECOS 키)."""
    from .netcheck import run
    r = run(_app(args))
    icon = {"ok": "✔", "missing": "○"}
    for x in r["rows"]:
        print(f"{icon.get(x['status'], '✖')} {x['title']:<28} {x['detail']} ({x.get('ms') or 0}ms)")
    print(r["headline"])


def cmd_keys(args):
    """.env 의 DART·FRED·ECOS 키 진단 (+ --probe: 실제 연결 시험). 키 값은 출력하지 않는다."""
    from .keys import diagnose, probe
    app = _app(args)
    d = diagnose(app)
    print(f".env: {d['env_file'] or '찾지 못함 (프로젝트 폴더에 .env 필요)'}")
    for x in d["issues"]:
        print(f"  ⚠ {x}")
    icon = {"ok": "✔", "warn": "⚠", "bad": "✖", "missing": "○"}
    for k in d["keys"]:
        print(f"{icon[k['status']]} {k['key']:<14} {k['title']} · {'불러옴' if k['loaded'] else '안 불러옴'}"
              + (f" · 길이 {k['length']}" if k["length"] else ""))
        for t in k["tips"]:
            print(f"    - {t}")
    if args.probe:
        for name, r in probe(app)["results"].items():
            print(f"{'✔' if r['ok'] else '○' if r['status'] == 'missing' else '✖'} 연결 {name.upper()}: {r['message']} ({r.get('ms', 0)}ms)")


def load_dotenv(path: str | None = None) -> None:
    """의존성 없이 .env 를 읽어 환경변수로 (이미 값이 있으면 유지, 빈 값은 채움 · export/BOM/따옴표/주석 처리 ·
    프로젝트 폴더의 .env 도 찾음 · OPENDART_API_KEY 같은 다른 이름도 인식) — keys.load_into_environ."""
    from pathlib import Path

    from .keys import load_into_environ
    load_into_environ(Path(path) if path else None)


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
    c.add_argument("what", choices=["prices", "krx", "news", "disclosures", "macro", "sectors"])
    c.add_argument("--marcap-dir", default="")
    c.add_argument("--top", type=int, default=100)
    c.add_argument("--source", default="yahoo", choices=["yahoo", "synthetic"])
    c.add_argument("--symbols", default="")
    c.add_argument("--years", type=int, default=5)
    c.add_argument("--days", type=int, default=7)
    c.set_defaults(fn=cmd_collect)
    au = sub.add_parser("auth-setup", help="웹 로그인(비밀번호·2단계 인증·읽기 전용 토큰) 설정값 만들기")
    au.add_argument("--password", help="비대화형 (생략하면 입력 받음)")
    au.add_argument("--viewer", action="store_true", help="읽기 전용 토큰도 만들기")
    au.set_defaults(fn=cmd_auth_setup)
    sub.add_parser("netcheck", help="외부 데이터 소스 연결 점검").set_defaults(fn=cmd_netcheck)
    rp = sub.add_parser("report", help="점검 보고서 (키·계좌번호·금액 없음 — 그대로 보내도 됨)")
    rp.add_argument("--out", default="")
    rp.add_argument("--no-net", action="store_true", help="외부 연결 시험 생략")
    rp.set_defaults(fn=cmd_report)
    bg = sub.add_parser("budget", help="원금·최대 손실 → 모든 한도 계산 (--save 로 저장)")
    bg.add_argument("--principal", type=float, required=True)
    bg.add_argument("--max-loss", type=float, required=True)
    bg.add_argument("--first-stage", type=float, default=0.10)
    bg.add_argument("--on-stop", choices=["hold", "reduce", "liquidate"], default="hold",
                    help="최대 손실 정지 후 보유분: hold 유지 · reduce 절반 매도표 · liquidate 전량 매도표 (자동 매도 안 함)")
    bg.add_argument("--save", action="store_true")
    bg.set_defaults(fn=cmd_budget)
    k = sub.add_parser("keys", help="DART·FRED·ECOS 키 진단 (--probe: 실제 연결 시험)")
    k.add_argument("--probe", action="store_true")
    k.set_defaults(fn=cmd_keys)
    sub.add_parser("train").set_defaults(fn=cmd_train)
    sub.add_parser("decide").set_defaults(fn=cmd_decide)
    r = sub.add_parser("run")
    r.add_argument("--mode", default="paper", choices=[m.value for m in Mode])
    r.set_defaults(fn=cmd_run)
    wd = sub.add_parser("watchdog", help="24시간 스케줄러 감시: 죽거나 멈추면(심장박동 5분) 다시 띄움")
    wd.add_argument("--mode", default="paper", choices=[m.value for m in Mode])
    wd.set_defaults(fn=cmd_watchdog)
    sub.add_parser("review").set_defaults(fn=cmd_review)
    k = sub.add_parser("kill")
    k.add_argument("state", choices=["on", "off"])
    k.set_defaults(fn=cmd_kill)
    kc = sub.add_parser("kis-check", help="한국투자증권 API 연결 점검 (기본: 주문 없음)")
    kc.add_argument("--symbol", default="005930")
    kc.add_argument("--test-order", action="store_true", help="모의투자 전용: 1주 비체결 주문 후 즉시 취소")
    kc.add_argument("--suite", action="store_true", help="검증 스위트 8단계 실행 · 결과 저장 (Readiness BROKER 관문)")
    kc.add_argument("--fill", action="store_true", help="--suite 와 함께: 모의투자에서 1주 실제 체결 → 되팔기 (슬리피지 실측)")
    kc.add_argument("--e2e", action="store_true", help="--suite 와 함께: 모의투자 장중 · 이 시스템의 주문 경로 그대로 주문→체결→장부→잔고 대조→중복 방지→되팔기→재시작 복구")
    kc.set_defaults(fn=cmd_kis_check)
    ps = sub.add_parser("power-study", help="실제 KRX 데이터로 코어 점수의 예측력 사후 검증 (IC · 분위 · 비용 후 초과)")
    ps.add_argument("--marcap-dir", required=True)
    ps.add_argument("--start", type=int, default=2010)
    ps.add_argument("--top", type=int, default=100)
    ps.set_defaults(fn=cmd_power_study)
    rd = sub.add_parser("readiness", help="매매 준비 점검 7관문 (DATA·BROKER·MODEL·RISK·EVENT·DRIFT·CALIBRATION)")
    rd.add_argument("--mode", default=None)
    rd.set_defaults(fn=cmd_readiness)
    sub.add_parser("health").set_defaults(fn=cmd_health)
    sub.add_parser("db-ping", help="DB 연결·데이터 유무 확인 (run.sh 용)").set_defaults(fn=cmd_db_ping)
    sub.add_parser("ops-status", help="운영 상태 (24시간 운영 · 데이터 날짜 · 뉴스 · 작업 실패)").set_defaults(fn=cmd_ops_status)
    pp = sub.add_parser("proof-project", help="증명 프로젝트 (100만원 실계좌 · 규칙·성공 기준 봉인 · 매일 봉인 기록 · 공개 페이지)")
    pp.add_argument("action", nargs="?", default="status", choices=["status", "start", "record", "end"])
    pp.add_argument("--mode", default="live", choices=["live", "paper", "shadow"])
    pp.add_argument("--principal", type=float, default=None)
    pp.add_argument("--max-loss", type=float, default=None)
    pp.add_argument("--public", action="store_true", help="공개 페이지(/proof)를 로그인 없이 열기")
    pp.add_argument("--reason", default="")
    pp.set_defaults(fn=cmd_proof_project)
    dc = sub.add_parser("datacheck", help="데이터 정합성 점검 (일봉 최신성 · 자동 갱신 · 수정주가 · 52주 · 시가총액 · 외부 시세 대조)")
    dc.add_argument("--offline", action="store_true", help="외부 시세 대조 없이")
    dc.add_argument("--limit", type=int, default=20)
    dc.set_defaults(fn=cmd_datacheck)
    cm = sub.add_parser("community", help="커뮤니티(종목토론실·StockTwits) 수집 점검")
    cm.add_argument("--test", help="점검할 종목 (쉼표, 기본 005930,NVDA)")
    cm.add_argument("--collect", action="store_true", help="관심·보유 종목 지금 수집")
    cm.set_defaults(fn=cmd_community)
    lg = sub.add_parser("logos", help="종목 로고 미리 받기 (관심·보유·주요 종목)")
    lg.add_argument("--symbols", help="쉼표로 구분한 종목 (생략하면 관심·보유·주요 종목)")
    lg.add_argument("--top", type=int, default=100, help="주요 종목 몇 개까지 (기본 100)")
    lg.add_argument("--retry", action="store_true", help="이전 실패 기억을 무시하고 다시 받기")
    lg.add_argument("--all", action="store_true", help="DB 의 모든 상장 종목 + 해외 목록 (처음 한 번 수십 분 · 이미 받은 것은 건너뜀)")
    lg.set_defaults(fn=cmd_logos)
    od = sub.add_parser("orders", help="리밸런싱 주문표 (다른 증권사·ISA·수동 매매용, 주문은 내지 않음)")
    od.add_argument("--cash", type=float, required=True, help="주문 가능 현금 (원)")
    od.add_argument("--holdings", help="보유 종목 CSV (종목코드,수량). '-' 는 표준입력. 없으면 전액 현금에서 시작")
    od.add_argument("--no-ai", action="store_true", help="AI 거부권·위성 없이 코어 팩터만 (LLM 비용 0)")
    od.add_argument("--out", help="CSV 저장 경로")
    od.set_defaults(fn=cmd_orders)
    dr = sub.add_parser("doctor", help="실행 전 점검 (설정·키·DB·데이터·AI·증권사·알림)")
    dr.add_argument("--ai", action="store_true", help="각 AI 에 짧은 테스트 요청 (무료 한도 1회씩 사용)")
    dr.add_argument("--kis", action="store_true", help="KIS 토큰·잔고 조회 (주문 없음)")
    dr.add_argument("--notify", action="store_true", help="알림 테스트 메시지 전송")
    dr.set_defaults(fn=cmd_doctor)
    cf = sub.add_parser("cashflow", help="입금(+)/출금(-) 기록 — 건강검진 수익률 보정")
    cf.add_argument("amount", type=float, nargs="?", help="원 단위. 생략하면 목록만")
    cf.add_argument("--mode", default="live")
    cf.add_argument("--date", help="YYYY-MM-DD (기본 오늘)")
    cf.add_argument("--memo")
    cf.set_defaults(fn=cmd_cashflow)
    ck = sub.add_parser("checkup", help="전략 건강검진 (성과가 과거 검증 범위 안인지)")
    ck.add_argument("--mode", default=None, help="paper / live / attr-core 등 장부 (기본: 현재 모드)")
    ck.add_argument("--no-ic", action="store_true", help="팩터 IC 계산 생략 (빠름)")
    ck.set_defaults(fn=cmd_checkup)
    cy = sub.add_parser("cycle", help="코어-위성 한 사이클 실행 (팩터 코어 + 멀티 AI)")
    cy.add_argument("--mode", default="paper", choices=["paper", "shadow", "live"])
    cy.add_argument("--core-only", action="store_true", help="AI 오버레이 없이 팩터 코어만 (QUANT_CORE_ONLY=true 와 같음)")
    cy.set_defaults(fn=cmd_cycle)
    wu = sub.add_parser("warmup", help="빈 화면 채우기 (뉴스·공시·거시 수집 + AI 판단 1회 + 자동 감시)")
    wu.set_defaults(fn=cmd_warmup)
    gd = sub.add_parser("guardian", help="자동 킬스위치 10개 조건 점검")
    gd.add_argument("--mode", default=None)
    gd.add_argument("--act", action="store_true", help="critical 이면 HALTED + 모델 롤백 실행")
    gd.set_defaults(fn=cmd_guardian)
    na = sub.add_parser("net-alpha", help="성과 검증 5단계 + 수익 분해")
    na.add_argument("--mode", default=None)
    na.add_argument("--market", default="KR", help="KR(국내) / US(미국 가상 장부)")
    na.set_defaults(fn=cmd_net_alpha)
    us = sub.add_parser("us", help="미국 코어 + AI 장부 (가상매매): 일봉 받기 + 한 사이클")
    us.add_argument("--force", action="store_true", help="같은 일봉이어도 다시 실행")
    us.set_defaults(fn=cmd_us)
    dc = sub.add_parser("db-clean", help="DB 정리 (기본 미리보기, --yes 로 실행)")
    dc.add_argument("--yes", action="store_true")
    dc.set_defaults(fn=cmd_db_clean)
    ch = sub.add_parser("chat", help="터미널 채팅 AI")
    ch.add_argument("message", nargs="?", default=None, help="한 번만 묻기")
    ch.set_defaults(fn=cmd_chat)
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
    try:
        args.fn(args)
    except KeyboardInterrupt:  # Ctrl+C: 트레이스백 대신 한 줄
        print("\n중단했습니다 (Ctrl+C). 이미 끝난 작업은 저장되어 있습니다.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        from sqlalchemy.exc import OperationalError
        if not isinstance(e, OperationalError):
            raise
        # 긴 트레이스백 대신 무엇을 하면 되는지 알려준다
        url = os.environ.get("DATABASE_URL", "")
        where = url.split("@")[-1] if "@" in url else url  # 비밀번호는 출력하지 않음
        print(f"\n✖ DB 에 연결할 수 없습니다 ({where})\n  {str(e.orig if hasattr(e, 'orig') else e).splitlines()[0][:200]}\n"
              "  → PostgreSQL 이 꺼져 있거나 설치되지 않았습니다. 설치 없이 쓰려면 .env 에서\n"
              "     DATABASE_URL=sqlite:///quant_ai.db   (./run.sh 은 자동으로 전환합니다)", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

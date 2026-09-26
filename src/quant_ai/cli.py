"""quant-ai 명령줄.

    quant-ai demo                     # 가상 데이터로 전체 파이프라인 + 대시보드 데이터 생성
    quant-ai serve                    # 대시보드 (http://127.0.0.1:8050)
    quant-ai collect prices --source yahoo --symbols 005930.KS,NVDA --years 5
    quant-ai train                    # walk-forward 백테스트 → 후보 모델 등록/게이트
    quant-ai decide                   # 연구/예측 모드: 멀티 AI 합의 신호 (주문 없음)
    quant-ai run --mode paper         # 24시간 스케줄러 (장중/장외 작업)
    quant-ai review                   # 오늘 복기
    quant-ai kill on|off              # 킬스위치 (신규 매수 즉시 중단)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

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
    now = datetime.now(timezone.utc)
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
        champ = app.registry.champion()
        app.settings.assert_live_allowed(champ is not None and champ.shadow_metrics is not None)
    build_default_scheduler(app, mode).run_forever()


def cmd_review(args):
    r = _app(args).review()
    print(json.dumps({"summary": r.summary, "lessons": r.lessons}, ensure_ascii=False, indent=1, default=str))


def cmd_kill(args):
    _app(args).set_kill_switch(args.state == "on")
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
        v = v.strip().strip('"').strip("'")
        if v:
            os.environ.setdefault(k.strip(), v)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="quant-ai")
    p.add_argument("--db", help="DATABASE_URL 덮어쓰기")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo"); d.add_argument("--days", type=int, default=60); d.set_defaults(fn=cmd_demo)
    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8050)
    s.set_defaults(fn=cmd_serve)
    c = sub.add_parser("collect")
    c.add_argument("what", choices=["prices", "news", "disclosures", "macro"])
    c.add_argument("--source", default="yahoo", choices=["yahoo", "synthetic"])
    c.add_argument("--symbols", default="")
    c.add_argument("--years", type=int, default=5)
    c.add_argument("--days", type=int, default=7)
    c.set_defaults(fn=cmd_collect)
    sub.add_parser("train").set_defaults(fn=cmd_train)
    sub.add_parser("decide").set_defaults(fn=cmd_decide)
    r = sub.add_parser("run"); r.add_argument("--mode", default="paper", choices=[m.value for m in Mode])
    r.set_defaults(fn=cmd_run)
    sub.add_parser("review").set_defaults(fn=cmd_review)
    k = sub.add_parser("kill"); k.add_argument("state", choices=["on", "off"]); k.set_defaults(fn=cmd_kill)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

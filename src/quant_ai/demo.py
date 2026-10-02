"""오프라인 데모: 가상 데이터로 전체 파이프라인을 한 번에 돌려 대시보드를 채운다.

⚠ 모든 가격·뉴스·지표는 SyntheticPriceSource 로 만든 가짜 데이터다 (UI 에 DEMO 표시).
실제 데이터는 `quant-ai collect` 로 수집한다.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from .config import Mode
from .data.collectors.prices import SyntheticPriceSource
from .data.db import session_scope
from .data.models import Instrument, MacroObservation, NewsArticle
from .engines.news_intel import NewsAnalyzer
from .ops import record_job
from .pipeline import BENCHMARK_MARKET, QuantAI
from .trading.broker import MarketQuote

log = logging.getLogger("quant_ai.demo")

UNIVERSE = [
    ("005930", "KRX", "삼성전자", "KRW", "반도체", ["삼성전자", "삼성"]),
    ("000660", "KRX", "SK하이닉스", "KRW", "반도체", ["SK하이닉스", "하이닉스"]),
    ("035420", "KRX", "NAVER", "KRW", "인터넷", ["네이버", "NAVER"]),
    ("035720", "KRX", "카카오", "KRW", "인터넷", ["카카오"]),
    ("373220", "KRX", "LG에너지솔루션", "KRW", "2차전지", ["LG에너지솔루션", "LG엔솔"]),
    ("NVDA", "US", "엔비디아", "USD", "반도체", ["NVIDIA", "엔비디아"]),
    ("AAPL", "US", "애플", "USD", "IT", ["Apple", "애플"]),
    ("TSLA", "US", "테슬라", "USD", "자동차", ["Tesla", "테슬라"]),
    ("MSFT", "US", "마이크로소프트", "USD", "IT", ["Microsoft", "마이크로소프트"]),
]
INDICES = [("KOSPI", "코스피"), ("KOSDAQ", "코스닥"), ("SPX", "S&P 500"), ("NDX", "나스닥 100")]
SCALE = {"005930": 7.0, "000660": 19.0, "035420": 17.0, "035720": 4.2, "373220": 38.0,
         "NVDA": 0.012, "AAPL": 0.022, "TSLA": 0.025, "MSFT": 0.04,
         "KOSPI": 0.32, "KOSDAQ": 0.085, "SPX": 0.57, "NDX": 1.9}

POS = ["{n}, 3분기 실적 컨센서스 상회 전망", "{n}, 대규모 공급계약 수주", "{n} 자사주 매입 결정",
       "{n}, 신제품 효과로 목표가 상향", "{n} 외국인 순매수 확대, 신고가 경신"]
NEG = ["{n}, 실적 부진 우려에 하락", "{n} 목표가 하향… 수요 둔화", "{n}, 경쟁 심화로 급락",
       "{n} 관련 소송 리스크 부각", "{n} 유상증자 결정에 투자심리 위축"]
NEU = ["{n}, 주주총회 개최", "{n} 신임 임원 선임", "{n} 사업부 조직 개편"]


def seed(app: QuantAI, years: int = 5, seed_value: int = 11) -> None:
    rng = np.random.default_rng(seed_value)
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=365 * years)
    src = SyntheticPriceSource(seed=seed_value, momentum=0.2)  # 데모용: 학습 가능한 모멘텀을 강하게

    with session_scope(app.engine) as s:
        have = {i.symbol for i in s.scalars(select(Instrument))}
        for sym, mkt, name, ccy, sector, kws in UNIVERSE:
            if sym not in have:
                s.add(Instrument(symbol=sym, market=mkt, name=name, currency=ccy, sector=sector, keywords=kws))
        for sym, name in INDICES:
            if sym not in have:
                s.add(Instrument(symbol=sym, market=BENCHMARK_MARKET, name=name, currency="", sector="index"))

    class ScaledDemoSource:  # 종목별 가격 수준만 현실적으로 맞춘 가상 시세
        name = "synthetic-demo"

        def fetch_bars(self, sym, start, end, interval="1d"):
            bars = src.fetch_bars(sym, start, end)
            bars[["open", "high", "low", "close"]] *= SCALE.get(sym, 1.0)
            return bars

    # 실제 수집과 같은 경로 (품질검사 → 저장)
    app.ingest_prices(ScaledDemoSource(), [u[0] for u in UNIVERSE] + [i[0] for i in INDICES], start, end)

    # 가짜 뉴스: 이후 5일 수익률과 약하게 상관 (데모에서 뉴스 AI 가 '무언가'를 배울 수 있게)
    analyzer = NewsAnalyzer()
    bars_all, _, _ = app.market_data()
    with session_scope(app.engine) as s:
        for sym, _, name, *_ in UNIVERSE:
            b = bars_all[sym]
            fwd = b["close"].shift(-5) / b["close"] - 1
            days = b.index[-500:]
            for ts in days[rng.random(len(days)) < 0.25]:
                f = fwd.get(ts)
                p_pos = 0.5 if pd.isna(f) else 0.5 + 0.25 * np.tanh(f * 15)
                tmpl = rng.choice(POS if rng.random() < p_pos else NEG) if rng.random() < 0.8 else rng.choice(NEU)
                title = tmpl.format(n=name)
                a = analyzer.analyze(title)
                pub = ts.to_pydatetime() - timedelta(hours=int(rng.integers(1, 20)))
                s.add(NewsArticle(source="DEMO", url=f"demo://{sym}/{ts.date()}/{rng.integers(1e9)}",
                                  published_at=pub, title=title, body="", symbols=[sym],
                                  sentiment=a.sentiment, events=a.events, importance=a.importance))
        # 가짜 거시지표
        mdays = pd.bdate_range(end - timedelta(days=400), end)
        for sid, base, vol in [("VIXCLS", 16, 0.05), ("DGS10", 4.2, 0.01), ("DEXKOUS", 1360, 0.004),
                               ("DCOILWTICO", 72, 0.02), ("DGS2", 3.9, 0.01)]:
            path = base * np.exp(np.cumsum(rng.normal(0, vol, len(mdays))))
            for d, v in zip(mdays, path):
                s.add(MacroObservation(series_id=sid, source="DEMO", ts=d.date(), value=float(v)))

    events = [{"ts": (end + timedelta(days=2, hours=18)).isoformat(), "name": "FOMC 금리 결정",
               "importance": 0.9, "symbol": None},
              {"ts": (end + timedelta(hours=20)).isoformat(), "name": "테슬라 실적 발표",
               "importance": 0.8, "symbol": "TSLA"}]
    Path(app.settings.artifacts_dir).mkdir(parents=True, exist_ok=True)
    (Path(app.settings.artifacts_dir) / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=1),
                                                                  encoding="utf-8")


def run_demo(app: QuantAI, replay_days: int = 60, verbose: bool = True, years: int = 5) -> dict:
    say = print if verbose else (lambda *a, **k: None)
    say("① 가상 데이터 생성 (DEMO)")
    seed(app, years=years)
    bars, bench, sent = app.market_data()
    dates = next(iter(bars.values())).index
    cutoff = dates[-replay_days - 1]

    say(f"② walk-forward 백테스트 + 후보 모델 등록 (학습 데이터: ~{cutoff.date()})")
    cut = {k: v[v.index <= cutoff] for k, v in bars.items()}
    with record_job(app.engine, "retrain_candidate"):
        rec, result, gate = app.train_candidate(cut, bench[bench.index <= cutoff],
                                                sent[sent.index <= cutoff] if sent is not None else None)
    m = result.metrics
    say(f"   전략 수익 {m['strategy']['total_return']:+.1%} / 벤치마크 {m['benchmark']['total_return']:+.1%}, "
        f"Sharpe {m['strategy']['sharpe']:.2f}, MDD {m['strategy']['max_drawdown']:.1%}, "
        f"OOS 정확도 {m['prediction']['accuracy']:.1%}")
    say(f"   게이트: {'통과 → shadow' if gate.passed else '탈락: ' + '; '.join(gate.failures)}")

    say(f"③ 최근 {replay_days}거래일 재생: 멀티 AI 판단 → 앙상블 → Paper/Shadow 매매 → 채점")
    replay = dates[-replay_days:]
    for i, t in enumerate(replay):
        quotes = {sym: MarketQuote(last=float(b.loc[t, "close"]), bid=float(b.loc[t, "close"]) * 0.9995,
                                   ask=float(b.loc[t, "close"]) * 1.0005, bid_qty=1e6, ask_qty=1e6)
                  for sym, b in bars.items() if t in b.index}
        ts = t.to_pydatetime() + timedelta(hours=6, minutes=20)  # 장 마감 무렵
        with record_job(app.engine, "decide_and_trade"):
            decisions = app.decide(as_of=t.to_pydatetime(), scenarios=(i == len(replay) - 1))
            app.trade(decisions, Mode.PAPER, ts=ts, quotes=dict(quotes))
            app.trade(decisions, Mode.SHADOW, ts=ts, quotes=dict(quotes))
        if i % 10 == 9 or i == len(replay) - 1:
            with record_job(app.engine, "review"):
                report = app.review(t.date())
            say(f"   {t.date()} 복기: 채점 {report.summary.get('n_resolved', 0)}건, 교훈 {len(report.lessons or [])}개")
    with record_job(app.engine, "shadow_eval"):
        results = app.evaluate_shadow_models()
    for mid, g in results:
        say(f"④ Shadow 평가 모델#{mid}: {'champion 승격' if g.passed else '보류/탈락: ' + '; '.join(g.failures)}")
    return m

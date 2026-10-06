"""QuantAI 오케스트레이터: 데이터 → 컨텍스트 → 멀티 AI → 앙상블 → 리스크 → 실행 → 기록 → 복기.

    Market Data ─┬─▶ Primary AI (종합)      ─┐
                 ├─▶ NVIDIA AI (독립 검증)   ─┤
                 ├─▶ Quant Model (숫자)     ─┼─▶ Ensemble ─▶ Risk Gate ─▶ Execution ─▶ Broker
                 ├─▶ Regime                 ─┤   (충돌탐지·성적가중)
                 └─▶ Risk AI (반대 논거/veto)─┘
                 └─▶ Challenger 모델 (조용히 채점만, 앙상블 미참여)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from . import ops
from .analysts.analysts import QuantAnalyst, backend_id, build_analysts
from .analysts.base import MarketContext, Opinion
from .analysts.context import build_context, macro_snapshot
from .analysts.llm_clients import HashingEmbeddings, NvidiaEmbeddings
from .analysts.memory import Memory
from .backtest.backtester import BacktestConfig, Backtester, performance
from .backtest.stats import deflated_sharpe
from .config import Mode, Settings
from .data.db import init_db, load_bars, make_engine, recent_adv, session_scope, upsert_bars
from .data.models import (
    AnalystOpinionRecord,
    ConsensusRecord,
    Instrument,
    ModelRecord,
    NewsArticle,
    OrderRecord,
    PortfolioSnapshot,
    PriceBar,
    Scenario,
)
from .data.quality import validate_bars
from .engines.features import build_dataset, feature_columns
from .engines.market_intel import FACTORS, cross_asset, load_macro, market_state
from .engines.news_intel import daily_sentiment
from .engines.prediction import Prediction, Predictor, direction_of
from .engines.regime import EXPOSURE_MULTIPLIER, Regime, RegimeState, equal_weight_index, regime_series
from .engines.scenario import build_scenarios
from .ensemble.calibration import calibrators_as_of, fit_calibrators
from .ensemble.engine import ConsensusSignal, EnsembleEngine, records_for
from .ensemble.tracker import evidence_snapshot, resolve, save_consensus, scoreboard
from .registry.model_registry import ModelRegistry
from .review.review import daily_review
from .strategy.core_satellite import CoreSatelliteConfig, Plan, build_plan, core_scores
from .trading.broker import LiveBroker, MarketQuote, PaperBroker, ShadowBroker
from .trading.execution import ExecutionEngine, Signal
from .trading.journal import DBJournal
from .trading.portfolio import CostModel, Portfolio, Position
from .trading.risk import RiskEngine

log = logging.getLogger("quant_ai")

GLOBAL_MARKET = "GLOBAL"
BENCHMARK_MARKET = "INDEX"  # instruments.market == INDEX 인 종목은 지수 (매매 대상 아님)


@dataclass
class Decision:
    symbol: str
    as_of: datetime
    signal: ConsensusSignal
    opinions: list[Opinion]
    context: MarketContext
    consensus_id: int | None = None


class QuantAI:
    def __init__(self, settings: Settings | None = None, horizon: int = 5):
        self.settings = settings or Settings.from_env()
        self.engine = make_engine(self.settings.database_url)
        init_db(self.engine)
        self.horizon = horizon
        self.registry = ModelRegistry(self.engine, self.settings.artifacts_dir)
        embedder = (NvidiaEmbeddings(self.settings.nvidia_api_key, self.settings.nvidia_embed_model,
                                     self.settings.nvidia_base_url)
                    if self.settings.nvidia_api_key else HashingEmbeddings())
        self.memory = Memory(embedder)
        self.ensemble = EnsembleEngine()
        self.notifier = ops.Notifier.from_env()
        # 알림 라우팅: 사이트 알림 중 일부 종류를 텔레그램·디스코드와 웹 푸시로도 (QUANT_NOTIFY_KINDS · QUANT_PUSH_KINDS)
        import os as _os

        from . import alerts as _alerts
        from .webpush import sender as _push_sender
        kinds = _os.environ.get("QUANT_NOTIFY_KINDS")
        pkinds = _os.environ.get("QUANT_PUSH_KINDS")
        _alerts.configure(self.notifier, {k.strip() for k in kinds.split(",") if k.strip()} if kinds else None,
                          _push_sender(self.engine, self.settings.artifacts_dir),
                          {k.strip() for k in pkinds.split(",") if k.strip()} if pkinds else None)
        try:  # 사용자가 정한 원금·최대 손실에서 계산한 한도 (모든 프로세스가 같은 값)
            from .budget import apply as _budget_apply
            _budget_apply(self)
        except Exception as e:  # noqa: BLE001 - 한도 저장값이 깨져도 기본(.env) 한도로 동작
            log.warning("투자 한도 적용 실패: %s", e)

    # ================================================================ 데이터
    def symbols(self, include_index: bool = False) -> list[str]:
        with session_scope(self.engine) as s:
            q = select(Instrument)
            rows = s.scalars(q).all()
            # 해외 관심 종목(GLOBAL)은 조회·채팅용 캐시일 뿐 국내 전략 유니버스가 아니다
            syms = [i.symbol for i in rows if i.market != GLOBAL_MARKET and (include_index or i.market != BENCHMARK_MARKET)]
        if self.settings.watchlist:
            syms = [x for x in syms if x in self.settings.watchlist] or list(self.settings.watchlist)
        return syms

    def index_symbols(self) -> list[str]:
        with session_scope(self.engine) as s:
            return [i.symbol for i in s.scalars(select(Instrument).where(Instrument.market == BENCHMARK_MARKET))]

    def ingest_prices(self, source, symbols: list[str], start, end, interval: str = "1d") -> int:
        """수집 → 품질검사(문제 행 제거·경고) → 저장. 품질 리포트는 대시보드에 표시된다."""
        self._md_cache = None  # 수정주가 재계산은 행 수가 같아도 값이 바뀐다
        n = 0
        reports = ops.get_state(self.engine, "data_quality")
        for sym in symbols:
            bars = source.fetch_bars(sym, start, end, interval)
            bars, rep = validate_bars(bars, sym)
            reports[sym] = {"rows_in": rep.rows_in, "rows_out": rep.rows_out, "dropped": rep.dropped,
                            "warnings": rep.warnings[-10:], "checked_at": datetime.now(UTC).isoformat()}
            if rep.dropped:
                self.notifier.send(f"데이터 품질: {sym} {rep.dropped}", "warn")
            with session_scope(self.engine) as s:
                n += upsert_bars(s, sym, bars, interval, source.name)
        ops.set_state(self.engine, "data_quality", reports)
        return n

    def ingest_krx(self, marcap_dir, years: int = 3, top_n: int = 100, end_year: int | None = None) -> dict:
        """실제 KRX 데이터 적재: 최근 N년 동안 시총 상위 N 에 들었던 종목(상장폐지 포함) + 월별 유니버스."""
        from .data.collectors.marcap import build_krx_dataset
        self._md_cache = None

        end_year = end_year or datetime.now(UTC).year
        ds = build_krx_dataset(marcap_dir, end_year - years, end_year, top_n)
        with session_scope(self.engine) as s:
            have = {i.symbol for i in s.scalars(select(Instrument))}
            for code in ds.bars:
                if code not in have:
                    s.add(Instrument(symbol=code, market="KRX", name=ds.names.get(code, code), currency="KRW"))
            if "KOSPI" not in have:
                s.add(Instrument(symbol="KOSPI", market=BENCHMARK_MARKET, name="코스피(시총가중 대용)", currency=""))
            for k in ds.extra_indices:  # 화면용 (벤치마크는 항상 KOSPI)
                if k not in have:
                    s.add(Instrument(symbol=k, market=BENCHMARK_MARKET, name={"KOSDAQ": "코스닥"}.get(k, k) + "(시총가중 대용)",
                                     currency=""))
        idx = {"KOSPI": ds.benchmark, **ds.extra_indices}

        class _Src:
            name = "marcap"

            def fetch_bars(self, sym, start, end, interval="1d"):
                b = idx[sym] if sym in idx else ds.bars[sym]
                return b[["open", "high", "low", "close", "volume"]]

        n = self.ingest_prices(_Src(), [*ds.bars, *idx], None, None)
        months = ds.eligible.index.tz_convert(None).to_period("M")
        universe = {}
        for m in sorted(set(months)):
            row = ds.eligible[months == m].iloc[0]
            codes = sorted(row[row].index)
            if codes:
                universe[str(m)] = codes
        ops.set_state(self.engine, "krx_universe", universe)
        if ds.facts:  # v29: 시가총액 · 상장주식수 · 수정주가 이벤트 (종목 화면 시가총액 · 데이터 점검)
            ops.set_state(self.engine, "krx_facts", {"at": datetime.now(UTC).isoformat(), "symbols": ds.facts})
        return {"bars": n, "symbols": len(ds.bars), "months": len(universe),
                "last_date": str(ds.eligible.index.max().date())}

    def universe_at(self, ts: datetime) -> list[str] | None:
        """그 시점 매매 대상 (전월 말 시총 상위 N). 없으면 None → 전 종목."""
        uni = ops.get_state(self.engine, "krx_universe")
        if not uni:
            return None
        key = pd.Timestamp(ts).tz_localize(None).to_period("M") if pd.Timestamp(ts).tz is None \
            else pd.Timestamp(ts).tz_convert(None).to_period("M")
        months = sorted(k for k in uni if k <= str(key))
        return uni[months[-1]] if months else None

    def market_data(self, as_of: datetime | None = None):
        """(bars, bench, sentiment). 실시간(as_of 없음) 호출은 DB 가 바뀌지 않았으면 60초 동안 재사용
        (대시보드·감시·분석이 동시에 전 종목 일봉을 다시 읽지 않도록). 재생(as_of)은 항상 새로 읽는다."""
        if as_of is None:
            import time as _time
            with session_scope(self.engine) as s:
                key = (s.scalar(select(func.max(PriceBar.id))), s.scalar(select(func.max(NewsArticle.id))),
                       tuple(self.symbols()))
            hit = getattr(self, "_md_cache", None)
            if hit and hit[1] == key and _time.monotonic() - hit[0] < 60:
                bars, bench, sent = hit[2]
                return dict(bars), bench, sent
            import threading
            lock = self.__dict__.setdefault("_md_lock", threading.Lock())
            with lock:  # 화면이 동시에 여러 API 를 부르면 전 종목 일봉을 한 번만 읽는다 (나머지는 기다렸다 재사용)
                hit = getattr(self, "_md_cache", None)
                if hit and hit[1] == key and _time.monotonic() - hit[0] < 60:
                    bars, bench, sent = hit[2]
                    return dict(bars), bench, sent
                out = self._market_data(None)
                self._md_cache = (_time.monotonic(), key, out)
            return dict(out[0]), out[1], out[2]
        return self._market_data(as_of)

    def _market_data(self, as_of: datetime | None = None):
        syms = self.symbols()
        with session_scope(self.engine) as s:
            bars = load_bars(s, syms)
            idx = load_bars(s, self.index_symbols())
            news = pd.DataFrame([
                {"published_at": n.published_at, "symbol": sym, "sentiment": n.sentiment or 0.0,
                 "importance": n.importance or 0.5, "title": n.title}
                for n in s.scalars(select(NewsArticle)) for sym in (n.symbols or [])
            ])
        if as_of is not None:
            ts = pd.Timestamp(as_of)
            bars = {k: v[v.index <= ts] for k, v in bars.items()}
            idx = {k: v[v.index <= ts] for k, v in idx.items()}
            if not news.empty:
                news = news[pd.to_datetime(news["published_at"], utc=True) <= ts]
        bench = idx.get("KOSPI", next(iter(idx.values()))) if idx else equal_weight_index(bars)
        sentiment = daily_sentiment(news) if not news.empty else None
        return bars, bench, sentiment

    # ============================================================ 모델 수명주기
    def train_candidate(self, bars=None, bench=None, sentiment=None, config: BacktestConfig | None = None,
                        name: str | None = None, reason: str = "정기 재학습"):
        """walk-forward 백테스트로 검증 → 레지스트리 등록 → 백테스트 게이트 평가 (통과 시 shadow)."""
        if bars is None:
            bars, bench, sentiment = self.market_data()
        cfg = config or BacktestConfig(horizon=self.horizon, risk=self.settings.risk, costs=self.settings.costs,
                                       initial_cash=self.settings.initial_cash)
        from .backtest.backtester import effective_config
        cfg = effective_config(cfg, bars)  # 종목이 20개 미만이면 순위 모델 대신 '오를까' 모델
        name = name or f"quant-{cfg.model_kind}"
        result = Backtester(cfg).run(bars, bench, sentiment)
        if result.model is None:
            raise RuntimeError("학습 가능한 데이터가 부족합니다")
        # 강건성: 비용 2배 스트레스 + 다중검정 보정(DSR)
        stress_costs = replace(cfg.costs, commission_bps=cfg.costs.commission_bps * 2,
                               slippage_bps=cfg.costs.slippage_bps * 2)
        stress = Backtester(replace(cfg, costs=stress_costs)).run(bars, bench, sentiment)
        result.metrics["stress"] = {"costs_x": 2, **stress.metrics["strategy"]}
        n_trials = self.registry.n_trials() + 1
        result.metrics["n_trials"] = n_trials
        result.metrics["dsr"] = deflated_sharpe(result.metrics.pop("daily_returns"), n_trials)
        # 최종 모델은 전체(라벨 확정) 데이터로 재학습
        data = build_dataset(bars, cfg.horizon, regime=regime_series(bench)["score"], sentiment=sentiment, target=cfg.target)
        train = data.dropna(subset=["label"])
        model = Predictor(cfg.model_kind, cfg.horizon, target=cfg.target).fit(train[feature_columns(data, cfg.target)], train["label"])
        rec = self.registry.register_candidate(model, name, result.metrics, {
            "horizon": cfg.horizon, "model_kind": cfg.model_kind, "train_window": cfg.train_window, "target": cfg.target,
            "min_prob": cfg.min_prob, "train_end": str(train.index.get_level_values(0).max()),
            "embargo": cfg.embargo, "reason": reason,  # 계보: 왜 · 무엇에서 · 어떤 데이터로 만들었나
            "parent": (lambda c: f"{c.name}@{c.version}" if c else None)(self.registry.champion()),
            "train_start": str(train.index.get_level_values(0).min()), "n_train": int(len(train)),
        })
        gate = self.registry.evaluate_candidate(rec.id)
        return rec, result, gate

    def _load_model(self, status: str) -> tuple[ModelRecord | None, Predictor | None]:
        with session_scope(self.engine) as s:
            rec = s.scalar(select(ModelRecord).where(ModelRecord.status == status)
                           .order_by(ModelRecord.created_at.desc()))
        if rec is None:
            return None, None
        try:
            return rec, Predictor.load(Path(rec.artifact_path))
        except Exception as e:  # noqa: BLE001 - 모델 파일 손상·삭제: 그 모델만 빼고 계속 (다른 AI 는 판단)
            log.warning("%s 모델 %s 불러오기 실패 → 제외: %s", status, rec.version, e)
            key = f"model_load_error:{rec.version}"
            if not ops.get_state(self.engine, key):
                ops.set_state(self.engine, key, {"at": datetime.now(UTC).isoformat(), "error": str(e)[:300]})
                self.notifier.send(f"{status} 모델 {rec.version} 파일을 불러오지 못해 제외했습니다: {e}", "warn")
            return None, None

    def active_model(self) -> tuple[ModelRecord | None, Predictor | None]:
        """champion 우선. 없으면 연구/Paper 용으로 shadow → candidate 순 (Live 는 champion 만 허용)."""
        for status in ("champion", "shadow", "candidate"):
            rec, model = self._load_model(status)
            if model is not None:
                return rec, model
        return None, None

    def evaluate_shadow_models(self) -> list[tuple[int, object]]:
        """shadow 상태 모델의 조용한(challenger) 성적으로 champion 승격 여부 판단."""
        out = []
        with session_scope(self.engine) as s:
            shadows = s.scalars(select(ModelRecord).where(ModelRecord.status == "shadow")).all()
        for rec in shadows:
            with session_scope(self.engine) as s:
                ops = s.scalars(select(AnalystOpinionRecord).where(
                    AnalystOpinionRecord.analyst.in_(("quant", "challenger")),
                    AnalystOpinionRecord.category == "direction",
                    AnalystOpinionRecord.correct.is_not(None))).all()
                ops = [o for o in ops if (o.payload or {}).get("model_version") == rec.version]
                snaps = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == "shadow")
                                  .order_by(PortfolioSnapshot.ts, PortfolioSnapshot.id)).all()
            days = len({o.as_of.date() for o in ops})
            acc = sum(o.correct for o in ops) / len(ops) if ops else 0.0
            eq = pd.Series([x.equity for x in snaps], dtype=float)
            mdd = float((eq / eq.cummax() - 1).min()) if len(eq) else 0.0
            metrics = {"days": days, "accuracy": acc, "n": len(ops), "max_drawdown": mdd}
            out.append((rec.id, self.registry.evaluate_shadow(rec.id, metrics)))
        return out

    # ================================================================ 판단
    def load_events(self) -> list[dict]:
        path = Path(self.settings.artifacts_dir) / "events.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def decide(self, as_of: datetime | None = None, symbols: list[str] | None = None,
               persist: bool = True, scenarios: bool = True, market: str = "KR") -> list[Decision]:
        """연구/예측 모드: 모든 AI 의 독립 의견 → 합의 신호 (주문은 내지 않음). market="US" 면 미국 유니버스."""
        if market == "US":
            from . import global_market
            bars, bench, sentiment = global_market.market_data(self, as_of, extra=symbols)
        else:
            bars, bench, sentiment = self.market_data(as_of)
        if not bars:
            return []
        reg = regime_series(bench)
        data = build_dataset(bars, self.horizon, regime=reg["score"], sentiment=sentiment)  # cs_*(순위) 도 함께 — 종목 선택 모델용
        feats = [c for c in data.columns if c not in ("fwd_ret", "label")]
        model_rec, model = self.active_model()
        challenger_rec, challenger = self._load_model("shadow")
        if model_rec is not None and model_rec.status == "shadow":
            challenger_rec, challenger = None, None  # shadow 가 이미 메인으로 쓰이는 중
        analysts = build_analysts(self.settings, model, model_rec.version if model_rec else None, self.engine)
        from .analysts.analysts import analyst_versions
        versions = analyst_versions(analysts, model_rec.version if model_rec else None)
        if as_of is not None:
            versions["replay"] = True  # 과거 재생 예측 (저장 시각이 기준 시각보다 늦는 게 정상)
        events = self.load_events()

        decisions: list[Decision] = []
        # 1) 재료 준비 (DB 읽기) — 종목별 맥락을 먼저 모두 만든다
        with session_scope(self.engine) as s:
            macro = macro_snapshot(s, as_of or datetime.now(UTC))
            # 백엔드가 바뀌면(예: 휴리스틱 → Claude) 이전 성적을 물려받지 않는다
            backends = {a.name: backend_id(a.client) for a in analysts if getattr(a, "client", None) is not None}
            # Point-in-Time: 과거 재생(as_of)이면 그 시점에 알 수 있던 성적·보정기만 쓴다 (미래 누수 방지)
            board = records_for(scoreboard(s, backends={k: v for k, v in backends.items() if v},
                                           until=pd.Timestamp(as_of).to_pydatetime() if as_of is not None else None))
            calibrators = calibrators_as_of(ops.get_state(self.engine, "calibration_history"),
                                            ops.get_state(self.engine, "calibration"), as_of)
            macro_series = load_macro(s, [k for k in FACTORS if k != "KOSPI"], as_of=as_of or datetime.now(UTC))
            factors = {**macro_series, **({("KOSPI" if market == "KR" else "SPY"): bench["close"]} if bench is not None else {})}
            mstate = market_state(bench, bars, vix=macro_series.get("VIXCLS"))
            from .agents import news_for_symbol
            from .engines.knowledge import for_context as related_for_context
            from .engines.sector import for_context as sector_for_context
            from .engines.sector import sector_map, sector_stats
            sector_mp = sector_map(self.engine) if as_of is None else {}
            sector_rows = sector_stats(bars, sector_mp, bench) if sector_mp else []
            mb, sv, nd = (ops.get_state(self.engine, k) for k in ("macro_brief", "sector_view", "news_digest"))
            agent_common = {k: v for k, v in {
                "macro": {x: mb.get(x) for x in ("view", "risk_level", "stance")} if mb.get("view") else None,
                "sectors": {x: sv.get(x) for x in ("view", "leaders", "laggards")} if sv.get("view") else None,
                "news_today": nd.get("summary")}.items() if v} if as_of is None else {}
            jobs = []
            for sym in symbols or list(bars):
                if sym not in bars or bars[sym].empty:
                    continue
                rows = data.xs(sym, level=1)
                t = rows.index[-1]  # market_data 가 as_of 이후를 이미 잘라냄
                frow = rows.loc[t, feats]
                rrow = reg.loc[t] if t in reg.index else None
                ctx = build_context(s, sym, t.to_pydatetime(), self.horizon, bars[sym], frow, rrow,
                                    memory=self.memory, events=events, macro=macro, market=mstate,
                                    cross=cross_asset(bars[sym]["close"], {k: v for k, v in factors.items()
                                                                           if k != sym}, lag_us=market == "KR"))
                if as_of is None:  # 과거 재생에는 넣지 않는다 (그때의 기록이 없으므로)
                    from .data.collectors.community import for_context
                    ctx.community = for_context(self.engine, sym)
                    ctx.sector = sector_for_context(sym, sector_mp, sector_rows)
                    ctx.related = related_for_context(self.engine, sym, bars)
                    ctx.market_agents = {**agent_common, "news_for_this_stock": news_for_symbol(self.engine, ctx.name, sym)}
                    if sym.isdigit():
                        from .data.collectors.investor_flow import for_context as flow_for_context
                        ctx.flow = flow_for_context(self.engine, sym)
                jobs.append((sym, t, frow, rrow, ctx))

        # 2) AI 의견 — 공급자마다 동시에 (공급자별 분당 한도는 클라이언트가 지킨다). 서로의 의견은 모른다.
        progress_key = f"ai_progress:{market}"
        live_llm = any(getattr(a, "client", None) is not None for a in analysts)
        workers = min(8, max(1, len(analysts))) if live_llm and len(jobs) > 0 else 1
        started = datetime.now(UTC).isoformat()
        if persist:
            ops.set_state(self.engine, progress_key, {"done": 0, "total": len(jobs), "started_at": started, "running": True})

        def opinion(analyst, ctx):
            try:
                return analyst.analyze(ctx)
            except Exception as e:  # noqa: BLE001 - 한 AI 의 예외가 판단 전체를 멈추지 않게 → 기권
                client = getattr(analyst, "client", None)
                return Opinion.abstain(analyst.name, ctx.symbol, f"{type(e).__name__}: {e}"[:300],
                                       (backend_id(client) if client is not None else "") or "")

        def batch_opinions(analyst, ctxs):
            try:
                return analyst.analyze_batch(ctxs)
            except Exception as e:  # noqa: BLE001 - 묶음이 통째로 실패하면 하나씩
                log.info("묶음 판단 실패 → 하나씩: %s", e)
                return [opinion(analyst, c) for c in ctxs]

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai") as pool:
            # 종목 × AI 자리마다 (future, 묶음 안 위치). LLM 은 공급자 설정에 따라 여러 종목을 한 요청에 묶는다
            slots: list[list] = [[None] * len(analysts) for _ in jobs]
            from .review.batch_ab import batch_size_for, is_single_arm
            for ai_i, a in enumerate(analysts):
                b = int(getattr(a, "batch_size", 1) or 1) if hasattr(a, "analyze_batch") else 1
                if b > 1:
                    b = batch_size_for(self.engine, a, b)  # A/B 결과로 조정된 묶음 크기
                if b > 1 and len(jobs) > 1:
                    # A/B: 약 20% 는 하나씩 물어 묶음 품질을 비교한다 (종목·날짜로 정해져 재현 가능)
                    single = [j for j in range(len(jobs)) if is_single_arm(jobs[j][0], jobs[j][1])]
                    grouped = [j for j in range(len(jobs)) if j not in set(single)]
                    for k in range(0, len(grouped), b):
                        idx = grouped[k:k + b]
                        if len(idx) == 1:
                            slots[idx[0]][ai_i] = (pool.submit(opinion, a, jobs[idx[0]][4]), None)
                            continue
                        fut = pool.submit(batch_opinions, a, [jobs[j][4] for j in idx])
                        for pos, j in enumerate(idx):
                            slots[j][ai_i] = (fut, pos)
                    for j in single:
                        slots[j][ai_i] = (pool.submit(opinion, a, jobs[j][4]), None)
                else:
                    for j, job in enumerate(jobs):
                        slots[j][ai_i] = (pool.submit(opinion, a, job[4]), None)
            # 3) 종목마다 합의 → 바로 저장 (중간에 끊겨도 끝난 종목은 남고, 화면에 진행 상황이 보인다)
            for i, (sym, t, frow, rrow, ctx) in enumerate(jobs):
                opinions = [f.result() if pos is None else f.result()[pos] for f, pos in slots[i]]
                sig = self.ensemble.combine(sym, opinions, board, calibrators)
                cid = None
                if persist:
                    with session_scope(self.engine) as s:
                        cid = self._persist_decision(s, sym, t, frow, rrow, ctx, sig, opinions, bars,
                                                     challenger, challenger_rec, scenarios, versions)
                    ops.set_state(self.engine, progress_key, {"done": i + 1, "total": len(jobs), "symbol": sym,
                                                              "started_at": started, "running": i + 1 < len(jobs)})
                decisions.append(Decision(sym, ctx.as_of, sig, opinions, ctx, cid))
        return decisions

    def _persist_decision(self, s, sym, t, frow, rrow, ctx, sig, opinions, bars, challenger, challenger_rec,
                          scenarios: bool, versions: dict | None = None) -> int:
        rec = save_consensus(s, sig, opinions, ctx.as_of, self.horizon, ctx.regime.get("regime"),
                             evidence=evidence_snapshot(ctx), versions=versions,
                             plan=self._trade_plan(sym, bars.get(sym), t, sig.prob_up, live=versions is None or
                                                   not (versions or {}).get("replay"), action=sig.action))
        cid = rec.id
        if challenger is not None:  # 앙상블 미참여, 채점만
            ch = QuantAnalyst(challenger, challenger_rec.version, name="challenger").analyze(ctx)
            if not ch.abstained:
                s.add(AnalystOpinionRecord(
                    consensus_id=None, analyst="challenger", symbol=sym, as_of=ctx.as_of,
                    horizon_bars=self.horizon, category="direction", prob_up=ch.prob_up,
                    confidence=ch.confidence, veto=False,
                    payload={"model_version": challenger_rec.version, "regime": ctx.regime.get("regime")}))
        if scenarios:
            pred = Prediction(sym, t, self.horizon, sig.prob_up, direction_of(sig.prob_up),
                              sig.confidence / 100, {"action": sig.action})
            rstate = (RegimeState(Regime(rrow["regime"]), float(rrow["trend"]), float(rrow["vol_pct"]),
                                  float(rrow["drawdown"])) if rrow is not None and rrow["regime"] else None)
            vol = float(frow.get("vol_20") or 0.02)
            payload = build_scenarios(pred, float(bars[sym]["close"].loc[t]), vol, rstate, horizon_days=1)
            s.add(Scenario(created_at=datetime.now(UTC), target_date=t.date(), symbol=sym, payload=payload))
        news_titles = " / ".join(n["title"] for n in ctx.news[:3])
        self.memory.add(s, "opinion",
                        f"{sym} {ctx.regime.get('regime')} 국면, {sig.action} P(up)={sig.prob_up:.2f}, "
                        f"충돌 {sig.conflict}. 뉴스: {news_titles}", ts=ctx.as_of, symbol=sym,
                        meta={"consensus_id": cid, "action": sig.action})
        return cid

    def _trade_plan(self, sym: str, b, t, prob_up: float, live: bool = True, action: str | None = None) -> dict | None:
        """사이징 · 진입 구간 · 무효화 조건 — 예측과 함께 봉인된다. 과거 재생에는 그때의 이벤트·준비 상태가 없어 배수 1."""
        from .trading.trade_plan import plan
        if b is None:
            return None
        try:
            c = self.settings.costs
            cost = 2 * (c.commission_bps + c.slippage_bps) + (c.sell_tax_bps if sym[:1].isdigit() else 0)
            em, why, rm, earn = 1.0, None, 1.0, False
            if live:
                cal = ops.get_state(self.engine, "event_calendar")
                r = next((x for x in cal.get("risk") or [] if x["symbol"] == sym), None)
                if r:
                    em, why = r.get("buy_multiplier", 1.0), r.get("reason")
                    earn = bool(r.get("earnings") and r["earnings"].get("trading_days", 99) <= self.horizon)
                rd = ops.get_state(self.engine, "readiness").get("status")
                rm = {"NOT_READY": 0.0, "CAUTION": 0.75}.get(rd, 1.0)
            return plan(sym, b.loc[:t], prob_up, self.horizon, cost, self.settings.risk.max_position_weight,
                        event_mult=em, event_reason=why, readiness_mult=rm, earnings_in_horizon=earn, action=action)
        except Exception as e:  # noqa: BLE001 - 계획 실패가 판단 저장을 막으면 안 됨
            log.warning("매매 계획 실패 %s: %s", sym, e)
            return None

    # ================================================================ 매매
    def kill_switch_on(self) -> bool:
        return ops.kill_switch_on(self.engine)

    def set_kill_switch(self, on: bool, reason: str = "", by: str = "manual") -> None:
        ops.set_kill_switch(self.engine, on, reason, by)
        self.notifier.send(f"킬스위치 {'ON' if on else 'OFF'} ({by}) {reason}", "critical" if on else "warn")

    def load_portfolio(self, mode: str) -> Portfolio:
        with session_scope(self.engine) as s:
            snap = s.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode)
                            .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.id.desc()))
        if snap is None:
            from .global_market import CASH_USD, is_us_book
            return Portfolio(cash=CASH_USD if is_us_book(mode) else self.settings.initial_cash)
        return Portfolio(cash=snap.cash, positions={
            k: Position(v["qty"], v["avg_price"]) for k, v in (snap.positions or {}).items()})

    def signals_from_decisions(self, decisions: list[Decision], pf: Portfolio, prices: dict[str, float],
                               budget_ratio: float = 1.0):
        """합의 신호 → 목표 비중. AI 는 여기까지만 영향을 준다 (주문 수량/실행은 시스템이 결정).

        budget_ratio: 계좌 자산 중 이 시스템이 운용할 비율 (Live 소액 상한 적용용).
        """
        weights = pf.weights(prices)
        max_w = self.settings.risk.max_position_weight * budget_ratio
        out = []
        for d in decisions:
            cur = weights.get(d.symbol, 0.0)
            a = d.signal.action
            exit_ops = [o for o in d.opinions if o.meta.get("exit")]
            if exit_ops and cur > 0:  # 치명적 위험: 보유분도 즉시 정리
                out.append(Signal(d.symbol, 0.0, d.signal.prob_up,
                                  f"RISK EXIT: {exit_ops[0].meta.get('exit_reason')}", consensus_id=d.consensus_id))
                continue
            if a == "BUY":
                target = max_w * min(1.0, d.signal.confidence / 80)
                out.append(Signal(d.symbol, max(target, cur), d.signal.prob_up,
                                  f"CONSENSUS BUY {d.signal.confidence:.0f}", consensus_id=d.consensus_id))
            elif a == "SELL":
                out.append(Signal(d.symbol, 0.0, d.signal.prob_up, "CONSENSUS SELL", consensus_id=d.consensus_id))
            elif cur > 0:  # HOLD / NO_TRADE: 보유분 유지 (신규 진입 없음)
                out.append(Signal(d.symbol, cur, d.signal.prob_up, a, consensus_id=d.consensus_id))
        return out

    def trade(self, decisions: list[Decision], mode: Mode, ts: datetime | None = None,
              quotes: dict[str, MarketQuote] | None = None, signals: list[Signal] | None = None,
              book: str | None = None) -> list:
        """한 번의 매매 사이클. 같은 장부의 사이클은 동시에 하나만 돈다.

        signals 를 주면 합의 신호 대신 그 목표 비중을 쓴다 (코어-위성).
        book 을 주면 별도의 가상 장부(Paper)로 실행한다 (AI 기여도 측정용).
        """
        if mode not in (Mode.PAPER, Mode.SHADOW, Mode.LIVE):
            raise ValueError(f"{mode} 모드는 주문을 내지 않습니다")
        if book is not None and mode is not Mode.PAPER:
            raise ValueError("가상 장부는 PAPER 로만 실행")
        name = book or mode.value
        with ops.trading_lock(self.engine, f"trade-{name}", Path(self.settings.artifacts_dir) / "locks"):
            return self._trade(decisions, mode, ts or datetime.now(UTC), quotes, signals, book)

    def _live_capital_capped(self) -> bool:
        """소액 상한(QUANT_LIVE_MAX_CAPITAL)은 실제 돈에만 적용. 모의투자(KIS_ENV=demo)는 계좌 전체로 운용."""
        return not (self.settings.broker == "kis" and self.settings.kis_env == "demo")

    # ================================================================ 검증 사다리
    def ladder_stage(self) -> str:
        return ops.get_state(self.engine, "ladder").get("stage") or "backtest"

    def _real_money(self, mode: Mode) -> bool:
        return mode is Mode.LIVE and self._live_capital_capped()

    def ai_enabled(self, mode: Mode) -> bool:
        """이 장부의 주문에 AI 를 쓰나.

        실제 돈: 검증 사다리 '소액 Live' 이상일 때만 (설정과 무관한 안전장치).
        가상/모의: 설정(QUANT_CORE_ONLY=false) 또는 자동 승격(QUANT_AUTO_PROMOTE)으로 사다리 'Paper' 이상."""
        from .aitrack import demoted
        from .review.ladder import rank
        if demoted(self.engine):  # 성적이 계속 나쁘면 자동 SHADOW — 판단·채점은 계속, 주문에는 코어만
            return False
        st, r = self.settings, rank(self.ladder_stage())
        if self._real_money(mode):
            return r >= rank("live_small") and (st.auto_promote or not st.core_only)
        return (not st.core_only) or (st.auto_promote and r >= rank("paper"))

    def live_cap(self) -> float:
        """실전 운용 상한: 사다리 '소액 Live' 단계면 QUANT_LIVE_SMALL_CAPITAL(기본 20만원)까지."""
        cap = self.settings.live_max_capital
        if self.ladder_stage() == "live_small":
            cap = min(cap, self.settings.live_small_capital)
        return cap

    def ladder_evidence(self) -> dict:
        from .analytics import book_equity
        from .review.promotion import _stats
        from .review.scorecard import scorecard
        with session_scope(self.engine) as s:
            sc = scorecard(s, "KR", 300)
            roll = scorecard(s, "KR", 50)["summary"]
            models = s.scalars(select(ModelRecord.status)).all()
        champ = self.registry.champion()
        bt = champ.metrics.get("strategy", {}) if champ and champ.metrics else {}
        ev = {"summary": sc["summary"], "last_30d": sc["last_30d"], "by_regime": sc["by_regime"],
              "days_tracked": sc["days_tracked"], "rolling50": roll, "halted": ops.halted(self.engine),
              "live_consent": self.settings.live_consent,
              "integrity_ok": (lambda e: None if not e else bool((e.get("ledger") or {}).get("ok", True)
                                                             and (e.get("leakage") or {}).get("ok", True)))(
                  ops.get_state(self.engine, "evaluation")),
              "backtest_ok": champ is not None or any(m in ("shadow", "champion", "retired") for m in models),
              "backtest_note": (f"퀀트 모델 과거 검증 통과 (Sharpe {bt.get('sharpe', 0):.2f})" if champ and bt
                                else "퀀트 모델 과거 검증 통과" if champ else ""),
              "backtest": {k: bt.get(k) for k in ("sharpe", "total_return", "max_drawdown", "psr")} if bt else None}
        core, full = book_equity(self, "attr-core"), book_equity(self, "attr-full")
        if len(core) >= 2 and len(full) >= 2:
            c = _stats(core.groupby(core.index.date).last())
            f = _stats(full.groupby(full.index.date).last())
            ev["paper"] = {"days": f["days"], "return": f["return"], "excess": f["return"] - c["return"],
                           "max_drawdown": f["max_drawdown"], "dd_diff": f["max_drawdown"] - c["max_drawdown"]}
        live = book_equity(self, "live")
        if self._live_capital_capped() and len(live) >= 2:
            ev["live"] = _stats(live.groupby(live.index.date).last())
        return ev

    def ladder(self, act: bool = True, now: datetime | None = None) -> dict:
        """검증 사다리 평가 → 단계 변경(승격·강등)은 기록·알림. act=False 면 판정만."""
        from .review import ladder as L
        now = now or datetime.now(UTC)
        ev = self.ladder_evidence()
        state = ops.get_state(self.engine, "ladder")
        new, res = L.step(state, ev, now)
        if act:
            ops.set_state(self.engine, "ladder", new)
            if res["changed"]:
                frm, to = L.STAGE_LABELS[state.get("stage") or "backtest"], L.STAGE_LABELS[res["stage"]]
                verb = "승격" if res["changed"] == "promote" else "강등"
                msg = f"AI 검증 사다리 {verb}: {frm} → {to}\n" + "\n".join(f"· {x}" for x in res.get("reasons", []))
                self.notifier.send(msg, "warn" if res["changed"] == "demote" else "info")
                from .alerts import push
                push(self.engine, "ladder", f"AI {verb}: {frm} → {to}", "; ".join(res.get("reasons", []))[:300],
                     level="warn" if res["changed"] == "demote" else "good", link="#control")
            elif res.get("ready") and res.get("ready") != state.get("ready"):
                from .alerts import push
                push(self.engine, "ladder", f"다음 단계 준비됨: {L.STAGE_LABELS[res['ready']]}",
                     "; ".join(res.get("reasons", []))[:300], level="info", link="#control")
        if not act:  # 미리보기: 저장된 단계를 보여주고, 다음 평가 때 바뀔 단계는 따로 알린다
            res = {**res, "stage": state.get("stage") or "backtest", "changed": None,
                   "would": res["stage"] if res["changed"] else None, "would_kind": res["changed"]}
            new = {**state, "stage": res["stage"], "reasons": res.get("reasons", [])}
        return {**res, "state": new, "evidence": {k: v for k, v in ev.items()
                                                                               if k not in ("summary",)},
                "summary": ev["summary"], "stages": [{"key": k, "label": L.STAGE_LABELS[k], "desc": L.STAGE_DESC[k]}
                                                     for k in L.STAGES],
                "effects": {"paper_ai": self.ai_enabled(Mode.PAPER), "live_ai": self.ai_enabled(Mode.LIVE)
                            if self._live_capital_capped() else None, "live_cap": self.live_cap(),
                            "auto_promote": self.settings.auto_promote, "core_only": self.settings.core_only,
                            "real_money_broker": self._live_capital_capped() and self.settings.broker == "kis"}}

    # ================================================================ 독립 평가 · 장부 · 누수 감사
    def _all_bars(self) -> tuple[dict, dict]:
        """(종목 → 일봉, 시장 → 벤치마크) 국내 + 미국."""
        bars, bench, _ = self.market_data()
        out, benches = dict(bars), {"KR": bench}
        try:
            from . import global_market
            ub, ubench, _ = global_market.market_data(self)
            out.update(ub)
            benches["US"] = ubench
        except Exception as e:  # noqa: BLE001 - 미국 데이터가 없어도 국내 평가는 계속
            log.info("미국 일봉 없음: %s", e)
        return out, benches

    def match_outcomes(self) -> int:
        """예측마다 1·5·20 거래일 결과 + 시장 대비 초과수익 (기간이 끝난 것만)."""
        from .review.outcomes import match
        bars, benches = self._all_bars()
        n = 0
        for mkt, bench in benches.items():
            sub = {k: v for k, v in bars.items() if (k.isdigit() if mkt == "KR" else not k.isdigit())}
            with session_scope(self.engine) as s:
                n += match(s, sub, bench)
        return n

    def ledger_anchor(self) -> dict | None:
        from .review.ledger import anchor
        with session_scope(self.engine) as s:
            return anchor(s)

    def evaluation(self, act: bool = True, market: str | None = None) -> dict:
        """독립 평가: 결과 재계산 · 기준선 · 유의성 · Brier 분해 · 오차 분해 + 장부 무결성 + 누수 감사."""
        from .review.evaluator import evaluate
        from .review.leakage import audit
        from .review.ledger import verify
        bars, _ = self._all_bars()
        champ = self.registry.champion()
        bt = {"embargo": (champ.params or {}).get("embargo", BacktestConfig().embargo),
              "horizon": (champ.params or {}).get("horizon", self.horizon)} if champ else \
            {"embargo": BacktestConfig().embargo, "horizon": self.horizon}
        with session_scope(self.engine) as s:
            led = verify(s)
            leak = audit(s, bars, backtest_cfg=bt, pit_universe=True)  # 국내 유니버스는 월별 PIT (상장폐지 포함)
            res = evaluate(s, bars, market, ledger=led, leakage=leak)
        if act:
            prev = ops.get_state(self.engine, "evaluation")
            ops.set_state(self.engine, "evaluation", _json_ready(res))
            if not led["ok"] and (prev.get("ledger") or {}).get("ok", True):
                self.notifier.send(f"예측 장부 경고: {led['message']}", "critical")
                from .alerts import push
                push(self.engine, "guardian", "예측 장부 무결성 경고", led["message"], level="bad", link="#verify")
            if not leak["ok"] and (prev.get("leakage") or {}).get("ok", True):
                from .alerts import push
                push(self.engine, "guardian", "미래 정보 누수 의심", leak["message"], level="bad", link="#verify")
        return res

    # ================================================================ 드리프트 · 재학습 후보
    def drift(self, act: bool = True) -> dict:
        """피처 · 예측 · 라벨 분포 변화 (PSI). 큰 변화면 알림 + 재학습 트리거."""
        from .engines.drift import report
        bars, _, _ = self.market_data()
        with session_scope(self.engine) as s:
            rep = report(bars, s)
        if act:
            prev = ops.get_state(self.engine, "drift")
            hist = (prev.get("history") or [])[-29:] + [{"at": rep["at"], "status": rep["status"],
                                                       "psi": {f["feature"]: f["psi"] for f in rep["features"]}}]
            ops.set_state(self.engine, "drift", _json_ready(rep | {"history": hist}))
            if rep["status"] == "drift" and prev.get("status") != "drift":
                from .alerts import push
                w = rep.get("worst") or {}
                push(self.engine, "market", "데이터 드리프트: 시장이 학습 때와 달라짐",
                     f"{w.get('label', '')} PSI {w.get('psi')} · 재학습 후보를 만듭니다", level="warn", link="#verify")
        return rep

    def sector_fill(self, limit: int = 8) -> dict:
        """업종 지도를 조금씩 채운다: 보유·관심·코어 → 거래대금 상위 순."""
        from .alerts import focus_symbols
        from .engines.sector import fill_map
        bars, _, _ = self.market_data()
        liq = [sym for _, sym in sorted(((float((b["close"] * b["volume"]).iloc[-20:].mean()), k)
                                         for k, b in bars.items() if len(b) >= 20), reverse=True)]
        order = list(dict.fromkeys([*focus_symbols(self), *liq]))
        return fill_map(self.engine, order, limit=limit)

    def build_graph(self) -> dict:
        from .engines.knowledge import build
        from .engines.sector import sector_map
        bars, _, _ = self.market_data()
        try:
            from . import global_market
            ub, _, _ = global_market.market_data(self)
            bars = {**bars, **ub}
        except Exception as e:  # noqa: BLE001
            log.info("미국 일봉 없음: %s", e)
        return build(self.engine, bars, sector_map(self.engine))

    RETRAIN_VARIANTS = (("gbm", 750), ("logistic", 750), ("logistic", 500))  # v20: 16년 실데이터에서 GBM 순위 모델이 가장 나음 (RESEARCH_QUANT_V2 6장)

    def auto_retrain(self, now: datetime | None = None, force: bool = False, max_variants: int = 2) -> dict:
        """재학습 후보 자동 생성. 트리거: 데이터 드리프트 · champion 전진 성과 하락 · 마지막 후보 7일 경과.

        후보는 바로 쓰지 않는다: 백테스트 게이트 → Shadow 전진 검증 → champion (기존 레지스트리 경로)."""
        now = now or datetime.now(UTC)
        reasons = []
        if ops.get_state(self.engine, "drift").get("status") == "drift":
            reasons.append("데이터 드리프트")
        try:
            fwd = self.check_champion(rollback=False)
            if fwd.get("status") == "ok" and fwd.get("passed") is False:
                reasons.append("champion 전진 성과 하락: " + "; ".join(fwd.get("failures", []))[:120])
        except Exception as e:  # noqa: BLE001
            log.info("champion 전진 점검 실패: %s", e)
        with session_scope(self.engine) as s:
            last = s.scalar(select(func.max(ModelRecord.created_at)))
        if last is None or now - (last if last.tzinfo else last.replace(tzinfo=UTC)) >= timedelta(days=7):
            reasons.append("정기 (7일)")
        if not reasons and not force:
            return {"trained": [], "reasons": [], "skipped": "트리거 없음"}
        reason = " · ".join(reasons) or "수동"
        bars, bench, sentiment = self.market_data()
        out = []
        for kind, window in self.RETRAIN_VARIANTS[:max_variants]:
            cfg = BacktestConfig(horizon=self.horizon, risk=self.settings.risk, costs=self.settings.costs,
                                 initial_cash=self.settings.initial_cash, model_kind=kind, train_window=window)
            try:
                rec, res, gate = self.train_candidate(bars, bench, sentiment, cfg, name=f"quant-{kind}-w{window}",
                                                      reason=reason)
                out.append({"id": rec.id, "version": rec.version, "kind": kind, "window": window,
                            "passed": gate.passed, "failures": gate.failures,
                            "sharpe": res.metrics.get("strategy", {}).get("sharpe")})
            except Exception as e:  # noqa: BLE001 - 한 변형 실패가 다른 변형을 막지 않게
                out.append({"kind": kind, "window": window, "error": str(e)[:200]})
        ev = ops.get_state(self.engine, "model_events").get("events", [])
        ev.append({"at": now.isoformat(), "kind": "retrain", "reason": reason, "candidates": out})
        ops.set_state(self.engine, "model_events", {"events": ev[-100:]})
        return {"trained": out, "reasons": reasons}

    def _live_broker(self, pf: Portfolio):
        st = self.settings
        if st.broker != "kis":
            # 증권사 미설정: 안전장치 검사 후 명시적으로 실패
            champ = self.registry.champion()
            return LiveBroker(pf, st, champion_ready=champ is not None and champ.shadow_metrics is not None)
        from .trading.kis import KISBroker, KISClient
        if st.kis_env == "real":
            champ = self.registry.champion()
            st.assert_live_allowed(champion_ready=champ is not None and champ.shadow_metrics is not None)
        else:
            log.warning("KIS 모의투자 계좌로 LIVE 파이프라인 실행 (실제 돈 아님)")
        return KISBroker(pf, KISClient.from_env(st.artifacts_dir), CostModel(st.costs))

    def _trade(self, decisions: list[Decision], mode: Mode, ts: datetime, quotes,
               signals_override: list[Signal] | None = None, book: str | None = None) -> list:
        st = self.settings
        name = book or mode.value
        pf = self.load_portfolio(name)
        broker = None
        if mode is not Mode.LIVE:
            self.recover_orders(name)  # 가상 장부: 지난번 중단된 사이클의 미완료 기록 정리
        if book is None and ops.halted(self.engine):
            # 자동 감시가 심각한 이상으로 멈춘 상태: 매도 포함 새 주문 없음 (사람이 확인 후 해제)
            ks = ops.get_state(self.engine, "kill_switch")
            log.warning("[%s] HALTED — 주문 건너뜀: %s", name, ks.get("reason"))
            DBJournal(name, self.engine).note(ts, "risk_block", f"HALTED: {ks.get('reason', '')}"[:500])
            return []
        if mode is Mode.LIVE:
            broker = self._live_broker(pf)
            try:
                if hasattr(broker, "recover"):
                    self.recover_orders(name, broker)  # 지난 사이클에 끝나지 않은 주문부터 정리 (중복 주문 방지)
                if hasattr(broker, "sync_portfolio"):
                    broker.sync_portfolio(self.symbols())  # 진실의 원천 = 증권사 잔고
                    if quotes is None:
                        # 필요한 종목만 실시간 조회 (전 종목 조회는 모의투자 기준 사이클당 수 분)
                        want = {x.symbol for x in signals_override} if signals_override is not None \
                            else {d.symbol for d in decisions}
                        quotes = broker.live_quotes(sorted(want | set(pf.positions)))
                        self._record_quotes(quotes)
                if st.broker == "kis":
                    ops.record_health(self.engine, "broker", True)
            except Exception as e:
                if st.broker == "kis":
                    ops.record_health(self.engine, "broker", False, f"{type(e).__name__}: {e}")
                raise
        if quotes is None:
            bars, _, _ = self.market_data(ts)
            quotes = {}
            for sym, b in bars.items():
                if len(b):
                    px = float(b["close"].iloc[-1])
                    sig = float(np.log(b["close"]).diff().iloc[-20:].std()) if len(b) > 21 else None
                    quotes[sym] = MarketQuote(last=px, bid=px * 0.9995, ask=px * 1.0005,
                                              sigma=sig if sig and np.isfinite(sig) else None)
        prices = {k: q.last for k, q in quotes.items()}
        for sym in list(pf.positions):
            prices.setdefault(sym, pf.positions[sym].avg_price)
            quotes.setdefault(sym, MarketQuote(last=prices[sym]))
        equity = pf.equity(prices)

        from .desk import cost_config
        from .global_market import COSTS as US_COSTS
        from .global_market import is_us_book
        # 해외 장부: 해외주식 수수료, 매도세 없음 · 국내: 실측 슬리피지 50건 이상이면 보정된 비용
        costs = CostModel(US_COSTS if is_us_book(name) else cost_config(self, st.costs))
        if broker is None:
            broker = PaperBroker(pf, costs) if mode is Mode.PAPER else ShadowBroker(pf, costs)
            if isinstance(broker, ShadowBroker):
                from .desk import sim_bias
                broker.sim_bias_bps = sim_bias(self)  # 실측 parity 100건 이상이면 시뮬레이터 편향 보정
        budget_ratio = 1.0
        if mode is Mode.LIVE and equity > 0 and self._live_capital_capped():
            budget_ratio = min(1.0, self.live_cap() / equity)  # 실전 계좌: 소액 상한만큼만 운용

        try:
            risk = RiskEngine(st.risk)
            risk.kill_switch = self.kill_switch_on()
            risk.start_day(ts.date(), self._day_start_equity(name, ts, equity), self._orders_today(name, ts))
            with session_scope(self.engine) as s:  # 유동성 한도: 20일 평균 거래대금 대비 주문 금액
                risk.adv = recent_adv(s, set(pf.positions) | ({x.symbol for x in signals_override}
                                                              if signals_override is not None else set()))
        except Exception as e:
            ops.record_health(self.engine, "risk_engine", False, f"{type(e).__name__}: {e}")
            raise
        from .engines.sector import sector_map
        risk.sectors = sector_map(self.engine)  # 업종 한도 (섹터 엔진 · 국내는 WICS 우선)
        from .proof import apply_risk as _proof_risk
        _proof_risk(self, name, risk)  # v29 증명 프로젝트 장부면 보유 종목 수 한도
        if name == "autopilot":  # v30 AI 자동매매 가상 장부: 같은 규칙 (보유 종목 수 · 종목당 상한)
            from .autopilot import config as _ap_cfg
            _c = _ap_cfg(self)
            risk.max_positions = int(_c["max_positions"])
            risk.limits = replace(risk.limits, max_position_weight=_c["max_position_weight"], max_order_value=float(_c["principal"]) * _c["max_position_weight"] * 1.5)
        if book is None:  # v13 게이트 — 성과 귀속용 장부(book)에는 적용하지 않는다 (비교가 공정해야 함)
            from . import readiness as _rd
            from .desk import event_caps_for, portfolio_gate
            risk.buy_block = _rd.buy_block(self, name)
            want = {x.symbol for x in signals_override} if signals_override is not None else {d.symbol for d in decisions}
            risk.event_caps = event_caps_for(self, want)
            from .failmode import apply_caps
            risk.event_caps = apply_caps(self, risk.event_caps, want, ts)  # 뉴스 수집 다운 → 신규 매수 절반 (fail-closed)
            try:
                gate_bars, _, _ = self.market_data(ts) if not is_us_book(name) else (None, None, None)
                if gate_bars:
                    risk.portfolio_gate = portfolio_gate(self, gate_bars)
            except Exception as e:  # noqa: BLE001 - 게이트 계산 실패는 기존 한도로 진행 (로그)
                log.warning("포트폴리오 사전 게이트 준비 실패: %s", e)
        from dataclasses import replace as _dc_replace
        for sym, q in list(quotes.items()):  # 시장 충격: 평균 거래대금을 알면 큰 주문일수록 비싸게 체결
            if risk.adv.get(sym) and q.adv is None:
                quotes[sym] = _dc_replace(q, adv=risk.adv[sym])
        if book is None:
            ops.record_health(self.engine, "risk_engine", True)
        journal = DBJournal(name, self.engine)
        regime = next((d.context.regime.get("regime") for d in decisions if d.context.regime.get("regime")), None)
        mult = (EXPOSURE_MULTIPLIER[Regime(regime)] if regime else 1.0) * budget_ratio
        if signals_override is not None:
            signals = [replace(x, target_weight=x.target_weight * budget_ratio) for x in signals_override]
            mult = budget_ratio  # 코어-위성은 국면 배수를 쓰지 않는다 (실데이터 검증에서 효과 없음)
        else:
            signals = self.signals_from_decisions(decisions, pf, prices, budget_ratio)
        for d in decisions if book is None else []:
            journal.note(ts, "signal", f"{d.symbol} {d.signal.action} P(up)={d.signal.prob_up:.2f} "
                         f"신뢰도 {d.signal.confidence:.0f} 충돌 {d.signal.conflict}", d.symbol,
                         consensus_id=d.consensus_id, vetoes=d.signal.vetoes)
        missing = {x.symbol for x in signals} - set(risk.adv) - set(pf.positions)
        if missing:
            with session_scope(self.engine) as s:
                risk.adv |= recent_adv(s, missing)
        engine = ExecutionEngine(broker, risk, journal)
        fills = engine.rebalance(signals, quotes, ts, mult)
        with session_scope(self.engine) as s:
            snap = pf.snapshot(prices)
            s.add(PortfolioSnapshot(mode=name, ts=ts, **snap))
        if book is not None:
            return fills

        if fills and mode is Mode.LIVE and st.broker == "kis":  # 체결 시뮬레이터 정확도 (parity) 실측
            try:
                from .desk import record_parity
                record_parity(self, fills, quotes)
            except Exception as e:  # noqa: BLE001 - 기록 실패가 매매를 막으면 안 됨
                log.warning("parity 기록 실패: %s", e)

        # ---- 자동 킬스위치: 일 손실 한도의 1.5배(budget.KILL_MULT)를 넘으면 전체 정지 + 알림
        from .budget import KILL_MULT
        dd = risk.daily_pnl_pct(pf.equity(prices))
        if dd <= -KILL_MULT * st.risk.max_daily_loss_pct and not risk.kill_switch:
            self.set_kill_switch(True, f"일 손실 {dd:.1%}", by="auto")
        if fills and mode is not Mode.PAPER:
            self.notifier.send(f"[{mode.value}] 체결 {len(fills)}건: " + ", ".join(
                f"{f.order.symbol} {'매수' if f.order.side.value == 'buy' else '매도'} {f.qty}@{f.price:,.0f}" for f in fills[:8]))
        if engine.unknown and mode is Mode.LIVE:
            # v23: 통신이 끊겨 접수 여부를 모름 → 재시작 복구와 같은 규칙 (신규 매수 중단 + 사람 확인).
            # 다음 사이클은 증권사 잔고로 장부를 맞춘 뒤 판단하므로 같은 주문이 두 번 나가지 않는다.
            self.set_kill_switch(True, "통신 끊김 — 주문 접수 여부 불명 (증권사 앱에서 확인 필요)", by="network")
            self.notifier.send(f"[{mode.value}] 통신 끊김으로 주문 {len(engine.unknown)}건 접수 여부 불명 → 신규 매수 중단(킬스위치). "
                               f"증권사 앱에서 체결·미체결을 확인한 뒤 킬스위치를 끄세요: {', '.join(engine.unknown[:5])}", "critical")
        if engine.errors:
            self.notifier.send(f"[{mode.value}] 주문 오류 {len(engine.errors)}건: {'; '.join(engine.errors[:3])}", "critical")
        return fills

    @staticmethod
    def data_age_days(last_bar: datetime, now: datetime | None = None) -> int:
        """마지막 봉 이후 빠진 평일 수 (한국 시간 기준). 장 시작 전 전일 봉까지 있으면 0."""
        import numpy as np
        kst = timezone(timedelta(hours=9))
        now = now or datetime.now(UTC)
        last = pd.Timestamp(last_bar)
        last_d = (last.tz_convert(kst) if last.tzinfo else last).date()
        today = (now.astimezone(kst) if now.tzinfo else now).date()
        return int(np.busday_count(last_d + timedelta(days=1), today)) if today > last_d else 0

    def _stale_guard(self, last_ts, ts: datetime, mode: Mode) -> str | None:
        """데이터가 너무 오래되면 매매를 건너뛰고 (하루 한 번) 알린다."""
        limit = self.settings.max_data_age_days
        age = self.data_age_days(last_ts, ts)
        if not limit or age <= limit:
            return None
        msg = (f"[{mode.value}] 주가 데이터가 {age}영업일 동안 갱신되지 않음 (마지막 {pd.Timestamp(last_ts).date()}) "
               f"→ 매매 건너뜀. marcap 갱신·quant-ai collect krx 확인")
        key = f"stale_alert:{mode.value}"
        if ops.get_state(self.engine, key).get("day") != str(ts.date()):
            ops.set_state(self.engine, key, {"day": str(ts.date()), "age": age})
            self.notifier.send(msg, "critical")
            DBJournal(mode.value, self.engine).note(ts, "skip", msg[:500], None, category="data", age=age)  # 왜 거래 안 했나
        log.warning(msg)
        return msg

    # ================================================================ 코어-위성
    ATTRIBUTION_BOOKS = {"attr-core": (False, False), "attr-veto": (True, False), "attr-full": (True, True)}

    @staticmethod
    def _trend(bench: pd.DataFrame | None, cfg: CoreSatelliteConfig) -> dict:
        """지수가 N일 이동평균 아래면 코어 비중 축소 (research_lab 사전등록 시험 통과 규칙)."""
        if not cfg.trend_ma or bench is None or len(bench) < cfg.trend_ma // 2:
            return {"below": False, "scale": 1.0}
        ic = bench["close"]
        ma = float(ic.rolling(cfg.trend_ma, min_periods=cfg.trend_ma // 2).mean().iloc[-1])
        below = bool(ic.iloc[-1] < ma)
        return {"below": below, "index": round(float(ic.iloc[-1]), 2), "ma": round(ma, 2),
                "scale": cfg.trend_off_scale if below else 1.0}

    @staticmethod
    def _ai_overlay(decisions: list[Decision]) -> tuple[dict[str, str], dict[str, str], list[dict]]:
        """멀티 AI 의견 → (코어 신규편입 거부권, 보유 긴급청산, 위성 후보 BUY)."""
        vetoes, exits, buys = {}, {}, []  # vetoes: 코어용 (종목 고유 위험만)
        for d in decisions:
            for o in d.opinions:
                if o.veto and o.meta.get("veto_scope", "stock") == "stock":
                    vetoes.setdefault(d.symbol, f"{o.analyst}: {o.veto_reason or 'veto'}")
                if o.meta.get("exit"):
                    exits[d.symbol] = o.meta.get("exit_reason") or "치명적 위험"
            # 위성은 AI 재량 베팅 → 시장 전체 위험(위기 국면·FOMC 등)에도 신규 매수하지 않는다 (합의가 NO_TRADE)
            if d.signal.action == "BUY":
                buys.append({"symbol": d.symbol, "confidence": d.signal.confidence,
                             "prob_up": round(d.signal.prob_up, 4), "consensus_id": d.consensus_id})
        return vetoes, exits, buys

    @staticmethod
    def _unaffordable(scores: pd.Series, bars: dict, equity: float, cfg: CoreSatelliteConfig) -> set[str]:
        """소액 계좌: 1주 가격이 종목당 목표 금액보다 훨씬 비싸면 매수 불가 → 다음 순위로 대체."""
        per_name = equity * cfg.core_weight / cfg.core_top_k
        return {s for s in scores.index[:cfg.core_buffer_k]
                if s in bars and float(bars[s]["close"].iloc[-1]) > per_name * cfg.affordability_slack}

    def _apply_var_budget(self, plan: Plan, bars: dict) -> None:
        """계획 포트폴리오의 1일 VaR95 가 한도를 넘으면 모든 비중을 같은 비율로 줄인다 (나머지 현금)."""
        from .trading.portfolio_risk import PortfolioRiskLimits, var_budget_scale
        scale, var = var_budget_scale(plan.weights, bars, PortfolioRiskLimits(max_var95=self.settings.risk.max_var95))
        if var is not None and scale < 1.0:
            plan.weights = {s: w * scale for s, w in plan.weights.items()}
            plan.notes.append(f"VaR 예산: 계획 1일 VaR95 {var:.1%} > 한도 {self.settings.risk.max_var95:.0%} "
                              f"→ 전체 비중 ×{scale:.2f}")

    def portfolio_risk(self, mode: str | None = None, with_ruin: bool = False) -> dict:
        """현재 장부(또는 증권사 잔고와 동기화된 live 장부)의 포트폴리오 리스크."""
        from .trading.portfolio_risk import PortfolioRiskLimits, portfolio_risk
        mode = mode or (self.settings.mode.value if self.settings.mode.value in ("paper", "shadow", "live") else "paper")
        pf = self.load_portfolio(mode)
        bars, bench, _ = self.market_data()
        prices = {s: float(b["close"].iloc[-1]) for s, b in bars.items() if len(b)}
        for s, p in pf.positions.items():
            prices.setdefault(s, p.avg_price)
        equity = pf.equity(prices)
        values = {s: p.qty * prices[s] for s, p in pf.positions.items() if p.qty}
        weights = {s: v / equity for s, v in values.items()} if equity > 0 else {}
        with session_scope(self.engine) as s:
            inst = {i.symbol: i for i in s.scalars(select(Instrument))}
        from .engines.sector import sector_map
        res = portfolio_risk(weights, bars, bench, names={k: v.name for k, v in inst.items()},
                             currencies={k: v.currency for k, v in inst.items()}, values=values,
                             limits=PortfolioRiskLimits(max_var95=self.settings.risk.max_var95,
                                                        max_sector_weight=self.settings.risk.max_sector_weight,
                                                        max_name_weight=max(self.settings.risk.max_position_weight * 1.5, 0.15)),
                             sectors=sector_map(self.engine), impact_coef=self.settings.costs.impact_coef)
        out = res | {"mode": mode, "equity": equity, "cash_weight": round(pf.cash / equity, 4) if equity else 1.0,
                     "var95_krw": round(res.get("var95", 0) * equity), "es95_krw": round(res.get("es95", 0) * equity),
                     "lvar95_krw": round(res.get("lvar95", 0) * equity)}
        if with_ruin and weights:
            try:
                from .desk import risk_of_ruin
                out["ruin"] = risk_of_ruin(self, mode)
            except Exception as e:  # noqa: BLE001
                out["ruin"] = {"insufficient": True, "message": f"{type(e).__name__}"}
        return out

    def order_sheet(self, holdings: dict[str, int], cash: float, use_ai: bool = True,
                    cfg: CoreSatelliteConfig | None = None, prices: dict[str, float] | None = None):
        """다른 증권사·ISA·수동 매매용 리밸런싱 주문표. 이 시스템의 장부·주문과는 무관 (읽기 전용).

        보유 종목을 '이전 코어'로 보고 리밸런싱 규칙(버퍼 유지·거부권·추세 필터)을 그대로 적용한다."""
        from .strategy.order_sheet import make_order_sheet
        cfg = cfg or CoreSatelliteConfig()
        bars, bench, _ = self.market_data()
        last_ts = max(b.index.max() for b in bars.values())
        scores = core_scores(bars, self.universe_at(last_ts), cfg.factor_weights)
        if scores.empty:
            raise RuntimeError("팩터 점수를 계산할 데이터가 부족합니다 (quant-ai collect krx 먼저)")
        px = {s: float(b["close"].iloc[-1]) for s, b in bars.items() if s != "KOSPI" and len(b)}
        px |= {s: float(v) for s, v in (prices or {}).items()}
        trend = self._trend(bench, cfg)
        prev_core = [s for s in holdings if s in scores.index]
        vetoes, exits, buys = {}, {}, []
        if use_ai:
            shortlist = list(dict.fromkeys([*scores.index[:cfg.shortlist_k], *[s for s in holdings if s in bars]]))
            vetoes, exits, buys = self._ai_overlay(self.decide(symbols=shortlist, scenarios=False))
        equity = cash + sum(q * px[s] for s, q in holdings.items() if s in px)
        plan = build_plan(scores, prev_core, True, cfg, vetoes, exits, buys, use_veto=use_ai, use_satellite=use_ai,
                          core_scale=trend["scale"], unaffordable=self._unaffordable(scores, bars, equity, cfg))
        with session_scope(self.engine) as s:
            names = {i.symbol: i.name for i in s.scalars(select(Instrument))}
        sat = {x["symbol"]: x for x in plan.satellite}
        roles = {sym: "satellite" if sym in sat else "core" for sym in plan.weights}
        roles |= {sym: "exit" for sym in plan.exits}
        reasons = {sym: (f"위성 · AI 합의 BUY 신뢰도 {sat[sym]['confidence']:.0f}" if sym in sat
                         else f"코어 #{plan.ranks[sym] + 1}" + (" (보유 유지)" if sym in holdings else " (신규)"))
                   for sym in plan.weights}
        reasons |= {sym: f"긴급 제외: {why}" for sym, why in plan.exits.items()}
        for sym in holdings:
            if sym not in plan.weights and sym not in plan.exits and sym in plan.ranks:
                reasons[sym] = f"순위 {plan.ranks[sym] + 1}위 → 유지 기준({cfg.core_buffer_k}위) 밖, 코어 제외"
            elif sym not in plan.weights and sym not in plan.exits and sym in px:
                reasons[sym] = "전략 유니버스(시총 상위) 밖 종목 → 코어 제외"
        notes = [n for n in plan.notes if "리밸런싱" not in n]
        age = self.data_age_days(last_ts)
        if age >= 1:
            notes.insert(0, f"⚠ 주가 데이터가 {age}영업일 전 것 ({last_ts.date()}) — 주문 전 quant-ai collect krx 로 갱신 권장")
        notes += [f"신규 편입 거부 (AI 리스크): {sym} {why}" for sym, why in plan.vetoed.items()]
        return make_order_sheet(plan.weights, holdings, cash, px, names=names, roles=roles, reasons=reasons,
                                costs=self.settings.costs, as_of=str(last_ts.date()), trend=trend, notes=notes)


    def run_core_satellite(self, mode: Mode, as_of: datetime | None = None, ts: datetime | None = None,
                           quotes: dict[str, MarketQuote] | None = None, cfg: CoreSatelliteConfig | None = None,
                           attribution: bool = True) -> dict:
        """코어(검증된 팩터) + 멀티 AI(거부권·긴급청산·위성) 한 사이클.

        같은 입력으로 가상 장부 3개도 굴린다 → AI 가 실제로 가치를 더했는지 측정:
          attr-core: AI 없음 / attr-veto: 코어+AI 거부권 / attr-full: 코어+거부권+위성
        """
        cfg = cfg or CoreSatelliteConfig(use_ai=self.ai_enabled(mode))
        ts = ts or datetime.now(UTC)
        # 코어 전용이면 실제 장부는 AI 를 보지 않는다. 대신 AI 섀도: 하루 한 번(새 일봉마다) AI 가 판단·채점되고
        # 가상 장부 3개로 "AI 를 켰다면" 을 측정 → AI 를 켜도 될지 증거가 쌓인다 (무료 한도 보호).
        shadow_ai = not cfg.use_ai and attribution and self.settings.ai_shadow and self.settings.has_llm
        attribution = attribution and cfg.use_ai  # 코어 전용이면 세 장부가 같으므로 측정 불필요
        bars, bench, _ = self.market_data(as_of)
        last_ts = max(b.index.max() for b in bars.values())
        if as_of is None:  # 재생(as_of 지정)이 아닌 실시간 사이클만 검사
            stale = self._stale_guard(last_ts, ts, mode)
            if stale:
                return {"plan": None, "fills": [], "decisions": [], "skipped": stale}
        universe = self.universe_at(last_ts)
        scores = core_scores(bars, universe, cfg.factor_weights)
        trend = self._trend(bench, cfg)  # 추세 필터 (코어 리밸런싱 때만 적용 → 월 1회, 연구와 동일)
        if scores.empty:
            raise RuntimeError("팩터 점수를 계산할 데이터가 부족합니다 (종목당 최소 130거래일)")
        calendar = max(bars.values(), key=len).index

        state_key = f"cs:{mode.value}"
        state = ops.get_state(self.engine, state_key)
        if mode is Mode.LIVE and self.settings.broker == "kis":
            self.reconcile_live(ts)  # 계획을 세우기 전에 증권사 잔고로 장부를 맞춘다
        held = set(self.load_portfolio(mode.value).positions)
        prev_core = state.get("core", [])
        shortlist = list(dict.fromkeys([*scores.index[:cfg.shortlist_k], *prev_core,
                                        *[s for s in held if s in bars]]))
        decisions = self.decide(as_of=as_of, symbols=shortlist, scenarios=False) if cfg.use_ai else []
        vetoes, exits, buys = self._ai_overlay(decisions)
        if buys and as_of is None:  # 사전 등록 규칙(옵트인): 조용한 장에서는 AI 위성 매수를 쉰다 — 거부권·긴급청산은 그대로
            from .alphascore import throttle
            buys, why = throttle(self, buys)
            if why:
                DBJournal(mode.value, self.engine).note(ts, "skip", why, None, category="ai_quiet")
        by_sym = {d.symbol: d for d in decisions}

        def due(st: dict) -> bool:
            last = st.get("last_rebalance")
            if not last:
                return True
            days = int(((calendar > pd.Timestamp(last)) & (calendar <= last_ts)).sum())
            return days >= cfg.core_rebalance_days

        def to_signals(plan: Plan) -> list[Signal]:
            sat = {x["symbol"] for x in plan.satellite}
            out = []
            for sym, w in plan.weights.items():
                d = by_sym.get(sym)
                # 코어는 검증된 팩터가 고른 종목 → AI 확률 최소치로 막지 않는다 (prob_up=None).
                # 위성만 AI 합의 확률이 리스크 엔진의 최소 확신도 검사를 받는다.
                if sym in sat:
                    out.append(Signal(sym, w, d.signal.prob_up if d else None,
                                      f"SATELLITE AI {d.signal.confidence:.0f}" if d else "SATELLITE",
                                      consensus_id=d.consensus_id if d else None))
                else:
                    ai = f" · AI {d.signal.action} {d.signal.prob_up:.2f}" if d else ""
                    out.append(Signal(sym, w, None, f"CORE #{plan.ranks.get(sym, -1) + 1}{ai}",
                                      consensus_id=d.consensus_id if d else None))
            return out

        # 소액 계좌: 목표 금액보다 훨씬 비싼 종목은 1주도 못 산다 → 다음 순위로 대체
        equity_est = self.load_portfolio(mode.value).equity(
            {s: float(b["close"].iloc[-1]) for s, b in bars.items() if len(b)})
        if mode is Mode.LIVE and self._live_capital_capped():
            equity_est = min(equity_est, self.live_cap())  # 실제로 운용할 금액 기준
        unaffordable = self._unaffordable(scores, bars, equity_est, cfg)

        def scale_for(st: dict) -> float:
            return trend["scale"] if due(st) or "trend_scale" not in st else st["trend_scale"]

        plan = build_plan(scores, prev_core, due(state), cfg, vetoes, exits, buys,
                          use_veto=cfg.use_ai, use_satellite=cfg.use_ai and self.settings.ai_overlay == "full",
                          core_scale=scale_for(state), unaffordable=unaffordable)
        self._apply_var_budget(plan, bars)
        if not cfg.use_ai:
            plan.notes.append("코어 전용 모드 (AI 오버레이 꺼짐 · 코어 100%)")
        if quotes is None and mode is Mode.LIVE and self.settings.broker == "kis":
            # 실제 장부와 측정용 가상 장부가 같은 실시간 가격을 쓰도록 한 번만 조회
            from .trading.kis import KISBroker, KISClient
            held_live = set(self.load_portfolio(mode.value).positions)
            books_core = {s for b in self.ATTRIBUTION_BOOKS for s in ops.get_state(self.engine, f"cs:{b}").get("core", [])}
            want = set(plan.weights) | held_live | books_core | set(scores.index[:cfg.core_top_k])
            try:
                quotes = KISBroker(Portfolio(cash=0), KISClient.from_env(self.settings.artifacts_dir)).live_quotes(sorted(want))
            except Exception as e:
                ops.record_health(self.engine, "broker", False, f"시세 조회 실패 {type(e).__name__}: {e}")
                raise
            ops.record_health(self.engine, "broker", True)
            self._record_quotes(quotes, bars)
        fills = self.trade(decisions, mode, ts=ts, quotes=dict(quotes) if quotes else None,
                           signals=to_signals(plan))
        if plan.core_rebalanced:
            state = {"core": plan.core, "last_rebalance": str(last_ts), "rebalances": state.get("rebalances", 0) + 1,
                     "trend_scale": trend["scale"]}
        else:
            state = {**state, "core": plan.core}
        ops.set_state(self.engine, state_key, state)
        pd_plan = plan.to_dict()
        pd_plan["scores"] = dict(list(plan.scores.items())[:60])
        pd_plan["ranks"] = {k: v for k, v in plan.ranks.items() if v < 60}
        ops.set_state(self.engine, f"cs-plan:{mode.value}", {
            **pd_plan, "as_of": str(last_ts), "ts": ts.isoformat(), "mode": mode.value,
            "universe_size": len(scores), "config": dict(cfg.__dict__),
            "trend": {**trend, "applied_scale": state.get("trend_scale", 1.0)},
            "unaffordable": sorted(unaffordable),
            "last_rebalance": state.get("last_rebalance"), "rebalances": state.get("rebalances", 0),
            "ai": {"analyzed": len(decisions), "buys": len(buys), "vetoes": len(vetoes), "exits": len(exits)}})

        shadow_key = f"ai-shadow:{mode.value}"
        if shadow_ai and ops.get_state(self.engine, shadow_key).get("bar") != str(last_ts):
            # 실제 주문을 낸 뒤에 AI 를 부른다 → LLM 지연·장애가 실제 주문을 늦추지 않는다
            try:
                decisions = self.decide(as_of=as_of, symbols=shortlist, scenarios=False)
            except Exception as e:  # noqa: BLE001 - 섀도는 실제 운용에 영향을 주면 안 된다
                log.warning("AI 섀도 판단 실패: %s", e)
                decisions = []
            if decisions:
                vetoes, exits, buys = self._ai_overlay(decisions)
                by_sym = {d.symbol: d for d in decisions}
                attribution = True
                ops.set_state(self.engine, shadow_key, {
                    "bar": str(last_ts), "ts": ts.isoformat(), "analyzed": len(decisions), "buys": len(buys),
                    "vetoes": len(vetoes), "exits": len(exits)})
        if attribution:
            for book, (use_veto, use_sat) in self.ATTRIBUTION_BOOKS.items():
                bst = ops.get_state(self.engine, f"cs:{book}")
                bplan = build_plan(scores, bst.get("core", []), due(bst), cfg, vetoes, exits, buys,
                                   use_veto=use_veto, use_satellite=use_sat, core_scale=scale_for(bst),
                                   unaffordable=unaffordable)
                self._apply_var_budget(bplan, bars)
                self.trade(decisions, Mode.PAPER, ts=ts, quotes=dict(quotes) if quotes else None,
                           signals=to_signals(bplan), book=book)
                ops.set_state(self.engine, f"cs:{book}", {
                    "core": bplan.core,
                    "last_rebalance": str(last_ts) if bplan.core_rebalanced else bst.get("last_rebalance"),
                    "trend_scale": trend["scale"] if bplan.core_rebalanced else bst.get("trend_scale", 1.0)})
        if plan.exits and cfg.use_ai:
            self.notifier.send(f"[{mode.value}] AI 긴급 청산: " + ", ".join(f"{k}({v})" for k, v in plan.exits.items()),
                               "warn")
        return {"plan": plan, "fills": fills, "decisions": decisions}

    def _record_quotes(self, quotes: dict | None, bars: dict | None = None) -> None:
        """실시간 시세 품질 기록 → 자동 킬스위치 (Quote stale / Data source conflict)."""
        if not quotes:
            return
        bad = [s for s, q in quotes.items() if not q.last or q.last <= 0]
        conflicts = []
        if bars:
            for s, q in quotes.items():
                b = bars.get(s)
                if b is not None and len(b) and q.last and q.last > 0:
                    ref = float(b["close"].iloc[-1])
                    if ref > 0 and abs(q.last / ref - 1) > 0.30:  # 국내 가격제한폭 ±30% 밖 = 데이터 문제
                        conflicts.append(s)
        ops.set_state(self.engine, "live_quotes", {"ts": datetime.now(UTC).isoformat(), "n": len(quotes),
                                                   "bad": len(bad), "conflicts": conflicts[:20]})

    def champion_forward(self) -> dict:
        """champion 모델의 승격 이후 전진(forward) 성적: 적중률 · Brier vs 기저율 · 최근 확률 분산."""
        champ = self.registry.champion()
        if champ is None:
            return {"status": "none"}
        promoted = (champ.shadow_metrics or {}).get("promoted_at")
        since = datetime.fromisoformat(promoted) if promoted else champ.created_at
        with session_scope(self.engine) as s:
            rows = s.execute(select(AnalystOpinionRecord.prob_up, AnalystOpinionRecord.correct,
                                    AnalystOpinionRecord.realized_return, AnalystOpinionRecord.payload).where(
                AnalystOpinionRecord.analyst == "quant", AnalystOpinionRecord.category == "direction",
                AnalystOpinionRecord.as_of >= since).order_by(AnalystOpinionRecord.id.desc()).limit(2000)).all()
        rows = [r for r in rows if (r[3] or {}).get("model_version") == champ.version]
        scored = [r for r in rows if r[1] is not None]
        recent = pd.Series([r[0] for r in rows[:200]], dtype=float)
        out = {"status": "ok", "version": champ.version, "since": str(since)[:19], "n": len(scored),
               "n_recent": len(recent), "prob_std": round(float(recent.std()), 5) if len(recent) > 1 else None}
        if scored:
            y = pd.Series([1.0 if (r[2] or 0) > 0 else 0.0 for r in scored])
            p = pd.Series([r[0] for r in scored], dtype=float)
            base = float(y.mean())
            out |= {"accuracy": round(float(sum(bool(r[1]) for r in scored) / len(scored)), 4),
                    "brier": round(float(((p - y) ** 2).mean()), 5),
                    "brier_excess": round(float(((p - y) ** 2).mean() - base * (1 - base)), 5)}
        return out

    def check_champion(self, rollback: bool = True) -> dict:
        """champion 전진 성과 이상 → (rollback=True 면) 자동 롤백 + 알림 + 기록."""
        from .registry.model_registry import forward_gate
        fwd = self.champion_forward()
        if fwd["status"] == "none":
            return fwd
        gate = forward_gate(fwd, self.registry.gates)
        out = fwd | {"passed": gate.passed, "failures": gate.failures}
        if not gate.passed and rollback:
            rb = self.registry.rollback("; ".join(gate.failures))
            out["rollback"] = rb
            log_ = ops.get_state(self.engine, "model_events").get("events", [])
            log_.append({"at": datetime.now(UTC).isoformat(), "kind": "rollback", **rb, "forward": fwd})
            ops.set_state(self.engine, "model_events", {"events": log_[-100:]})
            self.notifier.send(f"모델 자동 롤백: champion {rb.get('rolled_back')} → {rb.get('restored') or '없음'}\n"
                               f"사유: {'; '.join(gate.failures)}", "critical")
        return out

    def guardian(self, mode: str | None = None, act: bool = True, market_open: bool = False) -> dict:
        """자동 킬스위치 10개 조건 점검 (trading/guardian.py)."""
        from .trading.guardian import evaluate
        return evaluate(self, mode, act=act, market_open=market_open)

    def ai_verdict(self) -> dict:
        """AI 를 켜도 되나: AI 섀도 가상 장부 3개 + 합의 확률 보정 품질 (review/promotion.py)."""
        from .ensemble.calibration import calibration_report
        from .review.promotion import ai_verdict
        books = {}
        with session_scope(self.engine) as s:
            for book in self.ATTRIBUTION_BOOKS:
                snaps = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == book)
                                  .order_by(PortfolioSnapshot.ts, PortfolioSnapshot.id)).all()
                if snaps:
                    eq = pd.Series([x.equity for x in snaps], index=pd.DatetimeIndex([x.ts for x in snaps]))
                    books[book] = eq.groupby(eq.index.date).last()
            cons = calibration_report(s, window_days=365)["consensus"]
        mode = self.settings.mode.value
        res = ai_verdict(books, cons, ops.get_state(self.engine, f"ai-shadow:{mode}"))
        res |= {"core_only": self.settings.core_only, "ai_shadow": self.settings.ai_shadow and self.settings.has_llm,
                "overlay": self.settings.ai_overlay}
        prev = ops.get_state(self.engine, "ai_verdict")
        ops.set_state(self.engine, "ai_verdict", {k: res[k] for k in ("status", "title", "message")})
        if res["status"] == "promote" and prev.get("status") != "promote" and self.settings.core_only:
            self.notifier.send(f"AI 섀도 판정: {res['title']}\n{res['message']}", "info")
        return res

    def add_cashflow(self, mode: str, amount: float, day: date | None = None, memo: str = "") -> list[dict]:
        """입금(+)·출금(-) 기록. 건강검진 수익률에서 입출금 효과를 뺀다 (시간가중수익률)."""
        if amount == 0:
            raise ValueError("금액은 0 이 아니어야 합니다")
        st = ops.get_state(self.engine, f"cashflows:{mode}")
        flows = list(st.get("flows", []))
        flows.append({"date": str(day or datetime.now(UTC).date()), "amount": float(amount), "memo": memo[:100]})
        ops.set_state(self.engine, f"cashflows:{mode}", {"flows": flows})
        return flows

    def cashflows(self, mode: str) -> list[dict]:
        return ops.get_state(self.engine, f"cashflows:{mode}").get("flows", [])

    def strategy_health(self, mode: str | None = None, with_ic: bool = True, notify: bool = True) -> dict:
        """실제 운용 자산곡선이 과거 검증 범위 안인지 판정 (strategy/health.py). 상태가 바뀌면 알림."""
        from .strategy.health import evaluate, factor_ic_history, format_value
        mode = mode or (self.settings.mode.value if self.settings.mode.value in ("paper", "shadow", "live") else "paper")
        with session_scope(self.engine) as s:
            snaps = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode)
                              .order_by(PortfolioSnapshot.ts, PortfolioSnapshot.id)).all()
            eq = pd.Series([x.equity for x in snaps], index=pd.DatetimeIndex([x.ts for x in snaps]), dtype=float)
        flows = self.cashflows(mode)
        if flows and len(eq):
            eq = time_weighted_index(eq, flows)
        bars, bench, _ = self.market_data()
        ic = None
        if with_ic:
            try:
                ic = factor_ic_history(bars, CoreSatelliteConfig().factor_weights, universe_fn=self.universe_at)
            except Exception as e:  # noqa: BLE001 - IC 는 보조 지표
                log.warning("팩터 IC 계산 실패: %s", e)
        res = evaluate(eq, bench["close"] if bench is not None else None, factor_ic=ic)
        res |= {"mode": mode, "factor_ic": ic, "checked_at": datetime.now(UTC).isoformat(), "cashflows": len(flows)}
        prev = ops.get_state(self.engine, f"strategy_health:{mode}")
        ops.set_state(self.engine, f"strategy_health:{mode}", res)
        worse = res["status"] in ("warn", "critical")
        recovered = prev.get("status") in ("warn", "critical") and res["status"] == "ok"
        if notify and prev.get("status") != res["status"] and (worse or recovered):
            bad = [f"{c['label']} {format_value(c)}" for c in res["checks"] if c["status"] in ("warn", "critical")]
            self.notifier.send(f"[{mode}] 전략 건강검진: {prev.get('status', '-')} → {res['status']}"
                               + (f" ({', '.join(bad)})" if bad else "") + f"\n{res['action']}",
                               "critical" if res["status"] == "critical" else "warn")
        return res

    def reconcile_live(self, ts: datetime | None = None) -> dict:
        """증권사 잔고 = 진실의 원천. DB 장부와 다르면 증권사 기준으로 스냅샷을 남기고 알린다."""
        from .trading.kis import KISBroker, KISClient
        ts = ts or datetime.now(UTC)
        db = self.load_portfolio("live")
        broker = KISBroker(Portfolio(cash=db.cash, positions=dict(db.positions)),
                           KISClient.from_env(self.settings.artifacts_dir))
        before = {s: p.qty for s, p in db.positions.items() if p.qty}
        try:
            pf = broker.sync_portfolio(self.symbols())
        except Exception as e:
            ops.record_health(self.engine, "broker", False, f"잔고 조회 실패 {type(e).__name__}: {e}")
            raise
        ops.record_health(self.engine, "broker", True)
        after = {s: p.qty for s, p in pf.positions.items() if p.qty}
        drift = {s: (before.get(s, 0), after.get(s, 0)) for s in set(before) | set(after)
                 if before.get(s, 0) != after.get(s, 0)}
        prices = {s: p.avg_price for s, p in pf.positions.items()}
        with session_scope(self.engine) as s:
            s.add(PortfolioSnapshot(mode="live", ts=ts, **pf.snapshot(prices)))
        prev = ops.get_state(self.engine, "reconcile")
        ops.set_state(self.engine, "reconcile", {
            "at": ts.isoformat(), "drift_n": len(drift) if before else 0,
            "streak": (int(prev.get("streak", 0)) + 1) if drift and before else 0})
        if drift and before:
            DBJournal("live", self.engine).note(ts, "reconcile", f"장부 불일치 {len(drift)}종목 → 증권사 기준으로 수정",
                                                drift={k: list(v) for k, v in drift.items()})
            self.notifier.send(f"[live] 장부 불일치 {len(drift)}종목을 증권사 잔고로 수정: "
                               + ", ".join(f"{k} {a}→{b}" for k, (a, b) in list(drift.items())[:5]), "warn")
        return {"drift": drift, "cash": pf.cash, "positions": after}

    def recover_orders(self, mode: str, broker=None) -> list[dict]:
        """재시작 복구: DB 에 pending/submitted 로 남은 주문을 확정한다.

        - 증권사 주문번호가 있으면: 체결 조회 → 남은 잔량 취소 → filled/partial/unfilled 로 확정
        - 주문번호가 없으면(접수 응답 전에 종료): 증권사에 주문이 살아 있을 수 있다 → 'unknown' +
          신규 매수 중단(킬스위치) + 알림. 사람이 증권사 앱에서 확인한 뒤 킬스위치를 끈다.
        - 가상 장부(paper/shadow)의 미완료 기록은 주문이 실제로 나가지 않았으므로 취소로 확정."""
        with session_scope(self.engine) as s:
            rows = [{"id": r.id, "client_order_id": r.client_order_id, "broker_order_id": r.broker_order_id,
                     "broker_orgno": r.broker_orgno, "created_at": r.created_at, "qty": r.qty, "symbol": r.symbol,
                     "side": r.side}
                    for r in s.scalars(select(OrderRecord).where(OrderRecord.mode == mode,
                                                                 OrderRecord.status.in_(("pending", "submitted"))))]
        if not rows:
            return []
        results = broker.recover(rows) if broker is not None and hasattr(broker, "recover") else \
            [{**r, "status": "cancelled", "filled": 0, "avg_price": None} for r in rows]
        unknown = []
        with session_scope(self.engine) as s:
            for r in results:
                rec = s.get(OrderRecord, r["id"])
                rec.status, rec.updated_at = r["status"], datetime.now(UTC)
                rec.filled_qty = r.get("filled")
                rec.avg_price = r.get("avg_price") or rec.avg_price
                rec.reason = (rec.reason or "") + f" · 재시작 복구: {r['status']}"
                if r["status"] == "unknown":
                    unknown.append(f"{rec.symbol} {rec.side} {rec.qty:.0f}주 ({rec.client_order_id})")
        DBJournal(mode, self.engine).note(datetime.now(UTC), "recovery",
                                          f"미완료 주문 {len(results)}건 복구", results=[
                                              {k: v for k, v in r.items() if k != "created_at"} for r in results])
        if unknown:
            self.set_kill_switch(True, "주문 상태 불명 — 증권사 앱에서 미체결 확인 필요", by="recovery")
            self.notifier.send(f"[{mode}] 주문 상태 불명 {len(unknown)}건 → 신규 매수 중단(킬스위치). "
                               f"증권사 앱에서 미체결을 확인·취소한 뒤 킬스위치를 끄세요: {', '.join(unknown[:5])}",
                               "critical")
        else:
            self.notifier.send(f"[{mode}] 재시작 복구: 미완료 주문 {len(results)}건 확정 ("
                               + ", ".join(f"{r['symbol']} {r['status']}" for r in results[:5]) + ")", "warn")
        return results

    def slippage_stats(self, mode: str = "live", days: int = 90) -> dict:
        """실제 체결가 vs 주문 결정 시점 기준가 → 슬리피지(bps, 불리한 방향 +). 백테스트 비용 가정 검증용."""
        since = datetime.now(UTC) - timedelta(days=days)
        with session_scope(self.engine) as s:
            rows = s.execute(select(OrderRecord.side, OrderRecord.ref_price, OrderRecord.avg_price,
                                    OrderRecord.filled_qty).where(
                OrderRecord.mode == mode, OrderRecord.created_at >= since, OrderRecord.ref_price.is_not(None),
                OrderRecord.avg_price.is_not(None), OrderRecord.status.in_(("filled", "partial")))).all()
        bps = [((avg - ref) / ref * 1e4) * (1 if side == "buy" else -1) for side, ref, avg, _ in rows if ref]
        if not bps:
            return {"n": 0, "assumed_bps": self.settings.costs.slippage_bps}
        a = pd.Series(bps)
        return {"n": len(a), "mean_bps": round(float(a.mean()), 2), "median_bps": round(float(a.median()), 2),
                "p90_bps": round(float(a.quantile(0.9)), 2), "assumed_bps": self.settings.costs.slippage_bps,
                "verdict": ("가정보다 나쁨 → QUANT_SLIPPAGE_BPS 상향 후 재검증" if a.mean() > self.settings.costs.slippage_bps
                            else "가정 이내")}

    def _orders_today(self, mode: str, ts: datetime) -> int:
        start = datetime.combine(ts.date(), datetime.min.time(), UTC)
        with session_scope(self.engine) as s:
            return int(s.scalar(select(func.count()).select_from(OrderRecord).where(
                OrderRecord.mode == mode, OrderRecord.created_at >= start,
                OrderRecord.status.in_(("filled", "partial", "unfilled", "error", "pending", "submitted",
                                        "unknown", "cancelled")))) or 0)

    def _day_start_equity(self, mode: str, ts: datetime, default: float) -> float:
        start = datetime.combine(ts.date(), datetime.min.time(), UTC)
        with session_scope(self.engine) as s:
            snap = s.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode,
                                                            PortfolioSnapshot.ts < start)
                            .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.id.desc()))
        return snap.equity if snap else default

    # ================================================================ 복기
    def review(self, day: date | None = None):
        bars, bench, _ = self.market_data()
        with session_scope(self.engine) as s:
            n = resolve(s, bars, bench)
        try:
            self.match_outcomes()  # 1·5·20 거래일 결과 + 초과수익
        except Exception as e:  # noqa: BLE001
            log.warning("결과 매칭 실패: %s", e)
        try:  # 미국 예측도 채점 (미국 일봉 · SPY 기준)
            from . import global_market
            us_bars, us_bench, _ = global_market.market_data(self)
            if us_bars:
                with session_scope(self.engine) as s:
                    n += resolve(s, us_bars, us_bench)
        except Exception as e:  # noqa: BLE001 - 미국 데이터가 없어도 국내 복기는 계속
            log.info("미국 예측 채점 건너뜀: %s", e)
        with session_scope(self.engine) as s:  # 채점이 끝난 의견으로 AI 별·합의 확률 보정 다시 적합
            fitted = fit_calibrators(s)
        ops.set_state(self.engine, "calibration", fitted)
        hist = ops.get_state(self.engine, "calibration_history").get("fits", [])
        hist.append({"at": datetime.now(UTC).isoformat(), "fitted": {k: {kk: v[kk] for kk in ("a", "b", "n")}
                                                                    for k, v in fitted.items()}})
        ops.set_state(self.engine, "calibration_history", {"fits": hist[-400:]})  # 재생·감사용 보정기 버전 기록
        with session_scope(self.engine) as s:
            report = daily_review(s, day or datetime.now(UTC).date(), memory=self.memory)
            s.flush()
            log.info("채점 %d건, 교훈 %d개", n, len(report.lessons or []))
            return report


def _json_ready(obj):
    """numpy 수 · 튜플 → JSON 저장 가능한 값."""
    if isinstance(obj, dict):
        return {str(k): _json_ready(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_ready(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj) if np.isfinite(obj) else None
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    return obj


def time_weighted_index(equity: pd.Series, flows: list[dict]) -> pd.Series:
    """평가금액 + 입출금 기록 → 입출금 효과를 뺀 지수 (시작값 = 첫 평가금액).

    입출금은 그날 종가 평가에 이미 반영됐다고 본다: r_t = (E_t - F_t) / E_{t-1} - 1."""
    idx = pd.DatetimeIndex(equity.index)
    e = pd.Series(equity.to_numpy(float), index=idx.tz_localize(None) if idx.tz is not None else idx)
    e = e.groupby(e.index.normalize()).last()
    f = pd.Series(0.0, index=e.index)
    for x in flows:
        d = pd.Timestamp(x["date"]).normalize()
        pos = e.index.searchsorted(d)  # 장부가 없는 날의 입출금은 다음 평가일에 반영
        if pos < len(e):
            f.iloc[pos] += float(x["amount"])
    r = (e - f) / e.shift(1) - 1
    r.iloc[0] = 0.0
    return float(e.iloc[0]) * (1 + r.fillna(0)).cumprod()


def equity_metrics(mode: str, engine) -> dict:
    with session_scope(engine) as s:
        snaps = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode)
                          .order_by(PortfolioSnapshot.ts, PortfolioSnapshot.id)).all()
    if not snaps:
        return {}
    eq = pd.Series([x.equity for x in snaps], index=[x.ts for x in snaps])
    return performance(eq)


def latest_consensus(engine, symbol: str) -> ConsensusRecord | None:
    with session_scope(engine) as s:
        return s.scalar(select(ConsensusRecord).where(ConsensusRecord.symbol == symbol)
                        .order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()))

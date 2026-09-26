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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from .analysts.analysts import QuantAnalyst, build_analysts
from .analysts.base import MarketContext, Opinion
from .analysts.context import build_context, macro_snapshot
from .analysts.llm_clients import HashingEmbeddings, NvidiaEmbeddings
from .analysts.memory import Memory
from .backtest.backtester import Backtester, BacktestConfig, performance
from .config import Mode, Settings
from .data.db import init_db, load_bars, make_engine, session_scope, upsert_bars
from .data.models import (
    AnalystOpinionRecord,
    ConsensusRecord,
    Instrument,
    ModelRecord,
    NewsArticle,
    PortfolioSnapshot,
    Scenario,
)
from .engines.features import build_dataset, feature_columns
from .engines.news_intel import daily_sentiment
from .engines.prediction import Prediction, Predictor, direction_of
from .engines.regime import EXPOSURE_MULTIPLIER, Regime, RegimeState, equal_weight_index, regime_series
from .engines.scenario import build_scenarios
from .ensemble.engine import ConsensusSignal, EnsembleEngine, records_for
from .ensemble.tracker import resolve, save_consensus, scoreboard
from .registry.model_registry import ModelRegistry
from .review.review import daily_review
from .trading.broker import LiveBroker, MarketQuote, PaperBroker, ShadowBroker
from .trading.execution import ExecutionEngine, Signal
from .trading.journal import DBJournal
from .trading.portfolio import CostModel, Portfolio, Position
from .trading.risk import RiskEngine

log = logging.getLogger("quant_ai")

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
        self.kill_file = Path(self.settings.artifacts_dir) / "KILL_SWITCH"

    # ================================================================ 데이터
    def symbols(self, include_index: bool = False) -> list[str]:
        with session_scope(self.engine) as s:
            q = select(Instrument)
            rows = s.scalars(q).all()
            syms = [i.symbol for i in rows if include_index or i.market != BENCHMARK_MARKET]
        if self.settings.watchlist:
            syms = [x for x in syms if x in self.settings.watchlist] or list(self.settings.watchlist)
        return syms

    def index_symbols(self) -> list[str]:
        with session_scope(self.engine) as s:
            return [i.symbol for i in s.scalars(select(Instrument).where(Instrument.market == BENCHMARK_MARKET))]

    def ingest_prices(self, source, symbols: list[str], start, end, interval: str = "1d") -> int:
        n = 0
        for sym in symbols:
            bars = source.fetch_bars(sym, start, end, interval)
            with session_scope(self.engine) as s:
                n += upsert_bars(s, sym, bars, interval, source.name)
        return n

    def market_data(self, as_of: datetime | None = None):
        syms = self.symbols()
        with session_scope(self.engine) as s:
            bars = load_bars(s, syms)
            idx = load_bars(s, self.index_symbols())
            news = pd.DataFrame([
                {"published_at": n.published_at, "symbol": sym, "sentiment": n.sentiment or 0.0,
                 "importance": n.importance or 0.5}
                for n in s.scalars(select(NewsArticle)) for sym in (n.symbols or [])
            ])
        if as_of is not None:
            ts = pd.Timestamp(as_of)
            bars = {k: v[v.index <= ts] for k, v in bars.items()}
            idx = {k: v[v.index <= ts] for k, v in idx.items()}
            if not news.empty:
                news = news[pd.to_datetime(news["published_at"], utc=True) <= ts]
        bench = next(iter(idx.values())) if idx else equal_weight_index(bars)
        sentiment = daily_sentiment(news) if not news.empty else None
        return bars, bench, sentiment

    # ============================================================ 모델 수명주기
    def train_candidate(self, bars=None, bench=None, sentiment=None, config: BacktestConfig | None = None,
                        name: str = "quant-gbm"):
        """walk-forward 백테스트로 검증 → 레지스트리 등록 → 백테스트 게이트 평가 (통과 시 shadow)."""
        if bars is None:
            bars, bench, sentiment = self.market_data()
        cfg = config or BacktestConfig(horizon=self.horizon, risk=self.settings.risk, costs=self.settings.costs,
                                       initial_cash=self.settings.initial_cash)
        result = Backtester(cfg).run(bars, bench, sentiment)
        if result.model is None:
            raise RuntimeError("학습 가능한 데이터가 부족합니다")
        # 최종 모델은 전체(라벨 확정) 데이터로 재학습
        data = build_dataset(bars, cfg.horizon, regime=regime_series(bench)["score"], sentiment=sentiment)
        train = data.dropna(subset=["label"])
        model = Predictor(cfg.model_kind, cfg.horizon).fit(train[feature_columns(data)], train["label"])
        rec = self.registry.register_candidate(model, name, result.metrics, {
            "horizon": cfg.horizon, "model_kind": cfg.model_kind, "train_window": cfg.train_window,
            "min_prob": cfg.min_prob, "train_end": str(train.index.get_level_values(0).max()),
        })
        gate = self.registry.evaluate_candidate(rec.id)
        return rec, result, gate

    def _load_model(self, status: str) -> tuple[ModelRecord | None, Predictor | None]:
        with session_scope(self.engine) as s:
            rec = s.scalar(select(ModelRecord).where(ModelRecord.status == status)
                           .order_by(ModelRecord.created_at.desc()))
        if rec is None:
            return None, None
        return rec, Predictor.load(Path(rec.artifact_path))

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
                                  .order_by(PortfolioSnapshot.ts)).all()
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
               persist: bool = True, scenarios: bool = True) -> list[Decision]:
        """연구/예측 모드: 모든 AI 의 독립 의견 → 합의 신호 (주문은 내지 않음)."""
        bars, bench, sentiment = self.market_data(as_of)
        if not bars:
            return []
        reg = regime_series(bench)
        data = build_dataset(bars, self.horizon, regime=reg["score"], sentiment=sentiment)
        feats = feature_columns(data)
        model_rec, model = self.active_model()
        challenger_rec, challenger = self._load_model("shadow")
        if model_rec is not None and model_rec.status == "shadow":
            challenger_rec, challenger = None, None  # shadow 가 이미 메인으로 쓰이는 중
        analysts = build_analysts(self.settings, model, model_rec.version if model_rec else None)
        events = self.load_events()

        decisions: list[Decision] = []
        with session_scope(self.engine) as s:
            macro = macro_snapshot(s, as_of or datetime.now(timezone.utc))
            board = records_for(scoreboard(s))
            for sym in symbols or list(bars):
                if sym not in bars or bars[sym].empty:
                    continue
                rows = data.xs(sym, level=1)
                t = rows.index[-1]  # market_data 가 as_of 이후를 이미 잘라냄
                frow = rows.loc[t, feats]
                rrow = reg.loc[t] if t in reg.index else None
                ctx = build_context(s, sym, t.to_pydatetime(), self.horizon, bars[sym], frow, rrow,
                                    memory=self.memory, events=events, macro=macro)
                opinions = [a.analyze(ctx) for a in analysts]
                sig = self.ensemble.combine(sym, opinions, board)
                cid = None
                if persist:
                    rec = save_consensus(s, sig, opinions, ctx.as_of, self.horizon, ctx.regime.get("regime"))
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
                        payload = build_scenarios(pred, float(bars[sym]["close"].loc[t]), vol, rstate,
                                                  horizon_days=1)
                        s.add(Scenario(created_at=datetime.now(timezone.utc), target_date=t.date(), symbol=sym,
                                       payload=payload))
                    news_titles = " / ".join(n["title"] for n in ctx.news[:3])
                    self.memory.add(s, "opinion",
                                    f"{sym} {ctx.regime.get('regime')} 국면, {sig.action} P(up)={sig.prob_up:.2f}, "
                                    f"충돌 {sig.conflict}. 뉴스: {news_titles}", ts=ctx.as_of, symbol=sym,
                                    meta={"consensus_id": cid, "action": sig.action})
                decisions.append(Decision(sym, ctx.as_of, sig, opinions, ctx, cid))
        return decisions

    # ================================================================ 매매
    def kill_switch_on(self) -> bool:
        return self.kill_file.exists()

    def set_kill_switch(self, on: bool) -> None:
        self.kill_file.parent.mkdir(parents=True, exist_ok=True)
        if on:
            self.kill_file.write_text(datetime.now(timezone.utc).isoformat())
        elif self.kill_file.exists():
            self.kill_file.unlink()

    def load_portfolio(self, mode: str) -> Portfolio:
        with session_scope(self.engine) as s:
            snap = s.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode)
                            .order_by(PortfolioSnapshot.ts.desc()))
        if snap is None:
            return Portfolio(cash=self.settings.initial_cash)
        return Portfolio(cash=snap.cash, positions={
            k: Position(v["qty"], v["avg_price"]) for k, v in (snap.positions or {}).items()})

    def signals_from_decisions(self, decisions: list[Decision], pf: Portfolio, prices: dict[str, float]):
        """합의 신호 → 목표 비중. AI 는 여기까지만 영향을 준다 (주문 수량/실행은 시스템이 결정)."""
        weights = pf.weights(prices)
        max_w = self.settings.risk.max_position_weight
        out = []
        for d in decisions:
            cur = weights.get(d.symbol, 0.0)
            a = d.signal.action
            if a == "BUY":
                target = max_w * min(1.0, d.signal.confidence / 80)
                out.append(Signal(d.symbol, max(target, cur), d.signal.prob_up,
                                  f"CONSENSUS BUY {d.signal.confidence:.0f}", d.consensus_id))
            elif a == "SELL":
                out.append(Signal(d.symbol, 0.0, d.signal.prob_up, "CONSENSUS SELL", d.consensus_id))
            elif cur > 0:  # HOLD / NO_TRADE: 보유분 유지 (신규 진입 없음)
                out.append(Signal(d.symbol, cur, d.signal.prob_up, a, d.consensus_id))
        return out

    def trade(self, decisions: list[Decision], mode: Mode, ts: datetime | None = None,
              quotes: dict[str, MarketQuote] | None = None) -> list:
        if mode not in (Mode.PAPER, Mode.SHADOW, Mode.LIVE):
            raise ValueError(f"{mode} 모드는 주문을 내지 않습니다")
        ts = ts or datetime.now(timezone.utc)
        pf = self.load_portfolio(mode.value)
        if quotes is None:
            bars, _, _ = self.market_data(ts)
            quotes = {}
            for sym, b in bars.items():
                if len(b):
                    px = float(b["close"].iloc[-1])
                    quotes[sym] = MarketQuote(last=px, bid=px * 0.9995, ask=px * 1.0005)
        prices = {k: q.last for k, q in quotes.items()}
        for sym in list(pf.positions):
            prices.setdefault(sym, pf.positions[sym].avg_price)
            quotes.setdefault(sym, MarketQuote(last=prices[sym]))

        costs = CostModel(self.settings.costs)
        if mode is Mode.PAPER:
            broker = PaperBroker(pf, costs)
        elif mode is Mode.SHADOW:
            broker = ShadowBroker(pf, costs)
        else:
            champ = self.registry.champion()
            broker = LiveBroker(pf, self.settings, champion_ready=champ is not None and champ.shadow_metrics is not None)
        risk = RiskEngine(self.settings.risk)
        risk.kill_switch = self.kill_switch_on()
        risk.start_day(ts.date(), self._day_start_equity(mode.value, ts, pf.equity(prices)))
        journal = DBJournal(mode.value, self.engine)
        regime = next((d.context.regime.get("regime") for d in decisions if d.context.regime.get("regime")), None)
        mult = EXPOSURE_MULTIPLIER[Regime(regime)] if regime else 1.0
        signals = self.signals_from_decisions(decisions, pf, prices)
        for d in decisions:
            journal.note(ts, "signal", f"{d.symbol} {d.signal.action} P(up)={d.signal.prob_up:.2f} "
                         f"신뢰도 {d.signal.confidence:.0f} 충돌 {d.signal.conflict}", d.symbol,
                         consensus_id=d.consensus_id, vetoes=d.signal.vetoes)
        fills = ExecutionEngine(broker, risk, journal).rebalance(signals, quotes, ts, mult)
        with session_scope(self.engine) as s:
            snap = pf.snapshot(prices)
            s.add(PortfolioSnapshot(mode=mode.value, ts=ts, **snap))
        return fills

    def _day_start_equity(self, mode: str, ts: datetime, default: float) -> float:
        start = datetime.combine(ts.date(), datetime.min.time(), timezone.utc)
        with session_scope(self.engine) as s:
            snap = s.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode,
                                                            PortfolioSnapshot.ts < start)
                            .order_by(PortfolioSnapshot.ts.desc()))
        return snap.equity if snap else default

    # ================================================================ 복기
    def review(self, day: date | None = None):
        bars, bench, _ = self.market_data()
        with session_scope(self.engine) as s:
            n = resolve(s, bars, bench)
        with session_scope(self.engine) as s:
            report = daily_review(s, day or datetime.now(timezone.utc).date(), memory=self.memory)
            s.flush()
            log.info("채점 %d건, 교훈 %d개", n, len(report.lessons or []))
            return report


def equity_metrics(mode: str, engine) -> dict:
    with session_scope(engine) as s:
        snaps = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode)
                          .order_by(PortfolioSnapshot.ts)).all()
    if not snaps:
        return {}
    eq = pd.Series([x.equity for x in snaps], index=[x.ts for x in snaps])
    return performance(eq)


def latest_consensus(engine, symbol: str) -> ConsensusRecord | None:
    with session_scope(engine) as s:
        return s.scalar(select(ConsensusRecord).where(ConsensusRecord.symbol == symbol)
                        .order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()))

"""대시보드 JSON API (DB 읽기 전용 + 킬스위치)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from ..clock import MARKETS
from ..data.db import load_bars, session_scope
from ..data.models import (
    ConsensusRecord,
    Disclosure,
    Instrument,
    JobRun,
    JournalEntry,
    MacroObservation,
    ModelRecord,
    NewsArticle,
    PortfolioSnapshot,
    PriceBar,
    ReviewReport,
    Scenario,
)
from ..engines.regime import EXPOSURE_MULTIPLIER, Regime, regime_series
from ..ensemble.tracker import CATEGORY_LABELS, scoreboard, scoreboard_table

MACRO_LABELS = {"VIXCLS": "VIX", "DGS10": "美 10년", "DGS2": "美 2년", "DEXKOUS": "달러/원",
                "DCOILWTICO": "WTI", "DFF": "기준금리"}
REGIME_LABELS = {"bull_quiet": "안정적 상승", "bull_volatile": "변동성 상승", "sideways": "횡보",
                 "bear_quiet": "완만한 하락", "bear_volatile": "변동성 하락", "crisis": "위기"}
ANALYST_LABELS = {"primary": "Primary AI", "nvidia": "NVIDIA AI", "quant": "Quant Model",
                  "regime": "Market Regime", "risk": "Risk AI", "challenger": "Challenger"}


def _f(x, nd=4):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) or np.isinf(v) else round(v, nd)


def _ts(x) -> str | None:
    if x is None:
        return None
    t = pd.Timestamp(x)
    t = t.tz_localize("UTC") if t.tz is None else t
    return t.isoformat()


class DashboardAPI:
    def __init__(self, app):
        self.app = app
        self.engine = app.engine
        self._cache: tuple[float, dict] | None = None

    # ------------------------------------------------------------------ 공통
    def _instruments(self, s):
        return {i.symbol: i for i in s.scalars(select(Instrument))}

    def _bars(self, s, symbols):
        return load_bars(s, symbols)

    def _latest_consensus(self, s) -> dict[str, ConsensusRecord]:
        sub = select(ConsensusRecord.symbol, func.max(ConsensusRecord.id).label("mid")).group_by(
            ConsensusRecord.symbol).subquery()
        rows = s.scalars(select(ConsensusRecord).join(sub, ConsensusRecord.id == sub.c.mid)).all()
        return {r.symbol: r for r in rows}

    # ------------------------------------------------------------------ 대시보드
    def dashboard(self) -> dict:
        """여러 탭/사용자가 동시에 열어도 DB 를 두드리지 않도록 10초 캐시."""
        import time
        now = time.monotonic()
        if self._cache and now - self._cache[0] < 10:
            return self._cache[1]
        out = self._dashboard()
        self._cache = (now, out)
        return out

    def _dashboard(self) -> dict:
        app = self.app
        now = datetime.now(UTC)
        with session_scope(self.engine) as s:
            inst = self._instruments(s)
            demo = s.scalar(select(func.count()).select_from(PriceBar).where(PriceBar.source == "synthetic-demo")) > 0
            idx_syms = [k for k, v in inst.items() if v.market == "INDEX"]
            trade_syms = [k for k, v in inst.items() if v.market != "INDEX"]
            bars = self._bars(s, trade_syms + idx_syms)
            cons = self._latest_consensus(s)

            # ---- 지수
            indices = []
            for sym in idx_syms:
                b = bars.get(sym)
                if b is None or len(b) < 2:
                    continue
                c = b["close"]
                indices.append({"symbol": sym, "name": inst[sym].name, "last": _f(c.iloc[-1], 2),
                                "chg": _f(c.iloc[-1] - c.iloc[-2], 2), "chg_pct": _f(c.iloc[-1] / c.iloc[-2] - 1),
                                "spark": [_f(x, 2) for x in c.iloc[-60:]]})

            # ---- 시장 국면
            market = {}
            bench = bars.get(idx_syms[0]) if idx_syms else None
            if bench is not None and len(bench) > 60:
                reg = regime_series(bench).dropna(subset=["regime"])
                if len(reg):
                    last = reg.iloc[-1]
                    r = Regime(last["regime"])
                    # 0~100 시장 심리: 추세 + 변동성 + 낙폭
                    score = 50 + 250 * float(last["trend"]) - 30 * (float(last["vol_pct"]) - 0.5) \
                        + 100 * float(last["drawdown"]) * 0.5
                    score = float(np.clip(score, 0, 100))
                    market = {
                        "regime": r.value, "regime_label": REGIME_LABELS[r.value],
                        "risk_label": "RISK ON" if score >= 60 else ("RISK OFF" if score < 40 else "NEUTRAL"),
                        "score": round(score), "trend": _f(last["trend"]), "vol_pct": _f(last["vol_pct"]),
                        "drawdown": _f(last["drawdown"]), "exposure_multiplier": EXPOSURE_MULTIPLIER[r],
                        "momentum": _f(bench["close"].pct_change(20).iloc[-1]),
                        "support": _f(bench["low"].iloc[-20:].min(), 2),
                        "resistance": _f(bench["high"].iloc[-20:].max(), 2),
                        "history": [[_ts(t), REGIME_LABELS.get(v, v)] for t, v in reg["regime"].iloc[-120:].items()],
                    }

            # ---- 거시
            macro = []
            for sid in s.scalars(select(MacroObservation.series_id).distinct()):
                rows = s.scalars(select(MacroObservation).where(MacroObservation.series_id == sid)
                                 .order_by(MacroObservation.ts.desc()).limit(2)).all()
                if rows:
                    prev = rows[1].value if len(rows) > 1 else rows[0].value
                    macro.append({"id": sid, "label": MACRO_LABELS.get(sid, sid), "last": _f(rows[0].value, 2),
                                  "chg_pct": _f(rows[0].value / prev - 1 if prev else 0)})

            # ---- 관심 종목
            watch = []
            for sym in trade_syms:
                b = bars.get(sym)
                if b is None or len(b) < 2:
                    continue
                c = cons.get(sym)
                watch.append({
                    "symbol": sym, "name": inst[sym].name, "market": inst[sym].market,
                    "currency": inst[sym].currency, "last": _f(b["close"].iloc[-1], 2),
                    "chg_pct": _f(b["close"].iloc[-1] / b["close"].iloc[-2] - 1),
                    "action": c.action if c else None, "prob_up": _f(c.prob_up) if c else None,
                    "confidence": c.confidence if c else None, "conflict": c.conflict if c else None,
                })

            # ---- 신호 / AI 요약
            recent = s.scalars(select(ConsensusRecord).order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc())
                               .limit(12)).all()
            signals = [{"ts": _ts(r.as_of), "symbol": r.symbol, "name": inst[r.symbol].name if r.symbol in inst else r.symbol,
                        "action": r.action, "prob_up": _f(r.prob_up), "confidence": r.confidence} for r in recent]
            review = s.scalar(select(ReviewReport).order_by(ReviewReport.id.desc()))
            summary = []
            if market:
                summary.append(f"시장 국면: {market['regime_label']} ({market['risk_label']}, 노출 배수 {market['exposure_multiplier']})")
            buys = [w["name"] for w in watch if w["action"] == "BUY"]
            vetoed = [c.symbol for c in cons.values() if (c.payload or {}).get("vetoes")]
            if buys:
                summary.append(f"합의 매수 신호: {', '.join(buys[:4])}")
            if vetoed:
                summary.append(f"Risk AI 거부권 발동: {', '.join(inst[v].name for v in vetoed[:4] if v in inst)}")
            high_conf = [c.symbol for c in cons.values() if c.conflict == "high"]
            if high_conf:
                summary.append(f"AI 의견 충돌 높음: {', '.join(inst[v].name for v in high_conf[:4] if v in inst)}")
            if review and review.lessons:
                summary.extend(f"복기: {x}" for x in review.lessons[:2])

            # ---- 뉴스/공시
            news = [{"ts": _ts(n.published_at), "title": n.title, "symbols": n.symbols or [], "kind": "news",
                     "sentiment": _f(n.sentiment, 2), "events": n.events or [], "source": n.source}
                    for n in s.scalars(select(NewsArticle).order_by(NewsArticle.published_at.desc()).limit(30))]
            news += [{"ts": _ts(d.filed_at), "title": d.title, "symbols": [d.symbol] if d.symbol else [],
                      "kind": "disclosure", "sentiment": _f(d.sentiment, 2), "events": d.events or [], "source": "DART"}
                     for d in s.scalars(select(Disclosure).order_by(Disclosure.filed_at.desc()).limit(10))]
            news.sort(key=lambda x: x["ts"] or "", reverse=True)

            # ---- 포트폴리오 / 체결 / 리스크 로그
            portfolios = {m: self._portfolio(s, m, bars, inst) for m in ("paper", "shadow", "live")}
            trades, risk_log = [], []
            for j in s.scalars(select(JournalEntry).where(JournalEntry.kind == "order")
                               .order_by(JournalEntry.ts.desc(), JournalEntry.id.desc()).limit(200)):
                d = j.data or {}
                row = {"ts": _ts(j.ts), "mode": j.mode, "symbol": j.symbol,
                       "name": inst[j.symbol].name if j.symbol in inst else j.symbol,
                       "side": d.get("side"), "qty": d.get("qty"), "price": _f(d.get("fill_price"), 2),
                       "fee": _f(d.get("fee"), 2), "status": d.get("status"), "reasons": d.get("reasons", []),
                       "reason": d.get("reason")}
                (trades if d.get("status") == "filled" else risk_log).append(row)
            for c in cons.values():
                for v in (c.payload or {}).get("vetoes", []):
                    risk_log.append({"ts": _ts(c.as_of), "mode": "ensemble", "symbol": c.symbol,
                                     "name": inst[c.symbol].name if c.symbol in inst else c.symbol,
                                     "side": "buy", "status": "vetoed", "reasons": [v]})
            risk_log.sort(key=lambda x: x["ts"] or "", reverse=True)

            board = scoreboard_table(scoreboard(s))
            models = [{"id": m.id, "name": m.name, "version": m.version, "status": m.status,
                       "created_at": _ts(m.created_at), "notes": m.notes,
                       "sharpe": _f((m.metrics or {}).get("strategy", {}).get("sharpe"), 2),
                       "total_return": _f((m.metrics or {}).get("strategy", {}).get("total_return")),
                       "mdd": _f((m.metrics or {}).get("strategy", {}).get("max_drawdown")),
                       "accuracy": _f((m.metrics or {}).get("prediction", {}).get("accuracy")),
                       "ic": _f((m.metrics or {}).get("prediction", {}).get("ic")),
                       "psr": _f((m.metrics or {}).get("strategy", {}).get("psr"), 3),
                       "dsr": _f((m.metrics or {}).get("dsr"), 3),
                       "sharpe_ci": (m.metrics or {}).get("strategy", {}).get("sharpe_ci95"),
                       "stress_sharpe": _f((m.metrics or {}).get("stress", {}).get("sharpe"), 2),
                       "benchmark_return": _f((m.metrics or {}).get("benchmark", {}).get("total_return")),
                       "calibration": (m.metrics or {}).get("calibration"),
                       "shadow": m.shadow_metrics}
                      for m in s.scalars(select(ModelRecord).order_by(ModelRecord.created_at.desc()).limit(20))]
            last_bar = s.scalar(select(func.max(PriceBar.ts)))

        st = app.settings
        return {
            "demo": demo, "now": now.isoformat(), "mode": st.mode.value, "kill_switch": app.kill_switch_on(),
            "markets": {k: m.phase(now).value for k, m in MARKETS.items()},
            "indices": indices, "market": market, "macro": macro, "ai_summary": summary,
            "summary_time": _ts(recent[0].as_of) if recent else None,
            "watchlist": watch, "signals": signals, "news": news[:30], "portfolios": portfolios,
            "trades": trades[:100], "risk_log": risk_log[:100], "scoreboard": board,
            "category_labels": CATEGORY_LABELS, "analyst_labels": ANALYST_LABELS, "models": models,
            "system": {
                "last_bar": _ts(last_bar),
                "primary": st.primary_model if st.anthropic_enabled else "오프라인 휴리스틱 (ANTHROPIC_API_KEY 없음)",
                "nvidia": st.nvidia_model if st.nvidia_api_key else "오프라인 휴리스틱 (NVIDIA_API_KEY 없음)",
                "embeddings": app.memory.embedder.model,
                "live_enabled": st.live_enabled,
                "risk": st.risk.__dict__,
            },
        }

    def _portfolio(self, s, mode, bars, inst) -> dict:
        snaps = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode)
                          .order_by(PortfolioSnapshot.ts)).all()
        if not snaps:
            return {}
        last = snaps[-1]
        positions = []
        for sym, p in (last.positions or {}).items():
            px = float(bars[sym]["close"].iloc[-1]) if sym in bars and len(bars[sym]) else p["avg_price"]
            val = px * p["qty"]
            positions.append({"symbol": sym, "name": inst[sym].name if sym in inst else sym, "qty": p["qty"],
                              "avg_price": _f(p["avg_price"], 2), "last": _f(px, 2), "value": _f(val, 0),
                              "pnl_pct": _f(px / p["avg_price"] - 1 if p["avg_price"] else 0),
                              "weight": _f(val / last.equity if last.equity else 0)})
        positions.sort(key=lambda x: -(x["value"] or 0))
        first = snaps[0].equity
        return {"cash": _f(last.cash, 0), "equity": _f(last.equity, 0), "ts": _ts(last.ts),
                "return_pct": _f(last.equity / self.app.settings.initial_cash - 1),
                "start_equity": _f(first, 0), "positions": positions,
                "curve": [[_ts(x.ts), _f(x.equity, 0)] for x in snaps[-500:]]}

    # ------------------------------------------------------------------ 차트
    def chart(self, symbol: str, n: int = 260) -> dict:
        with session_scope(self.engine) as s:
            b = self._bars(s, [symbol]).get(symbol)
            cons = s.scalars(select(ConsensusRecord).where(ConsensusRecord.symbol == symbol)
                             .order_by(ConsensusRecord.as_of)).all()
        if b is None:
            return {"bars": []}
        c = b["close"]
        ma = {k: c.rolling(k).mean() for k in (5, 20, 60)}
        b = b.iloc[-n:]
        t = [int(x.timestamp()) for x in b.index]
        out = {
            "bars": [{"time": ti, "open": _f(r.open, 2), "high": _f(r.high, 2), "low": _f(r.low, 2),
                      "close": _f(r.close, 2), "volume": _f(r.volume, 0)} for ti, r in zip(t, b.itertuples())],
            **{f"ma{k}": [{"time": ti, "value": _f(v, 2)} for ti, v in zip(t, ma[k].iloc[-n:]) if not pd.isna(v)]
               for k in ma},
            "markers": [],
        }
        tset = set(t)
        for r in cons:
            ti = int(pd.Timestamp(r.as_of).tz_localize("UTC").timestamp()) if pd.Timestamp(r.as_of).tz is None \
                else int(pd.Timestamp(r.as_of).timestamp())
            if r.action in ("BUY", "SELL") and ti in tset:
                out["markers"].append({"time": ti, "action": r.action, "confidence": r.confidence})
        return out

    # ------------------------------------------------------------------ 종목 분석
    def analysis(self, symbol: str) -> dict:
        with session_scope(self.engine) as s:
            inst = self._instruments(s)
            c = s.scalar(select(ConsensusRecord).where(ConsensusRecord.symbol == symbol)
                         .order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()))
            scen = s.scalar(select(Scenario).where(Scenario.symbol == symbol).order_by(Scenario.id.desc()))
            hist = s.scalars(select(ConsensusRecord).where(ConsensusRecord.symbol == symbol)
                             .order_by(ConsensusRecord.as_of.desc()).limit(30)).all()
            from ..data.models import AnalystOpinionRecord
            ops = s.scalars(select(AnalystOpinionRecord).where(AnalystOpinionRecord.consensus_id == c.id)).all() if c else []
            news = [{"ts": _ts(n.published_at), "title": n.title, "sentiment": _f(n.sentiment, 2),
                     "events": n.events or []}
                    for n in s.scalars(select(NewsArticle).order_by(NewsArticle.published_at.desc()).limit(400))
                    if symbol in (n.symbols or [])][:12]
            similar = self.app.memory.search(s, f"{symbol} {inst[symbol].name if symbol in inst else ''} "
                                             + " ".join(n["title"] for n in news[:3]), k=5,
                                             before=c.as_of if c else None, symbol=symbol)
        details = {}
        for o in ops:
            if o.category == "direction" or o.analyst == "risk":
                details.setdefault(o.analyst, o.payload or {})
        return {
            "symbol": symbol, "name": inst[symbol].name if symbol in inst else symbol,
            "market": inst[symbol].market if symbol in inst else "",
            "consensus": ({**(c.payload or {}), "as_of": _ts(c.as_of), "id": c.id} if c else None),
            "details": details, "scenario": scen.payload if scen else None, "news": news, "similar": similar,
            "history": [{"ts": _ts(h.as_of), "action": h.action, "prob_up": _f(h.prob_up), "confidence": h.confidence,
                         "correct": h.correct, "realized": _f(h.realized_return)} for h in hist],
        }

    # ------------------------------------------------------------------ 운영
    def health(self) -> dict:
        """DB 연결 + 최근 작업 실패 여부. 외부 노출용이므로 상세 정보는 넣지 않는다."""
        try:
            with session_scope(self.engine) as s:
                s.execute(select(1))
                recent_fail = s.scalar(select(func.count()).select_from(JobRun).where(
                    JobRun.ok.is_(False), JobRun.started_at >= datetime.now(UTC) - pd.Timedelta(hours=1)))
            return {"ok": True, "db": "ok", "recent_job_failures": int(recent_fail or 0),
                    "kill_switch": self.app.kill_switch_on()}
        except Exception:  # noqa: BLE001
            return {"ok": False, "db": "error"}

    def ops(self) -> dict:
        from ..analysts.guard import llm_usage_summary
        from ..ops import get_state
        with session_scope(self.engine) as s:
            jobs = {}
            for r in s.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(300)):
                j = jobs.setdefault(r.job, {"job": r.job, "last_run": _ts(r.started_at), "ok": r.ok,
                                             "error": r.error, "runs": 0, "failures": 0})
                j["runs"] += 1
                j["failures"] += int(r.ok is False)
        return {
            "jobs": sorted(jobs.values(), key=lambda x: x["job"]),
            "llm": llm_usage_summary(self.engine),
            "llm_budget_usd": self.app.settings.llm_daily_budget_usd,
            "data_quality": get_state(self.engine, "data_quality"),
            "kill_switch": get_state(self.engine, "kill_switch"),
            "notifications": [{"level": lv, "message": m} for lv, m in self.app.notifier.sent[-20:]][::-1],
            "notifier_enabled": self.app.notifier.enabled,
            "broker": self.app.settings.broker, "kis_env": self.app.settings.kis_env,
        }

    def core_satellite(self) -> dict:
        """코어-위성 현재 계획 + AI 기여도 (가상 장부 3개 비교)."""
        from ..ops import get_state
        mode = self.app.settings.mode.value if self.app.settings.mode.value in ("paper", "shadow", "live") else "paper"
        plan = get_state(self.engine, f"cs-plan:{mode}") or get_state(self.engine, "cs-plan:paper")
        with session_scope(self.engine) as s:
            inst = self._instruments(s)
            books = {}
            for book in ("attr-core", "attr-veto", "attr-full", plan.get("mode", "paper") if plan else "paper"):
                snaps = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == book)
                                  .order_by(PortfolioSnapshot.ts)).all()
                if not snaps:
                    continue
                eq = pd.Series([x.equity for x in snaps], index=pd.DatetimeIndex([x.ts for x in snaps]))
                eq = eq.groupby(eq.index.date).last()
                eq.index = pd.DatetimeIndex(eq.index)
                from ..backtest.backtester import performance
                perf = performance(eq) if len(eq) > 2 else {}
                books[book] = {"curve": [[_ts(t), _f(v, 0)] for t, v in eq.items()],
                               "return": _f(eq.iloc[-1] / eq.iloc[0] - 1), "perf": perf, "days": len(eq)}
            bars = self._bars(s, ["KOSPI"]).get("KOSPI")
        if bars is not None and books:
            first = min(pd.Timestamp(v["curve"][0][0]) for v in books.values())
            k = bars["close"][bars.index >= first.normalize()]
            if len(k) > 1:
                books["kospi"] = {"curve": [[_ts(t), _f(v / k.iloc[0] * 1e7, 0)] for t, v in k.items()],
                                  "return": _f(k.iloc[-1] / k.iloc[0] - 1), "perf": {}, "days": len(k)}
        name = lambda c: inst[c].name if c in inst else c  # noqa: E731
        if plan:
            plan["names"] = {c: name(c) for c in {*plan.get("core", []), *plan.get("vetoed", {}),
                                                  *plan.get("exits", {}), *[x["symbol"] for x in plan.get("satellite", [])],
                                                  *list(plan.get("scores", {}))[:40]}}
        return {"plan": plan, "books": books}

    def research(self) -> dict:
        """가장 최근 실데이터 연구 리포트 (artifacts/research/*.json)."""
        from pathlib import Path
        d = Path(self.app.settings.artifacts_dir) / "research"
        files = sorted(d.glob("*.json")) if d.exists() else []
        if not files:
            return {}
        import json
        return json.loads(files[-1].read_text(encoding="utf-8"))

    def reviews(self, limit: int = 10) -> list[dict]:
        with session_scope(self.engine) as s:
            return [{"date": str(r.review_date), "created_at": _ts(r.created_at), "summary": r.summary,
                     "lessons": r.lessons or []}
                    for r in s.scalars(select(ReviewReport).order_by(ReviewReport.id.desc()).limit(limit))]

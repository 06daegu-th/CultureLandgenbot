"""대시보드 JSON API (DB 읽기 전용 + 킬스위치)."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from .. import ops as _ops
from ..clock import MARKETS
from ..data.db import load_bars, session_scope
from ..data.models import (
    ConsensusRecord,
    Disclosure,
    FillRecord,
    Instrument,
    JobRun,
    JournalEntry,
    MacroObservation,
    ModelRecord,
    NewsArticle,
    OrderRecord,
    PortfolioSnapshot,
    PriceBar,
    ReviewReport,
    Scenario,
)
from ..engines.regime import EXPOSURE_MULTIPLIER, Regime, regime_series
from ..ensemble.tracker import CATEGORY_LABELS, scoreboard, scoreboard_table

log = logging.getLogger(__name__)
GLOBAL_ORDER = ("SP500", "NASDAQCOM", "VIXCLS", "DGS10", "DTWEXBGS", "DEXKOUS", "DCOILWTICO", "DGS2", "DFF")
MACRO_LABELS = {"VIXCLS": "VIX", "DGS10": "美 10년", "DGS2": "美 2년", "DEXKOUS": "달러/원",
                "NASDAQCOM": "나스닥", "SP500": "S&P 500", "DTWEXBGS": "달러지수",
                "DCOILWTICO": "WTI", "DFF": "기준금리"}
REGIME_LABELS = {"bull_quiet": "안정적 상승", "bull_volatile": "변동성 상승", "sideways": "횡보",
                 "bear_quiet": "완만한 하락", "bear_volatile": "변동성 하락", "crisis": "위기"}
from ..analysts.analysts import ROLE_TITLES as ANALYST_LABELS  # noqa: E402

ROLE_DESC = {"primary": "뉴스 · 이벤트 · 선반영", "nvidia": "거시 · 시장 상태 · 해외 연동", "risk": "사지 말아야 할 이유 · 거부권",
             "panel": "공시 · 실적 · 기업 이벤트"}


def ai_roles_info(settings) -> list[dict]:
    """역할별로 어떤 AI(공급자·모델)가 맡는지 — 키 값은 절대 포함하지 않는다."""
    from ..analysts.analysts import AI_ROLES, assign_roles
    from ..analysts.llm_clients import FREE_PROVIDERS
    roles = assign_roles(settings)
    out = []
    for role in AI_ROLES:
        prov = roles.get(role)
        if prov == "claude":
            info = {"provider": "Anthropic Claude", "models": [settings.primary_model], "free": False, "daily": None}
        elif prov:
            cfg = settings.llm_providers[prov]
            info = {"provider": FREE_PROVIDERS[prov].label, "models": list(cfg["models"]), "free": True,
                    "daily": cfg["daily"]}
        else:
            info = {"provider": "오프라인 휴리스틱" if role != "panel" else "사용 안 함 (남는 무료 공급자 없음)",
                    "models": [], "free": True, "daily": None}
        out.append({"role": role, "label": ANALYST_LABELS[role], "desc": ROLE_DESC[role], **info})
    return out


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
        self._risk_cache: dict[str, tuple[float, dict]] = {}

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
            idx_syms = sorted((k for k, v in inst.items() if v.market == "INDEX"), key=lambda k: k != "KOSPI")  # 벤치마크 먼저
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
                    from ..engines.market_intel import load_macro, market_state
                    vix = load_macro(s, ["VIXCLS"]).get("VIXCLS")
                    ms = market_state(bench, {k: v for k, v in bars.items() if k not in idx_syms}, vix=vix)
                    market = {
                        "regime": r.value, "regime_label": REGIME_LABELS[r.value],
                        "risk_label": ms["label"], "type": ms.get("type"), "components": ms.get("components", []),
                        "score": ms["score"], "trend": _f(last["trend"]), "vol_pct": _f(last["vol_pct"]),
                        "drawdown": _f(last["drawdown"]), "exposure_multiplier": EXPOSURE_MULTIPLIER[r],
                        "momentum": _f(bench["close"].pct_change(20).iloc[-1]),
                        "support": _f(bench["low"].iloc[-20:].min(), 2),
                        "resistance": _f(bench["high"].iloc[-20:].max(), 2),
                        "history": [[_ts(t), REGIME_LABELS.get(v, v)] for t, v in reg["regime"].iloc[-120:].items()],
                    }

            # ---- 거시
            macro = []
            ids = list(s.scalars(select(MacroObservation.series_id).distinct()))
            for sid in sorted(ids, key=lambda x: GLOBAL_ORDER.index(x) if x in GLOBAL_ORDER else 99):
                rows = s.scalars(select(MacroObservation).where(MacroObservation.series_id == sid)
                                 .order_by(MacroObservation.ts.desc()).limit(60)).all()[::-1]
                if rows:
                    prev = rows[-2].value if len(rows) > 1 else rows[-1].value
                    macro.append({"id": sid, "label": MACRO_LABELS.get(sid, sid), "last": _f(rows[-1].value, 2),
                                  "chg": _f(rows[-1].value - prev, 3),
                                  "chg_pct": _f(rows[-1].value / prev - 1 if prev else 0),
                                  "rate": sid.startswith(("DGS", "DFF")), "ts": str(rows[-1].ts),
                                  "spark": [_f(r.value, 3) for r in rows]})

            # ---- 관심 종목
            watch = []
            for sym in trade_syms:
                b = bars.get(sym)
                if b is None or len(b) < 2:
                    continue
                c = cons.get(sym)
                tail = b.iloc[-20:]
                watch.append({
                    "_liq": float((tail["close"] * tail["volume"]).mean() or 0) if inst[sym].currency == "KRW" else 0.0,
                    "symbol": sym, "name": inst[sym].name, "market": inst[sym].market,
                    "currency": inst[sym].currency, "last": _f(b["close"].iloc[-1], 2),
                    "chg_pct": _f(b["close"].iloc[-1] / b["close"].iloc[-2] - 1),
                    "action": c.action if c else None, "prob_up": _f(c.prob_up) if c else None,
                    "confidence": c.confidence if c else None, "conflict": c.conflict if c else None,
                })
            # 관심 종목 순서: 보유·위성 → 코어 → 내가 본 종목 → AI 신호 → 나머지(거래대금 큰 순). 많으면 40개만
            from ..ops import get_state
            prio: dict[str, int] = {}
            for m in ("live", "shadow", "paper"):
                snap = s.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == m)
                                .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.id.desc()))
                for sym in (snap.positions or {}) if snap else {}:
                    prio.setdefault(sym, 0)
                plan = get_state(self.engine, f"cs-plan:{m}")
                for x in plan.get("satellite", []):
                    prio.setdefault(x["symbol"], 0)
                for sym in plan.get("core", []):
                    prio.setdefault(sym, 1)
            for sym in reversed(get_state(self.engine, "watch_symbols").get("symbols", [])):
                prio.setdefault(sym, 2)
            watch.sort(key=lambda w: (prio.get(w["symbol"], 3 if w["action"] else 4), -w["_liq"], w["name"] or ""))
            for w in watch:
                w["tier"] = {0: "held", 1: "core", 2: "watched"}.get(prio.get(w["symbol"]), "signal" if w["action"] else None)
                w.pop("_liq")
            all_symbols = [{"symbol": w["symbol"], "name": w["name"], "action": w["action"]} for w in watch]
            watch = watch[:40]

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
            from ..engines.market_intel import cluster_news, news_category
            events_news = cluster_news([n for n in news if n["kind"] == "news"])
            for e in events_news:
                e["kind"] = "news"
            for n in news:
                n["category"] = news_category(n["title"], n.get("events"))
            news_events = sorted(events_news + [{**n, "n_articles": 1} for n in news if n["kind"] == "disclosure"],
                                 key=lambda x: x.get("ts") or "", reverse=True)

            # ---- AI 실시간 분석 피드 (합의 신호 + 핵심 근거)
            ai_feed = []
            for r in recent:
                p = r.payload or {}
                why = (p.get("reasons") or p.get("explanation") or [""])[0]
                ai_feed.append({"id": r.id, "ts": _ts(r.as_of), "symbol": r.symbol,
                                "name": inst[r.symbol].name if r.symbol in inst else r.symbol,
                                "action": r.action, "prob_up": _f(r.prob_up), "confidence": r.confidence,
                                "text": why.split("] ", 1)[-1][:120],
                                "tone": "긍정" if r.prob_up >= 0.55 else "부정" if r.prob_up <= 0.45 else "중립"})

            # ---- 포트폴리오 / 체결 / 리스크 로그
            portfolios = {m: self._portfolio(s, m, bars, inst) for m in ("paper", "shadow", "live")}
            trades, risk_log = [], []
            for j in s.scalars(select(JournalEntry).where(JournalEntry.kind == "order",
                                                          ~JournalEntry.mode.startswith("attr-"))  # 가상 측정 장부 제외
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
        from ..asof import label as _label
        rd = _ops.get_state(self.engine, "readiness")
        return {
            "asof": {"now": _label(now), "last_bar": _label(last_bar, with_time=False),
                     "summary": _label(recent[0].as_of) if recent else None},
            "readiness": {"status": rd.get("status"), "at": rd.get("at"), "as_of": rd.get("as_of"),
                          "checks": [{k: c.get(k) for k in ("key", "title", "status", "detail")} for c in rd.get("checks") or []],
                          "blockers": rd.get("blockers") or []},
            "demo": demo, "now": now.isoformat(), "mode": st.mode.value, "kill_switch": app.kill_switch_on(),
            "halted": _ops.halted(self.engine), "kill_info": _ops.get_state(self.engine, "kill_switch"),
            "markets": {k: m.phase(now).value for k, m in MARKETS.items()},
            "indices": indices, "market": market, "macro": macro, "ai_summary": summary,
            "summary_time": _ts(recent[0].as_of) if recent else None,
            "watchlist": watch, "all_symbols": all_symbols, "signals": signals, "news": news[:30],
            "news_events": news_events[:20], "ai_feed": ai_feed, "events": self._events(),
            "lessons": (review.lessons or [])[:6] if review else [], "portfolios": portfolios,
            "trades": trades[:100], "risk_log": risk_log[:100], "scoreboard": board,
            "category_labels": CATEGORY_LABELS, "analyst_labels": ANALYST_LABELS, "models": models,
            "system": {
                "last_bar": _ts(last_bar),
                "ai_roles": ai_roles_info(st),
                "broker": st.broker, "kis_env": st.kis_env, "core_only": st.core_only,
                "initial_cash": st.initial_cash,
                "embeddings": app.memory.embedder.model,
                "live_enabled": st.live_enabled,
                "risk": st.risk.__dict__,
            },
        }

    def _events(self) -> list[dict]:
        """예정 경제 이벤트 (국가 · 중요도)."""
        out = []
        now = datetime.now(UTC)
        for e in self.app.load_events():
            ts = pd.Timestamp(e["ts"])
            ts = ts.tz_localize("UTC") if ts.tz is None else ts
            if ts < now - pd.Timedelta(days=2):
                continue
            name = e.get("name", "")
            country = e.get("country") or ("US" if any(k in name for k in ("FOMC", "미국", "US", "NFP", "CPI"))
                                           else "KR" if any(k in name for k in ("한국", "한은", "금통위", "KOSPI"))
                                           else "")
            out.append({"ts": ts.isoformat(), "name": name, "importance": e.get("importance"), "country": country,
                        "symbol": e.get("symbol")})
        out += self._stock_events(now)
        return sorted(out, key=lambda x: x["ts"])[:12]

    def _stock_events(self, now: datetime) -> list[dict]:
        """이미 열어 본 종목(캐시된 종목 상세)의 실적 발표·배당락 일정 — 30일 이내. 네트워크 호출 없음."""
        from ..data.fundamentals import with_d_day
        from ..data.models import SystemState
        out = []
        with session_scope(self.engine) as s:
            rows = s.scalars(select(SystemState).where(SystemState.key.like("profile:%"))).all()
            names = {i.symbol: i.name for i in s.scalars(select(Instrument))}
            prof = [(r.key.split(":", 1)[1], (r.value or {}).get("data") or {}) for r in rows]
        for sym, p in prof:
            for e in with_d_day([e for e in p.get("events") or [] if e.get("kind") in ("earnings", "ex_div")]):
                if not e["past"] and e["d_day"] <= 30:
                    out.append({"ts": pd.Timestamp(f"{e['date']}T00:00:00+09:00").tz_convert("UTC").isoformat(), "name": f"{names.get(sym) or sym} {e['label']}"
                                + (" (예상)" if e.get("estimated") else ""), "importance": 0.9 if e["kind"] == "earnings" else 0.6,
                                "country": "KR" if sym.isdigit() else "US", "symbol": sym, "d_label": e["d_label"]})
        return out

    def _portfolio(self, s, mode, bars, inst) -> dict:
        snaps = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.mode == mode)
                          .order_by(PortfolioSnapshot.ts, PortfolioSnapshot.id)).all()
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
        self.ensure_symbol(symbol)  # 해외 종목: 처음 볼 때 무료 일봉 받기
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
        fetched = self.ensure_symbol(symbol)
        if re.fullmatch(r"\d{6}|[A-Z][A-Z.\-]{0,9}", symbol or ""):
            from ..actions import add_watch
            add_watch(self.app, symbol)  # 본 종목은 매일 AI 판단·알림 대상에 포함 (해외는 미국 사이클에서)
        with session_scope(self.engine) as s:
            inst = self._instruments(s)
            c = s.scalar(select(ConsensusRecord).where(ConsensusRecord.symbol == symbol)
                         .order_by(ConsensusRecord.as_of.desc(), ConsensusRecord.id.desc()))
            scen = s.scalar(select(Scenario).where(Scenario.symbol == symbol).order_by(Scenario.id.desc()))
            hist = s.scalars(select(ConsensusRecord).where(ConsensusRecord.symbol == symbol)
                             .order_by(ConsensusRecord.as_of.desc()).limit(30)).all()
            from ..data.models import AnalystOpinionRecord
            ops = s.scalars(select(AnalystOpinionRecord).where(AnalystOpinionRecord.consensus_id == c.id)).all() if c else []
            terms = self._news_terms(symbol, inst[symbol].name if symbol in inst else None)
            news = [{"ts": _ts(n.published_at), "title": n.title, "sentiment": _f(n.sentiment, 2),
                     "events": n.events or [], "source": n.source, "url": n.url}
                    for n in s.scalars(select(NewsArticle).order_by(NewsArticle.published_at.desc()).limit(600))
                    if symbol in (n.symbols or []) or any(t in (n.title or "").lower() for t in terms)][:15]
            discs = [{"date": str(d.filed_at), "title": d.title, "summary": d.summary, "url": d.url}
                     for d in s.scalars(select(Disclosure).where(Disclosure.symbol == symbol)
                                        .order_by(Disclosure.filed_at.desc(), Disclosure.id.desc()).limit(8))]
            similar = self.app.memory.search(s, f"{symbol} {inst[symbol].name if symbol in inst else ''} "
                                             + " ".join(n["title"] for n in news[:3]), k=5,
                                             before=c.as_of if c else None, symbol=symbol)
        details = {}
        for o in ops:
            if o.category == "direction" or o.analyst == "risk":
                details.setdefault(o.analyst, o.payload or {})
        with session_scope(self.engine) as s:
            b = self._bars(s, [symbol]).get(symbol)
        return {
            "checklist": self._checklist(c, b), "range": self._range(b, (c.payload or {}).get("horizon", 5) if c else 5),
            "symbol": symbol, "name": inst[symbol].name if symbol in inst else symbol,
            "market": inst[symbol].market if symbol in inst else "",
            "last": _f(b["close"].iloc[-1], 2) if b is not None and len(b) else None,
            "chg": _f(b["close"].iloc[-1] - b["close"].iloc[-2], 2) if b is not None and len(b) > 1 else None,
            "chg_pct": _f(b["close"].iloc[-1] / b["close"].iloc[-2] - 1) if b is not None and len(b) > 1 else None,
            "last_ts": _ts(b.index[-1]) if b is not None and len(b) else None,
            "currency": inst[symbol].currency if symbol in inst else "",
            "fetch": fetched if fetched.get("error") or fetched.get("source") else None,
            "has_llm": self.app.settings.has_llm,
            "consensus": ({**(c.payload or {}), "as_of": _ts(c.as_of), "id": c.id} if c else None),
            "details": details, "scenario": scen.payload if scen else None, "news": news, "similar": similar,
            "disclosures": discs,
            "history": [{"ts": _ts(h.as_of), "action": h.action, "prob_up": _f(h.prob_up), "confidence": h.confidence,
                         "correct": h.correct, "realized": _f(h.realized_return)} for h in hist],
        }

    @staticmethod
    def _news_terms(symbol: str, name: str | None) -> list[str]:
        """제목으로 관련 뉴스 찾기: 종목명 · 한글 별칭 · (해외) 티커·영문명. 너무 짧은 말은 오탐이 많아 뺀다."""
        from ..data.global_stocks import GLOBAL_STOCKS, KR_NICKNAMES
        terms = {(name or "").lower()}
        for sym, ko, aliases in GLOBAL_STOCKS:
            if sym == symbol:
                terms |= {ko.lower(), *(a.lower() for a in aliases)}
                if len(sym) >= 3:
                    terms.add(sym.lower())
        terms |= {k.lower() for k, v in KR_NICKNAMES.items() if name and v == name}
        return [t for t in terms if len(t) >= 2 and not t.isdigit() and t not in {"spy", "arm", "meta", "비자", "우버"}]

    @staticmethod
    def _checklist(c, b) -> list[dict]:
        """AI 종합 분석 체크리스트: 기술 · 뉴스 · 수급(거래량) · 거시 — 각 항목 판정과 근거."""
        if b is None or len(b) < 60:
            return []
        close, vol = b["close"], b["volume"]
        ret20 = float(close.iloc[-1] / close.iloc[-21] - 1)
        ma20, ma60 = float(close.iloc[-20:].mean()), float(close.iloc[-60:].mean())
        d = close.diff()
        up, dn = d.clip(lower=0).iloc[-14:].mean(), (-d.clip(upper=0)).iloc[-14:].mean()
        rsi = float(100 - 100 / (1 + up / dn)) if dn > 0 else 100.0
        tech = "상승" if close.iloc[-1] > ma20 > ma60 else "하락" if close.iloc[-1] < ma20 < ma60 else "혼조"
        vr = float(vol.iloc[-5:].mean() / vol.iloc[-60:].mean()) if vol.iloc[-60:].mean() > 0 else 1.0
        p = (c.payload or {}) if c else {}
        ev = p.get("evidence") or {}
        fl = ev.get("flow") or {}
        news = ev.get("news") or []
        ns = [x.get("sentiment") for x in news if x.get("sentiment") is not None]
        nscore = float(np.mean(ns)) if ns else None
        mk = ev.get("market") or {}
        cross = ev.get("cross_asset") or []
        return [
            {"key": "기술", "ok": tech == "상승", "tone": tech,
             "text": f"{tech} 추세 · 20일 {ret20:+.1%} · RSI {rsi:.0f}"},
            {"key": "뉴스", "ok": (nscore or 0) > 0.1, "tone": "긍정" if (nscore or 0) > 0.1 else "부정" if (nscore or 0) < -0.1 else "중립",
             "text": (f"이벤트 {len(news)}건 · 평균 감성 {nscore:+.2f}" if nscore is not None else "관련 뉴스 없음")},
            ({"key": "수급", "ok": (fl.get("foreign_5d") or 0) + (fl.get("inst_5d") or 0) > 0,
              "tone": "유입" if (fl.get("foreign_5d") or 0) + (fl.get("inst_5d") or 0) > 0 else "유출",
              "text": f"5일 외국인 {fl.get('foreign_5d') or 0:+,.0f}주 · 기관 {fl.get('inst_5d') or 0:+,.0f}주"
                      + (f" · 외국인 {abs(fl['foreign_streak'])}일 연속 {'순매수' if fl['foreign_streak'] > 0 else '순매도'}"
                         if fl.get("foreign_streak") else "")}
             if fl.get("foreign_5d") is not None else
             {"key": "수급", "ok": vr >= 1.1 and ret20 > 0, "tone": "유입" if vr >= 1.1 else "보통" if vr >= 0.8 else "감소",
              "text": f"5일 거래량 60일 평균의 {vr:.1f}배 (외국인·기관 수급은 장 마감 후 수집되면 표시)"}),
            {"key": "거시", "ok": mk.get("label") == "RISK ON", "tone": mk.get("label") or "-",
             "text": (f"시장 {mk.get('label')} {mk.get('score')}점" if mk else "시장 상태 없음")
             + (f" · {cross[0]['name']} 상관 {cross[0]['corr']:+.2f}" if cross else "")},
        ]

    @staticmethod
    def _range(b, horizon: int = 5) -> dict | None:
        """변동성 기준 예상 범위 (목표가 예측이 아니다): horizon 거래일 1σ 범위와 2σ 손절선."""
        if b is None or len(b) < 30:
            return None
        last = float(b["close"].iloc[-1])
        sig = float(np.log(b["close"]).diff().iloc[-60:].std() * np.sqrt(horizon))
        return {"last": _f(last, 2), "horizon": horizon, "sigma": _f(sig),
                "upper": _f(last * np.exp(sig), 0), "lower": _f(last * np.exp(-sig), 0),
                "stop": _f(last * np.exp(-2 * sig), 0)}

    # ------------------------------------------------------------------ 근거 · 저널 · 보정 · 성적 · 리스크 · 주문
    def evidence(self, consensus_id: int) -> dict:
        from ..review.evidence import evidence_chain
        with session_scope(self.engine) as s:
            return evidence_chain(s, consensus_id) or {"error": "not found"}

    def journal(self, symbol: str | None = None, limit: int = 60, action: str | None = None) -> dict:
        from ..review.evidence import ai_journal, no_trade_value
        with session_scope(self.engine) as s:
            rows = ai_journal(s, limit=limit, symbol=symbol or None,
                              actions=tuple(action.split(",")) if action else None)
            return {"rows": rows, "no_trade": no_trade_value(s)}

    def calibration(self) -> dict:
        from ..ensemble.calibration import calibration_report
        from ..ops import get_state
        with session_scope(self.engine) as s:
            return calibration_report(s, fitted=get_state(self.engine, "calibration"))

    def ai_scoreboard(self) -> dict:
        from ..ensemble.tracker import provider_scoreboard
        with session_scope(self.engine) as s:
            rows = provider_scoreboard(s)
        return {"rows": rows, "roles": ai_roles_info(self.app.settings), "labels": CATEGORY_LABELS}

    def risk(self, mode: str | None = None) -> dict:
        """전체 시세를 읽어 계산하므로 장부별 60초 캐시."""
        import time
        key = mode or "_"
        hit = self._risk_cache.get(key)
        if hit and time.monotonic() - hit[0] < 60:
            return hit[1]
        try:
            out = self.app.portfolio_risk(mode, with_ruin=True)
        except Exception as e:  # noqa: BLE001 - 대시보드는 계속 떠야 한다
            return {"error": str(e)}
        self._risk_cache[key] = (time.monotonic(), out)
        return out

    def orders(self, mode: str | None = None, limit: int = 200) -> dict:
        with session_scope(self.engine) as s:
            inst = self._instruments(s)
            q = select(OrderRecord).order_by(OrderRecord.created_at.desc(), OrderRecord.id.desc()).limit(limit)
            if mode:
                q = q.where(OrderRecord.mode == mode)
            else:
                q = q.where(~OrderRecord.mode.startswith("attr-"))
            recs = list(s.scalars(q))
            fills: dict[int, float] = {}
            for oid, fp in s.execute(select(FillRecord.order_id, FillRecord.price).where(
                    FillRecord.order_id.in_([r.id for r in recs]))):
                fills[oid] = fp
            for r in recs:  # 예전 기록은 체결가를 체결 테이블에서
                if r.avg_price is None and r.id in fills:
                    r.avg_price = fills[r.id]
            s.expunge_all()
            rows = [{"id": r.id, "ts": _ts(r.created_at), "mode": r.mode, "symbol": r.symbol,
                     "name": inst[r.symbol].name if r.symbol in inst else r.symbol, "side": r.side, "qty": r.qty,
                     "status": r.status, "reason": r.reason, "limit_price": _f(r.limit_price, 2),
                     "ref_price": _f(r.ref_price, 2), "avg_price": _f(r.avg_price, 2), "filled_qty": r.filled_qty,
                     "client_order_id": r.client_order_id, "broker_order_id": r.broker_order_id,
                     "consensus_id": r.consensus_id,
                     "slippage_bps": _f((r.avg_price - r.ref_price) / r.ref_price * 1e4 * (1 if r.side == "buy" else -1), 2)
                     if r.avg_price and r.ref_price else None}
                    for r in recs]
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"rows": rows, "counts": counts,
                "slippage": {m: self.app.slippage_stats(m) for m in ("live", "shadow", "paper")}}

    # ------------------------------------------------------------------ Net Alpha · 분석 · 안전 (무거운 것은 캐시)
    def _cached(self, key: str, ttl: float, fn):
        import time
        hit = self._risk_cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        try:
            out = fn()
        except Exception as e:  # noqa: BLE001 - 대시보드는 계속 떠야 한다
            log.exception("API %s 실패", key)
            return {"error": f"{type(e).__name__}: {e}"}
        self._risk_cache[key] = (time.monotonic(), out)
        return out

    def net_alpha(self, mode: str | None = None, market: str = "KR") -> dict:
        from ..analytics import net_alpha_report
        market = "US" if (market or "").upper() == "US" else "KR"
        return self._cached(f"net_alpha:{mode}:{market}", 60, lambda: net_alpha_report(self.app, mode, market))

    def guardian(self) -> dict:
        # 화면 조회는 판정만 (정지 실행은 스케줄러의 guardian 작업과 매매 사이클이 한다)
        return self._cached("guardian", 20, lambda: self.app.guardian(act=False))

    def analytics(self, kind: str, mode: str | None = None) -> dict:
        from .. import analytics as an
        fns = {"execution": lambda: an.execution_quality(self.app, mode),
               "counterfactual": lambda: an.counterfactual(self.app),
               "events": lambda: an.event_reactions(self.app),
               "confidence": lambda: an.data_confidence(self.app),
               "stress": lambda: an.stress_test(self.app, mode),
               "experiments": lambda: self.experiments(),
               "ai_verdict": lambda: self.app.ai_verdict(),
               "champion": lambda: {**self.app.check_champion(rollback=False),
                                    "events": _ops.get_state(self.engine, "model_events").get("events", [])[::-1][:20]}}
        if kind not in fns:
            return {"error": "unknown"}
        return self._cached(f"an:{kind}:{mode}", 60, fns[kind])

    def experiments(self) -> dict:
        """실험 레지스트리: 모델 후보(게이트 결과·DSR) + 실데이터 연구 리포트의 시험 수. 다중검정 보정 근거."""
        import json
        from pathlib import Path

        from ..data.models import ModelRecord
        with session_scope(self.engine) as s:
            models = [{"kind": "model", "name": r.name, "version": r.version, "created_at": _ts(r.created_at),
                       "status": r.status, "notes": r.notes,
                       "metrics": {k: (r.metrics or {}).get(k) for k in ("dsr", "n_trials")} | {
                           k: ((r.metrics or {}).get("strategy") or {}).get(k) for k in ("sharpe", "max_drawdown", "cagr")},
                       "shadow": r.shadow_metrics}
                      for r in s.scalars(select(ModelRecord).order_by(ModelRecord.created_at.desc()).limit(100))]
        research = []
        d = Path(self.app.settings.artifacts_dir) / "research"
        for f in sorted(d.glob("*.json"))[-30:] if d.exists() else []:
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            trials = j.get("trials") or j.get("configs") or []
            research.append({"kind": j.get("kind", "research"), "file": f.name, "created_at": j.get("created_at"),
                             "n_trials": len(trials) if isinstance(trials, list) else j.get("n_trials"),
                             "dev_end": j.get("dev_end"), "data": j.get("data")})
        n_total = len(models) + sum(r["n_trials"] or 0 for r in research)
        return {"models": models, "research": research[::-1], "n_trials_total": n_total,
                "note": "시험을 많이 할수록 우연히 좋아 보이는 결과가 늘어난다 → DSR(다중검정 보정 샤프)로 승격 기준을 올린다"}

    # ------------------------------------------------------------------ 검색 · 해외 종목
    def search(self, q: str) -> dict:
        from ..data.global_stocks import search
        with session_scope(self.engine) as s:
            res = search(s, q, 10)
            cons = self._latest_consensus(s)
        for r in res:
            c = cons.get(r["symbol"])
            r["action"] = c.action if c else None
        return {"results": res}

    # ------------------------------------------------------------------ 검증실 · 장부 · 드리프트
    def verify(self) -> dict:
        """독립 평가(야간 결과) + 장부 무결성(지금 다시 확인) + 드리프트 + 모델 계보."""
        from ..review.ledger import verify as ledger_verify

        def build():
            with session_scope(self.engine) as s:
                led = ledger_verify(s)
            ev = _ops.get_state(self.engine, "evaluation")
            models = []
            with session_scope(self.engine) as s:
                for m in s.scalars(select(ModelRecord).order_by(ModelRecord.created_at.desc()).limit(12)):
                    p = m.params or {}
                    models.append({"id": m.id, "name": m.name, "version": m.version, "status": m.status,
                                   "created_at": _ts(m.created_at), "reason": p.get("reason"), "parent": p.get("parent"),
                                   "train_end": p.get("train_end"), "embargo": p.get("embargo"),
                                   "sharpe": _f((m.metrics or {}).get("strategy", {}).get("sharpe"), 2),
                                   "dsr": _f((m.metrics or {}).get("dsr"), 3)})
            from ..analysts.analysts import PROMPT_VERSIONS, ROLE_TITLES
            return {"evaluation": ev or None, "ledger": led, "drift": _ops.get_state(self.engine, "drift") or None,
                    "models": models, "model_events": _ops.get_state(self.engine, "model_events").get("events", [])[-10:][::-1],
                    "prompts": [{"role": k, "title": ROLE_TITLES.get(k, k), "version": v} for k, v in PROMPT_VERSIONS.items()]}
        return self._cached("verify", 20, build)

    def ledger(self, before: int | None = None) -> dict:
        from ..review.ledger import entries
        with session_scope(self.engine) as s:
            names = {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument))}
            return {"rows": entries(s, 60, before, names)}

    # ------------------------------------------------------------------ 지식 그래프 · 섹터 · 에이전트
    def graph(self, symbol: str | None = None) -> dict:
        from ..engines.knowledge import ego
        g = _ops.get_state(self.engine, "kgraph")
        if not g:
            return {"nodes": {}, "edges": [], "stats": None, "message": "아직 그래프가 없습니다 — 장 마감 후 자동으로 만들어집니다"}
        if symbol:
            return {**ego(g, symbol, 12), "stats": g.get("stats"), "at": g.get("at")}
        top = g.get("edges", [])[:160]
        keep = {x for e in top for x in (e["a"], e["b"])}
        return {"nodes": {k: v for k, v in g.get("nodes", {}).items() if k in keep}, "edges": top, "stats": g.get("stats"), "at": g.get("at")}

    def agents(self) -> dict:
        return {k: _ops.get_state(self.engine, k) or None for k in ("news_digest", "macro_brief", "sector_view")} | {
            "sector_map_size": len(_ops.get_state(self.engine, "sector_map").get("map", {}))}

    # ------------------------------------------------------------------ 알림 규칙 · 푸시 · 외부 알림 · 리포트
    def rules(self) -> dict:
        from ..alerts import RULE_KINDS, active_rules
        with session_scope(self.engine) as s:
            names = {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument))}
        return {"rules": [{**r, "name": names.get(r["symbol"], r["symbol"])} for r in active_rules(self.engine)],
                "kinds": RULE_KINDS}

    def rule_write(self, body: dict) -> dict:
        from ..alerts import add_rule, update_rule
        if body.get("id"):
            return update_rule(self.engine, int(body["id"]), body.get("active"), bool(body.get("delete")))
        sym = str(body.get("symbol", ""))[:12]
        if not re.fullmatch(r"\d{6}|[A-Z][A-Z.\-]{0,9}", sym):
            raise ValueError("종목 코드가 올바르지 않습니다")
        return add_rule(self.engine, sym, str(body.get("kind", "")), float(body.get("value") or 0),
                        str(body.get("note") or "")[:200] or None, bool(body.get("repeat")))

    def push_info(self) -> dict:
        from ..webpush import SUBS_KEY, available, vapid_keys
        if not available():
            return {"available": False, "message": "cryptography 미설치 — ./run.sh 를 다시 실행하세요"}
        return {"available": True, "public_key": vapid_keys(self.app.settings.artifacts_dir)["public"],
                "subscriptions": len(_ops.get_state(self.engine, SUBS_KEY).get("subs", []))}

    def push_write(self, path: str, body: dict) -> dict:
        from ..webpush import send, subscribe, unsubscribe
        if path.endswith("/subscribe"):
            return subscribe(self.engine, body.get("subscription") or {}, str(body.get("label", "")))
        if path.endswith("/unsubscribe"):
            return unsubscribe(self.engine, str((body.get("subscription") or {}).get("endpoint", "")))
        return send(self.engine, self.app.settings.artifacts_dir, "Quant AI 푸시 테스트",
                    "이 알림이 보이면 휴대폰·PC 푸시 연결 성공입니다", "#settings")

    def notify_test(self) -> dict:
        return self.app.notifier.test()

    def reports(self) -> dict:
        from ..reports import list_reports
        return {"reports": list_reports(self.app)}

    def report(self, file: str) -> dict:
        import json as _json

        from ..reports import report_dir
        if not re.fullmatch(r"(morning|daily)-\d{4}-\d{2}-\d{2}\.html", file or ""):
            raise ValueError("잘못된 리포트 이름")
        p = report_dir(self.app) / file.replace(".html", ".json")
        if not p.exists():
            raise ValueError("리포트 없음")
        return _json.loads(p.read_text(encoding="utf-8"))

    def alerts(self, after: int = 0) -> dict:
        from ..alerts import recent
        return recent(self.engine, after=max(0, int(after or 0)), limit=60)

    def quotes(self) -> dict:
        """최근 실시간 시세 (급등락 감시가 받아 둔 값 · 화면 가격 깜빡임용)."""
        st = _ops.get_state(self.engine, "live_quotes")
        return {"at": st.get("_at"), "quotes": {k: {kk: vv for kk, vv in v.items() if kk != "hist"}
                                                 for k, v in st.items() if not k.startswith("_") and isinstance(v, dict)}}

    def ladder(self) -> dict:
        return self._cached("ladder", 30, lambda: self.app.ladder(act=False))

    def control(self) -> dict:
        from ..control import control
        return self._cached("control", 15, lambda: control(self.app))

    def scorecard(self, market: str | None = None, n: int = 100) -> dict:
        """예측 성적표 (최근 n 회): 적중률 · 기대/실제 수익 · 비용 후 · MDD · 보정 · 국면별 · 틀린 이유."""
        from ..review.scorecard import scorecard
        market = market if market in ("KR", "US") else None
        n = max(10, min(int(n or 100), 1000))

        def build():
            with session_scope(self.engine) as s:
                names = {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument))}
                return scorecard(s, market, n, names=names)
        return self._cached(f"scorecard:{market}:{n}", 30, build)

    def profile(self, symbol: str, refresh: bool = False) -> dict:
        """종목 상세 (토스식): 다가오는 일정 D-day · 핵심 지표 · 애널리스트 · 실적 · 기업 정보 · 뉴스."""
        import re as _re

        from ..data.fundamentals import stock_profile
        if not _re.fullmatch(r"[A-Za-z0-9.\-]{1,12}", symbol or ""):
            return {"error": "잘못된 종목 코드"}
        with session_scope(self.engine) as s:
            inst = s.scalar(select(Instrument).where(Instrument.symbol == symbol))
            if inst is not None and inst.market == "INDEX":
                return {"symbol": symbol, "events": [], "skipped": "지수"}
            b = self._bars(s, [symbol]).get(symbol)
        try:
            p = stock_profile(self.engine, symbol, refresh=refresh)
        except Exception as e:  # noqa: BLE001 - 상세가 없어도 분석 화면은 떠야 한다
            log.warning("종목 상세 %s 실패: %s", symbol, e)
            return {"symbol": symbol, "events": [], "errors": [str(e)[:200]], "sources": []}
        st = dict(p.get("stats") or {})
        if b is not None and len(b):
            st["price"] = float(b["close"].iloc[-1])  # 화면의 차트와 같은 값으로
            st["price_ts"] = _ts(b.index[-1])
            if len(b) >= 2:
                st["chg_pct"] = float(b["close"].iloc[-1] / b["close"].iloc[-2] - 1)
            if st.get("high52") is None and len(b) >= 200:
                st["high52"], st["low52"] = float(b["high"].iloc[-250:].max()), float(b["low"].iloc[-250:].min())
        if st.get("price") and st.get("high52") and st.get("low52") and st["high52"] > st["low52"]:
            st["pos52"] = (st["price"] - st["low52"]) / (st["high52"] - st["low52"])
        a = dict(p.get("analyst") or {})
        if a.get("target_mean") and st.get("price"):
            a["upside"] = a["target_mean"] / st["price"] - 1
        flow = _ops.get_state(self.engine, f"flow:{symbol}")
        com = _ops.get_state(self.engine, f"community:{symbol}")
        return {**p, "stats": st, "analyst": a, "name": inst.name if inst else symbol,
                "market": inst.market if inst else "",
                "community": {**{k: com.get(k) for k in ("at", "source", "n", "bull", "bear", "mood", "label")},
                              "posts": (com.get("posts") or [])[:6]} if com.get("at") else None,
                "flow": {"at": flow.get("at"), "rows": flow.get("rows", [])[-20:], "summary": flow.get("summary")}
                if flow.get("at") else None}

    def ensure_symbol(self, symbol: str) -> dict:
        """해외 종목이면 무료 일봉을 받아 캐시 (처음 한 번 · 12시간마다 갱신)."""
        from ..data.global_stocks import GLOBAL_MARKET, GLOBAL_STOCKS, ensure_global, global_name
        with session_scope(self.engine) as s:
            inst = s.scalar(select(Instrument).where(Instrument.symbol == symbol))
            market = inst.market if inst else None
        if market == GLOBAL_MARKET or (market is None and any(symbol == g[0] for g in GLOBAL_STOCKS)):
            return ensure_global(self.engine, symbol, global_name(symbol))
        return {"ok": market is not None}

    # ------------------------------------------------------------------ 준비 상태 · 서버 · 동작 · 채팅
    def setup(self) -> dict:
        from ..actions import setup_status
        return self._cached("setup", 15, lambda: setup_status(self.app))

    def server(self) -> dict:
        from ..actions import server_status
        return self._cached("server", 10, lambda: server_status(self.app))

    def db_preview(self) -> dict:
        from ..actions import db_maintenance
        return db_maintenance(self.app, dry_run=True)

    def action(self, name: str, start: bool = False, params: dict | None = None) -> dict:
        from ..actions import get_action, start_action
        if start:
            self._cache = None
            self._risk_cache.clear()
            return start_action(self.app, name, params)
        return get_action(name)

    def chat(self, body: dict) -> dict:
        from ..assistant import reply
        return reply(self.app, str(body.get("message", "")), body.get("sid"))

    def chat_history(self, sid: str) -> dict:
        from ..assistant import chat_client, history
        client, prov = chat_client(self.app.settings)
        return {"messages": history(self.engine, sid), "provider": prov,
                "model": client.models[0] if client else None}

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
                                  .order_by(PortfolioSnapshot.ts, PortfolioSnapshot.id)).all()
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
        return {"plan": plan, "books": books, "health": self.strategy_health(mode)}

    def strategy_health(self, mode: str | None = None, refresh: bool = False) -> dict:
        """저장된 최근 건강검진 (없거나 refresh 면 계산 — IC 제외, 빠름)."""
        from ..ops import get_state
        mode = mode or (self.app.settings.mode.value if self.app.settings.mode.value in ("paper", "shadow", "live")
                        else "paper")
        h = get_state(self.engine, f"strategy_health:{mode}")
        if h and not refresh:
            return h
        try:
            return self.app.strategy_health(mode, with_ic=refresh, notify=False)
        except Exception as e:  # noqa: BLE001 - 대시보드는 계속 떠야 한다
            return {"status": "insufficient", "error": str(e), "checks": []}

    def order_sheet(self, body: dict) -> dict:
        """대시보드 주문표: {cash, holdings: "005930,10\n..." | {코드: 수량}, use_ai}."""
        from ..strategy.order_sheet import parse_holdings
        h = body.get("holdings") or {}
        holdings = parse_holdings(h) if isinstance(h, str) else {str(k): int(v) for k, v in h.items()}
        try:
            cash = float(body.get("cash") or 0)
        except (TypeError, ValueError) as e:
            raise ValueError("현금은 숫자로 입력하세요") from e
        if cash < 0 or len(holdings) > 300:
            raise ValueError("현금은 0 이상, 보유 종목은 300개 이하")
        sheet = self.app.order_sheet(holdings, cash, use_ai=bool(body.get("use_ai")))
        return {**sheet.to_dict(), "csv": sheet.to_csv()}

    def research(self) -> dict:
        """가장 최근 실데이터 연구 리포트 (artifacts/research/*.json)."""
        from pathlib import Path
        d = Path(self.app.settings.artifacts_dir) / "research"
        files = sorted(d.glob("*.json")) if d.exists() else []
        if not files:
            return {}
        import json
        return json.loads(files[-1].read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ v13
    def freshness(self) -> dict:
        from ..asof import freshness
        return self._cached("freshness", 20, lambda: freshness(self.app))

    def readiness(self, mode: str | None = None) -> dict:
        from .. import desk
        st = _ops.get_state(self.engine, "readiness")
        if st.get("at") and (datetime.now(UTC) - datetime.fromisoformat(st["at"])).total_seconds() < 300 and not mode:
            from ..asof import freshness
            return st | {"freshness": freshness(self.app), "cached": True}
        return desk.readiness(self.app, mode)

    def calendar(self) -> dict:
        from .. import desk
        st = _ops.get_state(self.engine, "event_calendar")
        if not st.get("at") or (datetime.now(UTC) - datetime.fromisoformat(st["at"])).total_seconds() > 3600:
            st = desk.event_calendar(self.app)
        from ..clock import CALENDAR_SOURCE
        return st | {"impact": _ops.get_state(self.engine, "event_impact"), "calendar_source": CALENDAR_SOURCE}

    def power(self) -> dict:
        from .. import desk
        return self._cached("power", 60, lambda: desk.prediction_power(self.app, store=False)
                            | {"decay": _ops.get_state(self.engine, "model_decay"),
                               "drift": {k: v for k, v in _ops.get_state(self.engine, "drift").items() if k != "features"}
                               | {"features": _ops.get_state(self.engine, "drift").get("features")},
                               "batch_ab": _ops.get_state(self.engine, "batch_ab")})

    def execution(self) -> dict:
        from ..trading.kis_ws import BOOKS
        return {"kis": _ops.get_state(self.engine, "kis_validation"), "slippage": _ops.get_state(self.engine, "slippage_model"),
                "parity": _ops.get_state(self.engine, "execution_parity"), "ws": _ops.get_state(self.engine, "kis_ws"),
                "books": {k: {x: v for x, v in b.items() if x != "_t"} for k, b in list(BOOKS.items())[:10]}
                or _ops.get_state(self.engine, "orderbook"),
                "costs": {"assumed": self.app.settings.costs.__dict__}, "broker": self.app.settings.broker,
                "kis_env": self.app.settings.kis_env}

    def desk(self, symbol: str) -> dict:
        from .. import desk
        return self._cached(f"desk:{symbol}", 30, lambda: desk.stock_desk(self.app, symbol))

    def rotation(self) -> dict:
        from .. import desk
        from ..engines.sector import QUADRANTS
        wm = _ops.get_state(self.engine, "wics_map")
        return self._cached("rotation", 300, lambda: {"rows": desk.rotation(self.app), "quadrants": list(QUADRANTS.values()),
                                                       "wics": {"n": len(wm.get("map") or {}), "at": wm.get("at"), "day": wm.get("day"),
                                                                "error": wm.get("error")},
                                                       "extract": {k: v for k, v in _ops.get_state(self.engine, "event_extract").items()
                                                                   if k != "events"} | {"events": (_ops.get_state(self.engine, "event_extract").get("events") or [])[:60]}})

    def pipeline_status(self) -> dict:
        from .. import desk
        from ..recovery import source_health
        return {"sources": source_health(self.app), "recovery": _ops.get_state(self.engine, "recovery"),
                "heartbeat": _ops.get_state(self.engine, "heartbeat"), "notary": _ops.get_state(self.engine, "notary"),
                "fx": self._cached("fx", 300, lambda: desk.fx(self.app))}

    def accounts(self) -> dict:
        from .. import accounts
        return self._cached("accounts", 15, lambda: accounts.summary(self.app))

    def accounts_write(self, body: dict) -> dict:
        from .. import accounts
        self._risk_cache.pop("accounts", None)
        if body.get("delete"):
            return {"deleted": accounts.delete(self.engine, str(body["delete"])[:20])}
        return accounts.upsert(self.engine, body)

    def my_journal(self) -> dict:
        from .. import desk
        return desk.my_journal(self.app)

    def my_journal_write(self, body: dict) -> dict:
        from .. import desk
        return desk.my_journal_add(self.app, body)

    def reviews(self, limit: int = 10) -> list[dict]:
        with session_scope(self.engine) as s:
            return [{"date": str(r.review_date), "created_at": _ts(r.created_at), "summary": r.summary,
                     "lessons": r.lessons or []}
                    for r in s.scalars(select(ReviewReport).order_by(ReviewReport.id.desc()).limit(limit))]

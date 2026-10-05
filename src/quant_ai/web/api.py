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

AI_ICON = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡", "NO_TRADE": "⚪"}

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
        # v26: 10초~3분 지난 값은 바로 돌려주고 뒤에서 새로 계산 (화면을 열 때마다 1~2초 기다리지 않게)
        if self._cache and now - self._cache[0] < 180:
            if not getattr(self, "_dash_busy", False):
                import threading
                self._dash_busy = True

                def bg():
                    try:
                        self._cache = (time.monotonic(), self._dashboard())
                    except Exception as e:  # noqa: BLE001 - 다음 요청이 다시 시도
                        log.info("대시보드 새로 계산 실패: %s", e)
                    finally:
                        self._dash_busy = False
                threading.Thread(target=bg, name="dash-refresh", daemon=True).start()
            return self._cache[1]
        out = self._dashboard()
        self._cache = (time.monotonic(), out)
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
            starred = set(get_state(self.engine, "starred").get("symbols", []))
            for sym in starred:
                prio[sym] = min(prio.get(sym, 0), 0)  # 별표 관심종목은 보유와 같은 맨 앞
            for sym in reversed(get_state(self.engine, "watch_symbols").get("symbols", [])):
                prio.setdefault(sym, 2)
            watch.sort(key=lambda w: (prio.get(w["symbol"], 3 if w["action"] else 4), w["symbol"] not in starred, -w["_liq"], w["name"] or ""))
            for w in watch:
                w["tier"] = {0: "held", 1: "core", 2: "watched"}.get(prio.get(w["symbol"]), "signal" if w["action"] else None)
                w["starred"] = w["symbol"] in starred
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
        if positions:  # v18: 보유종목 옆 AI 판단 (NVDA 🟢 BUY)
            held = [p["symbol"] for p in positions]
            sub = select(ConsensusRecord.symbol, func.max(ConsensusRecord.id).label("mid")).where(
                ConsensusRecord.symbol.in_(held)).group_by(ConsensusRecord.symbol).subquery()
            ai = {c.symbol: c for c in s.scalars(select(ConsensusRecord).join(sub, ConsensusRecord.id == sub.c.mid))}
            for p in positions:
                c = ai.get(p["symbol"])
                p["ai"] = None if c is None else {"action": c.action, "icon": AI_ICON.get(c.action, "⚪"),
                                                  "prob_up": _f(c.prob_up), "at": _ts(c.as_of)}
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

    @staticmethod
    def _display_name(symbol: str, inst: dict) -> str:
        """화면에 보일 종목 이름 — 해외 종목은 'NVDA' 대신 '엔비디아'처럼 한글 이름을 먼저."""
        from ..data.global_stocks import global_name
        n = inst[symbol].name if symbol in inst else None
        if not n or n.upper() == symbol.upper() or not re.search(r"[가-힣]", n):
            n = global_name(symbol) or n
        return n or symbol

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
            "symbol": symbol, "name": self._display_name(symbol, inst),
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
        from ..companies import identity
        for r in res:
            c = cons.get(r["symbol"])
            r["action"] = c.action if c else None
            idt = identity(None, r["symbol"], r.get("name"))  # v23: 검색 결과에도 같은 신분증 (거래소·영문명·로고)
            r["exchange"], r["name_en"], r["logo"] = idt["exchange"], idt["name_en"], idt["logo"]
        return {"results": res}

    def company(self, symbol: str) -> dict:
        from ..companies import identity
        if not symbol:
            return {"error": "symbol 필요"}
        return identity(self.app, symbol)

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

    def ledger(self, before: int | None = None, symbol: str | None = None, result: str | None = None) -> dict:
        from ..review.ledger import entries
        with session_scope(self.engine) as s:
            names = {i.symbol: i.name or i.symbol for i in s.scalars(select(Instrument))}
            return {"rows": entries(s, 60, before, names, symbol or None, result if result in ("fail", "success") else None)}

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
        self._audit("alert_rule", str({k: body.get(k) for k in ("symbol", "kind", "value", "delete") if k in body}))
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
        from ..governance import source_allowed
        if not (source_allowed(self.app.settings, "yahoo") and source_allowed(self.app.settings, "naver")):
            cached = (_ops.get_state(self.engine, f"profile:{symbol}").get("data") or {})  # 상용 모드: 새로 받지 않고 저장분만
            return {"symbol": symbol, "events": cached.get("events") or [], "stats": cached.get("stats") or {},
                    "sources": [], "license_blocked": "상용 모드 — Yahoo·네이버 종목 상세는 상용 계약 전까지 새로 받지 않음"}
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
        from ..keys import refresh
        if refresh(self.app).get("changed"):  # .env 를 고쳤으면 캐시를 버리고 바로 보여준다
            self._risk_cache.pop("setup", None)
        out = self._cached("setup", 15, lambda: setup_status(self.app))
        inst = self.instance()
        return out | {"server": {k: inst[k] for k in ("version", "root", "env_file")}}

    def news_board(self, days: int = 3, only: str = "", symbol: str = "") -> dict:
        from ..board import news_board
        if only not in ("", "mine", "긍정", "부정", "중립"):
            raise ValueError("only 는 mine / 긍정 / 부정 / 중립")
        d = max(1, min(int(days), 14))
        return self._cached(f"nb:{d}:{only}:{symbol}", 60, lambda: news_board(self.app, d, only or None, symbol or None))

    def market_map(self) -> dict:
        from ..board import market_map
        return self._cached("market_map", 60, lambda: market_map(self.app))

    def news_extract(self) -> dict:
        from ..news_llm import extract_pending
        self._risk_cache = {k: v for k, v in self._risk_cache.items() if not k.startswith("nb:")}
        return extract_pending(self.app)

    def pead(self) -> dict:
        from .. import pead
        return self._cached("pead", 120, lambda: pead.report(self.app))

    def us_sheet(self, body: dict) -> dict:
        from ..usorder import sheet

        def nums(d, cast):
            try:
                return {str(k).upper()[:10]: cast(v) for k, v in (d or {}).items() if str(v).strip() != ""}
            except (TypeError, ValueError):
                raise ValueError("수량·비중·평단은 숫자로") from None
        h = body.get("holdings") or {}
        if isinstance(h, str):  # "AAPL,10\nMSFT,5"
            h = dict(x.split(",", 1) for x in h.strip().splitlines() if "," in x)
        t = body.get("targets")
        if isinstance(t, str) and t.strip():
            t = dict(x.split(",", 1) for x in t.strip().splitlines() if "," in x)
        try:
            cu, ck, yg = float(body.get("cash_usd") or 0), float(body.get("cash_krw") or 0), float(body.get("ytd_gain_krw") or 0)
        except (TypeError, ValueError):
            raise ValueError("현금·올해 실현 이익은 숫자로") from None
        px = body.get("prices")
        if isinstance(px, str):
            px = dict(x.split(",", 1) for x in px.strip().splitlines() if "," in x)
        ac = body.get("avg_cost")
        if isinstance(ac, str):  # "AAPL,170" 또는 "AAPL,170,1300"(취득 환율)
            rows = [[y.strip() for y in x.split(",")] for x in ac.strip().splitlines() if "," in x]
            try:
                ac = {r[0].upper()[:10]: (float(r[1]), float(r[2]) if len(r) > 2 and r[2] else None) for r in rows if r[0] and r[1]}
            except ValueError:
                raise ValueError("평단은 '종목,평단USD' 또는 '종목,평단USD,취득환율' 숫자로") from None
        elif ac:
            ac = nums(ac, float)
        return sheet(self.app, nums(h, lambda v: int(float(v))), cu, ck, nums(t, float) if t else None,
                     ac or None, yg, nums(px, float) if px else None)

    def replay(self, d: str) -> dict:
        from datetime import date as _date

        from ..replay import day
        try:
            dd = _date.fromisoformat(d) if d else datetime.now(UTC).date() - pd.Timedelta(days=1)
        except ValueError:
            raise ValueError("날짜는 YYYY-MM-DD") from None
        return self._cached(f"replay:{dd}", 120, lambda: day(self.app, dd))

    def home5(self, mode: str | None = None) -> dict:
        from ..center import home5
        m = self._mode(mode)
        return self._cached(f"home5:{m}", 60, lambda: home5(self.app, m))

    def verdict(self, symbol: str) -> dict:
        """종목 첫 화면: AI 최종 판단 하나 + 52주 위치 + 이 종목 시장의 지금 상태."""
        from ..clock import clock_status
        from ..explain import verdict
        sym = symbol.strip().upper()[:12]

        def build():
            v = verdict(self.app, sym)
            b = self.app._all_bars()[0].get(sym)
            if b is None and not sym[:1].isdigit():
                from .. import global_market
                b = global_market.market_data(self.app, extra=[sym])[0].get(sym)
            if b is not None and len(b) >= 20:
                c = b["close"].astype(float).iloc[-252:]
                hi, lo, last = float(c.max()), float(c.min()), float(c.iloc[-1])
                v["range52"] = {"high": round(hi, 2), "low": round(lo, 2), "last": round(last, 2), "n": len(c),
                                "pos": round((last - lo) / (hi - lo), 3) if hi > lo else None,
                                "from_high": round(last / hi - 1, 4), "from_low": round(last / lo - 1, 4) if lo else None}
            m = clock_status(datetime.now(UTC))["markets"]["KRX" if sym[:1].isdigit() else "US"]
            v["market"] = {"flag": m.get("flag"), "name": m.get("short"), "light": m.get("light"), "notice": m.get("notice"),
                           "state": "장중" if m["phase"] == "open" else (m["session_label"] if m["trading_day"] else "휴장"),
                           "local_time": m.get("local_time"), "tz": m.get("tz"), "next": m.get("next_event"),
                           "next_kst": m.get("next_close_kst") if m.get("next_event") == "폐장" else m.get("next_open_kst"),
                           "seconds_to_next": m.get("seconds_to_next"), "dst": m.get("dst"),
                           "holiday": m.get("holiday") if not m["trading_day"] else None}
            # v23 종목 머리: 기업 신분증 · 실적 D-day · 내 보유 (한 번에 — 화면이 여러 번 묻지 않게)
            from .. import ux
            from ..companies import identity
            v["identity"] = identity(self.app, sym)
            r = next((x for x in _ops.get_state(self.engine, "event_calendar").get("risk") or [] if x.get("symbol") == sym), None)
            e = (r or {}).get("earnings") or {}
            v["earnings"] = {"d_label": e.get("d_label"), "trading_days": e.get("trading_days"), "date": e.get("date"),
                             "estimated": e.get("estimated"), "timing": e.get("timing")} if e else None
            try:
                rows = [x for x in (ux.holdings(self.app, sym).get("rows") or []) if x.get("qty")]
            except Exception:  # noqa: BLE001 - 보유 정보가 없어도 판단은 보인다
                rows = []
            if rows:
                top = max(rows, key=lambda x: x.get("value") or 0)
                v["holding"] = {"qty": sum(x["qty"] for x in rows), "avg_price": top.get("avg_price"), "pnl_pct": top.get("pnl_pct"),
                                "book": top.get("book"), "n_books": len(rows)}
            return v
        return self._cached(f"verdict:{sym}", 30, build)

    # ------------------------------------------------------------------ v25 토스식 화면
    def t_home(self, mode: str = "paper") -> dict:
        from .. import toss
        mode = mode if mode in ("paper", "shadow", "live") else "paper"
        return self._cached(f"t_home:{mode}", 30, lambda: toss.home(self.app, mode))

    def t_stock(self, symbol: str) -> dict:
        from .. import toss
        sym = symbol.strip().upper()[:12] if not symbol.strip()[:1].isdigit() else symbol.strip()[:12]
        if not sym:
            raise ValueError("symbol 필요")
        return self._cached(f"t_stock:{sym}", 20, lambda: toss.stock(self.app, sym))

    def t_feed(self, tab: str = "all", region: str = "all", topic: str = "all") -> dict:
        from .. import toss
        tab = tab if tab in ("all", "news", "disc", "event") else "all"
        region = region if region in ("all", "kr", "us") else "all"
        topic = topic if topic in ("all", "ai", "semi", "mine") else "all"
        return self._cached(f"t_feed:{tab}:{region}:{topic}", 60, lambda: toss.feed(self.app, tab, region, topic))

    def t_portfolio(self, mode: str = "paper") -> dict:
        from .. import toss
        mode = mode if mode in ("paper", "shadow", "live", "us-paper") else "paper"
        return self._cached(f"t_pf:{mode}", 20, lambda: toss.portfolio(self.app, mode))

    def t_community(self, symbol: str) -> dict:
        from .. import toss
        sym = symbol.strip().upper()[:12] if not symbol.strip()[:1].isdigit() else symbol.strip()[:12]
        if not sym:
            raise ValueError("symbol 필요")
        return toss.community(self.app, sym)

    def t_collect(self) -> dict:
        from .. import toss
        return self._cached("t_collect", 30, lambda: toss.collect_status(self.app))

    def signals2(self, market: str = "KR") -> dict:
        """v28 신호 엔진 2.0: 매수 후보 · 비중 축소 후보 · 피할 종목 (+ 신호별 근거 · 점수대별 과거 결과 · 전진 기록)."""
        from .. import signals2 as S2
        m = "US" if market.upper() == "US" else "KR"
        out = S2.cached(self.app, m)
        return {k: v for k, v in out.items() if k != "_rows"}

    def datacheck(self, run: bool = False) -> dict:
        """v29 데이터 정합성 점검 (최신성 · 자동 갱신 · 원천 자체 정합성 · 외부 시세 대조). 저장된 결과가 없으면 한 번 실행."""
        from .. import datacheck as DC
        from .. import ops as _ops
        if run:
            self._audit("datacheck", "데이터 점검 실행")
            return DC.run(self.app)
        return _ops.get_state(self.app.engine, DC.STATE_KEY) or DC.run(self.app)

    def signals2_stock(self, symbol: str) -> dict:
        from .. import signals2 as S2
        sym = symbol.strip()
        sym = sym if sym[:1].isdigit() else sym.upper()
        if not sym:
            raise ValueError("symbol 필요")
        return S2.for_symbol(self.app, sym)

    def proof_status(self) -> dict:
        """v29 증명 프로젝트: 규칙 · 성공 기준 · 매일 봉인 기록 · 체인 검증."""
        from .. import proof
        return proof.status(self.app)

    def proof_write(self, body: dict) -> dict:
        from .. import proof
        act = str(body.get("action") or "")
        if act == "start":
            try:
                r = proof.start(self.app, str(body.get("mode") or "live"), float(body.get("principal") or 0) or None,
                                float(body.get("max_loss") or 0) or None, bool(body.get("public")), bool(body.get("show_amounts")))
            except (TypeError, ValueError) as e:
                raise ValueError(str(e) or "원금·최대 손실을 숫자로") from None
            self._risk_cache.clear()
            self._audit("proof_start", f"{r['id']} · {r['mode']} · 원금 {r['params']['principal']:,.0f}")
            return {"ok": True, "project": r}
        if act == "end":
            r = proof.end(self.app, str(body.get("reason") or "")[:200])
            self._audit("proof_end", f"{r['id']} · {r.get('end_reason', '')}")
            return {"ok": True, "project": {k: v for k, v in r.items() if k != "log"}}
        if act == "public":
            r = proof.set_public(self.app, bool(body.get("public")), body.get("show_amounts") if "show_amounts" in body else None)
            self._audit("proof_public", f"공개 {'켬' if r['public'] else '끔'} · 금액 {'공개' if r.get('show_amounts') else '비공개'}")
            return {"ok": True, "project": r}
        if act == "record":
            return {"ok": True, "record": proof.record_day(self.app, force=True)}
        raise ValueError("action 은 start / end / public / record")

    def logo_upload(self, body: dict) -> dict:
        """v27: 화면에서 종목 로고 직접 넣기·지우기 (body: symbol, data = base64 또는 data: URL, 비우면 지움)."""
        import base64
        import binascii

        from ..logos import save_custom
        raw = str(body.get("data") or "")
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[-1]
        try:
            data = base64.b64decode(raw, validate=True) if raw else b""
        except (binascii.Error, ValueError):
            raise ValueError("이미지 데이터가 깨졌어요") from None
        out = save_custom(self.app, str(body.get("symbol") or ""), data)
        from ..governance import audit
        audit(self.engine, "logo_upload", f"{out['symbol']} {'삭제' if out.get('removed') else out.get('type')}")
        return out

    def t_intraday(self, symbol: str) -> dict:
        """v27: 종목 화면 '1일' — 5분봉 (Yahoo · 1분 저장). 받지 못하면 이유와 함께 빈 목록."""
        from ..data.collectors.indices import intraday
        sym = symbol.strip()
        sym = sym if sym[:1].isdigit() else sym.upper()
        if not sym:
            raise ValueError("symbol 필요")
        market = None
        if sym.isdigit():
            from ..companies import master
            c = master().get(sym)
            market = getattr(c, "market", None) or getattr(c, "exchange", None)
        return intraday(sym, market)

    def t_market(self) -> dict:
        from .. import toss
        return self._cached("t_market", 60, lambda: toss.market(self.app))

    def t_quotes(self, symbols: str) -> dict:
        from .. import toss
        syms = [x.strip().upper() if not x.strip()[:1].isdigit() else x.strip() for x in symbols.split(",") if x.strip()][:20]
        return self._cached(f"t_q:{','.join(syms)}", 20, lambda: toss.quotes(self.app, syms))

    def t_report(self, symbol: str) -> dict:
        from .. import toss
        sym = symbol.strip().upper()[:12] if not symbol.strip()[:1].isdigit() else symbol.strip()[:12]
        if not sym:
            raise ValueError("symbol 필요")
        return self._cached(f"t_rep:{sym}", 30, lambda: toss.report(self.app, sym))

    def t_alerts(self) -> dict:
        from .. import toss
        return toss.alerts(self.app)

    def t_alerts_write(self, body: dict) -> dict:
        from .. import toss
        self._audit("alert_settings", str({k: body.get(k) for k in ("kind", "on", "symbol", "price_on", "value")})[:200])
        return toss.alerts_write(self.app, body)

    def ai_trust(self) -> dict:
        """v24 AI 신뢰 센터 — 한 화면에 모은다: 지금 믿을 만한가(3단계) · 단계(사다리) · 실제 전진 기록 성적 vs 기준선 ·
        독립 평가 결론 · 예측 장부 봉인 상태 · 자동 강등 · 틀린 이유 Top · 상황별 성적. 각 칸은 실패해도 나머지는 보인다."""
        def build():
            from ..center import ai_state
            out: dict = {}

            def safe(k, fn):
                try:
                    out[k] = fn()
                except Exception as e:  # noqa: BLE001
                    out[k] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
            safe("state", lambda: ai_state(self.app))
            safe("ladder", lambda: {k: v for k, v in self.app.ladder(act=False).items() if k in ("stage", "changed", "reasons", "ready", "next")})
            ev = _ops.get_state(self.engine, "evaluation") or {}
            out["evaluation"] = {k: ev.get(k) for k in ("verdict", "status", "n", "hit_rate", "best_baseline", "p_value", "sealed_share", "evaluated_at")} if ev else None

            def ledger_():
                from ..review.ledger import verify as ledger_verify
                with session_scope(self.engine) as s:
                    r = ledger_verify(s, sample_limit=5)
                return {k: r.get(k) for k in ("ok", "sealed", "legacy", "pending")}
            safe("ledger", ledger_)
            fl = _ops.get_state(self.engine, "failure_lab") or {}
            out["failures"] = {"n": fl.get("n"), "findings": (fl.get("findings") or [])[:4], "message": fl.get("message")} if fl else None
            safe("context", lambda: self.ai_context())
            return out
        return self._cached("ai_trust", 120, build)

    def ai_context(self) -> dict:
        from ..scorecard import by_context
        return self._cached("ai_context", 300, lambda: by_context(self.app))

    def ai_plain(self, symbol: str = "") -> dict:
        from ..scorecard import plain
        sym = symbol.strip().upper()[:12]
        return self._cached(f"ai_plain:{sym}", 120, lambda: plain(self.app, 100, sym or None))

    def ops_status(self) -> dict:
        """상단 상태 표시용 — 24시간 운영 · 데이터 날짜 · 뉴스 · 업종 · 작업 실패 (center.ops_status)."""
        from ..center import ops_status
        return self._cached("ops_status", 60, lambda: ops_status(self.app))

    def baseline(self, mode: str = "paper") -> dict:
        """코어(이 시스템) vs '그냥 지수 ETF 를 샀다면' — 16년 연구 + 실제 장부 그림자 비교."""
        from .. import baseline
        mode = mode if mode in ("paper", "live", "shadow") else "paper"
        return self._cached(f"baseline:{mode}", 300, lambda: baseline.compare(self.app, mode))

    def goal(self, q: dict | None = None) -> dict:
        """저장된 목표(또는 화면에서 바꿔 본 값)로 확률 계획 + 진행률."""
        from .. import goal
        g = goal.get(self.app)
        q = {k: v for k, v in (q or {}).items() if v not in (None, "")}
        inp = {"principal": 5_000_000, "monthly": 500_000, "goal": 100_000_000, "target_years": 10, "strategy": "core", "raise_pct": 0.0} | \
              {k: g[k] for k in ("principal", "monthly", "goal", "target_years", "strategy", "raise_pct") if k in g}
        for k in ("principal", "monthly", "goal", "raise_pct"):
            if k in q:
                inp[k] = float(str(q[k]).replace(",", ""))
        if "target_years" in q:
            inp["target_years"] = int(q["target_years"])
        if "strategy" in q:
            inp["strategy"] = str(q["strategy"])
        key = "goal:" + ":".join(str(inp[k]) for k in sorted(inp))
        p = self._cached(key, 600, lambda: goal.plan(**inp))
        return p | {"saved": g or None, "progress": goal.progress(self.app), "presets": goal.PRESETS, "etfs": goal.ETFS}

    def goal_save(self, body: dict) -> dict:
        from .. import goal
        g = goal.save(self.app, body)
        self._risk_cache = {k: v for k, v in self._risk_cache.items() if not k.startswith(("goal", "home5"))}
        self._audit("goal", f"목표 {g['goal']:,.0f} · 월 {g['monthly']:,.0f}" + (f" · 적립 {'켬' if g.get('dca', {}).get('on') else '끔'}" if g.get("dca") else ""))
        return {"ok": True, "saved": g}

    def start_guide(self) -> dict:
        from ..center import start_guide
        return self._cached("start_guide", 10, lambda: start_guide(self.app))

    def oneline(self, mode: str | None = None) -> dict:
        from ..center import oneline
        m = self._mode(mode)
        return self._cached(f"oneline:{m}", 60, lambda: oneline(self.app, m))

    def news_detail(self, news_id: str) -> dict:
        from ..newsdetail import news_detail
        try:
            return news_detail(self.app, int(news_id))
        except ValueError:
            raise ValueError("뉴스 번호가 이상합니다") from None

    def news_explain(self, body: dict) -> dict:
        from ..newsdetail import explain_news
        self._audit("news_explain", f"뉴스 {body.get('id')}")
        return explain_news(self.app, int(body.get("id") or 0))

    def disclosure_detail(self, disc_id: str) -> dict:
        from ..newsdetail import disclosure_detail
        try:
            return disclosure_detail(self.app, int(disc_id))
        except ValueError:
            raise ValueError("공시 번호가 이상합니다") from None

    def disclosure_explain(self, body: dict) -> dict:
        from ..newsdetail import explain_disclosure
        self._audit("disclosure_explain", f"공시 {body.get('id')}")
        return explain_disclosure(self.app, int(body.get("id") or 0))

    def news_search(self, q: str, days: str = "30") -> dict:
        from ..newsdetail import search
        try:
            d = int(days or 30)
        except ValueError:
            d = 30
        return {"q": q, "days": d, "results": search(self.app, q[:100], d)}

    def conflicts(self) -> dict:
        from ..conflicts import detect
        return self._cached("conflicts", 120, lambda: detect(self.app))

    def weekly(self) -> dict:
        from ..center import weekly_schedule
        return self._cached("weekly", 300, lambda: weekly_schedule(self.app))

    def budget(self, principal: str = "", max_loss: str = "", on_stop: str = "") -> dict:
        from .. import budget
        cur = budget.get(self.app)
        out = {"saved": cur or None, "current": {"live_max_capital": self.app.settings.live_max_capital,
                                                 "live_small_capital": self.app.settings.live_small_capital,
                                                 **{k: getattr(self.app.settings.risk, k) for k in ("max_daily_loss_pct", "max_position_weight",
                                                                                                    "max_order_value", "max_var95", "max_sector_weight")}}}
        if principal and max_loss:
            try:
                vol, src = budget.effective_vol(self.app)
                out["preview"] = budget.plan(float(principal), float(max_loss), daily_vol=vol, on_stop=on_stop or "hold")
                out["preview"]["vol_source"] = src
            except (TypeError, ValueError) as e:
                raise ValueError(str(e)) from None
        mode = self.app.settings.mode.value if self.app.settings.mode.value in ("paper", "shadow", "live") else "paper"
        out["usage"] = budget.total_loss(self.app, mode)
        p = out.get("preview") or cur
        out["replay"] = self._cached(f"budget_replay:{p.get('loss_pct')}:{(p.get('limits') or {}).get('max_daily_loss_pct')}", 300,
                                     lambda: budget.replay(self.app, p)) if p.get("limits") else None
        out["policies"] = budget.STOP_POLICIES
        out["stop_sheet"] = _ops.get_state(self.app.engine, f"stop_sheet:{mode}") or None
        out["cashflows"] = self.app.cashflows(mode)[-10:]
        return out

    def budget_write(self, body: dict) -> dict:
        from .. import budget
        try:
            p = budget.save(self.app, body)
        except (TypeError, ValueError) as e:
            raise ValueError(str(e) or "원금·최대 손실을 숫자로") from None
        self._risk_cache.clear()
        self._audit("budget", f"원금 {p['principal']:,} · 최대 손실 {p['max_loss']:,}")
        return {"ok": True, **p}

    def netcheck(self, run: bool = False) -> dict:
        from .. import netcheck
        if run:
            self._audit("netcheck", "외부 연결 점검 실행")
            return netcheck.run(self.app)
        return netcheck.last(self.app) or {"rows": [], "headline": "아직 점검 안 함 — [연결 점검] 을 누르세요"}

    def keys(self) -> dict:
        from ..keys import diagnose, refresh
        refresh(self.app)
        return diagnose(self.app)

    def keys_reload(self) -> dict:
        from ..keys import diagnose, refresh
        r = refresh(self.app, force=True)
        self._risk_cache.pop("setup", None)
        self._audit("keys_reload", ", ".join(r.get("changed") or []) or "변경 없음")
        return {"changed": r.get("changed") or [], **diagnose(self.app)}

    def keys_probe(self, body: dict) -> dict:
        from ..keys import probe, refresh
        refresh(self.app, force=True)
        which = body.get("which") or None
        if which not in (None, "dart", "fred", "ecos"):
            raise ValueError("which 는 dart / fred / ecos")
        self._risk_cache.pop("setup", None)
        return probe(self.app, which)

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
        st = get_action(name)
        if name.startswith("analyze:") and not st.get("running"):  # 분석이 끝나면 종목 첫 화면 판단을 바로 새로
            self._risk_cache.pop(f"verdict:{name.split(':', 1)[1].upper()}", None)
        return st

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

    def instance(self) -> dict:
        """이 서버가 '어느 폴더의 어느 버전' 코드인지 — run.sh 가 옛 버전 서버를 재사용하지 않게 (로컬 요청에만 공개)."""
        import hashlib
        import os
        from pathlib import Path

        from .. import __version__
        from ..keys import env_file
        root = Path(__file__).resolve().parents[3]  # src/quant_ai/web/api.py → 프로젝트 폴더
        f = env_file()
        return {"version": __version__, "instance": hashlib.sha256(os.path.realpath(root).encode()).hexdigest()[:12],
                "pid": os.getpid(), "root": str(root), "env_file": str(f) if f else None}

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

    def clock(self) -> dict:
        from ..clock import clock_status
        return clock_status(datetime.now(UTC))

    def truth(self, refresh: bool = False) -> dict:
        from .. import truth
        return self._cached("truth", 0 if refresh else 60, lambda: truth.report(self.app))

    def explain(self, symbol: str) -> dict:
        from ..explain import for_symbol
        return for_symbol(self.app, symbol) or {"error": "판단 기록 없음"}

    def checklist(self) -> dict:
        from ..checklist import evaluate
        return self._cached("checklist", 60, lambda: evaluate(self.app))

    def stock(self, symbol: str, mode: str = "paper") -> dict:
        from .. import stock
        if mode not in ("paper", "shadow", "live"):
            raise ValueError("장부는 paper/shadow/live")
        return self._cached(f"stock:{symbol}:{mode}", 30, lambda: stock.page(self.app, symbol, mode))

    def pretrade(self, symbol: str, weight: str = "", mode: str = "paper") -> dict:
        from .. import stock
        try:
            w = float(weight) / 100 if weight else None
        except ValueError:
            raise ValueError("비중은 숫자(%)로") from None
        if w is not None and not 0 < w <= 1:
            raise ValueError("비중은 0~100% 사이")
        if mode not in ("paper", "shadow", "live"):
            raise ValueError("장부는 paper/shadow/live")
        return stock.pretrade(self.app, symbol, w, mode)

    def ai_health(self) -> dict:
        from ..health import ai_health
        return self._cached("ai_health", 60, lambda: ai_health(self.app))

    def prefs(self) -> dict:
        from .. import prefs
        from ..alerts import ROUTE
        return prefs.get(self.engine) | {"kinds": list(prefs.KINDS), "home_cards": list(prefs.HOME_CARDS),
                                         "channels": {"external": ROUTE.get("notifier") is not None, "push": ROUTE.get("push") is not None},
                                         "defaults": {"external": sorted(ROUTE["kinds"]), "push": sorted(ROUTE["push_kinds"])}}

    def prefs_write(self, body: dict) -> dict:
        from .. import prefs
        self._audit("prefs", ", ".join(sorted(k for k in body))[:200])
        return {"ok": True, **prefs.save(self.engine, body)}

    def today(self) -> dict:
        from .. import ux
        return self._cached("today", 30, lambda: ux.today(self.app))

    def holdings(self, symbol: str) -> dict:
        from .. import ux
        return ux.holdings(self.app, symbol) | {"track": ux.track(self.app, symbol)}

    def compare(self, symbols: str) -> dict:
        from .. import ux
        return ux.compare(self.app, symbols.split(","))

    def star(self, body: dict) -> dict:
        from .. import ux
        sym = str(body.get("symbol", ""))[:12]
        syms = ux.set_star(self.app, sym, bool(body.get("on", True)))
        if body.get("on", True) and sym:  # v19: 관심종목에 담는 순간 로고를 미리 받아 둔다 (화면이 느려지지 않게)
            import threading

            from ..logos import prefetch
            threading.Thread(target=lambda: prefetch(self.app, [sym]), daemon=True, name="logo-prefetch").start()
        self._risk_cache = {k: v for k, v in self._risk_cache.items()
                            if k not in ("watchlist", "today", "start_guide") and not k.startswith("home5:")}
        return {"ok": True, "starred": syms}

    def starred(self) -> dict:
        from .. import ux
        return {"starred": ux.starred(self.app)}

    def accounts(self) -> dict:
        from .. import accounts
        return self._cached("accounts", 15, lambda: accounts.summary(self.app))

    def accounts_write(self, body: dict) -> dict:
        from .. import accounts
        self._risk_cache.pop("accounts", None)
        self._audit("accounts", f"삭제 {body['delete']}" if body.get("delete") else f"저장 {body.get('name') or body.get('id') or ''}")
        if body.get("delete"):
            return {"deleted": accounts.delete(self.engine, str(body["delete"])[:20])}
        return accounts.upsert(self.engine, body)

    def my_journal(self) -> dict:
        from .. import desk
        return desk.my_journal(self.app)

    def my_journal_write(self, body: dict) -> dict:
        from .. import desk
        return desk.my_journal_add(self.app, body)

    # ------------------------------------------------------------------ v16
    def _mode(self, mode: str | None) -> str:
        mode = mode or "paper"
        if mode not in ("paper", "shadow", "live"):
            raise ValueError("장부는 paper/shadow/live")
        return mode

    def _audit(self, action: str, detail: str = "") -> None:
        from ..governance import audit
        try:
            audit(self.engine, action, detail)
        except Exception as e:  # noqa: BLE001 - 감사 기록 실패가 조작을 막지는 않는다 (로그)
            log.warning("감사 로그 실패: %s", e)

    def stock_part(self, part: str, symbol: str) -> dict:
        """종목 페이지 중 느린 칸 (뉴스 영향 통계 · 차트 오버레이 · 위험) 따로."""
        from .. import stockplus
        from ..insight import earnings
        fns = {"news": lambda: stockplus.news(self.app, symbol), "overlay": lambda: stockplus.overlay(self.app, symbol),
               "risk": lambda: stockplus.risk(self.app, symbol), "earnings": lambda: earnings(self.app, symbol),
               "situation": lambda: stockplus.situation(self.app, symbol), "freshness": lambda: stockplus.freshness(self.app, symbol),
               "header": lambda: stockplus.header(self.app, symbol)}
        if part not in fns:
            raise ValueError("알 수 없는 칸")
        if not symbol:
            raise ValueError("symbol 필요")
        ttl = {"news": 120, "overlay": 120, "risk": 300, "earnings": 300}.get(part, 0)
        return self._cached(f"sp:{part}:{symbol}", ttl, fns[part]) if ttl else fns[part]()

    def stock_digest(self, body: dict) -> dict:
        from ..stockplus import ai_digest
        sym = str(body.get("symbol", ""))[:12]
        if not sym:
            raise ValueError("symbol 필요")
        self._risk_cache.pop(f"sp:news:{sym}", None)
        return ai_digest(self.app, sym)

    def news_impact(self, news_id: int) -> dict:
        from ..insight import news_impact
        return news_impact(self.app, news_id)

    def notrade(self, mode: str | None, days: int = 30, symbol: str | None = None) -> dict:
        from ..notrade import report
        m = self._mode(mode)
        return self._cached(f"notrade:{m}:{days}:{symbol}", 60, lambda: report(self.app, m, max(1, min(days, 365)), symbol or None))

    def ai_card(self, symbol: str | None = None) -> dict:
        from ..scorecard import scorecard
        return self._cached(f"aicard:{symbol}", 120, lambda: scorecard(self.app, symbol or None))

    def ai_verify(self, symbol: str) -> dict:
        from ..scorecard import verify_now
        return verify_now(self.app, symbol)

    def ai_track(self, refresh: bool = False) -> dict:
        from .. import aitrack
        if refresh or not _ops.get_state(self.engine, aitrack.HIST).get("rows"):
            aitrack.snapshot(self.app)
        return aitrack.report(self.app)

    def ai_alpha(self) -> dict:
        from .. import alphascore
        return self._cached("ai_alpha", 300, lambda: {"alpha": alphascore.alpha_card(self.app), "quiet": alphascore.quiet_rule(self.app)})

    def ai_public(self, n: int = 1000) -> dict:
        from ..scorecard import public_report
        return self._cached(f"aipublic:{n}", 300, lambda: public_report(self.app, max(50, min(n, 5000))))

    def action_center(self, mode: str | None) -> dict:
        from ..center import action_center
        m = self._mode(mode)
        return self._cached(f"ac:{m}", 30, lambda: action_center(self.app, m))

    def watchlist(self) -> dict:
        from ..center import watchlist
        return self._cached("watchlist", 20, lambda: watchlist(self.app))

    def watch_group(self, body: dict) -> dict:
        from ..center import set_group
        self._risk_cache.pop("watchlist", None)
        g = body.get("group")
        return set_group(self.app, str(body.get("symbol", ""))[:12], str(g)[:20] if g else None)

    def risk_simple(self, mode: str | None) -> dict:
        from ..center import risk_simple
        m = self._mode(mode)
        return self._cached(f"risk_simple:{m}", 60, lambda: risk_simple(self.app, m))

    def thesis(self, symbol: str | None = None) -> dict:
        from .. import thesis
        if symbol:
            return {"symbol": symbol, "thesis": thesis.get(self.engine, symbol)}
        return {"all": thesis.all_(self.engine), "breaches": thesis.breaches(self.app)}

    def thesis_write(self, body: dict) -> dict:
        from .. import thesis
        sym = str(body.get("symbol", ""))[:12]
        if body.get("delete"):
            out = thesis.save(self.engine, sym, {"delete": True})
        else:
            out = thesis.save(self.engine, sym, body)
        self._audit("thesis", f"{sym} {'삭제' if body.get('delete') else '저장'}")
        return {"ok": True, "thesis": out}

    def sentinel(self) -> dict:
        from .. import sentinel
        return self._cached("sentinel", 30, lambda: sentinel.check(self.app, store=False))

    def data_health(self, refresh: bool = False) -> dict:
        from .. import datahealth
        if not refresh:
            st = _ops.get_state(self.engine, "data_health")
            if st.get("at") and (datetime.now(UTC) - datetime.fromisoformat(st["at"])).total_seconds() < 600:
                return st
        return datahealth.report(self.app)

    def failure_lab(self, refresh: bool = False) -> dict:
        from .. import failure_lab
        st = _ops.get_state(self.engine, "failure_lab")
        if st and not refresh:
            return st
        return failure_lab.analyze(self.app)

    def ai_lab(self) -> dict:
        from ..lab import lab_status
        return self._cached("ai_lab", 60, lambda: lab_status(self.app))

    def portfolio_os(self, mode: str | None, source: str = "auto") -> dict:
        from ..portfolio_os import overview
        m = self._mode(mode)
        src = source if source in ("auto", "accounts", "system") else "auto"
        return self._cached(f"pos:{m}:{src}", 30, lambda: overview(self.app, m, src))

    def briefing(self, mode: str | None) -> dict:
        from ..portfolio_os import briefing
        m = self._mode(mode)
        return self._cached(f"brief:{m}", 60, lambda: briefing(self.app, m))

    def simulate(self, mode: str | None) -> dict:
        from ..portfolio_os import simulate
        m = self._mode(mode)
        return self._cached(f"sim:{m}", 300, lambda: simulate(self.app, m))

    def user_profile(self) -> dict:
        from .. import personal
        return {"profile": personal.get_profile(self.engine), "styles": personal.STYLES}

    def user_profile_write(self, body: dict) -> dict:
        from .. import personal
        out = personal.save_profile(self.engine, body)
        self._risk_cache.pop("discover", None)
        self._audit("profile", ", ".join(sorted(k for k in body)))
        return {"ok": True, "profile": out}

    def discover(self) -> dict:
        from .. import personal
        return self._cached("discover", 120, lambda: personal.discover(self.app))

    def mistakes(self) -> dict:
        from .. import personal
        return self._cached("mistakes", 120, lambda: personal.mistakes(self.app))

    def exec_costs(self) -> dict:
        from ..execreport import costs
        return self._cached("exec_costs", 120, lambda: costs(self.app))

    def orderbook(self, symbol: str, qty: str = "") -> dict:
        from ..execreport import book
        try:
            q = int(qty) if qty else None
        except ValueError:
            raise ValueError("수량은 정수") from None
        return book(self.app, symbol, q)

    def validation(self) -> dict:
        from ..validation import progress
        return self._cached("validation", 60, lambda: progress(self.app))

    def governance(self) -> dict:
        from .. import governance
        from ..failmode import MATRIX
        return {"service": governance.service_level(self.app), "licenses": governance.licenses(self.app),
                "security": governance.security(self.app), "failmode": MATRIX, "audit": governance.audit_log(self.engine, 100)}

    def ticket(self, symbol: str, side: str, qty: str = "", amount: str = "") -> dict:
        from .. import ticket
        try:
            q = int(qty) if qty else None
            a = float(amount) if amount else None
        except ValueError:
            raise ValueError("수량·금액은 숫자로") from None
        return ticket.preview(self.app, symbol, side, q, a)

    def ticket_place(self, body: dict) -> dict:
        from .. import ticket
        out = ticket.place(self.app, body)
        self._risk_cache.clear()
        return out

    def ticket_book(self, mode: str = "") -> dict:
        from .. import ticket
        return ticket.book(self.app, ticket.US_MODE if mode == ticket.US_MODE else ticket.MODE)

    def reviews(self, limit: int = 10) -> list[dict]:
        with session_scope(self.engine) as s:
            return [{"date": str(r.review_date), "created_at": _ts(r.created_at), "summary": r.summary,
                     "lessons": r.lessons or []}
                    for r in s.scalars(select(ReviewReport).order_by(ReviewReport.id.desc()).limit(limit))]

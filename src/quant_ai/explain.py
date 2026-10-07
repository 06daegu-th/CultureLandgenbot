"""왜 BUY · 왜 SELL · 왜 NO TRADE — 판단 하나를 사람 말로 풀어 쓴다.

같은 기록(봉인된 합의 판단)만 쓴다 — 설명을 위해 새로 계산하지 않는다 (나중에 봐도 같은 설명).
  · 한 줄 결론: "BUY — 상승 확률 64%, 신뢰도 61 이 매수 기준(58% · 55)을 넘었다"
  · 찬성 근거 / 반대 근거: AI 별 확률·가중치에서 방향이 같은 것 / 반대인 것 (영향 큰 순)
  · 막은 것: Risk AI 거부권 · 의견 충돌 · 응답 AI 부족 · 확률·신뢰도 미달 · 매매 준비(NOT READY) · 이벤트(실적 D-1) · 포트폴리오 한도
  · 무엇이 바뀌면 판단이 바뀌나: 매수/매도 기준까지 남은 확률·신뢰도 · 무효화 가격
"""

from __future__ import annotations

import re

from .ensemble.engine import EnsembleConfig

CODE_KO = {"veto": "Risk AI 거부권", "few_responders": "방향 의견을 낸 AI 부족", "high_conflict": "AI 간 의견 충돌 높음",
           "weak_signal": "확률·신뢰도가 매매 기준 미달"}
LABELS = {"primary": "뉴스 AI", "nvidia": "경제·시장 AI", "panel": "공시·실적 AI", "quant": "차트·Quant AI", "chart": "차트 신호", "regime": "시장 국면",
          "risk": "Risk AI", "challenger": "도전자 모델"}


def explain(payload: dict, action: str, prob_up: float, confidence: float, gates: dict | None = None,
            cfg: EnsembleConfig | None = None) -> dict:
    cfg = cfg or EnsembleConfig()
    p = payload or {}
    contribs = p.get("contributions") or []
    up = prob_up >= 0.5
    for_, against = [], []
    for c in contribs:
        pu = c.get("prob_up")
        if pu is None:
            continue
        w = float(c.get("weight") or 0)
        infl = w * abs(pu - 0.5)
        item = {"ai": LABELS.get(c.get("analyst"), c.get("analyst")), "prob_up": round(pu, 3), "weight": round(w, 3),
                "influence": round(infl, 4), "summary": (c.get("summary") or "")[:140],
                "track": None if c.get("accuracy") is None else f"과거 적중 {c['accuracy']:.0%} (n={c.get('n_scored')})"}
        (for_ if (pu >= 0.5) == up else against).append(item)
    for_.sort(key=lambda x: -x["influence"])
    against.sort(key=lambda x: -x["influence"])
    blocks = [CODE_KO.get(k, k) for k in p.get("no_trade_codes") or []]
    blocks += [f"거부권 — {v}" for v in p.get("vetoes") or []]
    g = gates or {}
    if g.get("readiness"):
        blocks.append(f"매매 준비: {g['readiness']}")
    if g.get("event"):
        blocks.append(f"이벤트: {g['event']}")
    if g.get("data"):
        blocks.append(f"데이터 품질: {g['data']}")
    if g.get("demoted"):
        blocks.append(f"AI 자동 강등(SHADOW) — 주문에는 쓰지 않음: {g['demoted']}")
    plan = p.get("plan") or {}
    to_buy = max(cfg.buy_prob - prob_up, 0.0)
    to_sell = max(prob_up - cfg.sell_prob, 0.0)
    conf_gap = max(cfg.min_confidence - confidence, 0.0)
    if action == "BUY":
        head = f"BUY — 상승 확률 {prob_up:.0%} · 신뢰도 {confidence:.0f} 가 매수 기준({cfg.buy_prob:.0%} · {cfg.min_confidence:.0f})을 넘었다"
    elif action == "SELL":
        head = f"SELL — 상승 확률 {prob_up:.0%} 가 매도 기준({cfg.sell_prob:.0%}) 아래 · 신뢰도 {confidence:.0f}"
    elif action == "NO_TRADE":
        head = "NO TRADE — " + (blocks[0] if blocks else "진입 보류")
    else:
        head = f"HOLD — 상승 확률 {prob_up:.0%} · 신뢰도 {confidence:.0f}: 매수({cfg.buy_prob:.0%})·매도({cfg.sell_prob:.0%}) 기준 사이"
    change = []

    def need(prob_part: str | None, extra: list[str]) -> str:
        parts = ([prob_part] if prob_part else []) + ([f"신뢰도 +{conf_gap:.0f}"] if conf_gap else []) + extra
        return " · ".join(parts) or "기준 충족 — 다른 차단 사유 해소"

    if action != "BUY":
        change.append("BUY 가 되려면: " + need(f"상승 확률 +{to_buy * 100:.1f}%p" if to_buy > 0 else None,
                                              (["거부권 해제"] if p.get("vetoes") else []) + (["의견 충돌 완화"] if p.get("conflict") == "high" else [])))
    if action != "SELL":
        change.append("SELL 이 되려면: " + need(f"상승 확률 −{to_sell * 100:.1f}%p" if to_sell > 0 else None, []))
    if plan.get("stop"):
        change.append(f"판단이 틀렸다고 인정하는 가격: {plan['stop']:,.2f} ({plan.get('stop_pct', 0):+.1%})")
    plain = _plain(action, prob_up, confidence, for_, against, blocks, cfg, up)
    effective = action
    if g.get("demoted") and action in ("BUY", "SELL") and not g.get("data"):
        effective = "NO_TRADE"
        head = f"AI {action} {prob_up:.0%} — 하지만 AI 가 성적 불량으로 SHADOW 강등 → 주문에 쓰지 않음"
    if g.get("data") and action in ("BUY", "SELL"):  # 판단은 기록대로 두고, 실행 여부만 막는다 (fail-closed)
        effective = "NO_TRADE"
        head = f"AI {action} {prob_up:.0%} — 하지만 데이터 품질 LOW → 거래하지 않음 ({g['data']})"
        plain = plain + [f"실행: 데이터가 믿을 수 없는 상태({g['data']})라 이 판단으로 거래하지 않습니다. 데이터가 정상으로 돌아오면 다시 판단합니다."]
    return {"action": action, "effective_action": effective, "headline": head, "plain": plain, "for": for_[:5], "against": against[:5], "blocks": blocks,
            "reasons": (p.get("reasons") or [])[:6], "risks": (p.get("risks") or [])[:6], "what_changes": change,
            "thresholds": {"buy_prob": cfg.buy_prob, "sell_prob": cfg.sell_prob, "min_confidence": cfg.min_confidence},
            "conflict": p.get("conflict"), "prob_raw": p.get("prob_raw")}


def _plain(action: str, p: float, conf: float, for_: list, against: list, blocks: list, cfg, up: bool) -> list[str]:
    """숫자 대신 말로: 누가 어느 쪽인지 → 왜 이 결론인지 → 무엇을 뜻하는지."""
    n = len(for_) + len(against)
    ups = len(for_) if up else len(against)
    top = (for_ or [None])[0]
    side = "오른다" if up else "내린다"
    who = f"의견을 낸 AI {n}개 중 {ups}개가 오른다고, {n - ups}개가 내린다고 봤습니다" if n else "방향 의견을 낸 AI 가 없습니다"
    lead = f"가장 영향이 큰 건 {top['ai']} ({top['prob_up']:.0%})입니다." if top else ""
    if action == "BUY":
        why = f"합친 상승 확률 {p:.0%}, 신뢰도 {conf:.0f} 로 매수 기준({cfg.buy_prob:.0%} · {cfg.min_confidence:.0f})을 둘 다 넘었습니다."
        mean = "뜻: 오를 가능성이 기준보다 높다고 본 것이지, 오른다는 보장은 아닙니다. 손절 가격을 함께 보세요."
    elif action == "SELL":
        why = f"합친 상승 확률이 {p:.0%} 로 매도 기준({cfg.sell_prob:.0%}) 아래입니다 — 내릴 쪽이 우세합니다."
        mean = "뜻: 보유 중이면 줄이는 쪽, 새로 사지 않는 쪽입니다."
    elif action == "NO_TRADE":
        why = f"{side}는 쪽이 우세해도 막는 이유가 있습니다: " + (", ".join(blocks[:2]) or "진입 보류") + "."
        mean = "뜻: 방향과 상관없이 지금은 거래하지 않습니다. 막은 이유가 풀리면 다시 판단합니다."
    else:
        gap = []
        if p < cfg.buy_prob and p > cfg.sell_prob:
            gap.append(f"확률 {p:.0%} 가 매수 {cfg.buy_prob:.0%} · 매도 {cfg.sell_prob:.0%} 사이")
        if conf < cfg.min_confidence:
            gap.append(f"신뢰도 {conf:.0f} 가 기준 {cfg.min_confidence:.0f} 미만 (AI 들이 한 방향으로 모이지 않음)")
        why = "결론을 낼 만큼 뚜렷하지 않습니다: " + (" · ".join(gap) or "기준 근처") + "."
        mean = "뜻: 지금은 지켜보는 구간입니다. 보유 중이면 그대로, 새로 사지는 않습니다."
    return [x for x in (who + (". " + lead if lead else "."), why, mean) if x]


def for_symbol(app, symbol: str) -> dict | None:
    from . import ops
    from .asof import label
    from .pipeline import latest_consensus
    rec = latest_consensus(app.engine, symbol)
    if rec is None:
        return None
    gates = {}
    try:
        from .readiness import gate_applies
        rd = ops.get_state(app.engine, "readiness")
        if rd.get("status") == "NOT_READY" and gate_applies(app.settings, rd.get("mode") or "paper"):
            gates["readiness"] = "NOT READY (" + ", ".join(c["key"] for c in rd.get("checks") or [] if c["status"] == "red") + ")"
        r = next((x for x in ops.get_state(app.engine, "event_calendar").get("risk") or [] if x["symbol"] == symbol), None)
        if r and r.get("buy_multiplier", 1) < 1:
            gates["event"] = f"{r.get('reason')} → 매수 ×{r['buy_multiplier']}"
        dm = ops.get_state(app.engine, "ai_demotion")
        if dm.get("on"):
            gates["demoted"] = dm.get("reason") or "AI 성적 불량"
        dh = ops.get_state(app.engine, "data_health")
        if dh.get("trading") == "BLOCKED":
            gates["data"] = dh.get("block_reason") or f"데이터 건강 {dh.get('overall', 0)}%"
        else:
            from .stock import trust
            tr = trust(app, symbol)
            if tr["status"] == "bad":
                gates["data"] = next((c["label"] + " — " + c["detail"] for c in tr["checks"] if c["status"] == "bad"), "데이터 신뢰도 낮음")[:120]
    except Exception:  # noqa: BLE001, S110 - 설명 보조 정보라 실패해도 본 설명은 보인다
        pass
    out = explain(rec.payload or {}, rec.action, rec.prob_up, rec.confidence, gates)
    return out | {"id": rec.id, "as_of": label(rec.as_of), "sealed": bool(rec.row_hash)}


ROLE_SHORT = {"primary": "News", "nvidia": "Macro", "panel": "Earnings", "quant": "Quant", "chart": "Chart", "regime": "Regime", "risk": "Risk", "challenger": "Challenger"}
FINAL_ICON = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡", "NO_TRADE": "⚪"}
VOL_MAX = 0.05  # 하루 평균 움직임(20일 σ)이 5% 이상이면 새로 사지 않는다 (연 80% 수준)
MIN_BARS = 60  # 일봉이 이보다 적으면 판단 근거 부족
STALE_MIN = 20  # 장중 가격이 20분 넘게 안 바뀌면 지연으로 본다
STALE_DAYS = 3  # 판단이 3일 넘게 묵었으면 새로 판단하기 전까지 거래하지 않는다


def _view(c: dict, cfg: EnsembleConfig) -> str:
    if c.get("veto"):
        return "NO TRADE"
    pu = c.get("prob_up")
    if pu is None:
        return "통과" if c.get("analyst") == "risk" else "기권"
    return "BUY" if pu >= cfg.buy_prob else "SELL" if pu <= cfg.sell_prob else "HOLD"


def _market_open(symbol: str, now) -> bool:
    from .clock import clock_status
    m = clock_status(now)["markets"]["KRX" if symbol[:1].isdigit() else "US"]
    return m.get("phase") == "open"


def verdict(app, symbol: str, now=None) -> dict:
    """종목 하나의 'AI 최종 판단' — 크게 하나: BUY / HOLD / SELL / NO TRADE.

    AI 별 의견(News·Macro·Quant·Risk…)은 표로 보여 주되 사용자가 비교할 필요 없이 FINAL 하나로.
    NO TRADE 를 적극적으로: 데이터 부족 · 판단이 오래됨 · 장중 가격 지연 · 실적 발표 임박 · 변동성 과다 ·
    AI 강등 · 매매 준비 미달 · 데이터 품질 — 하나라도 걸리면 BUY 를 실행하지 않는다 (판단 기록은 그대로).
    """
    from datetime import UTC, datetime

    import pandas as pd

    from . import ops
    from .asof import label
    from .pipeline import latest_consensus
    now = now or datetime.now(UTC)
    cfg = EnsembleConfig()
    rec = latest_consensus(app.engine, symbol)
    if rec is None:
        why = ("해외 종목은 매일 판단 대상이 아닙니다 — '지금 AI 분석'을 누르면 바로 판단합니다 (주문 없음)" if not symbol[:1].isdigit()
               else "아직 이 종목 AI 판단 기록이 없습니다 — '지금 AI 분석'을 누르면 바로 판단합니다 (주문 없음)")
        return {"symbol": symbol, "final": None, "label": "AI 판단 없음", "icon": "⚪", "why_none": why, "can_analyze": True}
    ex = for_symbol(app, symbol) or {}
    p = rec.payload or {}
    ev = p.get("evidence") or {}
    bars = (app._all_bars()[0] or {}).get(symbol)
    blocks: list[str] = list(ex.get("blocks") or [])
    extra: list[str] = []
    n_bars = 0 if bars is None else len(bars)
    if n_bars < MIN_BARS:
        extra.append(f"데이터 부족 — 일봉 {n_bars}일치 (최소 {MIN_BARS}일)")
    at = pd.Timestamp(rec.as_of)
    at = at.tz_localize("UTC") if at.tzinfo is None else at
    age_d = (pd.Timestamp(now) - at).total_seconds() / 86400
    if age_d > STALE_DAYS:
        extra.append(f"판단이 오래됨 — {age_d:.0f}일 전 판단 (새로 판단하기 전까지 거래하지 않음)")
    if _market_open(symbol, now):
        q = (ops.get_state(app.engine, "live_quotes").get(symbol) or {})
        qa = pd.Timestamp(q["at"]) if q.get("at") else None
        if qa is not None and qa.tzinfo is None:
            qa = qa.tz_localize("UTC")
        mins = None if qa is None else (pd.Timestamp(now) - qa).total_seconds() / 60
        if mins is None or mins > STALE_MIN:
            extra.append("장중인데 실시간 가격 없음 — 일봉 종가 기준" if mins is None else f"가격 지연 — 마지막 시세 {mins:.0f}분 전 (기준 {STALE_MIN}분)")
    vol = ((ev.get("price") or {}).get("vol_20"))
    if vol is None and bars is not None and n_bars > 21:
        vol = float(bars["close"].pct_change().iloc[-20:].std())
    if vol is not None and vol >= VOL_MAX:
        extra.append(f"변동성 과다 — 하루 평균 ±{vol:.1%} 움직임 (기준 {VOL_MAX:.0%})")
    r = next((x for x in ops.get_state(app.engine, "event_calendar").get("risk") or [] if x.get("symbol") == symbol), None)
    earn = (r or {}).get("earnings") or {}
    if earn and earn.get("trading_days", 99) <= 1:
        extra.append(f"실적 발표 임박 — {earn.get('d_label', '')} (발표 뒤 방향을 보고 다시 판단)")
    trust = None
    try:
        from .center import ai_state
        st = ai_state(app)
        trust = {k: st[k] for k in ("key", "icon", "label")}
        if st["key"] == "banned" and not st.get("demoted"):
            extra.append("AI 성적 기준 미달 (🔴 사용 금지) — AI BUY 는 참고만")
    except Exception:  # noqa: BLE001
        trust = None
    raw = ex.get("effective_action") or rec.action
    final = raw
    if raw == "BUY" and extra:
        final = "NO_TRADE"
    all_blocks = list(dict.fromkeys(blocks + extra))
    votes = [{"role": ROLE_SHORT.get(c.get("analyst"), c.get("analyst")), "label": LABELS.get(c.get("analyst"), c.get("analyst")),
              "view": _view(c, cfg), "prob_up": None if c.get("prob_up") is None else round(c["prob_up"], 3),
              "weight": round(float(c.get("weight") or 0), 3), "summary": (c.get("summary") or "")[:100]}
             for c in p.get("contributions") or []]
    junk = ("휴리스틱", "국면 ", "기권", "재료 없음")
    from .aiinputs import for_record
    inputs = for_record(p)
    # v36: 재료 없이 낸 의견(키 없는 규칙 AI · 뉴스 0건 등)은 '근거'가 아니다 — 근거 목록에서 뺀다
    empty_ai = {r["analyst"] for r in inputs["readers"] if r["empty"]}
    empty_lab = {LABELS.get(a, a) for a in empty_ai}
    pro = [f"{x['ai']}: {x['summary']}" if x.get("summary") and not any(j in x["summary"] for j in junk) else f"{x['ai']} 상승 확률 {x['prob_up']:.0%}"
           for x in (ex.get("for") or []) if x["ai"] not in empty_lab] if rec.prob_up >= 0.5 else []
    reasons = [w for w in p.get("reasons") or [] if not any(str(w).startswith(f"[{a}]") for a in empty_ai)]
    regime_ko = {"bull_quiet": "안정적 상승", "bull_volatile": "변동성 상승", "sideways": "횡보", "bear_quiet": "완만한 하락",
                 "bear_volatile": "변동성 하락", "crisis": "위기"}

    def _ko(w: str) -> str:  # '[primary] 20일 모멘텀 상승' → '뉴스 AI: 20일 모멘텀 상승' · 'sideways' → '횡보'
        m = re.match(r"^\[(\w+)\]\s*(.*)$", w or "")
        if m:
            lab, body = LABELS.get(m.group(1), m.group(1)), m.group(2)
            w = body if body.startswith(lab) else f"{lab}: {body}"
        for k, v in regime_ko.items():
            w = w.replace(k, v)
        return w
    why_buy = [_ko(w) for w in reasons + pro if w and not any(j in w for j in junk)][:3]
    why_not = (all_blocks + list(p.get("risks") or []) + [x["summary"] for x in (ex.get("against") or []) if x.get("summary")])
    why_not = [_ko(w) for w in dict.fromkeys(why_not) if w and "휴리스틱" not in w][:3]
    from .scorecard import verify_now
    warn = None
    try:
        vn = verify_now(app, symbol, rec.prob_up)
        if (vn.get("similar_n") or 0) >= 20 and vn.get("similar_up") is not None:
            hit = vn["similar_up"] if rec.prob_up >= 0.5 else 1 - vn["similar_up"]
            if hit < 0.45:
                warn = f"주의: 과거 비슷한 판단(상승 {rec.prob_up:.0%} 근처) {vn['similar_n']}회에서 AI 가 맞은 비율 {hit:.0%} — 이런 상황에서 자주 틀렸습니다"
        fl = ops.get_state(app.engine, "failure_lab")
        for f in fl.get("findings") or []:
            if f.get("tag") == "earnings" and earn:
                warn = (warn + " · " if warn else "") + "AI 는 실적 발표가 낀 예측에서 약했습니다 (" + f["text"].split("—")[-1].strip()[:60] + ")"
    except Exception:  # noqa: BLE001, S110 - 경고는 보조 정보
        pass
    news = ev.get("news") or []
    discs = ev.get("disclosures") or []
    macro = ev.get("macro") or {}

    def _last(items, key="at"):
        ts = [str(x.get(key) or x.get("published_at") or x.get("date") or "") for x in items if isinstance(x, dict)]
        ts = [t for t in ts if t]
        return max(ts) if ts else None
    used = {"judged_at": label(rec.as_of), "price": (ev.get("price") or {}).get("last_bar"),
            "news": {"n": len(news), "last": _last(news)}, "disclosures": {"n": len(discs), "last": _last(discs)},
            "macro": {"n": len(macro) if isinstance(macro, dict) else 0, "last": (macro.get("as_of") if isinstance(macro, dict) else None)},
            "regime": (ev.get("regime") or {}).get("regime"), "sealed": bool(rec.row_hash)}
    if used["price"]:
        used["price"] = label(used["price"], with_time=False)
    shown = "NO TRADE" if final == "NO_TRADE" else final
    return {"symbol": symbol, "final": final, "raw": rec.action, "label": shown, "icon": FINAL_ICON.get(final, "⚪"),
            "prob_up": round(rec.prob_up, 3), "confidence": round(rec.confidence, 1), "horizon": int(p.get("horizon") or 5),
            "big": f"{FINAL_ICON.get(final, '⚪')} {shown}" + (f" {rec.prob_up:.0%}" if final in ("BUY", "SELL", "HOLD") else ""),
            "changed_by_gate": final != rec.action, "headline": ex.get("headline"), "plain": ex.get("plain") or [],
            "votes": votes, "inputs": inputs, "why_buy": why_buy, "why_not": why_not, "no_trade": all_blocks, "warn": warn, "used": used,
            "expected_return": p.get("expected_return"), "plan": {k: (p.get("plan") or {}).get(k) for k in ("stop", "target", "stop_pct")},
            "trust": trust, "as_of": label(rec.as_of), "id": rec.id,
            "note": "FINAL = 여러 AI 의견을 성적 가중으로 합친 뒤, 거래하면 안 되는 이유(NO TRADE)를 먼저 검사한 결과"}


__all__ = ["explain", "for_symbol", "verdict"]

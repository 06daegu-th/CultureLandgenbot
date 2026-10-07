"""v35 꾸준함 — 목표 금액은 수익률보다 '중간에 멈추지 않고 꾸준히 넣기'에서 대부분 나온다.

  · 연속 적립 기록: 이번 달 적립을 했나 · 몇 달 연속 · 최장 · 빠진 달
  · 월별 기록: 날마다 이번 달 자산·넣은 돈을 남겨 '월간 목표 리포트'(넣은 돈 vs 시장이 보탠 돈 · 계획 대비)를 만든다
  · 배지: 첫 적립 · 3·6·12·24개월 연속 · 목표의 10·25·50·75·100% — 처음 얻을 때 한 번 휴대폰 알림
  · 급락 안내: 시장이 하루 −3% · 고점 대비 −10%/−20% 일 때 '계획대로 가세요' + 과거에 같은 하락 뒤 어땠는지(숫자)
매매를 권하지 않는다 — 계획을 지키게 돕는 것만.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from . import ops

KST = ZoneInfo("Asia/Seoul")
CRASH_KEY = "crash_watch"
STREAK_BADGES = (3, 6, 12, 24, 36)
PCT_BADGES = (0.1, 0.25, 0.5, 0.75, 1.0)


def _month(d: date) -> str:
    return d.strftime("%Y-%m")


def _prev_month(m: str) -> str:
    y, mm = int(m[:4]), int(m[5:7])
    return f"{y - 1}-12" if mm == 1 else f"{y}-{mm - 1:02d}"


def _months_between(a: str, b: str) -> list[str]:
    """a 부터 b 까지 (둘 다 포함) 'YYYY-MM' 목록."""
    out, cur = [], a
    while cur <= b and len(out) < 600:
        out.append(cur)
        y, mm = int(cur[:4]), int(cur[5:7])
        cur = f"{y + 1}-01" if mm == 12 else f"{y}-{mm + 1:02d}"
    return out


def done_months(g: dict) -> set[str]:
    """적립을 한 달들 — 기록(done_months) + '샀어요' 기록 + 마지막 적립일 (예전 버전 기록도 살린다)."""
    s = set(g.get("done_months") or [])
    s |= {str(lot.get("date", ""))[:7] for lot in g.get("lots") or [] if lot.get("date")}
    last = (g.get("dca") or {}).get("last")
    if last:
        s.add(str(last)[:7])
    return {m for m in s if len(m) == 7}


def streak(g: dict, today: date) -> dict:
    """연속 적립 — 이번 달 적립일이 아직 안 왔으면 이번 달은 '진행 중'으로 보고 지난달부터 센다 (아직 끊긴 게 아님)."""
    done = done_months(g)
    cur_m = _month(today)
    day = int((g.get("dca") or {}).get("day") or 25)
    start = str(g.get("start") or today.isoformat())[:7]
    this_done = cur_m in done
    pending = not this_done and today.day <= day + 3  # 적립일 + 사흘까지는 기다려 준다
    m = cur_m if this_done else _prev_month(cur_m)
    n = 0
    while m in done and m >= start[:7]:
        n += 1
        m = _prev_month(m)
    best = run = 0
    for mm in _months_between(start, cur_m):
        run = run + 1 if mm in done else 0
        best = max(best, run)
    past = _months_between(start, _prev_month(cur_m)) if start <= _prev_month(cur_m) else []
    missed = [mm for mm in past if mm not in done]
    if this_done:
        status, text = "done", f"이번 달 적립 완료 · {n}개월 연속"
    elif pending:
        status, text = "pending", f"이번 달 적립일 {day}일 기다리는 중" + (f" · 지금까지 {n}개월 연속" if n else "")
    else:
        status, text = "missed", "이번 달 적립을 아직 안 했어요 — 늦어도 괜찮아요, 기록하면 이어져요"
    calendar = [{"month": mm, "done": mm in done, "current": mm == cur_m} for mm in _months_between(_shift(cur_m, -11), cur_m)]
    return {"current": n, "best": best, "total": len([d for d in done if d >= start]), "missed": missed[-12:], "status": status,
            "text": text, "this_month": this_done, "calendar": calendar, "since": start}


def _shift(m: str, k: int) -> str:
    y, mm = int(m[:4]), int(m[5:7]) - 1 + k
    return f"{y + mm // 12}-{mm % 12 + 1:02d}"


def paid_total(app, g: dict) -> float:
    """지금까지 넣은 돈 — '샀어요' 기록 · 모의 적립 장부 입금 · 없으면 계획(원금 + 매달 × 적립한 달)으로 추정."""
    from .goal import etf_book
    lots = g.get("lots") or []
    if lots:
        return float(sum(float(x.get("deposit") or 0) for x in lots))
    d = g.get("dca") or {}
    if d.get("mode") == "paper" and d.get("target") == "etf":
        flows = app.cashflows(etf_book())
        if flows:
            return float(sum(float(f.get("amount") or 0) for f in flows))
    n = len([m for m in done_months(g) if m >= str(g.get("start") or "")[:7]])
    return float(g.get("principal") or 0) + float(g.get("monthly") or 0) * n


# ------------------------------------------------------------------ 월별 기록 · 배지
def snapshot(app, now: datetime | None = None) -> dict | None:
    """이번 달 자산·넣은 돈 기록 (하루 한 번이면 충분) + 새 배지 확인. 목표가 없으면 None."""
    from .goal import KEY, current_assets, get
    now = now or datetime.now(UTC)
    g = get(app)
    if not g.get("goal"):
        return None
    today = now.astimezone(KST).date()
    total, _src = current_assets(app)
    hist = dict(g.get("history") or {})
    hist[_month(today)] = {"total": round(total), "paid": round(paid_total(app, g)), "at": today.isoformat()}
    hist = dict(sorted(hist.items())[-120:])
    new_badges = []
    badges = dict(g.get("badges") or {})
    st = streak(g, today)
    pct = total / g["goal"] if g.get("goal") else 0
    for key, title, ok in badge_rules(st, pct, g):
        if ok and key not in badges:
            badges[key] = today.isoformat()
            new_badges.append({"key": key, "title": title})
    ops.set_state(app.engine, KEY, g | {"history": hist, "badges": badges})
    if new_badges:  # 처음 얻은 배지는 한 번 알림 (어디서 기록되든 — 화면을 열 때도, 스케줄러가 돌 때도)
        from . import tenancy
        from .alerts import push
        who = f"{tenancy.b36(tenancy.uid())}:" if tenancy.scoped() else ""
        for b in new_badges:
            push(app.engine, "habit", f"배지: {b['title']}", "꾸준함이 목표를 만들어요 — 내 목표 화면에서 기록을 볼 수 있어요",
                 level="good", link="#goal", dedupe=f"badge:{who}{b['key']}", now=now)
    return {"total": total, "new_badges": new_badges, "streak": st}


def badge_rules(st: dict, pct: float, g: dict) -> list[tuple[str, str, bool]]:
    rules = [("first", "첫 적립", st["total"] >= 1)]
    rules += [(f"streak{n}", f"{n}개월 연속 적립", st["best"] >= n) for n in STREAK_BADGES]
    rules += [(f"pct{int(p * 100)}", f"목표의 {p:.0%} 달성" if p < 1 else "목표 달성", pct >= p) for p in PCT_BADGES]
    return rules


def badges(g: dict, st: dict, pct: float) -> list[dict]:
    have = g.get("badges") or {}
    return [{"key": k, "title": t, "at": have.get(k), "earned": k in have} for k, t, _ok in badge_rules(st, pct, g)]


def monthly_report(app, month: str | None = None, now: datetime | None = None) -> dict | None:
    """월간 목표 리포트: 그 달 자산 변화 = 넣은 돈 + 시장이 보탠(뺀) 돈 · 계획 대비 · 연속 적립 · 다음 달 적립일."""
    from .goal import band_at, get, next_dca
    now = now or datetime.now(UTC)
    g = get(app)
    if not g.get("goal"):
        return None
    today = now.astimezone(KST).date()
    month = month or _prev_month(_month(today))
    hist = g.get("history") or {}
    end = hist.get(month)
    if not end:
        return {"month": month, "empty": True, "text": f"{month[5:].lstrip('0')}월 기록이 없어요 — 매일 자동으로 남기기 시작했어요"}
    prev = hist.get(_prev_month(month)) or {"total": g.get("principal") or 0, "paid": g.get("principal") or 0}
    added = end["paid"] - prev["paid"]
    market = end["total"] - prev["total"] - added
    start = date.fromisoformat(str(g.get("start") or today.isoformat()))
    y, mm = int(month[:4]), int(month[5:7])
    months_in = max(0, (y - start.year) * 12 + mm - start.month + 1)
    band = band_at(g, g.get("checkpoints") or [], months_in) if g.get("checkpoints") else None
    st = streak(g, date(y, mm, 28) if month != _month(today) else today)
    pct = end["total"] / g["goal"]
    pos = None
    if band:
        pos = ("상위권 (계획보다 많이 앞섬)" if end["total"] >= band["p90"] else "계획보다 앞섬" if end["total"] >= band["p50"]
               else "정상 범위 안" if end["total"] >= band["p10"] else "정상 범위보다 아래")
    nd = next_dca(g, today)
    m_ko = f"{mm}월"
    parts = [f"{m_ko}: 넣은 돈 {added / 1e4:+,.0f}만원 · 시장 {market / 1e4:+,.0f}만원"]
    if pos:
        parts.append(f"계획 대비 {pos}")
    if st["current"]:
        parts.append(f"{st['current']}개월 연속 적립")
    tone = ("좋은 달이었어요" if market > 0 else "시장이 내린 달 — 적립 단가가 낮아진 달이기도 해요" if market < 0 else "")
    return {"month": month, "empty": False, "start_total": prev["total"], "end_total": end["total"], "added": round(added),
            "market": round(market), "pct": round(pct, 4), "band": band, "position": pos, "streak": st["current"],
            "next_dca": nd, "tone": tone, "text": " · ".join(parts),
            "months": [{"month": k, **v} for k, v in sorted(hist.items())[-24:]]}


# ------------------------------------------------------------------ 급락 안내
def _kospi(app):
    """국내 대표 지수 일봉 — 긴 기록(일봉 대용 지수)을 먼저 (과거 하락 통계용), 없으면 공개 지수 시세."""
    import pandas as pd
    try:
        _, benches = app._all_bars()
        kb = benches.get("KR")
        if kb is not None and len(kb) > 60:
            return kb["close"].astype(float), "국내 대표 지수(시가총액 가중 대용)"
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        from .data.collectors.indices import load_indices
        ser = (load_indices(app.engine).get("KOSPI") or {}).get("series") or []
        if len(ser) > 60:
            return pd.Series([float(x[1]) for x in ser], index=pd.to_datetime([x[0] for x in ser])), "코스피"
    except Exception:  # noqa: BLE001, S110
        pass
    return None, None


def history_stats(c, kind: str = "dd10") -> dict:
    """과거에 같은 하락이 몇 번 · 그 뒤 1년 수익 · 고점 회복까지 걸린 기간 (중간값)."""
    import numpy as np
    vals = c.to_numpy(dtype=float)
    n = len(vals)
    peak = np.maximum.accumulate(vals)
    dd = vals / peak - 1
    r1 = np.r_[np.nan, vals[1:] / vals[:-1] - 1]
    events = []
    if kind == "day3":
        last = -999
        for i in range(1, n):
            if r1[i] <= -0.03 and i - last > 20:
                events.append(i)
                last = i
    else:
        th = -0.10 if kind == "dd10" else -0.20
        inside = False
        for i in range(n):
            if not inside and dd[i] <= th:
                events.append(i)
                inside = True
            elif inside and dd[i] >= 0:
                inside = False
    fwd = [vals[i + 252] / vals[i] - 1 for i in events if i + 252 < n]
    rec = []
    for i in events:
        j = next((k for k in range(i, n) if vals[k] >= peak[i]), None)
        if j is not None:
            rec.append((j - i) / 21)
    return {"n": len(events), "n_fwd": len(fwd), "fwd1y_median": round(float(np.median(fwd)), 4) if fwd else None,
            "fwd1y_up": round(float(np.mean([x > 0 for x in fwd])), 3) if fwd else None,
            "recover_months_median": round(float(np.median(rec)), 1) if rec else None, "years": round(n / 252, 1)}


def market_drop(app, now: datetime | None = None) -> dict | None:
    """지금 시장이 크게 내렸나 (하루 −3% · 고점 대비 −10%/−20%). 시세가 오래됐으면 판단하지 않는다 (가짜 경보 방지)."""
    import pandas as pd
    now = now or datetime.now(UTC)
    c, src = _kospi(app)
    if c is None or len(c) < 61:
        return None
    last_day = pd.Timestamp(c.index[-1]).date()
    if (now.astimezone(KST).date() - last_day).days > 5:
        return {"stale": True, "as_of": str(last_day)}
    r1 = float(c.iloc[-1] / c.iloc[-2] - 1)
    hi = float(c.iloc[-60:].max())
    dd = float(c.iloc[-1] / hi - 1)
    level = "dd20" if dd <= -0.20 else "dd10" if dd <= -0.10 else "day3" if r1 <= -0.03 else None
    soft = level is None and (r1 <= -0.02 or dd <= -0.07)
    return {"stale": False, "level": level, "soft": soft, "r1": round(r1, 4), "dd": round(dd, 4), "as_of": str(last_day), "source": src,
            "peak_day": str(pd.Timestamp(c.iloc[-60:].idxmax()).date()), "series": c}


def calm_card(app, now: datetime | None = None) -> dict | None:
    """목표 화면·홈에 띄우는 '계획대로' 카드 — 시장이 크게 내렸을 때만."""
    m = market_drop(app, now)
    if not m or m.get("stale") or not (m["level"] or m["soft"]):
        return None
    kind = m["level"] or ("day3" if m["r1"] <= -0.02 else "dd10")
    hs = history_stats(m["series"], kind)
    what = (f"고점 대비 {m['dd']:.0%}" if kind.startswith("dd") else f"하루 {m['r1']:+.1%}") if m["level"] else f"최근 하락 (하루 {m['r1']:+.1%} · 고점 대비 {m['dd']:.0%})"
    lines = []
    if hs["n"]:
        lines.append(f"지난 {hs['years']:.0f}년 동안 이런 하락 {hs['n']}번")
        if hs["fwd1y_median"] is not None:
            lines.append(f"그 뒤 1년 수익 중간값 {hs['fwd1y_median']:+.0%} · {hs['fwd1y_up']:.0%}는 1년 뒤 올라 있었어요")
        if hs["recover_months_median"] is not None:
            lines.append(f"고점 회복까지 보통 {hs['recover_months_median']:.0f}개월")
    return {"level": m["level"] or "soft", "title": f"시장 {what} — 적립 계획은 그대로 가요",
            "body": "떨어질 때 같은 돈으로 더 많이 사는 것이 적립식의 원리예요. 급하게 팔거나 적립을 멈추지 않는 것이 계획의 일부예요.",
            "history": hs, "lines": lines, "as_of": m["as_of"], "source": m["source"],
            "note": "과거 통계일 뿐 앞으로도 같다는 보장은 없어요 — 계획을 지키도록 돕는 안내이고 매매 권유가 아니에요."}


def crash_job(app, now: datetime | None = None) -> dict:
    """한 시간마다: 새 급락 단계면 목표가 있는 모든 사람에게 한 번씩 '계획대로' 알림 (같은 하락 구간·단계는 한 번만)."""
    from .alerts import push
    now = now or datetime.now(UTC)
    m = market_drop(app, now)
    if not m or m.get("stale") or not m["level"]:
        return {"sent": 0}
    st = ops.get_state(app.engine, CRASH_KEY)
    episode = m["peak_day"] if m["level"].startswith("dd") else m["as_of"]
    key = f"{episode}:{m['level']}"
    if key in (st.get("sent") or []):
        return {"sent": 0, "already": key}
    card = calm_card(app, now)
    body = card["body"] + (" · " + " · ".join(card["lines"]) if card and card["lines"] else "")
    sent = {"n": 0}

    def one():
        from .goal import get
        if not get(app).get("goal"):
            return
        from . import tenancy
        who = tenancy.b36(tenancy.uid()) if tenancy.scoped() else "o"
        if push(app.engine, "calm", card["title"], body, level="info", link="#goal", dedupe=f"calm:{who}:{key}", now=now) is not None:
            sent["n"] += 1
    one()  # 소유자(1인 모드)
    from . import service
    if service.multi():
        from .members import for_each_member
        for_each_member(app, one)
    ops.set_state(app.engine, CRASH_KEY, {"sent": [*(st.get("sent") or []), key][-50:], "last": key, "at": now.isoformat()})
    return {"sent": sent["n"], "key": key}


# ------------------------------------------------------------------ 하루 작업 (사람마다)
def daily(app, now: datetime | None = None) -> dict:
    """사람마다 하루 한 번: 월별 기록 · 새 배지 알림 · 매달 1~3일엔 지난달 리포트 알림."""
    from . import tenancy
    from .alerts import push
    from .goal import KEY, get
    now = now or datetime.now(UTC)
    snap = snapshot(app, now)
    if snap is None:
        return {"skip": "목표 없음"}
    who = f"{tenancy.b36(tenancy.uid())}:" if tenancy.scoped() else ""
    today = now.astimezone(KST).date()
    sent = None
    if today.day <= 3:
        rep = monthly_report(app, None, now)
        g = get(app)
        if rep and not rep.get("empty") and rep["month"] not in (g.get("reported") or []):
            push(app.engine, "habit", f"{int(rep['month'][5:])}월 목표 리포트", rep["text"] + (f" · {rep['tone']}" if rep["tone"] else ""),
                 level="info", link="#goal", dedupe=f"report:{who}{rep['month']}", now=now)
            ops.set_state(app.engine, KEY, g | {"reported": [*(g.get("reported") or []), rep["month"]][-36:]})
            sent = rep["month"]
    return {"badges": [b["key"] for b in snap["new_badges"]], "report": sent}


def overview(app, now: datetime | None = None) -> dict:
    """목표 화면 '꾸준함' 칸 한 번에: 연속 기록 · 배지 · 지난달 리포트 · 급락 카드."""
    from .goal import get
    now = now or datetime.now(UTC)
    g = get(app)
    if not g.get("goal"):
        return {"set": False}
    today = now.astimezone(KST).date()
    st = streak(g, today)
    hist = g.get("history") or {}
    last_total = (hist.get(_month(today)) or (list(hist.values())[-1] if hist else {})).get("total")
    pct = (last_total or 0) / g["goal"]
    new_plan = str(g.get("start") or "")[:7] >= _month(today)  # 이번 달 시작한 계획 — 지난달 리포트는 아직 없다
    return {"set": True, "streak": st, "badges": badges(g, st, pct), "report": None if new_plan else monthly_report(app, None, now),
            "calm": calm_card(app, now)}


def job(app, now: datetime | None = None) -> dict:
    """스케줄러: 소유자 + (여러 사용자 모드면) 회원마다 daily."""
    from . import service
    out = {"owner": daily(app, now)}
    if service.multi():
        from .members import for_each_member
        out["members"] = for_each_member(app, lambda: daily(app, now))
    return out


__all__ = ["streak", "done_months", "snapshot", "monthly_report", "badges", "calm_card", "crash_job", "daily", "overview", "job",
           "history_stats", "market_drop", "paid_total"]

"""증명 체인 (Proof Chain) — "AI 가 똑똑한가" 가 아니라 "돈으로, 반복해서 남았나" 를 여섯 단계로 증명한다.

  ① 데이터가 과거 시점에서 정확했나   판단에 쓴 뉴스·공시가 판단 시각 이전 것인가 · 수정주가 · 생존편향 없는 유니버스
  ② AI 확률이 실제 확률과 맞나        Brier Skill > 0 · ECE ≤ 5%p (기준율보다 나은, 정직한 확률)
  ③ AI 가 실제로 추가 수익을 만들었나   AI 장부 − 코어 장부 (같은 가격·같은 규칙, AI 만 다름) 초과수익과 t 값
  ④ 비용을 빼도 남나                  AI 알파가 비용 후에도 + · 운용 장부가 비용 후 벤치마크 초과
  ⑤ 실제 주문에서도 같은 결과가 나오나  실제 장부 vs 같은 규칙의 시뮬레이션 장부 추적 차이 · 실측 슬리피지 · 체결률
  ⑥ 그 성과가 반복되나                20거래일 구간별로 AI 알파가 + 인 비율 · 전반/후반 모두 + · 국면별

각 단계는 pass / fail / insufficient(표본 부족). 기준은 이 파일 상수로 고정 (결과를 보고 바꾸지 않는다).
AI Alpha 분리: 전체(AI 장부 − 코어) = 거부권·긴급청산(veto − core) + 위성 베팅(full − veto).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

MIN_SCORED = 200
MAX_ECE = 0.05
MIN_DAYS = 60
T_PASS = 1.64  # 단측 5%
BLOCK = 20
MAX_TRACKING = 0.01
MIN_FILL = 0.9
REGIME_LABELS = {"bull_quiet": "강세·안정", "bull_volatile": "강세·변동", "sideways": "횡보",
                 "bear_quiet": "약세·안정", "bear_volatile": "약세·변동", "crisis": "위기"}


def _daily(eq: pd.Series | None) -> pd.Series:
    if eq is None:
        return pd.Series(dtype=float)
    eq = eq.dropna().astype(float)
    if eq.empty:
        return eq
    eq.index = pd.DatetimeIndex(eq.index)
    if eq.index.tz is not None:
        eq.index = eq.index.tz_convert(None)
    eq = eq.groupby(eq.index.normalize()).last()
    return eq[eq > 0]


def _ret(eq: pd.Series) -> float | None:
    return float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) >= 2 else None


def _step(key: str, title: str, status: str, headline: str, detail: str, **data) -> dict:
    return {"key": key, "title": title, "status": status, "headline": headline, "detail": detail, **data}


def relative(a: pd.Series, b: pd.Series) -> dict | None:
    """a 장부의 b 장부 대비 초과: 누적 차이 · 일간 차이 평균의 t 값 · 90% 신뢰구간(연율) · 곡선."""
    a, b = _daily(a), _daily(b)
    idx = a.index.intersection(b.index)
    if len(idx) < 3:
        return None
    ra, rb = a.loc[idx].pct_change().dropna(), b.loc[idx].pct_change().dropna()
    d = ra - rb
    sd = float(d.std()) if len(d) > 1 else 0.0
    n = len(d)
    t = float(d.mean() / sd * math.sqrt(n)) if sd > 0 else 0.0
    half = 1.645 * sd / math.sqrt(n) if n else 0.0
    ra_c, rb_c = a.loc[idx] / a.loc[idx[0]], b.loc[idx] / b.loc[idx[0]]
    return {"days": n + 1, "a": float(ra_c.iloc[-1] - 1), "b": float(rb_c.iloc[-1] - 1),
            "excess": float(ra_c.iloc[-1] - rb_c.iloc[-1]), "t": round(t, 2),
            "ann": round(float(d.mean() * 252), 4), "ci90_ann": [round(float((d.mean() - half) * 252), 4),
                                                            round(float((d.mean() + half) * 252), 4)],
            "te_ann": round(sd * math.sqrt(252), 4), "daily": d,
            "curve": [[str(t_.date()), round(float(ra_c.loc[t_] - rb_c.loc[t_]), 5)] for t_ in idx]}


def alpha_stats(eq: pd.Series, bench: pd.Series) -> dict | None:
    """운용 장부 vs 벤치마크: 초과수익 · 베타 · 연 알파 · 정보비율 · t."""
    r = relative(eq, bench)
    if r is None:
        return None
    a, b = _daily(eq), _daily(bench)
    idx = a.index.intersection(b.index)
    rp, rb = a.loc[idx].pct_change().dropna(), b.loc[idx].pct_change().dropna()
    beta = float(np.cov(rp, rb)[0, 1] / rb.var()) if rb.var() > 0 else 1.0
    return {"days": r["days"], "book": r["a"], "bench": r["b"], "excess": r["excess"], "beta": round(beta, 3),
            "alpha_ann": round(float((rp.mean() - beta * rb.mean()) * 252), 4),
            "ir": round(float(r["daily"].mean() / r["daily"].std() * math.sqrt(252)), 3) if r["daily"].std() > 0 else None,
            "t": r["t"], "curve": r["curve"]}


# ------------------------------------------------------------------ ① 데이터
def data_step(dc: dict) -> dict:
    """dc: {checked, ts_violations, adjusted, pit_universe, forward_only, quality_warnings, notes}"""
    n, v = int(dc.get("checked") or 0), int(dc.get("ts_violations") or 0)
    notes = list(dc.get("notes") or [])
    if n == 0:
        return _step("data", "① 데이터가 과거 시점에서 정확했나", "insufficient", "검사 대상 없음",
                     "AI 판단 기록이 쌓이면 판단에 쓴 뉴스·공시 시각을 검사합니다.", **dc)
    uni_ok = bool(dc.get("pit_universe") or dc.get("forward_only"))
    ok = v == 0 and bool(dc.get("adjusted")) and uni_ok
    status = "fail" if v or not dc.get("adjusted") or not uni_ok else "pass"
    return _step("data", "① 데이터가 과거 시점에서 정확했나", status, "누수 0건" if ok else f"누수 {v}건" if v else "점검 필요",
                 f"판단 {n}건의 입력 검사: 판단 시각 이후 뉴스·공시 {v}건 · 수정주가 {'✓' if dc.get('adjusted') else '✕'} · "
                 f"유니버스 {'시점 기준 ✓' if dc.get('pit_universe') else '전진 기록만 사용 ✓' if dc.get('forward_only') else '생존편향 ✕'}"
                 + (f" · 품질 경고 {dc['quality_warnings']}건" if dc.get("quality_warnings") else "")
                 + ("".join(f" · {x}" for x in notes)), **dc)


# ------------------------------------------------------------------ ② 확률
def calibration_step(cal: dict, hit: dict | None = None) -> dict:
    n = int(cal.get("n") or 0)
    bss, ece = cal.get("brier_skill"), cal.get("ece")
    hit_txt = ""
    if hit and hit.get("n"):
        hit_txt = f" · 방향 신호 {hit['n']}건 적중 {hit['hits'] / hit['n']:.1%}"
    if n < MIN_SCORED:
        return _step("calib", "② AI 확률이 실제 확률과 맞나", "insufficient", f"채점 {n}건",
                     f"채점된 합의 {n}/{MIN_SCORED}건 — 표본이 차면 Brier Skill·ECE 로 판정{hit_txt}", n=n, brier_skill=bss, ece=ece)
    ok = bss is not None and bss > 0 and ece is not None and ece <= MAX_ECE
    return _step("calib", "② AI 확률이 실제 확률과 맞나", "pass" if ok else "fail",
                 f"ECE {ece:.1%}" if ece is not None else "-",
                 f"합의 {n}건 · Brier Skill {bss:+.3f} (0 초과여야 기준율보다 나음) · ECE {ece:.1%} (≤{MAX_ECE:.0%}){hit_txt}",
                 n=n, brier_skill=bss, ece=ece, accuracy=cal.get("accuracy"))


# ------------------------------------------------------------------ ③ AI 알파
def ai_alpha(books: dict[str, pd.Series], ai_book: str = "attr-full", prefix: str = "") -> dict | None:
    """AI 알파 분리: 전체 = 거부권(veto−core) + 위성(full−veto). 각각 누적·t·90% CI."""
    core, veto, full = (books.get(f"{prefix}attr-{k}") for k in ("core", "veto", "full"))
    main = books.get(f"{prefix}{ai_book}")
    if core is None or main is None:
        return None
    tot = relative(main, core)
    if tot is None:
        return None
    parts = {}
    if veto is not None:
        parts["veto"] = relative(veto, core)
        if full is not None and ai_book.endswith("full"):
            parts["satellite"] = relative(full, veto)
    clean = lambda r: None if r is None else {k: v for k, v in r.items() if k not in ("daily",)}  # noqa: E731
    return {"total": clean(tot), "daily": tot["daily"], **{k: clean(v) for k, v in parts.items()}}


def ai_alpha_step(aa: dict | None) -> dict:
    if aa is None:
        return _step("ai", "③ AI 가 실제로 추가 수익을 만들었나", "insufficient", "기록 없음",
                     "AI 장부와 코어 장부(같은 가격·같은 규칙)가 쌓이면 차이를 계산합니다.")
    t = aa["total"]
    parts = " · ".join(f"{lab} {aa[k]['excess']:+.2%}p (t={aa[k]['t']})" for k, lab in (("veto", "거부권"), ("satellite", "위성"))
                       if aa.get(k))
    if t["days"] < MIN_DAYS or (t["excess"] > 0 and t["t"] <= T_PASS):
        status = "insufficient"
    else:
        status = "pass" if t["excess"] > 0 and t["t"] > T_PASS else "fail"
    return _step("ai", "③ AI 가 실제로 추가 수익을 만들었나", status, f"{t['excess']:+.2%}p",
                 f"AI 장부 {t['a']:+.2%} vs 코어 장부 {t['b']:+.2%} ({t['days']}거래일) · t={t['t']} (>{T_PASS} 필요) · "
                 f"연 {t['ann']:+.1%} [90% {t['ci90_ann'][0]:+.1%} ~ {t['ci90_ann'][1]:+.1%}]" + (f" · {parts}" if parts else "")
                 + (" · 아직 우연과 구분 안 됨" if t["excess"] > 0 and t["t"] <= T_PASS and t["days"] >= MIN_DAYS else ""),
                 excess=t["excess"], t=t["t"], days=t["days"])


# ------------------------------------------------------------------ ④ 비용
def cost_step(aa: dict | None, ai_cost_gap: float, main: dict | None, main_cost_pct: float) -> dict:
    """ai_cost_gap: (AI 장부 비용 − 코어 장부 비용) / 시작 자산 — AI 가 매매를 늘려 추가로 쓴 비용."""
    if aa is None and main is None:
        return _step("cost", "④ 비용을 빼도 남나", "insufficient", "기록 없음", "장부가 쌓이면 비용 전·후를 비교합니다.")
    txt, ok, days = [], True, 0
    if aa is not None:
        net = aa["total"]["excess"]
        gross = net + ai_cost_gap
        days = aa["total"]["days"]
        txt.append(f"AI 알파 비용 전 {gross:+.2%}p → AI 가 늘린 비용 {ai_cost_gap:.2%} → 비용 후 {net:+.2%}p")
        ok = ok and net > 0
    if main is not None:
        txt.append(f"운용 장부 비용 {main_cost_pct:.2%} 차감 후 벤치마크 대비 {main['excess']:+.2%}p")
        ok = ok and main["excess"] > 0
        days = max(days, main["days"])
    status = "insufficient" if days < MIN_DAYS else "pass" if ok else "fail"
    head = f"{aa['total']['excess']:+.2%}p" if aa else f"{main['excess']:+.2%}p"
    return _step("cost", "④ 비용을 빼도 남나", status, head, " · ".join(txt), ai_cost_gap=round(ai_cost_gap, 5))


# ------------------------------------------------------------------ ⑤ 실제 주문
def execution_step(ex: dict) -> dict:
    """ex: {live, tracking(relative dict or None), slippage_mean, assumed, fill_rate, n_orders, twin}"""
    tr = ex.get("tracking")
    slip, assumed = ex.get("slippage_mean"), ex.get("assumed")
    parts = []
    if tr:
        parts.append(f"실제 장부 vs 시뮬레이션({ex.get('twin')}) 차이 {tr['excess']:+.2%}p · 추적오차 {tr['te_ann']:.1%}/년")
    if slip is not None:
        parts.append(f"실측 슬리피지 {slip:.1f}bp (가정 {assumed:.0f}bp)")
    if ex.get("fill_rate") is not None:
        parts.append(f"체결률 {ex['fill_rate']:.0%} ({ex.get('n_orders', 0)}건)")
    if not ex.get("live"):
        return _step("exec", "⑤ 실제 주문에서도 같은 결과가 나오나", "insufficient", "가상매매만",
                     "실제(KIS 모의·실전) 주문 기록이 쌓여야 판정합니다" + (" — " + " · ".join(parts) if parts else ""), **{
                         k: v for k, v in ex.items() if k != "tracking"})
    if not tr or tr["days"] < 20 or ex.get("n_orders", 0) < 20:
        return _step("exec", "⑤ 실제 주문에서도 같은 결과가 나오나", "insufficient", f"주문 {ex.get('n_orders', 0)}건",
                     "실주문 20건 · 20거래일 이상 필요 — " + " · ".join(parts))
    ok = abs(tr["excess"]) <= MAX_TRACKING and (slip is None or slip <= assumed) and (ex.get("fill_rate") or 0) >= MIN_FILL
    return _step("exec", "⑤ 실제 주문에서도 같은 결과가 나오나", "pass" if ok else "fail", f"{tr['excess']:+.2%}p",
                 " · ".join(parts) + f" (기준: 차이 ±{MAX_TRACKING:.0%}p · 슬리피지 ≤ 가정 · 체결률 ≥ {MIN_FILL:.0%})")


# ------------------------------------------------------------------ ⑥ 반복성
def repeat_step(daily: pd.Series | None, regimes: pd.Series | None, label: str = "AI 알파") -> dict:
    if daily is None or len(daily) < BLOCK:
        return _step("repeat", "⑥ 그 성과가 반복되나", "insufficient", "기록 없음",
                     f"{BLOCK}거래일 구간이 3개 이상 쌓이면 구간별로 반복되는지 봅니다.", blocks=[], rows=[])
    d = daily.dropna()
    blocks = [float((1 + d.iloc[i:i + BLOCK]).prod() - 1) for i in range(0, len(d) - BLOCK + 1, BLOCK)]
    pos = sum(b > 0 for b in blocks)
    h = len(d) // 2
    halves = [float((1 + d.iloc[:h]).prod() - 1), float((1 + d.iloc[h:]).prod() - 1)]
    rows = []
    if regimes is not None:
        reg = regimes.copy()
        reg.index = pd.DatetimeIndex(reg.index)
        if reg.index.tz is not None:
            reg.index = reg.index.tz_convert(None)
        lab = reg.reindex(d.index, method="ffill")
        for r, g in d.groupby(lab):
            if r:
                rows.append({"regime": r, "label": REGIME_LABELS.get(r, r), "days": len(g),
                             "excess": round(float((1 + g).prod() - 1), 5)})
    if len(blocks) < 3:
        status = "insufficient"
    else:
        status = "pass" if pos / len(blocks) >= 2 / 3 and min(halves) > 0 else "fail" if pos / len(blocks) < 0.5 else "insufficient"
    return _step("repeat", "⑥ 그 성과가 반복되나", status, f"{pos}/{len(blocks)} 구간",
                 f"{label}: {BLOCK}거래일 구간 {len(blocks)}개 중 {pos}개 + · 전반 {halves[0]:+.2%}p / 후반 {halves[1]:+.2%}p"
                 + (f" · 국면 {sum(x['excess'] > 0 for x in rows)}/{len(rows)}개에서 +" if rows else ""),
                 blocks=[round(b, 5) for b in blocks], halves=[round(x, 5) for x in halves], rows=rows)


# ------------------------------------------------------------------ 수익 분해
def attribution(main: dict | None, books: dict[str, pd.Series], cost_pct: float | None, prefix: str = "") -> list[dict]:
    if main is None:
        return []
    mkt = main["beta"] * main["bench"]
    parts = [{"key": "market", "label": f"시장 (베타 {main['beta']:.2f} × 벤치마크)", "value": mkt}]
    rets = {k: _ret(_daily(books[f"{prefix}attr-{k}"])) for k in ("core", "veto", "full") if f"{prefix}attr-{k}" in books}
    core, veto, full = rets.get("core"), rets.get("veto"), rets.get("full")
    c = cost_pct or 0.0
    gross_other = main["book"] - mkt + c
    if core is not None:
        explained = core + c - mkt
        parts.append({"key": "core", "label": "코어 팩터 알파", "value": explained})
        if veto is not None:
            parts.append({"key": "veto", "label": "AI 거부권·긴급청산", "value": veto - core})
            explained += veto - core
        if full is not None and veto is not None:
            parts.append({"key": "satellite", "label": "AI 위성 베팅", "value": full - veto})
            explained += full - veto
        parts.append({"key": "cost", "label": "비용 (수수료·세금·슬리피지)", "value": -c})
        parts.append({"key": "other", "label": "기타 (실행 차이·현금 타이밍)", "value": gross_other - explained})
    else:
        parts.append({"key": "alpha", "label": "알파 (비용 전)", "value": gross_other})
        parts.append({"key": "cost", "label": "비용 (수수료·세금·슬리피지)", "value": -c})
    total = sum(p["value"] for p in parts)
    for p in parts:
        p["value"] = round(p["value"], 5)
    return parts + [{"key": "total", "label": "순수익 합계", "value": round(total, 5)}]


def proof_chain(main_eq: pd.Series, bench: pd.Series | None, *, books: dict[str, pd.Series] | None = None,
                data_check: dict | None = None, calibration: dict | None = None, hit: dict | None = None,
                main_cost: float = 0.0, ai_cost_gap: float = 0.0, execution: dict | None = None,
                regimes: pd.Series | None = None, ai_book: str = "attr-full", prefix: str = "") -> dict:
    books = books or {}
    bench = bench if bench is not None else pd.Series(dtype=float)
    main = alpha_stats(main_eq, bench) if len(bench) else None
    aa = ai_alpha(books, ai_book, prefix)
    start = float(_daily(main_eq).iloc[0]) if len(_daily(main_eq)) else 0.0
    main_cost_pct = main_cost / start if start else 0.0
    steps = [data_step(data_check or {}), calibration_step(calibration or {}, hit), ai_alpha_step(aa),
             cost_step(aa, ai_cost_gap, main, main_cost_pct), execution_step(execution or {}),
             repeat_step(aa["daily"] if aa else None, regimes)]
    passed = sum(s["status"] == "pass" for s in steps)
    failed = [s for s in steps if s["status"] == "fail"]
    pending = [s for s in steps if s["status"] == "insufficient"]
    if failed:
        v = {"status": "fail", "title": f"{failed[0]['title'][2:]} — 아니오", "message": failed[0]["detail"]}
    elif pending:
        v = {"status": "insufficient", "title": f"증명 진행 중 ({passed}/6 통과)",
             "message": f"다음 확인: {pending[0]['title'][2:]} — {pending[0]['detail']}"}
    else:
        v = {"status": "pass", "title": "6단계 모두 증명", "message": "시점 정확 · 정직한 확률 · AI 추가수익 · 비용 후 · 실주문 동일 · 반복"}
    ai_out = None if aa is None else {k: v_ for k, v_ in aa.items() if k != "daily"}
    return {"headline": {"excess": main["excess"] if main else None, "alpha_ann": main["alpha_ann"] if main else None,
                         "book": main["book"] if main else _ret(_daily(main_eq)), "bench": main["bench"] if main else None,
                         "ir": main["ir"] if main else None, "days": main["days"] if main else len(_daily(main_eq)),
                         "ai_excess": aa["total"]["excess"] if aa else None, "ai_t": aa["total"]["t"] if aa else None},
            "curve": main["curve"] if main else [], "ai_curve": aa["total"]["curve"] if aa else [],
            "ai_alpha": ai_out, "steps": steps, "passed": passed, "verdict": v,
            "attribution": attribution(main, books, main_cost_pct, prefix)}


# 예전 이름 호환
net_alpha = proof_chain

__all__ = ["proof_chain", "net_alpha", "relative", "alpha_stats", "ai_alpha", "data_step", "calibration_step",
           "ai_alpha_step", "cost_step", "execution_step", "repeat_step", "attribution"]

"""전략 건강검진 — "지금 손실이 과거에 검증된 범위 안인가?"

실전에서 돈을 잃는 가장 흔한 이유는 전략이 아니라 **정상 범위의 손실을 보고 규칙을 버리는 것**,
또는 반대로 **전략이 망가졌는데 계속 들고 있는 것**이다. 둘을 구분하려면 기준이 있어야 한다.

기준(REFERENCE)은 실제 KRX 데이터(2011~2026, 시점 기준 상위 100 유니버스, 거래비용 포함)로
코어 전략(모멘텀+저변동성+52주고점, 상위 20, 200일선 추세 필터)을 백테스트한 자산곡선에서
`reference_from_curve()` 로 계산한 값이다 (docs/RESEARCH_KRX.md 8장).

판정:
    ok       과거 검증 범위의 정상 구간 → 규칙대로 유지
    warn     과거 하위 5% 구간 (드물지만 있었던 일) → 유지하되 원인 점검 (시장 전체인가, 전략 고유인가)
    critical 과거 16년 동안 한 번도 없던 수준 → 신규 매수 중단 검토, 전략 재검증
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

TD_YEAR = 252

# reference_from_curve(코어+추세200 백테스트 2011-01~2026-09, 연도별 실제 매도세율 적용, KOSPI 시총가중 대리지수)
REFERENCE: dict[str, float] = {
    "worst_drawdown": -0.434, "dd_p95": -0.283,
    "worst_1y_return": -0.259, "p5_1y_return": -0.145,
    "worst_1y_excess": -1.234, "p5_1y_excess": -0.399,
    "worst_6m_excess": -0.539, "p5_6m_excess": -0.209,
    "vol60_p95": 0.267, "vol60_max": 0.534, "vol60_median": 0.112,
    "longest_underwater_days": 1917,
    "cagr": 0.076, "positive_1y_share": 0.546, "beat_kospi_1y_share": 0.481,
}

STATUS_ORDER = {"insufficient": -1, "ok": 0, "warn": 1, "critical": 2}
ACTIONS = {
    "ok": "과거 검증 범위 안 — 규칙대로 유지하세요. 손실 구간도 전략의 일부입니다.",
    "warn": "과거 하위 5% 구간 — 규칙은 유지하되 원인을 점검하세요 (시장 전체 하락인지, 전략만의 부진인지). "
            "추가 입금·비중 확대는 보류.",
    "critical": "과거 16년 검증에서 한 번도 없던 수준 — 신규 매수를 멈추고(킬스위치) 전략을 재검증하세요. "
                "보유분 일괄 매도는 급하게 하지 말고 원인 확인 후 결정.",
    "insufficient": "운용 기간이 짧아 판단할 수 없습니다 (최소 20거래일).",
}


@dataclass
class Check:
    name: str
    label: str
    value: float | None
    warn: float
    critical: float
    higher_is_worse: bool = False
    status: str = "insufficient"
    note: str = ""

    def judge(self) -> Check:
        if self.value is None or not np.isfinite(self.value):
            self.status = "insufficient"
            return self
        v = -self.value if self.higher_is_worse else self.value
        w = -self.warn if self.higher_is_worse else self.warn
        c = -self.critical if self.higher_is_worse else self.critical
        self.status = "critical" if v < c else "warn" if v < w else "ok"
        return self


def format_value(check: dict) -> str:
    v = check.get("value")
    if v is None:
        return "-"
    if check["name"] == "underwater":
        return f"{v:.0f}일"
    if check["name"] == "factor_ic":
        return f"t {v:+.2f}"
    return f"{v:+.1%}"


def _drawdown(eq: pd.Series) -> pd.Series:
    return eq / eq.cummax() - 1


def _underwater_days(eq: pd.Series) -> tuple[int, int]:
    """(현재 고점 이후 경과 거래일, 역대 최장)."""
    peak = eq.cummax()
    under = (eq < peak).astype(int)
    runs = under.groupby((under == 0).cumsum()).cumsum()
    return int(runs.iloc[-1]), int(runs.max())


def _daily(s: pd.Series) -> pd.Series:
    s = s.dropna()
    idx = pd.DatetimeIndex(s.index)
    s = pd.Series(s.to_numpy(dtype=float), index=idx.tz_localize(None) if idx.tz is not None else idx)
    return s.groupby(s.index.normalize()).last()


def reference_from_curve(eq: pd.Series, bench: pd.Series | None = None) -> dict:
    """백테스트 자산곡선에서 판정 기준을 계산 (기준 갱신·재현용)."""
    eq = _daily(eq)
    dd = _drawdown(eq)
    r1 = eq.pct_change(TD_YEAR).dropna()
    vol = eq.pct_change().rolling(60).std().dropna() * np.sqrt(TD_YEAR)
    out = {"worst_drawdown": float(dd.min()),
           "dd_p95": float(dd.quantile(0.05)),
           "worst_1y_return": float(r1.min()), "p5_1y_return": float(r1.quantile(0.05)),
           "vol60_p95": float(vol.quantile(0.95)), "vol60_max": float(vol.max()), "vol60_median": float(vol.median()),
           "longest_underwater_days": _underwater_days(eq)[1],
           "cagr": float((eq.iloc[-1] / eq.iloc[0]) ** (TD_YEAR / len(eq)) - 1),
           "positive_1y_share": float((r1 > 0).mean())}
    if bench is not None:
        b = _daily(bench).reindex(eq.index).ffill()
        x1 = (r1 - b.pct_change(TD_YEAR).reindex(r1.index)).dropna()
        x6 = (eq.pct_change(TD_YEAR // 2) - b.pct_change(TD_YEAR // 2)).dropna()
        out |= {"worst_1y_excess": float(x1.min()), "p5_1y_excess": float(x1.quantile(0.05)),
                "worst_6m_excess": float(x6.min()), "p5_6m_excess": float(x6.quantile(0.05)),
                "beat_kospi_1y_share": float((x1 > 0).mean())}
    return {k: round(v, 3) if isinstance(v, float) else v for k, v in out.items()}


def evaluate(equity: pd.Series, bench: pd.Series | None = None, ref: dict | None = None,
             factor_ic: dict | None = None) -> dict:
    """실제 운용 자산곡선(일별)을 기준과 비교.

    equity: 계좌 평가금액 시계열 (입출금이 있으면 그 효과를 뺀 값이어야 정확)
    bench:  KOSPI 종가 시계열
    factor_ic: factor_ic_history() 결과 (선택) — 팩터 자체가 아직 작동하는지
    """
    ref = {**REFERENCE, **(ref or {})}
    eq = _daily(equity) if equity is not None and len(equity) else pd.Series(dtype=float)
    n = len(eq)
    checks: list[Check] = []
    if n >= 2:
        dd = float(_drawdown(eq).iloc[-1])
        cur_uw, _ = _underwater_days(eq)
    else:
        dd, cur_uw = None, None
    checks.append(Check("drawdown", "고점 대비 하락", dd, ref["dd_p95"], ref["worst_drawdown"],
                        note=f"과거 하위 5% {ref['dd_p95']:.0%} · 최악 {ref['worst_drawdown']:.0%}"))
    ret1 = float(eq.iloc[-1] / eq.iloc[-TD_YEAR - 1] - 1) if n > TD_YEAR else None
    checks.append(Check("return_1y", "최근 1년 수익률", ret1, ref["p5_1y_return"], ref["worst_1y_return"],
                        note=f"과거 하위 5% {ref['p5_1y_return']:.0%} · 최악 {ref['worst_1y_return']:.0%}"))
    vol = float(eq.pct_change().iloc[-60:].std() * np.sqrt(TD_YEAR)) if n >= 60 else None
    checks.append(Check("vol60", "최근 60일 변동성(연율)", vol, ref["vol60_p95"], ref["vol60_max"],
                        higher_is_worse=True,
                        note=f"평소 {ref['vol60_median']:.0%} · 상위 5% {ref['vol60_p95']:.0%} · 최고 {ref['vol60_max']:.0%}"))
    checks.append(Check("underwater", "고점 회복까지 경과(거래일)", float(cur_uw) if n >= 20 else None,
                        ref["longest_underwater_days"] * 0.5, ref["longest_underwater_days"], higher_is_worse=True,
                        note=f"역대 최장 {ref['longest_underwater_days']}거래일 (약 {ref['longest_underwater_days'] / TD_YEAR:.1f}년)"))
    if bench is not None and len(bench) and n >= 2:
        b = _daily(bench).reindex(eq.index).ffill()
        x6 = (float(eq.iloc[-1] / eq.iloc[-127] - 1) - float(b.iloc[-1] / b.iloc[-127] - 1)) if n > 126 else None
        x1 = (float(eq.iloc[-1] / eq.iloc[-TD_YEAR - 1] - 1) - float(b.iloc[-1] / b.iloc[-TD_YEAR - 1] - 1)) \
            if n > TD_YEAR else None
        checks.append(Check("excess_6m", "최근 6개월 KOSPI 대비", x6, ref["p5_6m_excess"], ref["worst_6m_excess"],
                            note=f"과거 하위 5% {ref['p5_6m_excess']:+.0%} · 최악 {ref['worst_6m_excess']:+.0%} "
                                 "(급반등장에서 뒤처지는 건 이 전략의 알려진 약점)"))
        checks.append(Check("excess_1y", "최근 1년 KOSPI 대비", x1, ref["p5_1y_excess"], ref["worst_1y_excess"],
                            note=f"과거 하위 5% {ref['p5_1y_excess']:+.0%} · 최악 {ref['worst_1y_excess']:+.0%}"))
    if factor_ic and factor_ic.get("n", 0) >= 6:
        # 월별 IC 는 잡음이 크다 → 평균이 아니라 t값으로 판정 (t < -1 경고, t < -2 팩터가 반대로 작동 → 위험)
        checks.append(Check("factor_ic", "팩터 IC t값 (최근 12개월)", factor_ic["t"], -1.0, -2.0,
                            note=f"평균 IC {factor_ic['mean']:+.3f}, {factor_ic['n']}개월 · 1년 단위로는 음수인 해도 흔함"))
    for c in checks:
        c.judge()
    judged = [c for c in checks if c.status != "insufficient"]
    status = max((c.status for c in judged), key=STATUS_ORDER.get) if judged else "insufficient"
    return {"status": status, "action": ACTIONS[status], "days": n,
            "checks": [asdict(c) for c in checks], "reference": ref,
            "expectations": {"cagr": ref["cagr"], "positive_1y_share": ref["positive_1y_share"],
                             "beat_kospi_1y_share": ref["beat_kospi_1y_share"]},
            "as_of": str(eq.index[-1].date()) if n else None}


def factor_ic_history(bars: dict[str, pd.DataFrame], weights: dict | None = None, months: int = 12,
                      horizon: int = 20, universe_fn=None) -> dict:
    """최근 N개월 월말마다 코어 점수 vs 이후 20거래일 수익률 순위상관(IC). 팩터가 여전히 작동하는지."""
    from ..engines.factors import factor_score_cross_section, price_factors
    closes = pd.DataFrame({s: b["close"] for s, b in bars.items() if s != "KOSPI" and len(b) >= 260})
    if closes.shape[1] < 10:
        return {"n": 0}
    closes = closes.sort_index()
    idx = closes.index
    month_ends = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).last().tolist()
    month_ends = [t for t in month_ends if idx.get_loc(t) + horizon < len(idx)][-months:]
    facs = {s: price_factors(closes[s].dropna()) for s in closes}
    ics = []
    for t in month_ends:
        members = universe_fn(t) if universe_fn else None
        rows = {s: f.loc[t] for s, f in facs.items() if t in f.index and (members is None or s in members)}
        if len(rows) < 10:
            continue
        score = factor_score_cross_section(pd.DataFrame(rows).T, weights)
        fwd = closes.iloc[idx.get_loc(t) + horizon] / closes.loc[t] - 1
        both = pd.concat([score, fwd.reindex(score.index)], axis=1).dropna()
        if len(both) >= 10:
            ics.append(float(both.iloc[:, 0].rank().corr(both.iloc[:, 1].rank())))
    if not ics:
        return {"n": 0}
    a = np.array(ics)
    t_stat = float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if len(a) > 1 and a.std(ddof=1) > 0 else 0.0
    return {"n": len(a), "mean": round(float(a.mean()), 4), "t": round(t_stat, 2),
            "last": [round(x, 3) for x in ics[-12:]]}


__all__ = ["REFERENCE", "evaluate", "format_value", "reference_from_curve", "factor_ic_history"]

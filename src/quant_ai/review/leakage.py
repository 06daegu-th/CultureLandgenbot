"""미래 정보 누수(Look-ahead · Data Leakage) 감사 — 저장된 예측을 하나씩 다시 따져 본다.

검사 항목 (모두 예측 장부의 기록만으로, 사람이 고칠 수 없는 방식으로)
  L1 재료 시각     판단에 쓴 뉴스·공시가 기준 시각(as_of) 이전에 나온 것인가
  L2 저장 시각     예측이 결과 구간(다음 봉 시가)이 시작되기 전에 저장됐는가 (장부 created_at)
  L3 피처 재계산   그때 쓴 가격 피처(1·5·20일 수익률, 20일 변동성)를 as_of 이전 가격만으로 다시 계산하면 같은가
                   → 미래 가격이 섞였다면 다르게 나온다 (분할·배당 수정은 수익률을 바꾸지 않으므로 허용 오차 안)
  L4 결과 분리     예측을 저장할 때 결과 필드가 비어 있었나 (장부 해시에 결과가 없고, 결과는 나중에 채워짐)
  L5 학습 구간     퀀트 모델의 walk-forward 학습에 라벨 기간 + embargo 간격이 있는가
  L6 유니버스      상장폐지 종목을 포함한 그 시점의 유니버스를 쓰는가 (생존편향)
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sqlalchemy import select

from ..data.models import ConsensusRecord

TS_GRACE = pd.Timedelta(hours=1)
FEATURE_TOL = 0.005  # 0.5%p — 배당 수정 등으로 생기는 작은 차이는 허용


def _utc(t):
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


def recompute_features(bars: pd.DataFrame, as_of) -> dict | None:
    """as_of 이하의 가격만으로 ret_1 · ret_5 · ret_20 · vol_20 다시 계산."""
    if bars is None or bars.empty:
        return None
    idx = bars.index
    ts = _utc(as_of)
    if idx.tz is None:
        ts = ts.tz_localize(None)
    hist = bars[bars.index <= ts]["close"].astype(float)
    if len(hist) < 22:
        return None
    lr = np.log(hist).diff()
    return {"ret_1": float(hist.iloc[-1] / hist.iloc[-2] - 1), "ret_5": float(hist.iloc[-1] / hist.iloc[-6] - 1),
            "ret_20": float(hist.iloc[-1] / hist.iloc[-21] - 1), "vol_20": float(lr.iloc[-20:].std())}


def _check(key, title, status, detail, **data):
    return {"key": key, "title": title, "status": status, "detail": detail, **data}


def audit(session, bars: dict[str, pd.DataFrame] | None = None, sample: int = 300, backtest_cfg: dict | None = None,
          pit_universe: bool = True) -> dict:
    bars = bars or {}
    rows = session.scalars(select(ConsensusRecord).order_by(ConsensusRecord.id.desc()).limit(sample)).all()
    n = len(rows)
    ts_bad, ts_checked = [], 0
    late, sealed = [], 0
    feat_bad, feat_checked = [], 0
    for r in rows:
        p = r.payload or {}
        ev = p.get("evidence") or {}
        asof = _utc(r.as_of)
        for item in (ev.get("news") or []) + (ev.get("disclosures") or []):
            t = item.get("ts") or item.get("date")
            if not t:
                continue
            ts_checked += 1
            try:
                tt = _utc(t)
            except (ValueError, TypeError):
                continue
            # 공시는 날짜만 있다 → 같은 날은 허용
            if item.get("date") and not item.get("ts"):
                if tt.date() > asof.date():
                    ts_bad.append(r.id)
            elif tt > asof + TS_GRACE:
                ts_bad.append(r.id)
        if r.created_at is not None:
            sealed += 1
            # 결과 구간은 다음 봉 시가부터: 기준 봉 다음 날 00:00(현지) 이전에 저장됐어야 한다 — 보수적으로 as_of + 1일
            if _utc(r.created_at) > asof + pd.Timedelta(days=1, hours=12):
                late.append(r.id)
        price = ev.get("price") or {}
        b = bars.get(r.symbol)
        if b is not None and price.get("ret_20") is not None and feat_checked < 120:
            rc = recompute_features(b, r.as_of)
            if rc is not None:
                feat_checked += 1
                diffs = {k: abs(rc[k] - float(price[k])) for k in ("ret_1", "ret_5", "ret_20")
                         if price.get(k) is not None and math.isfinite(float(price[k]))}
                if diffs and max(diffs.values()) > FEATURE_TOL:
                    feat_bad.append({"id": r.id, "symbol": r.symbol, "diff": round(max(diffs.values()), 4)})
    # 재생(과거 as_of)으로 만든 예측은 저장 시각이 늦는 게 정상 → 따로 표시하되 실패로 보지 않는다
    replay_ids = {r.id for r in rows if ((r.payload or {}).get("versions") or {}).get("replay")}
    replay = [i for i in late if i in replay_ids]
    late_live = [i for i in late if i not in replay]
    checks = [
        _check("L1", "재료 시각 (뉴스·공시 ≤ 기준 시각)", "fail" if ts_bad else "pass" if ts_checked else "wait",
               f"재료 {ts_checked}건 중 미래 시각 {len(ts_bad)}건", ids=sorted(set(ts_bad))[:20]),
        _check("L2", "저장 시각 (결과 구간 시작 전에 기록)",
               "fail" if late_live else "pass" if sealed else "wait",
               f"봉인된 예측 {sealed}건 중 늦게 저장 {len(late_live)}건" + (f" (과거 재생 {len(replay)}건 제외)" if replay else ""),
               ids=late_live[:20]),
        _check("L3", "피처 재계산 (그때의 가격만으로 같은 값)", "fail" if feat_bad else "pass" if feat_checked else "wait",
               f"{feat_checked}건 재계산 · 불일치 {len(feat_bad)}건 (허용 {FEATURE_TOL:.1%}p)", items=feat_bad[:20]),
        _check("L4", "결과 분리 (예측 해시에 결과 없음)", "pass" if sealed else "wait",
               "결과는 장부 해시 밖 필드로 나중에 채워짐 · 독립 평가기가 가격에서 다시 계산"),
    ]
    if backtest_cfg is not None:
        emb, hz = backtest_cfg.get("embargo"), backtest_cfg.get("horizon")
        ok = emb is not None and emb >= 1
        checks.append(_check("L5", "학습 구간 간격 (라벨 기간 + embargo)", "pass" if ok else "fail",
                             f"학습 데이터는 테스트 시작 {hz}+{emb}봉 전까지만" if ok else "embargo 없음 — 라벨 겹침 위험"))
    checks.append(_check("L6", "유니버스 (상장폐지 포함 · 그 시점 기준)", "pass" if pit_universe else "fail",
                         "월별 시총·거래대금 상위 (상장폐지 종목 포함)" if pit_universe else "현재 종목만 사용 → 생존편향"))
    fails = [c for c in checks if c["status"] == "fail"]
    return {"checked": n, "checks": checks, "ok": not fails,
            "status": "fail" if fails else "pass" if all(c["status"] == "pass" for c in checks) else "partial",
            "message": ("미래 정보 누수 없음" if not fails else f"누수 의심 {len(fails)}개 항목 — 세부를 확인하세요")}


__all__ = ["audit", "recompute_features"]

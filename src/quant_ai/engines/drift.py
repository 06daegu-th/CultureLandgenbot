"""데이터 드리프트 — 모델이 배운 세상과 지금 세상이 달라졌나.

PSI (Population Stability Index) 로 기준 구간과 최근 구간의 분포를 비교한다.
  PSI < 0.10 안정 · 0.10~0.25 주의 · > 0.25 큰 변화 (재학습 후보를 만든다)

  · 피처 드리프트: 전 종목의 5·20일 수익률, 20일 변동성, 거래량 비율, RSI, 60일선 거리
      기준 = 최근 60거래일 이전 250거래일, 최근 = 마지막 20거래일
  · 예측 드리프트: AI 합의 상승확률 분포 (최근 20일 vs 그 전 120일) — 갑자기 한쪽으로 쏠리면 이상
  · 라벨 드리프트: 5일 뒤 상승 비율 (기준율) 변화 — 시장 성격 변화
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

STABLE, WARN = 0.10, 0.25
REF_DAYS, RECENT_DAYS, GAP_DAYS = 250, 20, 40


def psi(ref: np.ndarray, cur: np.ndarray, bins: int = 10) -> float | None:
    ref, cur = np.asarray(ref, float), np.asarray(cur, float)
    ref, cur = ref[np.isfinite(ref)], cur[np.isfinite(cur)]
    if len(ref) < 50 or len(cur) < 20:
        return None
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return None
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(ref, edges)[0] / len(ref)
    c = np.histogram(cur, edges)[0] / len(cur)
    r, c = np.clip(r, 1e-4, None), np.clip(c, 1e-4, None)
    return float(np.sum((c - r) * np.log(c / r)))


def status_of(v: float | None) -> str:
    return "unknown" if v is None else "stable" if v < STABLE else "warn" if v < WARN else "drift"


def _features(b: pd.DataFrame) -> pd.DataFrame:
    c, v = b["close"].astype(float), b["volume"].astype(float)
    lr = np.log(c).diff()
    d = c.diff()
    up, dn = d.clip(lower=0).rolling(14).mean(), (-d.clip(upper=0)).rolling(14).mean()
    return pd.DataFrame({
        "ret_5": c.pct_change(5), "ret_20": c.pct_change(20), "vol_20": lr.rolling(20).std(),
        "vol_ratio": v.rolling(5).mean() / v.rolling(60).mean(), "rsi_14": 100 - 100 / (1 + up / dn.replace(0, np.nan)),
        "dist_ma60": c / c.rolling(60).mean() - 1,
    })


LABELS = {"ret_5": "5일 수익률", "ret_20": "20일 수익률", "vol_20": "20일 변동성", "vol_ratio": "거래량 비율",
          "rsi_14": "RSI(14)", "dist_ma60": "60일선 거리"}


def feature_drift(bars: dict[str, pd.DataFrame]) -> list[dict]:
    frames = []
    for b in bars.values():
        if b is None or len(b) < REF_DAYS // 2:
            continue
        f = _features(b)
        f["pos"] = np.arange(len(f)) - len(f)  # 마지막 봉 = -1
        frames.append(f)
    if not frames:
        return []
    allf = pd.concat(frames)
    cur = allf[allf["pos"] >= -RECENT_DAYS]
    ref = allf[(allf["pos"] < -(RECENT_DAYS + GAP_DAYS)) & (allf["pos"] >= -(RECENT_DAYS + GAP_DAYS + REF_DAYS))]
    out = []
    for k, label in LABELS.items():
        v = psi(ref[k].to_numpy(), cur[k].to_numpy())
        out.append({"feature": k, "label": label, "psi": None if v is None else round(v, 4), "status": status_of(v),
                    "ref_mean": _m(ref[k]), "cur_mean": _m(cur[k])})
    return out


def _m(s: pd.Series) -> float | None:
    s = s.replace([np.inf, -np.inf], np.nan).dropna()
    return round(float(s.mean()), 5) if len(s) else None


def prediction_drift(session, now: datetime | None = None) -> dict:
    from sqlalchemy import select

    from ..data.models import ConsensusRecord
    now = now or datetime.now(UTC)
    rows = session.execute(select(ConsensusRecord.as_of, ConsensusRecord.prob_up, ConsensusRecord.realized_return)
                           .where(ConsensusRecord.as_of >= now - timedelta(days=200))).all()
    if not rows:
        return {"status": "unknown"}
    df = pd.DataFrame(rows, columns=["as_of", "p", "r"])
    df["as_of"] = pd.to_datetime(df["as_of"], utc=True)
    cut = pd.Timestamp(now - timedelta(days=28))
    cut = cut.tz_localize("UTC") if cut.tz is None else cut
    cur, ref = df[df["as_of"] >= cut], df[df["as_of"] < cut]
    v = psi(ref["p"].to_numpy(), cur["p"].to_numpy())
    lab_ref = ref["r"].dropna()
    lab_cur = df[(df["as_of"] >= cut - pd.Timedelta(days=30))]["r"].dropna()
    return {"psi": None if v is None else round(v, 4), "status": status_of(v), "n_ref": int(len(ref)), "n_cur": int(len(cur)),
            "mean_ref": _m(ref["p"]), "mean_cur": _m(cur["p"]),
            "up_rate_ref": round(float((lab_ref > 0).mean()), 4) if len(lab_ref) >= 30 else None,
            "up_rate_cur": round(float((lab_cur > 0).mean()), 4) if len(lab_cur) >= 20 else None}


def report(bars: dict[str, pd.DataFrame], session=None, now: datetime | None = None) -> dict:
    feats = feature_drift(bars)
    pred = prediction_drift(session, now) if session is not None else {"status": "unknown"}
    n_drift = sum(1 for f in feats if f["status"] == "drift")
    n_warn = sum(1 for f in feats if f["status"] == "warn")
    status = "drift" if n_drift >= 2 or pred.get("status") == "drift" else "warn" if n_drift or n_warn >= 2 else \
        "stable" if feats else "unknown"
    worst = max((f for f in feats if f["psi"] is not None), key=lambda f: f["psi"], default=None)
    msg = {"drift": "큰 변화 — 모델이 배운 시장과 달라졌습니다. 재학습 후보를 만듭니다",
           "warn": "일부 지표 분포가 변하는 중 — 지켜보는 단계", "stable": "안정 — 학습 때와 비슷한 시장",
           "unknown": "비교할 데이터가 부족합니다"}[status]
    return {"status": status, "message": msg, "features": feats, "prediction": pred,
            "worst": worst, "at": (now or datetime.now(UTC)).isoformat(), "thresholds": {"stable": STABLE, "drift": WARN}}


__all__ = ["psi", "report", "feature_drift", "prediction_drift", "status_of"]

"""Prediction Engine: '오를 것 같다 / 내릴 것 같다' 확률 예측.

모델은 P(horizon 봉 뒤 수익률 > 0) 를 출력한다. 모델 종류는 ``MODEL_FACTORIES`` 에 추가해 확장한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

MODEL_FACTORIES: dict[str, Callable[[], object]] = {
    "logistic": lambda: make_pipeline(SimpleImputer(), StandardScaler(), LogisticRegression(C=0.3, max_iter=500)),
    "gbm": lambda: HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=150, l2_regularization=1.0, min_samples_leaf=50,
    ),
}


@dataclass
class Prediction:
    symbol: str
    as_of: pd.Timestamp
    horizon: int
    prob_up: float
    direction: str  # up / down / neutral
    confidence: float  # |p - 0.5| * 2
    rationale: dict = field(default_factory=dict)


def direction_of(prob_up: float, band: float = 0.03) -> str:
    if prob_up >= 0.5 + band:
        return "up"
    if prob_up <= 0.5 - band:
        return "down"
    return "neutral"


class Predictor:
    def __init__(self, kind: str = "logistic", horizon: int = 5, target: str = "up"):
        if kind not in MODEL_FACTORIES:
            raise ValueError(f"알 수 없는 모델: {kind}")
        self.kind = kind
        self.horizon = horizon
        self.target = target  # up: P(오른다) · excess: P(같은 날 다른 종목들보다 더 오른다) — v20
        self.model = MODEL_FACTORIES[kind]()
        self.features: list[str] = []
        self._mean: pd.Series | None = None
        self.calibrator: LogisticRegression | None = None
        self._std: pd.Series | None = None

    @staticmethod
    def _fit_est(est, X, y, sample_weight):
        if not hasattr(est, "steps") and isinstance(X, pd.DataFrame):
            # GBM 은 값이 하나도 없는 열(예: 학습 초기 구간의 12개월 모멘텀)에서 구간 나누기가 실패한다 → 그 열만 0
            empty = [c for c in X.columns if X[c].notna().sum() == 0]
            if empty:
                X = X.assign(**{c: 0.0 for c in empty})
        if sample_weight is None:
            est.fit(X, y)
        elif hasattr(est, "steps"):  # Pipeline 은 마지막 단계 이름으로 전달해야 함
            est.fit(X, y, **{f"{est.steps[-1][0]}__sample_weight": sample_weight})
        else:
            est.fit(X, y, sample_weight=sample_weight)
        return est

    def fit(self, X: pd.DataFrame, y: pd.Series, sample_weight: np.ndarray | None = None,
            calibrate: bool = True, holdout: float = 0.2) -> Predictor:
        """학습 + 확률 보정.

        보정: 시간순 마지막 holdout 구간으로 Platt scaling 을 학습해 과신/과소신을 바로잡는다.
        (앙상블은 확률을 그대로 가중 결합하므로, 보정 안 된 확률은 신뢰도를 왜곡한다)
        """
        self.features = list(X.columns)
        if y.nunique() < 2:
            raise ValueError("라벨이 한 종류뿐이라 학습할 수 없음")
        self.calibrator = None
        if calibrate and len(X) >= 1000:
            ts = X.index.get_level_values(0) if isinstance(X.index, pd.MultiIndex) else X.index
            cut = pd.Series(ts).quantile(1 - holdout)
            early, late = np.asarray(ts < cut), np.asarray(ts >= cut)
            if y[early].nunique() == 2 and y[late].nunique() == 2 and late.sum() >= 200:
                base = self._fit_est(MODEL_FACTORIES[self.kind](), X[early], y[early],
                                     None if sample_weight is None else sample_weight[early])
                p = np.clip(base.predict_proba(X[late])[:, 1], 1e-4, 1 - 1e-4)
                z = np.log(p / (1 - p)).reshape(-1, 1)
                cal = LogisticRegression(C=1.0).fit(z, y[late])
                # 기울기를 [0.05, 1] 로 제한: 확률을 기저율 쪽으로 '줄이기'만 허용 (순위 뒤집기·증폭 금지).
                # holdout 구간에서 예측력이 없었으면 거의 기저율로 수축 → 과신 방지
                slope = float(np.clip(cal.coef_[0, 0], 0.05, 1.0))
                zc = z[:, 0] * slope
                base_rate = float(np.clip(y[late].mean(), 1e-3, 1 - 1e-3))
                # 기울기 고정 후 절편만 다시 맞춤 (평균 확률 = holdout 기저율 근처)
                intercept = float(np.log(base_rate / (1 - base_rate)) - np.mean(zc))
                cal.coef_[0, 0], cal.intercept_[0] = slope, intercept
                self.calibrator = cal
        self._fit_est(self.model, X, y, sample_weight)
        self._mean, self._std = X.mean(), X.std().replace(0, 1)
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        p = self.model.predict_proba(X[self.features])[:, 1]
        if getattr(self, "calibrator", None) is not None:
            p = np.clip(p, 1e-4, 1 - 1e-4)
            p = self.calibrator.predict_proba(np.log(p / (1 - p)).reshape(-1, 1))[:, 1]
        return p

    def predict(self, rows: pd.DataFrame) -> list[Prediction]:
        """rows: index=(ts, symbol) 인 피처 행들."""
        probs = self.predict_proba(rows)
        out = []
        for (ts, symbol), p, (_, feats) in zip(rows.index, probs, rows[self.features].iterrows()):
            z = ((feats - self._mean) / self._std).dropna()
            top = z.abs().sort_values(ascending=False).head(3).index
            out.append(Prediction(
                symbol=symbol, as_of=ts, horizon=self.horizon, prob_up=float(p),
                direction=direction_of(float(p)), confidence=float(abs(p - 0.5) * 2),
                rationale={"unusual_features": {k: round(float(z[k]), 2) for k in top}, "model": self.kind},
            ))
        return out

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)
        return path

    @staticmethod
    def load(path: Path) -> Predictor:
        return joblib.load(path)


def classification_metrics(prob: np.ndarray, label: np.ndarray, fwd_ret: np.ndarray | None = None,
                           ts=None) -> dict:
    """ts 를 주면 IC 는 '날짜별 횡단면 순위상관의 평균' (종목 간 순위를 매기는 전략의 표준 지표).

    기간을 섞은 pooled IC 는 기간마다 확률 스케일이 달라지면 왜곡되므로 참고용(ic_pooled)으로만 둔다.
    """
    prob, label = np.asarray(prob, float), np.asarray(label, float)
    if len(prob) == 0:
        return {"n": 0}
    pred_up = prob >= 0.5
    out = {
        "n": int(len(prob)),
        "accuracy": float(np.mean(pred_up == (label > 0.5))),
        "brier": float(np.mean((prob - label) ** 2)),
        "base_rate": float(label.mean()),
    }
    # 확신이 높은 예측만 따로 (실제로 매매에 쓰이는 구간)
    conf = np.abs(prob - 0.5) >= 0.05
    out["n_confident"] = int(conf.sum())
    out["accuracy_confident"] = float(np.mean(pred_up[conf] == (label[conf] > 0.5))) if conf.any() else None
    if fwd_ret is not None:
        r = np.asarray(fwd_ret, float)
        sig = np.where(pred_up, 1.0, -1.0)
        out["ic_pooled"] = float(pd.Series(prob).corr(pd.Series(r), method="spearman"))
        out["ic"] = out["ic_pooled"]
        if ts is not None:
            df = pd.DataFrame({"ts": np.asarray(ts), "p": prob, "r": r})
            daily = [g["p"].corr(g["r"], method="spearman") for _, g in df.groupby("ts") if len(g) >= 3]
            daily = [x for x in daily if x == x]
            if daily:
                out["ic"] = float(np.mean(daily))
                out["ic_t"] = float(np.mean(daily) / (np.std(daily, ddof=1) / np.sqrt(len(daily)))) \
                    if len(daily) > 2 and np.std(daily) > 0 else None  # IC 의 t-통계량
        out["avg_signed_ret"] = float(np.mean(sig * r))
    return out

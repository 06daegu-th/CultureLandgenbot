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
    def __init__(self, kind: str = "logistic", horizon: int = 5):
        if kind not in MODEL_FACTORIES:
            raise ValueError(f"알 수 없는 모델: {kind}")
        self.kind = kind
        self.horizon = horizon
        self.model = MODEL_FACTORIES[kind]()
        self.features: list[str] = []
        self._mean: pd.Series | None = None
        self._std: pd.Series | None = None

    def fit(self, X: pd.DataFrame, y: pd.Series, sample_weight: np.ndarray | None = None) -> "Predictor":
        self.features = list(X.columns)
        if y.nunique() < 2:
            raise ValueError("라벨이 한 종류뿐이라 학습할 수 없음")
        est = self.model
        if sample_weight is None:
            est.fit(X, y)
        elif hasattr(est, "steps"):  # Pipeline 은 마지막 단계 이름으로 전달해야 함
            est.fit(X, y, **{f"{est.steps[-1][0]}__sample_weight": sample_weight})
        else:
            est.fit(X, y, sample_weight=sample_weight)
        self._mean, self._std = X.mean(), X.std().replace(0, 1)
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(X[self.features])[:, 1]

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
    def load(path: Path) -> "Predictor":
        return joblib.load(path)


def classification_metrics(prob: np.ndarray, label: np.ndarray, fwd_ret: np.ndarray | None = None) -> dict:
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
        out["ic"] = float(pd.Series(prob).corr(pd.Series(r), method="spearman"))
        out["avg_signed_ret"] = float(np.mean(sig * r))
    return out

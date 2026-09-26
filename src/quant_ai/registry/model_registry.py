"""검증된 모델만 재학습/교체 (Champion / Challenger).

생명주기:
    candidate ──(백테스트 게이트 통과)──▶ shadow ──(Shadow 게이트 통과)──▶ champion
        │                                   │
        └──────────── 실패 ──────────────────┴──▶ rejected
    기존 champion 은 새 champion 등장 시 retired.

Live 는 champion 만 사용한다. 새로 학습된 모델은 절대 바로 실매매에 투입되지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine

from ..data.db import session_scope
from ..data.models import ModelRecord
from ..engines.prediction import Predictor


@dataclass(frozen=True)
class PromotionGates:
    # 백테스트(아웃오브샘플) 게이트
    min_oos_predictions: int = 500
    min_accuracy: float = 0.51
    max_brier_excess: float = 0.002  # 기저율(climatology) Brier 대비 허용 초과분
    min_ic: float = 0.02  # 날짜별 횡단면 순위상관 평균 (예측력의 핵심)
    min_ic_t: float = 2.0  # IC 의 t-통계량 (IC 가 0 과 통계적으로 다른가)
    min_sharpe: float = 0.5
    max_drawdown: float = -0.25
    must_beat_champion_brier_by: float = 0.0005
    min_psr: float = 0.90  # P(진짜 Sharpe > 0) — 운으로 나온 백테스트 걸러내기
    min_stress_sharpe: float = 0.0  # 비용 2배 스트레스에서도 손실 전략이 아닐 것
    min_dsr: float = 0.50  # 다중검정 보정 (후보를 많이 만들수록 기준 상승)
    # Shadow 게이트
    min_shadow_days: int = 20
    min_shadow_accuracy: float = 0.50
    max_shadow_drawdown: float = -0.10


@dataclass
class GateResult:
    passed: bool
    failures: list[str]


def backtest_gate(metrics: dict, champion_metrics: dict | None, g: PromotionGates) -> GateResult:
    pred, strat = metrics["prediction"], metrics["strategy"]
    fails = []
    if pred.get("n", 0) < g.min_oos_predictions:
        fails.append(f"OOS 예측 수 {pred.get('n', 0)} < {g.min_oos_predictions}")
    if pred.get("accuracy", 0) < g.min_accuracy:
        fails.append(f"정확도 {pred.get('accuracy', 0):.3f} < {g.min_accuracy}")
    base = pred.get("base_rate", 0.5)
    climatology = base * (1 - base)  # 항상 기저율만 말하는 모델의 Brier
    if pred.get("brier", 1) > climatology + g.max_brier_excess:
        fails.append(f"Brier {pred.get('brier', 1):.4f} > 기저율 기준 {climatology:.4f} (확률 보정 불량)")
    if (pred.get("ic") or 0) < g.min_ic:
        fails.append(f"IC {pred.get('ic') or 0:.3f} < {g.min_ic} (순위 예측력 부족)")
    if pred.get("ic_t") is not None and pred["ic_t"] < g.min_ic_t:
        fails.append(f"IC t-stat {pred['ic_t']:.1f} < {g.min_ic_t} (예측력이 통계적으로 유의하지 않음)")
    if strat["sharpe"] < g.min_sharpe:
        fails.append(f"Sharpe {strat['sharpe']:.2f} < {g.min_sharpe}")
    if strat["max_drawdown"] < g.max_drawdown:
        fails.append(f"MDD {strat['max_drawdown']:.1%} < {g.max_drawdown:.0%}")
    if strat.get("psr") is not None and strat["psr"] < g.min_psr:
        fails.append(f"PSR {strat['psr']:.2f} < {g.min_psr} (Sharpe 가 우연일 가능성)")
    stress = metrics.get("stress")
    if stress is not None and stress.get("sharpe", 0) < g.min_stress_sharpe:
        fails.append(f"비용 2배 스트레스 Sharpe {stress['sharpe']:.2f} < {g.min_stress_sharpe}")
    if metrics.get("dsr") is not None and metrics["dsr"] < g.min_dsr:
        fails.append(f"DSR {metrics['dsr']:.2f} < {g.min_dsr} (시도 {metrics.get('n_trials')}회 다중검정 보정)")
    if champion_metrics:
        champ_brier = champion_metrics["prediction"].get("brier", 1)
        if pred.get("brier", 1) > champ_brier - g.must_beat_champion_brier_by:
            fails.append(f"champion Brier {champ_brier:.4f} 대비 개선 없음")
        if strat["sharpe"] < champion_metrics["strategy"]["sharpe"]:
            fails.append("champion Sharpe 보다 낮음")
    return GateResult(not fails, fails)


def shadow_gate(shadow: dict, g: PromotionGates) -> GateResult:
    fails = []
    if shadow.get("days", 0) < g.min_shadow_days:
        fails.append(f"Shadow 기간 {shadow.get('days', 0)}일 < {g.min_shadow_days}일")
    if shadow.get("accuracy", 0) < g.min_shadow_accuracy:
        fails.append(f"Shadow 정확도 {shadow.get('accuracy', 0):.3f} < {g.min_shadow_accuracy}")
    if shadow.get("max_drawdown", 0) < g.max_shadow_drawdown:
        fails.append(f"Shadow MDD {shadow['max_drawdown']:.1%} < {g.max_shadow_drawdown:.0%}")
    return GateResult(not fails, fails)


class ModelRegistry:
    def __init__(self, engine: Engine, artifacts_dir: Path, gates: PromotionGates | None = None):
        self.engine = engine
        self.dir = Path(artifacts_dir)
        self.gates = gates or PromotionGates()

    def champion(self) -> ModelRecord | None:
        with session_scope(self.engine) as s:
            return s.scalar(select(ModelRecord).where(ModelRecord.status == "champion")
                            .order_by(ModelRecord.created_at.desc()))

    def n_trials(self) -> int:
        """지금까지 등록된 후보 수 (DSR 다중검정 보정에 사용)."""
        from sqlalchemy import func
        with session_scope(self.engine) as s:
            return int(s.scalar(select(func.count()).select_from(ModelRecord)) or 0)

    def load(self, rec: ModelRecord) -> Predictor:
        return Predictor.load(Path(rec.artifact_path))

    def register_candidate(self, model: Predictor, name: str, metrics: dict, params: dict) -> ModelRecord:
        now = datetime.now(UTC)
        version = now.strftime("%Y%m%d%H%M%S%f")
        path = model.save(self.dir / f"{name}-{version}.joblib")
        with session_scope(self.engine) as s:
            rec = ModelRecord(name=name, version=version, created_at=now, status="candidate",
                              params=params, metrics=_jsonable(metrics), artifact_path=str(path))
            s.add(rec)
            s.flush()
            return rec

    def evaluate_candidate(self, model_id: int) -> GateResult:
        """백테스트 게이트 → 통과 시 shadow 로 승격."""
        champ = self.champion()
        with session_scope(self.engine) as s:
            rec = s.get(ModelRecord, model_id)
            result = backtest_gate(rec.metrics, champ.metrics if champ else None, self.gates)
            rec.status = "shadow" if result.passed else "rejected"
            rec.notes = "; ".join(result.failures) or "백테스트 게이트 통과 → Shadow 검증 시작"
        return result

    def evaluate_shadow(self, model_id: int, shadow_metrics: dict) -> GateResult:
        """Shadow 게이트 → 통과 시 champion 교체."""
        result = shadow_gate(shadow_metrics, self.gates)
        with session_scope(self.engine) as s:
            rec = s.get(ModelRecord, model_id)
            if rec.status != "shadow":
                raise ValueError(f"shadow 상태가 아님: {rec.status}")
            rec.shadow_metrics = _jsonable(shadow_metrics)
            if not result.passed:
                if shadow_metrics.get("days", 0) >= self.gates.min_shadow_days:
                    rec.status = "rejected"  # 기간은 채웠는데 성과 미달
                rec.notes = "; ".join(result.failures)
                return result
            for old in s.scalars(select(ModelRecord).where(ModelRecord.status == "champion")):
                old.status = "retired"
            rec.status = "champion"
            rec.notes = "Shadow 게이트 통과 → champion"
        return result


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, float) and obj != obj:  # NaN
        return None
    return obj

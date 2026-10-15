"""ML management schemas - mirror PROJECT_CONTEXT section 8.2 (MetricSet, ModelVersion, ModelEvaluation, DriftCheck)."""
from datetime import datetime

from pydantic import Field

from app.schemas.base import CamelModel


class MetricSet(CamelModel):
    auc_roc: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    accuracy: float | None = None
    false_positive_rate: float | None = None


class MetricsPassed(CamelModel):
    auc_roc: bool = False
    precision: bool = False
    recall: bool = False
    f1: bool = False
    accuracy: bool = False
    false_positive_rate: bool = False


class ModelVersionOut(CamelModel):
    version: str
    algorithm: str
    status: str
    metrics: MetricSet
    targets: MetricSet
    passed: MetricsPassed
    decision_threshold: float
    train_rows: int | None
    trained_at: datetime
    promoted_at: datetime | None


class ModelEvaluationOut(MetricSet):
    model_version: str
    evaluated_at: datetime
    dataset_label: str | None
    sample_size: int | None


class RetrainRequest(CamelModel):
    notes: str | None = Field(None, max_length=1000)


class RetrainAccepted(CamelModel):
    retraining_event_id: int


class RetrainStatusOut(CamelModel):
    id: int
    status: str            # QUEUED, RUNNING, SUCCEEDED or FAILED
    trigger_reason: str
    started_at: datetime
    finished_at: datetime | None
    new_model_version: str | None
    log: str | None


class DriftResult(CamelModel):
    feature: str
    psi: float
    severity: str          # GREEN, AMBER or RED


class DriftCheckOut(CamelModel):
    run_at: datetime
    model_version: str
    threshold: float
    drift_detected: bool
    results: list[DriftResult]
    sample_size: int | None = None


class HybridConfigIn(CamelModel):
    """Send only what you want to change; the rest keeps its current value."""
    alpha: float | None = Field(None, ge=0, le=1)
    beta: float | None = Field(None, ge=0, le=1)
    pass_threshold: float | None = Field(None, gt=0, lt=1)
    exception_threshold: float | None = Field(None, gt=0, lt=1)
    psi_threshold: float | None = Field(None, gt=0, le=1)

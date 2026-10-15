"""Model registry, predictions, monitoring (schema.sql section 7)."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ModelStatusEnum, RetrainStatusEnum, TradeStatusEnum


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(20), unique=True)
    algorithm: Mapped[str] = mapped_column(String(50), server_default="RandomForestClassifier")
    hyperparameters: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    feature_list: Mapped[list[str]] = mapped_column(JSONB)
    decision_threshold: Mapped[Decimal] = mapped_column(Numeric(4, 3), server_default="0.5")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    global_shap: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    baseline_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    train_rows: Mapped[int | None] = mapped_column(Integer)
    test_rows: Mapped[int | None] = mapped_column(Integer)
    artifact_path: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(ModelStatusEnum, server_default="CANDIDATE")
    trained_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    promoted_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class MLPrediction(Base):
    __tablename__ = "ml_predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trade_pk: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    trade_version: Mapped[int] = mapped_column(Integer)
    model_version_id: Mapped[int] = mapped_column(Integer, ForeignKey("model_versions.id"))
    features: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rule_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    ml_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    final_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    alpha: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    beta: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    classification: Mapped[str] = mapped_column(TradeStatusEnum)
    shap_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ModelEvaluation(Base):
    __tablename__ = "model_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version_id: Mapped[int] = mapped_column(Integer, ForeignKey("model_versions.id"))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dataset_label: Mapped[str | None] = mapped_column(String(100))
    auc_roc: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    precision_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    recall_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    f1_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    accuracy: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    false_positive_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    sample_size: Mapped[int | None] = mapped_column(Integer)


class RetrainingEvent(Base):
    __tablename__ = "retraining_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    triggered_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    trigger_reason: Mapped[str] = mapped_column(String(20))  # MANUAL / DRIFT / SCHEDULED / INITIAL
    status: Mapped[str] = mapped_column(RetrainStatusEnum, server_default="QUEUED")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_model_version_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("model_versions.id"))
    log: Mapped[str | None] = mapped_column(Text)


class DriftCheck(Base):
    __tablename__ = "drift_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version_id: Mapped[int] = mapped_column(Integer, ForeignKey("model_versions.id"))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    triggered_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    threshold: Mapped[Decimal] = mapped_column(Numeric(4, 3), server_default="0.2")
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    max_psi: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    drift_detected: Mapped[bool] = mapped_column(Boolean, server_default="false")

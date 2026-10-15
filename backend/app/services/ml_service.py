"""Model registry, retraining, promotion and drift checks (endpoints 37-44, FR-MM).

Rules of this module:
  * Training, metrics and PSI maths are NEVER done here - they come from the engine (app.engine).
  * Retraining runs as a background task with its OWN database session.
  * Only one PRODUCTION model exists at a time (also enforced by a DB unique index). Promotion
    swaps the in-memory model, so no restart is needed.
"""
import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.engine import InsufficientDataError, ModelLoadError, Predictor, compute_drift, drift_detected, train_model
from app.models import (
    Counterparty, DriftCheck, HistoricalTrade, MLPrediction, ModelEvaluation, ModelVersion, Notification,
    RetrainingEvent, Role, User,
)
from app.schemas.ml import (
    DriftCheckOut, MetricSet, MetricsPassed, ModelEvaluationOut, ModelVersionOut, RetrainStatusOut,
)
from app.services import audit_service, email_service, engine_adapter

logger = logging.getLogger("app.ml")

# Section 6.5 targets: metric -> (comparison, target). FPR passes if lower, accuracy if higher, the rest if >=.
TARGETS: dict[str, tuple[str, float]] = {
    "auc_roc": (">=", 0.90), "precision": (">=", 0.70), "recall": (">=", 0.85), "f1": (">=", 0.77),
    "accuracy": (">", 0.90), "false_positive_rate": ("<", 0.05),
}
HISTORY_COLUMNS = ("trade_id", "trade_type", "symbol", "quantity", "price", "counterparty", "trade_date",
                   "settlement_date", "account_id", "ssi_matched", "currency_matched", "trader_name", "settlement_failed")
ACTIVE_RETRAIN = ("QUEUED", "RUNNING")
TRAIN_OPTIONS: dict[str, Any] = {}   # extra keyword arguments for engine.train_model (tests use a smaller forest)
_VERSION = re.compile(r"^v(\d+)$")


# ------------------------------------------------------------------ registry (37-39)
def _passes(name: str, value: float | None) -> bool:
    if value is None:
        return False
    comparison, target = TARGETS[name]
    return value >= target if comparison == ">=" else value > target if comparison == ">" else value < target


def to_model_out(model: ModelVersion) -> ModelVersionOut:
    metrics = {name: (model.metrics or {}).get(name) for name in TARGETS}
    return ModelVersionOut(
        version=model.version, algorithm=model.algorithm, status=model.status, metrics=MetricSet(**metrics),
        targets=MetricSet(**{name: target for name, (_, target) in TARGETS.items()}),
        passed=MetricsPassed(**{name: _passes(name, value) for name, value in metrics.items()}),
        decision_threshold=float(model.decision_threshold), train_rows=model.train_rows,
        trained_at=model.trained_at, promoted_at=model.promoted_at)


def list_models(db: Session) -> list[ModelVersionOut]:
    rows = db.scalars(select(ModelVersion).order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())).all()
    return [to_model_out(row) for row in rows]


def _production(db: Session) -> ModelVersion | None:
    return db.scalar(select(ModelVersion).where(ModelVersion.status == "PRODUCTION"))


def get_production(db: Session) -> ModelVersionOut:
    model = _production(db)
    if model is None:
        raise NotFoundError("No production model yet - the application is running on business rules only")
    return to_model_out(model)


def metrics_history(db: Session, date_from: date | None, date_to: date | None, version: str | None) -> list[ModelEvaluationOut]:
    stmt = select(ModelEvaluation, ModelVersion.version).join(ModelVersion, ModelVersion.id == ModelEvaluation.model_version_id)
    zone = ZoneInfo(get_settings().app_timezone)
    if version:
        stmt = stmt.where(ModelVersion.version == version.strip())
    if date_from:
        stmt = stmt.where(ModelEvaluation.evaluated_at >= datetime.combine(date_from, time.min, tzinfo=zone))
    if date_to:
        stmt = stmt.where(ModelEvaluation.evaluated_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone))
    rows = db.execute(stmt.order_by(ModelEvaluation.evaluated_at, ModelEvaluation.id)).all()

    def number(value):
        return None if value is None else float(value)

    return [ModelEvaluationOut(
        model_version=model_version, evaluated_at=e.evaluated_at, dataset_label=e.dataset_label, sample_size=e.sample_size,
        auc_roc=number(e.auc_roc), precision=number(e.precision_score), recall=number(e.recall_score),
        f1=number(e.f1_score), accuracy=number(e.accuracy), false_positive_rate=number(e.false_positive_rate))
        for e, model_version in rows]


# ------------------------------------------------------------------ retrain (40-41)
def _next_version(db: Session, folder: Path) -> str:
    """v<n+1>, where n is the highest number used by a registered model or by a file in the model folder."""
    numbers = [int(m.group(1)) for v in db.scalars(select(ModelVersion.version)) if (m := _VERSION.match(v))]
    numbers += [int(m.group(1)) for f in folder.glob("model_v*.joblib") if (m := re.match(r"^model_v(\d+)$", f.stem))]
    return f"v{max(numbers, default=0) + 1}"


def _history_frame(db: Session) -> pd.DataFrame:
    """historical_trades as the same DataFrame engine.load_historical_csv returns."""
    columns = [getattr(HistoricalTrade, name) for name in HISTORY_COLUMNS]
    frame = pd.DataFrame(db.execute(select(*columns)).all(), columns=list(HISTORY_COLUMNS))
    frame["quantity"] = frame["quantity"].astype("int64")
    frame["price"] = frame["price"].astype("float64")
    for name in ("ssi_matched", "currency_matched", "settlement_failed"):
        frame[name] = frame[name].astype(bool)
    return frame


def register_trained_model(db: Session, result, trained_by: int | None, notes: str | None = None) -> ModelVersion:
    """model_versions row (CANDIDATE) + one model_evaluations row per month and for the holdout set."""
    data = result.to_dict()  # plain JSON types
    trained_at = datetime.now(timezone.utc)
    model = ModelVersion(
        version=data["version"], algorithm=data["algorithm"], hyperparameters=data["hyperparameters"],
        feature_list=data["feature_list"], decision_threshold=data["decision_threshold"], metrics=data["metrics"],
        global_shap=data["global_shap"], baseline_stats=data["baseline_stats"], train_rows=data["train_rows"],
        test_rows=data["test_rows"], artifact_path=str(Path(data["artifact_path"]).resolve()), status="CANDIDATE",
        trained_by=trained_by, trained_at=trained_at, notes=notes)
    db.add(model)
    db.flush()
    rows = list(data.get("monthly_evaluations") or [])
    rows.append({"dataset_label": "holdout_test", "sample_size": data["test_rows"], **data["metrics"]})
    db.add_all(ModelEvaluation(
        model_version_id=model.id, evaluated_at=trained_at, dataset_label=row.get("dataset_label"),
        auc_roc=row.get("auc_roc"), precision_score=row.get("precision"), recall_score=row.get("recall"),
        f1_score=row.get("f1"), accuracy=row.get("accuracy"), false_positive_rate=row.get("false_positive_rate"),
        sample_size=row.get("sample_size")) for row in rows)
    return model


def start_retrain(db: Session, user: User, notes: str | None, ip_address: str | None) -> RetrainingEvent:
    if db.scalar(select(RetrainingEvent.id).where(RetrainingEvent.status.in_(ACTIVE_RETRAIN)).limit(1)) is not None:
        raise ConflictError("A retraining run is already in progress - wait for it to finish")
    event = RetrainingEvent(triggered_by=user.id, trigger_reason="MANUAL", status="QUEUED",
                            log=f"Notes: {notes}\n" if notes else None)
    db.add(event)
    db.flush()
    audit_service.log(db, "RETRAIN_STARTED", "MODEL", actor=user, entity_id=event.id, ip_address=ip_address,
                      details={"retrainingEventId": event.id, "notes": notes})
    db.commit()
    logger.info("Retraining %s queued by %s", event.id, user.username)
    return event


def _finish_retrain(event_id: int, status: str, line: str, model_id: int | None = None) -> None:
    """Fresh session: the one used for training may be unusable after an error."""
    with SessionLocal() as db:
        event = db.get(RetrainingEvent, event_id)
        user = db.get(User, event.triggered_by) if event.triggered_by else None
        event.status, event.finished_at, event.new_model_version_id = status, datetime.now(timezone.utc), model_id
        event.log = (event.log or "") + line + "\n"
        version = db.scalar(select(ModelVersion.version).where(ModelVersion.id == model_id)) if model_id else None
        ok = status == "SUCCEEDED"
        audit_service.log(db, "RETRAIN_COMPLETED" if ok else "RETRAIN_FAILED", "MODEL", actor=user, entity_id=version or event_id,
                          details={"retrainingEventId": event_id, "modelVersion": version, "message": line})
        db.add(Notification(
            role_code="ML_ADMIN", type="RETRAIN_DONE", severity="INFO" if ok else "WARNING",
            title=(f"Retraining finished: model {version} is ready to review" if ok else "Retraining failed")[:150],
            message=line, link="/ml/models"))
        db.commit()


def run_retrain(event_id: int) -> None:
    """Background task entry point. Never raises."""
    try:
        with SessionLocal() as db:
            event = db.get(RetrainingEvent, event_id)
            event.status = "RUNNING"
            db.commit()
            history = _history_frame(db)
            folder = engine_adapter.model_dir()
            version = _next_version(db, folder)
            result = train_model(history, engine_adapter.get_reference_data(db), engine_adapter.get_engine_config(db),
                                 version, str(folder), **TRAIN_OPTIONS)
            model = register_trained_model(db, result, event.triggered_by, notes=f"Retraining run #{event_id}")
            db.commit()
            metrics = ", ".join(f"{name} {value}" for name, value in result.metrics.items())
            line = (f"Trained {version} on {len(history):,} historical trades "
                    f"(threshold {result.decision_threshold}; {metrics}). Status: CANDIDATE - promote it to use it.")
        _finish_retrain(event_id, "SUCCEEDED", line, model.id)
        logger.info("Retraining %s done: %s", event_id, line)
    except Exception as exc:  # noqa: BLE001 - reported on the retraining event, never to a request
        logger.exception("Retraining %s failed", event_id)
        try:
            _finish_retrain(event_id, "FAILED", f"Retraining failed: {exc}"[:1500])
        except Exception:  # noqa: BLE001
            logger.exception("Could not mark retraining %s as FAILED", event_id)


def get_retrain(db: Session, event_id: int) -> RetrainStatusOut:
    event = db.get(RetrainingEvent, event_id)
    if event is None:
        raise NotFoundError(f"Retraining run {event_id} not found")
    version = (db.scalar(select(ModelVersion.version).where(ModelVersion.id == event.new_model_version_id))
               if event.new_model_version_id else None)
    return RetrainStatusOut(id=event.id, status=event.status, trigger_reason=event.trigger_reason,
                            started_at=event.started_at, finished_at=event.finished_at, new_model_version=version, log=event.log)


def fail_interrupted_retrains(db: Session) -> int:
    """Called at startup: a run still QUEUED/RUNNING was cut off by a restart and will never finish."""
    events = db.scalars(select(RetrainingEvent).where(RetrainingEvent.status.in_(ACTIVE_RETRAIN))).all()
    for event in events:
        event.status, event.finished_at = "FAILED", datetime.now(timezone.utc)
        event.log = (event.log or "") + "The server restarted while this run was in progress.\n"
    db.commit()
    return len(events)


# ------------------------------------------------------------------ promote (42)
def _update_tiers_from_report(db: Session, artifact_path: str) -> int:
    """Counterparty tiers are recomputed on every training run (section 2.3): apply the new model's tiers."""
    artifact = Path(artifact_path)
    report = artifact.with_name(f"{artifact.stem}_report.json")
    if not report.is_file():
        return 0
    tiers = {name.strip().lower(): info for name, info in
             (json.loads(report.read_text(encoding="utf-8")).get("counterparty_tiers") or {}).items()}
    changed, now = 0, datetime.now(timezone.utc)
    for party in db.scalars(select(Counterparty)).all():
        info = tiers.get(party.name.lower())
        if info is None:
            continue
        party.historical_trade_count, party.historical_fail_count = int(info["trade_count"]), int(info["fail_count"])
        party.historical_fail_rate = round(float(info["fail_rate"]), 4)
        if party.risk_tier != int(info["risk_tier"]):
            changed += 1
        party.risk_tier, party.tier_label, party.tier_updated_at = int(info["risk_tier"]), info["tier_label"], now
    return changed


def promote(db: Session, version: str, user: User, ip_address: str | None) -> ModelVersionOut:
    model = db.scalar(select(ModelVersion).where(ModelVersion.version == version).with_for_update())
    if model is None:
        raise NotFoundError(f"Model {version} not found")
    if model.status == "PRODUCTION":
        raise ConflictError(f"Model {version} is already the production model")
    if model.status == "FAILED":
        raise ConflictError(f"Model {version} failed and cannot be promoted")
    artifact = engine_adapter.resolve_artifact(model.artifact_path)
    try:  # load BEFORE changing anything: a model that cannot be loaded must never become production
        predictor = Predictor.load(artifact)
    except ModelLoadError as exc:
        raise ConflictError(f"Model {version} cannot be loaded, so it was not promoted: {exc}") from exc

    previous = _production(db)
    if previous is not None:
        previous.status = "ARCHIVED"
        db.flush()  # free the "one production model" unique index before the next line
    model.status, model.promoted_by, model.promoted_at = "PRODUCTION", user.id, datetime.now(timezone.utc)
    tiers_changed = _update_tiers_from_report(db, artifact)
    audit_service.log(db, "MODEL_PROMOTED", "MODEL", actor=user, entity_id=version, ip_address=ip_address,
                      before_state={"production": previous.version if previous else None},
                      after_state={"production": version},
                      details={"metrics": model.metrics, "counterpartyTiersChanged": tiers_changed})
    db.commit()
    engine_adapter.set_predictor(predictor)   # hot swap: new trades are scored by this model from now on
    engine_adapter.invalidate_cache()
    logger.info("Model %s promoted by %s (previous: %s)", version, user.username, previous.version if previous else None)
    return to_model_out(model)


# ------------------------------------------------------------------ drift (43-44)
def _drift_out(check: DriftCheck, version: str) -> DriftCheckOut:
    return DriftCheckOut(run_at=check.run_at, model_version=version, threshold=float(check.threshold),
                         drift_detected=check.drift_detected, results=check.results, sample_size=check.sample_size)


def latest_drift(db: Session) -> DriftCheckOut:
    row = db.execute(select(DriftCheck, ModelVersion.version).join(ModelVersion, ModelVersion.id == DriftCheck.model_version_id)
                     .order_by(DriftCheck.run_at.desc(), DriftCheck.id.desc()).limit(1)).first()
    if row is None:
        raise NotFoundError("No drift check has been run yet")
    return _drift_out(*row)


def run_drift(db: Session, user: User | None, ip_address: str | None = None) -> DriftCheckOut:
    """Compare the features of recently scored trades with the production model's training data (PSI)."""
    model = _production(db)
    if model is None or not model.baseline_stats:
        raise ConflictError("A drift check needs a production model with baseline statistics")
    days = int(engine_adapter.get_setting(db, "drift_window_days", 30))
    threshold = float(engine_adapter.get_setting(db, "psi_threshold", 0.2))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    features = db.scalars(select(MLPrediction.features).where(MLPrediction.predicted_at >= since)).all()
    try:
        results = compute_drift(model.baseline_stats, features, threshold)
    except InsufficientDataError as exc:
        raise BadRequestError(f"{exc}. Only trades scored by the model in the last {days} days count.") from exc

    detected = drift_detected(results, threshold)
    check = DriftCheck(
        model_version_id=model.id, run_at=now, triggered_by=user.id if user else None, window_start=since.date(),
        window_end=now.date(), sample_size=len(features), threshold=threshold, results=results,
        max_psi=max((r["psi"] for r in results), default=None), drift_detected=detected)
    db.add(check)
    red = [r for r in results if r["severity"] == "RED"]
    summary = ", ".join(f"{r['feature']} {r['psi']:.2f}" for r in red[:8])
    audit_service.log(db, "DRIFT_CHECK_RUN", "MODEL", actor=user, entity_id=model.version, ip_address=ip_address,
                      details={"sampleSize": len(features), "threshold": threshold, "driftDetected": detected,
                               "maxPsi": check.max_psi, "red": len(red)})
    if detected:
        message = (f"Data drift detected for model {model.version}: {len(red)} feature(s) above PSI {threshold} "
                   f"({summary}) on {len(features):,} recent trades. Consider retraining.")
        db.add(Notification(role_code="ML_ADMIN", type="DRIFT_ALERT", severity="CRITICAL",
                            title=f"Data drift detected ({len(red)} features)"[:150], message=message, link="/ml/monitoring"))
        recipients = list(db.scalars(select(User.email).join(Role, Role.id == User.role_id)
                                     .where(Role.code == "ML_ADMIN", User.is_active.is_(True))))
        if recipients:
            email_service.queue_email(db, recipients, f"Trade Perfect: data drift detected for model {model.version}",
                                      message, "DRIFT_ALERT", created_by=user)
        audit_service.log(db, "DRIFT_ALERT", "MODEL", actor=user, entity_id=model.version,
                          details={"red": [{"feature": r["feature"], "psi": r["psi"]} for r in red]})
    db.commit()
    logger.info("Drift check for %s: %d trades, detected=%s", model.version, len(features), detected)
    return _drift_out(check, model.version)

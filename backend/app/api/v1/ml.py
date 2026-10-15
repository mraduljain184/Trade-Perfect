"""ML management endpoints 37-44 and 46 (PROJECT_CONTEXT section 8.1). HTTP only.

#45 (feature importance) and #47 (predict) live in ml_scoring.py, owned by the ML module.
"""
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_db, require_permission
from app.models import User
from app.schemas.base import ERR_AUTH, ErrorResponse
from app.schemas.common import HybridConfigOut
from app.schemas.ml import (
    DriftCheckOut, HybridConfigIn, ModelEvaluationOut, ModelVersionOut, RetrainAccepted, RetrainRequest, RetrainStatusOut,
)
from app.services import admin_service, config_service, email_service, ml_service

router = APIRouter(prefix="/ml", tags=["ML"])

ERR_400 = {400: {"model": ErrorResponse, "description": "Not possible with the current data or values"}}
ERR_404 = {404: {"model": ErrorResponse, "description": "Not found"}}
ERR_409 = {409: {"model": ErrorResponse, "description": "Not allowed in the current state"}}


# ---------------------------------------------------------------- registry
@router.get("/models", response_model=list[ModelVersionOut], responses=ERR_AUTH,
            summary="All model versions, newest first, with metrics, targets and pass/fail")
def list_models(db: Session = Depends(get_db), _: User = Depends(require_permission("MODEL_METRICS_VIEW"))):
    return ml_service.list_models(db)


@router.get("/models/production", response_model=ModelVersionOut, responses={**ERR_AUTH, **ERR_404},
            summary="The production model with metrics, targets and pass/fail per metric")
def production_model(db: Session = Depends(get_db), _: User = Depends(require_permission("MODEL_METRICS_VIEW"))):
    return ml_service.get_production(db)


@router.get("/metrics/history", response_model=list[ModelEvaluationOut], responses=ERR_AUTH,
            summary="Metric evaluations over time for the trend chart")
def metrics_history(date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
                    model_version: str | None = Query(None, alias="modelVersion", max_length=20),
                    db: Session = Depends(get_db), _: User = Depends(require_permission("MODEL_METRICS_VIEW"))):
    return ml_service.metrics_history(db, date_from, date_to, model_version)


# ---------------------------------------------------------------- retrain + promote
@router.post("/retrain", response_model=RetrainAccepted, status_code=202, responses={**ERR_AUTH, **ERR_409},
             summary="Train a new model version in the background (poll GET /ml/retrain/{id})")
def retrain(request: Request, background_tasks: BackgroundTasks, body: RetrainRequest | None = None,
            db: Session = Depends(get_db), user: User = Depends(require_permission("MODEL_RETRAIN"))) -> RetrainAccepted:
    event = ml_service.start_retrain(db, user, body.notes if body else None, client_ip(request))
    background_tasks.add_task(ml_service.run_retrain, event.id)
    return RetrainAccepted(retraining_event_id=event.id)


@router.get("/retrain/{event_id}", response_model=RetrainStatusOut, responses={**ERR_AUTH, **ERR_404},
            summary="Status and log of one retraining run")
def retrain_status(event_id: int, db: Session = Depends(get_db),
                   _: User = Depends(require_permission("MODEL_RETRAIN"))) -> RetrainStatusOut:
    return ml_service.get_retrain(db, event_id)


@router.post("/models/{version}/promote", response_model=ModelVersionOut, responses={**ERR_AUTH, **ERR_404, **ERR_409},
             summary="Make this version the production model (loaded at once, no restart)")
def promote(version: str, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_permission("MODEL_PROMOTE"))) -> ModelVersionOut:
    return ml_service.promote(db, version, user, client_ip(request))


# ---------------------------------------------------------------- drift
@router.get("/drift/latest", response_model=DriftCheckOut, responses={**ERR_AUTH, **ERR_404},
            summary="The most recent drift check (PSI per feature)")
def latest_drift(db: Session = Depends(get_db), _: User = Depends(require_permission("DRIFT_MONITOR"))) -> DriftCheckOut:
    return ml_service.latest_drift(db)


@router.post("/drift/run", response_model=DriftCheckOut, responses={**ERR_AUTH, **ERR_400, **ERR_409},
             summary="Run a drift check now; ML Admins are notified if any feature is above the PSI threshold")
def run_drift(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
              user: User = Depends(require_permission("DRIFT_MONITOR"))) -> DriftCheckOut:
    result = ml_service.run_drift(db, user, client_ip(request))
    background_tasks.add_task(email_service.send_pending)
    return result


# ---------------------------------------------------------------- hybrid weights
@router.get("/config", response_model=HybridConfigOut, responses=ERR_AUTH,
            summary="Hybrid weights and thresholds (alpha, beta, pass, exception, PSI)")
def get_ml_config(
    _: User = Depends(require_permission("HYBRID_WEIGHTS_CONFIG")),
    db: Session = Depends(get_db),
) -> HybridConfigOut:
    return HybridConfigOut(**config_service.get_hybrid_config(db))


@router.put("/config", response_model=HybridConfigOut, responses={**ERR_AUTH, **ERR_400},
            summary="Change hybrid weights / thresholds (alpha + beta must be 1; pass < exception)")
def put_ml_config(body: HybridConfigIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_permission("HYBRID_WEIGHTS_CONFIG"))) -> HybridConfigOut:
    return HybridConfigOut(**admin_service.update_hybrid_config(db, body, user, client_ip(request)))

"""Endpoint 53: health check (public)."""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.schemas.common import HealthOut
from app.services import engine_adapter

router = APIRouter(tags=["Ops"])
logger = logging.getLogger("app.health")


@router.get("/health", response_model=HealthOut, summary="Health check: API, database, production model")
def health(db: Session = Depends(get_db)) -> HealthOut:
    try:
        db.execute(text("SELECT 1"))
        predictor = engine_adapter.get_predictor()  # the model that is really loaded; None = rules-only mode
        return HealthOut(status="ok", db="ok", model=predictor.version if predictor else None)
    except Exception:  # noqa: BLE001 - health must answer even when the DB is down
        logger.exception("Health check: database not reachable")
        return HealthOut(status="degraded", db="error", model=None)
 
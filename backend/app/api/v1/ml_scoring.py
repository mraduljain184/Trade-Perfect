"""ML scoring API - owned by the ML module (PROJECT_CONTEXT 8.1 #45, #47 + additive batch).

Scores trades WITHOUT saving them (no DB writes, no audit, no repair-queue item).
It uses the SAME engine_adapter as POST /trades - same reference data from the DB, same
config, same business date and the same loaded production model - so a trade gets exactly
the same finalScore here as when it is booked.
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.engine import score_trades
from app.schemas.ml_scoring import (
    FeatureImportanceOut, PredictBatchInput, PredictBatchResult, PredictInput, PredictResult,
)
from app.services import engine_adapter

logger = logging.getLogger("app.ml_scoring")

router = APIRouter(prefix="/ml", tags=["ml-scoring"])


def _user_name(user) -> str | None:
    return getattr(user, "full_name", None) or getattr(user, "username", None)


def _score(db: Session, trades: list[PredictInput], user):
    refs = engine_adapter.get_reference_data(db)
    cfg = engine_adapter.get_engine_config(db)
    business_date = engine_adapter.get_business_date(db)
    predictor = engine_adapter.get_predictor()
    records = [t.engine_record(i, _user_name(user)) for i, t in enumerate(trades)]
    scored = score_trades(records, refs, cfg, business_date, predictor=predictor)
    items = [PredictResult.from_scored(s, business_date) for s in scored]
    return items, (predictor.version if predictor is not None else None), business_date


@router.post("/predict", response_model=PredictResult,
             summary="Risk score + explanation for one trade (not saved)")
def predict(body: PredictInput, db: Session = Depends(get_db),
            user=Depends(require_permission("MODEL_METRICS_VIEW", "TRADE_SUBMIT_SINGLE"))):
    items, _, _ = _score(db, [body], user)
    return items[0]


@router.post("/predict/batch", response_model=PredictBatchResult,
             summary="Risk scores for up to 1,000 trades (not saved)")
def predict_batch(body: PredictBatchInput, db: Session = Depends(get_db),
                  user=Depends(require_permission("MODEL_METRICS_VIEW", "TRADE_SUBMIT_BULK"))):
    items, version, business_date = _score(db, body.trades, user)
    return PredictBatchResult.from_results(items, version, business_date)


@lru_cache(maxsize=8)
def _global_shap(report_path: str) -> tuple:
    with open(report_path, encoding="utf-8") as fh:
        return tuple(json.load(fh).get("global_shap") or [])


@router.get("/feature-importance", response_model=list[FeatureImportanceOut],
            summary="Global feature importance (mean |SHAP|) of the production model")
def feature_importance(user=Depends(require_permission("FEATURE_IMPORTANCE_VIEW"))):
    predictor = engine_adapter.get_predictor()
    if predictor is None or not getattr(predictor, "path", ""):
        return []
    artifact = Path(predictor.path)
    report = artifact.with_name(f"{artifact.stem}_report.json")
    if not report.is_file():
        logger.warning("No training report next to %s - feature importance empty", artifact)
        return []
    return [FeatureImportanceOut.model_validate(row) for row in _global_shap(str(report))]

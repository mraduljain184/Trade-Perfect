"""Team endpoint 29 (PROJECT_CONTEXT section 8.1)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.models import User
from app.schemas.base import ERR_AUTH
from app.schemas.repair_queue import TeamMemberPerformance
from app.services import repair_queue_service

router = APIRouter(prefix="/team", tags=["Team"])


@router.get("/performance", response_model=list[TeamMemberPerformance], responses=ERR_AUTH,
            summary="Per Ops User: items assigned, in progress, resolved and average resolution hours")
def performance(db: Session = Depends(get_db),
                _: User = Depends(require_permission("TEAM_METRICS_VIEW"))) -> list[TeamMemberPerformance]:
    return repair_queue_service.team_performance(db)

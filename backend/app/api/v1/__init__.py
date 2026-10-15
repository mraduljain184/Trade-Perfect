"""All /api/v1 routers are collected here; main.py includes `api_router` once."""
from fastapi import APIRouter

from app.api.v1 import (
    admin, audit, auth, chat, dashboard, health, ml, ml_scoring, notifications, reference, repair_queue, team, trades, uploads,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(reference.router)
api_router.include_router(trades.router)
api_router.include_router(uploads.router)
api_router.include_router(dashboard.router)
api_router.include_router(repair_queue.router)
api_router.include_router(team.router)
api_router.include_router(audit.router)
api_router.include_router(ml.router)
api_router.include_router(ml_scoring.router)
api_router.include_router(admin.router)
api_router.include_router(notifications.router)
api_router.include_router(chat.router)
api_router.include_router(health.router)

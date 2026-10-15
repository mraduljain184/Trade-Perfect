"""FastAPI application entry point.  Run:  uvicorn app.main:app --reload   ->  http://localhost:8000/docs"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.api.v1.health import health
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.errors import register_exception_handlers
from app.core.logging import RequestLogMiddleware, setup_logging
from app.services import engine_adapter, ml_service, upload_service

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Trade Perfect API starting (CORS origins: %s)", settings.cors_origin_list)
    try:  # load the production model once; without one the API runs rules-only
        with SessionLocal() as db:
            engine_adapter.load_predictor(db)
            ml_service.fail_interrupted_retrains(db)
            interrupted = upload_service.fail_interrupted_batches(db)
            if interrupted:
                logger.warning("%d upload batch(es) were cut off by a restart and are now FAILED", interrupted)
    except Exception:  # noqa: BLE001 - the API must start even if the DB is not ready yet
        logger.exception("Could not check for a production model - running in rules-only mode")
    yield
    logger.info("Trade Perfect API stopped")


app = FastAPI(
    title="Trade Perfect API",
    version="1.0.0",
    description="AI-based Trade Booking Validation & Settlement Exception Handler. "
                "Log in with POST /api/v1/auth/login, then click **Authorize** and paste the accessToken.",
    lifespan=lifespan,
)

app.add_middleware(RequestLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Content-Disposition"],
)
register_exception_handlers(app)

app.include_router(api_router, prefix="/api/v1")
# Short alias for Docker/Render health probes (not shown in Swagger).
app.add_api_route("/health", health, include_in_schema=False)

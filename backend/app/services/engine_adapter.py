"""Bridge between the database and the ML engine (PROJECT_CONTEXT section 8.3).

The engine is a pure library: it never touches the DB. This module loads what the engine needs
(reference data, config, business date, model) and hands it over. Reference data and config are
cached in memory; call `invalidate_cache()` whenever reference tables or system_config change.
The cache also expires by itself after CACHE_SECONDS, so changes made by another process
(e.g. `python scripts/seed.py`) are picked up without a restart.
"""
import logging
import threading
import time
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR, get_settings
from app.engine import CounterpartyRef, EngineConfig, ReferenceData, SymbolStats
from app.models import Account, Counterparty, Holiday, Symbol, SystemConfig, Trade

logger = logging.getLogger("app.engine_adapter")

CACHE_SECONDS = 60
_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}
_predictor: Any = None  # Predictor singleton; None = rules-only mode


def invalidate_cache() -> None:
    with _lock:
        _cache.clear()


def _cached(key: str, builder):
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = builder()
    with _lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _f(value) -> float:
    return float(value) if value is not None else 0.0


def _build_refs(db: Session) -> ReferenceData:
    symbols = db.scalars(select(Symbol).where(Symbol.is_active.is_(True))).all()
    parties = db.scalars(select(Counterparty).where(Counterparty.is_active.is_(True))).all()
    return ReferenceData(
        symbols={s.symbol.upper() for s in symbols},
        counterparties={
            c.name.lower(): CounterpartyRef(
                name=c.name, risk_tier=c.risk_tier, tier_label=c.tier_label, fail_count=c.historical_fail_count,
                trade_count=c.historical_trade_count, fail_rate=_f(c.historical_fail_rate))
            for c in parties
        },
        accounts={account_id: is_active for account_id, is_active in db.execute(select(Account.account_id, Account.is_active))},
        symbol_stats={
            s.symbol.upper(): SymbolStats(_f(s.price_mean), _f(s.price_std), _f(s.qty_mean), _f(s.qty_std), _f(s.historical_fail_rate))
            for s in symbols if s.price_mean is not None
        },
        holidays=set(db.scalars(select(Holiday.holiday_date)).all()),
    )


def _config_rows(db: Session) -> dict[str, Any]:
    return _cached("config_rows", lambda: dict(db.execute(select(SystemConfig.key, SystemConfig.value)).all()))


def get_reference_data(db: Session, existing_trade_ids: set[str] | None = None) -> ReferenceData:
    """A fresh copy for this call, so the engine can never change the cached object."""
    base: ReferenceData = _cached("refs", lambda: _build_refs(db))
    return replace(base, existing_trade_ids=set(existing_trade_ids or ()))


# Only these keys go to the engine. Others (business date, e-mail lists, batch size) are backend
# settings, and plain-text values such as "2026-09-28" must not reach the engine's JSON parsing.
ENGINE_CONFIG_KEYS = (
    "hybrid_weights", "classification_thresholds", "rule_weights", "counterparty_tier_thresholds",
    "quantity_limits", "settlement_far_days", "price_zscore_warn",
)


def get_engine_config(db: Session) -> EngineConfig:
    rows = _config_rows(db)
    return EngineConfig.from_system_config({key: rows[key] for key in ENGINE_CONFIG_KEYS if key in rows})


def get_setting(db: Session, key: str, default: Any = None) -> Any:
    """One backend-only system_config value (batch_size, max_upload_rows, e-mail lists ...)."""
    value = _config_rows(db).get(key)
    return default if value is None else value


def get_business_date(db: Session) -> date:
    """system_config.business_date_override if set, else today in APP_TIMEZONE (section 6.4)."""
    override = _config_rows(db).get("business_date_override")
    if override:
        try:
            return date.fromisoformat(str(override))
        except ValueError:
            logger.warning("Ignoring invalid business_date_override: %r", override)
    return datetime.now(ZoneInfo(get_settings().app_timezone)).date()


def existing_active_trade_ids(db: Session, trade_ids: list[str]) -> set[str]:
    """Which of these business IDs already belong to a NON-rejected trade."""
    wanted = [t for t in trade_ids if t]
    if not wanted:
        return set()
    return set(db.scalars(select(Trade.trade_id).where(Trade.trade_id.in_(wanted), Trade.status != "REJECTED")).all())


# ------------------------------------------------------------------ model
def get_predictor():
    return _predictor


def set_predictor(predictor) -> None:
    """Swap the in-memory production model (used by promote: no restart needed)."""
    global _predictor
    _predictor = predictor


def model_dir() -> Path:
    """Folder for model artifacts: MODEL_DIR from the settings, relative to backend/ unless absolute."""
    configured = Path(get_settings().model_dir)
    return configured if configured.is_absolute() else BACKEND_DIR / configured


def resolve_artifact(path_text: str) -> str:
    """The stored path, or the same file name inside MODEL_DIR when the stored path does not
    exist here (e.g. the model was registered on another machine or in another folder)."""
    if path_text and not Path(path_text).is_file():
        candidate = model_dir() / Path(path_text.replace("\\", "/")).name
        if candidate.is_file():
            return str(candidate)
    return path_text


def load_predictor(db: Session) -> None:
    """Called at startup. If no PRODUCTION model exists (or it cannot be loaded) the app runs
    in rules-only mode (ml_score = null, final = rule score)."""
    from app.engine import Predictor
    from app.models import ModelVersion

    set_predictor(None)
    production = db.scalar(select(ModelVersion).where(ModelVersion.status == "PRODUCTION"))
    if production is None:
        logger.warning("No PRODUCTION model registered - running in RULES-ONLY mode")
        return
    try:
        set_predictor(Predictor.load(resolve_artifact(production.artifact_path)))
        logger.info("Loaded production model %s", production.version)
    except Exception:  # noqa: BLE001 - a broken model file must never stop the API
        logger.exception("Could not load model %s - running in RULES-ONLY mode", production.version)
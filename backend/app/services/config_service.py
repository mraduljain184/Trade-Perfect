"""Read values from the system_config table (PROJECT_CONTEXT section 12)."""
from typing import Any

from sqlalchemy.orm import Session

from app.models import SystemConfig

# Used only if a key is missing from the table (seed.py normally creates every key).
DEFAULTS: dict[str, Any] = {
    "hybrid_weights": {"alpha": 0.3, "beta": 0.7},
    "classification_thresholds": {"pass": 0.5, "exception": 0.7},
    "psi_threshold": 0.2,
    "counterparty_tier_thresholds": {"tier4": 1500, "tier3": 1000, "tier2": 500},
}


def get_value(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(SystemConfig, key)
    if row is None:
        return DEFAULTS.get(key, default)
    return row.value


def get_hybrid_config(db: Session) -> dict[str, float]:
    weights = get_value(db, "hybrid_weights") or {}
    thresholds = get_value(db, "classification_thresholds") or {}
    return {
        "alpha": float(weights.get("alpha", 0.3)),
        "beta": float(weights.get("beta", 0.7)),
        "pass_threshold": float(thresholds.get("pass", 0.5)),
        "exception_threshold": float(thresholds.get("exception", 0.7)),
        "psi_threshold": float(get_value(db, "psi_threshold", 0.2)),
    }

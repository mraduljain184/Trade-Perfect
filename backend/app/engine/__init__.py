"""Public API of the Trade Perfect engine. The backend imports ONLY from here."""
from .config import DEFAULT_RULE_WEIGHTS, EngineConfig
from .drift import compute_drift, drift_detected
from .errors import ConfigError, EngineError, InsufficientDataError, ModelLoadError
from .evaluate import evaluate_model
from .model import Predictor
from .pipeline import classify, hybrid_score, priority_for, score_trades, validate_trade
from .reference import compute_counterparty_tiers, compute_symbol_stats, load_historical_csv
from .train import train_model
from .types import (
    CounterpartyRef, FieldError, ReferenceData, RuleHit, ScoredTrade,
    SymbolStats, TrainResult, ValidationResult,
)

__all__ = [
    "DEFAULT_RULE_WEIGHTS", "EngineConfig",
    "EngineError", "InsufficientDataError", "ModelLoadError", "ConfigError",
    "CounterpartyRef", "SymbolStats", "ReferenceData", "RuleHit", "FieldError",
    "ScoredTrade", "ValidationResult", "TrainResult",
    "load_historical_csv", "compute_counterparty_tiers", "compute_symbol_stats",
    "validate_trade", "score_trades", "classify", "hybrid_score", "priority_for",
    "train_model", "Predictor", "evaluate_model", "compute_drift", "drift_detected",
]

"""Engine configuration: defaults from PROJECT_CONTEXT §12 + EngineConfig."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

from .errors import ConfigError

# ---- §12 defaults -------------------------------------------------------
DEFAULT_RULE_WEIGHTS: dict[str, Any] = {
    "ssi": 0.35,
    "ccy": 0.15,
    "sd_past": 0.15,
    "sd_weekend": 0.10,
    "sd_far": 0.05,
    "sd_cap": 0.15,
    "cp_tier": {"1": 0.0, "2": 0.10, "3": 0.20, "4": 0.25},
    "qty_high": 0.10,
    "qty_low": 0.05,
    "price_warn": 0.0,
}
DEFAULT_TIER_THRESHOLDS: dict[str, int] = {"tier4": 1500, "tier3": 1000, "tier2": 500}
TIER_LABELS: dict[int, str] = {
    4: "Tier4_HighestRisk",
    3: "Tier3_ElevatedRisk",
    2: "Tier2_MediumTrust",
    1: "Tier1_HighTrust",
}

# ---- engine-wide constants ------------------------------------------------
SCORE_DECIMALS = 4          # §6.1: round all scores to 4 dp
SHAP_EXACT_MAX_ROWS = 100   # §6.6: batches > 100 rows use approximate SHAP
PSI_MIN_ROWS = 500          # §10
PSI_EPSILON = 1e-4          # §10
RANDOM_STATE = 42           # determinism everywhere


def _as_obj(value: Any) -> Any:
    """system_config values may arrive as JSON strings or already-decoded objects."""
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def merge_rule_weights(override: dict | None) -> dict:
    """Defaults + partial override (a Sys Admin may change only one weight)."""
    merged = copy.deepcopy(DEFAULT_RULE_WEIGHTS)
    for key, value in (override or {}).items():
        if key not in merged:
            raise ConfigError(f"Unknown rule weight key '{key}'")
        if key == "cp_tier":
            for tier, w in dict(value).items():
                if str(tier) not in merged["cp_tier"]:
                    raise ConfigError(f"Unknown counterparty tier '{tier}'")
                merged["cp_tier"][str(tier)] = float(w)
        else:
            merged[key] = float(value)
    return merged


@dataclass
class EngineConfig:
    alpha: float = 0.3
    beta: float = 0.7
    pass_threshold: float = 0.5
    exception_threshold: float = 0.7
    rule_weights: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_RULE_WEIGHTS))
    qty_min: int = 100
    qty_max: int = 5000
    settlement_far_days: int = 15
    price_zscore_warn: float = 3.0
    tier_thresholds: dict = field(default_factory=lambda: dict(DEFAULT_TIER_THRESHOLDS))

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not (0 <= self.alpha <= 1 and 0 <= self.beta <= 1):
            raise ConfigError("alpha and beta must be between 0 and 1")
        if abs(self.alpha + self.beta - 1.0) > 1e-6:
            raise ConfigError(f"alpha + beta must equal 1 (got {self.alpha} + {self.beta})")
        if not (0 < self.pass_threshold < self.exception_threshold <= 1):
            raise ConfigError("Need 0 < pass_threshold < exception_threshold <= 1")
        if not (0 <= self.qty_min < self.qty_max):
            raise ConfigError("Need 0 <= qty_min < qty_max")
        t = self.tier_thresholds
        if not (t["tier4"] > t["tier3"] > t["tier2"] >= 0):
            raise ConfigError("Need tier4 > tier3 > tier2 >= 0")
        for k, v in self.rule_weights.items():
            values = v.values() if isinstance(v, dict) else [v]
            if any(float(x) < 0 or float(x) > 1 for x in values):
                raise ConfigError(f"Rule weight '{k}' must be between 0 and 1")

    @classmethod
    def from_system_config(cls, rows: dict[str, Any]) -> "EngineConfig":
        """Build from system_config rows {key: value}. Keys the engine doesn't
        use (batch_size, business_date_override, emails...) are ignored."""
        rows = {k: _as_obj(v) for k, v in (rows or {}).items() if v is not None}
        kw: dict[str, Any] = {}
        if "hybrid_weights" in rows:
            kw["alpha"] = float(rows["hybrid_weights"]["alpha"])
            kw["beta"] = float(rows["hybrid_weights"]["beta"])
        if "classification_thresholds" in rows:
            kw["pass_threshold"] = float(rows["classification_thresholds"]["pass"])
            kw["exception_threshold"] = float(rows["classification_thresholds"]["exception"])
        if "rule_weights" in rows:
            kw["rule_weights"] = merge_rule_weights(rows["rule_weights"])
        if "counterparty_tier_thresholds" in rows:
            kw["tier_thresholds"] = {
                **DEFAULT_TIER_THRESHOLDS,
                **{k: int(v) for k, v in rows["counterparty_tier_thresholds"].items()},
            }
        if "quantity_limits" in rows:
            kw["qty_min"] = int(rows["quantity_limits"]["min"])
            kw["qty_max"] = int(rows["quantity_limits"]["max"])
        if "settlement_far_days" in rows:
            kw["settlement_far_days"] = int(rows["settlement_far_days"])
        if "price_zscore_warn" in rows:
            kw["price_zscore_warn"] = float(rows["price_zscore_warn"])
        return cls(**kw)
"""Public dataclasses of the engine (PROJECT_CONTEXT §8.3). Field names are a CONTRACT."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Optional

from .config import EngineConfig  # noqa: F401  re-exported so types.EngineConfig exists

TradeStatus = Literal["PASS", "MONITOR", "EXCEPTION", "REJECTED"]
Priority = Literal["HIGH", "MEDIUM", "LOW"]


def _jsonable(obj: Any) -> Any:
    """dates -> ISO strings, numpy scalars -> python scalars, recursively."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "item"):  # numpy.float64 / numpy.int64 / numpy.bool_
        return obj.item()
    return obj


@dataclass(frozen=True)
class CounterpartyRef:
    name: str; risk_tier: int; tier_label: str; fail_count: int; trade_count: int; fail_rate: float


@dataclass(frozen=True)
class SymbolStats:
    price_mean: float; price_std: float; qty_mean: float; qty_std: float; fail_rate: float


@dataclass
class ReferenceData:                                  # built by BACKEND from DB tables
    symbols: set[str]                                 # upper-case symbols
    counterparties: dict[str, CounterpartyRef]        # key = name.lower()
    accounts: dict[str, bool]                         # account_id -> is_active
    symbol_stats: dict[str, SymbolStats]
    holidays: set[date] = field(default_factory=set)
    existing_trade_ids: set[str] = field(default_factory=set)  # NON-rejected IDs already in DB


@dataclass
class RuleHit:
    rule_id: str; category: str; triggered: bool; contribution: float; message: str


@dataclass
class FieldError:
    field: str; rule_id: str; message: str


@dataclass
class ScoredTrade:                                    # one per input record, SAME ORDER as input
    row_index: int
    raw: dict
    trade_id: Optional[str]; trade_type: Optional[str]; symbol: Optional[str]
    quantity: Optional[int]; price: Optional[float]; counterparty: Optional[str]
    trade_date: Optional[date]; settlement_date: Optional[date]; account_id: Optional[str]
    trader_name: Optional[str]; ssi_matched: Optional[bool]; currency_matched: Optional[bool]
    status: TradeStatus
    priority: Optional[Priority]
    rule_score: Optional[float]; ml_score: Optional[float]; final_score: float
    risk_factors: list[dict]
    likely_reason: Optional[str]; suggested_action: Optional[str]; explanation: Optional[str]
    rejection_reasons: list[FieldError]
    rule_hits: list[RuleHit]
    features: Optional[dict]
    shap_values: Optional[dict]
    model_version: Optional[str]
    warnings: list[str]
    suggested_fixes: list[dict]

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))


@dataclass
class ValidationResult:
    valid: bool; errors: list[FieldError]; warnings: list[str]

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))


@dataclass
class TrainResult:
    version: str; artifact_path: str; algorithm: str; hyperparameters: dict
    feature_list: list[str]; decision_threshold: float
    metrics: dict              # auc_roc, precision, recall, f1, accuracy, false_positive_rate
    global_shap: list[dict]    # [{"feature","mean_abs_shap"}] sorted desc
    baseline_stats: dict       # for PSI
    monthly_evaluations: list[dict]
    train_rows: int; test_rows: int
    counterparty_tiers: dict
    diagnostics: dict = field(default_factory=dict)   # additive: CV estimate, confusion matrix, curve...

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))
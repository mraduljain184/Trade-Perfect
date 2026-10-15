"""Feature engineering (PROJECT_CONTEXT §6.5). The SAME code builds features for
training, evaluation and live scoring - the #1 rule against train/serve skew.

Two parts:
  FeatureEncoders : statistics LEARNED from the training split only (fail rates,
                    per-symbol price/quantity mean+std). Stored inside the model artifact.
  build_features  : turns normalized trades (+ rule scores) into the 17 model columns.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .calendar import holiday_mask, month_end_mask, to_datetime64, weekend_mask
from .normalize import LABEL_COLUMN
from .types import ReferenceData

# Exact names and order (used by the model, SHAP charts and PSI drift table)
FEATURE_LIST: list[str] = [
    "SSI_Matched_Flag",
    "Currency_Matched_Flag",
    "Counterparty_Tier",
    "Counterparty_Fail_Rate",
    "Symbol_Fail_Rate",
    "CP_Symbol_Fail_Rate",
    "Quantity",
    "Quantity_ZScore",
    "Price",
    "Price_ZScore",
    "Days_To_Settlement",
    "Is_Weekend_Settlement",
    "Is_Holiday_Settlement",
    "Settlement_DayOfWeek",
    "Is_Month_End",
    "Trade_Type_Buy",
    "Rule_Engine_Output",
]
# how each feature is binned for PSI (drift.py)
CATEGORICAL_FEATURES = {
    "SSI_Matched_Flag", "Currency_Matched_Flag", "Counterparty_Tier", "Is_Weekend_Settlement",
    "Is_Holiday_Settlement", "Settlement_DayOfWeek", "Is_Month_End", "Trade_Type_Buy",
}
DEFAULT_SMOOTHING = 100      # (fails + k*global_mean) / (count + k); k=20 and k=100 tested, same AUC


@dataclass
class FeatureEncoders:
    """Everything learned from the TRAINING split. Plain dicts -> safe to joblib/JSON."""
    global_fail_rate: float
    smoothing: int
    cp_fail_rate: dict[str, float] = field(default_factory=dict)
    sym_fail_rate: dict[str, float] = field(default_factory=dict)
    cp_sym_fail_rate: dict[str, float] = field(default_factory=dict)      # key "counterparty|SYMBOL"
    sym_price_mean: dict[str, float] = field(default_factory=dict)
    sym_price_std: dict[str, float] = field(default_factory=dict)
    sym_qty_mean: dict[str, float] = field(default_factory=dict)
    sym_qty_std: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FeatureEncoders":
        return cls(**d)


def _smoothed_rate(keys: pd.Series, y: pd.Series, k: int, prior: float) -> dict[str, float]:
    g = pd.DataFrame({"key": keys.to_numpy(), "y": y.to_numpy()}).groupby("key")["y"].agg(["sum", "count"])
    rate = (g["sum"] + k * prior) / (g["count"] + k)
    return {str(key): round(float(v), 6) for key, v in rate.items()}


def _pair_key(cp: pd.Series, sym: pd.Series) -> pd.Series:
    return cp.astype(str).str.lower() + "|" + sym.astype(str)


def fit_encoders(train: pd.DataFrame, smoothing: int = DEFAULT_SMOOTHING) -> FeatureEncoders:
    """Learn fail-rate encodings and symbol stats from TRAINING rows only (no leakage)."""
    y = train[LABEL_COLUMN].astype(float)
    prior = float(y.mean())
    g = train.groupby("symbol")

    def safe(d: pd.Series) -> dict[str, float]:
        return {str(k): (float(v) if pd.notna(v) and v > 0 else 1.0) for k, v in d.items()}

    return FeatureEncoders(
        global_fail_rate=round(prior, 6),
        smoothing=smoothing,
        cp_fail_rate=_smoothed_rate(train["counterparty"].astype(str).str.lower(), y, smoothing, prior),
        sym_fail_rate=_smoothed_rate(train["symbol"], y, smoothing, prior),
        cp_sym_fail_rate=_smoothed_rate(_pair_key(train["counterparty"], train["symbol"]), y, smoothing, prior),
        sym_price_mean={str(k): float(v) for k, v in g["price"].mean().items()},
        sym_price_std=safe(g["price"].std(ddof=1)),
        sym_qty_mean={str(k): float(v) for k, v in g["quantity"].mean().items()},
        sym_qty_std=safe(g["quantity"].std(ddof=1)),
    )


def build_features(df: pd.DataFrame, rule_score_ml: np.ndarray, enc: FeatureEncoders,
                   refs: ReferenceData) -> pd.DataFrame:
    """df: normalized ACCEPTED trades (canonical counterparty). rule_score_ml: RuleResult.rule_score_ml
    (rule score without S-SD-PAST, decision B). Returns float columns in FEATURE_LIST order.
    Unknown counterparty/symbol -> global fail rate / zero z-score."""
    df = df.reset_index(drop=True)
    prior = enc.global_fail_rate
    settle = to_datetime64(df["settlement_date"])
    trade = to_datetime64(df["trade_date"])
    cp_lower = df["counterparty"].astype(str).str.lower()
    sym = df["symbol"].astype(str)
    qty = df["quantity"].astype("float64")
    price = df["price"].astype("float64")
    tiers = {k: v.risk_tier for k, v in refs.counterparties.items()}

    def z(values: pd.Series, mean: dict, std: dict) -> pd.Series:
        return ((values - sym.map(mean)) / sym.map(std)).fillna(0.0)

    feats = pd.DataFrame({
        "SSI_Matched_Flag": df["ssi_matched"].astype(bool).astype(float),
        "Currency_Matched_Flag": df["currency_matched"].astype(bool).astype(float),
        "Counterparty_Tier": cp_lower.map(tiers).fillna(1).astype(float),
        "Counterparty_Fail_Rate": cp_lower.map(enc.cp_fail_rate).fillna(prior),
        "Symbol_Fail_Rate": sym.map(enc.sym_fail_rate).fillna(prior),
        "CP_Symbol_Fail_Rate": _pair_key(df["counterparty"], sym).map(enc.cp_sym_fail_rate).fillna(prior),
        "Quantity": qty,
        "Quantity_ZScore": z(qty, enc.sym_qty_mean, enc.sym_qty_std),
        "Price": price,
        "Price_ZScore": z(price, enc.sym_price_mean, enc.sym_price_std),
        "Days_To_Settlement": (settle - trade).dt.days.astype(float),
        "Is_Weekend_Settlement": weekend_mask(settle).astype(float),
        "Is_Holiday_Settlement": holiday_mask(settle, refs.holidays).astype(float),
        "Settlement_DayOfWeek": settle.dt.dayofweek.astype(float),
        "Is_Month_End": month_end_mask(settle).astype(float),
        "Trade_Type_Buy": (df["trade_type"].astype(str) == "BUY").astype(float),
        "Rule_Engine_Output": np.asarray(rule_score_ml, dtype=float),
    })
    return feats[FEATURE_LIST].astype("float64")

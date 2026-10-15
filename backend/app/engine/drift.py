"""Data drift (PSI) - PROJECT_CONTEXT §10.

ML-2 builds the BASELINE (training distribution, saved in the report).
ML-4 adds compute_drift() which compares live features against it.

Baseline format per feature:
  numeric     : {"type": "numeric", "edges": [9 inner quantile cut points], "proportions": [10 floats]}
  categorical : {"type": "categorical", "proportions": {"0.0": 0.62, "1.0": 0.38, ...}}
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .features import CATEGORICAL_FEATURES

N_BINS = 10


def _numeric_bins(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Bin index 0..len(edges) for each value (value == edge goes to the upper bin)."""
    return np.searchsorted(edges, values, side="right")


def _cat_key(v) -> str:
    return str(float(v))


def build_baseline_stats(train_features: pd.DataFrame, n_bins: int = N_BINS) -> dict:
    stats: dict[str, dict] = {}
    for col in train_features.columns:
        v = train_features[col].to_numpy(dtype=float)
        if col in CATEGORICAL_FEATURES:
            s = pd.Series(v).map(_cat_key).value_counts(normalize=True).sort_index()
            stats[col] = {"type": "categorical",
                          "proportions": {k: round(float(p), 6) for k, p in s.items()}}
        else:
            q = np.quantile(v, np.linspace(0, 1, n_bins + 1)[1:-1])
            edges = np.unique(q)                               # repeated values -> fewer bins
            counts = np.bincount(_numeric_bins(v, edges), minlength=len(edges) + 1)
            stats[col] = {"type": "numeric", "edges": [round(float(e), 6) for e in edges],
                          "proportions": [round(float(c) / len(v), 6) for c in counts]}
    return stats

# ---- ML-4: compare live features against the baseline ---------------------------------------
GREEN_BELOW = 0.1           # PSI < 0.1 -> GREEN, 0.1..threshold -> AMBER, > threshold -> RED


def _psi(base: np.ndarray, cur: np.ndarray, eps: float) -> float:
    base = np.maximum(np.asarray(base, dtype=float), eps)
    cur = np.maximum(np.asarray(cur, dtype=float), eps)
    return float(np.sum((cur - base) * np.log(cur / base)))


def severity_for(psi: float, threshold: float = 0.2) -> str:
    if psi > threshold:
        return "RED"
    if psi >= min(GREEN_BELOW, threshold):
        return "AMBER"
    return "GREEN"


def compute_drift(baseline_stats: dict, current_features, threshold: float = 0.2) -> list[dict]:
    """PSI per feature (PROJECT_CONTEXT §10): PSI = sum((cur% - base%) * ln(cur% / base%)), eps = 1e-4.
    current_features: DataFrame (or list of feature dicts, e.g. from ml_predictions.features).
    Returns [{"feature", "psi", "severity"}] sorted by PSI desc. Raises InsufficientDataError if < 500 rows."""
    from .config import PSI_EPSILON, PSI_MIN_ROWS
    from .errors import InsufficientDataError

    cur_df = current_features if isinstance(current_features, pd.DataFrame) else pd.DataFrame(list(current_features))
    if len(cur_df) < PSI_MIN_ROWS:
        raise InsufficientDataError(f"Need at least {PSI_MIN_ROWS} scored trades for a drift check, got {len(cur_df)}")

    results = []
    for feature, base in baseline_stats.items():
        if feature not in cur_df.columns:
            continue
        v = pd.to_numeric(cur_df[feature], errors="coerce").dropna().to_numpy(dtype=float)
        if len(v) == 0:
            continue
        if base["type"] == "categorical":
            cur_props = pd.Series(v).map(_cat_key).value_counts(normalize=True)
            cats = sorted(set(base["proportions"]) | set(cur_props.index))
            b = [base["proportions"].get(c, 0.0) for c in cats]
            c = [float(cur_props.get(c, 0.0)) for c in cats]
        else:
            edges = np.asarray(base["edges"], dtype=float)
            counts = np.bincount(_numeric_bins(v, edges), minlength=len(edges) + 1)
            b = base["proportions"]
            c = counts / len(v)
        psi = round(_psi(b, c, PSI_EPSILON), 4)
        results.append({"feature": feature, "psi": psi, "severity": severity_for(psi, threshold)})
    return sorted(results, key=lambda r: -r["psi"])


def drift_detected(results: list[dict], threshold: float = 0.2) -> bool:
    return any(r["psi"] > threshold for r in results)

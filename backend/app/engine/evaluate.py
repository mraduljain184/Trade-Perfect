"""Metrics and threshold selection (PROJECT_CONTEXT §6.5 targets, FR-MM-001..005).

Targets: AUC-ROC >= 0.90, Recall >= 0.85, Precision >= 0.70, F1 >= 0.77, Accuracy > 0.90, FPR < 0.05.
The decision threshold only affects these METRICS; the hybrid score always uses the raw probability.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, roc_auc_score

METRIC_TARGETS: dict[str, tuple[str, float]] = {      # metric -> (comparison, target)
    "auc_roc": (">=", 0.90),
    "recall": (">=", 0.85),
    "precision": (">=", 0.70),
    "f1": (">=", 0.77),
    "accuracy": (">", 0.90),
    "false_positive_rate": ("<", 0.05),
}
THRESHOLD_GRID = np.round(np.arange(0.30, 0.90 + 1e-9, 0.025), 3)      # MODULE_ML step 5


def compute_metrics(y_true, proba, threshold: float) -> dict:
    """The six dashboard metrics + confusion-matrix counts, rounded to 4 dp, plain python types."""
    y_true = np.asarray(y_true).astype(int)
    proba = np.asarray(proba, dtype=float)
    pred = (proba >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    auc = roc_auc_score(y_true, proba) if len(set(y_true.tolist())) == 2 else float("nan")
    r = lambda v: round(float(v), 4)                                           # noqa: E731
    return {
        "auc_roc": r(auc), "precision": r(precision), "recall": r(recall), "f1": r(f1),
        "accuracy": r((tp + tn) / len(y_true)), "false_positive_rate": r(fp / (fp + tn) if fp + tn else 0.0),
        "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
    }


def passes_targets(metrics: dict) -> dict[str, bool]:
    out = {}
    for name, (op, target) in METRIC_TARGETS.items():
        v = metrics[name]
        out[name] = bool(v >= target if op == ">=" else v > target if op == ">" else v < target)
    return out


def threshold_curve(y_true, proba, grid=THRESHOLD_GRID) -> list[dict]:
    """Metrics at every threshold of the grid (for the report's trade-off table/curve)."""
    return [{"threshold": float(t), **compute_metrics(y_true, proba, t)} for t in grid]


def select_threshold(y_true, proba, grid=THRESHOLD_GRID, max_fpr: float = 0.05,
                     min_recall: float = 0.85) -> tuple[float, bool]:
    """MODULE_ML step 5: lowest threshold with FPR < max_fpr AND recall >= min_recall.
    If none qualifies: the one with the lowest FPR among recall >= min_recall (returns ok=False)."""
    curve = threshold_curve(y_true, proba, grid)
    ok = [c for c in curve if c["false_positive_rate"] < max_fpr and c["recall"] >= min_recall]
    if ok:
        return ok[0]["threshold"], True
    cand = [c for c in curve if c["recall"] >= min_recall] or curve
    return min(cand, key=lambda c: (c["false_positive_rate"], c["threshold"]))["threshold"], False


def monthly_evaluations(y_true, proba, trade_dates: pd.Series, threshold: float,
                        min_rows: int = 50) -> list[dict]:
    """One metrics row per trade month (FR-MM-003 trend chart): dataset_label 'month_YYYY-MM'."""
    months = pd.to_datetime(pd.Series(trade_dates).reset_index(drop=True)).dt.strftime("%Y-%m")
    y_true = np.asarray(y_true)
    proba = np.asarray(proba)
    rows = []
    for m in sorted(months.unique()):
        idx = np.where(months == m)[0]
        if len(idx) < min_rows:
            continue
        rows.append({"dataset_label": f"month_{m}", "sample_size": int(len(idx)),
                     **compute_metrics(y_true[idx], proba[idx], threshold)})
    return rows

def evaluate_model(predictor, labelled: pd.DataFrame, refs, cfg) -> dict:
    """Score any LABELLED DataFrame (canonical columns incl. settlement_failed, e.g. from
    load_historical_csv) with a Predictor and return the 6 metrics at the model's threshold
    + pass/fail per target + sample size. Used for re-evaluation / new labelled uploads (ML-4)."""
    from .normalize import LABEL_COLUMN
    from .rules import evaluate_rules

    if LABEL_COLUMN not in labelled.columns:
        raise ValueError("labelled data needs a 'settlement_failed' column")
    df = labelled.reset_index(drop=True)
    rules = evaluate_rules(df, refs, cfg, None)                      # training-style rule score
    X = predictor.build_features(df, rules.rule_score_ml, refs)
    proba = predictor.predict(X)
    y = df[LABEL_COLUMN].astype(int).to_numpy()
    m = compute_metrics(y, proba, predictor.decision_threshold)
    return {**m, "passed": passes_targets(m), "sample_size": int(len(df)),
            "decision_threshold": predictor.decision_threshold, "model_version": predictor.version}

"""Model training (MODULE_ML ML-2). Callable repeatedly for v1, v2, ... (retrain endpoint).

Recipe:
  1. counterparty tiers recomputed from THIS history (§2.3, decision A: all-history tiers)
  2. Rule_Engine_Output = rule score with business date = trade date (decision B)
  3. stratified 80/20 split, random_state=42
  4. encoders (fail rates k=100, symbol stats) fitted on the TRAIN split only
  5. RandomForest (spec params)
  6. decision threshold = lowest on the 0.30..0.90 grid with FPR < 5% and recall >= 85% on the holdout
     (+ 5-fold cross-validated estimate on the train split, reported as a robustness check)
  7. holdout metrics, monthly evaluations, global SHAP (exact, 2,000 rows), PSI baseline
  8. ml_artifacts/model_<version>.joblib  +  model_<version>_report.json  (= TrainResult)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, train_test_split

from .config import RANDOM_STATE, EngineConfig
from .drift import build_baseline_stats
from .errors import EngineError
from .evaluate import (compute_metrics, monthly_evaluations, passes_targets, select_threshold,
                       threshold_curve)
from .features import DEFAULT_SMOOTHING, FEATURE_LIST, build_features, fit_encoders
from .normalize import LABEL_COLUMN
from .reference import build_counterparty_refs, compute_counterparty_tiers
from .rules import evaluate_rules
from .shap_utils import make_explainer, tree_shap_class1
from .types import ReferenceData, TrainResult

log = logging.getLogger(__name__)

ALGORITHM = "RandomForestClassifier"
ENGINE_SCHEMA_VERSION = 1             # bump when features.py changes -> old artifacts refuse to load
DEFAULT_PARAMS: dict = {
    "n_estimators": 200, "max_depth": 12, "min_samples_leaf": 5,
    "class_weight": "balanced", "random_state": RANDOM_STATE, "n_jobs": -1,
}
TEST_SIZE = 0.2
SHAP_SAMPLE = 2000
CV_FOLDS = 5


def _refs_with_tiers(refs: ReferenceData, tiers: dict) -> ReferenceData:
    """Same reference data, counterparty tiers recomputed from the training history."""
    names = [c.name for c in refs.counterparties.values()] or list(tiers)
    return ReferenceData(symbols=refs.symbols, counterparties=build_counterparty_refs(names, tiers),
                         accounts=refs.accounts, symbol_stats=refs.symbol_stats,
                         holidays=refs.holidays, existing_trade_ids=set())


def _cv_threshold_estimate(train_df, y_train, rule_train, refs, params, smoothing, folds) -> dict:
    """5-fold out-of-fold probabilities on the TRAIN split (encoders re-fitted per fold)."""
    oof = np.zeros(len(train_df))
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=RANDOM_STATE)
    for a, b in skf.split(train_df, y_train):
        enc = fit_encoders(train_df.iloc[a], smoothing)
        Xa = build_features(train_df.iloc[a], rule_train[a], enc, refs)
        Xb = build_features(train_df.iloc[b], rule_train[b], enc, refs)
        oof[b] = RandomForestClassifier(**params).fit(Xa, y_train[a]).predict_proba(Xb)[:, 1]
    t, ok = select_threshold(y_train, oof)
    return {"threshold": t, "qualified": ok, "oof_metrics": compute_metrics(y_train, oof, t)}


def train_model(historical: pd.DataFrame, refs: ReferenceData, cfg: EngineConfig, version: str,
                out_dir: str, params: Optional[dict] = None, *, smoothing: int = DEFAULT_SMOOTHING,
                cv_folds: int = CV_FOLDS, shap_sample: int = SHAP_SAMPLE,
                overwrite: bool = False) -> TrainResult:
    t0 = time.time()
    if not overwrite and os.path.exists(os.path.join(out_dir, f"model_{version}.joblib")):
        raise EngineError(f"model_{version}.joblib already exists in {out_dir} - choose a new version "
                          f"(e.g. v2) or pass overwrite=True")
    if LABEL_COLUMN not in historical.columns:
        raise EngineError("historical data has no 'settlement_failed' label column")
    df = historical.reset_index(drop=True)
    y = df[LABEL_COLUMN].astype(int).to_numpy()
    if len(df) < 1000 or y.min() == y.max():
        raise EngineError("need >= 1,000 labelled rows with both outcomes to train")
    hp = {**DEFAULT_PARAMS, **(params or {})}

    # 1-2. tiers from this history, rule score as in training (no "past" rule)
    tiers = compute_counterparty_tiers(df, cfg.tier_thresholds)
    refs_t = _refs_with_tiers(refs, tiers)
    rule_ml = evaluate_rules(df, refs_t, cfg, None).rule_score_ml

    # 3. split
    idx_tr, idx_te = train_test_split(np.arange(len(df)), test_size=TEST_SIZE, stratify=y,
                                      random_state=RANDOM_STATE)
    tr, te = df.iloc[idx_tr].reset_index(drop=True), df.iloc[idx_te].reset_index(drop=True)
    y_tr, y_te = y[idx_tr], y[idx_te]

    # 4. encoders on TRAIN only -> features
    enc = fit_encoders(tr, smoothing)
    X_tr = build_features(tr, rule_ml[idx_tr], enc, refs_t)
    X_te = build_features(te, rule_ml[idx_te], enc, refs_t)

    # 5. model
    model = RandomForestClassifier(**hp).fit(X_tr, y_tr)
    p_te = model.predict_proba(X_te)[:, 1]
    log.info("trained %s in %.1fs", ALGORITHM, time.time() - t0)

    # 6. threshold (spec) + cross-validated robustness estimate
    threshold, qualified = select_threshold(y_te, p_te)
    metrics = compute_metrics(y_te, p_te, threshold)
    cv = None
    if cv_folds and cv_folds > 1:
        cv = _cv_threshold_estimate(tr, y_tr, rule_ml[idx_tr], refs_t, hp, smoothing, cv_folds)
        cv["holdout_metrics_at_cv_threshold"] = compute_metrics(y_te, p_te, cv["threshold"])

    # 7. monthly trend, global SHAP, PSI baseline
    monthly = monthly_evaluations(y_te, p_te, te["trade_date"], threshold)
    n_shap = min(shap_sample, len(X_te))
    shap_rows = X_te.sample(n=n_shap, random_state=RANDOM_STATE)
    sv = tree_shap_class1(make_explainer(model), shap_rows, approximate=False)
    mean_abs = np.abs(sv).mean(axis=0)
    global_shap = sorted(({"feature": f, "mean_abs_shap": round(float(v), 6)}
                          for f, v in zip(FEATURE_LIST, mean_abs)), key=lambda d: -d["mean_abs_shap"])
    baseline = build_baseline_stats(X_tr)

    # 8. save artifact + report
    os.makedirs(out_dir, exist_ok=True)
    artifact_path = os.path.join(out_dir, f"model_{version}.joblib")
    trained_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    joblib.dump({
        "model": model, "feature_list": FEATURE_LIST, "encoders": enc.to_dict(),
        "decision_threshold": threshold, "version": version, "trained_at": trained_at,
        "algorithm": ALGORITHM, "engine_schema_version": ENGINE_SCHEMA_VERSION,
        "counterparty_tiers": tiers,
    }, artifact_path, compress=3)

    result = TrainResult(
        version=version, artifact_path=artifact_path, algorithm=ALGORITHM,
        hyperparameters={**hp, "smoothing": smoothing, "test_size": TEST_SIZE,
                         "threshold_rule": "lowest on 0.30..0.90 step 0.025 with FPR<0.05 and recall>=0.85"},
        feature_list=list(FEATURE_LIST), decision_threshold=float(threshold),
        metrics={k: metrics[k] for k in ("auc_roc", "precision", "recall", "f1", "accuracy", "false_positive_rate")},
        global_shap=global_shap, baseline_stats=baseline, monthly_evaluations=monthly,
        train_rows=int(len(tr)), test_rows=int(len(te)), counterparty_tiers=tiers,
        diagnostics={
            "trained_at": trained_at,
            "training_seconds": round(time.time() - t0, 1),
            "threshold_qualified": qualified,
            "targets_passed": passes_targets(metrics),
            "confusion_matrix": {k: metrics[k] for k in ("tp", "fp", "tn", "fn")},
            "metrics_at_0_5": compute_metrics(y_te, p_te, 0.5),
            "cv_estimate": cv,
            "threshold_curve": threshold_curve(y_te, p_te),
            "feature_importance_gini": {f: round(float(v), 6) for f, v in
                                        zip(FEATURE_LIST, model.feature_importances_)},
            "artifact_size_mb": round(os.path.getsize(artifact_path) / 1e6, 2),
        },
    )
    report_path = os.path.join(out_dir, f"model_{version}_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2)
    log.info("saved %s and %s (%.1fs total)", artifact_path, report_path, time.time() - t0)
    return result
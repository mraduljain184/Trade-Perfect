"""Training pipeline on a small sample (fast) - checks plumbing, artifact and report shape."""
import json

import joblib
import pytest

from app.engine import EngineConfig
from app.engine.evaluate import compute_metrics, select_threshold
from app.engine.features import FEATURE_LIST, build_features, fit_encoders
from app.engine.train import train_model


def test_compute_metrics_simple():
    m = compute_metrics([1, 1, 0, 0], [0.9, 0.4, 0.6, 0.1], 0.5)
    assert (m["tp"], m["fn"], m["fp"], m["tn"]) == (1, 1, 1, 1)
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["false_positive_rate"] == 0.5


def test_select_threshold_picks_lowest_qualifying():
    y = [1] * 90 + [0] * 30
    p = [0.95] * 90 + [0.4] * 29 + [0.97]      # one negative scored very high -> FPR 1/30 = 3.3%
    t, ok = select_threshold(y, p)
    assert ok and t == 0.425                    # first grid value above the 0.4 negatives


def test_encoders_use_train_rows_only(history):
    train = history.iloc[:1000]
    enc = fit_encoders(train, smoothing=100)
    assert enc.global_fail_rate == pytest.approx(train.settlement_failed.mean(), abs=1e-6)


def test_build_features_columns_and_order(history, real_refs):
    sample = history.iloc[:200]
    X = build_features(sample, [0.5] * 200, fit_encoders(sample), real_refs)
    assert list(X.columns) == FEATURE_LIST and X.shape == (200, 17)
    assert X.isna().sum().sum() == 0


def test_train_model_small_sample(history, real_refs, tmp_path):
    sample = history.sample(n=4000, random_state=1)
    r = train_model(sample, real_refs, EngineConfig(), "vtest", str(tmp_path),
                    params={"n_estimators": 20}, cv_folds=0, shap_sample=100)
    art = joblib.load(r.artifact_path)
    assert art["feature_list"] == FEATURE_LIST and art["version"] == "vtest"
    assert 0.3 <= r.decision_threshold <= 0.9
    assert set(r.metrics) == {"auc_roc", "precision", "recall", "f1", "accuracy", "false_positive_rate"}
    assert len(r.global_shap) == 17 and set(r.baseline_stats) == set(FEATURE_LIST)
    assert all(m["dataset_label"].startswith("month_2026-") for m in r.monthly_evaluations)
    report = json.loads((tmp_path / "model_vtest_report.json").read_text())
    assert report["version"] == "vtest" and report["metrics"] == r.metrics
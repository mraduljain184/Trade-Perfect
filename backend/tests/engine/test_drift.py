"""ML-4: PSI drift, evaluate_model and safe retraining."""
import json
import tempfile

import numpy as np
import pandas as pd
import pytest

from app.engine import (EngineConfig, EngineError, InsufficientDataError, Predictor, compute_drift,
                        drift_detected, evaluate_model, train_model)
from app.engine.drift import build_baseline_stats, severity_for


@pytest.fixture(scope="module")
def frame():
    rng = np.random.default_rng(42)
    return pd.DataFrame({"Quantity": rng.integers(100, 5000, 2000).astype(float),
                         "SSI_Matched_Flag": rng.integers(0, 2, 2000).astype(float)})


def test_psi_of_distribution_with_itself_is_zero(frame):
    res = compute_drift(build_baseline_stats(frame), frame)
    assert all(r["psi"] == 0 for r in res) and all(r["severity"] == "GREEN" for r in res)


def test_shifted_distribution_is_red(frame):
    shifted = frame.assign(Quantity=frame.Quantity * 3, SSI_Matched_Flag=1.0)
    res = {r["feature"]: r for r in compute_drift(build_baseline_stats(frame), shifted)}
    assert res["Quantity"]["severity"] == "RED" and res["SSI_Matched_Flag"]["severity"] == "RED"
    assert drift_detected(list(res.values()))


def test_accepts_list_of_feature_dicts(frame):
    rows = frame.to_dict(orient="records")                  # like ml_predictions.features JSON
    assert compute_drift(build_baseline_stats(frame), rows)[0]["psi"] == 0


def test_insufficient_data(frame):
    with pytest.raises(InsufficientDataError):
        compute_drift(build_baseline_stats(frame), frame.head(499))


@pytest.mark.parametrize("psi, sev", [(0.0, "GREEN"), (0.0999, "GREEN"), (0.1, "AMBER"), (0.2, "AMBER"), (0.2001, "RED")])
def test_severity_bands(psi, sev):
    assert severity_for(psi, 0.2) == sev


def test_threshold_comes_from_argument(frame):
    shifted = frame.assign(Quantity=frame.Quantity * 1.2)
    psi = compute_drift(build_baseline_stats(frame), shifted, threshold=10)[0]
    assert psi["severity"] != "RED"


def test_retrain_new_version_keeps_old_and_refuses_overwrite(history, real_refs):
    out = tempfile.mkdtemp()
    sample = history.sample(n=3000, random_state=5)
    kw = dict(params={"n_estimators": 10}, cv_folds=0, shap_sample=50)
    r1 = train_model(sample, real_refs, EngineConfig(), "v1", out, **kw)
    before = open(r1.artifact_path, "rb").read()
    r2 = train_model(sample, real_refs, EngineConfig(), "v2", out, **kw)
    assert r2.artifact_path != r1.artifact_path and open(r1.artifact_path, "rb").read() == before
    with pytest.raises(EngineError):
        train_model(sample, real_refs, EngineConfig(), "v1", out, **kw)
    m = evaluate_model(Predictor.load(r2.artifact_path), history.sample(n=1000, random_state=9),
                       real_refs, EngineConfig())
    assert m["sample_size"] == 1000 and m["model_version"] == "v2" and set(m["passed"]) >= {"auc_roc", "recall"}


def test_production_baseline_vs_20k_matches_prototype(real_refs, records_20k):
    from pathlib import Path
    from datetime import date
    art = Path(__file__).resolve().parents[2] / "ml_artifacts" / "model_v1.joblib"
    if not art.exists():
        pytest.skip("model_v1 not trained")
    from app.engine.hard_gates import run_hard_gates
    from app.engine.normalize import normalize_records
    from app.engine.pipeline import _accepted_frame
    from app.engine.rules import evaluate_rules
    p = Predictor.load(str(art))
    base = json.loads(art.with_name("model_v1_report.json").read_text())["baseline_stats"]
    b = normalize_records(records_20k)
    g = run_hard_gates(b, real_refs)
    df = _accepted_frame(b, g, [i for i, e in enumerate(g.errors) if not e])
    X = p.build_features(df, evaluate_rules(df, real_refs, EngineConfig(), date(2026, 9, 28)).rule_score_ml, real_refs)
    res = {r["feature"]: r for r in compute_drift(base, X)}
    assert res["SSI_Matched_Flag"]["severity"] == "GREEN" and res["Currency_Matched_Flag"]["severity"] == "GREEN"
    assert res["Quantity"]["severity"] == "AMBER"
    assert res["Counterparty_Tier"]["severity"] == "RED" and res["Is_Weekend_Settlement"]["severity"] == "RED"

"""The production model must meet the §6.5 targets on the holdout set.
Reads ml_artifacts/model_v1_report.json (run `python -m app.engine.cli train` first); skipped if missing."""
import json
from pathlib import Path

import pytest

REPORT = Path(__file__).resolve().parents[2] / "ml_artifacts" / "model_v1_report.json"


@pytest.fixture(scope="module")
def metrics():
    if not REPORT.exists():
        pytest.skip("model_v1_report.json not found - run the train command first")
    return json.loads(REPORT.read_text())["metrics"]


def test_auc(metrics):
    assert metrics["auc_roc"] >= 0.90


def test_recall(metrics):
    assert metrics["recall"] >= 0.85


def test_precision(metrics):
    assert metrics["precision"] >= 0.70


def test_f1(metrics):
    assert metrics["f1"] >= 0.77


def test_accuracy(metrics):
    assert metrics["accuracy"] > 0.90


def test_false_positive_rate(metrics):
    assert metrics["false_positive_rate"] < 0.05
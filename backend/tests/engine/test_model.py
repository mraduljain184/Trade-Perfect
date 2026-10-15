"""ML-3: Predictor loading, prediction, SHAP and the hybrid pipeline (uses a small model trained once)."""
import json
import tempfile
from datetime import date

import joblib
import pytest

from app.engine import EngineConfig, ModelLoadError, Predictor, score_trades
from app.engine.features import FEATURE_LIST
from app.engine.train import train_model

from .helpers import make_trade

BDATE = date(2026, 9, 28)


@pytest.fixture(scope="module")
def predictor(history, real_refs):
    out = tempfile.mkdtemp()
    sample = history.sample(n=6000, random_state=7)
    r = train_model(sample, real_refs, EngineConfig(), "vt", out,
                    params={"n_estimators": 30}, cv_folds=0, shap_sample=100)
    return Predictor.load(r.artifact_path)


def _real_trade(**override):
    """A trade that is valid against the REAL reference data."""
    return make_trade(**{"counterparty": "UBS", "symbol": "AAPL", "account_id": "ACC1000", **override})


def test_load_missing_file_raises():
    with pytest.raises(ModelLoadError):
        Predictor.load("does/not/exist.joblib")


def test_load_corrupt_file_raises():
    path = tempfile.mktemp(suffix=".joblib")
    with open(path, "w") as fh:
        fh.write("not a model")
    with pytest.raises(ModelLoadError):
        Predictor.load(path)


def test_load_wrong_schema_raises(predictor):
    path = tempfile.mktemp(suffix=".joblib")
    art = joblib.load(predictor.path)
    art["engine_schema_version"] = 999
    joblib.dump(art, path)
    with pytest.raises(ModelLoadError):
        Predictor.load(path)


def test_predict_and_explain_shapes(predictor, history, real_refs):
    from app.engine.rules import evaluate_rules
    df = history.iloc[:50]
    X = predictor.build_features(df, evaluate_rules(df, real_refs, EngineConfig(), None).rule_score_ml, real_refs)
    p = predictor.predict(X)
    assert p.shape == (50,) and ((p >= 0) & (p <= 1)).all()
    sv = predictor.explain(X)
    assert list(sv.columns) == FEATURE_LIST and sv.shape == (50, 17)


def test_hybrid_pipeline_fields(predictor, real_refs):
    cfg = EngineConfig()
    t = score_trades([_real_trade(ssi_matched="NO", currency_matched="NO", quantity="6000")],
                     real_refs, cfg, BDATE, predictor)[0]
    assert t.ml_score is not None and t.model_version == "vt"
    assert t.final_score == pytest.approx(round(0.3 * t.ml_score + 0.7 * t.rule_score, 4), abs=1e-4)
    assert set(t.features) == set(FEATURE_LIST) and set(t.shap_values) == set(FEATURE_LIST)
    assert sum(f["pct"] for f in t.risk_factors) == pytest.approx(100.0, abs=0.1)
    assert t.risk_factors[0]["factor"] == "SSI"
    assert "rules only" not in t.explanation
    json.dumps(t.to_dict())


def test_rejected_trades_have_no_ml(predictor, real_refs):
    t = score_trades([_real_trade(symbol="ISRG")], real_refs, EngineConfig(), BDATE, predictor)[0]
    assert t.status == "REJECTED" and t.ml_score is None and t.features is None


def test_broken_predictor_falls_back_to_rules_only(real_refs):
    class Broken:
        def score_batch(self, *a, **k):
            raise RuntimeError("boom")
    t = score_trades([_real_trade(ssi_matched="NO")], real_refs, EngineConfig(), BDATE, Broken())[0]
    assert t.ml_score is None and t.final_score == t.rule_score and "rules only" in t.explanation


def test_large_batch_uses_approximate_shap(predictor, history, real_refs, monkeypatch):
    calls = []
    real = predictor.explain

    def spy(X, approximate=None):
        calls.append(len(X) > 100 if approximate is None else approximate)
        return real(X, approximate)
    monkeypatch.setattr(predictor, "explain", spy)
    recs = [_real_trade(trade_id=f"X{i}") for i in range(150)]
    score_trades(recs, real_refs, EngineConfig(), BDATE, predictor)
    assert calls == [True]
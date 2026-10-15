"""BE-6: model registry, retrain, promote, drift (endpoints 37-44, 46).

The tests in this file run IN ORDER and build on each other (no model -> v1 registered -> trades
scored -> drift -> retrain v2 -> promote). The module fixture puts everything back at the end
(no production model, rules-only scoring), because every other test file expects rules-only scores.
"""
import sys

import pytest
from sqlalchemy import func, select, update

from app.core.config import BACKEND_DIR
from app.engine import EngineError
from app.models import (
    AuditLog, DriftCheck, EmailOutbox, MLPrediction, ModelEvaluation, ModelVersion, Notification, RetrainingEvent,
    SystemConfig, Trade,
)
from app.services import engine_adapter, ml_service, upload_service
from tests.conftest import assert_error_shape

ML, TRADES, HEALTH = "/api/v1/ml", "/api/v1/trades", "/api/v1/health"
ML_ADMIN, ADMIN, UPLOADER = "ml.admin", "sys.admin", "fatima.hassan"
REPORT = BACKEND_DIR / "ml_artifacts" / "model_v1_report.json"
HEADER = "tradeId,tradeType,symbol,quantity,price,counterparty,tradeDate,settlementDate,accountId,ssiMatched,currencyMatched,traderName"
METRICS = {"aucRoc", "precision", "recall", "f1", "accuracy", "falsePositiveRate"}

pytestmark = pytest.mark.skipif(not REPORT.is_file(), reason="ml_artifacts/model_v1_report.json is not in this checkout")


@pytest.fixture(autouse=True, scope="module")
def _ml_sandbox(_database, tmp_path_factory):
    from app.core.database import SessionLocal

    def business_date(value):
        with SessionLocal() as session:
            session.get(SystemConfig, "business_date_override").value = value
            session.commit()
        engine_adapter.invalidate_cache()

    business_date("2026-09-28")
    models = tmp_path_factory.mktemp("models")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(engine_adapter, "model_dir", lambda: models)                  # new models go to a temp folder
        patch.setattr(upload_service, "REPORT_DIR", tmp_path_factory.mktemp("reports"))
        patch.setattr(ml_service, "TRAIN_OPTIONS", {"params": {"n_estimators": 15, "max_depth": 6},
                                                    "cv_folds": 0, "shap_sample": 100})   # a small, fast forest
        yield models
    with SessionLocal() as session:                                                 # back to rules-only for the other files
        session.execute(update(ModelVersion).where(ModelVersion.status == "PRODUCTION").values(status="ARCHIVED"))
        session.commit()
    engine_adapter.set_predictor(None)
    business_date(None)


def upload(client, login, rows: list[str]):
    text = "\r\n".join([HEADER, *rows]) + "\r\n"
    response = client.post(f"{TRADES}/bulk", files={"file": ("ml.csv", text.encode(), "text/csv")}, headers=login(UPLOADER))
    assert response.status_code == 202, response.text
    return response.json()["batchId"]


def actions(db, *names):
    return db.scalars(select(AuditLog.action).where(AuditLog.action.in_(names)).order_by(AuditLog.id)).all()


# ---------------------------------------------------------------- 1. nothing registered yet
def test_without_a_production_model_the_app_is_rules_only(client, login):
    headers = login(ML_ADMIN)
    assert client.get(f"{ML}/models", headers=headers).json() == []
    assert client.get(f"{ML}/metrics/history", headers=headers).json() == []
    assert_error_shape(client.get(f"{ML}/models/production", headers=headers), 404, "NOT_FOUND")
    assert_error_shape(client.get(f"{ML}/drift/latest", headers=headers), 404, "NOT_FOUND")
    assert "production model" in assert_error_shape(client.post(f"{ML}/drift/run", headers=headers), 409, "CONFLICT")["message"]
    assert client.get(HEALTH).json()["model"] is None


# ---------------------------------------------------------------- 2. scripts/register_model.py
def test_register_script_puts_v1_in_production(client, login, db, monkeypatch, capsys):
    from scripts import register_model

    monkeypatch.setattr(sys, "argv", ["register_model.py", str(REPORT)])
    assert register_model.main() == 0
    row = db.scalar(select(ModelVersion).where(ModelVersion.version == "v1"))
    assert row.status == "PRODUCTION" and row.promoted_at and len(row.feature_list) == 17 and row.baseline_stats
    assert db.scalar(select(func.count()).select_from(ModelEvaluation).where(ModelEvaluation.model_version_id == row.id)) == 10
    initial = db.scalar(select(RetrainingEvent).where(RetrainingEvent.new_model_version_id == row.id))
    assert (initial.trigger_reason, initial.status) == ("INITIAL", "SUCCEEDED")

    monkeypatch.setattr(sys, "argv", ["register_model.py", str(REPORT), "--if-no-production"])   # safe at every start
    assert register_model.main() == 0 and "already PRODUCTION" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["register_model.py", "ml_artifacts/missing_report.json", "--if-no-production"])
    assert register_model.main() == 0                                              # no model file: still starts
    assert db.scalar(select(func.count()).select_from(ModelVersion)) == 1
    assert db.scalar(select(func.count()).select_from(RetrainingEvent)) == 1

    assert client.get(HEALTH).json()["model"] is None                             # not loaded until the app (re)loads it
    engine_adapter.load_predictor(db)
    assert client.get(HEALTH).json() == {"status": "ok", "db": "ok", "model": "v1"}


# ---------------------------------------------------------------- 3. registry (37-39)
def test_model_list_and_production_model_with_targets(client, login):
    headers = login(ML_ADMIN)
    models = client.get(f"{ML}/models", headers=headers).json()
    production = client.get(f"{ML}/models/production", headers=headers).json()
    assert models == [production]
    assert set(production) == {"version", "algorithm", "status", "metrics", "targets", "passed", "decisionThreshold",
                               "trainRows", "trainedAt", "promotedAt"}
    assert (production["version"], production["algorithm"], production["status"]) == ("v1", "RandomForestClassifier", "PRODUCTION")
    assert set(production["metrics"]) == set(production["targets"]) == set(production["passed"]) == METRICS
    assert production["targets"] == {"aucRoc": 0.9, "precision": 0.7, "recall": 0.85, "f1": 0.77, "accuracy": 0.9,
                                     "falsePositiveRate": 0.05}
    assert production["metrics"]["aucRoc"] > 0.9 and production["metrics"]["falsePositiveRate"] < 0.05
    assert all(production["passed"].values()) and production["decisionThreshold"] == 0.8
    assert production["trainRows"] == 40000 and production["promotedAt"]


def test_metrics_history_for_the_trend_chart(client, login):
    headers = login(ML_ADMIN)
    rows = client.get(f"{ML}/metrics/history", headers=headers).json()
    assert len(rows) == 10 and set(rows[0]) == METRICS | {"modelVersion", "evaluatedAt", "datasetLabel", "sampleSize"}
    labels = [r["datasetLabel"] for r in rows]
    assert labels[:9] == [f"month_2026-{m:02d}" for m in range(1, 10)] and labels[9] == "holdout_test"
    assert all(r["modelVersion"] == "v1" and 0 < r["aucRoc"] <= 1 and r["sampleSize"] > 0 for r in rows)
    assert len(client.get(f"{ML}/metrics/history?modelVersion=v1", headers=headers).json()) == 10
    assert client.get(f"{ML}/metrics/history?modelVersion=v9", headers=headers).json() == []
    assert client.get(f"{ML}/metrics/history?to=2020-01-01", headers=headers).json() == []
    assert len(client.get(f"{ML}/metrics/history?from=2020-01-01&to=2099-01-01", headers=headers).json()) == 10
    assert_error_shape(client.get(f"{ML}/metrics/history?from=yesterday", headers=headers), 422, "VALIDATION_ERROR")


# ---------------------------------------------------------------- 4. trades are scored by the model
def test_uploaded_trades_now_get_an_ml_score_and_a_prediction_row(client, login, db):
    upload(client, login, ["MLT00001,BUY,AAPL,1000,10250.5,BlackRock,2026-09-29,2026-10-01,ACC1000,NO,NO,Fatima Hassan"])
    trade = client.get(f"{TRADES}/MLT00001", headers=login(UPLOADER)).json()
    assert trade["modelVersion"] == "v1" and 0 < trade["mlScore"] <= 1 and trade["ruleScore"] == 0.75
    assert trade["finalScore"] == pytest.approx(0.3 * trade["mlScore"] + 0.7 * 0.75, abs=0.0002)   # hybrid score
    assert trade["shapValues"] and "SSI_Matched_Flag" in trade["shapValues"]
    row = db.scalar(select(MLPrediction).join(Trade, Trade.id == MLPrediction.trade_pk).where(Trade.trade_id == "MLT00001"))
    assert float(row.alpha) == 0.3 and float(row.beta) == 0.7 and row.classification == trade["status"]
    assert len(row.features) == 17


# ---------------------------------------------------------------- 5. drift (43-44)
def test_drift_check_needs_enough_scored_trades(client, login):
    error = assert_error_shape(client.post(f"{ML}/drift/run", headers=login(ML_ADMIN)), 400, "BAD_REQUEST")
    assert "at least 500" in error["message"]
    assert_error_shape(client.get(f"{ML}/drift/latest", headers=login(ML_ADMIN)), 404, "NOT_FOUND")


def test_drift_check_finds_drift_and_alerts_the_ml_admins(client, login, db):
    rows = [f"MLD{n:05d},{'BUY' if n % 2 else 'SELL'},AAPL,{9000 + n},10250.5,BlackRock,2026-09-29,2026-10-03,ACC1000,NO,NO,Fatima Hassan"
            for n in range(1, 521)]                                                 # all SSI = NO, big quantities, weekend
    upload(client, login, rows)
    response = client.post(f"{ML}/drift/run", headers=login(ML_ADMIN))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"runAt", "modelVersion", "threshold", "driftDetected", "results", "sampleSize"}
    assert (body["modelVersion"], body["threshold"], body["driftDetected"], body["sampleSize"]) == ("v1", 0.2, True, 521)
    assert len(body["results"]) == 17 and set(body["results"][0]) == {"feature", "psi", "severity"}
    psis = [r["psi"] for r in body["results"]]
    assert psis == sorted(psis, reverse=True)
    by_feature = {r["feature"]: r for r in body["results"]}
    assert by_feature["SSI_Matched_Flag"]["severity"] == "RED" and by_feature["Quantity"]["severity"] == "RED"
    assert {r["severity"] for r in body["results"]} <= {"GREEN", "AMBER", "RED"}
    for r in body["results"]:
        assert r["severity"] == ("RED" if r["psi"] > 0.2 else "AMBER" if r["psi"] >= 0.1 else "GREEN")

    assert client.get(f"{ML}/drift/latest", headers=login(ML_ADMIN)).json() == body
    check = db.scalar(select(DriftCheck).order_by(DriftCheck.id.desc()))
    assert check.drift_detected and float(check.max_psi) == psis[0] and check.sample_size == 521
    note = db.scalar(select(Notification).where(Notification.type == "DRIFT_ALERT").order_by(Notification.id.desc()))
    assert (note.role_code, note.user_id, note.severity) == ("ML_ADMIN", None, "CRITICAL")
    assert body["results"][0]["feature"] in note.message and "521 recent trades" in note.message   # worst feature is named
    mail = db.scalar(select(EmailOutbox).where(EmailOutbox.purpose == "DRIFT_ALERT").order_by(EmailOutbox.id.desc()))
    assert mail.to_addresses == "ml.admin@tradeperfect.local"
    assert actions(db, "DRIFT_CHECK_RUN", "DRIFT_ALERT") == ["DRIFT_CHECK_RUN", "DRIFT_ALERT"]
    mine = client.get("/api/v1/notifications", headers=login(ML_ADMIN)).json()
    assert any(n["type"] == "DRIFT_ALERT" and n["severity"] == "CRITICAL" for n in mine)   # shown in the bell


# ---------------------------------------------------------------- 6. retrain -> promote (40-42)
def test_retrain_creates_a_candidate_then_promote_swaps_the_model_without_restart(client, login, db, _ml_sandbox):
    response = client.post(f"{ML}/retrain", json={"notes": "monthly refresh"}, headers=login(ML_ADMIN))
    assert response.status_code == 202, response.text
    event_id = response.json()["retrainingEventId"]

    status = client.get(f"{ML}/retrain/{event_id}", headers=login(ML_ADMIN)).json()
    assert set(status) == {"id", "status", "triggerReason", "startedAt", "finishedAt", "newModelVersion", "log"}
    assert (status["status"], status["triggerReason"], status["newModelVersion"]) == ("SUCCEEDED", "MANUAL", "v2"), status
    assert "monthly refresh" in status["log"] and "50,000 historical trades" in status["log"] and status["finishedAt"]
    assert (_ml_sandbox / "model_v2.joblib").is_file() and (_ml_sandbox / "model_v2_report.json").is_file()

    by_version = {m["version"]: m for m in client.get(f"{ML}/models", headers=login(ML_ADMIN)).json()}
    assert by_version["v2"]["status"] == "CANDIDATE" and by_version["v1"]["status"] == "PRODUCTION"
    assert by_version["v2"]["metrics"]["aucRoc"] > 0.8 and by_version["v2"]["promotedAt"] is None
    assert len(client.get(f"{ML}/metrics/history?modelVersion=v2", headers=login(ML_ADMIN)).json()) == 10
    assert client.get(HEALTH).json()["model"] == "v1"                              # a candidate changes nothing yet
    note = db.scalar(select(Notification).where(Notification.type == "RETRAIN_DONE").order_by(Notification.id.desc()))
    assert note.role_code == "ML_ADMIN" and "v2" in note.title and note.severity == "INFO"
    assert actions(db, "RETRAIN_STARTED", "RETRAIN_COMPLETED", "RETRAIN_FAILED") == ["RETRAIN_STARTED", "RETRAIN_COMPLETED"]

    promoted = client.post(f"{ML}/models/v2/promote", headers=login(ML_ADMIN))
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "PRODUCTION" and promoted.json()["promotedAt"]
    by_version = {m["version"]: m["status"] for m in client.get(f"{ML}/models", headers=login(ML_ADMIN)).json()}
    assert by_version == {"v1": "ARCHIVED", "v2": "PRODUCTION"}
    assert client.get(HEALTH).json()["model"] == "v2"                              # no restart
    assert engine_adapter.get_predictor().version == "v2"
    upload(client, login, ["MLT00002,BUY,AAPL,1000,10250.5,BlackRock,2026-09-29,2026-10-01,ACC1000,NO,NO,Fatima Hassan"])
    assert client.get(f"{TRADES}/MLT00002", headers=login(UPLOADER)).json()["modelVersion"] == "v2"
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "MODEL_PROMOTED").order_by(AuditLog.id.desc()))
    assert audit.entity_id == "v2" and audit.before_state == {"production": "v1"} and audit.actor_username == ML_ADMIN

    assert "already" in assert_error_shape(client.post(f"{ML}/models/v2/promote", headers=login(ML_ADMIN)), 409, "CONFLICT")["message"]
    assert_error_shape(client.post(f"{ML}/models/v77/promote", headers=login(ML_ADMIN)), 404, "NOT_FOUND")
    assert_error_shape(client.get(f"{ML}/retrain/999999", headers=login(ML_ADMIN)), 404, "NOT_FOUND")
    assert client.post(f"{ML}/models/v1/promote", headers=login(ADMIN)).json()["status"] == "PRODUCTION"   # roll back
    assert client.get(HEALTH).json()["model"] == "v1"


def test_only_one_retraining_run_at_a_time_and_restart_cleanup(client, login, db):
    running = RetrainingEvent(trigger_reason="MANUAL", status="RUNNING")
    db.add(running)
    db.commit()
    error = assert_error_shape(client.post(f"{ML}/retrain", headers=login(ML_ADMIN)), 409, "CONFLICT")
    assert "already in progress" in error["message"]
    assert ml_service.fail_interrupted_retrains(db) == 1                           # what happens at startup
    db.refresh(running)
    assert running.status == "FAILED" and "restarted" in running.log and running.finished_at


def test_a_failed_training_run_is_reported_and_changes_nothing(client, login, db, monkeypatch):
    def broken(*args, **kwargs):
        raise EngineError("need >= 1,000 labelled rows with both outcomes to train")

    monkeypatch.setattr(ml_service, "train_model", broken)
    models_before = db.scalar(select(func.count()).select_from(ModelVersion))
    event_id = client.post(f"{ML}/retrain", headers=login(ML_ADMIN)).json()["retrainingEventId"]
    status = client.get(f"{ML}/retrain/{event_id}", headers=login(ML_ADMIN)).json()
    assert status["status"] == "FAILED" and status["newModelVersion"] is None and "1,000 labelled rows" in status["log"]
    assert db.scalar(select(func.count()).select_from(ModelVersion)) == models_before
    assert actions(db, "RETRAIN_FAILED") == ["RETRAIN_FAILED"]
    note = db.scalar(select(Notification).where(Notification.type == "RETRAIN_DONE").order_by(Notification.id.desc()))
    assert note.severity == "WARNING" and note.title == "Retraining failed"
    assert client.get(HEALTH).json()["model"] == "v1"


def test_a_model_that_cannot_be_loaded_is_never_promoted(client, login, db):
    broken = ModelVersion(version="v90", feature_list=[], metrics={}, artifact_path="/nowhere/model_v90.joblib", status="CANDIDATE")
    failed = ModelVersion(version="v91", feature_list=[], metrics={}, artifact_path="/nowhere/model_v91.joblib", status="FAILED")
    db.add_all([broken, failed])
    db.commit()
    error = assert_error_shape(client.post(f"{ML}/models/v90/promote", headers=login(ML_ADMIN)), 409, "CONFLICT")
    assert "cannot be loaded" in error["message"]
    assert_error_shape(client.post(f"{ML}/models/v91/promote", headers=login(ML_ADMIN)), 409, "CONFLICT")
    assert client.get(f"{ML}/models/production", headers=login(ML_ADMIN)).json()["version"] == "v1"
    assert client.get(HEALTH).json()["model"] == "v1" and engine_adapter.get_predictor().version == "v1"
    listed = {m["version"]: m for m in client.get(f"{ML}/models", headers=login(ML_ADMIN)).json()}
    assert listed["v90"]["metrics"]["aucRoc"] is None and not any(listed["v90"]["passed"].values())   # no metrics: nothing passes


# ---------------------------------------------------------------- 7. hybrid weights (46)
def test_put_ml_config_validates_and_takes_effect_at_once(client, login, db):
    headers = login(ML_ADMIN)
    try:
        body = client.put(f"{ML}/config", json={"alpha": 0.4}, headers=headers)     # beta follows: 0.6
        assert body.status_code == 200, body.text
        assert body.json() == {"alpha": 0.4, "beta": 0.6, "passThreshold": 0.5, "exceptionThreshold": 0.7, "psiThreshold": 0.2}
        assert client.get(f"{ML}/config", headers=headers).json()["alpha"] == 0.4
        assert engine_adapter.get_engine_config(db).alpha == 0.4                    # the cache was cleared
        changed = client.put(f"{ML}/config", json={"passThreshold": 0.45, "exceptionThreshold": 0.75, "psiThreshold": 0.25},
                             headers=headers).json()
        assert (changed["passThreshold"], changed["exceptionThreshold"], changed["psiThreshold"]) == (0.45, 0.75, 0.25)
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "CONFIG_CHANGED", AuditLog.entity_id == "hybrid_weights")
                          .order_by(AuditLog.id))
        assert audit.before_state == {"value": {"alpha": 0.3, "beta": 0.7}} and audit.after_state == {"value": {"alpha": 0.4, "beta": 0.6}}
        assert audit.actor_username == ML_ADMIN

        for bad, status in (({"alpha": 0.5, "beta": 0.6}, 400), ({"passThreshold": 0.8}, 400), ({"exceptionThreshold": 0.3}, 400),
                            ({"alpha": 1.5}, 422), ({"psiThreshold": 0}, 422), ({"alpha": 0.4}, 400), ({}, 400)):
            response = client.put(f"{ML}/config", json=bad, headers=headers)
            assert response.status_code == status, (bad, response.text)
        assert client.get(f"{ML}/config", headers=headers).json()["alpha"] == 0.4   # failed calls changed nothing
    finally:
        client.put(f"{ML}/config", json={"alpha": 0.3, "beta": 0.7, "passThreshold": 0.5, "exceptionThreshold": 0.7,
                                         "psiThreshold": 0.2}, headers=login(ADMIN))
    assert client.get(f"{ML}/config", headers=headers).json() == {"alpha": 0.3, "beta": 0.7, "passThreshold": 0.5,
                                                                  "exceptionThreshold": 0.7, "psiThreshold": 0.2}


# ---------------------------------------------------------------- 8. permissions
@pytest.mark.parametrize("username", ["priya.sharma", "ops.user1", "ops.manager"])
def test_only_ml_admin_and_sys_admin_manage_models(client, login, username):
    headers = login(username)
    for call in (client.get(f"{ML}/models", headers=headers), client.get(f"{ML}/models/production", headers=headers),
                 client.get(f"{ML}/metrics/history", headers=headers), client.post(f"{ML}/retrain", headers=headers),
                 client.get(f"{ML}/retrain/1", headers=headers), client.post(f"{ML}/models/v1/promote", headers=headers),
                 client.get(f"{ML}/drift/latest", headers=headers), client.post(f"{ML}/drift/run", headers=headers),
                 client.put(f"{ML}/config", json={"alpha": 0.5}, headers=headers)):
        assert_error_shape(call, 403, "FORBIDDEN")


def test_no_token_is_401(client):
    for call in (client.get(f"{ML}/models"), client.post(f"{ML}/retrain"), client.post(f"{ML}/models/v1/promote"),
                 client.post(f"{ML}/drift/run"), client.put(f"{ML}/config", json={"alpha": 0.5})):
        assert_error_shape(call, 401, "UNAUTHORIZED")

"""Register a trained model in model_versions (PROJECT_CONTEXT 8.3 / 6.5 Registry).

Reads ml_artifacts/model_<v>_report.json (written by `python -m app.engine.cli train`),
checks the .joblib next to it really loads, then inserts/updates the model_versions row.
By default the model becomes PRODUCTION (any other PRODUCTION row -> ARCHIVED).
Safe to run again: the same version is updated, never duplicated.

Usage (backend folder or inside the container):
    python scripts/register_model.py ml_artifacts/model_v1_report.json
    python scripts/register_model.py ml_artifacts/model_v2_report.json --candidate
Restart the backend afterwards so load_predictor() picks up the PRODUCTION model.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

import app.models as models  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.engine import Predictor  # noqa: E402
from app.models import ModelVersion  # noqa: E402

METRIC_KEYS = ("auc_roc", "precision", "recall", "f1", "accuracy", "false_positive_rate")


def _trained_at(report: dict) -> datetime:
    raw = (report.get("diagnostics") or {}).get("trained_at") or report.get("trained_at")
    try:
        return datetime.fromisoformat(raw) if raw else datetime.now(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _save_evaluations(db, mv, report: dict, trained_at: datetime) -> None:
    """Optional: monthly + holdout evaluations for the trend chart, if a ModelEvaluation model exists."""
    Eval = getattr(models, "ModelEvaluation", None)
    if Eval is None:
        print("  evaluations: no ModelEvaluation model - skipped")
        return
    cols = {c.name: c for c in Eval.__table__.columns}
    link = next((n for n in ("model_version_id", "model_id", "model_version") if n in cols), None)
    if link is None:
        print(f"  evaluations: cannot find the model link column in {list(cols)} - skipped")
        return
    link_value = mv.version if link == "model_version" else mv.id
    if db.scalar(select(Eval).where(getattr(Eval, link) == link_value).limit(1)) is not None:
        print("  evaluations: already stored for this version - skipped")
        return

    rows = list(report.get("monthly_evaluations") or [])
    rows.append({"dataset_label": "holdout_test", "sample_size": report.get("test_rows"),
                 **(report.get("metrics") or {})})
    sp = db.begin_nested()
    try:
        for row in rows:
            values = {}
            for name in cols:
                if name == "id":
                    continue
                if name == link:
                    values[name] = link_value
                elif name in row:
                    values[name] = row[name]
                elif name == "evaluated_at":
                    values[name] = trained_at
                elif name == "metrics":
                    values[name] = {k: row.get(k) for k in METRIC_KEYS}
            missing = [n for n, c in cols.items() if n != "id" and n not in values and not c.nullable
                       and c.default is None and c.server_default is None]
            if missing:
                raise ValueError(f"required columns not recognised: {missing}")
            db.add(Eval(**values))
        db.flush()
        sp.commit()
        print(f"  evaluations: {len(rows)} rows stored ({len(rows) - 1} monthly + holdout)")
    except Exception as exc:  # noqa: BLE001 - optional part must never block registration
        sp.rollback()
        print(f"  evaluations: skipped ({exc})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", help="e.g. ml_artifacts/model_v1_report.json")
    ap.add_argument("--candidate", action="store_true", help="register as CANDIDATE (do not promote)")
    ap.add_argument("--notes", default=None)
    ap.add_argument("--if-no-production", action="store_true",
                    help="do nothing if a PRODUCTION model already exists or the report is missing (safe at every start)")
    args = ap.parse_args()

    if args.if_no_production:
        if not os.path.isfile(args.report):
            print(f"no report at {args.report} - nothing registered (the app runs rules-only)")
            return 0
        gen = get_db()
        db = next(gen)
        try:
            current = db.scalar(select(ModelVersion.version).where(ModelVersion.status == "PRODUCTION"))
        finally:
            gen.close()
        if current is not None:
            print(f"model {current} is already PRODUCTION - nothing to do")
            return 0

    with open(args.report, encoding="utf-8") as fh:
        report = json.load(fh)
    version = str(report["version"])
    artifact = os.path.abspath(os.path.join(os.path.dirname(args.report), f"model_{version}.joblib"))

    predictor = Predictor.load(artifact)
    print(f"artifact OK: {artifact} ({predictor.algorithm}, {len(predictor.feature_list)} features)")

    trained_at = _trained_at(report)
    fields = dict(
        algorithm=report.get("algorithm") or predictor.algorithm,
        hyperparameters=report.get("hyperparameters"),
        feature_list=list(report["feature_list"]),
        decision_threshold=float(report["decision_threshold"]),
        metrics=report["metrics"],
        global_shap=report.get("global_shap"),
        baseline_stats=report.get("baseline_stats"),
        train_rows=report.get("train_rows"),
        test_rows=report.get("test_rows"),
        artifact_path=artifact,
        trained_at=trained_at,
    )
    if args.notes is not None:
        fields["notes"] = args.notes

    gen = get_db()
    db = next(gen)
    try:
        mv = db.scalar(select(ModelVersion).where(ModelVersion.version == version))
        if mv is None:
            mv = ModelVersion(version=version, status="CANDIDATE", **fields)
            db.add(mv)
            print(f"model_versions: inserted {version}")
        else:
            for key, value in fields.items():
                setattr(mv, key, value)
            print(f"model_versions: updated {version}")
        db.flush()

        if not args.candidate:
            others = db.scalars(select(ModelVersion).where(ModelVersion.status == "PRODUCTION",
                                                           ModelVersion.version != version)).all()
            for other in others:
                other.status = "ARCHIVED"
                print(f"model_versions: {other.version} PRODUCTION -> ARCHIVED")
            db.flush()
            mv.status = "PRODUCTION"
            mv.promoted_at = datetime.now(timezone.utc)
            print(f"model_versions: {version} -> PRODUCTION")

        _save_evaluations(db, mv, report, trained_at)
        Event = getattr(models, "RetrainingEvent", None)
        if Event is not None and db.scalar(select(Event.id).where(Event.new_model_version_id == mv.id).limit(1)) is None:
            db.add(Event(trigger_reason="INITIAL", status="SUCCEEDED", finished_at=datetime.now(timezone.utc),
                         new_model_version_id=mv.id, log=f"Registered from {os.path.basename(args.report)}"))
            print("retraining_events: INITIAL row added")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        gen.close()

    print("done - restart the backend so it loads the PRODUCTION model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

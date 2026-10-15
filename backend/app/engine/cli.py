"""Command line for the engine. Run from backend/:

    python -m app.engine.cli score-file ../data/Trade_Dataset_20K.csv --business-date 2026-09-28 [--model ml_artifacts/model_v1.joblib]
    python -m app.engine.cli train --data ../data --out ml_artifacts --version v1
    python -m app.engine.cli drift ../data/Trade_Dataset_20K.csv --model ml_artifacts/model_v1.joblib
    python -m app.engine.cli gen-tests --out tests/generated
"""
from __future__ import annotations

import argparse
import collections
import logging
import sys
import time

import pandas as pd

from .calendar import resolve_business_date
from .config import EngineConfig
from .normalize import read_trade_csv
from .pipeline import score_trades
from .reference import load_historical_csv, load_reference_data

DEFAULT_DATA = "../data"


def _load_refs(data_dir: str):
    hist = load_historical_csv(f"{data_dir}/Historical_Trade_Dataset_50K.csv")
    return load_reference_data(f"{data_dir}/Ref_Data", hist)


def cmd_score_file(args: argparse.Namespace) -> int:
    bdate = resolve_business_date(args.business_date)
    t0 = time.time()
    refs = _load_refs(args.data)
    records = read_trade_csv(args.file)
    t1 = time.time()
    cfg = EngineConfig()
    predictor = None
    if args.model:
        from .model import Predictor
        predictor = Predictor.load(args.model)

    results = []
    for start in range(0, len(records), args.chunk):                 # same chunking as the backend
        results += score_trades(records[start:start + args.chunk], refs, cfg, bdate, predictor=predictor)
    t2 = time.time()

    status = collections.Counter(r.status for r in results)
    rule_counts = collections.Counter(e.rule_id for r in results for e in r.rejection_reasons)
    print(f"File           : {args.file}  ({len(records):,} rows, chunks of {args.chunk:,})")
    print(f"Business date  : {bdate}")
    mode = f"hybrid ({cfg.alpha} x ML {predictor.version} + {cfg.beta} x rules)" if predictor else "rules only (no model)"
    print(f"Mode           : {mode}")
    print(f"REJECTED = {status['REJECTED']:,} ("
          + ", ".join(f"{k} {v:,}" for k, v in rule_counts.most_common()) + ")")
    for s in ("PASS", "MONITOR", "EXCEPTION"):
        print(f"{s:<10} = {status[s]:,}")
    zero = sum(1 for r in results if r.status != "REJECTED" and r.final_score == 0)
    print(f"Accepted with score 0 (no queue item) = {zero:,}")
    print(f"Time: load {t1 - t0:.1f}s · score {t2 - t1:.1f}s ({len(records) / max(t2 - t1, 1e-9):,.0f} rows/s)")

    if args.out:
        pd.DataFrame([{
            "trade_id": r.trade_id, "status": r.status, "priority": r.priority,
            "rule_score": r.rule_score, "ml_score": r.ml_score, "final_score": r.final_score,
            "likely_reason": r.likely_reason, "suggested_action": r.suggested_action,
            "explanation": r.explanation,
            "rejection_reasons": "; ".join(f"{e.rule_id} {e.message}" for e in r.rejection_reasons),
            "warnings": "; ".join(r.warnings),
        } for r in results]).to_csv(args.out, index=False)
        print(f"Wrote {args.out}")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    from .evaluate import METRIC_TARGETS
    from .train import train_model

    t0 = time.time()
    hist = load_historical_csv(f"{args.data}/Historical_Trade_Dataset_50K.csv")
    refs = load_reference_data(f"{args.data}/Ref_Data", hist)
    print(f"Training {args.version} on {len(hist):,} historical trades ...")
    r = train_model(hist, refs, EngineConfig(), args.version, args.out, cv_folds=args.cv_folds,
                    overwrite=args.overwrite)
    d = r.diagnostics

    print(f"\nDecision threshold : {r.decision_threshold}  (qualified: {d['threshold_qualified']})")
    print(f"{'Metric':<22}{'Value':>8}   Target")
    for name, (op, target) in METRIC_TARGETS.items():
        ok = "PASS" if d["targets_passed"][name] else "FAIL"
        print(f"{name:<22}{r.metrics[name]:>8.4f}   {op} {target:<5} {ok}")
    cm = d["confusion_matrix"]
    print(f"Confusion matrix   : TP {cm['tp']:,}  FP {cm['fp']:,}  TN {cm['tn']:,}  FN {cm['fn']:,}")
    if d.get("cv_estimate"):
        cv = d["cv_estimate"]
        h = cv["holdout_metrics_at_cv_threshold"]
        print(f"CV check (5-fold)  : threshold {cv['threshold']} -> holdout acc {h['accuracy']:.4f}, "
              f"recall {h['recall']:.4f}, FPR {h['false_positive_rate']:.4f}")
    print("Monthly accuracy   : " + ", ".join(f"{m['dataset_label'][6:]} {m['accuracy']:.3f}"
                                               for m in r.monthly_evaluations))
    print("Top SHAP features  : " + ", ".join(f"{g['feature']} {g['mean_abs_shap']:.3f}"
                                               for g in r.global_shap[:5]))
    print(f"Artifact           : {r.artifact_path} ({d['artifact_size_mb']} MB)")
    print(f"Report             : {args.out}/model_{args.version}_report.json")
    print(f"Total time         : {time.time() - t0:.0f}s")
    return 0


def cmd_drift(args: argparse.Namespace) -> int:
    import json

    from .drift import compute_drift, drift_detected
    from .hard_gates import run_hard_gates
    from .model import Predictor
    from .normalize import normalize_records
    from .pipeline import _accepted_frame
    from .rules import evaluate_rules

    predictor = Predictor.load(args.model)
    report = args.report or args.model.replace(".joblib", "_report.json")
    with open(report, encoding="utf-8") as fh:
        baseline = json.load(fh)["baseline_stats"]
    refs = _load_refs(args.data)
    batch = normalize_records(read_trade_csv(args.file))
    gates = run_hard_gates(batch, refs)
    ok = [i for i, e in enumerate(gates.errors) if not e]
    df = _accepted_frame(batch, gates, ok)
    rules = evaluate_rules(df, refs, EngineConfig(), resolve_business_date(args.business_date))
    X = predictor.build_features(df, rules.rule_score_ml, refs)
    results = compute_drift(baseline, X, args.threshold)

    print(f"Drift check: {args.file} ({len(X):,} accepted trades) vs training baseline of {predictor.version}")
    print(f"{'Feature':<26}{'PSI':>8}   Severity")
    for r in results:
        print(f"{r['feature']:<26}{r['psi']:>8.4f}   {r['severity']}")
    print(f"Drift detected (PSI > {args.threshold}): {drift_detected(results, args.threshold)}")
    return 0


def cmd_gen_tests(args: argparse.Namespace) -> int:
    from .testgen import generate_all

    paths = generate_all(args.out, args.data)
    for p in paths:
        print(f"wrote {p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="python -m app.engine.cli", description="Trade Perfect engine CLI")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("score-file", help="validate + score a trade CSV (add --model for hybrid ML scoring)")
    s.add_argument("file", help="trade CSV, e.g. ../data/Trade_Dataset_20K.csv")
    s.add_argument("--business-date", help="YYYY-MM-DD (default: today in Asia/Kolkata)")
    s.add_argument("--data", default=DEFAULT_DATA, help="folder with the 50K history and Ref_Data/")
    s.add_argument("--chunk", type=int, default=10_000, help="batch size (default 10,000)")
    s.add_argument("--out", help="optional CSV with one result row per trade")
    s.add_argument("--model", help="model artifact, e.g. ml_artifacts/model_v1.joblib (omit = rules only)")
    s.set_defaults(func=cmd_score_file)

    t = sub.add_parser("train", help="train the Random Forest and write model_<version>.joblib + report")
    t.add_argument("--data", default=DEFAULT_DATA, help="folder with the 50K history and Ref_Data/")
    t.add_argument("--out", default="ml_artifacts", help="output folder (default ml_artifacts)")
    t.add_argument("--version", default="v1", help="model version label, e.g. v1, v2")
    t.add_argument("--cv-folds", type=int, default=5, help="folds for the CV robustness check (0 = skip)")
    t.add_argument("--overwrite", action="store_true", help="replace an existing model_<version>.joblib")
    t.set_defaults(func=cmd_train)

    d = sub.add_parser("drift", help="PSI drift of a trade CSV vs the model's training baseline")
    d.add_argument("file", help="trade CSV, e.g. ../data/Trade_Dataset_20K.csv")
    d.add_argument("--model", default="ml_artifacts/model_v1.joblib", help="model artifact")
    d.add_argument("--report", help="report JSON with baseline_stats (default: next to the model)")
    d.add_argument("--data", default=DEFAULT_DATA, help="folder with the 50K history and Ref_Data/")
    d.add_argument("--business-date", help="YYYY-MM-DD (default: today in Asia/Kolkata)")
    d.add_argument("--threshold", type=float, default=0.2, help="PSI threshold for RED (default 0.2)")
    d.set_defaults(func=cmd_drift)

    g = sub.add_parser("gen-tests", help="generate parametrized rule tests + edge-case CSV + expected results")
    g.add_argument("--out", default="tests/generated", help="output folder (default tests/generated)")
    g.add_argument("--data", default=DEFAULT_DATA, help="folder with Ref_Data/ (for valid master values)")
    g.set_defaults(func=cmd_gen_tests)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

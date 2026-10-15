# MODULE ML — ML & Scoring Engine

> **Attach to every ML chat:** `PROJECT_CONTEXT.md` + `PROGRESS.md` + this file. Also attach the data you need: `Historical_Trade_Dataset_50K.csv`, `Ref_Data/*.csv`, and `Trade_Dataset_20K.csv` (for testing). After ML-1, also attach the engine files you're changing.
> **Run from:** Claude #1 for ML-1 → ML-3. ML-4 → ML-5 go to whichever Claude user is ahead (default Claude #2). **Paired with:** Member C (data/ML analyst).

---

## 1. Mission

Build `backend/app/engine/`: a **pure Python library** that takes raw trade records and returns, for each one: REJECTED with reasons, **or** a rule score, ML score, final score, PASS/MONITOR/EXCEPTION, priority, risk-factor %, SHAP, likely reason, suggested fix and a plain-English explanation. It also trains the Random Forest, evaluates it, and computes data drift (PSI).

This module contains **all the "intelligence"** judges will look at: business rules (I1), ML model (I2), SHAP (I3), hybrid scoring (I4), plus the drift and metrics logic behind the monitoring dashboard (A3).

## 2. Boundaries

| You own | You never touch |
|---|---|
| `backend/app/engine/**`, `backend/tests/engine/**`, `backend/scripts/generate_test_cases.py` (thin wrapper), `notebooks/**`, `docs/ML_MODEL_REPORT.md`, `backend/ml_artifacts/` contents | DB, SQLAlchemy models, FastAPI routes, `.env`, frontend |

- **No imports** of `fastapi`, `sqlalchemy`, `psycopg`, `app.core`, `app.services` inside `engine/`. Allowed: `pandas, numpy, scikit-learn, shap, joblib`, plus the standard library.
- The **public interface is §8.3 of PROJECT_CONTEXT.md**. Function names, dataclass fields and return shapes must match exactly, because the backend is written against it in parallel. If you must change it, stop, agree with the backend owner, and log it in §17.
- Everything must be **deterministic** (`random_state=42` everywhere).

## 3. Inputs & reference facts you need

- Scoring spec: PROJECT_CONTEXT **§6.1–6.7** (pipeline, hard gates R-000..R-008, soft rules, business date, features, SHAP and risk-factor merge, classification).
- Data facts: **§2.1** (traps in the 20K file), **§2.2** (historical patterns and prototype results), **§2.3** (expected counterparty tiers, which your code must reproduce exactly).
- Config defaults: **§12** (`rule_weights`, thresholds, quantity limits …) → your `EngineConfig` defaults.
- CSV quirks: Symbol and Counterparty files have a **UTF-8 BOM** (`encoding="utf-8-sig"`). Dates are `M/D/YYYY`. Booleans are `YES/NO`. Read everything with `dtype=str` first, then parse, so bad values become errors instead of crashes.
- Header map (normalize.py must accept all of these):

| Canonical | Accepted headers (case/space-insensitive) |
|---|---|
| trade_id | Trade ID, tradeId, trade_id |
| trade_type | Trade Type, tradeType |
| symbol | Symbol |
| quantity | Quantity |
| price | Price |
| counterparty | Counterparty |
| trade_date | Trade Date, tradeDate |
| settlement_date | Settlement Date, settlementDate |
| account_id | Account ID, accountId |
| ssi_matched | SSI Matched, ssiMatched |
| currency_matched | Currency Matched, currencyMatched |
| trader_name | Trader Name, traderName |
| settlement_failed | Settlement Failed (historical only) |

Value parsing: trim everything; `trade_type`/`symbol` upper-case; booleans `YES/Y/TRUE/1` → True, `NO/N/FALSE/0` → False, anything else → error; dates accept `M/D/YYYY` and `YYYY-MM-DD` (and Python `date` objects); quantity must be integral (`"100.0"` OK, `"100.5"` → R-003); counterparty matched case-insensitively, then replaced with the canonical master spelling.

---

## 4. Steps

### ML-1 — Engine skeleton + rules-only scoring  ⟵ do this FIRST (it unblocks the backend)

**Build:** `__init__.py` (re-exports §8.3), `types.py`, `config.py` (`DEFAULT_RULE_WEIGHTS` from §12, `EngineConfig.from_system_config`), `errors.py`, `normalize.py`, `calendar.py`, `hard_gates.py`, `rules.py`, `reference.py`, `explain.py` (rules part), `fixes.py`, `pipeline.py` (`validate_trade`, `score_trades` with `predictor=None`), `cli.py` (`score-file` command).

**Rules details:**
- Evaluate **all** hard gates and return every error. R-001 duplicates: the first occurrence in the batch wins, later ones are rejected, and so is anything in `refs.existing_trade_ids`.
- Settlement-date category cap 0.15: if the triggered rules sum to more, **scale each rule's contribution proportionally**, so `sum(rule_hits.contribution) == rule_score` always holds.
- Rules-only mode: `ml_score=None`, `final_score=rule_score`, and the explanation says "(rules only — ML model unavailable)".
- `suggested_fixes` / `suggested_action` from the fix catalogue (§6.6). Weekend/holiday fixes include the computed next business day.

**Tests (`backend/tests/engine/`):** one pass and one fail case per hard gate; each soft rule; settlement cap; tier boundaries 499/500/999/1000/1500/1501; classification boundaries 0.4999/0.5/0.6999/0.7; priority mapping; normalization (BOM, `9/11/2026`, `" yes "`, `100.0`); duplicate in batch; amendment (own ID not counted as duplicate); risk factor pct sums to 100 (±0.1).

**Acceptance:**
- [ ] `python -m app.engine.cli score-file ../data/Trade_Dataset_20K.csv --business-date 2026-09-28` prints **REJECTED = 704 (R-007 577, R-005 130, R-001 1, R-002 1)** and a status distribution.
- [ ] `compute_counterparty_tiers` reproduces the §2.3 table exactly.
- [ ] 10,000 records score in < 10 s (rules-only).
- [ ] All tests green. Hand the backend owner `__init__.py` + `types.py`.

### ML-2 — Features + training + model v1

**Build:** `features.py` (exact feature names and order from §6.5), `train.py`, `evaluate.py`, `cli.py train`, `notebooks/01_eda.ipynb` (with C), `notebooks/02_model_experiments.ipynb`.

**Training recipe:**
1. `load_historical_csv` → stratified 80/20 split (`random_state=42`).
2. Fit encoders **on the train split only**: smoothed fail rates for counterparty / symbol / (counterparty|symbol) (`(fails + 20·global_mean) / (count + 20)`), and per-symbol price and quantity mean/std. Store them in the artifact.
3. `Rule_Engine_Output` for training rows = `rules.py` with business date = **trade date** (so S-SD-PAST is never triggered in history).
4. `RandomForestClassifier(n_estimators=200, max_depth=12, min_samples_leaf=5, class_weight='balanced', random_state=42, n_jobs=-1)`. **Start here, then experiment** (below).
5. **Threshold sweep** 0.30 → 0.90 (step 0.025). Pick the lowest threshold where `FPR < 0.05 AND recall ≥ 0.85`. If none qualifies, pick the one with the lowest FPR and recall ≥ 0.85, and record it in the report.
6. Metrics on the holdout set; **monthly evaluations** (holdout split by trade month, Jan–Sep, for the trend chart); global SHAP (exact, on a 2,000-row sample); **baseline_stats** for PSI (10 quantile bin edges + proportions for numeric features, category proportions for binary/categorical, built from training features).
7. Save `ml_artifacts/model_v1.joblib` (`compress=3`, **< 50 MB**) + `model_v1_report.json` (= `TrainResult` as JSON).

**⚠ The FPR problem (read this):** targets are AUC ≥ 0.90, Recall ≥ 0.85, Precision ≥ 0.70, F1 ≥ 0.77, Accuracy > 0.90, **FPR < 0.05**. Prototype results:

| Feature set | AUC | Recall | Precision | FPR @0.5 | best FPR with recall ≥ 0.85 |
|---|---|---|---|---|---|
| Simple (SSI, CCY, tier, qty, price z, days, weekend, dow, month-end, rule score) | 0.961 | 0.917 | 0.971 | 0.055 | 0.054 @0.7 |
| Full §6.5 list (+ target-encoded fail rates) | 0.959 | 0.923 | 0.963 | 0.072 | 0.051 @0.775 |

About 850 of the 16.6K non-failing trades have SSI = NO, so a model that flags every SSI = NO trade already sits at FPR ≈ 5%. Ideas, in order:
(a) drop or regularise the target-encoded features (they overfit; try smoothing k = 100);
(b) `min_samples_leaf` 20–50 and `max_features` 0.5;
(c) compare `class_weight=None` vs `'balanced'` vs `{0: 2, 1: 1}` (up-weighting the non-fail class lowers FPR);
(d) `CalibratedClassifierCV` then re-sweep the threshold;
(e) for the **report only**, compare with `HistGradientBoostingClassifier`.
Production stays a **Random Forest** (the reference diagram names it), unless mentors say otherwise. If FPR can't get below 0.05, report the closest result honestly with the trade-off curve. The dashboard then shows FPR in red, which is fine.

**Acceptance:**
- [ ] `python -m app.engine.cli train --data ../data --out ml_artifacts --version v1` runs in < 3 min and writes the artifact + report.
- [ ] Report has metrics, threshold, monthly evaluations (9 rows), global SHAP, baseline_stats, counterparty tiers.
- [ ] `tests/engine/test_model_targets.py` asserts AUC/Recall/Precision/F1/Accuracy targets on the holdout set (FPR asserted only if achieved).
- [ ] Results added to PROGRESS.md §5.

### ML-3 — Predictor + SHAP + full hybrid pipeline

**Build:** `model.py` (`Predictor.load/predict/explain`), and plug it into `pipeline.score_trades`. Finish `explain.py` (merge rule contributions + positive SHAP into the 7 factor groups exactly as §6.6, pct sums to 100, drop < 1%).

**Rules:**
- `explain(features, exact: bool)`: **batches > 100 rows → `approximate=True`** (0.05 s/1K rows); ≤ 100 rows → exact.
- Missing or corrupt artifact → `Predictor.load` raises `ModelLoadError`. The backend then falls back to rules-only mode.
- Features for live trades: `Days_To_Settlement = settlement_date − trade_date` (same as training). Unknown counterparty/symbol in encoders → global mean.
- Return `features` and `shap_values` as plain `dict[str, float]` (JSON-serialisable: no numpy types, use `float()`).

**Acceptance:**
- [ ] 20K file at business date 2026-09-28 gives roughly PASS 9.1K / MONITOR 5.3K / EXCEPTION 4.8K / REJECTED 704 (prototype numbers; small differences are fine).
- [ ] 10,000 records with model + approximate SHAP in **< 20 s**; a single trade with exact SHAP in < 1 s.
- [ ] Explanation example looks like: *"Trade T110219 (BUY 5,635 ALV with UBS) has a 0.75 risk of settlement failure → EXCEPTION. Main drivers: SSI not matched with counterparty (46%), Quantity 5,635 above limit 5,000 (23%), Currency mismatch (20%)."*
- [ ] Every value in the output is JSON-serialisable (test with `json.dumps`).

### ML-4 — Drift, evaluation, retraining support

**Build:** `drift.py` (`compute_drift`, §10 formula, ε = 1e-4, severity GREEN < 0.1 ≤ AMBER ≤ 0.2 < RED, threshold from argument, `InsufficientDataError` if < 500 rows), `evaluate.py` (`evaluate_model` on any labelled DataFrame), and make `train_model` callable repeatedly with new versions (v2, v3 …) for the backend's retrain endpoint. Add the `cli drift` command (baseline from the report JSON vs a CSV file).

**Acceptance:**
- [ ] PSI of a distribution with itself = 0; prototype PSI values (training vs 20K) are close to: SSI 0.06 GREEN, Currency 0.03 GREEN, Quantity 0.12 AMBER, Counterparty_Tier 0.30 RED, Is_Weekend 0.73 RED.
- [ ] `train_model(..., version="v2")` produces a new artifact without touching v1.

### ML-5 — Automated test-case generation + ML report

**Build:** `testgen.py` + `cli gen-tests` + `backend/scripts/generate_test_cases.py` (wrapper):
- (a) Reads `DEFAULT_RULE_WEIGHTS` and the hard-gate list, then **generates** `backend/tests/generated/test_generated_rules.py`: parametrized pytest cases, one positive + one negative + boundary values per rule, with expected status/score.
- (b) Generates `backend/tests/generated/edge_cases.csv` (~50 rows: every §2.1 trap, boundaries, BOM, bad dates, duplicates) and `expected_results.json`. The backend's integration test uploads that CSV.

Write `docs/ML_MODEL_REPORT.md` (with C): data overview, preprocessing, feature engineering (table), training setup, threshold choice, metrics vs targets, confusion matrix, SHAP summary, limitations (synthetic prices, FPR trade-off), retraining and drift approach.

**Acceptance:** `python scripts/generate_test_cases.py && pytest tests/generated` passes; report committed.

---

## 5. Optional extras (only after ML-5)
- `explain.rephrase_with_llm(text)` hook (the backend chatbot may call it).
- Vector DB helper: `build_embedding_text(scored_trade) -> str` for pgvector (§11).

## 6. Definition of Done (ML module)
- [ ] Interface matches §8.3 exactly; backend imports only `app.engine`.
- [ ] Rules reproduce §6.2/6.3 and the §2.3 tiers; 20K trap counts correct.
- [ ] Model v1 artifact + report committed (< 50 MB); metrics in PROGRESS.md.
- [ ] Engine test coverage ≥ 80%; generated tests pass.
- [ ] `ML_MODEL_REPORT.md` done; notebooks run top-to-bottom.

## 7. Session-end ritual
Ask Claude: *"Give me the PROGRESS.md session entry for this step (done, files changed, deviations, next step)."* Commit with `feat(engine): …`, and send the backend owner any changed `__init__.py`/`types.py`.

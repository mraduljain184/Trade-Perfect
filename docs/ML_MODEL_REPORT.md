# Trade Perfect — ML Model Report

**Model:** v1 · RandomForestClassifier · trained on 50,000 labelled historical trades
**Artifacts:** `backend/ml_artifacts/model_v1.joblib` (4.5 MB) · `model_v1_report.json`
**Reproduce:** `cd backend && python -m app.engine.cli train --version v1 --overwrite` (≈ 40 s) · notebooks `notebooks/01_eda.ipynb`, `02_model_experiments.ipynb`

## 1. Problem
Binary classification: will a booked trade **fail to settle**? Output = probability P(fail) ∈ [0, 1], combined with a rule score:
**final = 0.3 × ML + 0.7 × rules** → PASS (< 0.5) · MONITOR (0.5–0.7) · EXCEPTION (≥ 0.7). Weights are configurable by the ML Admin.

## 2. Data
| Dataset | Rows | Use |
|---|---|---|
| Historical_Trade_Dataset_50K | 50,000 (Jan–Sep 2026) | Training / evaluation; label `Settlement Failed` (66.8% YES) |
| Trade_Dataset_20K | 20,000 (Sep–Oct 2026) | Unlabelled demo/upload file; drift check |
| Ref_Data | 140 symbols · 38 counterparties · 100 accounts (3 inactive) | Hard gates + tiers |

**Key findings (EDA):** SSI = NO → 97.3% fail (vs 16.9%); Currency = NO → 90.1% (vs 50.6%); SSI and currency both OK → 9.1%; quantity < 100 or > 5,000 → 100%; trader name and BUY/SELL carry no signal; prices are synthetic (≈ 10,000 mean per symbol); **every counterparty fails ≈ 66.7%** → count-based tiers measure volume, not risk (Q15).

## 3. Pre-processing
- One shared normaliser for UI, files, training: header variants, UTF-8 BOM, M/D/YYYY + ISO dates, YES/Y/TRUE/1 booleans, whole-number quantities.
- Hard gates R-000..R-008 reject invalid trades (20K file: 704 = R-007 577 · R-005 130 · R-001 1 · R-002 1); rejected trades are never scored.

## 4. Features (17, identical code for training and inference)
| Group | Features | Notes |
|---|---|---|
| Matching | SSI_Matched_Flag, Currency_Matched_Flag | strongest signals |
| Counterparty | Counterparty_Tier, Counterparty_Fail_Rate, CP_Symbol_Fail_Rate | fail rates smoothed: (fails + k·mean)/(n + k), k = 100 |
| Instrument | Symbol_Fail_Rate, Quantity, Quantity_ZScore, Price, Price_ZScore | z-scores per symbol |
| Timing | Days_To_Settlement (settlement − trade date), Is_Weekend, Is_Holiday, Settlement_DayOfWeek, Is_Month_End | |
| Other | Trade_Type_Buy, Rule_Engine_Output | rule score **without** the past-date rule (train/serve consistency) |

**Leakage control:** all learned statistics (fail rates, symbol means/stds) are fitted on the training split only, stored in the artifact and reused live; re-fitted inside every CV fold.

## 5. Training setup
Stratified 80/20 split (40K / 10K), `random_state = 42` everywhere.
RandomForest: `n_estimators=200, max_depth=12, min_samples_leaf=5, class_weight='balanced'`.
**Threshold:** lowest value on 0.30–0.90 (step 0.025) with FPR < 5% and recall ≥ 85% → **0.80**. The threshold only affects reported metrics; the hybrid score uses the raw probability.

## 6. Results (holdout, 10,000 trades)
| Metric | v1 | Target | |
|---|---|---|---|
| AUC-ROC | 0.959 | ≥ 0.90 | ✅ |
| Precision | 0.973 | ≥ 0.70 | ✅ |
| Recall | 0.879 | ≥ 0.85 | ✅ |
| F1 | 0.924 | ≥ 0.77 | ✅ |
| Accuracy | 0.903 | > 0.90 | ✅ |
| False positive rate | 0.0496 | < 0.05 | ✅ |

Confusion matrix: TP 5,870 · FP 165 · TN 3,159 · FN 806.
**Robustness:** 5-fold CV estimate (threshold 0.75) → accuracy 0.916, recall 0.900, **FPR 0.051**; time split (train Jan–Jul, test Aug–Sep) → AUC 0.961. Monthly holdout accuracy 0.88–0.92 (Apr 0.880, Jul 0.892 below 0.90).

## 7. Model comparison (same split and features)
| Model | AUC | Recall | Accuracy | FPR | All 6 |
|---|---|---|---|---|---|
| **Random Forest (production)** | 0.959 | 0.879 | 0.903 | 0.049 | ✅ |
| HistGradientBoosting | 0.959 | 0.886 | 0.907 | 0.050 | ✅ |
| Logistic Regression | 0.959 | 0.873 | 0.899 | 0.048 | ❌ |
| MLP neural net (64-32) | 0.956 | 0.851 | 0.886 | 0.045 | ❌ |
| RF without fail-rate features | 0.960 | 0.851 | 0.885 | 0.048 | ❌ |
| Rules alone | 0.947 | 0.888 | 0.901 | 0.072 | ❌ |

Every model reaches AUC ≈ 0.959 — the data ceiling. Random Forest chosen: meets all targets, exact and fast TreeSHAP, robust to outliers, no scaling, named in the reference architecture.

## 8. Explainability
- **Per trade:** SHAP values (exact for ≤ 100 trades, approximate for bulk) merged with rule contributions into one "risk factors %" breakdown, plus likely reason, suggested fix (e.g. next business day) and a one-sentence explanation.
- **Global (mean |SHAP|, 2,000 rows):** SSI_Matched_Flag 0.225 · Rule_Engine_Output 0.162 · Currency_Matched_Flag 0.061 · CP_Symbol_Fail_Rate 0.047 · Quantity 0.015. Counterparty_Tier ranks low (consistent with Q15).

## 9. Monitoring, drift and retraining
- **PSI** per feature vs the training baseline (10 quantile bins / category shares, ε = 1e-4, ≥ 500 rows): GREEN < 0.1 ≤ AMBER ≤ 0.2 < RED; RED triggers an ML-Admin alert.
- 20K demo file: Days_To_Settlement 2.66, Price 1.47, Weekend 0.73, Counterparty_Tier 0.30 (RED); Quantity 0.12 (AMBER); SSI 0.06, Currency 0.03 (GREEN).
- **Retraining:** `train_model(..., version="v2")` writes a new artifact and report; existing versions are never overwritten (guard). Admin compares and promotes; the backend hot-swaps the Predictor.
- **Safety:** missing/corrupt/incompatible artifacts raise `ModelLoadError` → rules-only mode; scoring errors fall back to rules-only for the batch.

## 10. Testing
143 engine unit/acceptance tests + 101 **auto-generated** tests (`python scripts/generate_test_cases.py`): one fail case per hard gate, positive + negative/boundary per soft rule, classification boundaries, and a 49-row edge-case CSV (every data trap) with expected results.

## 11. Limitations
1. Narrow target band (thresholds ≈ 0.79–0.81); CV estimate FPR 5.1% — a data floor (≈ 5% of non-failing trades have SSI = NO).
2. Synthetic data: uniform prices, identical counterparty fail rates → price and tier features carry little signal.
3. No holiday calendar in history (Is_Holiday always 0 in training).
4. Strong drift between history and the demo file (timing, counterparty mix) → monitor and retrain on new labelled data.
5. With ML every accepted trade scores > 0 → all accepted trades enter the repair queue (Q2).

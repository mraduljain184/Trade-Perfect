# TRADE PERFECT — Master Project Context

> **What this file is:** the single source of truth for our hackathon project. Every teammate's Claude chat must be given this file first.
> **Rule:** if code and this file disagree, **this file wins** until someone updates it and logs the change in §17 (Contract Change Log).
> **Companion files:** `schema.sql` (DB source of truth), `PROGRESS.md` (who did what), `KICKOFF_PROMPTS.md` (how to start each chat).

---

## 0. TL;DR (read this if nothing else)

- **What we build:** a web app where trades are **booked** (one by one or by CSV file), **validated** with hard rules, **scored** for settlement-failure risk (`FinalScore = 0.3 × ML_Score + 0.7 × Rule_Score`), **classified** (PASS / MONITOR / EXCEPTION, or REJECTED), **explained** (SHAP plus plain English), and **routed to a Repair Queue** where Ops users fix and re-submit them. Everything is **audit-logged** (immutable). ML Admins watch **model metrics and data drift**. A **chatbot** answers questions about failures.
- **Stack:** React + Vite + TypeScript + Ant Design (frontend) · Python 3.11 + FastAPI (backend **and** ML in the same codebase) · PostgreSQL 16 · scikit-learn Random Forest + SHAP · Docker Compose locally · Render (backend + DB) and Vercel (frontend) for deployment.
- **Three modules** (§15), each with its own brief file and its own Claude chat stream:
  - **FE: Frontend** (`MODULE_FRONTEND.md`, folder `frontend/`).
  - **ML: ML & Scoring Engine** (`MODULE_ML.md`, folder `backend/app/engine/`). A **pure Python library**: validation, rules, ML, SHAP, hybrid score, explanations, drift. No DB, no web.
  - **BE: Backend** (`MODULE_BACKEND.md`): FastAPI + PostgreSQL. Auth, persistence, workflows (repair queue, audit, uploads), all REST APIs, chatbot. It calls the Engine.
- **Contracts that keep the modules independent:** FE↔BE = REST API §8 + JSON shapes §8.2 (FE uses mocks until BE is live). BE↔ML = Python interface §8.3 (BE uses the rules-only engine until the model is ready).

---

## 1. Problem Statement (plain-English version)

Banks book thousands of trades a day. Some will **fail to settle**: the money or shares don't move on the settlement date, usually because the **SSI** (Standard Settlement Instructions, i.e. bank-account and custodian details) don't match the counterparty's, the currency doesn't match, the counterparty is unreliable, or the dates are wrong. Today Ops teams find these failures by hand, after the fact.

We build an **AI-based Trade Booking Validation & Settlement Exception Handler** that:
1. Validates trade bookings (mandatory fields + business rules) and **rejects** bad ones.
2. **Predicts** settlement-failure risk with a **hybrid** score (rules + ML) from 0.0 to 1.0.
3. Explains **why** (risk factor %, SHAP, likely reason, suggested fix, natural-language text).
4. **Routes** risky trades to a **Repair Queue** with priority, assignment and fix workflow.
5. Keeps an **immutable audit trail** of everything.
6. Monitors the ML model (metrics vs targets, PSI drift, SHAP importance, retraining).
7. (Good-to-have) **LLM chatbot**, **vector DB**, real-time **streamer**.

Domain glossary:

| Term | Meaning |
|---|---|
| SSI | Standard Settlement Instructions. `ssiMatched = false` is the #1 failure cause |
| Counterparty | The other bank/firm in the trade (e.g. "BlackRock") |
| Settlement date | The date the trade must actually settle (T+1, T+2, …) |
| Business day | Not Saturday/Sunday and not in the `holidays` table |
| Repair Queue | Work list of risky trades that Ops must review and fix |
| PSI | Population Stability Index: measures how much live data differs from training data |
| SHAP | Explains how much each feature pushed one ML prediction up or down |

---

## 2. Source Documents & Datasets (what we were given)

| Item | Notes |
|---|---|
| `2026_Use_Case_TradePerfect.pdf` | 9-page brief: FR-TB, FR-RV, FR-RQ, FR-AT, FR-MM, roles, tiers, guardrails |
| `Decision_Logic.docx` | Hybrid scoring framework: hard gates R-001..R-007, weighted rules, ML conditions, metric targets (all in §6, §7) |
| `Reference_Diagram.docx` | Flow: Submission → Mandatory → (Reject) → Business rules → Rule score → ML (Random Forest) → Hybrid → Classification → SHAP + Chatbot |
| `Historical_Trade_Dataset_50K.csv` | **Training data.** 50,000 rows, Trade IDs `HT…`, trade dates 2026-01-01 → 2026-09-24, has label `Settlement Failed` (YES 66.8% / NO 33.2%) |
| `Trade_Dataset_20K.csv` | **Primary test/demo file to upload.** 20,000 rows, IDs `T110000–T129999`, trade dates 2026-09-29 → 2026-10-28, **no label** |
| `Trade_Dataset_100K` | Password-protected, **ignored** (team decision) |
| `Ref_Data/Symbol_DataSet.csv` | 140 symbols (file has UTF-8 BOM) |
| `Ref_Data/Counterparty_Dataset.csv` | 38 counterparties (file has UTF-8 BOM) |
| `Ref_Data/Account_MasterData.csv` | 100 accounts ACC1000–ACC1099; **inactive: ACC1011, ACC1063, ACC1093** |
| `Ref_Data/UserRoles.csv` | 5 roles, ids 101–105 |
| `Ref_Data/UserRoles_Matrix.csv` | 25 permissions × 5 roles (§5) |

**CSV columns (both trade files):** `Trade ID, Trade Type, Symbol, Quantity, Price, Counterparty, Trade Date, Settlement Date, Account ID, SSI Matched, Currency Matched, Trader Name` (+ `Settlement Failed` in historical only).
Date format in files is **M/D/YYYY** (e.g. `9/11/2026`); booleans are **YES/NO**.

### 2.1 Known data traps in the 20K file (our tests must cover these)

| Trap | Count | Expected handling |
|---|---|---|
| Row with empty `Trade ID` (row index 183) | 1 | REJECTED (R-001) |
| Row with empty `Trade Type` (T110188) | 1 | REJECTED (R-002) |
| Symbols not in master: `ISRG` (master has `ISRG1`!), `TMO1`, `HD12` | 130 | REJECTED (R-005) |
| Inactive accounts ACC1011 / ACC1063 / ACC1093 | 577 | REJECTED (R-007) |
| Absurd quantities (T111854 = 123,444,297; T120236 = 223,333,333) | 2 | Not a hard reject (still a positive integer) → `Quantity > 5000` rule +0.10, flagged in explanation |
| Quantity > 5000 | 3,439 | +0.10 quantity rule |
| Weekend settlement dates | 880 | +0.10 settlement-date rule |
| File has 20,000 rows, batch limit is 10,000 | – | Processed as 2 chunks of 10K (FR-TB-007) |
| Settlement dates before "today" (2026-10-10) | many | `IsPastSettlement` +0.15 → see `business_date` config (§6.4) |

### 2.2 Facts learned from the 50K historical data (verified by analysis)

- Fail rate when SSI = NO: **97.3%**; SSI = YES: **16.9%**. Currency NO: 90.1%; YES: 50.6%.
- SSI = YES and Currency = YES → only **9.1%** fail. Quantity < 100 or > 5000 → **100%** fail in history.
- Settlement > 15 days after trade → 87.9% fail; weekend settlement → 69.7% (vs 65.2%).
- Trader name and BUY/SELL have **no** predictive power (~66–68% everywhere).
- Prices are synthetic and roughly uniform (every symbol has mean ≈ 10,000, std ≈ 5,800), so **price z-score carries little signal**. Implement it anyway because it's required (§6.3).
- **Feasibility check (Random Forest, 80/20 split):** AUC 0.961, Accuracy 0.926, Precision 0.971, Recall 0.917, F1 0.943, **FPR 0.055**. All targets are met except FPR (< 5% required), so tune the decision threshold or `class_weight`.
- **Prototype with the full §6.5 feature list:** AUC 0.959, but **FPR got worse (0.072 at threshold 0.5, 0.051 at 0.775)**. The extra target-encoded fail-rate features overfit. **FPR is the hardest target.** Why: about 850 of the 16.6K non-failing trades have SSI = NO, and a model that flags every SSI = NO trade already sits at FPR ≈ 5%. The ML module must experiment (see MODULE_ML.md).
- **Prototype scoring of the 20K file** (business date 2026-09-28): PASS 9,144 · MONITOR 5,334 · EXCEPTION 4,818 · REJECTED 704 (R-007 577, R-005 130, R-001 1, R-002 1). **Every one of the 19,296 accepted trades has score > 0**, so every accepted trade gets a repair-queue item (see Q2 in §16).
- **Prototype PSI (training vs 20K):** RED for Counterparty_Tier, Price_ZScore, Days_To_Settlement, Is_Weekend_Settlement and Rule_Engine_Output; AMBER for Quantity; GREEN for SSI and Currency. The drift dashboard has real data to show.

### 2.3 Counterparty Risk Tiers (computed from historical failure counts — seed these)

Rules from the decision-logic doc: `> 1500 → Tier4_HighestRisk (+0.25)`, `1000–1500 → Tier3_ElevatedRisk (+0.20)`, `500–999 → Tier2_MediumTrust (+0.10)`, `< 500 → Tier1_HighTrust (+0.0)`.

| Tier | Counterparties (historical fail count) |
|---|---|
| **Tier 4** | BlackRock 2049, Raymond James 1888, PJT Partners 1866, Deutsche Bank 1823, Credit Suisse 1687 |
| **Tier 3** | Northern Trust 1496, Charles Schwab 1411, Capital Group 1314, Morgan Stanley 1257, Tokyo Marine 1248, Bank of America 1220, Barclays Wealth 1133, Commerzbank 1123, Vanguard 1005 |
| **Tier 2** | State Street 984, Moelis 940, Evercore 915, Lazard 846, Nomura 831, E*TRADE 808, Goldman Sachs 744, Fidelity 684, BofA Securities 600, Santander 596, Interactive Brokers 548, HSBC 501 |
| **Tier 1** | Jefferies 485, JPMorgan Chase 475, Standard Chartered 452, Societe Generale 438, Barclays 383, UBS 355, Centerview 313, Citigroup 303, Goldman Sachs Asset Management 207, Mizuho 174, ING Group 161, Royal Bank of Scotland 116 |

The seed script must **compute** these from `historical_trades`, not hard-code them, and recompute them on every retrain.

---

## 3. Scope & Priorities

### 3.1 Tiers from the brief

| Tier | Items | Our priority |
|---|---|---|
| **Tier 1 Must** | B1 Backend, B2 Frontend, B3 AI/ML service, B4 Trade Booking Form, B5 Trade Dashboard | P0 |
| **Tier 2 Must** | I1 Business rules, I2 ML model, I3 SHAP, I4 Hybrid scoring, I5 Repair Queue, I6 Roles & access, I7 Deployment | P0/P1 |
| **Tier 3 Good-to-have** | A1 Audit trail (**we treat it as P1**, because FR-AT is also in "Functional Requirements Coverage"), A2 LLM chatbot, A3 Model monitoring dashboard, A4 Model management API | P1/P2 |
| Optional | Vector DB, streamer, compliance report | P3 |

### 3.2 Definition of Done (checked by mentors)
- REST APIs for submission, validation, repair queue and audit; **Swagger/OpenAPI** (FastAPI gives `/docs` for free).
- DB persistence for trades, exceptions, repair queue and audit logs.
- Frontend: booking form with **real-time inline validation**; dashboard with filters (status, priority, date range); repair queue with priority sort, **bulk ops**, fix application; **CSV export**; audit timeline viewer.
- Integrated end-to-end and **deployed** (open-source/free platform).
- **Automated unit + integration tests** (positive and negative scenarios).
- Deliverables: **Technical design doc**, interactive visualization, rule engine with scoring, chatbot (good-to-have), streamer (optional), **automated test case generation**.

### 3.3 Out of scope (guardrails — DO NOT build)
Prometheus/Grafana/ELK, Kubernetes, **Redis**, **Kafka**. (Use FastAPI `BackgroundTasks` instead of queues.)

### 3.4 Engineering checklist (judged at evaluation)
Git with meaningful commits · **pinned dependency versions** · **no hard-coded secrets** (`.env` + `.env.example`) · modular code · graceful error handling · logging · positive and negative tests · README (setup, run, architecture, assumptions) · clean working build in the demo.

---

## 4. Architecture

```
┌──────────────────────────┐      HTTPS/JSON (JWT)      ┌──────────────────────────────────────────┐
│  Frontend (React + AntD) │ ─────────────────────────▶ │  Backend (FastAPI, Python 3.11)          │
│  Vercel                  │ ◀───────────────────────── │  Render web service                      │
└──────────────────────────┘                            │  ├─ api/v1 routers (auth, trades, …)     │
                                                        │  ├─ engine/  ◀── ML MODULE (pure Python) │
                                                        │  │   normalize · hard gates · rules ·    │
                                                        │  │   features · RF model · SHAP ·        │
                                                        │  │   hybrid · explain · PSI · train      │
                                                        │  ├─ services/trade, upload (call engine) │
                                                        │  ├─ services/repair_queue, audit, export │
                                                        │  ├─ services/reports (PDF), email outbox │
                                                        │  └─ chat/ (LLM tool-calling + fallback)  │
                                                        └───────────────┬──────────────────────────┘
                                                                        │ SQLAlchemy
                                                         ┌──────────────▼──────────────┐
                                                         │ PostgreSQL 16 (Render)      │
                                                         │ (+ pgvector optional)       │
                                                         └─────────────────────────────┘
        ml_artifacts/model_vN.joblib  ← loaded into memory at startup (production model)
        LLM provider (OpenAI-compatible: OpenAI / Groq / Gemini) ← only for chatbot, key in .env
```

**Why Python for the backend (not Express)?** SHAP, scikit-learn and pandas are Python-only. One Python service means the model runs **in-process**, so there's no second service to deploy and no network hop. FastAPI also generates Swagger automatically.

### 4.1 Tech stack (pin these versions in requirements.txt / package.json)

**Backend:** python 3.11 · fastapi · uvicorn[standard] · sqlalchemy 2.x · alembic · psycopg[binary] 3.x · pydantic 2.x · pydantic-settings · python-jose[cryptography] (or PyJWT) · passlib[bcrypt] · python-multipart · pandas · numpy · scikit-learn · shap · joblib · holidays (optional) · reportlab (PDF) · openai (OpenAI-compatible client for the chatbot) · pytest · pytest-cov · httpx · faker (test generation).
Pin exact versions with `pip freeze > requirements.txt` once everything works.

**Frontend:** react 18 · vite · typescript · antd 5 · @ant-design/icons · react-router-dom 6 · @tanstack/react-query 5 · axios · dayjs · recharts · zod (form validation schemas) · vitest + @testing-library/react · msw (mock API during parallel development) · playwright (optional e2e).

**Infra:** Docker + docker-compose (postgres + backend + frontend) for local; Render (backend + Postgres), Vercel/Netlify (frontend) for deployment; GitHub for version control.

---

## 5. Roles & Access Control (RBAC)

### 5.1 Roles (seed exactly these ids)

| id | code | Name | Main screens |
|---|---|---|---|
| 101 | `TRADER` | Trader | Trade Booking Form, Bulk Upload, My Trades, Trade Details |
| 102 | `OPS_USER` | Operations User | Trade Dashboard, Repair Queue (own assigned items), Audit (assigned) |
| 103 | `OPS_MANAGER` | Operations Manager | Trade Dashboard, Repair Queue (all + assignment), Team Performance, Reports, Audit (all) |
| 104 | `ML_ADMIN` | ML Model Administrator | Model Monitoring, Model Registry/Retrain/Promote, Hybrid weight config |
| 105 | `SYS_ADMIN` | System Administrator / APS | Everything + User Management + System/Rule config |

### 5.2 Permission codes (from `UserRoles_Matrix.csv`) — T=Trader, OU=Ops User, OM=Ops Mgr, ML=ML Admin, SA=Sys Admin

| Code | Matrix text | T | OU | OM | ML | SA |
|---|---|---|---|---|---|---|
| `TRADE_SUBMIT_SINGLE` | Submit Individual Trades | ✅ | | | | ✅ |
| `TRADE_SUBMIT_BULK` | Submit Bulk Trades | ✅ | | | | ✅ |
| `TRADE_VIEW` | View Trade Details | ✅ (own) | ✅ | ✅ | | ✅ |
| `RISK_SCORE_VIEW` | View AI Risk Score | ✅ | ✅* | ✅* | | ✅ |
| `EXCEPTION_DASHBOARD_VIEW` | View Exception Dashboard | | ✅ | ✅ | | ✅ |
| `REPAIR_QUEUE_MANAGE` | Repair Queue Management | | ✅ | ✅ | | ✅ |
| `FIX_APPLY` | Apply/Modify Fixes | | ✅ | ✅ | | ✅ |
| `QUEUE_BULK_OPS` | Bulk Queue Operations | | ✅ | ✅ | | ✅ |
| `QUEUE_ASSIGN` | Assign Trades to Users | | ✅** | ✅ | | ✅ |
| `QUEUE_REASSIGN` | Reassign Trades Between Users | | | ✅ | | ✅ |
| `HIGH_RESOLUTION_APPROVE` | Approve High-Priority Resolutions | | | ✅ | | ✅ |
| `TEAM_METRICS_VIEW` | View Team Performance Metrics | | | ✅ | | ✅ |
| `COMPLIANCE_REPORT` | Generate Compliance Reports | | | ✅ | | ✅ |
| `MODEL_METRICS_VIEW` | View Model Performance Metrics | | | | ✅ | ✅ |
| `DRIFT_MONITOR` | Monitor Data Drift (PSI) | | | | ✅ | ✅ |
| `FEATURE_IMPORTANCE_VIEW` | View Feature Importance | | | | ✅ | ✅ |
| `MODEL_RETRAIN` | Trigger Model Retraining | | | | ✅ | ✅ |
| `MODEL_PROMOTE` | Promote Model Versions | | | | ✅ | ✅ |
| `HYBRID_WEIGHTS_CONFIG` | Configure Alpha/Beta Weights | | | | ✅ | ✅ |
| `BUSINESS_RULES_CONFIG` | Configure Business Rules | | | | | ✅ |
| `SYSTEM_CONFIG` | Configure System Parameters | | | | | ✅ |
| `USER_MANAGE` | Manage User Accounts | | | | | ✅ |
| `ROLE_ASSIGN` | Role Assignment | | | | | ✅ |
| `API_KEYS_MANAGE` | Manage API Keys | | | | | ✅ |
| `MONITORING_ALERTS_CONFIG` | Configure Monitoring Alerts | | | | | ✅ |
| `AUDIT_VIEW` | View Complete Audit Trail | | ✅ scope=ASSIGNED | ✅ scope=ALL | | ✅ ALL |
| `TRADE_AMEND` | *(ours)* Update existing trade / Apply Fix target | ✅ (own) | ✅ | ✅ | | ✅ |
| `CHATBOT_USE` | *(ours)* Use chatbot | ✅ | ✅ | ✅ | ✅ | ✅ |

\* **Deviation:** the matrix says Ops can't view the AI risk score, but FR-RQ-008 requires the Ops dashboard to show it. We grant it to Ops (listed in §16 as a question for mentors).
\** **Interpretation:** an Ops User may *pick up* (self-assign) unassigned items. Only the Ops Manager can assign items to **other** users or reassign (FR-RQ-005, FR-RQ-013).

### 5.3 How auth works (both sides must follow)
- `POST /api/v1/auth/login` with username + password returns a JWT (8 h expiry) plus the user object with a `permissions[]` array.
- Backend: every protected route uses a FastAPI dependency `require_permission("CODE")`. Row-level scoping (Trader sees own trades; Ops User sees own assigned queue items) happens **in the service layer**, not the UI.
- Frontend: stores the token in memory plus `sessionStorage`, sends `Authorization: Bearer <token>`, hides menu items/buttons using `permissions[]`, and redirects to `/login` on 401. **UI hiding is cosmetic. The backend is the real gate.**
- Passwords are bcrypt-hashed. Seed users get their password from the env var `SEED_DEFAULT_PASSWORD` (never hard-coded).
- **Trader Name (FR-TB-004):** for UI bookings the backend sets `trader_name = current_user.full_name` and **ignores** any value sent by the client.

### 5.4 Seed users
- 15 **Traders**, one per name in the data: Aisha Khan, Carlos Rodriguez, David Wilson, Emma Davis, Fatima Hassan, James Anderson, John Miller, Laura Martinez, Michael Brown, Nina Petrova, Priya Sharma, Robert Taylor, Sarah Chen, Tom Richardson, Wei Zhang. Username = `firstname.lastname` (lowercase).
- 3 Ops Users: `ops.user1`, `ops.user2`, `ops.user3` · 1 Ops Manager: `ops.manager` · 1 ML Admin: `ml.admin` · 1 Sys Admin: `sys.admin`.
- Emails: `<username>@tradeperfect.local` (configurable).

---

## 6. Validation & Scoring Logic (THE CORE — implement exactly)

### 6.1 Pipeline (per trade)

```
input ──▶ normalize (trim, case, parse dates/bools/numbers)
      ──▶ HARD GATES R-000..R-008  ── any fail ──▶ status=REJECTED, final_score=1.0, no queue item,
      │                                            rejection_reasons[], add to rejection report (email)
      ▼ all pass
      ──▶ RULE ENGINE (weighted)          → rule_score ∈ [0,1] + per-rule contributions
      ──▶ FEATURE BUILD + ML (RandomForest) → ml_score = P(fail) ∈ [0,1]  (Rule_Score is a feature!)
      ──▶ HYBRID: final = α·ml_score + β·rule_score   (α=0.3, β=0.7 from config)
      ──▶ CLASSIFY: final < 0.5 PASS | 0.5 ≤ final < 0.7 MONITOR | final ≥ 0.7 EXCEPTION
      ──▶ PRIORITY: EXCEPTION→HIGH, MONITOR→MEDIUM, PASS→LOW
      ──▶ EXPLAIN: risk_factors %, SHAP, likely_reason, suggested_action, explanation text
      ──▶ PERSIST: trades + trade_versions + rule_evaluations + ml_predictions + audit_logs
      ──▶ ROUTE: if final_score > 0.0 (and not REJECTED) → create/update repair_queue item
```

Round all scores to **4 decimals** and clamp to [0, 1].

### 6.2 Hard gates (deterministic → REJECT, score = 1.0)

| Rule | Condition (reject if…) | Message example |
|---|---|---|
| R-000 *(ours, FR-TB-003)* | Any mandatory field missing/blank: tradeId (file only), tradeType, symbol, quantity, price, counterparty, tradeDate, settlementDate, accountId, traderName, ssiMatched, currencyMatched | `"settlementDate is required"` |
| R-001 | tradeId empty **or duplicate** (within the same file **or** already exists as a non-rejected trade in DB) | `"Duplicate tradeId T110005"` |
| R-002 | tradeType not `BUY`/`SELL` (case-insensitive after trim) | `"tradeType must be BUY or SELL"` |
| R-003 | quantity not a positive integer | `"quantity must be a positive whole number"` |
| R-004 | price not a positive decimal | `"price must be greater than 0"` |
| R-005 | symbol not in Symbol master (exact match after trim + uppercase) | `"Symbol 'ISRG' not found in master data"` |
| R-006 | counterparty not in Counterparty master (exact, case-insensitive) | `"Counterparty 'ABC Bank' not found"` |
| R-007 | accountId not in Account master **or** `is_active = false` | `"Account ACC1011 is inactive"` |
| R-008 *(ours)* | settlementDate earlier than tradeDate, or date not parseable | `"settlementDate cannot be before tradeDate"` |

- Evaluate **all** gates and return **all** failures (don't stop at the first one). Users want every error at once (FR-TB-006).
- **Rejected trades:** stored in `trades` with `status=REJECTED`, `final_score=1.0`, `rejection_reasons`, `raw_payload`. **No queue ID, no navigation to the booking form** (FR-RQ-011). Must be fixed in the upstream system (FR-RQ-012).
- **Rejection report** (decision-logic note): after each single rejection and after each bulk batch, generate a CSV (and/or PDF) of rejected trades and queue an email to `config.rejection_report_recipients` (the trader group). If SMTP isn't configured, save the report and mark the email `LOGGED_ONLY`. It must never crash the flow.
- **Single UI submission with errors:** the API returns **HTTP 422** with field errors and does **not** persist anything, so the user fixes the form inline. **File/bulk/stream:** rejected rows **are persisted** as REJECTED. *(Assumption, listed in §16.)*

### 6.3 Rule Engine (soft rules → Rule_Score)

| Rule ID | Category (max weight) | Condition | Contribution |
|---|---|---|---|
| S-SSI | SSI (0.35) | `ssiMatched = false` | **+0.35** |
| S-CCY | Currency (0.15) | `currencyMatched = false` | **+0.15** |
| S-SD-PAST | Settlement Date (0.15 cap) | `settlementDate < businessDate` (IsPastSettlement / DaysToSettlement < 0; **same condition, count once**) | +0.15 |
| S-SD-WKND | Settlement Date | settlementDate is Sat/Sun **or in `holidays`** (not a valid business day) | +0.10 |
| S-SD-FAR | Settlement Date | DaysToSettlement > 15 | +0.05 |
| S-CP-TIER | Counterparty (0.25) | Tier4 / Tier3 / Tier2 / Tier1 | +0.25 / +0.20 / +0.10 / 0 |
| S-QTY-HIGH | Quantity (0.10) | quantity > 5000 (`config.qty_max`) | +0.10 |
| S-QTY-LOW | Quantity | quantity < 100 (`config.qty_min`) | +0.05 |
| W-PRICE | Price (warning only) | `abs(price_zscore) > 3` vs symbol's historical mean/std | 0.00 (shown in explanation, configurable weight, default 0) |

- **Settlement-date category is capped at 0.15** (sum of its triggered rules, then `min(…, 0.15)`). Max possible rule score = 0.35 + 0.15 + 0.15 + 0.25 + 0.10 = **1.00**.
- `DaysToSettlement` = calendar days between **businessDate** and settlementDate for live trades. For **training rows**, use the trade date as "businessDate" (so past = false). Document this in the ML report.
- All weights and thresholds live in `system_config` (key `rule_weights`), so a Sys Admin can tune them. The defaults above come from the decision-logic doc.
- Every **triggered** rule (soft rules and failed hard gates) is written to `rule_evaluations`. That powers the "Which rule was triggered for a trade?" chatbot answer. (Storing untriggered rules too would mean ~200K extra rows for the 20K file, for no benefit. The API's `ruleHits` for a single trade still lists all rules, because the engine returns them all.)

### 6.4 Business date (important for the demo!)
- `businessDate` = `config.business_date_override` if set, else **today in Asia/Kolkata**.
- The 20K file covers 2026-09-29 → 2026-10-28. If today is mid-October, roughly half the trades are "past settlement" (+0.15). Use the override to show realistic demos (e.g. set it to `2026-09-28`).

### 6.5 ML model (Tier 2 I2)

- **Algorithm:** `RandomForestClassifier` (the reference diagram names it). Suggested starting params: `n_estimators=200, max_depth=12, min_samples_leaf=5, class_weight='balanced', random_state=42, n_jobs=-1`. Keep `joblib` artifact < 50 MB (`compress=3`).
- **Target:** `Settlement Failed` YES→1, NO→0 (≈67% positive, imbalanced *towards failure*).
- **Split:** stratified 80/20, `random_state=42`. Optionally also a **time-based** check (train Jan–Jul, test Aug–Sep).
- **Feature list (exact names; also used in SHAP and PSI charts):**

| Feature | How |
|---|---|
| `SSI_Matched_Flag` | 1/0 |
| `Currency_Matched_Flag` | 1/0 |
| `Counterparty_Tier` | 1–4 |
| `Counterparty_Fail_Rate` | historical fail rate of counterparty (**train split only**, smoothed) |
| `Symbol_Fail_Rate` | historical fail rate of symbol (train split only, smoothed) |
| `CP_Symbol_Fail_Rate` | fail rate of (counterparty, symbol) pair, smoothed toward the global mean (min 20 samples) → "counterparty-symbol interaction risk" |
| `Quantity` | raw |
| `Quantity_ZScore` | vs symbol mean/std → "quantity pattern anomalies" |
| `Price` | raw |
| `Price_ZScore` | vs symbol mean/std → "price reasonableness" |
| `Days_To_Settlement` | settlementDate − **tradeDate**, the **same in training and live**. (Being past the business date is handled only by rule S-SD-PAST; mixing the two definitions creates fake drift, which the prototype showed.) |
| `Is_Weekend_Settlement` | 1/0 |
| `Is_Holiday_Settlement` | 1/0 |
| `Settlement_DayOfWeek` | 0–6 → "temporal patterns" |
| `Is_Month_End` | settlement in last 3 days of month |
| `Trade_Type_Buy` | 1/0 |
| `Rule_Engine_Output` | the rule score (diagram: "Input: All features + Rule_Score") |

  *Leakage warning:* fail-rate encodings must be computed from the **training** rows only, then saved inside the artifact (a dict) and reused at inference. Never compute them from the test set.
- **Metric targets** (must display pass/fail): **AUC-ROC ≥ 0.90, Recall ≥ 0.85, Precision ≥ 0.70, F1 ≥ 0.77, Accuracy > 0.90, FPR < 0.05**. Tune `decision_threshold` (stored in `model_versions`) to satisfy FPR while keeping recall ≥ 0.85. Note: the threshold only affects **metrics**. The hybrid score uses the raw probability.
- **Artifact contents** (one `joblib` dict): `{model, feature_list, encoders(fail-rate dicts, symbol stats), decision_threshold, version, trained_at}`.
- **Registry:** each training run creates a `model_versions` row (status `CANDIDATE`) + `model_evaluations` row + `retraining_events` row. ML Admin **promotes** one to `PRODUCTION` (only one at a time, enforced by a DB unique index). The backend keeps the production model **in memory** and reloads it on promote.
- **Bootstrapping:** `python -m app.engine.cli train … --version v1` trains v1, then `scripts/register_model.py` registers and promotes it automatically (trigger_reason `INITIAL`). The app must still start if no model exists. In that case `ml_score = null`, final = rule_score, and the UI shows a "model unavailable" badge.
- **Trend chart data (FR-MM-003):** to make the chart meaningful in a demo, evaluate v1 on each historical month separately and store each as a `model_evaluations` row (dataset_label `month_2026-01`, …).

### 6.6 Explainability (I3 + FR-RQ-008 columns)

For every scored trade produce:

1. **`shap_values`**: per-feature SHAP for class 1 (`shap.TreeExplainer`, computed once per batch, vectorised). Store in `ml_predictions.shap_values`.
   **Speed (measured):** exact Tree SHAP on a 200-tree/depth-12 forest takes **~16 s per 1,000 rows** (≈ 3 min per 10K batch, too slow). `approximate=True` takes **0.05 s per 1,000 rows**. Rule: **batches > 100 rows use `approximate=True`**. Single trades (form, amend) and the global SHAP summary use exact SHAP.
2. **`risk_factors`** (the "Risk Factors %" column): one unified taxonomy so rules and ML read the same way:

   `factor groups = SSI, CURRENCY, SETTLEMENT_DATE, COUNTERPARTY, QUANTITY, PRICE, PATTERN(other ML)`

   - Rule part: `β × contribution` of each triggered rule, mapped to its group.
   - ML part: `α × ml_score` split across groups in proportion to **positive** SHAP values (feature→group map: SSI_Matched_Flag→SSI, Currency_Matched_Flag→CURRENCY, Days_To_Settlement/Is_Weekend/Is_Holiday/DayOfWeek/Is_Month_End→SETTLEMENT_DATE, Counterparty_Tier/Counterparty_Fail_Rate/CP_Symbol_Fail_Rate→COUNTERPARTY, Quantity/Quantity_ZScore→QUANTITY, Price/Price_ZScore→PRICE, Symbol_Fail_Rate/Trade_Type_Buy→PATTERN, Rule_Engine_Output→split across rule groups in the same ratio as the rule contributions).
   - `pct = 100 × group_total / Σ group_totals` (sum = 100). Drop groups < 1%. Sort descending.
   - Shape: `[{"factor":"SSI","pct":61.2,"contribution":0.31,"detail":"SSI not matched with counterparty"}, …]`
3. **`likely_reason`**: the `detail` text of the top factor (e.g. "SSI not matched with counterparty").
4. **`suggested_action`**: from the fix catalogue below, for the top 1–2 factors.
5. **`explanation`**: one template sentence (no LLM needed):
   `"Trade T110003 (SELL 5,291 QCOM with Vanguard) has a 0.82 risk of settlement failure → EXCEPTION. Main drivers: SSI not matched (58%), Counterparty Vanguard is Tier 3 elevated risk (24%), Currency mismatch (12%). Suggested: re-confirm SSI with Vanguard and correct currency before settlement on 2026-10-14."`
   (The chatbot can optionally rephrase it with the LLM.)

**Fix catalogue** (`services/scoring/fix_suggestions.py`):

| Factor | Suggested action |
|---|---|
| SSI | Re-confirm Standard Settlement Instructions with the counterparty; update SSI in the booking and set SSI Matched = YES once confirmed. |
| CURRENCY | Align settlement currency with the counterparty's instruction / book FX; set Currency Matched = YES after confirmation. |
| SETTLEMENT_DATE (past) | Settlement date is in the past — amend to the next valid business day (T+1/T+2). |
| SETTLEMENT_DATE (weekend/holiday) | Move settlement date to the next valid business day: `<computed date>`. |
| SETTLEMENT_DATE (far) | Settlement > 15 days out — verify instruction is not stale with trader/counterparty. |
| COUNTERPARTY | Tier 3/4 counterparty — pre-match trade details with the counterparty and escalate to Ops Manager for monitoring. |
| QUANTITY (high) | Quantity exceeds limit 5,000 — verify with trader for fat-finger error or split the order. |
| QUANTITY (low) | Quantity below minimum lot 100 — verify order size with trader. |
| PRICE | Price deviates strongly from historical range for the symbol — verify price with market data/trader. |
| PATTERN | Historical pattern risk — review similar past failures for this counterparty/symbol. |

### 6.7 Classification, priority & routing summary

| Final score | Status | Priority | Repair queue | Queue initial status | Re-Submit allowed? |
|---|---|---|---|---|---|
| — (hard gate fail) | REJECTED | — | **No** | — | No (fix upstream) |
| = 0.0 | PASS | LOW | **No** (rule: only score > 0) | — | — |
| 0 < s < 0.5 | PASS | LOW | Yes | OPEN | ✅ (bulk allowed) |
| 0.5 ≤ s < 0.7 | MONITOR | MEDIUM | Yes | OPEN | ✅ |
| ≥ 0.7 | EXCEPTION | HIGH | Yes | OPEN | ❌ disabled → must **Apply Fix** or **Reject** |

---

## 7. Repair Queue & Trade Lifecycle

### 7.1 Queue status machine (FR-RQ-014)

```
            create (score>0)
                 │
                 ▼
   ┌────────── OPEN ──────── assign / self-pick ──────▶ IN_PROGRESS
   │             ▲                                          │
   │             └─────── unassign (Mgr) ───────────────────┘
   │                                                        │
   │  Re-Submit (MEDIUM/LOW only)                           │ Apply Fix → amend trade → re-score
   ├──────────────────────────────────────▶ RESOLVED ◀──────┤   • new score = 0      → RESOLVED
   │                                          ▲             │   • new score < 0.7    → priority drops, Re-Submit enabled
   │  Mgr approves HIGH resolution ───────────┘             │   • still ≥ 0.7        → stays HIGH
   │                                                        │
   └── Reject (HIGH, findings not addressed / cpty cancels) ▶ REJECTED (trade.status = REJECTED)
```

- **Assignment** (FR-RQ-005/013): the Ops Manager assigns one or many items to any Ops User at any time. Assigning sets `IN_PROGRESS`; unassigning returns to `OPEN`. An Ops User can only self-assign `OPEN` unassigned items.
- **Visibility** (FR-RQ-012): Ops Manager/Sys Admin see all items. An Ops User sees only items `assigned_to = me` (plus unassigned OPEN items, so they can pick them up — *interpretation*).
- **Apply Fix** (FR-RQ-006): navigates to `/trades/{tradeId}?mode=edit&queueId=RQ-…`. The Update Trade action creates a new `trade_versions` row, re-runs the full pipeline, updates the queue item's priority/score and sets `applied_fix/applied_by/applied_at`, all audited.
- **Re-Submit** (FR-RQ-007/009/011): allowed only when priority ∈ {MEDIUM, LOW}. It re-runs scoring for the record, marks the queue item **RESOLVED** and sets `trade.is_resolved = true` (covers the false-positive case). Bulk Re-Submit only accepts LOW items (FR-RQ-010). The API skips/returns errors for items that aren't eligible.
- **Reject** (FR-RQ-008): HIGH items only, a reason is required, and it sets queue `REJECTED` and trade `REJECTED`.
- **Top bar** (FR-RQ-001): total items in the queue, plus counts by status and priority. Filters: Status (default All), Priority (default All). Buttons: **Bulk Assignment, Bulk Re-Submit, Export CSV**.
- **Sorting**: default priority HIGH → MEDIUM → LOW, then score descending.

### 7.2 Trade booking form behaviour (FR-TB)
- **New trade** (`/trades/new`): `tradeId` field is read-only and shows "Auto-generated" (server assigns `T200001…` from `trade_id_seq`, FR-TB-008). `traderName` is read-only = logged-in user. The **Submit Trade** button is enabled (FR-TB-005).
- **Existing trade** (`/trades/{tradeId}`): fields are pre-filled, and the **Update Trade** button replaces Submit. History tab (versions) and Audit box (FR-AT-007) are shown.
- **Real-time validation:** client-side zod rules on every change (types, required, BUY/SELL, positive numbers, settlement ≥ trade date), plus a debounced server call `POST /trades/validate` (master-data checks: symbol, counterparty, account active). Show **inline errors under each field**.
- Dropdowns load from the reference APIs (symbols, counterparties, active accounts). Show a weekend/holiday warning next to the settlement date.
- After submit, show a **result card**: status tag, final/rule/ML scores, risk factor bar, likely reason, suggested action, explanation, queue ID (link).

### 7.3 Bulk upload (FR-TB-002/007/009)
- Accepts `.csv` (required) and `.xlsx` (nice-to-have). Header matching is tolerant: dataset headers (`Trade ID`) **and** camelCase (`tradeId`) both work. Strip the BOM, trim cells, parse dates in `M/D/YYYY` **and** `YYYY-MM-DD`, and accept booleans `YES/NO/Y/N/TRUE/FALSE/1/0`.
- Processed as a **background task** in **chunks of 10,000** (`config.batch_size`). The response returns `batchId` immediately, and the UI polls `GET /uploads/{batchId}` for progress. Max file rows = `config.max_upload_rows` (default 100,000).
- Duplicate trade IDs within the file: the **first occurrence wins**, and later ones are REJECTED (R-001). An ID that already exists in the DB is REJECTED (R-001).
- **Performance:** vectorised pandas for rules/features, one `predict_proba` call per chunk, one SHAP call per chunk, and SQLAlchemy bulk inserts. **Never** call the single-trade endpoint 20,000 times. Target: 10K rows in < 60 s.
- Ends with a summary (total / accepted / PASS / MONITOR / EXCEPTION / REJECTED) plus a downloadable rejection report.

---

## 8. Contracts (FE↔BE: REST §8.1–8.2 · BE↔ML: Python §8.3)

**Conventions**
- Base URL: `/api/v1`. JSON in **camelCase** (Pydantic `alias_generator=to_camel`, `populate_by_name=True`). DB and Python use snake_case.
- Dates: `YYYY-MM-DD`. Timestamps: ISO-8601 UTC (`2026-10-10T08:15:07Z`). The frontend displays them in IST.
- Pagination: query `page` (1-based), `pageSize` (default **50**, max 500). Response: `{ "items": [...], "total": 1234, "page": 1, "pageSize": 50 }`.
- Sorting: `sort=finalScore:desc` (comma-separated for multiple).
- Errors (always this shape):
  ```json
  { "error": { "code": "VALIDATION_ERROR", "message": "Trade validation failed",
               "fields": [ { "field": "symbol", "ruleId": "R-005", "message": "Symbol 'ISRG' not found in master data" } ] } }
  ```
  Codes: `VALIDATION_ERROR` (422), `UNAUTHORIZED` (401), `FORBIDDEN` (403), `NOT_FOUND` (404), `CONFLICT` (409), `BAD_REQUEST` (400), `INTERNAL_ERROR` (500).
- CSV exports: `text/csv` with `Content-Disposition: attachment; filename=trades_20261010.csv`. They respect the same filters as the list endpoint (FR-RQ-006: "displayed trades").

### 8.1 Endpoints

| # | Method & Path | Permission | Purpose / FR |
|---|---|---|---|
| **Auth** |||
| 1 | `POST /auth/login` | public | `{username,password}` → `{accessToken, tokenType, expiresIn, user: User}` |
| 2 | `GET /auth/me` | any | current `User` |
| 3 | `POST /auth/logout` | any | audit log only |
| **Reference** |||
| 4 | `GET /reference/symbols` | any | `[{symbol}]` |
| 5 | `GET /reference/counterparties` | any | `[{name, riskTier, tierLabel}]` |
| 6 | `GET /reference/accounts?active=true` | any | `[{accountId, isActive}]` |
| 7 | `GET /reference/users?role=OPS_USER` | QUEUE_ASSIGN or AUDIT_VIEW | assignee/user dropdowns |
| **Trades** |||
| 8 | `POST /trades/validate` | TRADE_SUBMIT_SINGLE or TRADE_AMEND | dry-run validation → `{valid, fields:[…], warnings:[…]}` (no DB write) |
| 9 | `POST /trades` | TRADE_SUBMIT_SINGLE | book one trade → `TradeResult` (201) / 422 |
| 10 | `PUT /trades/{tradeId}` | TRADE_AMEND | amend → new version, re-score → `TradeResult`; body may include `queueId`, `appliedFix` |
| 11 | `GET /trades` | TRADE_VIEW | list. Filters: `status, priority, tradeDateFrom, tradeDateTo, settlementDateFrom, settlementDateTo, search (tradeId/symbol), traderName, counterparty, batchId, mine=true` |
| 12 | `GET /trades/{tradeId}` | TRADE_VIEW | `TradeDetail` (TradeResult + versions count + latest audit box) |
| 13 | `GET /trades/{tradeId}/history` | TRADE_VIEW | `[TradeVersion]` (FR-TB-011) |
| 14 | `GET /trades/export` | TRADE_VIEW | CSV of filtered list |
| 15 | `POST /trades/bulk` | TRADE_SUBMIT_BULK | multipart `file` → `202 {batchId}` |
| 16 | `GET /uploads/{batchId}` | TRADE_SUBMIT_BULK | `UploadBatch` progress + summary |
| 17 | `GET /uploads/{batchId}/rejections` | TRADE_SUBMIT_BULK | CSV of rejected rows |
| **Dashboard** |||
| 18 | `GET /dashboard/summary?date=YYYY-MM-DD` | EXCEPTION_DASHBOARD_VIEW or TRADE_VIEW | `{today:{total,pass,monitor,exception,rejected, pct:{…}}, historical:{…same}}` (FR-RQ-002/003) |
| 19 | `GET /dashboard/charts` | same | `{byStatusOverTime:[{date,pass,monitor,exception,rejected}], byCounterparty:[…top10], bySymbol:[…], riskScoreHistogram:[…]}` |
| **Repair Queue** |||
| 20 | `GET /repair-queue` | REPAIR_QUEUE_MANAGE | list `QueueItem`. Filters `status, priority, assignedTo, search`. Ops User auto-scoped |
| 21 | `GET /repair-queue/summary` | REPAIR_QUEUE_MANAGE | `{total, byStatus:{…}, byPriority:{…}}` |
| 22 | `GET /repair-queue/{queueId}` | REPAIR_QUEUE_MANAGE | `QueueItem` + trade + fixes |
| 23 | `POST /repair-queue/assign` | QUEUE_ASSIGN (others' → QUEUE_REASSIGN) | `{queueIds:[…], assigneeId}` (null = unassign) → `BulkResult` |
| 24 | `POST /repair-queue/{queueId}/resubmit` | FIX_APPLY | → `QueueItem` (409 if HIGH) |
| 25 | `POST /repair-queue/bulk-resubmit` | QUEUE_BULK_OPS | `{queueIds}` LOW only → `BulkResult` |
| 26 | `POST /repair-queue/{queueId}/reject` | FIX_APPLY | `{reason}` HIGH only |
| 27 | `POST /repair-queue/{queueId}/approve` | HIGH_RESOLUTION_APPROVE | Mgr resolves HIGH item `{note}` |
| 28 | `GET /repair-queue/export` | REPAIR_QUEUE_MANAGE | CSV (FR-RQ-015) |
| 29 | `GET /team/performance` | TEAM_METRICS_VIEW | per Ops user: assigned, inProgress, resolved, avgResolutionHours |
| **Audit** |||
| 30 | `GET /audit` | AUDIT_VIEW | filters `tradeId, dateFrom, dateTo, dateField=tradeDate|settlementDate|eventTime, userId, action` (FR-AT-002) |
| 31 | `GET /audit/trades/{tradeId}/timeline` | AUDIT_VIEW | ordered `[AuditEvent]` (FR-AT-001) |
| 32 | `GET /audit/trades/{tradeId}/latest` | AUDIT_VIEW or TRADE_VIEW | `AuditBox` (FR-AT-007) |
| 33 | `GET /audit/export` | AUDIT_VIEW | CSV: Trade Id, Queue ID, Risk Score, Likely Reason, Recommended Fix, Applied Fix, Applied By, Applied Timestamp, Status (FR-AT-006) |
| 34 | `POST /audit/trades/{tradeId}/report` | AUDIT_VIEW | `{emailTo?:[…]}` → generates PDF, queues email → `{reportUrl, emailStatus}` |
| 35 | `GET /audit/reports/{fileName}` | AUDIT_VIEW | download PDF |
| 36 | `GET /audit/verify` | SYS_ADMIN | re-computes hash chain → `{valid, brokenAtId}` (proves immutability in demo) |
| **ML / Model management (A3, A4)** |||
| 37 | `GET /ml/models` | MODEL_METRICS_VIEW | `[ModelVersion]` |
| 38 | `GET /ml/models/production` | MODEL_METRICS_VIEW | current `ModelVersion` with metrics + targets + pass/fail |
| 39 | `GET /ml/metrics/history?from&to&modelVersion` | MODEL_METRICS_VIEW | `[ModelEvaluation]` (FR-MM-003/004) |
| 40 | `POST /ml/retrain` | MODEL_RETRAIN | `{notes?}` → `202 {retrainingEventId}` |
| 41 | `GET /ml/retrain/{id}` | MODEL_RETRAIN | status + log |
| 42 | `POST /ml/models/{version}/promote` | MODEL_PROMOTE | hot-reloads model |
| 43 | `GET /ml/drift/latest` | DRIFT_MONITOR | `DriftCheck` (FR-MM-006/007/008) |
| 44 | `POST /ml/drift/run` | DRIFT_MONITOR | "Run Drift Check Now" → `DriftCheck`; creates notification if any PSI > threshold (FR-MM-009) |
| 45 | `GET /ml/feature-importance` | FEATURE_IMPORTANCE_VIEW | `[{feature, meanAbsShap}]` (FR-MM-010) |
| 46 | `GET /ml/config` · `PUT /ml/config` | HYBRID_WEIGHTS_CONFIG | `{alpha, beta, passThreshold:0.5, exceptionThreshold:0.7, psiThreshold:0.2}`; validate α+β = 1 |
| 47 | `POST /ml/predict` | MODEL_METRICS_VIEW | score a trade payload without saving (for testing) |
| **Admin** |||
| 48 | `GET/POST/PUT /admin/users` | USER_MANAGE | CRUD (+ role assign → ROLE_ASSIGN) |
| 49 | `GET/PUT /admin/config/{key}` | SYSTEM_CONFIG / BUSINESS_RULES_CONFIG | rule weights, thresholds, business date, emails, batch size |
| 50 | `GET/POST /admin/holidays` | SYSTEM_CONFIG | manage holiday calendar |
| **Notifications & Chat** |||
| 51 | `GET /notifications` · `POST /notifications/{id}/read` | any | bell icon (own + role) |
| 52 | `POST /chat` | CHATBOT_USE | `{sessionId, message}` → `{answer, toolUsed, data?}` |
| **Ops** |||
| 53 | `GET /health` | public | `{status:"ok", db:"ok", model:"v1"}` |

### 8.2 Shared JSON shapes (TypeScript notation — FE copies into `frontend/src/types/api.ts`; BE Pydantic schemas mirror them)

```ts
type TradeStatus = 'PASS' | 'MONITOR' | 'EXCEPTION' | 'REJECTED';
type Priority = 'HIGH' | 'MEDIUM' | 'LOW';
type QueueStatus = 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'REJECTED';
type RoleCode = 'TRADER' | 'OPS_USER' | 'OPS_MANAGER' | 'ML_ADMIN' | 'SYS_ADMIN';

interface User { id: number; username: string; fullName: string; email: string;
  role: RoleCode; roleName: string; permissions: string[]; }

interface TradeInput {               // POST /trades, PUT /trades/{id}, POST /trades/validate
  tradeId?: string;                  // omit for new UI trade (server generates)
  tradeType: 'BUY' | 'SELL'; symbol: string; quantity: number; price: number;
  counterparty: string; tradeDate: string; settlementDate: string; accountId: string;
  ssiMatched: boolean; currencyMatched: boolean;
  queueId?: string; appliedFix?: string; changeReason?: string;   // PUT only
}

interface RiskFactor { factor: 'SSI'|'CURRENCY'|'SETTLEMENT_DATE'|'COUNTERPARTY'|'QUANTITY'|'PRICE'|'PATTERN';
  pct: number; contribution: number; detail: string; }

interface RuleHit { ruleId: string; category: string; triggered: boolean; contribution: number; message: string; }

interface TradeResult extends Omit<TradeInput, 'queueId'|'appliedFix'|'changeReason'> {
  id: string;                        // internal uuid
  tradeId: string | null;
  traderName: string;
  source: 'UI' | 'FILE' | 'STREAM' | 'API';
  status: TradeStatus; priority: Priority | null;
  ruleScore: number | null; mlScore: number | null; finalScore: number;
  riskFactors: RiskFactor[]; likelyReason: string | null; suggestedAction: string | null;
  explanation: string | null; rejectionReasons: { ruleId: string; field?: string; message: string }[] | null;
  ruleHits?: RuleHit[]; shapValues?: Record<string, number>;
  queueId: string | null; queueStatus: QueueStatus | null; assignedTo: string | null;
  isResolved: boolean; version: number; modelVersion: string | null;
  createdAt: string; updatedAt: string; createdBy: string;
}

interface QueueItem { queueId: string; tradeId: string; trade: TradeResult;
  priority: Priority; status: QueueStatus; riskScore: number;
  suggestedFixes: { factor: string; fix: string }[];
  assignedTo: { id: number; fullName: string } | null; assignedAt: string | null;
  appliedFix: string | null; appliedBy: string | null; appliedAt: string | null;
  canResubmit: boolean; canReject: boolean; createdAt: string; updatedAt: string; }

interface BulkResult { succeeded: string[]; failed: { id: string; reason: string }[]; }

interface UploadBatch { batchId: string; fileName: string; status: 'PENDING'|'PROCESSING'|'COMPLETED'|'FAILED';
  totalRows: number; processedRows: number;
  summary: { accepted: number; pass: number; monitor: number; exception: number; rejected: number };
  startedAt: string; completedAt: string | null; errorMessage: string | null; }

interface TradeVersion { version: number; changeType: string; changedFields: Record<string,{old:unknown;new:unknown}> | null;
  changeReason: string | null; changedBy: string; changedAt: string; snapshot: TradeResult; }

interface AuditEvent { id: number; eventTime: string; actorUsername: string; actorRole: string | null;
  action: string; entityType: string; tradeId: string | null; queueId: string | null;
  riskScore: number | null; statusAfter: string | null; likelyReason: string | null;
  recommendedFix: string | null; appliedFix: string | null; details: Record<string, unknown> | null; }

interface AuditBox { tradeId: string; queueId: string | null; riskScore: number; likelyReason: string | null;
  recommendedFix: string | null; appliedFix: string | null; appliedBy: string | null;
  appliedTimestamp: string | null; status: string; }

interface MetricSet { aucRoc: number; precision: number; recall: number; f1: number; accuracy: number; falsePositiveRate: number; }
interface ModelVersion { version: string; algorithm: string; status: 'CANDIDATE'|'PRODUCTION'|'ARCHIVED'|'FAILED';
  metrics: MetricSet; targets: MetricSet; passed: Record<keyof MetricSet, boolean>;
  decisionThreshold: number; trainRows: number; trainedAt: string; promotedAt: string | null; }
interface ModelEvaluation extends MetricSet { modelVersion: string; evaluatedAt: string; datasetLabel: string; sampleSize: number; }
interface DriftCheck { runAt: string; modelVersion: string; threshold: number; driftDetected: boolean;
  results: { feature: string; psi: number; severity: 'GREEN'|'AMBER'|'RED' }[]; }
interface Paged<T> { items: T[]; total: number; page: number; pageSize: number; }
```

### 8.3 Engine interface (ML module implements · Backend calls) — `backend/app/engine/`

**Golden rule:** the engine is a **pure Python library**. It never imports FastAPI or SQLAlchemy, never reads `.env`, and never touches the DB. Backend loads data from the DB, packs it into the dataclasses below, calls the engine, and saves what comes back. The backend imports **only** from `app.engine` (the names re-exported in `app/engine/__init__.py`), never from internal engine files.

```python
# ---------- app/engine/types.py ----------
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Optional

@dataclass(frozen=True)
class CounterpartyRef:
    name: str; risk_tier: int; tier_label: str; fail_count: int; trade_count: int; fail_rate: float

@dataclass(frozen=True)
class SymbolStats:
    price_mean: float; price_std: float; qty_mean: float; qty_std: float; fail_rate: float

@dataclass
class ReferenceData:                        # built by BACKEND from DB tables
    symbols: set[str]                       # upper-case symbols
    counterparties: dict[str, CounterpartyRef]   # key = name.lower()
    accounts: dict[str, bool]               # account_id -> is_active
    symbol_stats: dict[str, SymbolStats]
    holidays: set[date] = field(default_factory=set)
    existing_trade_ids: set[str] = field(default_factory=set)  # NON-rejected trade IDs already in DB
    # For an amendment (PUT), backend removes the trade's own id from this set before calling.

@dataclass
class EngineConfig:                         # built by BACKEND from system_config (§12)
    alpha: float = 0.3; beta: float = 0.7
    pass_threshold: float = 0.5; exception_threshold: float = 0.7
    rule_weights: dict = field(default_factory=lambda: dict(DEFAULT_RULE_WEIGHTS))
    qty_min: int = 100; qty_max: int = 5000
    settlement_far_days: int = 15; price_zscore_warn: float = 3.0
    tier_thresholds: dict = field(default_factory=lambda: {"tier4": 1500, "tier3": 1000, "tier2": 500})
    @classmethod
    def from_system_config(cls, rows: dict[str, Any]) -> "EngineConfig": ...

@dataclass
class RuleHit:
    rule_id: str; category: str; triggered: bool; contribution: float; message: str

@dataclass
class FieldError:
    field: str; rule_id: str; message: str

@dataclass
class ScoredTrade:                          # one per input record, SAME ORDER as input
    row_index: int
    raw: dict                               # original record -> trades.raw_payload
    # normalized fields (None if missing/unparseable)
    trade_id: Optional[str]; trade_type: Optional[str]; symbol: Optional[str]
    quantity: Optional[int]; price: Optional[float]; counterparty: Optional[str]
    trade_date: Optional[date]; settlement_date: Optional[date]; account_id: Optional[str]
    trader_name: Optional[str]; ssi_matched: Optional[bool]; currency_matched: Optional[bool]
    # results
    status: Literal["PASS", "MONITOR", "EXCEPTION", "REJECTED"]
    priority: Optional[Literal["HIGH", "MEDIUM", "LOW"]]     # None if REJECTED
    rule_score: Optional[float]; ml_score: Optional[float]; final_score: float   # 1.0 if REJECTED
    risk_factors: list[dict]                # [{"factor","pct","contribution","detail"}] (§6.6)
    likely_reason: Optional[str]; suggested_action: Optional[str]; explanation: Optional[str]
    rejection_reasons: list[FieldError]     # empty unless REJECTED
    rule_hits: list[RuleHit]                # -> rule_evaluations
    features: Optional[dict]                # -> ml_predictions.features (None if no model / rejected)
    shap_values: Optional[dict]             # -> ml_predictions.shap_values
    model_version: Optional[str]
    warnings: list[str]                     # e.g. "Price 3.4 std devs from symbol mean"
    suggested_fixes: list[dict]             # [{"factor","fix"}] -> repair_queue.suggested_fixes

@dataclass
class ValidationResult:                     # for POST /trades/validate
    valid: bool; errors: list[FieldError]; warnings: list[str]

@dataclass
class TrainResult:
    version: str; artifact_path: str; algorithm: str; hyperparameters: dict
    feature_list: list[str]; decision_threshold: float
    metrics: dict                           # {"auc_roc","precision","recall","f1","accuracy","false_positive_rate"}
    global_shap: list[dict]                 # [{"feature","mean_abs_shap"}] sorted desc
    baseline_stats: dict                    # for PSI
    monthly_evaluations: list[dict]         # [{"dataset_label":"month_2026-01","sample_size":..., **metrics}]
    train_rows: int; test_rows: int
    counterparty_tiers: dict                # name -> {"fail_count","trade_count","fail_rate","risk_tier","tier_label"}
```

```python
# ---------- public functions (app/engine/__init__.py re-exports these) ----------
def validate_trade(record: dict, refs: ReferenceData, cfg: EngineConfig,
                   business_date: date) -> ValidationResult
    # Hard gates + type checks for ONE record. Used by POST /trades/validate and before POST /trades.

def score_trades(records: list[dict], refs: ReferenceData, cfg: EngineConfig,
                 business_date: date, predictor: "Predictor | None") -> list[ScoredTrade]
    # The full §6.1 pipeline for 1..10,000 records, vectorised.
    # `records`: raw dicts with ANY header style (dataset headers or camelCase), values str or typed.
    # Detects duplicates inside the batch + against refs.existing_trade_ids.
    # predictor=None -> ml_score=None, final_score=rule_score (rules-only mode).

class Predictor:
    version: str; feature_list: list[str]; decision_threshold: float
    @classmethod
    def load(cls, artifact_path: str) -> "Predictor": ...
    def predict(self, features: "pd.DataFrame") -> "np.ndarray": ...      # P(fail)
    def explain(self, features: "pd.DataFrame") -> "pd.DataFrame": ...    # SHAP, same columns

def load_historical_csv(path: str) -> "pd.DataFrame"
    # Canonical snake_case columns (same names as table historical_trades), parsed dates/bools.
def compute_counterparty_tiers(historical: "pd.DataFrame", thresholds: dict) -> dict
def compute_symbol_stats(historical: "pd.DataFrame") -> dict[str, SymbolStats]
def train_model(historical: "pd.DataFrame", refs: ReferenceData, cfg: EngineConfig,
                version: str, out_dir: str, params: dict | None = None) -> TrainResult
def evaluate_model(predictor: Predictor, labelled: "pd.DataFrame", refs: ReferenceData,
                   cfg: EngineConfig) -> dict
def compute_drift(baseline_stats: dict, current_features: "pd.DataFrame",
                  threshold: float = 0.2) -> list[dict]   # [{"feature","psi","severity"}]; raises InsufficientDataError (<500 rows)

class EngineError(Exception): ...
class InsufficientDataError(EngineError): ...
class ModelLoadError(EngineError): ...
```

**Mapping engine output → DB (BE's job):** `ScoredTrade` fields → `trades` columns. `rule_hits` → `rule_evaluations`. If `ml_score` is not None → one `ml_predictions` row (features, shap_values, scores, alpha, beta, classification). `suggested_fixes` → `repair_queue.suggested_fixes` when `final_score > 0` and status ≠ REJECTED. Engine `snake_case` keys become API `camelCase` in the BE Pydantic schemas.
**CLI (ML module, for the team and for Render):** `python -m app.engine.cli train --data ../data --out ml_artifacts --version v1` → writes `model_v1.joblib` + `model_v1_report.json` (the TrainResult). The backend script `scripts/register_model.py` reads that JSON into `model_versions` / `model_evaluations`.

---

## 9. Audit Trail (FR-AT)

- **Append-only** `audit_logs` table. The DB trigger blocks UPDATE/DELETE/TRUNCATE (already in `schema.sql`). Each row stores `hash = sha256(prev_hash + canonical JSON of row)`, so `/audit/verify` can prove nobody tampered with it. Take a Postgres advisory lock while inserting so the chain stays ordered.
- All audit writes go through **one** function, `audit_service.log(action, entity_type, …, actor, before, after, details)`. Never write to the table directly.
- **Action catalogue:** `LOGIN, LOGIN_FAILED, LOGOUT, TRADE_SUBMITTED, TRADE_REJECTED, TRADE_SCORED, ML_PREDICTION, TRADE_AMENDED, BULK_UPLOAD_STARTED, BULK_UPLOAD_COMPLETED, QUEUE_ITEM_CREATED, QUEUE_ASSIGNED, QUEUE_REASSIGNED, QUEUE_UNASSIGNED, FIX_APPLIED, TRADE_RESUBMITTED, QUEUE_RESOLVED, QUEUE_REJECTED, HIGH_RESOLUTION_APPROVED, EXPORT_CSV, AUDIT_REPORT_GENERATED, REJECTION_REPORT_SENT, RETRAIN_STARTED, RETRAIN_COMPLETED, RETRAIN_FAILED, MODEL_PROMOTED, DRIFT_CHECK_RUN, DRIFT_ALERT, CONFIG_CHANGED, USER_CREATED, USER_UPDATED, ROLE_CHANGED, CHAT_QUERY`.
- For bulk uploads, write one `TRADE_SUBMITTED` (+ `TRADE_REJECTED` / `QUEUE_ITEM_CREATED`) row per trade **in bulk inserts**, plus one `BULK_UPLOAD_*` row per batch.
- **Audit viewer UI** (Good-to-have, we build it): search by Trade ID, date range (choose Trade Date / Settlement Date / Event time), user dropdown, action. An AntD `Timeline` shows the lifecycle. **Audit box** (FR-AT-007) on the trade page shows the latest change. **Report** button generates the PDF (reportlab) and emails it to an auditor/trader/compliance address.
- Ops User sees audit only for trades assigned to them (scope=ASSIGNED). Manager/Sys Admin see everything.

---

## 10. ML Monitoring Dashboard (FR-MM, A3)

| Panel | Content |
|---|---|
| Metric cards | AUC-ROC, Precision, Recall, F1, Accuracy, **FPR**, each with its target and a green/red badge (FR-MM-001/002/005) |
| Trend chart | Line chart per metric over `model_evaluations` with dashed target lines; filter by date range + model version (FR-MM-003/004) |
| Drift table | Per feature PSI (SSI_Matched_Flag, Currency_Matched_Flag, Counterparty_Tier, Quantity, Price_ZScore, Days_To_Settlement, Is_Weekend_Settlement, Rule_Engine_Output …). Severity **GREEN < 0.1 ≤ AMBER ≤ 0.2 < RED**. Shows last check time and a **"Run Drift Check Now"** button (FR-MM-006/007/008) |
| Alerts | When any PSI > threshold: notification to all ML_ADMIN users (+ email outbox) (FR-MM-009) |
| Feature importance | Horizontal bar chart of mean \|SHAP\| (global) for the production model (FR-MM-010) |
| Model registry | Table of versions; **Retrain** and **Promote** buttons; α/β editor with validation α+β=1 |

**PSI computation:** baseline = training-set distribution stored in `model_versions.baseline_stats` (10 quantile bins for numeric features, category proportions for binary/categorical). Current window = features of trades scored in the last `config.drift_window_days` (default 30) from `ml_predictions.features`. `PSI = Σ (cur% − base%) × ln(cur% / base%)`, with ε = 1e-4 for empty bins. Need ≥ 500 rows, otherwise return "insufficient data".
*Demo tip:* uploading the 20K file produces real drift (e.g. Quantity, because of the huge values, and SSI/Currency proportions differ: 50/50 vs 38/62 in training).

---

## 11. Chatbot (A2, Good-to-have)

**Approach: tool-calling over SQL, not free-form text-to-SQL.** It's safer and the answers are exact.

| Tool | Answers | Query |
|---|---|---|
| `failures_by_counterparty(name)` | "How many times did settlement fail for BlackRock?" | historical `settlement_failed=true` count + live EXCEPTION/REJECTED count for that counterparty |
| `failures_by_symbol(symbol)` | "…for symbol INFY?" | same by symbol |
| `rules_triggered(trade_id)` | "Which rule was triggered for T110003?" | `rule_evaluations WHERE triggered` (+ rejection_reasons) |
| `trades_settling_within(days)` | "Trades settling in the next one/two weeks?" | `settlement_date BETWEEN businessDate AND businessDate + days`, grouped by status |
| `explain_trade(trade_id)` | "Why is T110003 an exception?" | explanation + risk factors |
| `top_risky(entity, n)` | "Top 5 riskiest counterparties" | aggregates |

- LLM: an OpenAI-compatible client with `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in `.env` (works with OpenAI, Groq or Gemini's OpenAI endpoint). The LLM picks a tool via function-calling, we run the SQL, and the LLM phrases the answer.
- **Fallback with no key:** a regex/keyword intent router calls the same tools and fills answer templates. The chatbot must work in the demo even without internet.
- Respect RBAC: a Trader only gets data about their own trades. Log every query in `chat_messages` + audit `CHAT_QUERY`.
- (P3) Vector DB: pgvector `trade_embeddings` with `sentence-transformers/all-MiniLM-L6-v2` over each trade's explanation text, for "find trades similar to T110003" / semantic search.

---

## 12. Configuration (`system_config` keys — seed defaults)

| Key | Default | Editable by |
|---|---|---|
| `hybrid_weights` | `{"alpha":0.3,"beta":0.7}` | ML_ADMIN |
| `classification_thresholds` | `{"pass":0.5,"exception":0.7}` | ML_ADMIN |
| `rule_weights` | `{"ssi":0.35,"ccy":0.15,"sd_past":0.15,"sd_weekend":0.10,"sd_far":0.05,"sd_cap":0.15,"cp_tier":{"1":0,"2":0.10,"3":0.20,"4":0.25},"qty_high":0.10,"qty_low":0.05,"price_warn":0.0}` | SYS_ADMIN |
| `counterparty_tier_thresholds` | `{"tier4":1500,"tier3":1000,"tier2":500}` | SYS_ADMIN |
| `quantity_limits` | `{"min":100,"max":5000}` | SYS_ADMIN |
| `settlement_far_days` | `15` | SYS_ADMIN |
| `price_zscore_warn` | `3` | SYS_ADMIN |
| `business_date_override` | `null` | SYS_ADMIN |
| `batch_size` | `10000` | SYS_ADMIN |
| `max_upload_rows` | `100000` | SYS_ADMIN |
| `psi_threshold` | `0.2` (amber from 0.1) | ML_ADMIN |
| `drift_window_days` | `30` | ML_ADMIN |
| `rejection_report_recipients` | `["trader-group@tradeperfect.local"]` | SYS_ADMIN |
| `audit_report_recipients` | `["compliance@tradeperfect.local"]` | SYS_ADMIN |

**`.env` (never committed; commit `.env.example`):** `DATABASE_URL, JWT_SECRET, JWT_EXPIRE_MINUTES=480, CORS_ORIGINS, SEED_DEFAULT_PASSWORD, MODEL_DIR=ml_artifacts, DATA_DIR=data, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, APP_TIMEZONE=Asia/Kolkata, LOG_LEVEL=INFO`. Frontend: `VITE_API_BASE_URL, VITE_USE_MOCKS=true|false`.

---

## 13. Folder Structure (monorepo)

```
trade-perfect/
├── README.md                     # setup, run, architecture, assumptions (C/E own)
├── PROJECT_CONTEXT.md            # this file  (also copy as CLAUDE.md if using Claude Code)
├── MODULE_FRONTEND.md  MODULE_ML.md  MODULE_BACKEND.md   # per-module briefs
├── KICKOFF_PROMPTS.md  schema.sql
├── PROGRESS.md                   # running log — update after every work session
├── docker-compose.yml            # postgres + backend + frontend
├── .env.example  .gitignore
├── data/                         # provided CSVs (historical 50K, 20K, Ref_Data/*) — committed
├── docs/
│   ├── TECHNICAL_DESIGN.md       # deliverable (derived from this file)
│   ├── ML_MODEL_REPORT.md        # preprocessing, features, training, evaluation
│   ├── ASSUMPTIONS.md            # copy of §16 with mentor answers
│   └── diagrams/
├── notebooks/
│   ├── 01_eda.ipynb
│   └── 02_model_experiments.ipynb
├── backend/
│   ├── Dockerfile  requirements.txt  pytest.ini  alembic.ini
│   ├── alembic/versions/         # migration 0001 = schema.sql
│   ├── app/
│   │   ├── main.py               # FastAPI app, CORS, routers, exception handlers, startup (load model)
│   │   ├── core/                 # config.py (pydantic-settings), database.py, security.py (JWT, bcrypt),
│   │   │                         # deps.py (get_db, get_current_user, require_permission), logging.py, errors.py
│   │   ├── models/               # SQLAlchemy ORM (one file per area: user.py, reference.py, trade.py,
│   │   │                         # repair_queue.py, audit.py, ml.py, config.py, notification.py)
│   │   ├── schemas/              # Pydantic request/response models (camelCase aliases) mirroring §8.2
│   │   ├── api/v1/               # routers: auth.py, reference.py, trades.py, uploads.py, dashboard.py,
│   │   │                         # repair_queue.py, team.py, audit.py, ml.py, admin.py, notifications.py, chat.py
│   │   ├── engine/               # ══ ML MODULE OWNS THIS FOLDER (pure Python, no DB/web) ══
│   │   │   ├── __init__.py       # public API re-exports (§8.3) — the ONLY import point for backend
│   │   │   ├── types.py  config.py  errors.py
│   │   │   ├── normalize.py      # header mapping, parse dates/bools/numbers (UI + file share it)
│   │   │   ├── calendar.py       # weekend/holiday checks, next business day
│   │   │   ├── hard_gates.py     # R-000..R-008 (vectorised)
│   │   │   ├── rules.py          # soft rules → rule_score + RuleHits
│   │   │   ├── reference.py      # load_historical_csv, compute_counterparty_tiers, compute_symbol_stats
│   │   │   ├── features.py       # build_features — SAME code for train + inference
│   │   │   ├── model.py          # Predictor (load/predict/explain with SHAP)
│   │   │   ├── train.py  evaluate.py  drift.py
│   │   │   ├── explain.py        # risk_factors %, likely reason, explanation text
│   │   │   ├── fixes.py          # fix catalogue
│   │   │   ├── pipeline.py       # validate_trade, score_trades
│   │   │   ├── testgen.py        # automated test-case generation
│   │   │   └── cli.py            # python -m app.engine.cli train|evaluate|score-file|drift|gen-tests
│   │   ├── services/             # ══ BACKEND MODULE ══
│   │   │   ├── engine_adapter.py # builds ReferenceData/EngineConfig from DB, holds Predictor singleton
│   │   │   ├── trade_service.py  upload_service.py  repair_queue_service.py
│   │   │   ├── audit_service.py  export_service.py  report_service.py (PDF)
│   │   │   ├── ml_service.py     # registry, retrain (calls engine.train_model), promote, drift (engine.compute_drift)
│   │   │   ├── email_service.py  notification_service.py  config_service.py
│   │   └── chat/                 # tools.py (SQL tools), router.py (fallback intents), llm.py
│   ├── scripts/
│   │   ├── seed.py               # roles, permissions, users, reference data, historical trades, tiers, config
│   │   ├── register_model.py     # reads model_vN_report.json → model_versions/model_evaluations, promotes
│   │   ├── streamer.py           # optional: replays 20K file to POST /trades at N trades/sec
│   │   └── generate_test_cases.py# thin wrapper → app.engine.testgen (§14)
│   ├── ml_artifacts/             # model_v1.joblib (commit v1 only if < 50 MB)
│   └── tests/
│       ├── conftest.py           # test DB, client, auth helpers
│       ├── engine/               # ML module: test_hard_gates.py, test_rules.py, test_hybrid.py, test_explain.py,
│       │                         # test_features.py, test_drift.py, test_normalize.py, test_model_targets.py
│       ├── unit/                 # BE: services without DB (export formatting, hash chain, permissions)
│       ├── integration/          # test_auth.py, test_trades_api.py, test_bulk_upload.py, test_repair_queue.py,
│       │                         # test_audit.py, test_rbac.py, test_ml_api.py
│       └── generated/            # output of generate_test_cases.py (parametrized cases + edge CSVs)
└── frontend/
    ├── package.json  vite.config.ts  tsconfig.json  .env.example
    └── src/
        ├── main.tsx  App.tsx  routes.tsx
        ├── api/                  # client.ts (axios + JWT interceptor + error mapping), auth.ts, trades.ts,
        │                         # uploads.ts, dashboard.ts, repairQueue.ts, audit.ts, ml.ts, admin.ts, chat.ts
        ├── types/api.ts          # copy of §8.2
        ├── auth/                 # AuthContext.tsx, ProtectedRoute.tsx, usePermission.ts
        ├── layouts/AppLayout.tsx # sider menu filtered by permissions, header with user + bell
        ├── pages/
        │   ├── LoginPage.tsx
        │   ├── trades/TradeBookingPage.tsx  BulkUploadPage.tsx  MyTradesPage.tsx
        │   ├── dashboard/TradeDashboardPage.tsx
        │   ├── repair-queue/RepairQueuePage.tsx
        │   ├── audit/AuditTrailPage.tsx
        │   ├── team/TeamPerformancePage.tsx
        │   ├── ml/ModelMonitoringPage.tsx  ModelRegistryPage.tsx
        │   └── admin/UsersPage.tsx  ConfigPage.tsx
        ├── components/           # StatusTag, PriorityTag, QueueStatusTag, RiskScoreBadge, RiskFactorsBar,
        │                         # ExplanationPopover, ExportCsvButton, DateRangeFilter, AuditBox,
        │                         # TradeResultCard, MetricCard, PsiTable, ChatWidget, NotificationBell
        ├── hooks/                # useTrades, useRepairQueue, … (react-query)
        ├── utils/                # constants.ts (colors, enums), format.ts (dates IST, numbers, %)
        └── mocks/                # msw handlers + fixtures/mock_fixtures.json (provided; matches §8.2)
```

### 13.1 Frontend routes & who can see them

| Route | Page | Roles |
|---|---|---|
| `/login` | Login | public |
| `/trades/new` | Booking form (Submit) | TRADER, SYS_ADMIN |
| `/trades/upload` | Bulk upload + progress + summary | TRADER, SYS_ADMIN |
| `/trades/:tradeId` | Booking form (Update) + history + audit box | TRADER(own), OPS_*, SYS_ADMIN; **not for REJECTED** (read-only detail instead) |
| `/my-trades` | Trader's own trades grid | TRADER |
| `/dashboard` | Trade Dashboard (KPIs today + historical, grid, filters, search, export, charts) | OPS_USER, OPS_MANAGER, SYS_ADMIN |
| `/repair-queue` | Repair Queue | OPS_USER, OPS_MANAGER, SYS_ADMIN |
| `/audit` | Audit trail viewer | OPS_USER(assigned), OPS_MANAGER, SYS_ADMIN |
| `/team` | Team performance | OPS_MANAGER, SYS_ADMIN |
| `/ml/monitoring` | Model monitoring | ML_ADMIN, SYS_ADMIN |
| `/ml/models` | Registry, retrain, promote, α/β | ML_ADMIN, SYS_ADMIN |
| `/admin/users`, `/admin/config` | Admin | SYS_ADMIN |
| Chat widget (floating) | Chatbot | all logged-in |

**Default landing after login:** TRADER → `/trades/new`, OPS_USER/OPS_MANAGER → `/dashboard`, ML_ADMIN → `/ml/monitoring`, SYS_ADMIN → `/dashboard`.

### 13.2 Trade Dashboard grid columns (FR-RQ-008, exact order)
Trade ID (link → booking form, except REJECTED) · Queue ID (link → repair queue, empty for REJECTED / score 0) · Risk Score · Status · Likely Reason · Suggested Action · Priority · Risk Factors % (mini stacked bar + tooltip) · Explanation (popover). Optional extra columns: Symbol, Counterparty, Trade Date, Settlement Date, Trader.
Top KPI cards: **Today:** Total, PASS n (%), MONITOR n (%), EXCEPTION n (%), REJECTED n. **Historical:** same. Filters: Trade Date range, Settlement Date range, Status, Priority. Search: Trade ID / Symbol. Export CSV. Pagination 50.

### 13.3 Colour convention (use everywhere: dashboard, queue, monitoring)
PASS / GREEN `#52c41a` · MONITOR / AMBER `#faad14` · EXCEPTION / RED `#f5222d` · REJECTED `#595959` (dark grey) · Priority HIGH red, MEDIUM amber, LOW green · Queue OPEN blue `#1677ff`, IN_PROGRESS purple `#722ed1`, RESOLVED green, REJECTED grey.

---

## 14. Testing Strategy

- **Engine unit tests (ML module, pytest, no DB):** every hard gate pass + fail; each soft rule; settlement cap; tier boundaries (499/500/999/1000/1500/1501); hybrid math + thresholds at 0.4999/0.5/0.6999/0.7; priority mapping; risk-factor % sums to 100; PSI on identical distributions = 0; normalization (BOM, `9/11/2026`, `YES`, whitespace).
- **Backend integration (pytest + httpx TestClient + separate test DB):** login and 401/403 for every role (RBAC matrix test generated from §5.2); submit valid/invalid trade; amend creates version; bulk upload of a 30-row sample containing every trap from §2.1; duplicate IDs; queue assign/resubmit (HIGH blocked → 409)/reject/bulk; audit immutability (UPDATE raises); CSV export headers; ML retrain → promote.
- **Automated test case generation** (deliverable): `scripts/generate_test_cases.py` reads the rule config and **generates** (a) parametrized pytest cases (one positive + one negative per rule, boundary values) into `tests/generated/`, and (b) synthetic CSV files of edge-case trades with `faker`. It runs in CI/README.
- **Frontend:** vitest + testing-library for the booking form validation and table rendering; optional Playwright e2e "trader books trade → ops resolves".
- **ML:** `test_model_meets_targets` asserts the production metrics meet §6.5 targets on the holdout set.
- Target coverage ≥ 70% on `services/` and `engine/`.

---

## 15. Team Split & Plan — THREE MODULES

### 15.1 Modules & ownership

| Module | Brief file | Folder it owns | Run from | Paired non-Claude member |
|---|---|---|---|---|
| **FE: Frontend** | `MODULE_FRONTEND.md` | `frontend/**` | **Claude #2** | D (QA: tests each screen per role) |
| **ML: ML & Scoring Engine** | `MODULE_ML.md` | `backend/app/engine/**`, `backend/tests/engine/**`, `notebooks/**`, `docs/ML_MODEL_REPORT.md` | **Claude #1** for ML-1 → ML-3; ML-4 → ML-5 go to **whichever Claude user is ahead** (default Claude #2, because they pair with the monitoring screens) | C (data/ML analyst: EDA, metric checks, report) |
| **BE: Backend** | `MODULE_BACKEND.md` | everything else in `backend/` (`app/core, models, schemas, api, services, chat`, `alembic/`, `scripts/`, `tests/unit`, `tests/integration`), `schema.sql`, `docker-compose.yml` | **Claude #1** | E (DevOps: Docker, deploy, README) |

**Why this split works:** each module talks to the others only through a written contract (FE↔BE = §8.1/8.2, BE↔ML = §8.3), so all three can be built in parallel:
- FE builds against **mock data** (`mock_fixtures.json`, real-looking data generated from the 20K file) until BE is live.
- BE builds against the engine in **rules-only mode** (`predictor=None`) until the model is trained.
- ML is a pure library tested with pytest on the CSV files and needs nobody.

**Who touches what:** only the owner edits a module's folder. If you need a change in someone else's module, ask the owner (or open an issue) and add a line to §17 if a contract changes.

### 15.2 Build order (steps, not days; ⇢ = depends on)

| Step | Claude #1 | Claude #2 | Non-Claude |
|---|---|---|---|
| 1 | **ML-1** Engine skeleton + normalize + hard gates + rules + hybrid + explain (rules-only `score_trades` works) | **FE-1** Project setup, layout, login, routing, mocks | E: repo + docker-compose; C: EDA notebook |
| 2 | **BE-1** Setup, DB, Alembic, seed, auth/RBAC | **FE-2** Booking form + result card | D: TEST_PLAN.md from FR list |
| 3 | **BE-2** Trade APIs using engine (rules-only) ⇢ ML-1, BE-1 | **FE-3** Bulk upload + My Trades | C: sample trap CSVs |
| 4 | **ML-2** Features + training + threshold tuning + `model_v1` artifact | **FE-4** Trade Dashboard | C: verifies metrics |
| 5 | **ML-3** Predictor + SHAP + risk-factor merge; **BE-3** bulk upload + dashboard APIs ⇢ ML-3 | **FE-5** Repair Queue + Team page | **🔗 Integration #1:** FE switches booking/dashboard to the real API |
| 6 | **BE-4** Repair queue workflows | **FE-6** Audit viewer + AuditBox | D: queue scenarios |
| 7 | **BE-5** Audit, hash chain, PDF, email | **ML-4** Drift (PSI), evaluate, retrain function | |
| 8 | **BE-6** ML/admin/notification APIs ⇢ ML-4 | **FE-7** Model Monitoring + Registry + Admin + bell | **🔗 Integration #2** |
| 9 | **BE-7** Chatbot | **FE-8** Chat widget; **ML-5** test generation + ML report | C: ML_MODEL_REPORT.md |
| 10 | **BE-8** Hardening, perf (20K upload), coverage, deploy support | **FE-9** Polish, tests, empty/error states | E: deploy; D: full regression; **🔗 Final integration + demo rehearsal** |

Details and acceptance criteria for each step are in the module files.

### 15.3 How we share context between chats
1. **Every chat** gets attached: `PROJECT_CONTEXT.md` + `PROGRESS.md` + **its module file** (`MODULE_*.md`). Add the module's extra files listed in that module file. Then paste the kickoff prompt from `KICKOFF_PROMPTS.md`.
2. **One chat per step** (e.g. "BE-2 Trade APIs"). When the step is done, ask Claude for the PROGRESS.md session entry, commit, and start a new chat for the next step.
3. **Don't paste the whole repo.** Share only the files the step needs. Cross-module hand-over files:
   - FE needs from BE: `backend/app/schemas/<module>.py` (to check field names), or simply the Swagger JSON (`/openapi.json`).
   - BE needs from ML: `backend/app/engine/__init__.py` + `types.py` (the interface), never the internals.
   - ML needs from BE: nothing (only the CSVs).
4. Any contract change (API, schema, engine interface, rules) means: update this file, add a §17 line, and tell the other Claude user.
5. If you use **Claude Code** in the repo, copy this file to `CLAUDE.md` at the repo root so it's picked up automatically.

### 15.4 Git workflow
- `main` is protected and always demo-able. Branch names: `fe/<step>-<thing>`, `be/<step>-<thing>`, `ml/<step>-<thing>` (e.g. `ml/ml-1-rule-engine`). Merge by PR with one reviewer.
- Commit messages: `feat(trades): add bulk upload chunking`, `fix(queue): block resubmit on HIGH`, `test(rules): tier boundaries`, `docs: update README`.
- Never commit `.env`, `node_modules/`, `__pycache__/`, `.venv/`, or large model files (> 50 MB).

---

## 16. Assumptions & Open Questions (ask mentors; record answers in docs/ASSUMPTIONS.md)

| # | Ambiguity in brief | Our default |
|---|---|---|
| Q1 | Priority HIGH/MEDIUM/LOW never defined | EXCEPTION→HIGH, MONITOR→MEDIUM, PASS(score>0)→LOW |
| Q2 | FR-RQ-001 routes **PASS** trades with score > 0 to the repair queue. In the 20K prototype that is **100% of accepted trades** (19,296) | Follow the spec literally. PASS items are LOW and bulk-resubmittable. The Repair Queue page defaults to sorting HIGH first, and its "Hide LOW" toggle starts ON. **Ask mentors whether PASS should be queued at all** |
| Q3 | FR-RQ-014 "if priority is Low then RESOLVED" — auto on creation? | Created as OPEN, becomes RESOLVED on Re-Submit / bulk Re-Submit |
| Q4 | Matrix: Ops can't "View AI Risk Score", but the dashboard requires it | Ops users can see the score |
| Q5 | Matrix: Ops User can "Assign Trades", but FR says the Manager assigns | Ops User can self-assign only; Manager assigns/reassigns anyone |
| Q6 | FR-TB-008 "generate unique ID" vs tradeId mandatory | UI: server generates `T2xxxxx`. File: tradeId required, validated |
| Q7 | Single UI submission failing hard gates — persist as REJECTED? | No: return 422 with inline errors. File/stream rejects are persisted |
| Q8 | "Today" for past-settlement check | `business_date_override` or today (IST) |
| Q9 | Holiday calendar / market not specified | Weekends + editable `holidays` table (seed a few sample dates) |
| Q10 | Settlement date before trade date not in rules | Extra hard gate R-008 |
| Q11 | Symbol `ISRG` vs master `ISRG1` | Treat as not-in-master → REJECTED (exact match) |
| Q12 | "Same day" in dashboard = booked today or trade date today? | Booked today (`created_at` in IST). Filter also offers trade date |
| Q13 | Retraining data source (live trades have no outcome labels) | Retrain on historical table + optional uploaded labelled CSV |
| Q14 | Ops Manager key access says "Reports, Team Management" | Team Performance page + compliance CSV/PDF export |

---

## 17. Contract Change Log
| Date | Who | Change |
|---|---|---|
| 2026-10-10 | Initial | v1.0 of context, schema, API contract |
| 2026-10-10 | Planning | v1.1: split into 3 modules (FE / ML engine / BE); added §8.3 engine interface; `Days_To_Settlement` feature = settlement − trade date in both training and live; added prototype findings to §2.2; `scripts/train_initial_model.py` replaced by engine CLI + `scripts/register_model.py` |

---

## 18. Beginner Checklist (things people forget)
- [ ] CORS enabled for the frontend URL (local + deployed).
- [ ] Ref CSVs have a **UTF-8 BOM**: read with `encoding="utf-8-sig"`, or the first column name becomes `﻿Symbol`.
- [ ] Dates in files are `M/D/YYYY`; the API uses `YYYY-MM-DD`. Never compare dates as strings.
- [ ] Use `Decimal`/`NUMERIC` for price, not float, in the DB. Round scores to 4 dp.
- [ ] Use the **same** `features.py` for training and inference (most common ML bug).
- [ ] Load the ML model **once** at startup, not per request.
- [ ] Bulk = vectorised + bulk insert + background task. Don't loop the API.
- [ ] All list endpoints are paginated (50 default), and every filter has a DB index.
- [ ] Every state change writes an audit row (use the one helper).
- [ ] Permission checks happen on the **backend**, not just hidden buttons.
- [ ] Loading, empty and error states on every screen. Show the API error message.
- [ ] `.env.example` is up to date; no secrets in Git; dependency versions pinned.
- [ ] Logging (`logging` module, INFO level, request id) instead of `print`.
- [ ] A `make reset-demo` / `scripts/seed.py --reset` to restore a clean demo DB.
- [ ] Swagger at `/docs` works and every endpoint has a summary + example.
- [ ] Deployed app: model file present on the server; free-tier cold start (open it 2 minutes before judging).
- [ ] Rehearse the demo flow: Trader books → EXCEPTION → Ops Manager assigns → Ops applies fix → re-scored → resubmit → audit timeline → PDF → ML dashboard → drift check → chatbot.

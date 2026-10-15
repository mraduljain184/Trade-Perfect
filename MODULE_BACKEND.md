# MODULE BE — Backend (FastAPI + PostgreSQL)

> **Attach to every BE chat:** `PROJECT_CONTEXT.md` + `PROGRESS.md` + this file + `schema.sql`. From BE-2 on, also attach `backend/app/engine/__init__.py` and `types.py` (the engine interface). Attach the existing backend files the step modifies.
> **Run from:** Claude #1. **Paired with:** Member E (Docker/deploy/README) and Member D (API testing with Swagger/Postman).

---

## 1. Mission

Build the FastAPI service that the frontend talks to. It handles login and permissions, stores everything in PostgreSQL, calls the **Engine** (ML module) to validate and score trades, and runs the business workflows: bulk uploads, repair queue, audit trail with PDF reports and email, model registry, retraining, drift checks, notifications, and the chatbot. It also exposes Swagger at `/docs`.

## 2. Boundaries

| You own | You don't touch |
|---|---|
| `backend/` **except** `app/engine/**` and `tests/engine/**`; plus `schema.sql`, `docker-compose.yml`, root `.env.example` | `frontend/**`, `backend/app/engine/**` (ML module) |

- **You call the engine only via** `from app.engine import validate_trade, score_trades, Predictor, train_model, evaluate_model, compute_drift, compute_counterparty_tiers, compute_symbol_stats, load_historical_csv, ReferenceData, EngineConfig, ...` (§8.3). Never re-implement rules or scoring in the backend.
- **API shapes are fixed by §8.1 + §8.2.** Pydantic schemas use camelCase aliases (`model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)`) and the error shape from §8. If you need to change a shape, log it in §17 and tell the frontend owner.
- **DB = `schema.sql`.** The first Alembic migration executes it as-is. Later changes go through new Alembic migrations, plus an update to `schema.sql` and a §17 entry.

## 3. Key design decisions (follow these)

| Topic | Decision |
|---|---|
| Layering | `api/v1/*.py` (HTTP only: parse, permission, call service, return schema) → `services/*.py` (business logic + transactions) → `models/*.py` (SQLAlchemy ORM). No SQL in routers. |
| Sync vs async | Use plain `def` endpoints with a sync SQLAlchemy session (FastAPI runs them in a thread pool). This is simpler for beginners, and CPU-heavy scoring won't block the event loop. |
| Engine adapter | `services/engine_adapter.py` builds `ReferenceData` (cached, rebuilt when reference/config changes), `EngineConfig` (from `system_config`) and `business_date` (override or today in Asia/Kolkata), and holds the **Predictor singleton** (loaded at startup from the PRODUCTION model's `artifact_path`; `None` if missing → rules-only mode + warning log). |
| Background work | FastAPI `BackgroundTasks` for bulk upload, retraining and email sending. Each task opens **its own DB session**. No Redis/Kafka/Celery (out of scope). |
| Audit | Single entry point `audit_service.log(...)` / `audit_service.log_many(...)`. Hash chain: `hash = sha256(prev_hash + json.dumps(row, sort_keys=True, default=str))`, inside `SELECT pg_advisory_xact_lock(42)`. |
| Scoping | Row-level rules live in services: Trader → `trades.trader_user_id = me`; Ops User → queue items `assigned_to = me OR (status='OPEN' AND assigned_to IS NULL)`; audit for Ops User → trades in their assigned items. |
| IDs | UI trades get `'T' + nextval('trade_id_seq')`. Queue IDs come from the DB default (`RQ-000001`). Batch IDs: `BATCH-YYYYMMDD-NNNN`. |
| Time | Store `TIMESTAMPTZ` in UTC and return ISO-8601 `Z`. Business dates are plain `DATE`. |
| Money/scores | `NUMERIC` in the DB; convert to `float` in schemas; scores rounded to 4 dp. |
| Logging | `logging` with a request-id middleware (`X-Request-ID`), one line per request (method, path, status, ms, user). Never log passwords or tokens. |
| Errors | Global exception handlers map `HTTPException`, `RequestValidationError`, the engine's `EngineError` and unknown errors to the §8 error shape. A 500 never leaks a stack trace. |

## 4. Steps

### BE-1 — Setup, DB, seed, auth  (needs nothing)
**Build:** `requirements.txt` (pinned), `app/main.py` (CORS from `CORS_ORIGINS`, routers, handlers, `/health`), `core/config.py` (pydantic-settings, all §12 env vars), `core/database.py`, `core/security.py` (bcrypt, JWT HS256, 8 h), `core/deps.py` (`get_db`, `get_current_user`, `require_permission(code)` → 403 in the §8 error shape), `core/logging.py`, `core/errors.py`, all `models/*.py` mirroring `schema.sql`, Alembic migration `0001_initial` that runs `schema.sql`, `Dockerfile`, `docker-compose.yml` (postgres:16 + backend, volume, healthcheck), `.env.example`.
`scripts/seed.py` (idempotent, `--reset` flag):
- roles 101–105, the 28 permission codes from §5.2 with scopes, users from §5.4 (password from `SEED_DEFAULT_PASSWORD`);
- symbols, counterparties, accounts from `data/Ref_Data` (strip BOM);
- the 50K historical rows into `historical_trades` (bulk insert / `COPY`; < 30 s);
- counterparty stats and tiers via `engine.compute_counterparty_tiers` and symbol stats via `engine.compute_symbol_stats` (ML-1 delivers these first);
- `system_config` defaults (§12); a few sample `holidays`.
Auth endpoints 1–3, with audit `LOGIN`, `LOGIN_FAILED`, `LOGOUT` (implement `audit_service` with the hash chain now).
**Acceptance:**
- [ ] `docker compose up` → `/docs` loads; `/health` returns ok.
- [ ] Each seeded role can log in, and `/auth/me` returns the right permissions.
- [ ] A wrong password gives 401; a Trader calling an ML endpoint gets 403.
- [ ] Counterparty tiers in the DB match §2.3; re-running seed gives no duplicates.

### BE-2 — Reference + Trade APIs (single trade)  ⇢ ML-1
**Build:** `services/engine_adapter.py`, `services/trade_service.py`, `services/export_service.py`, routers `reference.py`, `trades.py` (endpoints 4–14).
- `POST /trades/validate` → `engine.validate_trade` → `{valid, fields, warnings}` (no DB write).
- `POST /trades`: hard-gate errors → **422** and nothing saved. Otherwise assign tradeId, force `trader_name = current_user.full_name` and `trader_user_id`, `source=UI`, call `score_trades([record])`, then persist in **one transaction**: `trades`, `trade_versions` (v1 `CREATED`), `rule_evaluations` (triggered only), `ml_predictions` (if `ml_score`), `repair_queue` item if `final_score > 0` (priority from the result, status OPEN, suggested_fixes), audit `TRADE_SUBMITTED` (+ `ML_PREDICTION`, `QUEUE_ITEM_CREATED`). Return `TradeResult` (201).
- `PUT /trades/{tradeId}` (amend / Apply Fix): forbidden for REJECTED trades (409). Re-score with the trade's own ID removed from `existing_trade_ids`; `version += 1`; `trade_versions` row with `changed_fields` diff and `change_type` = `FIX_APPLIED` if `queueId` is given, else `AMENDED`. Update the queue item: new priority/score, `applied_fix/by/at`; if the new score = 0 → RESOLVED. Audit `TRADE_AMENDED` / `FIX_APPLIED`.
- `GET /trades` with every filter in §8.1, pagination (50 default, 500 max), sort, `mine=true`; Trader auto-scoped. `GET /trades/{tradeId}` (the non-rejected one first, else the latest rejected), `/history`, `/export` (CSV of the same filters, all FR-RQ-008 columns).
**Acceptance:**
- [ ] Swagger flow: Trader books a valid trade → gets status, scores, risk factors, queue ID; books an invalid one → 422 listing every field error.
- [ ] Amend creates version 2 and the history shows changed fields.
- [ ] Integration tests for all of the above, including RBAC (Ops User can't POST /trades).

### BE-3 — Bulk upload + Dashboard APIs  ⇢ ML-3 (works rules-only before that)
**Build:** `services/upload_service.py`, routers `uploads.py`, `dashboard.py`, optional `scripts/streamer.py`.
- `POST /trades/bulk`: accept `.csv` (and `.xlsx` if time allows), max 20 MB. Save to a temp dir, create an `upload_batches` row, return **202 `{batchId}`**, then process in a BackgroundTask:
  1. Read with pandas `dtype=str, encoding='utf-8-sig'` and convert to a list of dicts. If rows > `max_upload_rows` → batch FAILED with a message.
  2. For each chunk of `batch_size` (10,000): `score_trades(chunk, refs, cfg, bdate, predictor)`, then **bulk insert** trades / versions / rule_evaluations / ml_predictions / repair_queue / audit rows (use `session.execute(insert(Model), list_of_dicts)` or psycopg `COPY`). Add the accepted trade IDs to `refs.existing_trade_ids` before the next chunk. Update `processed_rows` and commit once per chunk.
  3. At the end: counts, `COMPLETED`, rejection report CSV → `email_outbox` (`REJECTION_REPORT`, recipients from config) + audit `BULK_UPLOAD_COMPLETED` + notification to the uploader.
  4. Any exception → batch `FAILED` with `error_message`; chunks already committed stay.
- `GET /uploads/{batchId}`, `GET /uploads/{batchId}/rejections` (CSV).
- `GET /dashboard/summary?date=` (today = `created_at` date in IST, FR-RQ-002; historical = all, FR-RQ-003) and `GET /dashboard/charts` (SQL `GROUP BY`; top 10 counterparties by EXCEPTION count; 10-bucket score histogram).
**Acceptance:**
- [ ] Upload `data/Trade_Dataset_20K.csv` → 2 chunks → **704 REJECTED**, the rest scored, in **< 2 min** locally.
- [ ] Uploading the same file again → all 20K rows REJECTED as duplicates (R-001), no crash.
- [ ] Dashboard numbers match a manual SQL count.

### BE-4 — Repair Queue workflows
**Build:** `services/repair_queue_service.py` (state-transition table, §7.1), routers `repair_queue.py`, `team.py` (endpoints 20–29), notifications to assignees (`ASSIGNMENT`).
- Assign: Ops User may only assign `OPEN` unassigned items **to themselves** (`QUEUE_ASSIGN`). Assigning to someone else or reassigning needs `QUEUE_REASSIGN` (Manager). `assigneeId=null` → unassign → OPEN.
- Re-Submit: only if priority ∈ {MEDIUM, LOW} and status ∉ {RESOLVED, REJECTED}, else **409**. Re-score the trade, then queue → RESOLVED and `trade.is_resolved = true`; audit `TRADE_RESUBMITTED` + `QUEUE_RESOLVED`.
- Bulk re-submit: LOW only; returns `BulkResult` with a reason per failed ID; never all-or-nothing.
- Reject: HIGH only, reason required → queue REJECTED + trade REJECTED (+ version row). Approve: Manager resolves HIGH with a note.
- `canResubmit` / `canReject` are computed server-side in `QueueItem`. Default sort: priority (HIGH → LOW), then risk score descending.
- `/team/performance`: per Ops user → assigned, in progress, resolved, average resolution hours (`resolved_at − assigned_at`).
**Acceptance:**
- [ ] A test for every allowed **and** forbidden transition.
- [ ] An Ops User can't see another user's items; the Manager sees all.
- [ ] Export CSV matches the filters.

### BE-5 — Audit trail, PDF, email
**Build:** finish `audit_service` (bulk `log_many`, hash chain), routers `audit.py` (endpoints 30–36), `services/report_service.py` (reportlab PDF: title, trade details, audit box, timeline table, hash-check line, generated-by/at), `services/email_service.py` (send `PENDING` outbox rows via SMTP if `SMTP_HOST` is set, else mark `LOGGED_ONLY` and keep the file — **never fail the request**).
- `GET /audit` filters: tradeId, date range on `dateField` (`tradeDate` | `settlementDate` | `eventTime`), userId, action. Ops User scope = ASSIGNED.
- CSV export columns exactly as FR-AT-006. `/audit/trades/{id}/latest` = AuditBox (FR-AT-007).
- `/audit/verify` re-computes the chain and returns `{valid, brokenAtId}`.
**Acceptance:**
- [ ] Every action in the §9 catalogue fires somewhere (grep test).
- [ ] An `UPDATE audit_logs` from psql fails (DB trigger).
- [ ] The PDF opens and contains the full timeline; the email outbox row exists.

### BE-6 — ML management, admin, notifications APIs  ⇢ ML-2, ML-4
**Build:** `services/ml_service.py`, `scripts/register_model.py`, routers `ml.py` (37–47), `admin.py` (48–50), `notifications.py` (51).
- `register_model.py <report.json> --promote`: insert `model_versions` (+ `model_evaluations` from `monthly_evaluations` + the holdout row) + `retraining_events` (`INITIAL`). Update counterparty tiers from the report.
- `POST /ml/retrain` → `retraining_events` QUEUED → BackgroundTask: load `historical_trades` from the DB as a DataFrame (same column names) → `engine.train_model(version=f"v{n+1}")` → register as **CANDIDATE** → notification `RETRAIN_DONE` to ML_ADMIN. Audit `RETRAIN_*`.
- `POST /ml/models/{v}/promote`: previous production → ARCHIVED; new → PRODUCTION (in one transaction, respecting the unique index); **hot-reload the Predictor singleton**; audit `MODEL_PROMOTED`.
- `POST /ml/drift/run`: features from `ml_predictions` in the last `drift_window_days` → `engine.compute_drift(baseline_stats, df, psi_threshold)` → `drift_checks` row. If any RED → notification (role `ML_ADMIN`, CRITICAL) + email outbox + audit `DRIFT_ALERT`. Catch `InsufficientDataError` → 400 with a friendly message.
- `GET /ml/models/production` adds `targets` (§6.5) and `passed` per metric (FPR passes if `<`, accuracy if `>`, others if `>=`).
- `PUT /ml/config`: validate α+β = 1 (±0.001) and 0 < pass < exception < 1; audit `CONFIG_CHANGED`; invalidate the engine-adapter config cache.
- Admin: users CRUD (hash password; role change → `ROLE_CHANGED` audit), `system_config` get/put per key with `editable_by` check, holidays CRUD (invalidate refs cache).
**Acceptance:**
- [ ] Retrain → v2 CANDIDATE → promote → `/health` shows model v2 without a restart.
- [ ] Drift run after the 20K upload shows RED features and creates a notification.

### BE-7 — Chatbot
**Build:** `app/chat/tools.py` (§11 tools as **parameterised SQL**: never string-format user text into SQL), `app/chat/router.py` (regex/keyword fallback intents, used when `LLM_API_KEY` is empty or the LLM call fails), `app/chat/llm.py` (OpenAI-compatible client with `base_url=LLM_BASE_URL`, function-calling with the tool schemas, 20 s timeout), router `chat.py` (52). Apply RBAC inside tools (Trader → only own trades). Log to `chat_messages` + audit `CHAT_QUERY`.
**Acceptance:**
- [ ] The 4 sample questions from the brief answer correctly **with no API key** (fallback), e.g. "How many times did settlement fail for BlackRock?" → 2,049 historical failures + live EXCEPTION/REJECTED count.

### BE-8 — Hardening & deploy support
- Integration tests from §14 (`tests/integration/`), including an **RBAC matrix test generated from §5.2** (every role × a representative endpoint per permission → 2xx/403) and an upload test with `tests/generated/edge_cases.csv` (from ML-5).
- Coverage ≥ 70% on `services/`; `pytest -q` green in a clean DB.
- Performance: 20K upload timing in PROGRESS.md; check the indexes are used for dashboard filters (`EXPLAIN`).
- Render: build `pip install -r requirements.txt`; start `alembic upgrade head && python scripts/seed.py --if-empty && uvicorn app.main:app --host 0.0.0.0 --port $PORT`; set env vars; make sure `ml_artifacts/model_v1.joblib` is in the image. **The free tier has 512 MB RAM:** measure memory after loading the model and scoring 10K rows. If it's too high, ask the ML owner for a smaller forest (100 trees, depth 10) or set `batch_size` to 5,000.
- README backend section (with E): setup, env vars, seed, run, test, Swagger URL, demo users.

## 5. Gotchas checklist
- [ ] Background tasks must not reuse the request's DB session.
- [ ] Avoid N+1 queries in list endpoints (`selectinload` for the queue item → trade → assignee).
- [ ] `JSONB` columns: convert `date`/`Decimal`/numpy values before saving (engine output is already plain types).
- [ ] CSV exports: stream with `StreamingResponse`; set `Content-Disposition`; dates `YYYY-MM-DD`.
- [ ] CORS origins include `http://localhost:5173` and the Vercel URL.
- [ ] Never return `password_hash`; never log tokens.
- [ ] Every endpoint has `summary`, `tags` and a response model, so Swagger documents itself (Definition of Done).
- [ ] The app starts even if there's no model (rules-only) or no SMTP/LLM config.

## 6. Definition of Done (BE module)
- [ ] All 53 endpoints of §8.1 implemented, documented in Swagger, and permission-guarded.
- [ ] Shapes match §8.2 (FE confirms at each integration checkpoint).
- [ ] Bulk 20K works; queue state machine and audit immutability tested; PDF and email outbox work.
- [ ] Tests green, coverage ≥ 70%; Docker compose runs; deployed on Render.

## 7. Session-end ritual
Ask Claude for the PROGRESS.md session entry. Tick the endpoint table (PROGRESS §2). Commit as `feat(api): …` / `feat(queue): …`. If an API shape changed, log it in §17 and tell the frontend owner.

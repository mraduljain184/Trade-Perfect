# Trade Perfect — Backend

FastAPI service that the frontend talks to. It handles login and permissions, stores everything in
PostgreSQL, calls the scoring engine (`app/engine`, owned by the ML module) to validate and score trades,
and runs the workflows: bulk upload, repair queue, audit trail with PDF reports, model registry and
retraining, drift checks, notifications and the chatbot.

- Swagger (try every endpoint in the browser): http://localhost:8000/docs
- Health check: http://localhost:8000/api/v1/health → `{"status":"ok","db":"ok","model":"v1"}`
- The contract (paths, permissions, JSON shapes) is `PROJECT_CONTEXT.md` §8; status is in `PROGRESS.md`.

## 1. Run it locally (Docker)

From the repository root (the folder with `docker-compose.yml`):

    copy .env.example .env          (then replace every CHANGE_ME value)
    docker compose up --build

On start the backend container does four things, in this order:

1. `alembic upgrade head` — creates / upgrades the tables (`schema.sql`).
2. `python scripts/seed.py --if-empty` — loads roles, users, reference data and the 50K historical trades, only if the database is empty.
3. `python scripts/register_model.py ml_artifacts/model_v1_report.json --if-no-production` — puts model v1 in production if no model is in production yet. Without a model file the app still starts and scores with business rules only.
4. `uvicorn app.main:app` — starts the API.

Rebuild (`docker compose up --build`) whenever `backend/requirements.txt` changes.

### Demo users

Password for all of them = `SEED_DEFAULT_PASSWORD` from `.env`.

| Role | Username |
|---|---|
| Trader (15) | `firstname.lastname`, for example `priya.sharma` |
| Ops User | `ops.user1`, `ops.user2`, `ops.user3` |
| Ops Manager | `ops.manager` |
| ML Admin | `ml.admin` |
| Sys Admin | `sys.admin` |

In Swagger: call `POST /api/v1/auth/login`, copy `accessToken`, click **Authorize**, paste it.

## 2. Useful commands

Run these in a second terminal while `docker compose up` is running.

    docker compose exec backend pytest                              # all tests (uses a separate *_test database)
    docker compose exec backend pytest --cov=app/services --cov=app/chat --cov-report=term-missing
    docker compose exec backend python scripts/seed.py              # re-seed (no duplicates) and refresh tiers
    docker compose exec backend python scripts/seed.py --reset      # WIPE everything and seed again
    docker compose exec backend python scripts/register_model.py ml_artifacts/model_v1_report.json
    docker compose exec backend python scripts/generate_test_cases.py
    docker compose down -v                                          # delete the database volume completely

For a realistic demo of the 20K file, set the business date first (Sys Admin):
`PUT /api/v1/admin/config/business_date_override` with `{"value": "2026-09-28"}`.
Otherwise every trade that settles before today gets the "settlement date in the past" rule.

## 3. Environment variables

Copy `.env.example` to `.env`. Never commit `.env`.

| Variable | Needed | What it is |
|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL URL. `postgres://…` (Render style) is accepted and converted. |
| `JWT_SECRET` | yes | Random text, at least 32 characters. |
| `SEED_DEFAULT_PASSWORD` | yes (first start) | Password given to the seeded demo users, at least 8 characters. |
| `CORS_ORIGINS` | yes | Frontend URLs, comma-separated, e.g. `http://localhost:5173,https://your-app.vercel.app`. |
| `JWT_EXPIRE_MINUTES` | no (480) | Login lifetime. |
| `DATA_DIR` | no (`data`) | Folder with the provided CSV files. `/data` in docker-compose, `../data` on Render. |
| `MODEL_DIR` | no (`ml_artifacts`) | Where model files are read from and retrained models are written. |
| `SEED_EMAIL_DOMAIN` | no | Domain of the seeded users' e-mail addresses. |
| `APP_TIMEZONE` | no (`Asia/Kolkata`) | "Today" for the dashboard and the business date. |
| `LOG_LEVEL` | no (`INFO`) | |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | no | Empty `SMTP_HOST` = e-mails are not sent; they are stored in `email_outbox` as `LOGGED_ONLY`. |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | no | Empty `LLM_API_KEY` = the chatbot uses its keyword fallback (works offline). Any OpenAI-compatible provider works. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | docker-compose only | Used to create the local database. |

## 4. What is inside

    app/
      main.py            FastAPI app, CORS, error handlers, startup (loads the production model)
      core/              settings, database session, JWT + bcrypt, permission guard, logging, error shape
      models/            SQLAlchemy models - they mirror schema.sql
      schemas/           request / response shapes (camelCase JSON), mirror PROJECT_CONTEXT §8.2
      api/v1/            routers - HTTP only: parse, check permission, call a service, return a schema
      services/          business logic and transactions (one file per area)
      chat/              chatbot: SQL tools, keyword fallback, optional LLM
      engine/            scoring, training, drift - OWNED BY THE ML MODULE, imported only via `from app.engine import ...`
    scripts/             seed.py, register_model.py, generate_test_cases.py
    tests/               unit/, integration/ (backend), engine/ and generated/ (ML module)
    ml_artifacts/        model_v1.joblib + its training report

| Endpoints (§8.1) | Router | Service |
|---|---|---|
| 1–3 auth | `auth.py` | `auth_service` |
| 4–7 reference | `reference.py` | `reference_service` |
| 8–14 trades | `trades.py` | `trade_service`, `export_service` |
| 15–17 bulk upload | `uploads.py` | `upload_service` |
| 18–19 dashboard | `dashboard.py` | `dashboard_service` |
| 20–29 repair queue, team | `repair_queue.py`, `team.py` | `repair_queue_service` |
| 30–36 audit | `audit.py` | `audit_service` (write), `audit_query_service`, `report_service`, `email_service` |
| 37–44, 46 ML management | `ml.py` | `ml_service`, `admin_service` |
| 45, 47 scoring preview | `ml_scoring.py` (ML module) | `engine_adapter` |
| 48–50 admin | `admin.py` | `admin_service` |
| 51 notifications | `notifications.py` | `notification_service` |
| 52 chat | `chat.py` | `app/chat/*` |
| 53 health | `health.py` | — |

Rules the code follows: no SQL in routers; every write is one transaction and writes its audit rows in
the same transaction; row-level scoping (a Trader's own trades, an Ops User's own queue items) is done in
the services; background tasks (bulk upload, retraining, e-mail) open their own database session.

## 5. Tests

`docker compose exec backend pytest` → **1,022 passed, 1 skipped** (the skipped one compares
`schema.sql` with its migration copy and only runs outside Docker). Coverage of `app/services`: **97%**.

Highlights: `test_rbac_matrix.py` checks every route × every role against §5.2 and fails if a new route
is not permission-guarded; `test_queue_state_machine.py` checks every allowed and forbidden queue
transition; `test_edge_cases_upload.py` uploads the generated edge-case file through the API;
`test_audit_actions_used.py` checks that every audit action of §9 is written somewhere.

Tests rebuild the `*_test` database on every run and never touch the demo data.

## 6. Deploying on Render (prepared, not deployed)

Create a PostgreSQL database and a Web Service (Python 3.11) from this repository.

| Setting | Value |
|---|---|
| Root directory | `backend` |
| Build command | `pip install -r requirements.txt` |
| Start command | `alembic upgrade head && python scripts/seed.py --if-empty && python scripts/register_model.py ml_artifacts/model_v1_report.json --if-no-production && uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/api/v1/health` |

Environment variables to set: `DATABASE_URL` (the database's internal URL), `JWT_SECRET`,
`SEED_DEFAULT_PASSWORD`, `CORS_ORIGINS` (the Vercel URL), `DATA_DIR=../data`, `PYTHON_VERSION=3.11.9`,
and optionally the `SMTP_*` and `LLM_*` groups.

Things to know before deploying:

- **Memory.** Measured in Docker: 264 MB with the model loaded and idle; a 20K upload with the model
  peaks at **726 MB** with `batch_size` 10,000 and **552 MB** with 5,000. Render's free tier has 512 MB,
  so a 20K upload does not fit there as it is. Use an instance with 1 GB or more, or lower `batch_size`
  further (`PUT /admin/config/batch_size`; smaller values are not measured), or ask the ML owner for a
  smaller forest.
- **Seed data.** `scripts/seed.py` reads the CSV files from `DATA_DIR`. With root directory `backend` they
  are at `../data`; if Render does not expose files outside the root directory, clear the root directory
  and start every command with `cd backend && `.
- **Disk is temporary.** PDF reports and rejection reports (`backend/reports/`) and retrained models
  (`MODEL_DIR`) are lost on every deploy or restart. The database keeps the records; rejection reports
  are rebuilt on download, PDFs must be generated again, and a retrained model whose file is gone cannot
  be promoted. Add a persistent disk for `ml_artifacts` if retrained models must survive.
- **One instance only.** The production model, the config cache and background tasks live in the web
  process. Running several instances would need a shared cache and a job queue.
- **The model needs the pinned scikit-learn.** `model_v1.joblib` only loads with the version in
  `requirements.txt`.

## 7. Performance (Docker, laptop)

| What | Result |
|---|---|
| 20K file, rules only | 32.9 s, 2 chunks |
| 20K file, with model v1 + SHAP | 36.0 s — PASS 9,548 / MONITOR 5,541 / EXCEPTION 4,207 / REJECTED 704 |
| Same file again | all 20,000 rejected as duplicates, 19 s |
| Retraining on 50K rows | 88 s (background task) |
| Dashboard summary on 20K trades | about 40 ms |
| Repair queue page (50 rows) on 19K items | about 125 ms |

Index check (`EXPLAIN` on 20K trades): lookups by trade ID, status + newest first, symbol, queue status
and audit-by-trade use their indexes. Date-range filters fall back to a sequential scan when the range
covers a large share of the table, which is the planner's normal choice at this size. There is no index
on `trades.batch_id`, so the batch summary scans `trades`; add one with a migration if uploads grow.

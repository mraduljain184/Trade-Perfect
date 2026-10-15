-- =====================================================================
-- Trade Perfect - PostgreSQL schema (v1.0)
-- Source of truth for the DB. Backend SQLAlchemy models MUST mirror this.
-- Any change: update this file + PROJECT_CONTEXT.md "Contract Change Log".
-- Target: PostgreSQL 15+  (pgvector optional, see bottom)
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid(), digest()

-- ---------------------------------------------------------------------
-- 1. ENUM TYPES
-- ---------------------------------------------------------------------
CREATE TYPE trade_type_enum      AS ENUM ('BUY', 'SELL');
CREATE TYPE trade_status_enum    AS ENUM ('PASS', 'MONITOR', 'EXCEPTION', 'REJECTED');
CREATE TYPE priority_enum        AS ENUM ('HIGH', 'MEDIUM', 'LOW');
CREATE TYPE queue_status_enum    AS ENUM ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'REJECTED');
CREATE TYPE trade_source_enum    AS ENUM ('UI', 'FILE', 'STREAM', 'API');
CREATE TYPE batch_status_enum    AS ENUM ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED');
CREATE TYPE model_status_enum    AS ENUM ('CANDIDATE', 'PRODUCTION', 'ARCHIVED', 'FAILED');
CREATE TYPE retrain_status_enum  AS ENUM ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED');
CREATE TYPE email_status_enum    AS ENUM ('PENDING', 'SENT', 'FAILED', 'LOGGED_ONLY');

-- ---------------------------------------------------------------------
-- 2. USERS, ROLES, PERMISSIONS (RBAC)
-- ---------------------------------------------------------------------
CREATE TABLE roles (
    id              SMALLINT PRIMARY KEY,               -- 101..105 (from UserRoles.csv)
    code            VARCHAR(30)  NOT NULL UNIQUE,       -- TRADER, OPS_USER, OPS_MANAGER, ML_ADMIN, SYS_ADMIN
    name            VARCHAR(60)  NOT NULL,
    primary_function VARCHAR(200),
    key_access      VARCHAR(200)
);

CREATE TABLE permissions (
    id              SERIAL PRIMARY KEY,
    code            VARCHAR(50)  NOT NULL UNIQUE,       -- e.g. TRADE_SUBMIT_SINGLE
    description     VARCHAR(200) NOT NULL               -- text from UserRoles_Matrix.csv
);

CREATE TABLE role_permissions (
    role_id         SMALLINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id   INT      NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    scope           VARCHAR(10) NOT NULL DEFAULT 'ALL' CHECK (scope IN ('ALL', 'ASSIGNED', 'OWN')),
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(50)  NOT NULL UNIQUE,
    full_name       VARCHAR(100) NOT NULL,              -- for traders = "Trader Name" in trades
    email           VARCHAR(150) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,              -- bcrypt, never plain text
    role_id         SMALLINT     NOT NULL REFERENCES roles(id),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);
CREATE INDEX ix_users_role ON users(role_id);

-- ---------------------------------------------------------------------
-- 3. REFERENCE / MASTER DATA
-- ---------------------------------------------------------------------
CREATE TABLE counterparties (
    id                      SERIAL PRIMARY KEY,
    name                    VARCHAR(100) NOT NULL UNIQUE,   -- exact text as in Counterparty_Dataset.csv
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    historical_trade_count  INT     NOT NULL DEFAULT 0,
    historical_fail_count   INT     NOT NULL DEFAULT 0,     -- drives risk tier
    historical_fail_rate    NUMERIC(6,4) NOT NULL DEFAULT 0,
    risk_tier               SMALLINT NOT NULL DEFAULT 1 CHECK (risk_tier BETWEEN 1 AND 4),
    tier_label              VARCHAR(30) NOT NULL DEFAULT 'Tier1_HighTrust',
    tier_updated_at         TIMESTAMPTZ
);

CREATE TABLE symbols (
    id                  SERIAL PRIMARY KEY,
    symbol              VARCHAR(20) NOT NULL UNIQUE,         -- exact text as in Symbol_DataSet.csv
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    price_mean          NUMERIC(18,4),                       -- from historical data (price reasonableness)
    price_std           NUMERIC(18,4),
    qty_mean            NUMERIC(18,4),
    qty_std             NUMERIC(18,4),
    historical_fail_rate NUMERIC(6,4)
);

CREATE TABLE accounts (
    id              SERIAL PRIMARY KEY,
    account_id      VARCHAR(20) NOT NULL UNIQUE,             -- ACC1000..ACC1099
    is_active       BOOLEAN NOT NULL DEFAULT TRUE            -- 'Active' column Y/N
);

CREATE TABLE holidays (
    holiday_date    DATE        NOT NULL,
    market          VARCHAR(10) NOT NULL DEFAULT 'GLOBAL',
    description     VARCHAR(100),
    PRIMARY KEY (holiday_date, market)
);

-- ---------------------------------------------------------------------
-- 4. HISTORICAL TRAINING DATA (Historical_Trade_Dataset_50K.csv, read-only)
-- ---------------------------------------------------------------------
CREATE TABLE historical_trades (
    trade_id            VARCHAR(20) PRIMARY KEY,
    trade_type          trade_type_enum NOT NULL,
    symbol              VARCHAR(20)  NOT NULL,
    quantity            BIGINT       NOT NULL,
    price               NUMERIC(18,4) NOT NULL,
    counterparty        VARCHAR(100) NOT NULL,
    trade_date          DATE NOT NULL,
    settlement_date     DATE NOT NULL,
    account_id          VARCHAR(20)  NOT NULL,
    ssi_matched         BOOLEAN NOT NULL,
    currency_matched    BOOLEAN NOT NULL,
    trader_name         VARCHAR(100) NOT NULL,
    settlement_failed   BOOLEAN NOT NULL                      -- TARGET (YES=1 / NO=0)
);
CREATE INDEX ix_hist_cp ON historical_trades(counterparty);
CREATE INDEX ix_hist_symbol ON historical_trades(symbol);

-- ---------------------------------------------------------------------
-- 5. UPLOAD BATCHES (bulk file submission, max 10K rows per batch)
-- ---------------------------------------------------------------------
CREATE TABLE upload_batches (
    id              SERIAL PRIMARY KEY,
    batch_id        VARCHAR(30) NOT NULL UNIQUE,              -- BATCH-20261010-0001
    file_name       VARCHAR(255) NOT NULL,
    uploaded_by     INT NOT NULL REFERENCES users(id),
    total_rows      INT NOT NULL DEFAULT 0,
    processed_rows  INT NOT NULL DEFAULT 0,
    accepted_count  INT NOT NULL DEFAULT 0,                   -- PASS + MONITOR + EXCEPTION
    rejected_count  INT NOT NULL DEFAULT 0,
    status          batch_status_enum NOT NULL DEFAULT 'PENDING',
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

-- ---------------------------------------------------------------------
-- 6. TRADES (current state) + TRADE VERSIONS (amendment history)
-- ---------------------------------------------------------------------
-- Server-generated business trade IDs for UI-booked trades (FR-TB-008): T200001, T200002 ...
-- Starts at 200001 so it never collides with the provided files (historical HT*, primary T110000-T129999).
CREATE SEQUENCE trade_id_seq START 200001;

CREATE TABLE trades (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),   -- internal key
    trade_id            VARCHAR(30),               -- business ID (T110000). NULL allowed only for REJECTED (R-001 empty)
    trade_type          trade_type_enum,           -- typed cols are NULLABLE because REJECTED rows may be malformed
    symbol              VARCHAR(20),
    quantity            BIGINT,
    price               NUMERIC(18,4),
    counterparty        VARCHAR(100),
    trade_date          DATE,
    settlement_date     DATE,
    account_id          VARCHAR(20),
    trader_name         VARCHAR(100),
    trader_user_id      INT REFERENCES users(id),  -- set when booked via UI (FR-TB-004)
    ssi_matched         BOOLEAN,
    currency_matched    BOOLEAN,
    raw_payload         JSONB,                     -- original input exactly as received (for rejects / debugging)

    source              trade_source_enum NOT NULL DEFAULT 'UI',
    batch_id            INT REFERENCES upload_batches(id),
    batch_row_number    INT,

    -- scoring results (latest)
    status              trade_status_enum NOT NULL,              -- PASS / MONITOR / EXCEPTION / REJECTED
    priority            priority_enum,                           -- NULL for REJECTED
    rule_score          NUMERIC(5,4) CHECK (rule_score  BETWEEN 0 AND 1),
    ml_score            NUMERIC(5,4) CHECK (ml_score    BETWEEN 0 AND 1),
    final_score         NUMERIC(5,4) NOT NULL CHECK (final_score BETWEEN 0 AND 1),  -- 1.0 for REJECTED
    risk_factors        JSONB,          -- [{"factor":"SSI","pct":58.3,"contribution":0.245,"detail":"SSI not matched"}]
    likely_reason       TEXT,
    suggested_action    TEXT,
    explanation         TEXT,           -- natural-language explanation
    rejection_reasons   JSONB,          -- [{"ruleId":"R-005","message":"Symbol 'ISRG' not in master"}] for REJECTED
    model_version_id    INT,            -- FK added after model_versions is created

    version             INT NOT NULL DEFAULT 1,
    is_resolved         BOOLEAN NOT NULL DEFAULT FALSE,          -- set when repair item resolved
    created_by          INT REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by          INT REFERENCES users(id),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- tradeId must be unique among NON-rejected trades. Rejected duplicates are still stored for reporting.
CREATE UNIQUE INDEX ux_trades_trade_id_active ON trades(trade_id) WHERE status <> 'REJECTED';
CREATE INDEX ix_trades_trade_id        ON trades(trade_id);
CREATE INDEX ix_trades_status          ON trades(status);
CREATE INDEX ix_trades_priority        ON trades(priority);
CREATE INDEX ix_trades_trade_date      ON trades(trade_date);
CREATE INDEX ix_trades_settlement_date ON trades(settlement_date);
CREATE INDEX ix_trades_symbol          ON trades(symbol);
CREATE INDEX ix_trades_counterparty    ON trades(counterparty);
CREATE INDEX ix_trades_created_at      ON trades(created_at);
CREATE INDEX ix_trades_trader_user     ON trades(trader_user_id);

CREATE TABLE trade_versions (
    id              BIGSERIAL PRIMARY KEY,
    trade_pk        UUID NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    version         INT  NOT NULL,
    snapshot        JSONB NOT NULL,          -- full trade row (input + scores) at this version
    change_type     VARCHAR(30) NOT NULL,    -- CREATED / AMENDED / FIX_APPLIED / RESUBMITTED / REJECTED
    changed_fields  JSONB,                   -- {"ssiMatched":{"old":false,"new":true}}
    change_reason   TEXT,
    changed_by      INT REFERENCES users(id),
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trade_pk, version)
);

-- Every rule evaluated for every trade version (powers "Which rule was triggered for a trade?")
CREATE TABLE rule_evaluations (
    id                  BIGSERIAL PRIMARY KEY,
    trade_pk            UUID NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    trade_version       INT  NOT NULL,
    rule_id             VARCHAR(10) NOT NULL,     -- R-001..R-008, S-SSI, S-CCY, S-SD-PAST, S-SD-WKND, S-SD-FAR, S-CP-TIER, S-QTY-HIGH, S-QTY-LOW, W-PRICE
    rule_category       VARCHAR(20) NOT NULL,     -- HARD_GATE / SSI / CURRENCY / SETTLEMENT_DATE / COUNTERPARTY / QUANTITY / PRICE
    triggered           BOOLEAN NOT NULL,
    score_contribution  NUMERIC(5,4) NOT NULL DEFAULT 0,
    message             TEXT,
    evaluated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_rule_eval_trade ON rule_evaluations(trade_pk, trade_version);
CREATE INDEX ix_rule_eval_rule  ON rule_evaluations(rule_id) WHERE triggered;

-- ---------------------------------------------------------------------
-- 7. ML MODEL REGISTRY, PREDICTIONS, MONITORING
-- ---------------------------------------------------------------------
CREATE TABLE model_versions (
    id                  SERIAL PRIMARY KEY,
    version             VARCHAR(20) NOT NULL UNIQUE,   -- v1, v2 ...
    algorithm           VARCHAR(50) NOT NULL DEFAULT 'RandomForestClassifier',
    hyperparameters     JSONB,
    feature_list        JSONB NOT NULL,                -- ordered list of feature names
    decision_threshold  NUMERIC(4,3) NOT NULL DEFAULT 0.5,
    metrics             JSONB NOT NULL,                -- {"auc":0.96,"precision":..,"recall":..,"f1":..,"accuracy":..,"fpr":..}
    global_shap         JSONB,                         -- [{"feature":"SSI_Matched_Flag","meanAbsShap":0.21}, ...]
    baseline_stats      JSONB,                         -- per-feature bin edges + proportions for PSI
    train_rows          INT,
    test_rows           INT,
    artifact_path       VARCHAR(255) NOT NULL,         -- ml_artifacts/model_v1.joblib
    status              model_status_enum NOT NULL DEFAULT 'CANDIDATE',
    trained_by          INT REFERENCES users(id),
    trained_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_by         INT REFERENCES users(id),
    promoted_at         TIMESTAMPTZ,
    notes               TEXT
);
-- only one PRODUCTION model at a time
CREATE UNIQUE INDEX ux_model_one_production ON model_versions(status) WHERE status = 'PRODUCTION';

ALTER TABLE trades ADD CONSTRAINT fk_trades_model FOREIGN KEY (model_version_id) REFERENCES model_versions(id);

-- every ML inference logged (FR: "ML prediction logging")
CREATE TABLE ml_predictions (
    id                  BIGSERIAL PRIMARY KEY,
    trade_pk            UUID NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    trade_version       INT  NOT NULL,
    model_version_id    INT  NOT NULL REFERENCES model_versions(id),
    features            JSONB NOT NULL,          -- feature values fed to model
    rule_score          NUMERIC(5,4) NOT NULL,
    ml_score            NUMERIC(5,4) NOT NULL,
    final_score         NUMERIC(5,4) NOT NULL,
    alpha               NUMERIC(4,3) NOT NULL,
    beta                NUMERIC(4,3) NOT NULL,
    classification      trade_status_enum NOT NULL,
    shap_values         JSONB,                   -- {"SSI_Matched_Flag":0.21,"Counterparty_Tier":0.05,...}
    predicted_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_pred_trade ON ml_predictions(trade_pk);
CREATE INDEX ix_pred_time  ON ml_predictions(predicted_at);

-- metric time series (one row per evaluation / retraining run) -> FR-MM-003 trend chart
CREATE TABLE model_evaluations (
    id                  SERIAL PRIMARY KEY,
    model_version_id    INT NOT NULL REFERENCES model_versions(id),
    evaluated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    dataset_label       VARCHAR(100),            -- 'holdout_20pct', 'labelled_recent'
    auc_roc             NUMERIC(5,4),
    precision_score     NUMERIC(5,4),
    recall_score        NUMERIC(5,4),
    f1_score            NUMERIC(5,4),
    accuracy            NUMERIC(5,4),
    false_positive_rate NUMERIC(5,4),
    sample_size         INT
);
CREATE INDEX ix_eval_model_time ON model_evaluations(model_version_id, evaluated_at);

CREATE TABLE retraining_events (
    id                  SERIAL PRIMARY KEY,
    triggered_by        INT REFERENCES users(id),
    trigger_reason      VARCHAR(20) NOT NULL CHECK (trigger_reason IN ('MANUAL', 'DRIFT', 'SCHEDULED', 'INITIAL')),
    status              retrain_status_enum NOT NULL DEFAULT 'QUEUED',
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    new_model_version_id INT REFERENCES model_versions(id),
    log                 TEXT
);

CREATE TABLE drift_checks (
    id                  SERIAL PRIMARY KEY,
    model_version_id    INT NOT NULL REFERENCES model_versions(id),
    run_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    triggered_by        INT REFERENCES users(id),      -- NULL = automatic
    window_start        DATE,
    window_end          DATE,
    sample_size         INT,
    threshold           NUMERIC(4,3) NOT NULL DEFAULT 0.2,
    results             JSONB NOT NULL,                -- [{"feature":"Quantity","psi":0.27,"severity":"RED"}]
    max_psi             NUMERIC(6,4),
    drift_detected      BOOLEAN NOT NULL DEFAULT FALSE
);

-- ---------------------------------------------------------------------
-- 8. REPAIR QUEUE
-- ---------------------------------------------------------------------
CREATE SEQUENCE repair_queue_seq START 1;

CREATE TABLE repair_queue (
    id                  SERIAL PRIMARY KEY,
    queue_id            VARCHAR(20) NOT NULL UNIQUE DEFAULT ('RQ-' || lpad(nextval('repair_queue_seq')::text, 6, '0')),
    trade_pk            UUID NOT NULL UNIQUE REFERENCES trades(id) ON DELETE CASCADE,  -- one queue item per trade
    priority            priority_enum     NOT NULL,
    status              queue_status_enum NOT NULL DEFAULT 'OPEN',
    risk_score_at_entry NUMERIC(5,4) NOT NULL,
    suggested_fixes     JSONB,           -- [{"factor":"SSI","fix":"Re-confirm SSI with counterparty ..."}]
    assigned_to         INT REFERENCES users(id),
    assigned_by         INT REFERENCES users(id),
    assigned_at         TIMESTAMPTZ,
    applied_fix         TEXT,
    applied_by          INT REFERENCES users(id),
    applied_at          TIMESTAMPTZ,
    resolution_note     TEXT,
    resolved_by         INT REFERENCES users(id),
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_rq_status   ON repair_queue(status);
CREATE INDEX ix_rq_priority ON repair_queue(priority);
CREATE INDEX ix_rq_assignee ON repair_queue(assigned_to);

-- ---------------------------------------------------------------------
-- 9. AUDIT LOG (immutable, append-only, hash-chained)
-- ---------------------------------------------------------------------
CREATE TABLE audit_logs (
    id                  BIGSERIAL PRIMARY KEY,
    event_time          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id       INT REFERENCES users(id),     -- NULL = SYSTEM
    actor_username      VARCHAR(50)  NOT NULL DEFAULT 'SYSTEM',
    actor_role          VARCHAR(30),
    action              VARCHAR(40)  NOT NULL,        -- see PROJECT_CONTEXT.md §9 action catalogue
    entity_type         VARCHAR(30)  NOT NULL,        -- TRADE / REPAIR_QUEUE / MODEL / USER / CONFIG / AUTH / BATCH
    entity_id           VARCHAR(60),
    trade_pk            UUID,                         -- denormalised for fast timeline queries (no FK: logs outlive rows)
    trade_id            VARCHAR(30),
    queue_id            VARCHAR(20),
    trade_date          DATE,                         -- denormalised for FR-AT-002 date search
    settlement_date     DATE,
    risk_score          NUMERIC(5,4),
    status_after        VARCHAR(20),
    likely_reason       TEXT,
    recommended_fix     TEXT,
    applied_fix         TEXT,
    before_state        JSONB,
    after_state         JSONB,
    details             JSONB,
    ip_address          VARCHAR(45),
    prev_hash           CHAR(64),
    hash                CHAR(64) NOT NULL              -- sha256(prev_hash || canonical row json), computed by backend
);
CREATE INDEX ix_audit_trade_id ON audit_logs(trade_id);
CREATE INDEX ix_audit_time     ON audit_logs(event_time);
CREATE INDEX ix_audit_actor    ON audit_logs(actor_user_id);
CREATE INDEX ix_audit_action   ON audit_logs(action);

-- Immutability: block UPDATE / DELETE / TRUNCATE at DB level (FR-AT-005)
CREATE OR REPLACE FUNCTION audit_logs_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: % not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_immutable();

-- ---------------------------------------------------------------------
-- 10. CONFIG, NOTIFICATIONS, EMAIL OUTBOX, CHAT
-- ---------------------------------------------------------------------
CREATE TABLE system_config (
    key             VARCHAR(60) PRIMARY KEY,
    value           JSONB NOT NULL,
    description     VARCHAR(255),
    editable_by     VARCHAR(30) NOT NULL DEFAULT 'SYS_ADMIN',   -- SYS_ADMIN or ML_ADMIN
    updated_by      INT REFERENCES users(id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE notifications (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INT REFERENCES users(id),        -- either a specific user ...
    role_code       VARCHAR(30),                     -- ... or every user of a role (e.g. ML_ADMIN)
    type            VARCHAR(30) NOT NULL,            -- DRIFT_ALERT / ASSIGNMENT / REJECTION_REPORT / RETRAIN_DONE
    severity        VARCHAR(10) NOT NULL DEFAULT 'INFO' CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    title           VARCHAR(150) NOT NULL,
    message         TEXT NOT NULL,
    link            VARCHAR(255),
    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (user_id IS NOT NULL OR role_code IS NOT NULL)
);
CREATE INDEX ix_notif_user ON notifications(user_id, is_read);
CREATE INDEX ix_notif_role ON notifications(role_code, is_read);

CREATE TABLE email_outbox (
    id              BIGSERIAL PRIMARY KEY,
    to_addresses    TEXT NOT NULL,                    -- comma separated
    subject         VARCHAR(255) NOT NULL,
    body            TEXT NOT NULL,
    attachment_path VARCHAR(255),
    purpose         VARCHAR(30) NOT NULL,             -- REJECTION_REPORT / AUDIT_REPORT / DRIFT_ALERT
    status          email_status_enum NOT NULL DEFAULT 'PENDING',
    error_message   TEXT,
    created_by      INT REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ
);

CREATE TABLE chat_messages (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INT NOT NULL REFERENCES users(id),
    session_id      VARCHAR(50) NOT NULL,
    role            VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    tool_used       VARCHAR(50),                      -- which query tool answered it
    tool_result     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_chat_session ON chat_messages(session_id, created_at);

-- ---------------------------------------------------------------------
-- 11. OPTIONAL (Good-to-have): Vector store with pgvector
--     Run only if the pgvector extension is installed (Render/Supabase/Neon support it).
-- ---------------------------------------------------------------------
-- CREATE EXTENSION IF NOT EXISTS vector;
-- CREATE TABLE trade_embeddings (
--     trade_pk     UUID PRIMARY KEY REFERENCES trades(id) ON DELETE CASCADE,
--     content      TEXT NOT NULL,          -- "Trade T110003 SELL QCOM ... EXCEPTION because SSI not matched ..."
--     embedding    vector(384) NOT NULL,   -- all-MiniLM-L6-v2 = 384 dims
--     created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
-- );
-- CREATE INDEX ON trade_embeddings USING hnsw (embedding vector_cosine_ops);

"""Initial schema - runs schema.sql exactly as written.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-10

alembic/sql/0001_schema.sql is a byte-for-byte copy of the repo-root schema.sql
(tests/unit/test_schema_copy.py fails if they ever differ).
Later schema changes: add a NEW migration + update schema.sql + log it in PROJECT_CONTEXT section 17.
"""
from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "0001_schema.sql"

TABLES = [
    "chat_messages", "email_outbox", "notifications", "system_config", "audit_logs",
    "repair_queue", "drift_checks", "retraining_events", "model_evaluations", "ml_predictions",
    "rule_evaluations", "trade_versions", "trades", "model_versions", "upload_batches",
    "historical_trades", "holidays", "accounts", "symbols", "counterparties",
    "users", "role_permissions", "permissions", "roles",
]
ENUMS = [
    "trade_type_enum", "trade_status_enum", "priority_enum", "queue_status_enum", "trade_source_enum",
    "batch_status_enum", "model_status_enum", "retrain_status_enum", "email_status_enum",
]


def upgrade() -> None:
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    # Use the raw driver cursor with NO parameters: the file holds many statements and a
    # literal "%" (in the audit trigger), which a parameterised execute would misread.
    cursor = op.get_bind().connection.dbapi_connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_immutable() CASCADE")
    op.execute("DROP SEQUENCE IF EXISTS trade_id_seq")
    op.execute("DROP SEQUENCE IF EXISTS repair_queue_seq")
    for enum in ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {enum}")

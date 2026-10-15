from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_migration_sql_is_identical_to_root_schema_sql():
    root_schema = BACKEND_DIR.parent / "schema.sql"
    if not root_schema.exists():
        pytest.skip("repo-root schema.sql not visible here (e.g. inside the Docker container)")
    migration_copy = BACKEND_DIR / "alembic" / "sql" / "0001_schema.sql"
    normalise = lambda p: p.read_text(encoding="utf-8").replace("\r\n", "\n")  # noqa: E731
    assert normalise(migration_copy) == normalise(root_schema), (
        "schema.sql and alembic/sql/0001_schema.sql differ. Schema changes need a NEW Alembic migration."
    )

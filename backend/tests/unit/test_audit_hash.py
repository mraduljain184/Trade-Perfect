import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.services import audit_service

ROW = {
    "event_time": datetime(2026, 10, 10, 8, 15, 7, 123456, tzinfo=timezone.utc),
    "actor_username": "priya.sharma", "action": "LOGIN", "entity_type": "AUTH",
    "trade_pk": uuid.UUID("11111111-2222-3333-4444-555555555555"), "trade_date": date(2026, 10, 1),
    "risk_score": Decimal("0.8200"), "details": {"b": 1, "a": [1, 2]},
}


def test_hash_is_deterministic_and_64_hex_chars():
    first = audit_service.compute_hash(None, ROW)
    assert first == audit_service.compute_hash(None, dict(ROW))
    assert len(first) == 64 and int(first, 16) >= 0


def test_hash_changes_with_content_and_with_previous_hash():
    base = audit_service.compute_hash(None, ROW)
    assert audit_service.compute_hash(None, {**ROW, "action": "LOGOUT"}) != base
    assert audit_service.compute_hash("a" * 64, ROW) != base


def test_same_hash_for_python_values_and_values_read_back_from_db():
    as_read_from_db = {
        **ROW,
        "event_time": ROW["event_time"].astimezone(timezone(timedelta(hours=5, minutes=30))),  # same instant, IST
        "risk_score": Decimal("0.82"),
        "details": {"a": [1, 2], "b": 1},
    }
    assert audit_service.compute_hash(None, as_read_from_db) == audit_service.compute_hash(None, ROW)

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.models import AuditLog, User
from app.services import audit_service


def _last(db, n=1):
    return list(reversed(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(n)).all()))


@pytest.mark.parametrize("statement", [
    "UPDATE audit_logs SET action = 'HACKED'",
    "DELETE FROM audit_logs",
    "TRUNCATE audit_logs",
])
def test_database_blocks_update_delete_truncate(client, login, db, statement):
    login("sarah.chen")  # guarantees at least one audit row exists
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text(statement))


def test_log_links_each_row_to_the_previous_one(db):
    actor = db.scalar(select(User).where(User.username == "ops.manager"))
    audit_service.log(db, "CONFIG_CHANGED", "CONFIG", actor=actor, entity_id="batch_size",
                      before_state={"value": 10000}, after_state={"value": 5000}, risk_score=0.123456)
    audit_service.log(db, "EXPORT_CSV", "TRADE", details={"rows": 3})
    first, second = _last(db, 2)
    assert first.actor_username == "ops.manager" and first.actor_role == "OPS_MANAGER" and first.actor_user_id == actor.id
    assert float(first.risk_score) == 0.1235
    assert second.actor_username == "SYSTEM" and second.actor_user_id is None
    assert second.prev_hash == first.hash and second.hash != first.hash


def test_log_many_inserts_a_valid_chain_in_one_go(db):
    before = db.scalar(select(func.count()).select_from(AuditLog))
    entries = [{"action": "TRADE_SUBMITTED", "entity_type": "TRADE", "trade_id": f"T9{i:05d}", "status_after": "PASS"}
               for i in range(500)]
    assert audit_service.log_many(db, entries) == 500
    assert audit_service.log_many(db, []) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before + 500
    assert audit_service.verify_chain(db) == (True, None)


def test_verify_chain_is_valid_after_real_requests(client, login, db):
    client.post("/api/v1/auth/login", json={"username": "david.wilson", "password": "nope"})
    client.post("/api/v1/auth/logout", headers=login("david.wilson"))
    assert audit_service.verify_chain(db) == (True, None)


def test_verify_chain_detects_tampering(client, login, db):
    login("laura.martinez")
    audit_service.log(db, "EXPORT_CSV", "TRADE")
    audit_service.log(db, "EXPORT_CSV", "TRADE")
    victim = _last(db, 2)[0]
    # Only possible with the trigger switched off (a DBA-level attack); rolled back by the fixture.
    db.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER USER"))
    db.execute(text("UPDATE audit_logs SET actor_username = 'someone.else' WHERE id = :id"), {"id": victim.id})
    db.expire_all()
    assert audit_service.verify_chain(db) == (False, victim.id)


def test_unknown_action_or_field_is_refused(db):
    with pytest.raises(ValueError, match="Unknown audit action"):
        audit_service.log(db, "MADE_UP", "AUTH")
    with pytest.raises(ValueError, match="Unknown audit fields"):
        audit_service.log(db, "LOGIN", "AUTH", not_a_column=1)

"""The repair-queue transition table (PROJECT_CONTEXT section 7.1): every pair is checked, allowed or not."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.errors import ConflictError
from app.services.repair_queue_service import (
    ALLOWED_PRIORITIES, PRIORITIES, STATUSES, TRANSITIONS, next_status, reopen, why_not,
)

ACTIONS = ("ASSIGN", "UNASSIGN", "RESUBMIT", "BULK_RESUBMIT", "APPROVE", "REJECT", "REOPEN")
EXPECTED = {
    ("OPEN", "ASSIGN"): "IN_PROGRESS", ("IN_PROGRESS", "ASSIGN"): "IN_PROGRESS", ("IN_PROGRESS", "UNASSIGN"): "OPEN",
    ("OPEN", "RESUBMIT"): "RESOLVED", ("IN_PROGRESS", "RESUBMIT"): "RESOLVED",
    ("OPEN", "BULK_RESUBMIT"): "RESOLVED", ("IN_PROGRESS", "BULK_RESUBMIT"): "RESOLVED",
    ("OPEN", "APPROVE"): "RESOLVED", ("IN_PROGRESS", "APPROVE"): "RESOLVED",
    ("OPEN", "REJECT"): "REJECTED", ("IN_PROGRESS", "REJECT"): "REJECTED",
    ("RESOLVED", "REOPEN"): "OPEN",
}


def item(status, priority="LOW"):
    return SimpleNamespace(queue_id="RQ-000001", status=status, priority=priority)


def a_priority_for(action):
    return ALLOWED_PRIORITIES.get(action, PRIORITIES)[0]


def test_the_table_is_exactly_section_7_1():
    assert TRANSITIONS == EXPECTED


@pytest.mark.parametrize("status, action", [(s, a) for s in STATUSES for a in ACTIONS])
def test_every_status_action_pair(status, action):
    subject = item(status, a_priority_for(action))
    if (status, action) in EXPECTED:
        assert why_not(subject, action) is None
        assert next_status(subject, action) == EXPECTED[(status, action)]
    else:
        assert status in why_not(subject, action)
        with pytest.raises(ConflictError) as error:
            next_status(subject, action)
        assert error.value.status_code == 409


@pytest.mark.parametrize("status", ["RESOLVED", "REJECTED"])
def test_closed_items_allow_no_work_actions(status):
    for action in ("ASSIGN", "UNASSIGN", "RESUBMIT", "BULK_RESUBMIT", "APPROVE", "REJECT"):
        assert why_not(item(status, a_priority_for(action)), action)
    assert why_not(item("REJECTED"), "REOPEN")  # a rejected trade can never come back


@pytest.mark.parametrize("action, priority, allowed", [
    ("RESUBMIT", "HIGH", False), ("RESUBMIT", "MEDIUM", True), ("RESUBMIT", "LOW", True),
    ("BULK_RESUBMIT", "HIGH", False), ("BULK_RESUBMIT", "MEDIUM", False), ("BULK_RESUBMIT", "LOW", True),
    ("REJECT", "HIGH", True), ("REJECT", "MEDIUM", False), ("REJECT", "LOW", False),
    ("APPROVE", "HIGH", True), ("APPROVE", "MEDIUM", False), ("APPROVE", "LOW", False),
    ("ASSIGN", "HIGH", True), ("ASSIGN", "MEDIUM", True), ("ASSIGN", "LOW", True),
])
def test_priority_rules(action, priority, allowed):
    reason = why_not(item("OPEN", priority), action)
    assert (reason is None) is allowed
    if not allowed:
        assert priority in reason


def test_reopen_clears_the_previous_work():
    now = datetime.now(timezone.utc)
    closed = SimpleNamespace(queue_id="RQ-000009", status="RESOLVED", priority="LOW", suggested_fixes=None, updated_at=None,
                             assigned_to=7, assigned_by=3, assigned_at=now, applied_fix="x", applied_by=7, applied_at=now,
                             resolution_note="done", resolved_by=7, resolved_at=now)
    reopen(closed, "HIGH", [{"factor": "SSI", "fix": "confirm"}], now)
    assert (closed.status, closed.priority, closed.updated_at) == ("OPEN", "HIGH", now)
    assert closed.assigned_to is None and closed.applied_fix is None and closed.resolved_at is None
    assert closed.suggested_fixes == [{"factor": "SSI", "fix": "confirm"}]

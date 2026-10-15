import pytest

from app.engine import validate_trade
from app.engine.hard_gates import run_hard_gates
from app.engine.normalize import normalize_records

from .helpers import BUSINESS_DATE, make_refs, make_trade


def gate_errors(records, refs, **kw):
    return run_hard_gates(normalize_records(records), refs, **kw).errors


def test_clean_trade_passes_all_gates(refs):
    assert gate_errors([make_trade()], refs) == [[]]


@pytest.mark.parametrize("override, rule_id", [
    ({"symbol": ""}, "R-000"),
    ({"trader_name": " "}, "R-000"),
    ({"ssi_matched": ""}, "R-000"),
    ({"ssi_matched": "maybe"}, "R-000"),
    ({"trade_id": ""}, "R-001"),
    ({"trade_type": ""}, "R-002"),
    ({"trade_type": "HOLD"}, "R-002"),
    ({"quantity": "0"}, "R-003"),
    ({"quantity": "-5"}, "R-003"),
    ({"quantity": "1.5"}, "R-003"),
    ({"price": "0"}, "R-004"),
    ({"price": "abc"}, "R-004"),
    ({"symbol": "ISRG"}, "R-005"),
    ({"counterparty": "ABC Bank"}, "R-006"),
    ({"account_id": "ACC1011"}, "R-007"),          # inactive
    ({"account_id": "ACC9999"}, "R-007"),          # unknown
    ({"settlement_date": "10/1/2026"}, "R-008"),   # before trade date
    ({"trade_date": "31/12/2026"}, "R-008"),       # unparseable
])
def test_each_gate_rejects(refs, override, rule_id):
    errs = gate_errors([make_trade(**override)], refs)[0]
    assert [e.rule_id for e in errs] == [rule_id]


def test_all_errors_returned_at_once(refs):
    t = make_trade(trade_type="HOLD", quantity="0", symbol="ISRG", counterparty="X", account_id="ACC1011")
    rules = {e.rule_id for e in gate_errors([t], refs)[0]}
    assert rules == {"R-002", "R-003", "R-005", "R-006", "R-007"}


def test_duplicate_in_batch_first_occurrence_wins(refs):
    errs = gate_errors([make_trade(trade_id="T9"), make_trade(trade_id="t9")], refs)
    assert errs[0] == [] and [e.rule_id for e in errs[1]] == ["R-001"]


def test_existing_trade_id_rejected_and_amendment_allowed():
    existing = make_refs(existing_trade_ids={"T1"})
    assert [e.rule_id for e in gate_errors([make_trade()], existing)[0]] == ["R-001"]
    amend = make_refs(existing_trade_ids=set())          # backend removes the trade's own id
    assert gate_errors([make_trade()], amend)[0] == []


def test_case_insensitive_counterparty_returns_canonical_spelling(refs):
    g = run_hard_gates(normalize_records([make_trade(counterparty="  ubs ")]), refs)
    assert g.errors[0] == [] and g.counterparty_canonical[0] == "UBS"


def test_any_trader_name_is_accepted(refs):
    # new traders can register at any time, so the name is required (R-000) but not checked against a list
    assert gate_errors([make_trade(trader_name="New Trader Joined Today")], refs)[0] == []


def test_validate_trade_new_ui_trade_without_id(refs, cfg):
    rec = make_trade()
    del rec["Trade ID"]
    res = validate_trade(rec, refs, cfg, BUSINESS_DATE)
    assert res.valid and res.errors == []


def test_validate_trade_returns_field_errors(refs, cfg):
    res = validate_trade(make_trade(symbol="ISRG", quantity="1.5"), refs, cfg, BUSINESS_DATE)
    assert not res.valid
    assert {(e.field, e.rule_id) for e in res.errors} == {("symbol", "R-005"), ("quantity", "R-003")}


def test_validate_trade_weekend_warning(refs, cfg):
    res = validate_trade(make_trade(settlement_date="10/10/2026"), refs, cfg, BUSINESS_DATE)   # Saturday
    assert res.valid and any("Saturday" in w for w in res.warnings)
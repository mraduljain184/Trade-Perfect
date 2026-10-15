"""BE-8: upload the generated edge-case file (tests/generated/edge_cases.csv, from ML-5) through the
API and compare every stored row with tests/generated/expected_results.json.

This checks the whole path CSV -> upload -> engine -> database, not just the engine.
The expected results are for rules-only scoring at business date 2026-09-28.
"""
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import RepairQueueItem, SystemConfig, Trade, UploadBatch
from app.services import engine_adapter, upload_service

GENERATED = Path(__file__).resolve().parents[1] / "generated"
CSV_FILE, EXPECTED_FILE = GENERATED / "edge_cases.csv", GENERATED / "expected_results.json"
UPLOADER = "david.wilson"

pytestmark = pytest.mark.skipif(not CSV_FILE.is_file() or not EXPECTED_FILE.is_file(),
                                reason="generated edge-case files are missing (run scripts/generate_test_cases.py)")
EXPECTED = json.loads(EXPECTED_FILE.read_text(encoding="utf-8")) if EXPECTED_FILE.is_file() else {"rows": [], "summary": {}}


@pytest.fixture(scope="module")
def stored(client, login, _database, tmp_path_factory):
    """Upload the file once; returns {row index: Trade}."""
    from app.core.database import SessionLocal

    def business_date(value):
        with SessionLocal() as session:
            session.get(SystemConfig, "business_date_override").value = value
            session.commit()
        engine_adapter.invalidate_cache()

    assert engine_adapter.get_predictor() is None, "these expected results are rules-only"
    business_date(EXPECTED["business_date"])
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(upload_service, "REPORT_DIR", tmp_path_factory.mktemp("reports"))
            response = client.post("/api/v1/trades/bulk", files={"file": ("edge_cases.csv", CSV_FILE.read_bytes(), "text/csv")},
                                   headers=login(UPLOADER))
        assert response.status_code == 202, response.text
        batch_id = response.json()["batchId"]
        progress = client.get(f"/api/v1/uploads/{batch_id}", headers=login(UPLOADER)).json()
        assert progress["status"] == "COMPLETED", progress
    finally:
        business_date(None)
    with SessionLocal() as session:
        pk = session.scalar(select(UploadBatch.id).where(UploadBatch.batch_id == batch_id))
        trades = session.scalars(select(Trade).where(Trade.batch_id == pk)).all()
        queued = set(session.scalars(select(RepairQueueItem.trade_pk).where(RepairQueueItem.trade_pk.in_([t.id for t in trades]))))
    return {"progress": progress, "rows": {t.batch_row_number - 1: t for t in trades}, "queued": queued}


def test_summary_matches(stored):
    summary = stored["progress"]["summary"]
    expected = EXPECTED["summary"]
    assert stored["progress"]["totalRows"] == len(EXPECTED["rows"]) == len(stored["rows"])
    assert {"PASS": summary["pass"], "MONITOR": summary["monitor"], "EXCEPTION": summary["exception"],
            "REJECTED": summary["rejected"]} == expected
    assert dict(Counter(t.status for t in stored["rows"].values())) == {k: v for k, v in expected.items() if v}


@pytest.mark.parametrize("row", EXPECTED["rows"], ids=[f"{r['row']}-{r['description']}" for r in EXPECTED["rows"]])
def test_edge_case_row_is_stored_as_expected(stored, row):
    trade = stored["rows"][row["row"]]
    assert trade.status == row["expected_status"]
    if row["expected_status"] == "REJECTED":
        assert {r["ruleId"] for r in trade.rejection_reasons} == set(row["expected_rejection_rules"])
        assert float(trade.final_score) == 1.0 and trade.priority is None and trade.id not in stored["queued"]
    else:
        assert float(trade.rule_score) == pytest.approx(row["expected_rule_score"], abs=1e-4)
        assert float(trade.final_score) == pytest.approx(row["expected_rule_score"], abs=1e-4)   # rules-only: final = rule
        assert (trade.id in stored["queued"]) == (float(trade.final_score) > 0)                  # queue only when score > 0
        assert trade.trade_id == row["trade_id"] and isinstance(trade.trade_date, date)

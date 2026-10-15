import csv
import io

from app.services import export_service


def test_safe_cell_formats_values_and_blocks_spreadsheet_formulas():
    assert export_service.safe_cell(None) == ""
    assert export_service.safe_cell(True) == "YES" and export_service.safe_cell(False) == "NO"
    assert export_service.safe_cell(-5) == "-5" and export_service.safe_cell(0.75) == "0.75"
    assert export_service.safe_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert export_service.safe_cell("@cmd") == "'@cmd" and export_service.safe_cell("E*TRADE") == "E*TRADE"


def test_csv_chunks_keep_every_row_and_quote_commas():
    rows = [[f"T{i}", "Goldman Sachs, Inc", i] for i in range(1203)]
    chunks = list(export_service.to_csv_chunks(["Trade ID", "Counterparty", "N"], rows, chunk_rows=500))
    assert len(chunks) == 3
    parsed = list(csv.reader(io.StringIO("".join(chunks))))
    assert parsed[0] == ["Trade ID", "Counterparty", "N"] and len(parsed) == 1204
    assert parsed[-1] == ["T1202", "Goldman Sachs, Inc", "1202"]


def test_empty_export_still_has_the_header():
    assert "".join(export_service.to_csv_chunks(export_service.TRADE_COLUMNS, [])).strip().split(",")[0] == "Trade ID"

from datetime import date

import pytest

from app.engine.normalize import (canonical_header, normalize_record, parse_bool, parse_date,
                                  parse_int, read_trade_csv)


@pytest.mark.parametrize("header", ["Trade ID", "tradeId", "trade_id", "\ufeffTrade ID", " TRADE id "])
def test_header_variants_map_to_trade_id(header):
    assert canonical_header(header) == "trade_id"


@pytest.mark.parametrize("raw, expected", [
    (" yes ", True), ("Y", True), ("true", True), ("1", True), (True, True),
    ("NO", False), ("n", False), ("0", False), ("FALSE", False), (False, False),
])
def test_parse_bool_valid(raw, expected):
    assert parse_bool(raw) == (expected, None)


def test_parse_bool_invalid_and_blank():
    assert parse_bool("maybe")[0] is None and parse_bool("maybe")[1]
    assert parse_bool("  ") == (None, None)


@pytest.mark.parametrize("raw", ["9/11/2026", "2026-09-11", date(2026, 9, 11), "09/11/2026"])
def test_parse_date_formats(raw):
    assert parse_date(raw) == (date(2026, 9, 11), None)


def test_parse_date_invalid():
    value, err = parse_date("31/12/2026")
    assert value is None and "not a valid date" in err


@pytest.mark.parametrize("raw, expected", [("100", 100), ("100.0", 100), ("5,291", 5291), (7, 7)])
def test_parse_int_valid(raw, expected):
    assert parse_int(raw) == (expected, None)


@pytest.mark.parametrize("raw", ["100.5", "abc", True])
def test_parse_int_invalid(raw):
    assert parse_int(raw)[0] is None and parse_int(raw)[1]


def test_normalize_record_cleans_and_reports_errors():
    clean, errs = normalize_record({"tradeType": " buy ", "Symbol": "aapl", "Quantity": "100.5",
                                    "SSI Matched": " yes ", "accountId": "acc1000"})
    assert clean["trade_type"] == "BUY" and clean["symbol"] == "AAPL" and clean["account_id"] == "ACC1000"
    assert clean["ssi_matched"] is True and clean["quantity"] is None
    assert [(e.field, e.rule_id) for e in errs] == [("quantity", "R-003")]


def test_read_csv_with_bom(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_text("Trade ID,Symbol\nT1,AAPL\n", encoding="utf-8-sig")
    rows = read_trade_csv(str(p))
    assert rows == [{"Trade ID": "T1", "Symbol": "AAPL"}]
"""Turn raw trade records (any header style, string or typed values) into clean,
typed, canonical columns. Shared by UI, file upload, training and the CLI.

Design rule: normalize NEVER decides if a trade is acceptable. It only
  - maps headers to canonical names,
  - parses values (blank -> None),
  - reports values that are present but malformed (parse_errors).
Business checks (blank mandatory fields, master data, duplicates) live in hard_gates.py.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd

from .types import FieldError

# ---- header mapping --------------------------------------------------------
CANONICAL_COLUMNS: list[str] = [
    "trade_id", "trade_type", "symbol", "quantity", "price", "counterparty",
    "trade_date", "settlement_date", "account_id", "ssi_matched",
    "currency_matched", "trader_name",
]
LABEL_COLUMN = "settlement_failed"

# camelCase names used in API messages (FieldError.field) -> matches §8.2 TradeInput
API_FIELD_NAMES: dict[str, str] = {
    "trade_id": "tradeId", "trade_type": "tradeType", "symbol": "symbol",
    "quantity": "quantity", "price": "price", "counterparty": "counterparty",
    "trade_date": "tradeDate", "settlement_date": "settlementDate",
    "account_id": "accountId", "ssi_matched": "ssiMatched",
    "currency_matched": "currencyMatched", "trader_name": "traderName",
    "settlement_failed": "settlementFailed",
}


def _header_key(h: str) -> str:
    """'Trade ID' / 'tradeId' / 'trade_id' / '\\ufeffTrade ID' -> 'tradeid'."""
    return re.sub(r"[\s_\-]", "", str(h).replace("\ufeff", "")).lower()


_HEADER_LOOKUP: dict[str, str] = {
    _header_key(c): c for c in CANONICAL_COLUMNS + [LABEL_COLUMN]
}


def canonical_header(h: str) -> Optional[str]:
    return _HEADER_LOOKUP.get(_header_key(h))


# ---- value parsers (return (value, error_message_or_None)) -----------------
_TRUE = {"YES", "Y", "TRUE", "1", "T"}
_FALSE = {"NO", "N", "FALSE", "0", "F"}
_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d")
_date_cache: dict[str, Optional[date]] = {}


def is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if v is pd.NaT:
        return True
    return isinstance(v, str) and v.strip() == ""


def parse_str(v: Any, upper: bool = False) -> Optional[str]:
    if is_blank(v):
        return None
    s = str(v).strip()
    return s.upper() if upper else s


def parse_bool(v: Any) -> tuple[Optional[bool], Optional[str]]:
    if is_blank(v):
        return None, None
    if isinstance(v, bool):
        return v, None
    s = str(v).strip().upper()
    if s.endswith(".0"):                     # "1.0" from Excel
        s = s[:-2]
    if s in _TRUE:
        return True, None
    if s in _FALSE:
        return False, None
    return None, "must be YES or NO"


def parse_date(v: Any) -> tuple[Optional[date], Optional[str]]:
    if is_blank(v):
        return None, None
    if isinstance(v, datetime):              # includes pandas.Timestamp
        return v.date(), None
    if isinstance(v, date):
        return v, None
    s = str(v).strip()
    if s in _date_cache:
        d = _date_cache[s]
    else:
        d = None
        for fmt in _DATE_FORMATS:
            try:
                d = datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
                break
            except ValueError:
                continue
        _date_cache[s] = d
    return (d, None) if d else (None, f"'{s}' is not a valid date (use M/D/YYYY or YYYY-MM-DD)")


def parse_int(v: Any) -> tuple[Optional[int], Optional[str]]:
    """Whole numbers only: '100' / '100.0' / '5,291' OK; '100.5' / 'abc' -> error."""
    if is_blank(v):
        return None, None
    if isinstance(v, bool):
        return None, "must be a positive whole number"
    try:
        f = float(str(v).strip().replace(",", ""))
    except ValueError:
        return None, "must be a positive whole number"
    if not math.isfinite(f) or f != int(f):
        return None, "must be a positive whole number"
    return int(f), None


def parse_float(v: Any) -> tuple[Optional[float], Optional[str]]:
    if is_blank(v):
        return None, None
    if isinstance(v, bool):
        return None, "must be a number"
    try:
        f = float(str(v).strip().replace(",", ""))
    except ValueError:
        return None, "must be a number"
    if not math.isfinite(f):
        return None, "must be a number"
    return f, None


# field -> (parser, rule_id used if the value is present but malformed)
_PARSERS = {
    "quantity": (parse_int, "R-003"),
    "price": (parse_float, "R-004"),
    "trade_date": (parse_date, "R-008"),
    "settlement_date": (parse_date, "R-008"),
    "ssi_matched": (parse_bool, "R-000"),
    "currency_matched": (parse_bool, "R-000"),
    "settlement_failed": (parse_bool, "R-000"),
}
_UPPER_FIELDS = {"trade_type", "symbol", "account_id"}


# ---- record / batch normalisation ------------------------------------------
def canonicalize_record(record: dict) -> dict:
    """Rename keys to canonical names; unknown keys are dropped."""
    out: dict[str, Any] = {}
    for k, v in record.items():
        c = canonical_header(k)
        if c is not None and c not in out:   # first matching header wins
            out[c] = v
    return out


def normalize_record(record: dict) -> tuple[dict, list[FieldError]]:
    """One raw record -> (typed canonical dict with every column present, parse errors)."""
    rec = canonicalize_record(record)
    clean: dict[str, Any] = {}
    errors: list[FieldError] = []
    for col in CANONICAL_COLUMNS + [LABEL_COLUMN]:
        if col == LABEL_COLUMN and col not in rec:
            continue
        v = rec.get(col)
        if col in _PARSERS:
            parser, rule_id = _PARSERS[col]
            value, msg = parser(v)
            if msg:
                errors.append(FieldError(API_FIELD_NAMES[col], rule_id, f"{API_FIELD_NAMES[col]} {msg}"))
            clean[col] = value
        else:
            clean[col] = parse_str(v, upper=col in _UPPER_FIELDS)
    return clean, errors


@dataclass
class NormalizedBatch:
    df: pd.DataFrame                       # canonical typed columns, index 0..n-1
    parse_errors: list[list[FieldError]]   # per row, same order as input
    raw: list[dict]                        # original records (-> trades.raw_payload)
    present_columns: set[str]              # canonical columns that appeared in the input headers


def normalize_records(records: list[dict]) -> NormalizedBatch:
    rows, errs, present = [], [], set()
    for r in records:
        clean, e = normalize_record(r)
        rows.append(clean)
        errs.append(e)
        present.update(c for c in (canonical_header(k) for k in r.keys()) if c)
    df = pd.DataFrame(rows, columns=CANONICAL_COLUMNS + ([LABEL_COLUMN] if LABEL_COLUMN in present else []))
    for col in df.columns:
        if col == "quantity":
            df[col] = df[col].astype("Int64")            # nullable int (<NA> if missing)
        elif col == "price":
            df[col] = df[col].astype("Float64")          # nullable float
        else:                                            # str / date / bool -> object, missing = None
            df[col] = df[col].astype(object).where(df[col].notna(), None)
    return NormalizedBatch(df=df, parse_errors=errs, raw=[dict(r) for r in records], present_columns=present)


def read_trade_csv(path: str) -> list[dict]:
    """Read any trade CSV as raw string records (BOM-safe, nothing auto-converted)."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return df.to_dict(orient="records")
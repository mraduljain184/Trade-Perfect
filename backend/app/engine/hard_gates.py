"""Hard gates R-000..R-008 (PROJECT_CONTEXT §6.2).

A trade that fails ANY gate is REJECTED (final_score = 1.0) and never scored.
We evaluate EVERY gate and return EVERY error, so the user can fix all of them at once (FR-TB-006).

Which rule a blank field gets:
  tradeId blank   -> R-001  (spec: "tradeId empty or duplicate")
  tradeType blank -> R-002  (spec: "not BUY/SELL"; the 20K trap T110188 must count as R-002)
  any other blank -> R-000  ("<field> is required")
traderName is mandatory (R-000) but NOT checked against a list: new traders can register at any time.
If a field is blank or malformed, its later checks are skipped (one clear error per field).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .normalize import API_FIELD_NAMES, NormalizedBatch
from .types import FieldError, ReferenceData

VALID_TRADE_TYPES = {"BUY", "SELL"}

# Mandatory fields (R-000). trade_id is handled by R-001, trade_type by R-002.
MANDATORY_FIELDS = [
    "symbol", "quantity", "price", "counterparty", "trade_date", "settlement_date",
    "account_id", "trader_name", "ssi_matched", "currency_matched",
]

# Every gate, for docs / test generation (ML-5) / the chatbot.
HARD_GATES: dict[str, str] = {
    "R-000": "Mandatory field missing",
    "R-001": "Trade ID empty or duplicate",
    "R-002": "Trade type must be BUY or SELL",
    "R-003": "Quantity must be a positive whole number",
    "R-004": "Price must be a positive number",
    "R-005": "Symbol not in master data",
    "R-006": "Counterparty not in master data",
    "R-007": "Account not found or inactive",
    "R-008": "Invalid dates / settlement before trade date",
}


@dataclass
class GateResult:
    errors: list[list[FieldError]]        # per row, same order as input (empty list = passed)
    counterparty_canonical: list[Optional[str]]  # master spelling ("ubs" -> "UBS"), None if unknown

    @property
    def rejected(self) -> list[bool]:
        return [bool(e) for e in self.errors]


def _missing(v) -> bool:
    return v is None or v is pd.NA or (isinstance(v, float) and v != v)


def run_hard_gates(batch: NormalizedBatch, refs: ReferenceData, require_trade_id: bool = True) -> GateResult:
    """Apply R-000..R-008 to a normalized batch.

    require_trade_id=False is used only by validate_trade for a NEW UI trade
    (the server generates the ID afterwards, FR-TB-008)."""
    df = batch.df
    existing_ids = {str(t).strip().upper() for t in refs.existing_trade_ids}
    seen_ids: set[str] = set()
    all_errors: list[list[FieldError]] = []
    cp_canonical: list[Optional[str]] = []

    for i, row in enumerate(df.itertuples(index=False)):
        errs: list[FieldError] = list(batch.parse_errors[i])          # malformed values from normalize
        bad_fields = {e.field for e in errs}
        F = API_FIELD_NAMES

        def add(field: str, rule: str, msg: str) -> None:
            errs.append(FieldError(F[field], rule, msg))
            bad_fields.add(F[field])

        # --- R-000: mandatory fields present ------------------------------------
        for f in MANDATORY_FIELDS:
            if F[f] not in bad_fields and _missing(getattr(row, f)):
                add(f, "R-000", f"{F[f]} is required")

        # --- R-001: trade id present and unique ---------------------------------
        tid = row.trade_id
        if _missing(tid):
            if require_trade_id:
                add("trade_id", "R-001", "tradeId is required")
        else:
            key = str(tid).strip().upper()
            if key in seen_ids:
                add("trade_id", "R-001", f"Duplicate tradeId {tid} (already in this file)")
            elif key in existing_ids:
                add("trade_id", "R-001", f"Duplicate tradeId {tid} (already exists)")
            seen_ids.add(key)                                         # first occurrence wins

        # --- R-002: BUY / SELL ---------------------------------------------------
        if _missing(row.trade_type):
            add("trade_type", "R-002", "tradeType is required (BUY or SELL)")
        elif row.trade_type not in VALID_TRADE_TYPES:
            add("trade_type", "R-002", f"tradeType must be BUY or SELL (got '{row.trade_type}')")

        # --- R-003 / R-004: positive numbers -------------------------------------
        if F["quantity"] not in bad_fields and row.quantity <= 0:
            add("quantity", "R-003", "quantity must be a positive whole number")
        if F["price"] not in bad_fields and row.price <= 0:
            add("price", "R-004", "price must be greater than 0")

        # --- R-005: symbol in master ----------------------------------------------
        if F["symbol"] not in bad_fields and row.symbol not in refs.symbols:
            add("symbol", "R-005", f"Symbol '{row.symbol}' not found in master data")

        # --- R-006: counterparty in master (case-insensitive) ------------------------
        canonical = None
        if F["counterparty"] not in bad_fields:
            ref = refs.counterparties.get(str(row.counterparty).lower())
            if ref is None:
                add("counterparty", "R-006", f"Counterparty '{row.counterparty}' not found")
            else:
                canonical = ref.name
        cp_canonical.append(canonical)

        # --- R-007: account exists and is active -----------------------------------
        if F["account_id"] not in bad_fields:
            active = refs.accounts.get(row.account_id)
            if active is None:
                add("account_id", "R-007", f"Account {row.account_id} not found")
            elif not active:
                add("account_id", "R-007", f"Account {row.account_id} is inactive")

        # --- R-008: settlement on/after trade date ---------------------------------
        if (F["trade_date"] not in bad_fields and F["settlement_date"] not in bad_fields
                and row.settlement_date < row.trade_date):
            add("settlement_date", "R-008", "settlementDate cannot be before tradeDate")

        all_errors.append(errs)

    return GateResult(errors=all_errors, counterparty_canonical=cp_canonical)
"""Fix catalogue (PROJECT_CONTEXT §6.6): what Ops should do for each risk factor."""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from .calendar import next_business_day

FIX_CATALOGUE: dict[str, str] = {
    "SSI": "Re-confirm Standard Settlement Instructions with the counterparty; update SSI in the "
           "booking and set SSI Matched = YES once confirmed.",
    "CURRENCY": "Align settlement currency with the counterparty's instruction / book FX; "
                "set Currency Matched = YES after confirmation.",
    "SETTLEMENT_DATE_PAST": "Settlement date is in the past - amend to the next valid business day: {date}.",
    "SETTLEMENT_DATE_WKND": "Move settlement date to the next valid business day: {date}.",
    "SETTLEMENT_DATE_FAR": "Settlement > {days} days out - verify instruction is not stale with trader/counterparty.",
    "COUNTERPARTY": "Tier 3/4 counterparty - pre-match trade details with the counterparty and escalate "
                    "to Ops Manager for monitoring.",
    "COUNTERPARTY_LOW": "Tier 2 counterparty - pre-match trade details with the counterparty before settlement.",
    "COUNTERPARTY_PATTERN": "{cp} has a higher historical failure rate on similar trades - pre-match trade "
                            "details with the counterparty before settlement.",
    "SETTLEMENT_DATE_PATTERN": "Settlement timing (day of week / month end / days to settle) failed more often "
                               "historically - confirm the settlement date and instructions with the counterparty.",
    "QUANTITY_HIGH": "Quantity exceeds limit {limit:,} - verify with trader for fat-finger error or split the order.",
    "QUANTITY_LOW": "Quantity below minimum lot {limit:,} - verify order size with trader.",
    "PRICE": "Price deviates strongly from historical range for the symbol - verify price with market data/trader.",
    "PATTERN": "Historical pattern risk - review similar past failures for this counterparty/symbol.",
}

# Short verb phrases used inside the one-sentence explanation
SHORT_FIX: dict[str, str] = {
    "SSI": "re-confirm SSI with {cp}",
    "CURRENCY": "correct the settlement currency",
    "SETTLEMENT_DATE_PAST": "amend settlement date to {date}",
    "SETTLEMENT_DATE_WKND": "move settlement date to {date}",
    "SETTLEMENT_DATE_FAR": "verify the settlement date with the trader",
    "COUNTERPARTY": "pre-match trade details with {cp}",
    "COUNTERPARTY_LOW": "pre-match trade details with {cp}",
    "COUNTERPARTY_PATTERN": "pre-match trade details with {cp}",
    "SETTLEMENT_DATE_PATTERN": "confirm the settlement date with {cp}",
    "QUANTITY_HIGH": "verify quantity with the trader",
    "QUANTITY_LOW": "verify quantity with the trader",
    "PRICE": "verify price with market data",
    "PATTERN": "review similar past failures",
}


def fix_key(factor: str, triggered_rules: Iterable[str], tier: int = 0) -> str:
    """Factor group + which rules fired -> catalogue key."""
    rules = set(triggered_rules)
    if factor == "SETTLEMENT_DATE":
        if "S-SD-PAST" in rules:
            return "SETTLEMENT_DATE_PAST"
        if "S-SD-WKND" in rules:
            return "SETTLEMENT_DATE_WKND"
        if "S-SD-FAR" in rules:
            return "SETTLEMENT_DATE_FAR"
        return "SETTLEMENT_DATE_PATTERN"          # only the ML flagged settlement timing
    if factor == "QUANTITY":
        return "QUANTITY_LOW" if "S-QTY-LOW" in rules else "QUANTITY_HIGH"
    if factor == "COUNTERPARTY":
        if tier >= 3:
            return "COUNTERPARTY"
        if tier == 2:
            return "COUNTERPARTY_LOW"
        return "COUNTERPARTY_PATTERN"             # tier 1: only the ML flagged this counterparty
    return factor


def fix_date(key: str, settlement_date: Optional[date], business_date: Optional[date], holidays) -> Optional[date]:
    """The concrete date to suggest for past / weekend settlement dates."""
    if key == "SETTLEMENT_DATE_PAST" and business_date:
        return next_business_day(business_date, holidays)
    if key == "SETTLEMENT_DATE_WKND" and settlement_date:
        return next_business_day(settlement_date, holidays)
    return None


def render_fix(key: str, *, short: bool = False, cp: str = "", d: Optional[date] = None,
               qty_min: int = 100, qty_max: int = 5000, far_days: int = 15) -> str:
    template = (SHORT_FIX if short else FIX_CATALOGUE)[key]
    limit = qty_min if key == "QUANTITY_LOW" else qty_max
    return template.format(cp=cp, date=d.isoformat() if d else "the next business day",
                           limit=limit, days=far_days)
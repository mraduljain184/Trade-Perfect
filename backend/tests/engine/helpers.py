"""Shared test helpers: a small hand-made reference set and a 'clean' trade builder.

The clean trade scores 0.0: SSI/currency matched, Tier-1 counterparty (UBS),
quantity inside 100..5000, settles on a Wednesday 2 days after trade date.
"""
from datetime import date

from app.engine.types import CounterpartyRef, ReferenceData, SymbolStats

BUSINESS_DATE = date(2026, 10, 1)          # Thursday, before every trade date below
HOLIDAY = date(2026, 10, 2)                # a Friday we declare a holiday


def make_refs(**overrides) -> ReferenceData:
    cps = {
        "UBS": (1, "Tier1_HighTrust", 355),
        "State Street": (2, "Tier2_MediumTrust", 984),
        "Vanguard": (3, "Tier3_ElevatedRisk", 1005),
        "BlackRock": (4, "Tier4_HighestRisk", 2049),
    }
    refs = dict(
        symbols={"AAPL", "MSFT", "ISRG1"},
        counterparties={
            n.lower(): CounterpartyRef(name=n, risk_tier=t, tier_label=l, fail_count=f,
                                       trade_count=3 * f, fail_rate=0.33)
            for n, (t, l, f) in cps.items()
        },
        accounts={"ACC1000": True, "ACC1001": True, "ACC1011": False},
        symbol_stats={
            "AAPL": SymbolStats(price_mean=100.0, price_std=10.0, qty_mean=2500.0, qty_std=1000.0, fail_rate=0.6),
            "MSFT": SymbolStats(price_mean=200.0, price_std=20.0, qty_mean=2500.0, qty_std=1000.0, fail_rate=0.7),
        },
        holidays={HOLIDAY},
        existing_trade_ids=set(),
    )
    refs.update(overrides)
    return ReferenceData(**refs)


def make_trade(**overrides) -> dict:
    """Dataset-style headers (as in the CSV). Override with canonical names, e.g. quantity='0'."""
    base = {
        "trade_id": "T1", "trade_type": "BUY", "symbol": "AAPL", "quantity": "1000", "price": "100",
        "counterparty": "UBS", "trade_date": "10/5/2026", "settlement_date": "10/7/2026",
        "account_id": "ACC1000", "ssi_matched": "YES", "currency_matched": "YES", "trader_name": "Wei Zhang",
    }
    base.update(overrides)
    headers = {
        "trade_id": "Trade ID", "trade_type": "Trade Type", "symbol": "Symbol", "quantity": "Quantity",
        "price": "Price", "counterparty": "Counterparty", "trade_date": "Trade Date",
        "settlement_date": "Settlement Date", "account_id": "Account ID", "ssi_matched": "SSI Matched",
        "currency_matched": "Currency Matched", "trader_name": "Trader Name",
    }
    return {headers[k]: v for k, v in base.items()}
import sys
import types
from dataclasses import dataclass

from sqlalchemy import func, select, text

from app.models import (
    Account, Counterparty, HistoricalTrade, Holiday, Permission, Role, RolePermission, Symbol, SystemConfig, User,
)
from scripts import seed, seed_data
from tests.conftest import TEST_PASSWORD

# PROJECT_CONTEXT section 2.3
EXPECTED_TIERS = {
    4: {"BlackRock": 2049, "Raymond James": 1888, "PJT Partners": 1866, "Deutsche Bank": 1823, "Credit Suisse": 1687},
    3: {"Northern Trust": 1496, "Charles Schwab": 1411, "Capital Group": 1314, "Morgan Stanley": 1257,
        "Tokyo Marine": 1248, "Bank of America": 1220, "Barclays Wealth": 1133, "Commerzbank": 1123, "Vanguard": 1005},
    2: {"State Street": 984, "Moelis": 940, "Evercore": 915, "Lazard": 846, "Nomura": 831, "E*TRADE": 808,
        "Goldman Sachs": 744, "Fidelity": 684, "BofA Securities": 600, "Santander": 596,
        "Interactive Brokers": 548, "HSBC": 501},
    1: {"Jefferies": 485, "JPMorgan Chase": 475, "Standard Chartered": 452, "Societe Generale": 438, "Barclays": 383,
        "UBS": 355, "Centerview": 313, "Citigroup": 303, "Goldman Sachs Asset Management": 207, "Mizuho": 174,
        "ING Group": 161, "Royal Bank of Scotland": 116},
}
LABELS = {4: "Tier4_HighestRisk", 3: "Tier3_ElevatedRisk", 2: "Tier2_MediumTrust", 1: "Tier1_HighTrust"}
MODELS = [Role, Permission, RolePermission, User, Symbol, Counterparty, Account, HistoricalTrade, SystemConfig, Holiday]


def _counts(db):
    return {m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in MODELS}


def test_row_counts(db):
    counts = _counts(db)
    assert counts["roles"] == 5 and counts["permissions"] == 28 and counts["users"] >= 21  # admin tests add users
    assert counts["symbols"] == 140 and counts["counterparties"] == 38 and counts["accounts"] == 100
    assert counts["historical_trades"] == 50000
    assert counts["system_config"] == len(seed_data.SYSTEM_CONFIG) == 14
    assert counts["role_permissions"] == sum(len(g) for _, _, g in seed_data.PERMISSIONS)


def test_counterparty_tiers_match_project_context(db):
    actual = {c.name: c for c in db.scalars(select(Counterparty)).all()}
    assert sum(len(v) for v in EXPECTED_TIERS.values()) == len(actual) == 38
    for tier, members in EXPECTED_TIERS.items():
        for name, fail_count in members.items():
            row = actual[name]
            assert (row.risk_tier, row.historical_fail_count, row.tier_label) == (tier, fail_count, LABELS[tier]), name
            assert 0 < row.historical_fail_rate < 1 and row.historical_trade_count >= fail_count


def test_reference_data_details(db):
    inactive = set(db.scalars(select(Account.account_id).where(Account.is_active.is_(False))).all())
    assert inactive == {"ACC1011", "ACC1063", "ACC1093"}
    symbols = set(db.scalars(select(Symbol.symbol)).all())
    assert "ISRG1" in symbols and "ISRG" not in symbols and "BRK.B" in symbols  # BOM stripped: first symbol is intact
    assert db.scalar(select(func.count()).select_from(Symbol).where(Symbol.price_mean.is_(None))) == 0
    assert db.get(SystemConfig, "business_date_override").value is None
    assert db.get(SystemConfig, "hybrid_weights").editable_by == "ML_ADMIN"
    assert db.scalar(select(func.count()).select_from(HistoricalTrade).where(HistoricalTrade.settlement_failed)) \
        == sum(sum(members.values()) for members in EXPECTED_TIERS.values())


def test_reseed_creates_no_duplicates_and_keeps_admin_changes(db):
    before = _counts(db)
    db.get(SystemConfig, "batch_size").value = 5000
    db.commit()
    try:
        assert seed.run_seed(db, password=TEST_PASSWORD) is True
        assert _counts(db) == before
        assert db.get(SystemConfig, "batch_size").value == 5000  # admin edit survived
    finally:
        db.get(SystemConfig, "batch_size").value = 10000
        db.commit()


def test_if_empty_skips_when_already_seeded(db):
    assert seed.run_seed(db, password=TEST_PASSWORD, if_empty=True) is False


def test_sequences_start_where_the_schema_says(db):
    assert db.scalar(text("SELECT last_value FROM trade_id_seq")) == 200001


def test_stats_come_from_the_engine_when_it_exists(db, monkeypatch):
    """Simulates ML-1 being merged: seed must call the engine's functions (section 8.3 signatures)."""

    @dataclass(frozen=True)
    class SymbolStats:
        price_mean: float
        price_std: float
        qty_mean: float
        qty_std: float
        fail_rate: float

    calls = {}
    fake = types.ModuleType("app.engine")
    fake.load_historical_csv = lambda path: calls.setdefault("path", path) or "frame"
    fake.compute_counterparty_tiers = lambda historical, thresholds: calls.setdefault("thresholds", thresholds) and {
        "BlackRock": {"fail_count": 7, "trade_count": 10, "fail_rate": 0.7, "risk_tier": 2, "tier_label": "Tier2_MediumTrust"}}
    fake.compute_symbol_stats = lambda historical: {"AAPL": SymbolStats(1.5, 2.5, 3.5, 4.5, 0.25)}
    monkeypatch.setitem(sys.modules, "app.engine", fake)

    folder = seed.resolve_data_dir("data")
    assert seed.compute_stats(db, seed.find_file(folder, seed.HISTORICAL_FILE)) == "engine"
    assert calls["thresholds"] == {"tier4": 1500, "tier3": 1000, "tier2": 500}
    blackrock = db.scalar(select(Counterparty).where(Counterparty.name == "BlackRock"))
    assert (blackrock.risk_tier, blackrock.historical_fail_count) == (2, 7)
    assert float(db.scalar(select(Symbol.qty_std).where(Symbol.symbol == "AAPL"))) == 4.5
    # fixture rolls this back, so the real seeded values stay in place

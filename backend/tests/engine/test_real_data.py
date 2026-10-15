"""Acceptance checks on the real files (PROJECT_CONTEXT §2.1, §2.3)."""
import collections
import time
from datetime import date

from app.engine import EngineConfig, compute_counterparty_tiers, score_trades

EXPECTED_TIERS = {
    4: {"BlackRock", "Raymond James", "PJT Partners", "Deutsche Bank", "Credit Suisse"},
    3: {"Northern Trust", "Charles Schwab", "Capital Group", "Morgan Stanley", "Tokyo Marine",
        "Bank of America", "Barclays Wealth", "Commerzbank", "Vanguard"},
    2: {"State Street", "Moelis", "Evercore", "Lazard", "Nomura", "E*TRADE", "Goldman Sachs", "Fidelity",
        "BofA Securities", "Santander", "Interactive Brokers", "HSBC"},
    1: {"Jefferies", "JPMorgan Chase", "Standard Chartered", "Societe Generale", "Barclays", "UBS",
        "Centerview", "Citigroup", "Goldman Sachs Asset Management", "Mizuho", "ING Group", "Royal Bank of Scotland"},
}


def test_counterparty_tiers_match_spec(history):
    tiers = compute_counterparty_tiers(history)
    got = collections.defaultdict(set)
    for name, t in tiers.items():
        got[t["risk_tier"]].add(name)
    assert dict(got) == EXPECTED_TIERS
    assert tiers["BlackRock"]["fail_count"] == 2049 and tiers["HSBC"]["fail_count"] == 501


def test_20k_file_rejections(records_20k, real_refs):
    cfg = EngineConfig()
    out = []
    for s in range(0, len(records_20k), 10_000):
        out += score_trades(records_20k[s:s + 10_000], real_refs, cfg, date(2026, 9, 28))
    assert len(out) == 20_000
    rejected = [t for t in out if t.status == "REJECTED"]
    assert len(rejected) == 704
    counts = collections.Counter(e.rule_id for t in rejected for e in t.rejection_reasons)
    assert counts == {"R-007": 577, "R-005": 130, "R-001": 1, "R-002": 1}


def test_10k_rules_only_under_10_seconds(records_20k, real_refs):
    t0 = time.time()
    score_trades(records_20k[:10_000], real_refs, EngineConfig(), date(2026, 9, 28))
    assert time.time() - t0 < 10
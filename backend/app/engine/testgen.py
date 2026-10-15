"""Automated test-case generation (MODULE_ML ML-5, deliverable "automated test case generation").

generate_all(out_dir, data_dir) writes:
  test_generated_rules.py : parametrized pytest cases built FROM the rule configuration
                            (DEFAULT_RULE_WEIGHTS + HARD_GATES): one fail case per hard gate, one
                            positive + one negative/boundary case per soft rule, classification boundaries.
                            Expected values are computed from the weights, NOT by running the engine.
  edge_cases.csv          : ~50 trades covering every §2.1 trap, boundaries, messy formats (UTF-8 BOM).
  expected_results.json   : expected status / rule score / rejection rules per edge-case row.
  test_edge_cases.py      : scores edge_cases.csv with the engine and compares to expected_results.json.
The backend integration test can upload edge_cases.csv and assert the same expectations.
"""
from __future__ import annotations

import csv
import json
import os
import pprint
from typing import Optional

from .config import DEFAULT_RULE_WEIGHTS, EngineConfig
from .hard_gates import HARD_GATES
from .rules import RULES

BUSINESS_DATE = "2026-09-28"
HEADERS = ["Trade ID", "Trade Type", "Symbol", "Quantity", "Price", "Counterparty", "Trade Date",
           "Settlement Date", "Account ID", "SSI Matched", "Currency Matched", "Trader Name"]
KEYS = ["trade_id", "trade_type", "symbol", "quantity", "price", "counterparty", "trade_date",
        "settlement_date", "account_id", "ssi_matched", "currency_matched", "trader_name"]
# a clean trade valid against the REAL master data: Tier-1 counterparty, Mon trade, Wed settle -> score 0
BASE = dict(zip(KEYS, ["EC000", "BUY", "AAPL", "1000", "100.00", "UBS", "10/5/2026", "10/7/2026",
                       "ACC1000", "YES", "YES", "Wei Zhang"]))

# how to break each hard gate (generation fails if a new gate is added without a breaker)
GATE_BREAKERS: dict[str, list[tuple[str, dict]]] = {
    "R-000": [("symbol blank", {"symbol": ""}), ("trader blank", {"trader_name": ""}),
              ("ssi not YES/NO", {"ssi_matched": "maybe"}), ("settlement date blank", {"settlement_date": ""})],
    "R-001": [("trade id blank", {"trade_id": ""})],
    "R-002": [("trade type blank", {"trade_type": ""}), ("trade type HOLD", {"trade_type": "HOLD"})],
    "R-003": [("quantity zero", {"quantity": "0"}), ("quantity negative", {"quantity": "-5"}),
              ("quantity fractional", {"quantity": "1.5"})],
    "R-004": [("price zero", {"price": "0"}), ("price not a number", {"price": "abc"})],
    "R-005": [("symbol ISRG (master has ISRG1)", {"symbol": "ISRG"}), ("symbol TMO1", {"symbol": "TMO1"}),
              ("symbol HD12", {"symbol": "HD12"})],
    "R-006": [("unknown counterparty", {"counterparty": "ABC Bank"})],
    "R-007": [("inactive ACC1011", {"account_id": "ACC1011"}), ("inactive ACC1063", {"account_id": "ACC1063"}),
              ("inactive ACC1093", {"account_id": "ACC1093"}), ("unknown account", {"account_id": "ACC9999"})],
    "R-008": [("settlement before trade", {"settlement_date": "10/2/2026"}),
              ("unparseable date", {"trade_date": "31/12/2026"})],
}

# how to trigger each soft rule, with a negative (boundary) twin that must NOT trigger it
SOFT_TRIGGERS: dict[str, tuple[dict, dict, str]] = {
    # rule: (positive override, negative/boundary override, weight key path)
    "S-SSI": ({"ssi_matched": "NO"}, {"ssi_matched": "YES"}, "ssi"),
    "S-CCY": ({"currency_matched": "NO"}, {"currency_matched": "YES"}, "ccy"),
    "S-SD-PAST": ({"trade_date": "9/21/2026", "settlement_date": "9/23/2026"}, {"settlement_date": "10/7/2026"}, "sd_past"),
    "S-SD-WKND": ({"settlement_date": "10/10/2026"}, {"settlement_date": "10/9/2026"}, "sd_weekend"),
    "S-SD-FAR": ({"settlement_date": "10/21/2026"}, {"settlement_date": "10/20/2026"}, "sd_far"),   # 16 vs 15 days
    "S-CP-TIER": ({"counterparty": "BlackRock"}, {"counterparty": "UBS"}, "cp_tier.4"),
    "S-QTY-HIGH": ({"quantity": "5001"}, {"quantity": "5000"}, "qty_high"),
    "S-QTY-LOW": ({"quantity": "99"}, {"quantity": "100"}, "qty_low"),
}


def _w(path: str, weights: dict) -> float:
    if "." in path:
        a, b = path.split(".")
        return float(weights[a][b])
    return float(weights[path])


def _status(score: float, cfg: EngineConfig) -> str:
    return "EXCEPTION" if score >= cfg.exception_threshold else "MONITOR" if score >= cfg.pass_threshold else "PASS"


def build_rule_cases(weights: Optional[dict] = None, cfg: Optional[EngineConfig] = None) -> dict:
    weights = weights or DEFAULT_RULE_WEIGHTS
    cfg = cfg or EngineConfig()
    missing = set(HARD_GATES) - set(GATE_BREAKERS)
    if missing:
        raise ValueError(f"no test breaker defined for hard gates {sorted(missing)}")
    gate_cases = [(f"{rid} {desc}", ov, rid) for rid, items in GATE_BREAKERS.items() for desc, ov in items]
    missing = {r for r in RULES if r != "W-PRICE"} - set(SOFT_TRIGGERS)
    if missing:
        raise ValueError(f"no test trigger defined for soft rules {sorted(missing)}")
    soft_cases = []
    for rid, (pos, neg, key) in SOFT_TRIGGERS.items():
        w = _w(key, weights)
        soft_cases.append((f"{rid} positive", pos, rid, True, w))
        soft_cases.append((f"{rid} negative/boundary", neg, rid, False, 0.0))
    for tier, cp in (("2", "State Street"), ("3", "Vanguard"), ("4", "BlackRock")):
        soft_cases.append((f"S-CP-TIER tier {tier}", {"counterparty": cp}, "S-CP-TIER", True, _w(f"cp_tier.{tier}", weights)))
    combos = [
        ("SSI + currency", {"ssi_matched": "NO", "currency_matched": "NO"}, weights["ssi"] + weights["ccy"]),
        ("SSI + currency + tier 4", {"ssi_matched": "NO", "currency_matched": "NO", "counterparty": "BlackRock"},
         weights["ssi"] + weights["ccy"] + weights["cp_tier"]["4"]),
        ("date cap: past + weekend + far", {"trade_date": "9/1/2026", "settlement_date": "9/19/2026"},
         weights["sd_cap"]),
    ]
    combo_cases = [(d, ov, round(s, 4), _status(s, cfg)) for d, ov, s in combos]
    class_cases = [(s, _status(s, cfg)) for s in (0.0, cfg.pass_threshold - 0.0001, cfg.pass_threshold,
                                                  cfg.exception_threshold - 0.0001, cfg.exception_threshold, 1.0)]
    return {"gate": gate_cases, "soft": soft_cases, "combo": combo_cases, "classification": class_cases}


_RULE_TEST_TEMPLATE = '''"""AUTO-GENERATED by app.engine.testgen - do not edit by hand.
Regenerate: python scripts/generate_test_cases.py   (or: python -m app.engine.cli gen-tests)
Cases are derived from DEFAULT_RULE_WEIGHTS and HARD_GATES; expected values come from the weights."""
from datetime import date

import pytest

from app.engine import EngineConfig, classify, score_trades
from app.engine.types import CounterpartyRef, ReferenceData

BUSINESS_DATE = date.fromisoformat({bdate!r})
BASE = {base}
HEADERS = {headers}
KEYS = {keys}


def refs():
    tiers = {{"UBS": 1, "State Street": 2, "Vanguard": 3, "BlackRock": 4}}
    return ReferenceData(
        symbols={{"AAPL", "ISRG1"}},
        counterparties={{n.lower(): CounterpartyRef(n, t, f"Tier{{t}}", 0, 0, 0.0) for n, t in tiers.items()}},
        accounts={{"ACC1000": True, "ACC1011": False, "ACC1063": False, "ACC1093": False}},
        symbol_stats={{}},
    )


def trade(override):
    t = {{**BASE, **override}}
    return {{h: t[k] for h, k in zip(HEADERS, KEYS)}}


GATE_CASES = {gate}
SOFT_CASES = {soft}
COMBO_CASES = {combo}
CLASS_CASES = {classification}


@pytest.mark.parametrize("name, override, rule_id", GATE_CASES, ids=[c[0] for c in GATE_CASES])
def test_hard_gate(name, override, rule_id):
    r = score_trades([trade(override)], refs(), EngineConfig(), BUSINESS_DATE)[0]
    assert r.status == "REJECTED" and r.final_score == 1.0
    assert rule_id in {{e.rule_id for e in r.rejection_reasons}}


@pytest.mark.parametrize("name, override, rule_id, triggered, contribution", SOFT_CASES, ids=[c[0] for c in SOFT_CASES])
def test_soft_rule(name, override, rule_id, triggered, contribution):
    r = score_trades([trade(override)], refs(), EngineConfig(), BUSINESS_DATE)[0]
    hit = next(h for h in r.rule_hits if h.rule_id == rule_id)
    assert hit.triggered is triggered
    assert hit.contribution == pytest.approx(contribution, abs=1e-4)


@pytest.mark.parametrize("name, override, score, status", COMBO_CASES, ids=[c[0] for c in COMBO_CASES])
def test_combinations(name, override, score, status):
    r = score_trades([trade(override)], refs(), EngineConfig(), BUSINESS_DATE)[0]
    assert r.rule_score == pytest.approx(score, abs=1e-4) and r.status == status


@pytest.mark.parametrize("score, status", CLASS_CASES)
def test_classification_boundary(score, status):
    assert classify(score, EngineConfig()) == status


def test_clean_trade_scores_zero():
    r = score_trades([trade({{}})], refs(), EngineConfig(), BUSINESS_DATE)[0]
    assert (r.status, r.final_score, r.priority) == ("PASS", 0.0, "LOW")
'''

_EDGE_TEST_TEMPLATE = '''"""AUTO-GENERATED by app.engine.testgen - do not edit by hand.
Scores edge_cases.csv (rules-only, real reference data) and compares with expected_results.json."""
import json
from datetime import date
from pathlib import Path

import pytest

from app.engine import EngineConfig, load_historical_csv, score_trades
from app.engine.normalize import read_trade_csv
from app.engine.reference import load_reference_data

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[2] / "data"
EXPECTED = json.loads((HERE / "expected_results.json").read_text())


@pytest.fixture(scope="module")
def results():
    hist = load_historical_csv(str(DATA / "Historical_Trade_Dataset_50K.csv"))
    refs = load_reference_data(str(DATA / "Ref_Data"), hist)
    recs = read_trade_csv(str(HERE / "edge_cases.csv"))
    return score_trades(recs, refs, EngineConfig(), date.fromisoformat(EXPECTED["business_date"]))


@pytest.mark.parametrize("row", EXPECTED["rows"], ids=[f"{r['row']}-{r['description']}" for r in EXPECTED["rows"]])
def test_edge_case(results, row):
    r = results[row["row"]]
    assert r.status == row["expected_status"]
    if row["expected_status"] == "REJECTED":
        assert {e.rule_id for e in r.rejection_reasons} == set(row["expected_rejection_rules"])
    else:
        assert r.rule_score == pytest.approx(row["expected_rule_score"], abs=1e-4)
        for w in row.get("expected_warnings_contain", []):
            assert any(w in x for x in r.warnings)


def test_summary(results):
    from collections import Counter
    assert dict(Counter(r.status for r in results)) == EXPECTED["summary"]
'''


def build_edge_cases(weights: Optional[dict] = None, cfg: Optional[EngineConfig] = None) -> list[dict]:
    """[{description, override, expected_status, expected_rule_score | expected_rejection_rules}]"""
    w = weights or DEFAULT_RULE_WEIGHTS
    cfg = cfg or EngineConfig()
    ok = lambda d, ov, score, warn=None: {"description": d, "override": ov, "score": round(score, 4),  # noqa: E731
                                          "status": _status(score, cfg), "warnings": warn or []}
    bad = lambda d, ov, rules: {"description": d, "override": ov, "rules": rules, "status": "REJECTED"}  # noqa: E731
    t4, t3, t2 = (float(w["cp_tier"][k]) for k in ("4", "3", "2"))
    cases = [
        ok("clean trade", {}, 0.0),
        ok("SSI not matched", {"ssi_matched": "NO"}, w["ssi"]),
        ok("currency not matched", {"currency_matched": "NO"}, w["ccy"]),
        ok("weekend settlement", {"settlement_date": "10/10/2026"}, w["sd_weekend"]),
        ok("settlement 16 days after trade", {"settlement_date": "10/21/2026"}, w["sd_far"]),
        ok("settlement in the past", {"trade_date": "9/21/2026", "settlement_date": "9/23/2026"}, w["sd_past"]),
        ok("date cap past+weekend+far", {"trade_date": "9/1/2026", "settlement_date": "9/19/2026"}, w["sd_cap"]),
        ok("tier 2 counterparty", {"counterparty": "State Street"}, t2),
        ok("tier 3 counterparty", {"counterparty": "Vanguard"}, t3),
        ok("tier 4 counterparty", {"counterparty": "BlackRock"}, t4),
        ok("quantity 5001", {"quantity": "5001"}, w["qty_high"]),
        ok("quantity 5000 boundary", {"quantity": "5000"}, 0.0),
        ok("quantity 99", {"quantity": "99"}, w["qty_low"]),
        ok("quantity 100 boundary", {"quantity": "100"}, 0.0),
        ok("absurd quantity", {"quantity": "223333333"}, w["qty_high"], ["fat-finger"]),
        ok("SSI + currency", {"ssi_matched": "NO", "currency_matched": "NO"}, w["ssi"] + w["ccy"]),
        ok("SSI + currency + tier 4", {"ssi_matched": "NO", "currency_matched": "NO", "counterparty": "BlackRock"},
           w["ssi"] + w["ccy"] + t4),
        ok("SSI + currency + tier 4 + qty", {"ssi_matched": "NO", "currency_matched": "NO",
                                             "counterparty": "BlackRock", "quantity": "6000"},
           w["ssi"] + w["ccy"] + t4 + w["qty_high"]),
        ok("everything at max", {"ssi_matched": "NO", "currency_matched": "NO", "counterparty": "BlackRock",
                                 "quantity": "6000", "trade_date": "9/1/2026", "settlement_date": "9/19/2026"},
           min(1.0, w["ssi"] + w["ccy"] + t4 + w["qty_high"] + w["sd_cap"])),
        ok("lowercase and spaces", {"trade_type": " buy ", "symbol": "aapl", "counterparty": " ubs "}, 0.0),
        ok("Y/N booleans", {"ssi_matched": "Y", "currency_matched": "N"}, w["ccy"]),
        ok("ISO dates", {"trade_date": "2026-10-05", "settlement_date": "2026-10-07"}, 0.0),
        ok("quantity 100.0", {"quantity": "1000.0"}, 0.0),
        ok("TRUE/0 booleans", {"ssi_matched": "true", "currency_matched": "0"}, w["ccy"]),
        ok("lowercase tier-4 name", {"counterparty": "blackrock"}, t4),
        bad("trade id blank", {"trade_id": ""}, ["R-001"]),
        bad("duplicate trade id", {"trade_id": "EC001"}, ["R-001"]),
        bad("trade type blank", {"trade_type": ""}, ["R-002"]),
        bad("trade type HOLD", {"trade_type": "HOLD"}, ["R-002"]),
        bad("quantity zero", {"quantity": "0"}, ["R-003"]),
        bad("quantity negative", {"quantity": "-5"}, ["R-003"]),
        bad("quantity fractional", {"quantity": "1.5"}, ["R-003"]),
        bad("price zero", {"price": "0"}, ["R-004"]),
        bad("price text", {"price": "abc"}, ["R-004"]),
        bad("symbol ISRG", {"symbol": "ISRG"}, ["R-005"]),
        bad("symbol TMO1", {"symbol": "TMO1"}, ["R-005"]),
        bad("symbol HD12", {"symbol": "HD12"}, ["R-005"]),
        bad("unknown counterparty", {"counterparty": "ABC Bank"}, ["R-006"]),
        bad("inactive ACC1011", {"account_id": "ACC1011"}, ["R-007"]),
        bad("inactive ACC1063", {"account_id": "ACC1063"}, ["R-007"]),
        bad("inactive ACC1093", {"account_id": "ACC1093"}, ["R-007"]),
        bad("unknown account", {"account_id": "ACC9999"}, ["R-007"]),
        bad("settlement before trade", {"settlement_date": "10/2/2026"}, ["R-008"]),
        bad("unparseable date", {"trade_date": "31/12/2026"}, ["R-008"]),
        bad("symbol blank", {"symbol": ""}, ["R-000"]),
        bad("trader blank", {"trader_name": ""}, ["R-000"]),
        bad("ssi maybe", {"ssi_matched": "maybe"}, ["R-000"]),
        bad("settlement date blank", {"settlement_date": ""}, ["R-000"]),
        bad("ISRG on inactive account", {"symbol": "ISRG", "account_id": "ACC1011"}, ["R-005", "R-007"]),
    ]
    for i, c in enumerate(cases):                       # unique IDs EC001.., except explicit overrides
        c["override"] = {"trade_id": f"EC{i + 1:03d}", **c["override"]}
    return cases


def generate_all(out_dir: str, data_dir: str = "../data") -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    cfg = EngineConfig()
    rc = build_rule_cases(cfg=cfg)
    fmt = lambda v: pprint.pformat(v, width=110, sort_dicts=False)          # noqa: E731
    paths = []

    p = os.path.join(out_dir, "test_generated_rules.py")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(_RULE_TEST_TEMPLATE.format(bdate=BUSINESS_DATE, base=fmt(BASE), headers=fmt(HEADERS),
                                            keys=fmt(KEYS), gate=fmt(rc["gate"]), soft=fmt(rc["soft"]),
                                            combo=fmt(rc["combo"]), classification=fmt(rc["classification"])))
    paths.append(p)

    cases = build_edge_cases(cfg=cfg)
    p = os.path.join(out_dir, "edge_cases.csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:            # BOM on purpose (trap)
        wr = csv.writer(fh)
        wr.writerow(HEADERS)
        for c in cases:
            t = {**BASE, **c["override"]}
            wr.writerow([t[k] for k in KEYS])
    paths.append(p)

    rows, summary = [], {}
    for i, c in enumerate(cases):
        row = {"row": i, "trade_id": c["override"].get("trade_id"), "description": c["description"],
               "expected_status": c["status"]}
        if c["status"] == "REJECTED":
            row["expected_rejection_rules"] = c["rules"]
        else:
            row["expected_rule_score"] = c["score"]
            if c["warnings"]:
                row["expected_warnings_contain"] = c["warnings"]
        summary[c["status"]] = summary.get(c["status"], 0) + 1
        rows.append(row)
    p = os.path.join(out_dir, "expected_results.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"business_date": BUSINESS_DATE, "mode": "rules_only", "summary": summary, "rows": rows},
                  fh, indent=2)
    paths.append(p)

    p = os.path.join(out_dir, "test_edge_cases.py")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(_EDGE_TEST_TEMPLATE)
    paths.append(p)
    open(os.path.join(out_dir, "__init__.py"), "a").close()
    return paths

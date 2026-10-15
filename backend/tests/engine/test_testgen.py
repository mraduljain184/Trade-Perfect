"""ML-5: the generator itself - every gate/rule covered, files written, expectations consistent."""
import json
import tempfile
from pathlib import Path

from app.engine.hard_gates import HARD_GATES
from app.engine.rules import RULES
from app.engine.testgen import build_edge_cases, build_rule_cases, generate_all


def test_every_hard_gate_and_soft_rule_has_cases():
    rc = build_rule_cases()
    assert {c[2] for c in rc["gate"]} == set(HARD_GATES)
    assert {c[2] for c in rc["soft"]} == set(RULES) - {"W-PRICE"}
    assert all(any(c[3] for c in rc["soft"] if c[2] == r) and any(not c[3] for c in rc["soft"] if c[2] == r)
               for r in set(RULES) - {"W-PRICE"})          # positive AND negative per rule


def test_expected_scores_follow_the_weights():
    rc = build_rule_cases(weights={"ssi": 0.5, "ccy": 0.1, "sd_past": 0.15, "sd_weekend": 0.1, "sd_far": 0.05,
                                   "sd_cap": 0.15, "cp_tier": {"1": 0, "2": 0.1, "3": 0.2, "4": 0.25},
                                   "qty_high": 0.1, "qty_low": 0.05, "price_warn": 0.0})
    assert ("S-SSI positive", {"ssi_matched": "NO"}, "S-SSI", True, 0.5) in rc["soft"]


def test_generate_all_writes_files():
    out = tempfile.mkdtemp()
    paths = generate_all(out)
    names = {Path(p).name for p in paths}
    assert names == {"test_generated_rules.py", "edge_cases.csv", "expected_results.json", "test_edge_cases.py"}
    exp = json.loads((Path(out) / "expected_results.json").read_text())
    assert len(exp["rows"]) == len(build_edge_cases()) >= 45
    assert (Path(out) / "edge_cases.csv").read_bytes().startswith(b"\xef\xbb\xbf")     # BOM trap present

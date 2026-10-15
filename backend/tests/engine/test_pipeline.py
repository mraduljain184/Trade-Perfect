import json

import pytest

from app.engine import EngineConfig, classify, hybrid_score, priority_for, score_trades
from app.engine.explain import build_risk_factors

from .helpers import BUSINESS_DATE, make_trade


@pytest.mark.parametrize("score, status", [(0.0, "PASS"), (0.4999, "PASS"), (0.5, "MONITOR"),
                                           (0.6999, "MONITOR"), (0.7, "EXCEPTION"), (1.0, "EXCEPTION")])
def test_classification_boundaries(cfg, score, status):
    assert classify(score, cfg) == status


@pytest.mark.parametrize("status, priority", [("EXCEPTION", "HIGH"), ("MONITOR", "MEDIUM"),
                                              ("PASS", "LOW"), ("REJECTED", None)])
def test_priority_mapping(status, priority):
    assert priority_for(status) == priority


def test_hybrid_math(cfg):
    assert hybrid_score(0.5, 0.9, cfg) == pytest.approx(0.62)        # 0.3*0.9 + 0.7*0.5
    assert hybrid_score(0.5, None, cfg) == 0.5                       # rules only
    assert hybrid_score(0.5, 0.9, EngineConfig(alpha=0.5, beta=0.5)) == pytest.approx(0.7)


def test_score_trades_keeps_order_and_marks_rejections(refs, cfg):
    recs = [make_trade(trade_id="A"), make_trade(trade_id="B", symbol="ISRG"), make_trade(trade_id="C", ssi_matched="NO")]
    out = score_trades(recs, refs, cfg, BUSINESS_DATE)
    assert [t.trade_id for t in out] == ["A", "B", "C"] and [t.row_index for t in out] == [0, 1, 2]
    assert [t.status for t in out] == ["PASS", "REJECTED", "PASS"]
    rej = out[1]
    assert rej.final_score == 1.0 and rej.priority is None and rej.rule_hits == []
    assert rej.rejection_reasons[0].rule_id == "R-005"


def test_rules_only_result_fields(refs, cfg):
    t = score_trades([make_trade(ssi_matched="NO", currency_matched="NO", counterparty="Vanguard")],
                     refs, cfg, BUSINESS_DATE)[0]
    assert t.rule_score == pytest.approx(0.70) and t.ml_score is None and t.final_score == pytest.approx(0.70)
    assert t.status == "EXCEPTION" and t.priority == "HIGH"
    assert t.likely_reason == "SSI not matched with counterparty"
    assert "(rules only" in t.explanation and t.counterparty == "Vanguard"
    assert len(t.rule_hits) == 9                                  # every rule listed, triggered or not
    assert [f["factor"] for f in t.suggested_fixes] == ["SSI", "COUNTERPARTY", "CURRENCY"]
    assert sum(f["pct"] for f in t.risk_factors) == pytest.approx(100.0, abs=0.1)


def test_zero_score_trade(refs, cfg):
    t = score_trades([make_trade()], refs, cfg, BUSINESS_DATE)[0]
    assert (t.status, t.priority, t.final_score, t.risk_factors) == ("PASS", "LOW", 0.0, [])


def test_weekend_fix_contains_next_business_day(refs, cfg):
    t = score_trades([make_trade(settlement_date="10/10/2026")], refs, cfg, BUSINESS_DATE)[0]
    assert "2026-10-12" in t.suggested_fixes[0]["fix"]


def test_output_is_json_serialisable(refs, cfg):
    out = score_trades([make_trade(), make_trade(trade_id="X", symbol="ISRG")], refs, cfg, BUSINESS_DATE)
    json.dumps([t.to_dict() for t in out])


def test_empty_batch(refs, cfg):
    assert score_trades([], refs, cfg, BUSINESS_DATE) == []


def test_risk_factor_pct_sums_to_100_with_ml_part():
    rf = build_risk_factors(
        {"S-SSI": 0.35, "S-CP-TIER": 0.20}, {"S-SSI": "SSI not matched", "S-CP-TIER": "Tier 3"},
        counterparty="Vanguard", ml_score=0.9,
        shap_values={"SSI_Matched_Flag": 0.3, "Rule_Engine_Output": 0.1, "Quantity": 0.05, "Price": -0.2},
    )
    assert sum(f["pct"] for f in rf) == pytest.approx(100.0, abs=0.1)
    assert rf[0]["factor"] == "SSI" and all(f["pct"] >= 1.0 for f in rf)
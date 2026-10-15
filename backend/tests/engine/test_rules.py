from datetime import date

import pytest

from app.engine import EngineConfig
from app.engine.normalize import normalize_records
from app.engine.reference import tier_for_fail_count
from app.engine.rules import evaluate_rules

from .helpers import BUSINESS_DATE, make_trade


def rules_for(refs, cfg, business_date=BUSINESS_DATE, **override):
    df = normalize_records([make_trade(**override)]).df
    df["counterparty"] = df["counterparty"].map(lambda c: refs.counterparties[c.lower()].name)
    return evaluate_rules(df, refs, cfg, business_date)


def test_clean_trade_scores_zero(refs, cfg):
    r = rules_for(refs, cfg)
    assert r.rule_score[0] == 0.0 and not r.triggered.iloc[0].any()


@pytest.mark.parametrize("override, rule_id, contribution", [
    ({"ssi_matched": "NO"}, "S-SSI", 0.35),
    ({"currency_matched": "NO"}, "S-CCY", 0.15),
    ({"settlement_date": "10/10/2026"}, "S-SD-WKND", 0.10),         # Saturday
    ({"trade_date": "9/30/2026", "settlement_date": "10/2/2026"}, "S-SD-WKND", 0.10),   # holiday
    ({"settlement_date": "10/22/2026"}, "S-SD-FAR", 0.05),          # 17 days after trade
    ({"counterparty": "State Street"}, "S-CP-TIER", 0.10),
    ({"counterparty": "Vanguard"}, "S-CP-TIER", 0.20),
    ({"counterparty": "BlackRock"}, "S-CP-TIER", 0.25),
    ({"quantity": "5001"}, "S-QTY-HIGH", 0.10),
    ({"quantity": "99"}, "S-QTY-LOW", 0.05),
    ({"price": "140"}, "W-PRICE", 0.0),                              # z = 4, warning only
])
def test_each_soft_rule(refs, cfg, override, rule_id, contribution):
    r = rules_for(refs, cfg, **override)
    assert bool(r.triggered.at[0, rule_id])
    assert r.contribution.at[0, rule_id] == pytest.approx(contribution)
    assert r.rule_score[0] == pytest.approx(contribution)


@pytest.mark.parametrize("qty, high, low", [(5000, False, False), (100, False, False), (5001, True, False), (99, False, True)])
def test_quantity_boundaries(refs, cfg, qty, high, low):
    r = rules_for(refs, cfg, quantity=str(qty))
    assert bool(r.triggered.at[0, "S-QTY-HIGH"]) is high and bool(r.triggered.at[0, "S-QTY-LOW"]) is low


def test_past_settlement_only_in_hybrid_score_not_ml_score(refs, cfg):
    r = rules_for(refs, cfg, business_date=date(2026, 10, 9))      # settlement 10/7 is in the past
    assert r.triggered.at[0, "S-SD-PAST"]
    assert r.rule_score[0] == pytest.approx(0.15) and r.rule_score_ml[0] == 0.0


def test_far_rule_measured_from_trade_date_not_business_date(refs, cfg):
    r = rules_for(refs, cfg, business_date=date(2026, 9, 1))       # business date 36 days before settlement
    assert not r.triggered.at[0, "S-SD-FAR"]                       # but trade->settlement is only 2 days


def test_settlement_cap_scales_proportionally(refs, cfg):
    # past (biz date later) + Saturday + 19 days after trade -> 0.15+0.10+0.05 = 0.30 -> capped to 0.15
    r = rules_for(refs, cfg, business_date=date(2026, 10, 30), trade_date="10/5/2026", settlement_date="10/24/2026")
    c = r.contribution.iloc[0]
    assert (c["S-SD-PAST"], c["S-SD-WKND"], c["S-SD-FAR"]) == (pytest.approx(0.075), pytest.approx(0.05), pytest.approx(0.025))
    assert r.rule_score[0] == pytest.approx(0.15)


def test_contributions_always_sum_to_rule_score(refs, cfg):
    r = rules_for(refs, cfg, business_date=date(2026, 10, 30), ssi_matched="NO", currency_matched="NO",
                  counterparty="BlackRock", quantity="9000", settlement_date="10/24/2026")
    assert r.contribution.sum(axis=1).round(4).iloc[0] == pytest.approx(r.rule_score[0])
    assert r.rule_score[0] == pytest.approx(1.0)                   # every category at its max


def test_rule_weights_come_from_config(refs):
    cfg = EngineConfig.from_system_config({"rule_weights": {"ssi": 0.5}})
    assert rules_for(refs, cfg, ssi_matched="NO").rule_score[0] == pytest.approx(0.5)


@pytest.mark.parametrize("fails, tier", [(499, 1), (500, 2), (999, 2), (1000, 3), (1500, 3), (1501, 4)])
def test_tier_boundaries(fails, tier):
    assert tier_for_fail_count(fails) == tier
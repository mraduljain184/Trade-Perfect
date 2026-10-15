"""Soft rule engine -> Rule_Score (PROJECT_CONTEXT §6.3).

Every rule adds a fixed weight when its condition is true. Weights come from
cfg.rule_weights (system_config "rule_weights"), so a Sys Admin can tune them.

Settlement-date rules (PAST, WKND, FAR) share a CAP (default 0.15). If the triggered
ones add up to more, each is scaled down proportionally, so
    sum(contributions) == rule_score   always holds.

Day counts:
  S-SD-PAST : settlement_date < business_date        (the ONLY rule that depends on "today")
  S-SD-FAR  : settlement_date - trade_date > 15 days  (deviation from §6.3, logged in §17:
              measuring from the business date flagged 12,311 of 19,296 demo trades as "far"
              although every one settles T+2..T+4; the history fact is about trade->settlement)

Two scores are computed (decision B):
  rule_score     : all rules                 -> used in the HYBRID score
  rule_score_ml  : all rules except S-SD-PAST -> ML feature Rule_Engine_Output
                   (identical to training, where business date = trade date)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from .calendar import holiday_mask, to_datetime64, weekend_mask
from .config import SCORE_DECIMALS, EngineConfig
from .types import ReferenceData, RuleHit

# rule_id -> (category / factor group, key in cfg.rule_weights)
RULES: dict[str, tuple[str, str]] = {
    "S-SSI": ("SSI", "ssi"),
    "S-CCY": ("CURRENCY", "ccy"),
    "S-SD-PAST": ("SETTLEMENT_DATE", "sd_past"),
    "S-SD-WKND": ("SETTLEMENT_DATE", "sd_weekend"),
    "S-SD-FAR": ("SETTLEMENT_DATE", "sd_far"),
    "S-CP-TIER": ("COUNTERPARTY", "cp_tier"),
    "S-QTY-HIGH": ("QUANTITY", "qty_high"),
    "S-QTY-LOW": ("QUANTITY", "qty_low"),
    "W-PRICE": ("PRICE", "price_warn"),
}
RULE_IDS = list(RULES)
SETTLEMENT_RULES = ["S-SD-PAST", "S-SD-WKND", "S-SD-FAR"]
ABSURD_QTY_FACTOR = 100        # quantity > 100 x qty_max -> extra "possible fat-finger" warning


@dataclass
class RuleResult:
    """Vectorised output for N accepted trades (row i = i-th trade passed in)."""
    triggered: pd.DataFrame        # bool,  columns = RULE_IDS
    contribution: pd.DataFrame     # float, columns = RULE_IDS (after cap / rounding)
    rule_score: np.ndarray         # all rules -> hybrid
    rule_score_ml: np.ndarray      # without S-SD-PAST -> ML feature Rule_Engine_Output
    # context needed for messages / fixes
    tier: np.ndarray
    days_to_settlement: np.ndarray     # settlement - trade date
    price_zscore: np.ndarray
    business_date: Optional[date]
    weekend: np.ndarray
    holiday: np.ndarray

    def __post_init__(self) -> None:   # numpy copies for fast per-row access
        self.trig_np = self.triggered[RULE_IDS].to_numpy()
        self.contrib_np = self.contribution[RULE_IDS].to_numpy()


def _tier_array(counterparties: pd.Series, refs: ReferenceData) -> np.ndarray:
    lookup = {k: v.risk_tier for k, v in refs.counterparties.items()}
    return counterparties.astype(str).str.lower().map(lookup).fillna(1).astype(int).to_numpy()


def price_zscores(df: pd.DataFrame, refs: ReferenceData) -> np.ndarray:
    """(price - symbol mean) / symbol std; 0 for symbols without stats."""
    mean = df["symbol"].map({k: v.price_mean for k, v in refs.symbol_stats.items()})
    std = df["symbol"].map({k: v.price_std for k, v in refs.symbol_stats.items()})
    z = (df["price"].astype("float64") - mean) / std
    return z.fillna(0.0).to_numpy(dtype=float)


def _score(df: pd.DataFrame, cfg: EngineConfig, tier: np.ndarray, zscore: np.ndarray,
           weekend: np.ndarray, holiday: np.ndarray, days: np.ndarray,
           past: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
    w = cfg.rule_weights
    n = len(df)
    qty = df["quantity"].astype("float64").to_numpy()
    tier_w = np.array([float(w["cp_tier"].get(str(t), 0.0)) for t in tier])

    trig = pd.DataFrame({
        "S-SSI": ~df["ssi_matched"].astype(bool).to_numpy(),
        "S-CCY": ~df["currency_matched"].astype(bool).to_numpy(),
        "S-SD-PAST": past,
        "S-SD-WKND": weekend | holiday,
        "S-SD-FAR": days > cfg.settlement_far_days,
        "S-CP-TIER": tier_w > 0,
        "S-QTY-HIGH": qty > cfg.qty_max,
        "S-QTY-LOW": qty < cfg.qty_min,
        "W-PRICE": np.abs(zscore) > cfg.price_zscore_warn,
    })

    weight = pd.DataFrame({rid: np.full(n, float(w[key])) for rid, (_, key) in RULES.items() if key != "cp_tier"})
    weight["S-CP-TIER"] = tier_w
    contrib = trig.astype(float) * weight[RULE_IDS]

    # settlement-date cap: scale the triggered settlement rules proportionally
    sd_sum = contrib[SETTLEMENT_RULES].sum(axis=1)
    cap = float(w["sd_cap"])
    factor = np.where(sd_sum > cap, cap / sd_sum.replace(0, 1), 1.0)
    contrib[SETTLEMENT_RULES] = contrib[SETTLEMENT_RULES].mul(factor, axis=0)

    # safety: if an admin sets weights summing > 1, scale everything into [0, 1]
    total = contrib.sum(axis=1)
    contrib = contrib.div(np.maximum(total, 1.0), axis=0)

    contrib = contrib.round(SCORE_DECIMALS)
    score = contrib.sum(axis=1).clip(0, 1).round(SCORE_DECIMALS).to_numpy()
    return trig, contrib, score


def evaluate_rules(df: pd.DataFrame, refs: ReferenceData, cfg: EngineConfig,
                   business_date: Optional[date]) -> RuleResult:
    """df: ACCEPTED trades only, normalized columns, counterparty already canonical.
    business_date=None means 'training mode' (no trade is past settlement)."""
    df = df.reset_index(drop=True)
    settle = to_datetime64(df["settlement_date"])
    trade = to_datetime64(df["trade_date"])
    days = (settle - trade).dt.days.to_numpy(dtype=float)
    tier = _tier_array(df["counterparty"], refs)
    zscore = price_zscores(df, refs)
    weekend = weekend_mask(settle)
    holiday = holiday_mask(settle, refs.holidays)
    no_past = np.zeros(len(df), dtype=bool)

    # ML version: never "past" (identical to training)
    _, _, score_ml = _score(df, cfg, tier, zscore, weekend, holiday, days, no_past)

    if business_date is None:
        trig, contrib, score = _score(df, cfg, tier, zscore, weekend, holiday, days, no_past)
    else:
        past = (settle < pd.Timestamp(business_date)).to_numpy(dtype=bool)
        trig, contrib, score = _score(df, cfg, tier, zscore, weekend, holiday, days, past)

    return RuleResult(
        triggered=trig, contribution=contrib, rule_score=score, rule_score_ml=score_ml,
        tier=tier, days_to_settlement=days, price_zscore=zscore,
        business_date=business_date, weekend=weekend, holiday=holiday,
    )


# ---- per-trade messages (for RuleHit / explanations) ---------------------------
TIER_WORDS = {4: "Tier 4 highest risk", 3: "Tier 3 elevated risk", 2: "Tier 2 medium trust", 1: "Tier 1 high trust"}


def rule_message(rule_id: str, triggered: bool, row, i: int, res: RuleResult, cfg: EngineConfig) -> str:
    """Human-readable message for one rule on one trade (row = normalized trade)."""
    sd = row.settlement_date
    if rule_id == "S-SSI":
        return "SSI not matched with counterparty" if triggered else "SSI matched"
    if rule_id == "S-CCY":
        return "Currency mismatch" if triggered else "Currency matched"
    if rule_id == "S-SD-PAST":
        return (f"Settlement date {sd} is before business date {res.business_date}" if triggered
                else "Settlement date not in the past")
    if rule_id == "S-SD-WKND":
        if not triggered:
            return "Settlement date is a business day"
        return (f"Settlement date {sd} is a holiday" if res.holiday[i] and not res.weekend[i]
                else f"Settlement date {sd} is a {sd.strftime('%A')} (not a business day)")
    if rule_id == "S-SD-FAR":
        d = int(res.days_to_settlement[i])
        return (f"Settlement {d} days after trade date (more than {cfg.settlement_far_days})" if triggered
                else f"Settlement {d} days after trade date")
    if rule_id == "S-CP-TIER":
        return f"Counterparty {row.counterparty} is {TIER_WORDS[int(res.tier[i])]}"
    if rule_id == "S-QTY-HIGH":
        return (f"Quantity {row.quantity:,} above limit {cfg.qty_max:,}" if triggered
                else f"Quantity within limit {cfg.qty_max:,}")
    if rule_id == "S-QTY-LOW":
        return (f"Quantity {row.quantity:,} below minimum {cfg.qty_min:,}" if triggered
                else f"Quantity at or above minimum {cfg.qty_min:,}")
    if rule_id == "W-PRICE":
        z = res.price_zscore[i]
        return (f"Price {abs(z):.1f} std devs from symbol mean" if triggered
                else "Price within normal range")
    raise KeyError(rule_id)


def build_rule_hits(row, i: int, res: RuleResult, cfg: EngineConfig) -> list[RuleHit]:
    """ALL rules for one trade (triggered or not), as the API's ruleHits expects."""
    hits = []
    for j, rid in enumerate(RULE_IDS):
        t = bool(res.trig_np[i, j])
        hits.append(RuleHit(
            rule_id=rid, category=RULES[rid][0], triggered=t,
            contribution=float(res.contrib_np[i, j]),
            message=rule_message(rid, t, row, i, res, cfg),
        ))
    return hits


def build_warnings(row, i: int, res: RuleResult, cfg: EngineConfig) -> list[str]:
    warnings = []
    if res.trig_np[i, RULE_IDS.index("W-PRICE")]:
        warnings.append(rule_message("W-PRICE", True, row, i, res, cfg))
    if row.quantity > ABSURD_QTY_FACTOR * cfg.qty_max:
        warnings.append(f"Quantity {row.quantity:,} is extremely large - possible fat-finger error")
    return warnings
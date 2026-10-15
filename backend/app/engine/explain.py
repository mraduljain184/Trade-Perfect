"""Explainability (PROJECT_CONTEXT §6.6): risk factors %, likely reason, suggested
action/fixes and the one-sentence explanation.

Risk factors merge rules and ML into ONE taxonomy:
  rule part : beta x contribution of each triggered rule -> its group
              (rules-only mode: the contribution itself, because final = rule_score)
  ML part   : alpha x ml_score split over groups in proportion to POSITIVE SHAP values;
              Rule_Engine_Output's share is split across the rule groups in the same
              ratio as the rule contributions.
  pct       : 100 x group / total, groups < 1% dropped, re-normalised to sum to exactly 100.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from .config import SCORE_DECIMALS
from .fixes import fix_date, fix_key, render_fix
from .rules import RULES

FACTOR_GROUPS = ["SSI", "CURRENCY", "SETTLEMENT_DATE", "COUNTERPARTY", "QUANTITY", "PRICE", "PATTERN"]

# ML feature -> factor group (§6.6). Rule_Engine_Output is special (split by rule ratio).
FEATURE_GROUPS: dict[str, str] = {
    "SSI_Matched_Flag": "SSI",
    "Currency_Matched_Flag": "CURRENCY",
    "Days_To_Settlement": "SETTLEMENT_DATE",
    "Is_Weekend_Settlement": "SETTLEMENT_DATE",
    "Is_Holiday_Settlement": "SETTLEMENT_DATE",
    "Settlement_DayOfWeek": "SETTLEMENT_DATE",
    "Is_Month_End": "SETTLEMENT_DATE",
    "Counterparty_Tier": "COUNTERPARTY",
    "Counterparty_Fail_Rate": "COUNTERPARTY",
    "CP_Symbol_Fail_Rate": "COUNTERPARTY",
    "Quantity": "QUANTITY",
    "Quantity_ZScore": "QUANTITY",
    "Price": "PRICE",
    "Price_ZScore": "PRICE",
    "Symbol_Fail_Rate": "PATTERN",
    "Trade_Type_Buy": "PATTERN",
    "Rule_Engine_Output": "__RULES__",
}

# detail text for a group that only the ML (no rule) flagged
ML_DETAIL: dict[str, str] = {
    "SSI": "SSI pattern linked to past failures",
    "CURRENCY": "Currency pattern linked to past failures",
    "SETTLEMENT_DATE": "Settlement date pattern (day of week / month end / days to settle)",
    "COUNTERPARTY": "Counterparty {cp} historical failure pattern",
    "QUANTITY": "Quantity unusual for this symbol",
    "PRICE": "Price unusual for this symbol",
    "PATTERN": "Historical pattern risk",
}
MIN_PCT = 1.0
TOP_DRIVERS = 3


def build_risk_factors(
    rule_contrib: dict[str, float],          # rule_id -> contribution (triggered rules only matter)
    rule_messages: dict[str, str],           # rule_id -> message
    counterparty: str = "",
    alpha: float = 0.3,
    beta: float = 0.7,
    ml_score: Optional[float] = None,
    shap_values: Optional[dict[str, float]] = None,
) -> list[dict]:
    rules_only = ml_score is None
    totals = {g: 0.0 for g in FACTOR_GROUPS}
    top_rule: dict[str, tuple[float, str]] = {}          # group -> (contribution, rule message)

    rule_w = 1.0 if rules_only else beta
    for rid, c in rule_contrib.items():
        if c <= 0:
            continue
        g = RULES[rid][0]
        totals[g] += rule_w * c
        if c > top_rule.get(g, (0.0, ""))[0]:
            top_rule[g] = (c, rule_messages[rid])

    if not rules_only and shap_values:
        positive = {f: v for f, v in shap_values.items() if v > 0}
        pos_sum = sum(positive.values())
        rule_sum = sum(c for c in rule_contrib.values() if c > 0)
        if pos_sum > 0:
            ml_mass = alpha * ml_score
            for f, v in positive.items():
                share = ml_mass * v / pos_sum
                g = FEATURE_GROUPS.get(f, "PATTERN")
                if g == "__RULES__":
                    if rule_sum > 0:
                        for rid, c in rule_contrib.items():
                            if c > 0:
                                totals[RULES[rid][0]] += share * c / rule_sum
                    else:
                        totals["PATTERN"] += share
                else:
                    totals[g] += share

    grand = sum(totals.values())
    if grand <= 0:
        return []
    kept = {g: t for g, t in totals.items() if 100 * t / grand >= MIN_PCT}
    kept_sum = sum(kept.values())
    factors = []
    for g, t in sorted(kept.items(), key=lambda kv: -kv[1]):
        detail = top_rule[g][1] if g in top_rule else ML_DETAIL[g].format(cp=counterparty)
        factors.append({"factor": g, "pct": round(100 * t / kept_sum, 1),
                        "contribution": round(t, SCORE_DECIMALS), "detail": detail})
    drift = round(100.0 - sum(f["pct"] for f in factors), 1)        # make pct sum exactly 100
    factors[0]["pct"] = round(factors[0]["pct"] + drift, 1)
    return factors


def build_fixes(risk_factors: list[dict], triggered_rules: Iterable[str], *, tier: int,
                counterparty: str, settlement_date: Optional[date], business_date: Optional[date],
                holidays=(), qty_min: int = 100, qty_max: int = 5000, far_days: int = 15) -> list[dict]:
    """[{"factor","fix","short","key"}] in risk-factor order (-> repair_queue.suggested_fixes)."""
    out = []
    for f in risk_factors:
        key = fix_key(f["factor"], triggered_rules, tier)
        d = fix_date(key, settlement_date, business_date, holidays)
        kw = dict(cp=counterparty, d=d, qty_min=qty_min, qty_max=qty_max, far_days=far_days)
        out.append({"factor": f["factor"], "key": key,
                    "fix": render_fix(key, **kw), "short": render_fix(key, short=True, **kw)})
    return out


def explanation_text(*, trade_id: Optional[str], trade_type: str, quantity: int, symbol: str,
                     counterparty: str, final_score: float, status: str,
                     risk_factors: list[dict], fixes: list[dict],
                     settlement_date: Optional[date], rules_only: bool) -> str:
    who = f"Trade {trade_id}" if trade_id else "This trade"
    s = (f"{who} ({trade_type} {quantity:,} {symbol} with {counterparty}) has a "
         f"{final_score:.2f} risk of settlement failure → {status}.")
    if not risk_factors:
        s += " No risk factors found."
    else:
        drivers = ", ".join(f"{f['detail']} ({f['pct']:.0f}%)" for f in risk_factors[:TOP_DRIVERS])
        s += f" Main drivers: {drivers}."
        actions = list(dict.fromkeys(fx["short"] for fx in fixes[:2]))     # unique, keep order
        when = f" before settlement on {settlement_date.isoformat()}" if settlement_date else ""
        s += f" Suggested: {' and '.join(actions)}{when}."
    if rules_only:
        s += " (rules only — ML model unavailable)"
    return s
"""The two functions the backend calls (PROJECT_CONTEXT §6.1, §8.3):

    validate_trade(record, refs, cfg, business_date) -> ValidationResult
    score_trades(records, refs, cfg, business_date, predictor) -> list[ScoredTrade]

score_trades pipeline (per batch, vectorised where it matters):
    normalize -> hard gates -> (REJECTED rows stop here)
    -> rule engine -> [ML model + SHAP, from ML-3] -> hybrid score
    -> classify + priority -> explain -> ScoredTrade (same order as input)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

import pandas as pd

from .config import SCORE_DECIMALS, EngineConfig
from .explain import build_fixes, build_risk_factors, explanation_text
from .hard_gates import GateResult, run_hard_gates
from .normalize import NormalizedBatch, normalize_records
from .rules import RuleResult, build_rule_hits, build_warnings, evaluate_rules
from .types import ReferenceData, ScoredTrade, ValidationResult

log = logging.getLogger(__name__)

PRIORITY_BY_STATUS = {"EXCEPTION": "HIGH", "MONITOR": "MEDIUM", "PASS": "LOW"}
REJECTED_SCORE = 1.0
# rules shown as inline warnings on the booking form (POST /trades/validate)
_FORM_WARNING_RULES = {"S-SD-PAST", "S-SD-WKND", "S-SD-FAR", "S-QTY-HIGH", "S-QTY-LOW"}


# ---- scoring helpers -----------------------------------------------------------
def clamp_round(x: float) -> float:
    return round(min(max(float(x), 0.0), 1.0), SCORE_DECIMALS)


def hybrid_score(rule_score: float, ml_score: Optional[float], cfg: EngineConfig) -> float:
    """final = alpha*ml + beta*rule; rules-only (ml None) -> final = rule_score."""
    if ml_score is None:
        return clamp_round(rule_score)
    return clamp_round(cfg.alpha * ml_score + cfg.beta * rule_score)


def classify(final_score: float, cfg: EngineConfig) -> str:
    """< pass -> PASS · [pass, exception) -> MONITOR · >= exception -> EXCEPTION."""
    if final_score >= cfg.exception_threshold:
        return "EXCEPTION"
    if final_score >= cfg.pass_threshold:
        return "MONITOR"
    return "PASS"


def priority_for(status: str) -> Optional[str]:
    return PRIORITY_BY_STATUS.get(status)          # REJECTED -> None


# ---- small conversions -----------------------------------------------------------
def _py(v: Any) -> Any:
    """pandas/numpy missing & scalar types -> plain python (JSON-safe)."""
    if v is None or v is pd.NA or (isinstance(v, float) and v != v):
        return None
    return v.item() if hasattr(v, "item") else v


def _accepted_frame(batch: NormalizedBatch, gates: GateResult, idx: list[int]) -> pd.DataFrame:
    df = batch.df.iloc[idx].copy()
    df["counterparty"] = [gates.counterparty_canonical[i] for i in idx]
    df["quantity"] = df["quantity"].astype("int64")
    df["price"] = df["price"].astype("float64")
    return df.reset_index(drop=True)


def _base_fields(rows: list[dict], batch: NormalizedBatch, gates: GateResult, i: int) -> dict:
    """Normalized input fields of row i (canonical counterparty / trader spelling when known)."""
    r = rows[i]
    return dict(
        row_index=i, raw=batch.raw[i],
        trade_id=_py(r["trade_id"]), trade_type=_py(r["trade_type"]), symbol=_py(r["symbol"]),
        quantity=_py(r["quantity"]), price=_py(r["price"]),
        counterparty=gates.counterparty_canonical[i] or _py(r["counterparty"]),
        trade_date=_py(r["trade_date"]), settlement_date=_py(r["settlement_date"]),
        account_id=_py(r["account_id"]),
        trader_name=_py(r["trader_name"]),
        ssi_matched=_py(r["ssi_matched"]), currency_matched=_py(r["currency_matched"]),
    )


# ---- public: validate one record ------------------------------------------------
def validate_trade(record: dict, refs: ReferenceData, cfg: EngineConfig,
                   business_date: date) -> ValidationResult:
    """Hard gates for ONE record (POST /trades/validate, and before POST /trades).
    tradeId is optional here: a new UI trade gets its ID from the server (FR-TB-008).
    If valid, soft-rule warnings (weekend/past/far settlement, quantity limits) are returned
    so the form can show them next to the field."""
    batch = normalize_records([record])
    gates = run_hard_gates(batch, refs, require_trade_id=False)
    errors = gates.errors[0]
    warnings: list[str] = []
    if not errors:
        df = _accepted_frame(batch, gates, [0])
        res = evaluate_rules(df, refs, cfg, business_date)
        row = next(df.itertuples(index=False))
        for hit in build_rule_hits(row, 0, res, cfg):
            if hit.triggered and hit.rule_id in _FORM_WARNING_RULES:
                warnings.append(hit.message)
        warnings += build_warnings(row, 0, res, cfg)
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


# ---- public: score a batch ----------------------------------------------------------
def score_trades(records: list[dict], refs: ReferenceData, cfg: EngineConfig,
                 business_date: date, predictor=None) -> list[ScoredTrade]:
    """Full §6.1 pipeline for 1..10,000 raw records (any header style).
    predictor=None -> rules-only mode: ml_score=None, final_score=rule_score."""
    if not records:
        return []
    batch = normalize_records(records)
    gates = run_hard_gates(batch, refs, require_trade_id=True)
    accepted_idx = [i for i, e in enumerate(gates.errors) if not e]
    rows = batch.df.to_dict("records")                       # fast per-row access
    results: list[Optional[ScoredTrade]] = [None] * len(records)

    # 1) REJECTED rows: stop here
    for i, errs in enumerate(gates.errors):
        if errs:
            results[i] = ScoredTrade(
                **_base_fields(rows, batch, gates, i),
                status="REJECTED", priority=None,
                rule_score=None, ml_score=None, final_score=REJECTED_SCORE,
                risk_factors=[], likely_reason=errs[0].message,
                suggested_action="Rejected by validation - correct the trade in the upstream system and re-book.",
                explanation=f"Trade {_py(rows[i]['trade_id']) or '(no id)'} rejected: "
                            + "; ".join(f"{e.rule_id} {e.message}" for e in errs),
                rejection_reasons=errs, rule_hits=[], features=None, shap_values=None,
                model_version=None, warnings=[], suggested_fixes=[],
            )
    if not accepted_idx:
        return results

    # 2) rule engine (vectorised over all accepted rows)
    df = _accepted_frame(batch, gates, accepted_idx)
    res: RuleResult = evaluate_rules(df, refs, cfg, business_date)

    # 3) ML model + SHAP (rules-only if no predictor or if it fails)
    ml_scores: list[Optional[float]] = [None] * len(df)
    features: list[Optional[dict]] = [None] * len(df)
    shaps: list[Optional[dict]] = [None] * len(df)
    model_version: Optional[str] = None
    rules_only = True
    if predictor is not None:
        try:
            ml = predictor.score_batch(df, res, refs, cfg)
            ml_scores, features, shaps, model_version = ml.scores, ml.features, ml.shap_values, ml.version
            rules_only = False
        except Exception:                                     # a broken model must never stop booking
            log.exception("ML scoring failed - falling back to rules-only for this batch")

    # 4) per-trade: hybrid, classify, explain
    for k, row in enumerate(df.itertuples(index=False)):
        i = accepted_idx[k]
        hits = build_rule_hits(row, k, res, cfg)
        triggered = [h.rule_id for h in hits if h.triggered]
        msgs = {h.rule_id: h.message for h in hits}
        rule_score = float(res.rule_score[k])
        ml_score = None if ml_scores[k] is None else clamp_round(ml_scores[k])
        final = hybrid_score(rule_score, ml_score, cfg)
        status = classify(final, cfg)

        rf = build_risk_factors({h.rule_id: h.contribution for h in hits if h.triggered}, msgs,
                                counterparty=row.counterparty, alpha=cfg.alpha, beta=cfg.beta,
                                ml_score=ml_score, shap_values=shaps[k])
        fixes = build_fixes(rf, triggered, tier=int(res.tier[k]), counterparty=row.counterparty,
                            settlement_date=row.settlement_date, business_date=business_date,
                            holidays=refs.holidays, qty_min=cfg.qty_min, qty_max=cfg.qty_max,
                            far_days=cfg.settlement_far_days)
        results[i] = ScoredTrade(
            **_base_fields(rows, batch, gates, i),
            status=status, priority=priority_for(status),
            rule_score=clamp_round(rule_score), ml_score=ml_score, final_score=final,
            risk_factors=rf,
            likely_reason=rf[0]["detail"] if rf else None,
            suggested_action=" ".join(f["fix"] for f in fixes[:2]) or None,
            explanation=explanation_text(
                trade_id=row.trade_id, trade_type=row.trade_type, quantity=row.quantity,
                symbol=row.symbol, counterparty=row.counterparty, final_score=final, status=status,
                risk_factors=rf, fixes=fixes, settlement_date=row.settlement_date, rules_only=rules_only),
            rejection_reasons=[], rule_hits=hits,
            features=features[k], shap_values=shaps[k], model_version=model_version,
            warnings=build_warnings(row, k, res, cfg),
            suggested_fixes=[{"factor": f["factor"], "fix": f["fix"]} for f in fixes],
        )
    return results
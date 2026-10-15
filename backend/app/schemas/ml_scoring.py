"""ML scoring API schemas (ML module, PROJECT_CONTEXT 8.1 #45 and #47).

Same camelCase field names as TradeResult in schemas/trade.py, so the frontend can reuse
its result card. Self-contained on purpose: nothing here depends on other backend schema files.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

MAX_BATCH = 1000

Loose = str | int | float | bool | None


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PredictInput(_Camel):
    """One trade to score. Fields are loose on purpose: the engine validates them and
    reports every problem with its rule id (an invalid trade comes back as REJECTED)."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore",
                              json_schema_extra={"example": {
                                  "tradeType": "BUY", "symbol": "ALV", "quantity": 5635, "price": 250.5,
                                  "counterparty": "UBS", "tradeDate": "2026-10-12",
                                  "settlementDate": "2026-10-14", "accountId": "ACC1001",
                                  "traderName": "Emma Davis", "ssiMatched": False, "currencyMatched": False}})

    trade_id: Loose = Field(None, description="Optional. Echoed back; a preview ID is used when empty")
    trade_type: Loose = None
    symbol: Loose = None
    quantity: Loose = None
    price: Loose = None
    counterparty: Loose = None
    trade_date: Loose = None
    settlement_date: Loose = None
    account_id: Loose = None
    trader_name: Loose = Field(None, description="Optional. Defaults to the logged-in user's name")
    ssi_matched: Loose = None
    currency_matched: Loose = None

    def engine_record(self, index: int, default_trader: str | None) -> dict[str, Any]:
        """camelCase dict the engine understands directly (any header style is accepted)."""
        rec = self.model_dump(by_alias=True)
        if rec.get("tradeId") in (None, ""):
            rec["tradeId"] = f"PREVIEW-{index + 1:04d}"
        if rec.get("traderName") in (None, ""):
            rec["traderName"] = default_trader
        return rec


class PredictBatchInput(_Camel):
    trades: list[PredictInput] = Field(..., min_length=1, max_length=MAX_BATCH)


class RiskFactorOut(_Camel):
    factor: str
    pct: float
    contribution: float
    detail: str


class SuggestedFixOut(_Camel):
    factor: str
    fix: str


class RejectionReasonOut(_Camel):
    rule_id: str
    field: str | None = None
    message: str


class RuleHitOut(_Camel):
    rule_id: str
    category: str
    triggered: bool
    contribution: float
    message: str | None = None


class PredictResult(_Camel):
    trade_id: str | None
    status: str
    priority: str | None
    rule_score: float | None
    ml_score: float | None
    final_score: float
    risk_factors: list[RiskFactorOut] = []
    likely_reason: str | None = None
    suggested_action: str | None = None
    explanation: str | None = None
    suggested_fixes: list[SuggestedFixOut] = []
    rejection_reasons: list[RejectionReasonOut] = []
    rule_hits: list[RuleHitOut] = []
    shap_values: dict[str, float] | None = None
    warnings: list[str] = []
    model_version: str | None = None
    business_date: str
    saved: bool = False

    @classmethod
    def from_scored(cls, scored, business_date) -> "PredictResult":
        d = scored.to_dict()
        return cls(
            trade_id=d.get("trade_id"), status=d["status"], priority=d.get("priority"),
            rule_score=d.get("rule_score"), ml_score=d.get("ml_score"), final_score=d["final_score"],
            risk_factors=d.get("risk_factors") or [], likely_reason=d.get("likely_reason"),
            suggested_action=d.get("suggested_action"), explanation=d.get("explanation"),
            suggested_fixes=d.get("suggested_fixes") or [], rejection_reasons=d.get("rejection_reasons") or [],
            rule_hits=d.get("rule_hits") or [], shap_values=d.get("shap_values"),
            warnings=d.get("warnings") or [], model_version=d.get("model_version"),
            business_date=business_date.isoformat())


class PredictSummary(_Camel):
    total: int
    pass_: int = Field(0, alias="pass")
    monitor: int = 0
    exception: int = 0
    rejected: int = 0
    avg_final_score: float | None = None


class PredictBatchResult(_Camel):
    items: list[PredictResult]
    summary: PredictSummary
    model_version: str | None
    business_date: str
    saved: bool = False

    @classmethod
    def from_results(cls, items: list[PredictResult], model_version, business_date) -> "PredictBatchResult":
        counts = {"PASS": 0, "MONITOR": 0, "EXCEPTION": 0, "REJECTED": 0}
        for it in items:
            counts[it.status] = counts.get(it.status, 0) + 1
        accepted = [it.final_score for it in items if it.status != "REJECTED"]
        summary = PredictSummary(total=len(items), pass_=counts["PASS"], monitor=counts["MONITOR"],
                                 exception=counts["EXCEPTION"], rejected=counts["REJECTED"],
                                 avg_final_score=round(sum(accepted) / len(accepted), 4) if accepted else None)
        return cls(items=items, summary=summary, model_version=model_version,
                   business_date=business_date.isoformat())


class FeatureImportanceOut(_Camel):
    feature: str
    mean_abs_shap: float

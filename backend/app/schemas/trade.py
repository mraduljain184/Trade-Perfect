"""Trade schemas - mirror PROJECT_CONTEXT section 8.2 (TradeInput, TradeResult, TradeVersion ...)."""
from datetime import date, datetime
from typing import Any

from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.schemas.base import CamelModel, FieldErrorOut

# Trade fields are deliberately loose here ("abc" is accepted for quantity): the ENGINE validates
# them, so the user gets every problem at once, each with its rule id (R-001 ... R-008).
Loose = str | int | float | bool | None


class TradeInput(CamelModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore",
                              json_schema_extra={"example": {
                                  "tradeType": "BUY", "symbol": "AAPL", "quantity": 1200, "price": 10250.5,
                                  "counterparty": "BlackRock", "tradeDate": "2026-10-12",
                                  "settlementDate": "2026-10-14", "accountId": "ACC1000",
                                  "ssiMatched": False, "currencyMatched": True}})

    trade_id: Loose = Field(None, description="Ignored on POST (the server generates it)")
    trade_type: Loose = None
    symbol: Loose = None
    quantity: Loose = None
    price: Loose = None
    counterparty: Loose = None
    trade_date: Loose = None
    settlement_date: Loose = None
    account_id: Loose = None
    ssi_matched: Loose = None
    currency_matched: Loose = None
    # PUT only
    queue_id: str | None = Field(None, description="Set when this update is an Apply Fix from the repair queue")
    applied_fix: str | None = Field(None, max_length=2000)
    change_reason: str | None = Field(None, max_length=2000)

    def engine_record(self) -> dict[str, Any]:
        """camelCase dict of the 10 business fields - the engine understands these keys directly."""
        return self.model_dump(by_alias=True, exclude={"trade_id", "queue_id", "applied_fix", "change_reason"})


class ValidationOut(CamelModel):
    valid: bool
    fields: list[FieldErrorOut] = []
    warnings: list[str] = []


class RiskFactorOut(CamelModel):
    factor: str
    pct: float
    contribution: float
    detail: str


class RuleHitOut(CamelModel):
    rule_id: str
    category: str
    triggered: bool
    contribution: float
    message: str | None = None


class RejectionReasonOut(CamelModel):
    rule_id: str
    field: str | None = None
    message: str


class TradeResult(CamelModel):
    id: str
    trade_id: str | None
    trade_type: str | None
    symbol: str | None
    quantity: int | None
    price: float | None
    counterparty: str | None
    trade_date: date | None
    settlement_date: date | None
    account_id: str | None
    ssi_matched: bool | None
    currency_matched: bool | None
    trader_name: str | None
    source: str
    status: str
    priority: str | None
    rule_score: float | None
    ml_score: float | None
    final_score: float
    risk_factors: list[RiskFactorOut] = []
    likely_reason: str | None = None
    suggested_action: str | None = None
    explanation: str | None = None
    rejection_reasons: list[RejectionReasonOut] | None = None
    rule_hits: list[RuleHitOut] | None = None
    shap_values: dict[str, float] | None = None
    warnings: list[str] | None = None
    queue_id: str | None = None
    queue_status: str | None = None
    assigned_to: str | None = None
    is_resolved: bool
    version: int
    model_version: str | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None


class AuditBoxOut(CamelModel):
    trade_id: str | None
    queue_id: str | None
    risk_score: float
    likely_reason: str | None
    recommended_fix: str | None
    applied_fix: str | None
    applied_by: str | None
    applied_timestamp: datetime | None
    status: str


class TradeDetail(TradeResult):
    versions_count: int
    audit_box: AuditBoxOut


class TradeVersionOut(CamelModel):
    version: int
    change_type: str
    changed_fields: dict[str, Any] | None
    change_reason: str | None
    changed_by: str | None
    changed_at: datetime
    snapshot: dict[str, Any]

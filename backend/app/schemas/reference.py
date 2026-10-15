"""Reference-data schemas (endpoints 4-7)."""
from app.schemas.base import CamelModel


class SymbolOut(CamelModel):
    symbol: str


class CounterpartyOut(CamelModel):
    name: str
    risk_tier: int
    tier_label: str


class AccountOut(CamelModel):
    account_id: str
    is_active: bool


class UserBrief(CamelModel):
    id: int
    username: str
    full_name: str
    role: str

"""Master data + historical training data (schema.sql sections 3-4)."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TradeTypeEnum


class Counterparty(Base):
    __tablename__ = "counterparties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    historical_trade_count: Mapped[int] = mapped_column(Integer, server_default="0")
    historical_fail_count: Mapped[int] = mapped_column(Integer, server_default="0")
    historical_fail_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), server_default="0")
    risk_tier: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    tier_label: Mapped[str] = mapped_column(String(30), server_default="Tier1_HighTrust")
    tier_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    price_mean: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    price_std: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    qty_mean: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    qty_std: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    historical_fail_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(20), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class Holiday(Base):
    __tablename__ = "holidays"

    holiday_date: Mapped[date] = mapped_column(Date, primary_key=True)
    market: Mapped[str] = mapped_column(String(10), primary_key=True, server_default="GLOBAL")
    description: Mapped[str | None] = mapped_column(String(100))


class HistoricalTrade(Base):
    __tablename__ = "historical_trades"

    trade_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_type: Mapped[str] = mapped_column(TradeTypeEnum)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    quantity: Mapped[int] = mapped_column(BigInteger)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    counterparty: Mapped[str] = mapped_column(String(100), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    settlement_date: Mapped[date] = mapped_column(Date)
    account_id: Mapped[str] = mapped_column(String(20))
    ssi_matched: Mapped[bool] = mapped_column(Boolean)
    currency_matched: Mapped[bool] = mapped_column(Boolean)
    trader_name: Mapped[str] = mapped_column(String(100))
    settlement_failed: Mapped[bool] = mapped_column(Boolean)

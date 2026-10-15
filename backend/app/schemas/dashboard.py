"""Dashboard schemas (endpoints 18-19)."""
from datetime import date

from pydantic import Field

from app.schemas.base import CamelModel


class StatusPct(CamelModel):
    pass_: float = Field(0.0, alias="pass")
    monitor: float = 0.0
    exception: float = 0.0
    rejected: float = 0.0


class StatusCounts(CamelModel):
    total: int = 0
    pass_: int = Field(0, alias="pass")
    monitor: int = 0
    exception: int = 0
    rejected: int = 0
    pct: StatusPct = StatusPct()


class DashboardSummary(CamelModel):
    today: StatusCounts
    historical: StatusCounts


class StatusByDate(CamelModel):
    date: date
    pass_: int = Field(0, alias="pass")
    monitor: int = 0
    exception: int = 0
    rejected: int = 0


class StatusByName(CamelModel):
    name: str
    total: int = 0
    pass_: int = Field(0, alias="pass")
    monitor: int = 0
    exception: int = 0
    rejected: int = 0


class HistogramBucket(CamelModel):
    bucket: str  # "0.0-0.1"
    min: float
    max: float
    count: int


class DashboardCharts(CamelModel):
    by_status_over_time: list[StatusByDate]
    by_counterparty: list[StatusByName]
    by_symbol: list[StatusByName]
    risk_score_histogram: list[HistogramBucket]

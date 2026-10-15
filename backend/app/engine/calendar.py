"""Business-day helpers: weekend/holiday checks, next business day, month end.

A *business day* is Mon-Fri and not in the holidays set (PROJECT_CONTEXT §1, Q9).
"""
from __future__ import annotations

import calendar as _stdcal
from datetime import date, datetime, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

DEFAULT_TZ = "Asia/Kolkata"
MONTH_END_DAYS = 3          # "settlement in last 3 days of month" (§6.5 Is_Month_End)
_MAX_LOOKAHEAD = 31         # safety stop for next_business_day


# ---- single-date helpers (used by explain/fixes and single-trade flows) ----
def is_weekend(d: date) -> bool:
    return d.weekday() >= 5                      # 5 = Saturday, 6 = Sunday


def is_holiday(d: date, holidays: Iterable[date] = ()) -> bool:
    return d in holidays


def is_business_day(d: date, holidays: Iterable[date] = ()) -> bool:
    return not is_weekend(d) and not is_holiday(d, holidays)


def next_business_day(d: date, holidays: Iterable[date] = (), include_same_day: bool = False) -> date:
    """First business day AFTER d (or d itself if include_same_day and d is a business day)."""
    holidays = set(holidays)
    cur = d if include_same_day else d + timedelta(days=1)
    for _ in range(_MAX_LOOKAHEAD):
        if is_business_day(cur, holidays):
            return cur
        cur += timedelta(days=1)
    raise ValueError(f"No business day within {_MAX_LOOKAHEAD} days after {d}")


def is_month_end(d: date, days: int = MONTH_END_DAYS) -> bool:
    last = _stdcal.monthrange(d.year, d.month)[1]
    return d.day > last - days


def resolve_business_date(override: Optional[date | str] = None, tz: str = DEFAULT_TZ) -> date:
    """§6.4: config override if set, else today in Asia/Kolkata."""
    if override:
        return override if isinstance(override, date) else date.fromisoformat(str(override))
    return datetime.now(ZoneInfo(tz)).date()


# ---- vectorised helpers (used for 10K-row batches and training) ----------
def to_datetime64(dates: pd.Series) -> pd.Series:
    """Series of python dates/None -> datetime64 (NaT for None)."""
    return pd.to_datetime(dates, errors="coerce")


def weekend_mask(dt: pd.Series) -> np.ndarray:
    return (dt.dt.dayofweek >= 5).fillna(False).to_numpy(dtype=bool)


def holiday_mask(dt: pd.Series, holidays: Iterable[date]) -> np.ndarray:
    hol = pd.to_datetime(pd.Series(sorted(set(holidays)), dtype="object"))
    if len(hol) == 0:
        return np.zeros(len(dt), dtype=bool)
    return dt.isin(hol).to_numpy(dtype=bool)


def month_end_mask(dt: pd.Series, days: int = MONTH_END_DAYS) -> np.ndarray:
    return (dt.dt.day > dt.dt.days_in_month - days).fillna(False).to_numpy(dtype=bool)


def days_between(start: pd.Series, end: pd.Series) -> pd.Series:
    """end - start in calendar days (float, NaN if either side missing)."""
    return (end - start).dt.days.astype("float64")
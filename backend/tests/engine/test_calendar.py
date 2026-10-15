from datetime import date

from app.engine.calendar import (is_business_day, is_month_end, next_business_day,
                                 resolve_business_date)

HOL = {date(2026, 10, 2)}       # Friday


def test_weekend_and_holiday_are_not_business_days():
    assert not is_business_day(date(2026, 10, 3))          # Saturday
    assert not is_business_day(date(2026, 10, 4))          # Sunday
    assert not is_business_day(date(2026, 10, 2), HOL)     # holiday
    assert is_business_day(date(2026, 10, 1), HOL)         # Thursday


def test_next_business_day_skips_holiday_and_weekend():
    assert next_business_day(date(2026, 10, 1), HOL) == date(2026, 10, 5)    # Thu -> (Fri hol, Sat, Sun) -> Mon
    assert next_business_day(date(2026, 10, 10)) == date(2026, 10, 12)       # Sat -> Mon
    assert next_business_day(date(2026, 10, 12), include_same_day=True) == date(2026, 10, 12)


def test_month_end_is_last_three_days():
    assert is_month_end(date(2026, 9, 28)) and is_month_end(date(2026, 9, 30))
    assert not is_month_end(date(2026, 9, 27))


def test_business_date_override():
    assert resolve_business_date("2026-09-28") == date(2026, 9, 28)
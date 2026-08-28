"""Jalali calendar tests.

TSETMC/IME/CODAL dates are Jalali; a wrong conversion shifts every bar and
silently corrupts weekly/monthly resampling and session-anchored VWAP.  The
reference dates below are the Nowruz days, and when ``jdatetime`` happens to be
installed the whole range is cross-checked against it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from iran_market import jalali

NOWRUZ = [
    ((1399, 1, 1), date(2020, 3, 20)),
    ((1400, 1, 1), date(2021, 3, 21)),
    ((1401, 1, 1), date(2022, 3, 21)),
    ((1402, 1, 1), date(2023, 3, 21)),
    ((1403, 1, 1), date(2024, 3, 20)),
    ((1404, 1, 1), date(2025, 3, 21)),
]


@pytest.mark.parametrize(("jalali_date", "gregorian"), NOWRUZ)
def test_nowruz_reference_dates(jalali_date, gregorian):
    assert jalali.jalali_to_gregorian(*jalali_date) == gregorian
    assert jalali.gregorian_to_jalali(gregorian) == jalali_date


def test_round_trip_over_a_decade():
    current = date(2020, 3, 1)
    for _ in range(4000):
        converted = jalali.gregorian_to_jalali(current)
        assert jalali.jalali_to_gregorian(*converted) == current
        current += timedelta(days=1)


def test_leap_years_and_month_lengths():
    # 1399 and 1403 both ended on 30 Esfand; 1402 did not
    assert jalali.is_jalali_leap(1399) is True
    assert jalali.is_jalali_leap(1402) is False
    assert jalali.is_jalali_leap(1403) is True
    assert jalali.jalali_month_length(1399, 12) == 30
    assert jalali.jalali_month_length(1402, 12) == 29
    assert jalali.jalali_month_length(1403, 12) == 30
    assert jalali.gregorian_to_jalali(date(2025, 3, 20)) == (1403, 12, 30)
    assert jalali.jalali_month_length(1403, 1) == 31
    assert jalali.jalali_month_length(1403, 7) == 30


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (14030415, date(2024, 7, 5)),
        ("1403-04-15", date(2024, 7, 5)),
        (0, None),
        (None, None),
        ("", None),
    ],
)
def test_parse_deven(raw, expected):
    assert jalali.parse_deven(raw) == expected


def test_parse_deven_reads_hyphenated_input_as_jalali():
    """TSETMC's dEven is always a Jalali date, whatever the separator."""
    assert jalali.parse_deven("1403-04-15") == jalali.parse_deven(14030415) == date(2024, 7, 5)
    assert jalali.parse_deven("1403/04/15") == date(2024, 7, 5)


def test_format_helpers():
    assert jalali.format_jalali(date(2024, 3, 20)) == "1403/01/01"
    assert jalali.format_jalali_long(date(2024, 3, 20)) == "1 فروردین 1403"
    assert jalali.jalali_weekday(date(2024, 3, 20)) == "چهارشنبه"


def test_tse_calendar_skips_friday():
    friday = date(2024, 3, 22)  # a Friday
    assert jalali.is_tse_trading_day(friday) is False
    assert jalali.is_tse_trading_day(friday - timedelta(days=1)) is True
    days = jalali.trading_days_back(friday + timedelta(days=3), 5)
    assert len(days) == 5
    assert all(d.weekday() != 4 for d in days)
    assert all(days[i] < days[i + 1] for i in range(len(days) - 1))


def test_cross_check_against_jdatetime_when_available():
    jdatetime = pytest.importorskip("jdatetime")
    current = date(2015, 3, 21)
    for _ in range(5000):
        expected = jdatetime.date.fromgregorian(date=current)
        assert jalali.gregorian_to_jalali(current) == (expected.year, expected.month, expected.day)
        current += timedelta(days=1)

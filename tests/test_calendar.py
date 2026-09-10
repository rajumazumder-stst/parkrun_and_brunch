"""The calendar week scheme — pure functions, plus one parity check.

The drawing itself is judged by eye (that is what `calendar_proto.py` is for),
but the week arithmetic underneath it is not a matter of taste: it decides
which square a run lands in, and it is written twice — once in Python and once
in SQL. Both are checked here, against each other and against the dates.

No app runtime and no project database: the SQL check builds its own in-memory
DuckDB from a handful of dates.
"""
from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd
import pytest

import parkrun_calendar as cal


# --------------------------------------------------------------------------- #
# week_of_year — 7-day blocks counted from 1 January
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("date, week", [
    ((2025, 1, 1), 1),      # first day of the year
    ((2025, 1, 7), 1),      # last day of week 1
    ((2025, 1, 8), 2),      # first day of week 2
    ((2025, 12, 30), 52),
    ((2025, 12, 31), 53),   # the stub: one day, on its own
    ((2024, 12, 30), 53),   # a leap year makes the stub two days
    ((2024, 12, 31), 53),
    ((2024, 12, 29), 52),
])
def test_week_of_year(date, week):
    assert cal.week_of_year(dt.date(*date)) == week


def test_every_day_of_a_year_lands_in_1_to_53():
    for year in (2023, 2024):          # ordinary, then leap
        day = dt.date(year, 1, 1)
        while day.year == year:
            assert 1 <= cal.week_of_year(day) <= cal.WEEKS_PER_YEAR
            day += dt.timedelta(days=1)


def test_week_span_round_trips():
    """Every date in a week's span reports that week, and only that week."""
    for wk in range(1, cal.WEEKS_PER_YEAR + 1):
        start, end = cal.week_span(2025, wk)
        day = start
        while day <= end:
            assert cal.week_of_year(day) == wk
            day += dt.timedelta(days=1)


def test_week_53_is_the_leftover_not_a_week():
    """1-2 days, never seven — which is why it is only drawn conditionally."""
    ordinary = cal.week_span(2025, cal.STUB_WEEK)
    leap = cal.week_span(2024, cal.STUB_WEEK)
    assert (ordinary[1] - ordinary[0]).days == 0
    assert (leap[1] - leap[0]).days == 1


def test_series_matches_the_scalar():
    days = pd.Series(pd.date_range("2024-12-20", "2025-01-10", freq="D"))
    expected = [cal.week_of_year(d.date()) for d in days]
    assert list(cal.week_of_year_series(days)) == expected


def test_sql_matches_python():
    """The DuckDB expression and `week_of_year` must agree, or a run lands in a
    different square depending on which one asked.

    `floor(...)::INT` is the whole point: a bare `::INT` ROUNDS in DuckDB, which
    silently pushed late-December runs into week 53 the first time these
    numbers were computed.
    """
    days = pd.date_range("2024-01-01", "2026-12-31", freq="D")
    expr = cal.WEEK_SQL.format(col="d")
    con = duckdb.connect()
    # Registered explicitly rather than left to DuckDB's replacement scan, so
    # the frame is not an apparently unused local.
    con.register("days", pd.DataFrame({"d": days}))
    got = con.execute(f"SELECT d, {expr} AS wk FROM days ORDER BY d").df()
    expected = [cal.week_of_year(d.date()) for d in days]
    assert list(got["wk"]) == expected


# --------------------------------------------------------------------------- #
# stub_visible_years — when the leftover is worth a square at all
# --------------------------------------------------------------------------- #

def _runs(*year_week_pairs) -> pd.DataFrame:
    return pd.DataFrame({"iso_year": [y for y, _ in year_week_pairs],
                         "iso_week": [w for _, w in year_week_pairs]})


def test_stub_kept_only_where_a_saturday_falls_in_it():
    years = list(range(2007, 2027))
    got = cal.stub_visible_years(_runs(), years)
    # 31 December is a Saturday in these three; in the other seventeen the
    # leftover holds no day a parkrun could be run on.
    assert got == {2011, 2016, 2022}


def test_stub_kept_where_somebody_ran_in_it_anyway():
    """Christmas and New Year fixtures do not care what day of the week it is."""
    got = cal.stub_visible_years(_runs((2025, cal.STUB_WEEK)), [2024, 2025])
    assert got == {2025}


def test_stub_dropped_when_the_only_runs_are_in_other_weeks():
    got = cal.stub_visible_years(_runs((2025, 1), (2025, 52)), [2025])
    assert got == set()

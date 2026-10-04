"""Milestone arithmetic — `milestones.py`, a pure function over a list of run
dates, plus the single-definition contract on `milestone_config.py`.

No database: each test builds the dates it needs. `today` is always pinned.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re

from milestone_config import (ALWAYS_SHOWN, MILESTONE_COLOURS, MILESTONES,
                              milestones_for)
from milestones import milestone_summary

TODAY = dt.date(2026, 10, 4)
START = dt.date(2020, 1, 4)   # a Saturday


def saturdays(n, start=START):
    return [start + dt.timedelta(weeks=i) for i in range(n)]


def test_zero_runs():
    s = milestone_summary([], today=TODAY)
    assert s.total == 0
    assert s.first_date is s.last_date is None
    assert s.days_since_first is s.days_since_last is None
    assert s.days_first_to_last is None
    assert s.reached == []
    assert (s.next_milestone, s.runs_to_go) == (25, 25)


def test_zero_runs_junior_is_next_to_ten():
    s = milestone_summary([], junior=True, today=TODAY)
    assert (s.next_milestone, s.runs_to_go) == (10, 10)


def test_one_run():
    d = dt.date(2026, 9, 26)
    s = milestone_summary([d], today=TODAY)
    assert s.total == 1
    assert s.first_date == s.last_date == d
    assert s.days_since_first == s.days_since_last == 8
    assert s.days_first_to_last == 0
    assert s.reached == []
    assert (s.next_milestone, s.runs_to_go) == (25, 24)


def test_total_exactly_on_a_milestone():
    dates = saturdays(50)
    s = milestone_summary(dates, today=TODAY)
    assert [m.n for m in s.reached] == [25, 50]
    fifty = s.reached[-1]
    assert fifty.date == dates[49]
    assert fifty.days_since_first == 49 * 7
    assert fifty.days_since_prev == 25 * 7
    assert (s.next_milestone, s.runs_to_go) == (100, 50)


def test_first_milestone_counts_from_the_first_run():
    s = milestone_summary(saturdays(30), today=TODAY)
    first = s.reached[0]
    assert first.n == 25
    assert first.days_since_prev == first.days_since_first == 24 * 7


def test_junior_hits_ten():
    dates = saturdays(12)
    s = milestone_summary(dates, junior=True, today=TODAY)
    assert [m.n for m in s.reached] == [10]
    assert s.reached[0].date == dates[9]
    assert s.reached[0].days_since_prev == 9 * 7
    assert (s.next_milestone, s.runs_to_go) == (25, 13)
    # 25 then counts from the 10th, not from the first run
    s30 = milestone_summary(saturdays(30), junior=True, today=TODAY)
    assert s30.reached[1].days_since_prev == 15 * 7


def test_adult_never_gets_ten():
    s = milestone_summary(saturdays(12), today=TODAY)
    assert s.reached == []
    assert s.next_milestone == 25
    assert 10 not in milestones_for(junior=False)
    s = milestone_summary(saturdays(300), today=TODAY)
    assert 10 not in [m.n for m in s.reached]


def test_multiple_runs_on_the_same_date():
    # 24 Saturdays, then a New Year's Day double: runs 25 and 26 share a date
    nyd = dt.date(2021, 1, 1)
    dates = saturdays(24) + [nyd, nyd]
    s = milestone_summary(dates, today=TODAY)
    assert s.total == 26
    assert s.reached[0].n == 25 and s.reached[0].date == nyd
    assert s.last_date == nyd


def test_milestone_on_the_first_day_is_zero_days_in():
    nyd = dt.date(2021, 1, 1)
    s = milestone_summary([nyd] * 10, junior=True, today=TODAY)
    assert s.reached[0].date == nyd
    assert s.reached[0].days_since_first == s.reached[0].days_since_prev == 0


def test_unsorted_input_is_sorted():
    dates = saturdays(25)
    s = milestone_summary(list(reversed(dates)), today=TODAY)
    assert s.first_date == dates[0] and s.last_date == dates[-1]
    assert s.reached[0].date == dates[24]


def test_last_milestone_reached_next_is_none():
    dates = [START + dt.timedelta(days=i) for i in range(1003)]
    s = milestone_summary(dates, today=TODAY)
    assert s.reached[-1].n == 1000
    assert s.reached[-1].date == dates[999]
    assert s.next_milestone is None
    assert s.runs_to_go is None


def test_days_since_last_is_relative_to_today():
    s = milestone_summary([dt.date(2026, 10, 3)], today=TODAY)
    assert s.days_since_last == 1


def test_days_first_to_last_ignores_today():
    dates = saturdays(50)
    s = milestone_summary(dates, today=TODAY)
    assert s.days_first_to_last == 49 * 7
    later = milestone_summary(dates, today=TODAY + dt.timedelta(days=100))
    assert later.days_first_to_last == 49 * 7


def test_milestone_list_and_colours_are_one_definition():
    assert MILESTONES == (10, 25, 50, 100, 200, 250, 300, 400, 500, 600, 700,
                          800, 900, 1000)
    assert ALWAYS_SHOWN == (10, 25, 50, 100, 200, 250, 300, 400, 500)
    assert all(re.fullmatch(r"#[0-9A-F]{6}", c.bg) and
               re.fullmatch(r"#[0-9A-F]{6}", c.fg)
               for c in MILESTONE_COLOURS.values())
    # No other module writes a milestone colour out again.
    repo = pathlib.Path(__file__).resolve().parent.parent
    for path in repo.glob("*.py"):
        if path.name == "milestone_config.py":
            continue
        text = path.read_text()
        for hexes in ("#695D9E", "#C81D30", "#2D514B", "#890030", "#2E4EA9"):
            assert hexes.lower() not in text.lower(), (path.name, hexes)

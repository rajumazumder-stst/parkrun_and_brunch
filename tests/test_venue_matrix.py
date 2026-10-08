"""The tab 1 unique-parkruns matrix — `venue_matrix.py` fed hand-built frames.
No database."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from milestone_config import MILESTONE_COLOURS, UNIQUE_MILESTONES
from milestones import milestone_summary
from venue_matrix import (first_visits, venue_columns, venue_matrix_html,
                          venue_rows)

TODAY = dt.date(2026, 10, 4)
START = dt.date(2017, 1, 7)


def _firsts(name, n, start=START):
    return pd.DataFrame(dict(
        athlete_id=1, athlete_name=name, event_id=range(n),
        short_name=[f"Park {i:03d}" for i in range(n)],
        first_date=[start + dt.timedelta(weeks=i) for i in range(n)]))


def _rows():
    firsts = pd.concat([_firsts("George", 133), _firsts("Raju", 40)])
    return venue_rows(firsts, ["George", "Raju"],
                      {"George": 354, "Raju": 60}, TODAY)


def test_ladder_is_multiples_of_fifty():
    assert UNIQUE_MILESTONES[:4] == (50, 100, 150, 200)
    assert all(m % 50 == 0 for m in UNIQUE_MILESTONES)


def test_custom_ladder_leaves_default_summary_alone():
    dates = [START + dt.timedelta(weeks=i) for i in range(60)]
    assert [m.n for m in milestone_summary(dates, today=TODAY).reached] == [25, 50]
    s = milestone_summary(dates, today=TODAY, ladder=UNIQUE_MILESTONES)
    assert [m.n for m in s.reached] == [50]
    assert (s.next_milestone, s.runs_to_go) == (100, 40)


def test_rows_and_columns():
    rows = _rows()
    assert [r["name"] for r in rows] == ["George", "Raju"]
    assert [m.n for m in rows[0]["s"].reached] == [50, 100]
    assert rows[0]["s"].next_milestone == 150
    assert rows[1]["s"].runs_to_go == 10
    # up to the furthest anyone is next to, no further
    assert venue_columns(rows) == [50, 100, 150]


def test_html_names_the_venue_and_stays_neutral():
    html = venue_matrix_html(_rows())
    d100 = START + dt.timedelta(weeks=99)
    assert f"100th different parkrun: Park 099, {d100.day} {d100:%b %Y}" in html
    assert "1st parkrun: Park 000" in html
    assert "17 to go" in html          # George, 133 → 150
    assert "of 354 runs" in html
    # never the official milestone colours
    for c in MILESTONE_COLOURS.values():
        if c.bg.upper() not in ("#FFFFFF", "#000000"):
            assert c.bg.lower() not in html.lower()


def test_first_visits_from_the_milestone_frame():
    d1, d2 = dt.date(2020, 1, 4), dt.date(2020, 1, 11)
    runs = pd.DataFrame(dict(
        athlete_name=["Raju"] * 4,
        run_date=[d1, d1, d2, d2],          # same-day double on d1
        event_id=[1, 2, 1, 3],
        short_name=["A", "B", "A", "C"]))
    firsts, totals = first_visits(runs)
    assert totals == {"Raju": 4}            # every run, repeats included
    got = dict(zip(firsts["short_name"], firsts["first_date"]))
    assert got == {"A": d1, "B": d1, "C": d2}

"""The tab 1 milestone matrix's rows, columns and HTML — `milestone_matrix.py`
fed hand-built frames. No database."""
from __future__ import annotations

import datetime as dt
import re

import pandas as pd

from milestone_matrix import (matrix_html, milestone_columns, milestone_rows,
                              ordinal, short_date)
from milestones import milestone_summary

TODAY = dt.date(2026, 10, 4)
GEORGE, RAJU = 3087156, 5672   # George pushes a buggy; Raju never has


def _runs(athlete_id, name, n, buggy_every=0, start=dt.date(2017, 1, 7)):
    return pd.DataFrame(dict(
        athlete_id=athlete_id, athlete_name=name,
        run_date=[start + dt.timedelta(weeks=i) for i in range(n)],
        is_buggy=[bool(buggy_every) and i % buggy_every == 0 for i in range(n)]))


def _rows():
    runs = pd.concat([_runs(GEORGE, "George", 120, buggy_every=4),
                      _runs(RAJU, "Raju", 60)])
    return milestone_rows(runs, ["George", "Raju"], TODAY)


def test_buggy_runner_gets_a_junior_row_beneath_them():
    rows = _rows()
    assert [(r["name"], r["junior"]) for r in rows] == [
        ("George", False), ("George", True), ("Raju", False)]
    assert rows[0]["s"].total == 120     # every run, buggy or not
    assert rows[1]["s"].total == 30      # the buggy ones only
    assert [m.n for m in rows[1]["s"].reached] == [10, 25]


def test_html_row_order_and_adult_ten_dash():
    html = matrix_html(_rows())
    body = html.split("<tbody>")[1]
    names = re.findall(r"class='ms-name[^']*'[^>]*>(.*?)</td>", body)
    assert "George" in names[0] and names[1].startswith("↳") and "Raju" in names[2]
    # adults show a dash under 10; the junior row has a date there
    trs = body.split("<tr>")[1:]
    assert ">–</td>" in trs[0] and ">–</td>" in trs[2]
    assert ">–</td>" not in trs[1]


def test_reached_cell_and_tooltip_sentence():
    html = matrix_html(_rows())
    d100 = dt.date(2017, 1, 7) + dt.timedelta(weeks=99)
    d50 = dt.date(2017, 1, 7) + dt.timedelta(weeks=49)
    assert (f"George · 100th on {d100.day} {d100:%b %Y} · "
            f"{(d100 - dt.date(2017, 1, 7)).days:,} days since first · "
            f"{(d100 - d50).days} days since 50th") in html
    assert f">{short_date(d100)}<br>" in html


def test_next_milestone_is_dashed_with_runs_to_go():
    html = matrix_html(_rows())
    assert "border-style:dashed" in html
    assert ">80 to go<" in html      # George 120 → 200
    assert ">40 to go<" in html      # Raju 60 → 100


def test_last_column_is_first_to_latest():
    html = matrix_html(_rows())
    assert "parkrun lifetime<br>(days)" in html
    span = 119 * 7                                  # George's 120 Saturdays
    assert f"<td class='ms-days' title='George · {span:,} days from the 1st" in html
    assert f">{span:,}</td>" in html


def test_columns_above_500_only_when_reached_or_next():
    small = [milestone_summary([TODAY] * 120, today=TODAY)]
    assert milestone_columns(small)[-1] == 500
    near = [milestone_summary([TODAY] * 550, today=TODAY)]
    assert milestone_columns(near)[-1] == 600
    big = [milestone_summary([TODAY] * 1000, today=TODAY)]
    assert milestone_columns(big)[-1] == 1000


def test_numbers_and_dates_are_formatted_for_the_uk():
    assert ordinal(1000) == "1,000th" and ordinal(1) == "1st" and ordinal(25) == "25th"
    assert short_date(dt.date(2026, 3, 14)) == "14 Mar 26"

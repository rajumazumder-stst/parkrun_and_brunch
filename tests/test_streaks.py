"""Win/loss streaks — `streaks.py`, pure functions over `v_head_to_head`-shaped
frames, plus the one wording helper that lives with the tab 2 renderer.

A win is 1st place (`place_rank == 1`); anything else is a loss, so a 2nd in a
three-way is a loss. A dead heat for 1st is a win for everyone sharing it.
No database: each test builds the handful of rows it needs.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from h2h_streaks import streak_text
from streaks import (ANY, THREE_WAY, best_period, best_range, heatmap_column,
                     heatmap_columns, opponents_label, uk_date, win_streaks)

GR = "George vs Raju"
DR = "Duncan vs Raju"
DGR = "Duncan vs George vs Raju"


def _h2h(rows):
    """rows: (date, event_id, classification, {athlete: place_rank})."""
    out = []
    for date, event_id, cls, places in rows:
        for name, rank in places.items():
            out.append(dict(run_date=pd.Timestamp(date), event_id=event_id,
                            classification=cls, athlete_name=name,
                            place_rank=rank))
    return pd.DataFrame(out)


def _row(streaks, name, scope):
    m = streaks[(streaks["athlete_name"] == name) & (streaks["scope"] == scope)]
    assert len(m) == 1, f"expected one row for {name} / {scope}"
    return m.iloc[0]


def test_current_streak_counts_back_from_the_latest_result():
    s = win_streaks(_h2h([
        ("2026-01-03", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-10", 1, GR, {"George": 2, "Raju": 1}),
        ("2026-01-17", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-24", 1, GR, {"George": 1, "Raju": 2}),
    ]))
    g = _row(s, "George", GR)
    assert (g["won"], g["n"], g["since"]) == (True, 2, pd.Timestamp("2026-01-17"))
    r = _row(s, "Raju", GR)
    assert (r["won"], r["n"], r["since"]) == (False, 2, pd.Timestamp("2026-01-17"))


def test_second_in_a_three_way_is_a_loss():
    s = win_streaks(_h2h([
        ("2026-01-03", 1, DGR, {"Duncan": 1, "Raju": 2, "George": 3}),
        ("2026-01-10", 1, DGR, {"Duncan": 1, "Raju": 2, "George": 3}),
    ]))
    r = _row(s, "Raju", DGR)
    assert (r["won"], r["n"]) == (False, 2)


def test_a_dead_heat_for_first_is_a_win_for_both():
    s = win_streaks(_h2h([
        ("2026-01-03", 1, GR, {"George": 1, "Raju": 1}),
    ]))
    assert _row(s, "George", GR)["won"]
    assert _row(s, "Raju", GR)["won"]


def test_any_merges_classifications_in_date_order():
    s = win_streaks(_h2h([
        ("2026-01-03", 1, GR, {"George": 2, "Raju": 1}),
        ("2026-01-10", 2, DR, {"Duncan": 2, "Raju": 1}),
        ("2026-01-17", 1, DGR, {"Duncan": 2, "Raju": 1, "George": 3}),
        ("2026-01-24", 2, DR, {"Duncan": 1, "Raju": 2}),
    ]))
    r = _row(s, "Raju", ANY)
    assert (r["won"], r["n"], r["since"]) == (False, 1, pd.Timestamp("2026-01-24"))
    # Before that loss Raju had won three in a row across three classifications.
    assert (r["best_n"], r["best_since"], r["best_until"]) == (
        3, pd.Timestamp("2026-01-03"), pd.Timestamp("2026-01-17"))


def test_one_row_per_classification_the_athlete_is_in_plus_any():
    s = win_streaks(_h2h([
        ("2026-01-03", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-10", 2, DR, {"Duncan": 1, "Raju": 2}),
    ]))
    assert sorted(s.loc[s["athlete_name"] == "Raju", "scope"]) == sorted([ANY, DR, GR])
    assert sorted(s.loc[s["athlete_name"] == "George", "scope"]) == sorted([ANY, GR])


def test_best_ever_is_the_longest_win_run_and_the_latest_on_a_tie():
    s = win_streaks(_h2h([
        ("2026-01-03", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-10", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-17", 1, GR, {"George": 2, "Raju": 1}),
        ("2026-01-24", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-31", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-02-07", 1, GR, {"George": 2, "Raju": 1}),
    ]))
    g = _row(s, "George", GR)
    assert (g["best_n"], g["best_since"], g["best_until"]) == (
        2, pd.Timestamp("2026-01-24"), pd.Timestamp("2026-01-31"))


def test_never_won_has_no_best():
    s = win_streaks(_h2h([
        ("2026-01-03", 1, GR, {"George": 1, "Raju": 2}),
    ]))
    r = _row(s, "Raju", GR)
    assert r["best_n"] == 0
    assert pd.isna(r["best_since"])


def test_same_day_order_is_deterministic_by_event_id():
    rows = [
        ("2026-01-03", 9, GR, {"George": 2, "Raju": 1}),
        ("2026-01-03", 4, GR, {"George": 1, "Raju": 2}),
    ]
    a = win_streaks(_h2h(rows))
    b = win_streaks(_h2h(list(reversed(rows))))
    # event 9 sorts last, so it is the latest result: George lost it.
    assert not _row(a, "George", GR)["won"]
    pd.testing.assert_frame_equal(a, b)


def test_empty_input_gives_an_empty_frame():
    assert win_streaks(_h2h([])).empty


def test_opponents_label():
    assert opponents_label("George", ANY) == "Any"
    assert opponents_label("George", GR) == "vs Raju"
    assert opponents_label("Raju", DGR) == "vs Duncan & George"


def test_streak_text_is_singular_for_one():
    assert streak_text(1, True) == "1 win"
    assert streak_text(4, True) == "4 wins"
    assert streak_text(1, False) == "1 loss"
    assert streak_text(2, False) == "2 losses"


def test_best_range():
    d = dt.date
    assert best_range(d(2024, 3, 2), d(2024, 3, 30)) == "Mar 2024"
    assert best_range(d(2024, 3, 2), d(2024, 7, 13)) == "Mar–Jul 2024"
    assert best_range(d(2023, 11, 4), d(2024, 2, 10)) == "Nov 2023–Feb 2024"


def test_a_single_result():
    s = win_streaks(_h2h([("2026-01-03", 1, GR, {"George": 1, "Raju": 2})]))
    g = _row(s, "George", GR)
    assert (g["won"], g["n"], g["since"], g["last_date"]) == (
        True, 1, pd.Timestamp("2026-01-03"), pd.Timestamp("2026-01-03"))
    assert (g["best_n"], g["live"]) == (1, True)
    r = _row(s, "Raju", GR)
    assert (r["won"], r["n"], r["best_n"], r["live"]) == (False, 1, 0, False)


def test_an_all_wins_history_is_one_live_streak():
    s = win_streaks(_h2h([
        (f"2026-01-{d:02d}", 1, GR, {"George": 1, "Raju": 2}) for d in (3, 10, 17, 24)
    ]))
    g = _row(s, "George", GR)
    assert (g["won"], g["n"], g["since"]) == (True, 4, pd.Timestamp("2026-01-03"))
    assert (g["best_n"], g["best_since"], g["best_until"], g["live"]) == (
        4, pd.Timestamp("2026-01-03"), pd.Timestamp("2026-01-24"), True)


def test_a_current_streak_that_equals_the_record_is_live_and_is_the_record():
    s = win_streaks(_h2h([
        ("2026-01-03", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-10", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-17", 1, GR, {"George": 2, "Raju": 1}),
        ("2026-01-24", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-31", 1, GR, {"George": 1, "Raju": 2}),
    ]))
    g = _row(s, "George", GR)
    assert g["live"]
    # Ties go to the most recent, so the record IS the current streak.
    assert (g["best_since"], g["best_until"]) == (g["since"], g["last_date"])


def test_a_shorter_current_win_streak_is_not_live():
    s = win_streaks(_h2h([
        ("2026-01-03", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-10", 1, GR, {"George": 1, "Raju": 2}),
        ("2026-01-17", 1, GR, {"George": 2, "Raju": 1}),
        ("2026-01-24", 1, GR, {"George": 1, "Raju": 2}),
    ]))
    g = _row(s, "George", GR)
    assert (g["won"], g["n"], g["best_n"], g["live"]) == (True, 1, 2, False)


def test_uk_date_has_no_leading_zero():
    assert uk_date(dt.date(2026, 10, 3)) == "3 Oct 2026"
    assert uk_date(pd.Timestamp("2026-08-01")) == "1 Aug 2026"
    assert uk_date(dt.date(2026, 12, 25)) == "25 Dec 2026"


def test_heatmap_column():
    assert heatmap_column("George", ANY) == ANY
    assert heatmap_column("George", GR) == "vs Raju"
    assert heatmap_column("Raju", GR) == "vs George"
    assert heatmap_column("Raju", DGR) == THREE_WAY


def test_heatmap_columns_follow_the_row_order():
    order = ["George", "Duncan", "Raju"]
    cols = heatmap_columns(order)
    assert cols == [ANY, "vs George", "vs Duncan", "vs Raju", THREE_WAY]
    # Each athlete's own cell sits on the diagonal of the "vs" block.
    assert [cols.index(f"vs {n}") - 1 for n in order] == [0, 1, 2]


def test_best_period():
    d = dt.date
    assert best_period(d(2024, 3, 2), d(2024, 3, 30), live=False) == "Mar 2024"
    assert best_period(d(2024, 3, 2), d(2024, 7, 13), live=False) == "Mar–Jul 2024"
    assert best_period(d(2023, 11, 4), d(2024, 2, 10), live=False) == "Nov 2023–Feb 2024"
    assert best_period(d(2026, 4, 18), d(2026, 8, 29), live=True) == "Apr 2026–now"

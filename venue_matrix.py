"""Tab 1 — the unique-parkruns matrix: when each runner reached their 1st, 50th,
100th … different parkrun. Drawn beneath the milestone matrix and built the
same way (`milestone_matrix.py`, whose formatting helpers it shares), over the
same arithmetic (`milestones.milestone_summary`, fed first-visit dates and the
`UNIQUE_MILESTONES` ladder instead of every run date and the official one).

Neutral greys throughout, never the official milestone colours: these are not
parkrun milestones, and borrowing those colours would say they were.

One row per runner, no junior rows: a venue is new to the runner whoever is in
the buggy, so a buggy-only count of first visits would not be anyone's tally.
"""

from __future__ import annotations

import datetime as dt
from html import escape

import pandas as pd
import streamlit as st

from milestone_config import UNIQUE_MILESTONES
from milestone_matrix import (DARK_SURFACE, OUTLINE_GREY, days,
                              load_milestone_runs, name_cell, ordinal,
                              reached_body, ring_badge, short_date, table_css,
                              table_html, tint, togo_body)
from milestones import milestone_summary
from parkrun_ui import ATHLETE_COLORS, UK_TZ, _surface_color, fmt_n
from streaks import uk_date

def first_visits(runs: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Each runner's first visit to each parkrun, and their run count — both
    from the milestone matrix's own frame, so the "of N runs" here and its
    "Total runs" can never disagree. A same-day double at two new venues is
    two first visits on one date."""
    firsts = (runs.sort_values(["run_date", "event_id"], kind="stable")
              .drop_duplicates(["athlete_name", "event_id"])
              [["athlete_name", "event_id", "short_name", "run_date"]]
              .rename(columns={"run_date": "first_date"}))
    return firsts, runs.groupby("athlete_name").size().astype(int).to_dict()


def venue_rows(firsts: pd.DataFrame, order: list[str], runs: dict[str, int],
               today: dt.date) -> list[dict]:
    """One row per runner in `order`: the milestone summary of their first
    visits, the venues in the order they were first run, and their run count
    (`runs` comes from the same frame as `firsts`, so every name is in it)."""
    rows = []
    for name in order:
        mine = firsts[firsts["athlete_name"] == name].sort_values(
            ["first_date", "short_name"])
        if mine.empty:
            continue
        rows.append(dict(
            name=name, runs=runs[name], venues=list(mine["short_name"]),
            s=milestone_summary(mine["first_date"], today=today,
                                ladder=UNIQUE_MILESTONES)))
    return rows


def venue_columns(rows: list[dict]) -> list[int]:
    """Every multiple of 50 up to the furthest anyone has reached or is next to."""
    # The next milestone is above anything reached; past the top of the
    # ladder there is none, and the total covers every rung reached.
    top = max((r["s"].next_milestone or r["s"].total for r in rows), default=0)
    return [m for m in UNIQUE_MILESTONES if m <= top]


# --- HTML --------------------------------------------------------------------

def _css(dark_guess: bool) -> str:
    return f"""<style>{table_css("vm", dark_guess, "720px", OUTLINE_GREY)}
/* The total carries an "of N runs" line under it. */
.vm .vm-total {{ line-height:1.1; }}
.vm-sub {{ font-weight:400; }}
.vm .vm-reached {{ {tint(OUTLINE_GREY, dark_guess)} }}
.vm .vm-next {{ border-style:dashed; border-color:{OUTLINE_GREY}; }}
.vm .vm-latest {{ background:transparent; }}
.vm-venue {{ display:inline-block; max-width:9rem; overflow:hidden;
          text-overflow:ellipsis; vertical-align:bottom; }}
</style>"""


def _total_cell(row: dict) -> str:
    s = row["s"]
    tip = f"{row['name']} · {fmt_n(s.total)} different parkruns in {fmt_n(row['runs'])} runs"
    return (f"<td class='vm-total' title='{escape(tip)}'>{fmt_n(s.total)}<br>"
            f"<span class='vm-sub'>of {fmt_n(row['runs'])} runs</span></td>")


def _first_cell(row: dict) -> str:
    s = row["s"]
    venue = row["venues"][0]
    tip = f"{row['name']} · 1st parkrun: {venue}, {uk_date(s.first_date)}"
    return (f"<td class='vm-reached' title='{escape(tip)}'>"
            f"{short_date(s.first_date)}<br>"
            f"<span class='vm-sub vm-venue'>{escape(venue)}</span></td>")


def _milestone_cell(row: dict, m: int) -> str:
    s = row["s"]
    ladder = [x.n for x in s.reached]
    if m in ladder:
        i = ladder.index(m)
        x = s.reached[i]
        venue = row["venues"][m - 1]
        tip = (f"{row['name']} · {ordinal(m)} different parkrun: {venue}, "
               f"{uk_date(x.date)} · {days(x.days_since_first)} since the 1st")
        # The first rung's previous column is the 1st, already said above.
        if i > 0:
            tip += f" · {days(x.days_since_prev)} since the {ordinal(ladder[i - 1])}"
        return (f"<td class='vm-reached' title='{escape(tip)}'>"
                f"{reached_body(x, 'vm')}</td>")
    if m == s.next_milestone:
        tip = (f"{row['name']} · {fmt_n(s.runs_to_go)} new parkruns to go "
               f"to the {ordinal(m)}")
        return f"<td class='vm-next' title='{escape(tip)}'>{togo_body(s, 'vm')}</td>"
    return "<td></td>"


def _latest_cell(row: dict) -> str:
    venue, date = row["venues"][-1], row["s"].last_date
    tip = f"{row['name']} · latest new parkrun: {venue}, {uk_date(date)}"
    return (f"<td class='vm-latest' title='{escape(tip)}'>"
            f"<span class='vm-venue'>{escape(venue)}</span><br>"
            f"<span class='vm-sub'>{short_date(date)}</span></td>")


def venue_matrix_html(rows: list[dict], dark_guess: bool = False) -> str:
    columns = venue_columns(rows)
    head = ("<tr><th class='vm-name'></th><th>Different<br>parkruns</th>"
            f"<th>{ring_badge('1st', 'vm')}</th>"
            + "".join(f"<th>{ring_badge(fmt_n(m), 'vm')}</th>" for m in columns)
            + "<th>Latest new<br>parkrun</th></tr>")
    body = []
    for r in rows:
        cells = [name_cell(r["name"], "vm"), _total_cell(r), _first_cell(r)]
        cells += [_milestone_cell(r, m) for m in columns]
        cells.append(_latest_cell(r))
        body.append("<tr>" + "".join(cells) + "</tr>")
    return table_html("vm", _css(dark_guess), head, body)


def render_venue_matrix(version: str) -> None:
    firsts, runs = first_visits(load_milestone_runs(version))
    if firsts.empty:
        st.info("No parkruns yet.")
        return
    today = dt.datetime.now(UK_TZ).date()
    rows = venue_rows(firsts, list(ATHLETE_COLORS), runs, today)
    st.html(venue_matrix_html(rows, dark_guess=_surface_color() == DARK_SURFACE))
    st.caption(
        "When each runner first ran their 1st, 50th, 100th … different parkrun. "
        "Each cell: date of that first visit · days since the 1st · +days since "
        "the previous column. A dashed cell is the next one, with the new "
        "parkruns still to go. Hover a cell for the parkrun's name. These are "
        "not official parkrun milestones, so they carry no milestone colours."
    )

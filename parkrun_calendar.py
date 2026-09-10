"""Participation calendars — the shared drawing layer.

Imported by `parkrun_app.py` (tab 1's overview and tab 3's head-to-head picker)
and by `calendar_proto.py`, the dev-only design bench that still renders every
candidate scheme side by side. Nothing here decides layout or copy; the pages do.

Everything is hand-emitted **SVG**. The GitHub-contributions look IS a fixed
geometry (11px cell, 3px gutter), and plotly sizes markers in pixels while
sizing axes in fraction-of-container, so a 53-column grid drawn with markers is
only correct at one window width. SVG states the geometry directly and lets the
viewBox scale it — or, on a phone, deliberately does not scale it and lets the
strip be swiped. Precedent: `_pie_svg` in parkrun_app.

Tab 3's picker was plotly for a while, because it has to be *clickable* and
`st.plotly_chart(on_select="rerun")` is the only selection channel Streamlit
offers out of the box. It cost more than it saved: a tap on a phone fires
plotly's click but not its selection, so on a phone the picker could not be
used at all. It is now the same SVG as every other calendar, inside the small
declared component in `components/calendar/`, where a click is an ordinary DOM
click.

Rows are calendar years and weeks are counted from 1 January — see WEEKS_PER_YEAR.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from parkrun_ui import (
    ATHLETE_COLORS,
    BUGGY_GLYPH,
    MEDAL,
    _read_sql,
    _surface_color,
    fmt_time,
    mode_suffix,
)

# --------------------------------------------------------------------------- #
# Geometry + encoding — defined once so every layout agrees
# --------------------------------------------------------------------------- #


class Geom(NamedTuple):
    cell: int = 11
    gutter: int = 3
    radius: int = 2
    block_gap: int = 12
    label_w: int = 38  # left gutter for year / athlete labels
    label_h: int = 16  # top strip for month ticks
    total_w: int = 74  # right gutter for the end-of-year totals

    @property
    def pitch(self) -> int:
        return self.cell + self.gutter


DESKTOP = Geom()
# Extra vertical space inserted in layout C wherever the year sequence jumps,
# so a row gap on the page means a gap in time. Without it Raju's 2007 row sits
# directly above his 2015 one and reads as consecutive.
YEAR_JUMP_GAP = 18
# Layout C labels its blocks with names rather than years, and "Duncan" does not
# fit in a gutter sized for "2026".
NAMED = Geom(label_w=54)

# Athlete row order inside a year block (and the small-multiple order).
ATHLETES = list(ATHLETE_COLORS)  # George, Raju, Duncan

EMPTY_LIGHT = "#ebedf0"
EMPTY_DARK = "#1c2128"

# Head-to-head fills, one per pairing, lifted straight off tab 1's Venn —
# `venn3(set_colors=ATHLETE_COLORS, alpha=0.55)` in parkrun_app.py, whose region
# patches matplotlib_venn colours with its own `mix_colors`. Read off the
# patches once and hardcoded: a prototype should not import a plotting library
# to pick a fill colour.
#
# These are the undiluted mixes. What the Venn actually SHOWS is each of these
# at alpha 0.55 over matplotlib's white figure (H2H_COLORS_WASHED). Those are
# too pale to read in an 11px cell against the empty-cell grey, and the wash is
# an artefact of a white figure the calendar does not have — so the saturated
# mix is used here. Same hues, same derivation. Swap the constant to match the
# Venn pixel-for-pixel.
H2H_COLORS = {
    "George vs Raju": "#abb155",             # olive   · 148 occasions
    "Duncan vs Raju": "#ea917f",             # salmon  ·  33
    "Duncan vs George": "#65bdd1",           # sky     ·   2
    "Duncan vs George vs Raju": "#909278",   # stone   ·  23
}
H2H_COLORS_WASHED = {
    "George vs Raju": "#d1d4a2",
    "Duncan vs Raju": "#f3c2b9",
    "Duncan vs George": "#aadbe6",
    "Duncan vs George vs Raju": "#c2c3b5",
}

# Four buggy treatments were built and compared at cell size; the diagonal
# hatch won and the other three (ring outline, corner dot, centre dot) are
# gone. The hatch is the only one that changes the cell's *texture* rather than
# adding a mark to it, so it survives being scaled down and never reads as a
# stray dot. Kept as a named constant because the hover copy refers to it.
# Generic stroke width for the few things that still outline a cell — the
# selected-occasion ring in tab 3, and nothing else in the SVG path.
CELL_STROKE_W = 1.6

# Head-to-head fill schemes. "Venn" is the flat per-pairing colour above;
# "Split" paints the cell from the participants' own colours — a diagonal half
# each for a two-way, vertical thirds for a three-way — so the cell names who
# met without a legend lookup, at the cost of being busier at 11px.
H2H_SCHEMES = (
    "Venn regions (flat)",
    "Split by athlete",
    "Distinct palette",
    "Winner's colour",
    "Three-way highlighted",
)

# Okabe-Ito, chosen for maximum separation — the answer to the Venn scheme's
# one real weakness, that its three-way stone and its George-vs-Raju olive are
# blends of the same constituents and sit close together at 11px. Assignment is
# not arbitrary: the two pairings involving Raju take the blue family, the
# one that does not takes pink, and the rare three-way takes vermillion.
H2H_DISTINCT = {
    "George vs Raju": "#0072b2",             # blue
    "Duncan vs Raju": "#56b4e9",             # sky
    "Duncan vs George": "#cc79a7",           # pink
    "Duncan vs George vs Raju": "#d55e00",   # vermillion
}

# The winner-coloured calendar in tab 3 uses ATHLETE_COLORS directly. A cell
# with two winners is split diagonally between them rather than given a colour
# of its own — a "drawn" colour would have named a fourth thing that is not a
# runner, and would have collapsed two different situations (one contest tied,
# two contests won by different people) into one unreadable swatch.

# "Three-way highlighted" answers one question only — when were all three of
# them there — so everything else is deliberately flattened to one neutral.
THREE_WAY = "Duncan vs George vs Raju"
THREE_WAY_ACCENT = "#d55e00"
TWO_WAY_MUTED_LIGHT = "#b8c0c9"
TWO_WAY_MUTED_DARK = "#3f4b5c"

SCHEME_NOTES = {
    "Venn regions (flat)":
        "Tab 1's Venn regions — the same mix of the athletes' own colours the "
        "overlap diagram uses, so the two agree. Its weakness is inherent to "
        "the mix: the three-way stone and the George-vs-Raju olive are blends "
        "of the same constituents, so they sit close together at 11px.",
    "Split by athlete":
        "Each cell painted from its participants' own colours — a diagonal "
        "half each for a two-way, vertical thirds for a three-way — always "
        "left to right in the same order (George, Raju, Duncan), so the cell "
        "says who met with no legend lookup. Busiest of the five at 11px, and "
        "the only one that names the pairing rather than coding it.",
    "Distinct palette":
        "Okabe-Ito, chosen for maximum separation rather than derived from "
        "anyone's colour — the direct answer to the Venn scheme's olive/stone "
        "problem. The two pairings involving Raju take the blue family, the "
        "one without him takes pink, and the rare three-way takes vermillion. "
        "Colourblind-safe, at the cost of no longer meaning anything.",
    "Winner's colour":
        "Fill is whoever beat their own form by most that day — so this stops "
        "answering *who met* and starts answering *who wins when they do*. "
        "Raju 91, George 82, Duncan 34: that sums to 207 against 206 contests "
        "because one was a dead heat and credits both. A split cell means two "
        "firsts in the week — that drawn contest, and 2019 wk 1, where "
        "they raced twice and won one each.",
    "Three-way highlighted":
        "One question only — when were all three of them there. Every two-way "
        "is flattened to a single neutral so the 23 three-way weeks carry the "
        "whole picture. Throws away the most information of the five, which is "
        "the point.",
}

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# Month tick positions are approximate — a fixed 7-day week drifts against
# the months, so a tick marks where a month starts to within a few days.
# Each year's true month boundaries drift by up to a week; the ticks are a
# reading aid, not an axis.
# Where each month starts, in the fixed-week scheme. Exact for a non-leap year
# and out by at most one column after February in a leap one — these are a
# reading aid, not an axis.
MONTH_WEEK = {m: (dt.date(2025, m, 1).timetuple().tm_yday - 1) // 7 + 1
              for m in range(1, 13)}


class Theme(NamedTuple):
    empty: str
    mark: str     # the buggy marker — maximum contrast against a mid-tone fill
    label: str
    surface: str
    tip_bg: str
    tip_fg: str
    muted: str    # the flattened two-way in the "three-way highlighted" scheme


def theme() -> Theme:
    """Colours for the viewer's theme.

    Everything the SVG draws is passed in from here rather than set in CSS: the
    grid lives inside a component iframe, whose document does not inherit the
    Streamlit theme.
    """
    dark = _surface_color() == "#0e1117"
    return Theme(
        empty=EMPTY_DARK if dark else EMPTY_LIGHT,
        mark="#e6edf3" if dark else "#1f2328",
        label="#8b949e" if dark else "#6e7781",
        surface="#0e1117" if dark else "#ffffff",
        tip_bg="#161b22" if dark else "#24292f",
        tip_fg="#e6edf3" if dark else "#ffffff",
        muted=TWO_WAY_MUTED_DARK if dark else TWO_WAY_MUTED_LIGHT,
    )


# Weeks are counted from 1 January, not by the ISO calendar: week 1 is 1-7 Jan
# and every week after is a fixed 7 days. Every year therefore has exactly 53
# columns, the last of which is a LEFTOVER rather than a week — 31 Dec alone,
# or 30-31 Dec in a leap year. It is drawn only in the years where that leftover
# can actually hold a parkrun (`stub_visible_years`), and full size when it is.
#
# ISO weeks were the obvious first choice and were wrong for this drawing: they
# put a run on 1 January in the previous year's row, and they give some years
# 53 weeks and others 52, which needed a caveat under every calendar.
WEEKS_PER_YEAR = 53
STUB_WEEK = 53

# How far a filtered-out head-to-head fades. Low enough to read as excluded,
# high enough to still be a target — a square you cannot see is a square you
# cannot click your way out of the filter with.
DIM_OPACITY = 0.22

# Right-hand gutter for the picker's standings — wide enough for
# "George 82 (5🛒)" beside a swatch, which is the longest any of these gets.
TOTALS_COL_W = 142


def cell_x(g, wk: int) -> float:
    """Left edge of a week column, in SVG units."""
    return g.label_w + (wk - 1) * g.pitch


def week_of_year(d) -> int:
    """1..53, counting 7-day blocks from 1 January."""
    return (d.timetuple().tm_yday - 1) // 7 + 1


def week_of_year_series(dates: pd.Series) -> pd.Series:
    """`week_of_year` over a column of dates."""
    return ((dates.dt.dayofyear - 1) // 7 + 1).astype(int)


# The same rule in SQL, written once and interpolated into both queries.
# `floor(...)::INT`, never `(...)::INT`: DuckDB's cast ROUNDS, which silently
# pushed late-December runs into week 53 the first time these numbers were
# computed.
WEEK_SQL = "floor((dayofyear({col}) - 1) / 7)::INT + 1"


def week_span(year: int, wk: int) -> tuple[dt.date, dt.date]:
    """The dates a cell covers — 7 days, or the 1-2 day stub at week 53."""
    start = dt.date(year, 1, 1) + dt.timedelta(days=(wk - 1) * 7)
    end = min(start + dt.timedelta(days=6), dt.date(year, 12, 31))
    return start, end


def stub_visible_years(runs: pd.DataFrame, years) -> set[int]:
    """Years whose week-53 leftover is worth drawing at all.

    The stub covers 31 Dec, or 30-31 Dec in a leap year. Most years that is a
    day nobody could have run a parkrun on, so an empty cell there says "they
    missed it" about a week that never had one. Keep it only when the leftover
    actually contains a Saturday, or when somebody ran in it anyway (Christmas
    and New Year fixtures do not care what day it is).

    Across 2007-2026 that is 2011, 2016 and 2022 — and only the last two are in
    the data, so 11 of 13 year rows lose a column that could never be filled.
    """
    ran = set(map(int, runs.loc[runs["iso_week"] == STUB_WEEK, "iso_year"]))
    out = set()
    for y in map(int, years):
        a, b = week_span(y, STUB_WEEK)
        days = [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]
        if any(d.weekday() == 5 for d in days) or y in ran:
            out.add(y)
    return out


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

RUNS_SQL = """
SELECT a.athlete_name,
       r.run_date,
       r.event_id,
       e.short_name,
       r.time_seconds,
       r.is_buggy,
       year(r.run_date)                             AS iso_year,
       {run_week}                                   AS iso_week,
       coalesce(o.n_athletes, 1)                    AS occasion_n
FROM parkrun.v_results_moded r
JOIN parkrun.athletes a USING (athlete_id)
JOIN parkrun.events   e USING (event_id)
LEFT JOIN parkrun.v_overlap o USING (event_id, run_date)
ORDER BY a.athlete_name, r.run_date
""".replace("{run_week}", WEEK_SQL.format(col="r.run_date"))

H2H_SQL = """
SELECT run_date,
       event_id,
       short_name,
       classification,
       year(run_date)                              AS iso_year,
       {week}                                       AS iso_week,
       athlete_name,
       actual_seconds,
       place_rank,
       is_buggy
FROM parkrun.v_head_to_head
ORDER BY run_date, place_rank, athlete_name
""".replace("{week}", WEEK_SQL.format(col="run_date"))


@st.cache_data(show_spinner=False)
def load_runs(version: str) -> pd.DataFrame:
    """One row per run.

    `occasion_n` comes from a join on (event_id, run_date) — that key IS the
    shared-occasion definition. Nothing draws it any more (A and C lost their
    head-to-head marker), but `data_checks` still asserts on it: it is the
    cheapest guard that the join key has not drifted to something week-shaped,
    which would be wrong on 92 weeks and look entirely plausible.
    """
    df = _read_sql(RUNS_SQL)
    df["run_date"] = pd.to_datetime(df["run_date"])
    df["hover_line"] = [
        f"{who} · {d:%a %-d %b %Y} · {n}{mode_suffix(b)} · {fmt_time(t)}"
        for who, d, n, b, t in zip(
            df["athlete_name"], df["run_date"], df["short_name"],
            df["is_buggy"], df["time_seconds"]
        )
    ]
    return df


@st.cache_data(show_spinner=False)
def week_frame(runs: pd.DataFrame) -> pd.DataFrame:
    """One row per (athlete, year, week). 844 runs collapse to 822 —
    20 weeks hold more than one run, and two hold three (George's New Year
    doubles in 2018 and 2020, each plus the following Saturday).

    `any_buggy` rather than "the week was buggy": one George week holds both a
    buggy and a regular run, so the cell marks that a buggy appeared and the
    hover, which carries `mode_suffix` per line, says which run it was.
    """
    rows = []
    for (name, iy, iw), g in runs.groupby(
        ["athlete_name", "iso_year", "iso_week"], sort=True
    ):
        rows.append(
            {
                "athlete_name": name,
                "iso_year": int(iy),
                "iso_week": int(iw),
                "n_runs": len(g),
                "any_buggy": bool(g["is_buggy"].any()),
                "all_buggy": bool(g["is_buggy"].all()),
                "shared": bool(g["occasion_n"].max() >= 2),
                "hover": "\n".join(g["hover_line"]),
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def load_h2h(version: str) -> pd.DataFrame:
    """`v_head_to_head` — the app's own definition of a contest, so this
    calendar cannot disagree with the leaderboard on tab 2. It is 206
    occasions against `v_overlap`'s 208; the two missing ones are days where
    too few participants had a valid form target to be ranked.
    """
    df = _read_sql(H2H_SQL)
    df["run_date"] = pd.to_datetime(df["run_date"])
    return df


@st.cache_data(show_spinner=False)
def h2h_week_frame(h2h: pd.DataFrame) -> pd.DataFrame:
    """One row per week that held a head-to-head — 206 occasions, 205 weeks.

    No tiebreak on the doubled week. Verified against the data: exactly one
    week ever holds two occasions (2019 wk 1 — Tue 1 Jan Oak Hill and Sat
    5 Jan Barking) and NO week ever holds two different classifications, so the
    fill is unambiguous and only the hover has to mention both.
    """
    rows = []
    for (iy, iw), g in h2h.groupby(["iso_year", "iso_week"], sort=True):
        blocks = []
        for (d, ev), occ in g.groupby(["run_date", "short_name"], sort=True):
            head = f"{d:%a %-d %b %Y} · {ev} — {occ['classification'].iloc[0]}"
            lines = [
                f"   {MEDAL.get(int(r.place_rank), '·')} {r.athlete_name}"
                f"{mode_suffix(r.is_buggy)} {fmt_time(r.actual_seconds)}"
                for r in occ.sort_values("place_rank").itertuples()
            ]
            blocks.append("\n".join([head, *lines]))
        rows.append(
            {
                "iso_year": int(iy),
                "iso_week": int(iw),
                "classification": g["classification"].iloc[0],
                # Always in ATHLETES order, so the split-cell scheme puts the
                # same person in the same place every time and the eye can
                # learn it. Safe to take from the week rather than per
                # occasion: no week mixes two classifications (asserted).
                "participants": tuple(n for n in ATHLETES
                                      if n in set(g["athlete_name"])),
                # Everyone who took a 1st that week. Usually one person, but
                # two in the two cases the cell must not misreport: the single
                # drawn contest in 206, and 2019 wk 1, where they held two
                # occasions and won one each.
                "winners": tuple(
                    n for n in ATHLETES
                    if n in set(g.loc[g["place_rank"] == 1, "athlete_name"])
                ),
                "n_occasions": int(g[["run_date", "event_id"]].drop_duplicates().shape[0]),
                "hover": "\n".join(blocks),
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def athlete_year_totals(runs: pd.DataFrame) -> pd.DataFrame:
    """Runs and buggy runs per (athlete, year) — the end-of-row totals.

    Runs, not athlete-weeks: a total at the end of a year should count what
    they actually ran, and 20 weeks across the data hold more than one. So the
    total legitimately exceeds the number of filled cells in that row.
    """
    g = (runs.groupby(["athlete_name", "iso_year"])
             .agg(n=("run_date", "size"), bug=("is_buggy", "sum"))
             .reset_index())
    g["bug"] = g["bug"].astype(int)
    return g


@st.cache_data(show_spinner=False)
def h2h_year_totals(h2h: pd.DataFrame) -> pd.DataFrame:
    """Head-to-head occasions per year, with the buggy count PER ATHLETE.

    A single "with buggy" number could not say whose buggy it was, and in a
    contest that is the whole question — one of them pushing is a fact about
    that runner, not about the day. So the bracket carries a per-athlete
    count, in each athlete's own colour: 2026 is George 8 and Duncan 4, which
    a combined 12 would flatten into something unattributable.

    A contest is counted once per athlete who pushed in it, so an occasion
    where two of them had buggies would count for both. That has never
    happened, but the arithmetic should not depend on it.
    """
    occ = h2h[["iso_year", "run_date", "event_id"]].drop_duplicates()
    n = occ.groupby("iso_year").size().rename("n")
    bug = (h2h[h2h["is_buggy"]]
           .groupby(["iso_year", "athlete_name"]).size()
           .rename("bug").reset_index())
    per = {int(y): {} for y in n.index}
    for r in bug.itertuples():
        per[int(r.iso_year)][r.athlete_name] = int(r.bug)
    out = n.reset_index()
    out["by_athlete"] = [per[int(y)] for y in out["iso_year"]]
    return out


def h2h_win_totals(h2h: pd.DataFrame) -> dict:
    """`{name: (wins, wins_with_a_buggy)}` for whatever slice is passed in.

    Counted per OCCASION, not per week: today those are equal because the only
    week holding two contests (2019 wk 1) was won by a different person each
    time, and they would stop being equal the moment one person won both. The
    grain is pinned here rather than left correct by luck.

    Deliberately not cached — it is handed a FILTERED frame on every rerun, so
    the totals say what the filters select rather than what the record holds.
    Unfiltered they sum to 207 against 206 contests, which is right: a dead
    heat credits both runners.

    The buggy count is the winner's own flag on the winning run — the question
    a reader has is "did they win that pushing one", which is a fact about the
    winner and not about the occasion.
    """
    firsts = h2h[h2h["place_rank"] == 1]
    occ = firsts[["run_date", "event_id", "athlete_name",
                  "is_buggy"]].drop_duplicates()
    out = {}
    for n in ATHLETES:
        mine = occ[occ["athlete_name"] == n]
        out[n] = (len(mine), int(mine["is_buggy"].sum()) if len(mine) else 0)
    return out


def hatch_swatch(size: int = 12) -> str:
    """One hatched square as a data URI, for prose that names the marker.

    The caption used the buggy glyph to stand for the hatch, which asked the
    reader to hold a translation in their head; this is the marker itself. It
    is filled in the muted grey rather than an athlete's colour — the sentence
    is about all three of them — but a MID grey, not the empty-cell one, so it
    reads as a week that was run rather than one that was missed.
    """
    t = theme()
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
           f'height="{size}" viewBox="0 0 12 12">'
           f'<defs><pattern id="h" width="4" height="4" '
           f'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           f'<rect width="4" height="4" fill="{t.muted}"/>'
           f'<line x1="0" y1="0" x2="0" y2="4" stroke="{t.mark}" '
           f'stroke-width="1.5" opacity="0.85"/></pattern></defs>'
           f'<rect width="12" height="12" rx="2" fill="url(#h)"/></svg>')
    return "data:image/svg+xml;utf8," + quote(svg)


# The per-year win tallies beside the picker, as four candidate treatments.
# Which one ships is a question for the picture, so all four are drawable and
# the bench in calendar_proto.py compares them.
YEAR_TOTAL_STYLES = ("Counts", "Initials", "Share bar", "Initials + bar")
YEAR_TOTAL_W = {"Counts": 118, "Initials": 168, "Share bar": 128,
                "Initials + bar": 296}
SHARE_BAR_W = 96
# One fixed slot per athlete, so the tallies line up down the page. Laying them
# out by text width let 2019's second name start left of 2024's, which turned a
# column of numbers into a ragged sentence.
INITIAL_SLOT_W = 54


def G_HEAD_Y(g) -> float:
    """Baseline for a gutter heading, level with the month ticks."""
    return g.label_h - 5


def _yt_counts(x: float, y: float, g: Geom, t: Theme, won: dict) -> list[str]:
    """Just the numbers, each in its own athlete's colour."""
    parts = []
    for n in ATHLETES:
        w, bug = won.get(n, (0, 0))
        if not w:
            continue
        seg = f'<tspan fill="{ATHLETE_COLORS[n]}">{w}</tspan>'
        if bug:
            seg += (f'<tspan fill="{t.label}">(</tspan>'
                    f'<tspan fill="{ATHLETE_COLORS[n]}">{bug}{BUGGY_GLYPH}</tspan>'
                    f'<tspan fill="{t.label}">\u2009)</tspan>')
        parts.append(seg)
    if not parts:
        return []
    joined = '<tspan> </tspan>'.join(parts)
    return [_text(x, y + g.cell - 1, joined, anchor="start", size=9.5,
                  weight=600, fill=t.label, raw=True)]


def _yt_initials(x: float, y: float, g: Geom, t: Theme, won: dict) -> list[str]:
    """`G11  R4  D7`, one fixed slot each, buggy wins after the count."""
    out = []
    for i, n in enumerate(ATHLETES):
        w, bug = won.get(n, (0, 0))
        if not w:
            continue
        lab = f"{n[0]}{w}" + (f" {bug}{BUGGY_GLYPH}" if bug else "")
        out.append(_text(x + i * INITIAL_SLOT_W, y + g.cell - 1, lab,
                         anchor="start", size=9.5, weight=600,
                         fill=ATHLETE_COLORS[n]))
    return out


def _yt_bar(x: float, y: float, g: Geom, t: Theme, won: dict) -> list[str]:
    """The year's wins as one bar, hatched over the share pushed with a buggy."""
    tot = sum(w for w, _ in won.values())
    if not tot:
        return []
    out, cx = [], float(x)
    for n in ATHLETES:
        w, bug = won.get(n, (0, 0))
        if not w:
            continue
        seg = SHARE_BAR_W * w / tot
        out.append(_rect(cx, y, g, ATHLETE_COLORS[n], w=seg, radius=1))
        if bug:
            out.append(_rect(cx + seg * (w - bug) / w, y, g,
                             f"url(#{_hatch_id(n)})", w=seg * bug / w, radius=1))
        cx += seg
    out.append(_text(x + SHARE_BAR_W + 6, y + g.cell - 1, str(tot),
                     anchor="start", size=9.5, weight=600, fill=t.label))
    return out


def _year_total_row(style: str, x: float, y: float, g: Geom, t: Theme,
                    won: dict) -> list[str]:
    if style == "Counts":
        return _yt_counts(x, y, g, t, won)
    if style == "Initials":
        return _yt_initials(x, y, g, t, won)
    if style == "Share bar":
        return _yt_bar(x, y, g, t, won)
    return (_yt_initials(x, y, g, t, won)
            + _yt_bar(x + 3 * INITIAL_SLOT_W + 8, y, g, t, won))


def h2h_year_wins(h2h: pd.DataFrame) -> dict:
    """`{year: {name: (wins, buggy_wins)}}` for whatever slice is passed in.

    Wins, not head-to-heads: a dead heat credits both runners, so a year's
    tallies can sum to one more than the contests it held — 2022 reads 24
    against 23. That is the arithmetic being right, not a miscount, and it is
    why no row shows a "total" alongside them.
    """
    firsts = h2h[h2h["place_rank"] == 1]
    occ = firsts[["run_date", "event_id", "athlete_name",
                  "is_buggy"]].drop_duplicates()
    # The year comes off the date, not from an `iso_year` column: this is
    # handed the APP's head-to-head frame, which never had one — only the
    # calendar module's own loader adds it.
    occ = occ.assign(year=occ["run_date"].dt.year.astype(int))
    out = {}
    for y, g in occ.groupby("year"):
        out[int(y)] = {n: (int((g["athlete_name"] == n).sum()),
                           int(g[(g["athlete_name"] == n) & g["is_buggy"]].shape[0]))
                       for n in ATHLETES}
    return out


def _total_tspans(n: int, by_athlete: dict, label_fill: str) -> str:
    """`18 (8🛒 4🛒)` with each count in its own athlete's colour.

    Colour is doing the attribution, so nobody's name has to fit in a 74px
    gutter. Athletes appear in the fixed ATHLETES order and only when they
    have a count — a zero would put the glyph on every row and turn the buggy
    into a standing column instead of the exception it is.
    """
    parts = [f'<tspan fill="{label_fill}">{n}</tspan>']
    inner = [f'<tspan fill="{ATHLETE_COLORS[a]}">{by_athlete[a]}{BUGGY_GLYPH}</tspan>'
             for a in ATHLETES if by_athlete.get(a)]
    if inner:
        # Hair spaces: the emoji's advance is wider than the metric the digits
        # are set in, so without them it collides with what follows.
        parts.append(f'<tspan fill="{label_fill}"> (</tspan>')
        parts.append('<tspan>\u2009</tspan>'.join(inner))
        parts.append(f'<tspan fill="{label_fill}">\u2009)</tspan>')
    return "".join(parts)


def _total_label(n: int, bug: int) -> str:
    """`42 (19🛒)` — the bracket only when there is one to show.

    Bracketing a zero would put the glyph on every row and make the buggy look
    like a standing column rather than the exception it is: only three
    athlete-years in the whole dataset have any.
    """
    # Hair space before the bracket: the emoji's advance width is wider than
    # the metric the surrounding digits are set in, so it collides with the ")".
    return f"{n} ({bug}{BUGGY_GLYPH}\u2009)" if bug else str(n)


# --------------------------------------------------------------------------- #
# Streaks + headline
# --------------------------------------------------------------------------- #


def render_headline(runs: pd.DataFrame, weeks: pd.DataFrame,
                    years: list[int]) -> None:
    """Parkruns run in the selected period, one box per athlete."""
    sel = runs[runs["iso_year"].between(years[0], years[-1])]

    cols = st.columns(len(ATHLETES))
    for col, name in zip(cols, ATHLETES):
        n = int((sel["athlete_name"] == name).sum())
        with col:
            st.markdown(
                f"<div style='border-left:4px solid {ATHLETE_COLORS[name]};"
                f"padding:2px 0 2px 10px'>"
                f"<div style='font-size:1.35rem;font-weight:600'>{n} parkruns</div>"
                f"<div style='opacity:.75'>{name}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    st.caption("Run counts follow the year filter.")


# --------------------------------------------------------------------------- #
# SVG primitives
# --------------------------------------------------------------------------- #


def _attr_tip(tip: str) -> str:
    """Hover text as a `data-t` attribute, read by the injected tooltip script.

    Newlines are written as `&#10;` rather than literal newlines: an HTML parser
    keeps a raw newline in an attribute value, but the entity survives every
    round trip through escaping and is unambiguous.
    """
    return ' data-t="' + escape(tip, quote=True).replace("\n", "&#10;") + '"'


def _rect(x: float, y: float, g: Geom, fill: str, *, stroke: str | None = None,
          stroke_w: float = CELL_STROKE_W, tip: str | None = None,
          w: float | None = None, h: float | None = None,
          radius: float | None = None, extra: str = "") -> str:
    """`extra` is appended raw to the tag — `data-w`, for a clickable cell."""
    w = g.cell if w is None else w
    h = g.cell if h is None else h
    r = g.radius if radius is None else radius
    a = (f'x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
         f'rx="{r}" fill="{fill}"')
    if stroke:
        a += f' stroke="{stroke}" stroke-width="{stroke_w}"'
    if tip:
        a += _attr_tip(tip)
    return f"<rect {a}{extra}/>"


def _text(x: float, y: float, s: str, *, size: float = 9, anchor: str = "end",
          fill: str = "#6e7781", weight: int = 400, raw: bool = False) -> str:
    """`raw=True` passes `s` through unescaped — for pre-built `<tspan>` runs."""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'text-anchor="{anchor}" fill="{fill}" font-weight="{weight}" '
            f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif">'
            f"{s if raw else escape(s)}</text>")


def _hatch_id(name: str) -> str:
    return f"hatch-{name.lower()}"


def _hatch_defs(mark: str) -> str:
    """One diagonal-hatch pattern per athlete colour, defined once per SVG."""
    out = []
    for name, col in ATHLETE_COLORS.items():
        out.append(
            f'<pattern id="{_hatch_id(name)}" width="4" height="4" '
            f'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<rect width="4" height="4" fill="{col}"/>'
            f'<line x1="0" y1="0" x2="0" y2="4" stroke="{mark}" '
            f'stroke-width="1.5" opacity="0.85"/></pattern>'
        )
    return f"<defs>{''.join(out)}</defs>"


def _athlete_cell(x: float, y: float, g: Geom, name: str, *, buggy: bool,
                  tip: str) -> str:
    """One athlete-week cell. A buggy week is hatched in the athlete's colour."""
    fill = f"url(#{_hatch_id(name)})" if buggy else ATHLETE_COLORS[name]
    return _rect(x, y, g, fill, tip=tip)


CELL_CLIP_ID = "cellclip"


def _cell_clip_def(g: Geom) -> str:
    """One reusable rounded-square clip for the split head-to-head cells.

    `objectBoundingBox` units so a single definition serves every cell wherever
    it sits — the alternative is one clipPath per cell, or `userSpaceOnUse`,
    whose interaction with a transform on the referencing element is the kind
    of spec corner browsers have historically disagreed about.
    """
    r = g.radius / g.cell
    return (f'<clipPath id="{CELL_CLIP_ID}" clipPathUnits="objectBoundingBox">'
            f'<rect x="0" y="0" width="1" height="1" rx="{r:.4f}" ry="{r:.4f}"/>'
            f"</clipPath>")


def _split_cell(x: float, y: float, g: Geom, names, tip: str,
                w: float | None = None, extra: str = "") -> str:
    """A head-to-head cell painted from its participants' own colours.

    Two athletes: split on the leading diagonal, first-in-ATHLETES-order taking
    the upper-left triangle. Three: vertical thirds, left to right in the same
    order. Absolute coordinates rather than a transform, so the clip needs no
    assumption about which coordinate system it resolves in.
    """
    cw = g.cell if w is None else w
    c = g.cell
    parts = []
    if len(names) == 2:
        a, b = names
        parts.append(f'<polygon points="{x},{y} {x + cw},{y} {x},{y + c}" '
                     f'fill="{ATHLETE_COLORS[a]}"/>')
        parts.append(f'<polygon points="{x + cw},{y} {x + cw},{y + c} {x},{y + c}" '
                     f'fill="{ATHLETE_COLORS[b]}"/>')
    else:
        bw = cw / len(names)
        for i, n in enumerate(names):
            parts.append(f'<rect x="{x + i * bw:.2f}" y="{y}" width="{bw:.2f}" '
                         f'height="{c}" fill="{ATHLETE_COLORS[n]}"/>')
    return (f'<g clip-path="url(#{CELL_CLIP_ID})"'
            + (_attr_tip(tip) if tip else "") + extra + ">"
            + "".join(parts) + "</g>")


def _svg(w: float, h: float, body: str, *, scale: bool = True) -> str:
    """`scale=False` renders at natural size and lets the frame scroll.

    Scaling to the container is what shrinks a 53-column grid to 4.9px cells on
    a phone; not scaling keeps the cells legible and asks the reader to swipe
    instead. Which of those is better is the question the phone bench exists to
    answer, so both are available.
    """
    style = ("max-width:100%;height:auto;display:block" if scale
             else "display:block")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" '
            f'style="{style}">{body}</svg>')


def _month_ticks(g: Geom, label: str, y: float | None = None) -> str:
    y = g.label_h - 5 if y is None else y
    return "".join(
        _text(g.label_w + (wk - 1) * g.pitch, y, MONTHS[m - 1],
              anchor="start", fill=label)
        for m, wk in MONTH_WEEK.items()
    )


# Tooltip lives in the iframe, positioned `fixed` against the iframe viewport,
# and flips above/left of the cursor near an edge — the iframe is a fixed
# height, so a bottom-row tooltip would otherwise be clipped with no scroll to
# reveal it.
# Two scripts, because they answer to different things: the frame must fit its
# contents in every mode, while how a tap is presented is exactly what the phone
# bench is comparing.
_FIT_JS = """
<script>
(function () {
  function fit() {
    var fe = window.frameElement;
    if (!fe) { return; }
    // The whole body, not just the drawing: in panel mode a detail block sits
    // under the grid and has to be inside the frame too.
    var h = Math.ceil(document.body.scrollHeight) + 4;
    fe.style.height = h + 'px';
    fe.setAttribute('height', h);

    // Streamlit wraps the iframe in `stElementContainer` and pins that wrapper
    // to the ORIGINAL height through a generated `st-emotion-cache-*` class —
    // and it does so with `flex: 0 0 705px`, not with `height`. The wrapper is
    // a column flex item, so the fixed FLEX-BASIS is what sizes it and no
    // amount of `height: ...!important` touches it. That is why the first two
    // attempts at this changed nothing measurable.
    //
    // Left unfixed it stranded 341px of dead space at 900px wide and 408px on
    // a phone — growing as the window narrowed, because the SVG scales to the
    // width while the reserved basis never moved.
    var host = fe.closest('[data-testid="stElementContainer"]');
    if (host) {
      host.setAttribute('data-cal-fit', '1');
      var pd = window.parent.document;
      if (!pd.getElementById('cal-fit-style')) {
        var st = pd.createElement('style');
        st.id = 'cal-fit-style';
        st.textContent =
          'div[data-testid="stElementContainer"][data-cal-fit]{' +
          'flex:0 0 auto!important;height:auto!important;' +
          'min-height:0!important}';
        pd.head.appendChild(st);
      }
    }
  }
  try {
    fit();
    window.addEventListener('resize', fit);
    if (window.ResizeObserver) { new ResizeObserver(fit).observe(document.body); }
  } catch (e) { /* a fixed-height frame is the old behaviour, not a failure */ }
})();
</script>
"""


# How a tap is presented — the sheet and the floating label — is one file,
# `components/calendar/detail.js`, shared with the clickable component. It is
# read at import rather than inlined here so there is one copy of behaviour the
# two calendars must not differ on.
_ASSET_DIR = Path(__file__).resolve().parent / "components" / "calendar"


@lru_cache(maxsize=1)
def _detail_js() -> str:
    return (_ASSET_DIR / "detail.js").read_text(encoding="utf-8")


def _tip_css(t: Theme) -> str:
    """The floating label. It lives inside the frame, positioned `fixed`
    against the frame's own viewport, and flips near an edge."""
    return (f"#tt{{position:fixed;display:none;z-index:9;pointer-events:none;"
            f"white-space:pre-wrap;background:{t.tip_bg};color:{t.tip_fg};"
            "padding:6px 9px;border-radius:6px;font-size:11.5px;"
            "line-height:1.45;max-width:min(420px,92vw);"
            "font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;"
            "box-shadow:0 3px 10px rgba(0,0,0,.28)}")


def _detail_bundle(t: Theme) -> str:
    """The label node plus the shared script, configured with the theme."""
    return ('<div id="tt"></div>'
            f"<script>window.CAL_DETAIL={{bg:'{t.tip_bg}',fg:'{t.tip_fg}'}};"
            "</script><script>" + _detail_js() + "</script>")


def _embed(svg: str, height: float, *, scroll_x: bool = False) -> None:
    """A read-only calendar, in a component iframe rather than st.markdown.

    st.markdown sanitises what it renders and inline <svg> survival is version
    dependent; an iframe is unconditional. The cost is an explicit height and a
    document that inherits nothing from the Streamlit theme — hence every
    colour, the label's included, is passed in from `theme()`.

    Use `svg_component` instead when the drawing has to be clickable.
    """
    t = theme()
    css = (
        "<style>"
        f"body{{margin:0;background:transparent;"
        f"overflow-x:{'auto' if scroll_x else 'hidden'}}}"
        "rect[data-t],g[data-t]{cursor:default}"
        + _tip_css(t) +
        "</style>"
    )
    # This height is only the starting value: the script inside re-fits the
    # iframe to the drawing once it knows how wide it actually got.
    components.html(css + svg + _detail_bundle(t) + _FIT_JS,
                    height=int(height) + 8, scrolling=scroll_x)


# The clickable calendar. Streamlit's own components (`components.html`, and
# `st.plotly_chart`'s selection) could not serve tab 3: an html iframe cannot
# talk back to Python at all, and plotly's click-to-select never runs on touch,
# so a tap on a phone was heard by plotly and dropped by Streamlit. A declared
# component is the supported way for a drawing to return a value, and it costs
# one static page — `components/calendar/index.html` — with no build step.
_COMPONENT = components.declare_component("parkrun_calendar",
                                          path=str(_ASSET_DIR))


def svg_component(svg: str, height: float, *, key: str) -> dict | None:
    """The same drawing as `_embed`, but a click comes back.

    Returns `{"year": int, "week": int, "seq": int}` for the last cell clicked,
    or None before the first click. `seq` counts clicks: Streamlit discards a
    component value identical to the previous one, and clicking the same square
    twice is a real gesture here (it focuses that week), so the value has to
    differ even when the square does not.
    """
    t = theme()
    return _COMPONENT(svg=svg, tip_bg=t.tip_bg, tip_fg=t.tip_fg,
                      height=int(height), key=key, default=None)



def _legend_item(body: list[str], lx: float, y: float, g: Geom, t: Theme,
                 label: str, *, swatch: str | None = None,
                 names=None) -> float:
    """One swatch-and-label legend entry; returns where the next one starts.

    Four legends were laying this out by hand with the same arithmetic, which
    is three chances for a drift nobody would notice. `names` draws the split
    cell instead of a flat swatch — a diagonal cannot be explained by a square
    of one colour.
    """
    body.append(_split_cell(lx, y, g, names, "") if names
                else _rect(lx, y, g, swatch))
    body.append(_text(lx + g.cell + 5, y + g.cell - 1, label, anchor="start",
                      fill=t.label))
    return lx + g.cell + 10 + 6.05 * len(label) + 16


def _break_row(body: list[str], y: float, g: Geom, t: Theme, gap: str) -> float:
    """The labelled discontinuity drawn in place of skipped years."""
    y += YEAR_JUMP_GAP * 0.55
    body.append(_text(g.label_w, y, f"\u22ef nothing {gap}", anchor="start",
                      size=8.5, fill=t.label))
    return y + YEAR_JUMP_GAP * 0.45


def _total(body: list[str], x: float, y: float, g: Geom, text: str,
           fill: str) -> None:
    body.append(_text(x, y + g.cell - 1, text, anchor="start", size=9.5,
                      fill=fill, weight=600))


# --------------------------------------------------------------------------- #
# Layout A — one grid, year blocks of three athlete rows
# --------------------------------------------------------------------------- #


def render_side_by_side(weeks: pd.DataFrame, years: list[int],
                        totals: pd.DataFrame,
                        g: Geom = DESKTOP,
                        breaks: bool = True) -> tuple[str, float]:
    """All three athletes stacked inside each year block — the comparison view."""
    t = theme()
    stubs = stub_visible_years(weeks, years)
    cell = {(r.athlete_name, r.iso_year, r.iso_week): r
            for r in weeks.itertuples()}

    tot = {(r.athlete_name, r.iso_year): r for r in totals.itertuples()}
    body = [_hatch_defs(t.mark), _month_ticks(g, t.label)]
    block_h = len(ATHLETES) * g.pitch - g.gutter
    tx = g.label_w + 53 * g.pitch + 8
    y = float(g.label_h)
    for yr, gap in _year_rows(years, breaks):
        if gap:
            y = _break_row(body, y, g, t, gap)
        body.append(_text(g.label_w - 6, y + g.cell - 1, str(yr), fill=t.label,
                          weight=600))
        for ai, name in enumerate(ATHLETES):
            ry = y + ai * g.pitch
            for wk in range(1, WEEKS_PER_YEAR + 1):
                if wk == STUB_WEEK and yr not in stubs:
                    continue
                x = cell_x(g, wk)
                r = cell.get((name, yr, wk))
                if r is None:
                    body.append(_rect(x, ry, g, t.empty))
                else:
                    body.append(_athlete_cell(x, ry, g, name,
                                              buggy=r.any_buggy,
                                              tip=r.hover))
            # One total per athlete row, in their own colour — three stacked
            # numbers at the year's right edge read as a mini standings table.
            a = tot.get((name, yr))
            if a is not None:
                _total(body, tx, ry, g, _total_label(a.n, a.bug),
                       ATHLETE_COLORS[name])
        y += block_h + g.block_gap

    return _svg(g.label_w + 53 * g.pitch + g.total_w, y, "".join(body)), y


# --------------------------------------------------------------------------- #
# Layout C — small multiples, one grid per athlete
# --------------------------------------------------------------------------- #


def _athlete_years(weeks: pd.DataFrame, name: str,
                   years: list[int]) -> list[int]:
    """The years to draw for one athlete: only those they actually ran in.

    Years before their first are time before they started, not a gap in their
    running. Whole years they missed *after* starting are real absences, but
    an empty row of 52 grey cells is a weak way to say "nothing all year" —
    `_year_rows` collapses them to a labelled break instead, which is louder
    and takes less space. Raju is missing 2008–2014, Duncan 2021–2022.
    """
    mine = set(weeks.loc[weeks["athlete_name"] == name, "iso_year"])
    return sorted(mine & set(years))


def _year_rows(years: list[int], breaks: bool = True):
    """Yield (year, label-for-the-break-above-it-or-None).

    Rows are evenly pitched, so consecutive rows read as consecutive years.
    Wherever the sequence jumps, the drawing needs to say so — otherwise Raju's
    2007 sits directly on top of his 2015 and claims they are adjacent.

    `breaks=False` when the jump is the READER's doing. A year filter that
    leaves 2019 and 2026 selected has not discovered a gap in anyone's running,
    and labelling one "nothing 2020-2025" would state a falsehood about years
    the filter simply removed.
    """
    prev = None
    for y in years:
        gap = None
        if breaks and prev is not None and y - prev > 1:
            gap = f"{prev + 1}–{y - 1}" if y - prev > 2 else f"{prev + 1}"
        yield y, gap
        prev = y


def render_individual(weeks: pd.DataFrame, years: list[int],
                      totals: pd.DataFrame,
                      g: Geom = NAMED,
                      breaks: bool = True) -> tuple[str, float]:
    """One grid per athlete — the individual view."""
    t = theme()
    stubs = stub_visible_years(weeks, years)
    cell = {(r.athlete_name, r.iso_year, r.iso_week): r
            for r in weeks.itertuples()}

    tot = {(r.athlete_name, r.iso_year): r for r in totals.itertuples()}
    tx = g.label_w + 53 * g.pitch + 8
    body = [_hatch_defs(t.mark)]
    y = 0.0
    for name in ATHLETES:
        # Block header, left-aligned at the margin — not a row label, so it is
        # not squeezed into the year gutter.
        body.append(_text(0, y + 11, name, fill=ATHLETE_COLORS[name],
                          size=12, weight=700, anchor="start"))
        y += g.label_h + 4
        body.append(_month_ticks(g, t.label, y - 5))
        for yr, gap in _year_rows(_athlete_years(weeks, name, years), breaks):
            if gap:
                y = _break_row(body, y, g, t, gap)
            body.append(_text(g.label_w - 6, y + g.cell - 1, str(yr),
                              fill=t.label))
            for wk in range(1, WEEKS_PER_YEAR + 1):
                if wk == STUB_WEEK and yr not in stubs:
                    continue
                x = cell_x(g, wk)
                r = cell.get((name, yr, wk))
                if r is None:
                    body.append(_rect(x, y, g, t.empty))
                else:
                    body.append(_athlete_cell(x, y, g, name,
                                              buggy=r.any_buggy,
                                              tip=r.hover))
            a = tot.get((name, yr))
            if a is not None:
                _total(body, tx, y, g, _total_label(a.n, a.bug),
                       ATHLETE_COLORS[name])
            y += g.pitch
        y += g.block_gap + 6

    return _svg(g.label_w + 53 * g.pitch + g.total_w, y, "".join(body)), y


# --------------------------------------------------------------------------- #
# Layout H — one row per year, marking only head-to-head weeks
# --------------------------------------------------------------------------- #


def _h2h_fill(r, scheme: str, t: Theme) -> str:
    """The flat fill for one head-to-head cell under a non-split scheme."""
    if scheme == "Distinct palette":
        return H2H_DISTINCT[r.classification]
    if scheme == "Three-way highlighted":
        return THREE_WAY_ACCENT if r.classification == THREE_WAY else t.muted
    return H2H_COLORS[r.classification]


def render_h2h_calendar(h2h_weeks: pd.DataFrame, years: list[int],
                        scheme: str = H2H_SCHEMES[0],
                        totals: pd.DataFrame | None = None,
                        g: Geom = DESKTOP, *,
                        lit: set | None = None,
                        selected: tuple | None = None,
                        clickable: bool = False,
                        win_totals: dict | None = None,
                        standings: str = "column",
                        year_wins: dict | None = None,
                        year_style: str = "Initials") -> tuple[str, float]:
    """A's skeleton, one row per year, filled only where a contest happened.

    No buggy marker here on purpose: the cell is an *occasion*, not one
    person's run, so a glyph on it could not say whose buggy it was. The hover
    carries the glyph per participant, where it means something.

    The last three arguments are what tab 3's picker needs on top of the
    picture. `lit` is the set of `(year, week)` the filters admit — everything
    else is drawn faded rather than dropped, so a filtered-out square is still
    somewhere you can click to leave the filter. `selected` gets a box around
    it. `clickable` stamps each contest cell with `data-w`, which is what the
    component reads and sends back to Python.
    """
    t = theme()
    # Right-hand gutters, left to right: the per-year tallies, then the
    # standings if they are a column rather than a legend.
    yt_w = YEAR_TOTAL_W[year_style] if year_wins is not None else 0
    st_w = (TOTALS_COL_W if win_totals is not None and standings == "column"
            else 0)
    tot_w = (yt_w + st_w) if (yt_w or st_w) else g.total_w
    split = scheme == H2H_SCHEMES[1]
    winner = scheme == "Winner's colour"
    cell = {(r.iso_year, r.iso_week): r for r in h2h_weeks.itertuples()}
    tot = {int(r.iso_year): r for r in totals.itertuples()} if totals is not None else {}
    stubs = stub_visible_years(h2h_weeks, years)

    # Only years that actually held a head-to-head. There were none before
    # 2017 even though Raju was running from 2007, and an empty row here would
    # say they met and it went unrecorded.
    h_years = [y for y in years if y in set(h2h_weeks["iso_year"])]

    body = [_cell_clip_def(g), _month_ticks(g, t.label)]
    if year_wins is not None and "bar" in year_style.lower():
        body.append(_hatch_defs(t.mark))
    tx = g.label_w + 53 * g.pitch + 8
    y = float(g.label_h)
    for yr, gap in _year_rows(h_years):
        if gap:
            y = _break_row(body, y, g, t, gap)
        body.append(_text(g.label_w - 6, y + g.cell - 1, str(yr), fill=t.label,
                          weight=600))
        for wk in range(1, WEEKS_PER_YEAR + 1):
            if wk == STUB_WEEK and yr not in stubs:
                continue
            x = cell_x(g, wk)
            r = cell.get((yr, wk))
            if r is None:
                body.append(_rect(x, y, g, t.empty))
                continue
            extra = f' data-w="{yr}-{wk}"' if clickable else ""
            if split:
                drawn = _split_cell(x, y, g, r.participants, r.hover,
                                    extra=extra)
            elif winner:
                # A drawn contest — one in 206 — splits between the joint
                # winners rather than picking one arbitrarily.
                drawn = (_split_cell(x, y, g, r.winners, r.hover,
                                     extra=extra)
                         if len(r.winners) > 1
                         else _rect(x, y, g, ATHLETE_COLORS[r.winners[0]],
                                    tip=r.hover, extra=extra))
            else:
                drawn = _rect(x, y, g, _h2h_fill(r, scheme, t), tip=r.hover,
                              extra=extra)
            if lit is not None and (yr, wk) not in lit:
                # Faded, not removed. The opacity goes on a wrapper so it
                # applies whether the cell is one rect or a clipped split.
                drawn = f'<g opacity="{DIM_OPACITY}">{drawn}</g>'
            body.append(drawn)
            if selected is not None and (yr, wk) == tuple(selected):
                body.append(_rect(x - 1.6, y - 1.6, g, "none", stroke=t.mark,
                                  stroke_w=1.4, w=g.cell + 3.2,
                                  h=g.cell + 3.2,
                                  radius=g.radius + 1.6))
        if year_wins is not None:
            body.extend(_year_total_row(year_style, tx, y, g, t,
                                        year_wins.get(int(yr), {})))
        a = tot.get(yr)
        if a is not None:
            body.append(_text(tx, y + g.cell - 1,
                              _total_tspans(a.n, a.by_athlete, t.label),
                              anchor="start", size=9.5, weight=600,
                              fill=t.label, raw=True))
        y += g.pitch

    if year_wins is not None:
        body.append(_text(tx, G_HEAD_Y(g), "won", anchor="start", size=8.5,
                          fill=t.label))

    if win_totals is not None and standings == "column":
        # The standings, level with the top of the grid: swatch, name, wins,
        # and the wins pushed with a buggy in brackets.
        sx = tx + yt_w
        ty = float(g.label_h)
        body.append(_text(sx, G_HEAD_Y(g), "all years", anchor="start",
                          size=8.5, fill=t.label))
        for nm in ATHLETES:
            n, bug = win_totals.get(nm, (0, 0))
            # Named, not just coloured: where the standings are the only
            # totals on show they are also the only thing saying which colour
            # is whom.
            body.append(_rect(sx, ty, g, ATHLETE_COLORS[nm]))
            body.append(_text(sx + g.cell + 6, ty + g.cell - 1,
                              f"{nm} {_total_label(n, bug) if bug else n}",
                              anchor="start", size=10, weight=600,
                              fill=t.label))
            ty += g.pitch
        y = max(y, ty)

    if standings != "legend":
        return _svg(g.label_w + 53 * g.pitch + tot_w, y + 4,
                    "".join(body)), y + 4

    y += 10
    n_col = "n_occasions" if "n_occasions" in h2h_weeks.columns else "n_contests"
    counts = h2h_weeks.groupby("classification")[n_col].sum().to_dict()
    lx = float(g.label_w)
    if winner:
        # Under the grid, spanning it: the same numbers the column shows, read
        # left to right instead of down.
        for nm in ATHLETES:
            n, bug = (win_totals or {}).get(nm, (0, 0))
            lx = _legend_item(body, lx, y, g, t,
                              f"{nm} won {_total_label(n, bug) if bug else n}",
                              swatch=ATHLETE_COLORS[nm])
    elif scheme == "Three-way highlighted":
        for colour, lab in ((THREE_WAY_ACCENT,
                             f"all three ({int(counts.get(THREE_WAY, 0))})"),
                            (t.muted,
                             f"two of them ({sum(int(v) for k, v in counts.items() if k != THREE_WAY)})")):
            lx = _legend_item(body, lx, y, g, t, lab, swatch=colour)
    elif split:
        # The legend is the two cell shapes themselves — a swatch of flat
        # colour would not explain a diagonal.
        for names, cls in (
            (("George", "Raju"), "George vs Raju"),
            (("Raju", "Duncan"), "Duncan vs Raju"),
            (("George", "Duncan"), "Duncan vs George"),
            (("George", "Raju", "Duncan"), "Duncan vs George vs Raju"),
        ):
            n = int(counts.get(cls, 0))
            lx = _legend_item(body, lx, y, g, t, f"{cls} ({n})", names=names)
    else:
        # Swatch, pairing, count, in the order the app names them.
        palette = (H2H_DISTINCT if scheme == "Distinct palette" else H2H_COLORS)
        for cls, colour in palette.items():
            n = int(counts.get(cls, 0))
            lx = _legend_item(body, lx, y, g, t, f"{cls} ({n})", swatch=colour)
    y += g.pitch

    return _svg(g.label_w + 53 * g.pitch + tot_w, y, "".join(body)), y


embed_svg = _embed  # public name; the app should not reach for an underscore


# --------------------------------------------------------------------------- #
# The clickable head-to-head calendar (tab 3)
# --------------------------------------------------------------------------- #
# plotly rather than SVG for one reason: this calendar has to be clickable, and
# `st.plotly_chart(on_select="rerun")` is the only selection channel Streamlit
# offers without a custom bidirectional component. An SVG in a components.html
# iframe is sandboxed and cannot talk back to Python at all.


@st.cache_data(show_spinner=False)
def h2h_calendar_frame(h2h: pd.DataFrame) -> pd.DataFrame:
    """One row per WEEK that held a head-to-head — 205 of them.

    Week grain, not occasion grain. Two contests in one week would otherwise
    land on the same square and overlap, with whichever the click resolved to
    first silently winning; as a week the square can say that both happened
    and, when they were won by different people, show both winners.

    `winners` is the union across the week's contests, in ATHLETES order, and
    drives the fill: one name is a solid cell, two is a diagonal split. It is
    two in exactly two weeks — 2019 wk 1 (two contests, George took one and
    Raju the other) and 2022 wk 9 (one contest, a dead heat). Three has never
    happened; `_split_cell` divides evenly if it ever does.
    """
    occ = (h2h.sort_values(["run_date", "place_rank"])
              .groupby(["run_date", "event_id"], as_index=False)
              .agg(short_name=("short_name", "first"),
                   classification=("classification", "first")))
    won = {}
    for (d, ev), g in h2h[h2h["place_rank"] == 1].groupby(["run_date", "event_id"]):
        won[(d, ev)] = tuple(n for n in ATHLETES if n in set(g["athlete_name"]))

    lines = {}
    for (d, ev), g in h2h.groupby(["run_date", "event_id"]):
        head = (f"{g['short_name'].iloc[0]} · {d:%a %-d %b %Y}"
                f" · {g['classification'].iloc[0]}")
        body = "\n".join(
            f"{MEDAL.get(int(r.place_rank), '·')} {r.athlete_name}"
            f"{mode_suffix(r.is_buggy)} {fmt_time(r.actual_seconds)}"
            for r in g.sort_values("place_rank").itertuples()
        )
        lines[(d, ev)] = head + "\n" + body

    # Calendar year and the fixed 7-day week, matching the SQL — this frame
    # derives its own coordinates, so it has to use the same scheme or the
    # picker would disagree with the calendars about which cell a date is in.
    occ["iso_year"] = occ["run_date"].dt.year.astype(int).to_numpy()
    occ["iso_week"] = week_of_year_series(occ["run_date"]).to_numpy()

    rows = []
    for (iy, iw), g in occ.groupby(["iso_year", "iso_week"], sort=True):
        g = g.sort_values("run_date")
        keys = [(d, int(e)) for d, e in zip(g["run_date"], g["event_id"])]
        wins = []
        for k in keys:
            for n in won.get(k, ()):
                if n not in wins:
                    wins.append(n)
        rows.append({
            "iso_year": int(iy),
            "iso_week": int(iw),
            "n_contests": len(keys),
            "occ_keys": tuple(keys),
            # The most recent contest of the week is what a click selects.
            "latest_date": keys[-1][0],
            "latest_event": keys[-1][1],
            "classification": g["classification"].iloc[-1],
            "winners": tuple(n for n in ATHLETES if n in wins),
            "hover": "\n".join(lines[k] for k in keys),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Phone layouts — three candidates, for the bench in calendar_proto.py
# --------------------------------------------------------------------------- #
# 53 columns across a 358px phone is 4.9px per cell, which is a quarter of the
# 44px minimum touch target. Each of these trades something different away to
# fix that; which trade is right is a question for the picture, not the code.

PHONE_LAYOUTS = ("Swipe sideways", "Turned on its side", "One year at a time")


def unscaled(svg: str) -> str:
    """Take the drawing off the container's leash — the swipe treatment.

    `max-width:100%` shrinks the whole grid to whatever width it is given, so a
    390px phone renders 53 columns at 4.9px: a quarter of a 44px touch target,
    and nothing anyone can read or hit. Left unscaled the cells stay the size
    they were designed at and the *frame* scrolls sideways instead, which is
    what GitHub's own graph does on a phone. Pair it with `scroll_x=True`.
    """
    return svg.replace("max-width:100%;height:auto;display:block",
                       "display:block")


def render_swipe(weeks: pd.DataFrame, years: list[int], totals: pd.DataFrame,
                 g: Geom = NAMED,
                 breaks: bool = True) -> tuple[str, float]:
    """Full-size cells in a strip you swipe. Keeps the familiar shape.

    Identical to the individual view except the SVG is NOT scaled to the
    container, so cells stay ~11px and the frame scrolls sideways instead.
    """
    svg, h = render_individual(weeks, years, totals, g, breaks)
    return unscaled(svg), h


def render_transposed(weeks: pd.DataFrame, years: list[int],
                      totals: pd.DataFrame, g: Geom = DESKTOP
                      ) -> tuple[str, float]:
    """Weeks DOWN the page, years across — the axis swap.

    Scrolling down is what a phone does naturally, and ~13 year columns across
    358px is ~27px each, a real target. It cannot serve the side-by-side view,
    where every year would need three sub-columns of ~9px; that limitation is
    part of what is being judged.
    """
    t = theme()
    lab_w = 34
    cell = {(r.athlete_name, r.iso_year, r.iso_week): r
            for r in weeks.itertuples()}
    body = [_hatch_defs(t.mark)]

    for i, yr in enumerate(years):
        body.append(_text(lab_w + i * g.pitch + g.cell / 2, g.label_h - 5,
                          str(yr)[2:], anchor="middle", fill=t.label))
    month_at = {v: k for k, v in MONTH_WEEK.items()}
    for wk in range(1, WEEKS_PER_YEAR + 1):
        y = g.label_h + (wk - 1) * g.pitch
        if wk in month_at:
            body.append(_text(lab_w - 6, y + g.cell - 1,
                              MONTHS[month_at[wk] - 1], fill=t.label))
        for i, yr in enumerate(years):
            x = lab_w + i * g.pitch
            hgt = g.cell
            hit = [(n, cell.get((n, yr, wk))) for n in ATHLETES]
            hit = [(n, r) for n, r in hit if r is not None]
            if not hit:
                body.append(_rect(x, y, g, t.empty, h=hgt))
                continue
            # One column per year, so a week several of them ran is split
            # vertically between them — the same idea as the head-to-head
            # split cell, applied to attendance.
            bw = g.cell / len(hit)
            tip = "\n".join(r.hover for _, r in hit)
            for j, (n, r) in enumerate(hit):
                fill = (f"url(#{_hatch_id(n)})" if r.any_buggy
                        else ATHLETE_COLORS[n])
                body.append(_rect(x + j * bw, y, g, fill, w=bw, h=hgt,
                                  tip=tip if j == 0 else None))
    h = g.label_h + WEEKS_PER_YEAR * g.pitch
    w = lab_w + len(years) * g.pitch
    # `max-width:100%` only ever shrinks. This grid is NARROWER than a phone
    # (13 year columns is ~216px), so left alone it would sit at 11px cells and
    # waste the width the axis swap was meant to buy — ~27px a column is the
    # whole point of turning it on its side.
    svg = _svg(w, h, "".join(body))
    return svg.replace("max-width:100%;height:auto;display:block",
                       "width:100%;height:auto;display:block"), h


def render_year_blocks(weeks: pd.DataFrame, year: int, totals: pd.DataFrame,
                       athlete: str, per_row: int = 5) -> tuple[str, float]:
    """One year, wrapped — 53 weeks at 5 across, so the cells are big.

    Most tappable of the three by a distance, and the only one that stops being
    a single picture of the whole history, which is what the calendar is for.
    """
    t = theme()
    g = Geom(cell=52, gutter=7, radius=6, label_w=46, label_h=18, total_w=0)
    cell = {r.iso_week: r for r in weeks.itertuples()
            if r.athlete_name == athlete and r.iso_year == year}
    body = [_hatch_defs(t.mark)]
    y = float(g.label_h)
    for wk in range(1, WEEKS_PER_YEAR + 1):
        col = (wk - 1) % per_row
        if col == 0 and wk > 1:
            y += g.pitch
        x = g.label_w + col * g.pitch
        if col == 0:
            a, _ = week_span(year, wk)
            body.append(_text(g.label_w - 8, y + g.cell / 2 + 4,
                              f"{a:%-d %b}", size=11, fill=t.label))
        r = cell.get(wk)
        if r is None:
            body.append(_rect(x, y, g, t.empty))
        else:
            fill = (f"url(#{_hatch_id(athlete)})" if r.any_buggy
                    else ATHLETE_COLORS[athlete])
            body.append(_rect(x, y, g, fill, tip=r.hover))
    h = y + g.pitch
    return _svg(g.label_w + per_row * g.pitch, h, "".join(body)), h

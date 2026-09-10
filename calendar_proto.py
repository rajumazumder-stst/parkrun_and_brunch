"""Design bench for the participation calendars — dev only, not the app.

    PARKRUN_DB=data/parkrun_dev.duckdb streamlit run calendar_proto.py --server.port 8503

Port 8503 leaves 8501 (the app) and 8502 (label_impact) free.

Everything it draws now lives in `parkrun_calendar.py`, which `parkrun_app.py`
imports for real. This page survives the promotion because it is the only place
the five head-to-head colour schemes can be seen against each other; the app
ships one of them. Delete it once that choice stops being revisited.

History worth not repeating: four layouts were built and two kept (B, an
intensity ramp, could not say *who*; D, a continuous 1,000-week timeline, was
14,000px wide). Four buggy treatments were built and the diagonal hatch kept.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from parkrun_calendar import (
    ATHLETES,
    PHONE_LAYOUTS,
    YEAR_TOTAL_STYLES,
    h2h_win_totals,
    h2h_year_wins,
    render_swipe,
    render_transposed,
    render_year_blocks,
    H2H_SCHEMES,
    SCHEME_NOTES,
    athlete_year_totals,
    h2h_week_frame,
    h2h_year_totals,
    load_h2h,
    load_runs,
    render_h2h_calendar,
    render_headline,
    unscaled,
    render_individual,
    render_side_by_side,
    week_frame,
)
from parkrun_calendar import _embed as _embed
from parkrun_ui import DB_PATH, data_version


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #



def data_checks(runs: pd.DataFrame, weeks: pd.DataFrame,
                h2h: pd.DataFrame, hw: pd.DataFrame) -> pd.DataFrame:
    """Verified against data/parkrun_snapshot.duckdb before any of this was
    drawn. A mismatch means the prep is wrong, not that the data moved —
    except after a refresh, when the counts legitimately grow.
    """
    bug = weeks[weeks["any_buggy"]].groupby("athlete_name").size()
    mixed = int((weeks["any_buggy"] & ~weeks["all_buggy"]).sum())
    # The negative control: weeks where 2+ ran but NOT together. Nothing draws
    # this any more, but it is the cheapest guard that the occasion join key is
    # still (event_id, run_date) and has not become week-shaped.
    per_week = weeks.groupby(["iso_year", "iso_week"]).agg(
        n=("athlete_name", "nunique"), sh=("shared", "any"))
    apart = int(((per_week["n"] >= 2) & (~per_week["sh"])).sum())
    cls = h2h[["run_date", "event_id", "classification"]].drop_duplicates()
    cc = cls["classification"].value_counts()
    checks = [
        ("runs", len(runs), 844),
        ("athlete-weeks", len(weeks), 822),
        ("max runs in a week", int(weeks["n_runs"].max()), 3),
        ("buggy weeks — George", int(bug.get("George", 0)), 31),
        ("buggy weeks — Duncan", int(bug.get("Duncan", 0)), 6),
        ("buggy weeks — Raju", int(bug.get("Raju", 0)), 0),
        ("weeks both buggy and regular", mixed, 1),
        ("head-to-head occasions", len(cls), 206),
        ("head-to-head weeks", len(hw), 205),
        ("  George vs Raju", int(cc.get("George vs Raju", 0)), 148),
        ("  Duncan vs Raju", int(cc.get("Duncan vs Raju", 0)), 33),
        ("  all three", int(cc.get("Duncan vs George vs Raju", 0)), 23),
        ("  Duncan vs George", int(cc.get("Duncan vs George", 0)), 2),
        ("weeks mixing two classifications",
         int((h2h.groupby(["iso_year", "iso_week"])["classification"]
              .nunique() > 1).sum()), 0),
        ("negative control: ran apart", apart, 92),
        ("ISO years present", int(weeks["iso_year"].nunique()), 13),
    ]
    df = pd.DataFrame(checks, columns=["check", "actual", "expected"])
    df["ok"] = ["✅" if a == e else "❌" for a, e in zip(df["actual"],
                                                        df["expected"])]
    return df


def main() -> None:
    st.set_page_config(page_title="Calendar prototype", page_icon="🗓️",
                       layout="wide")
    st.title("🗓️ Participation calendar")
    st.caption(
        f"Throwaway prototype · {DB_PATH} · pick one, then it gets built properly."
    )

    ver = data_version()
    runs = load_runs(ver)
    weeks = week_frame(runs)
    ytot = athlete_year_totals(runs)
    h2h = load_h2h(ver)
    hw = h2h_week_frame(h2h)
    htot = h2h_year_totals(h2h)

    all_years = sorted(weeks["iso_year"].unique())
    lo, hi = st.select_slider(
        "ISO years shown",
        options=all_years,
        value=(all_years[0], all_years[-1]),
        help="Local to this prototype — it deliberately does not import the "
             "app's Year/Season control, because importing parkrun_app runs "
             "the whole app.",
    )
    years = [y for y in all_years if lo <= y <= hi]

    render_headline(runs, weeks, years)
    st.divider()

    with st.expander("Data checks"):
        st.dataframe(data_checks(runs, weeks, h2h, hw), hide_index=True,
                     width="stretch")

    ta, tc, th, tp = st.tabs([
        "A · year blocks", "C · small multiples", "H · head-to-heads",
        "📱 Phone",
    ])

    with ta:
        st.markdown(
            "**One grid, three rows per year** — George, Raju, Duncan in their "
            "own colours. Who ran, and when, in one picture."
        )
        svg, h = render_side_by_side(weeks, years, ytot)
        _embed(svg, h)
        st.caption(
            "Buggy runs are hatched — 37 weeks of 822 (George 31, Duncan 6, "
            "Raju never). Hover any cell for the date, the parkrun and the time."
        )

    with tc:
        st.markdown(
            "**One grid per athlete.** Cleanest read of one person's rhythm — "
            "streaks and gaps jump out. Each block starts at that athlete's "
            "first parkrun: years before they started are not a gap in their "
            "running, and drawing them empty would say they turned up to "
            "nothing."
        )
        svg, h = render_individual(weeks, years, ytot)
        _embed(svg, h)
        st.caption(
            "A jump in the year sequence gets real space and a label — Raju's "
            "one run in 2007 is eight years before his next, and evenly "
            "pitched rows would read as consecutive. Years missed *after* "
            "starting stay as empty rows, because those are real absences."
        )

    with th:
        st.markdown(
            "**One row per year, marking only the weeks they actually met** — "
            "coloured by which pairing it was. The sparsest of the three, and "
            "the one that answers *when do they run together*."
        )
        scheme = st.radio("Colour scheme", H2H_SCHEMES, horizontal=True,
                          key="h2h_scheme", label_visibility="collapsed")
        svg, h = render_h2h_calendar(hw, years, scheme, htot,
                                     win_totals=h2h_win_totals(h2h),
                                     standings="legend")
        _embed(svg, h)
        st.caption(SCHEME_NOTES[scheme])
        st.caption(
            "206 contests across 205 weeks; the one doubled week "
            "(2019 wk 1) hovers with both. A head-to-head here is "
            "`v_head_to_head`, the app's definition: 2+ of them at the same "
            "parkrun on the same day, with enough valid form targets to be "
            "ranked. No buggy marker — a cell is an occasion, not one person's "
            "run, so the glyph goes in the hover where it can say whose."
        )

        st.divider()
        st.markdown(
            "**The picker's gutters.** What tab 3 of the app shows beside the "
            "same grid: per-year win tallies on the right, and the overall "
            "standings somewhere. Every combination is drawable; the app "
            "currently ships *Off* + *Column*, which is the first pair below."
        )
        gc1, gc2 = st.columns(2)
        y_style = gc1.radio("Per-year tallies", ("Off",) + YEAR_TOTAL_STYLES,
                            key="h2h_ystyle")
        stand = gc2.radio("Standings", ("column", "legend", "none"),
                          key="h2h_stand",
                          format_func=lambda v: {"column": "Column, right",
                                                 "legend": "Legend, under",
                                                 "none": "None"}[v])
        # Counted off the years the slider admits, so the tallies move with it
        # the way the app's move with its filters.
        pool = h2h[h2h["iso_year"].isin(years)]
        svg2, h2 = render_h2h_calendar(
            hw, years, "Winner's colour",
            win_totals=h2h_win_totals(pool), standings=stand,
            year_wins=(None if y_style == "Off" else h2h_year_wins(pool)),
            year_style=("Initials" if y_style == "Off" else y_style),
        )
        _embed(unscaled(svg2), h2, scroll_x=True)
        st.caption(
            "A year's tallies are **wins**, and a dead heat credits both "
            "runners — 2022 reads 24 against 23 head-to-heads, which is also "
            "why the standings sum to 207 against 206. No row shows a total "
            "beside them for that reason."
        )

    with tp:
        st.markdown(
            "**The phone bench.** 53 columns across a 358px screen is 4.9px a "
            "cell — a quarter of a 44px touch target. Each layout below gives "
            "that up for something different. **Open this on the phone**, not "
            "in a narrow desktop window."
        )
        layout = st.radio("Layout", PHONE_LAYOUTS, key="ph_layout",
                          horizontal=True)
        st.caption(
            "Tapping a square raises the sheet from the bottom of the "
            "**page**, not of the chart — it is built in the parent document, "
            "because a fixed element inside the frame is fixed to the frame, "
            "and the frame is only as tall as the drawing. Tap anywhere to "
            "dismiss. The three other tap treatments that were benched here "
            "(floating label, panel underneath, compact label) are gone: the "
            "sheet won, and the app now shares one implementation with this "
            "page."
        )

        if layout == "Swipe sideways":
            st.caption(
                "Cells stay full size and the strip scrolls sideways — the "
                "shape you already know, at the size it was designed for. "
                "What GitHub's own graph does on a phone. You lose seeing a "
                "whole year at once."
            )
            svg, h = render_swipe(weeks, years, ytot)
            _embed(svg, h, scroll_x=True)
        elif layout == "Turned on its side":
            st.caption(
                "Weeks run down, years across — scrolling down is what a phone "
                "does anyway, and ~13 columns across is ~27px each. A week "
                "more than one of them ran is split vertically between them. "
                "This one cannot express the side-by-side view."
            )
            svg, h = render_transposed(weeks, years, ytot)
            _embed(svg, h)
        else:
            yc1, yc2 = st.columns(2)
            who = yc1.selectbox("Runner", ATHLETES, key="ph_who")
            yr = yc2.selectbox("Year", list(reversed(years)), key="ph_year")
            st.caption(
                "One year wrapped to five weeks a row, so the cells are ~52px "
                "and unmissable. The trade is the whole point of the "
                "calendar: it is no longer one picture of the whole history."
            )
            svg, h = render_year_blocks(weeks, int(yr), ytot, who)
            _embed(svg, h)

    st.divider()
    st.caption(
        "Weeks are counted from 1 January — week 1 is 1-7 Jan and every week "
        "after is a fixed 7 days, so week 53 is the leftover (31 Dec, or "
        "30-31 Dec in a leap year). It is drawn full size, like any other "
        "cell, and only in the years where that leftover holds a Saturday or "
        "somebody ran in it — 2016 and 2022 here."
    )


# `streamlit run` executes this as __main__, so the guard costs nothing there
# and lets the data prep be imported and checked without a Streamlit runtime.
if __name__ == "__main__":
    main()

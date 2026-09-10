"""parkrun & brunch — comparison app for George, Duncan and Raju.

Reads the `parkrun` schema (read-only) from the local DuckDB and presents:
  Tab 1  intro + participation overlap (Venn) + per-athlete company
  Tab 2  head-to-head summary (targets, latest result, record, cumulative 1sts)
  Tab 3  head-to-head detail (drill into a single contest: scoreline one-liner
         + victory lollipop chart + results table)
  Tab 4  form — target time by Saturday
  Tab 5  map — where the head-to-heads happen

This is a PAGE SCRIPT, not the entrypoint: `app.py` routes to it via
`st.navigation`, which is what lets the handicap analysis live at a path on the
same domain without a nav link. `st.set_page_config` therefore lives in `app.py`
— only one call is legal per run — and the browser-tab title for this page comes
from its `st.Page(title=...)`.

Run:  streamlit run app.py
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import duckdb
import folium
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from matplotlib_venn import venn3
from streamlit_folium import st_folium

import parkrun_calendar as cal
from parkrun_core import TARGET_WINDOW_DAYS
from parkrun_ui import (  # shared with label_impact.py — see that module
    ATHLETE_COLORS,
    BUGGY_GLYPH,
    PLACE_COLORS,
    PLACE_LABEL,
    _h2h_headline,
    _read_sql,
    _render_basis_note,
    _victory_fig,
    HL_BUGGY,
    HL_REGULAR,
    REGULAR_LABEL,
    UK_TZ,
    data_version,
    fmt_time,
)

# Logo built by scripts/build_logo.py (three runners in ATHLETE_COLORS on a
# fried egg). Resolved off __file__, not the CWD, so it survives being launched
# from anywhere. Falls back to the old emoji if the file is missing, so a bad
# checkout degrades to a working app rather than a crash on line 1.
_ICON = Path(__file__).resolve().parent / "static" / "logo-512.png"
def _inject_home_screen_icons() -> None:
    """Give "Add to Home Screen" our icon on both platforms.

    The two platforms read different things, so both are needed:

    * iOS uses `apple-touch-icon`. Streamlit's index.html ships only
      `<link rel="shortcut icon">`, so iOS falls back to fetching an icon from
      the server root and gets the host's default. page_icon rewrites just that
      one favicon href, so it cannot reach the home screen.
    * Android/Chrome prefers a **web app manifest** and only falls back to
      apple-touch-icon when there is none. Streamlit Cloud serves its own
      manifest, which is why installing gave an app called "Streamlit" with
      their logo. Chrome honours only the *first* `<link rel="manifest">`, so
      theirs must be removed rather than ours merely appended.

    The component runs in a same-origin iframe, so it can reach the real
    document head. Both platforms read the DOM when the user taps install, so
    links injected at load time are visible by then. None of this is supported
    by Streamlit, hence the guard: any failure leaves the page untouched.
    """
    st.components.v1.html(
        """<script>
        try {
          var d = window.parent.document;
          // Served by [server] enableStaticServing in .streamlit/config.toml.
          // Must be real URLs: iOS ignores data: URIs for apple-touch-icon.
          if (!d.querySelector('link[rel="apple-touch-icon"]')) {
            var l = d.createElement('link');
            l.rel = 'apple-touch-icon';
            l.href = './app/static/apple-touch-icon.png';
            d.head.appendChild(l);
          }
          if (!d.querySelector('link[data-prb-manifest]')) {
            // Drop the host's manifest first - Chrome uses only the first one.
            d.querySelectorAll('link[rel="manifest"]').forEach(function (n) {
              n.parentNode.removeChild(n);
            });
            var m = d.createElement('link');
            m.rel = 'manifest';
            m.setAttribute('data-prb-manifest', '1');
            m.href = './app/static/manifest.json';
            d.head.appendChild(m);
          }
        } catch (e) { /* cross-origin or no parent: leave the page alone */ }
        </script>""",
        height=0,
    )


_inject_home_screen_icons()


# --------------------------------------------------------------------------- #
# Data access (read-only; cached so the DB lock is held only briefly)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_data_meta(version) -> pd.Series:
    """Update markers from the data: the latest parkrun date and when the
    pipeline last wrote (max scrape_timestamp — a server-side fact, advanced on
    every refresh). Keyed on `version` so it refetches when a refresh lands."""
    df = _read_sql(
        """
        SELECT max(run_date)         AS latest_parkrun,
               max(scrape_timestamp)  AS pipeline_last_run
        FROM parkrun.results
        """
    )
    return df.iloc[0]


@st.cache_data(show_spinner=False)
def data_fetched_at(version) -> datetime:
    """When this app last actually pulled fresh data from the backend. Cached on
    `version`, so the timestamp is stamped when a new version triggers a refetch
    (or the Reload button clears the cache) and otherwise stays put — i.e. the
    age of the data currently on screen, not merely 'now'."""
    return datetime.now(timezone.utc)


@st.cache_data(show_spinner=False)
def load_overlap(version) -> pd.DataFrame:
    return _read_sql("SELECT * FROM parkrun.v_overlap")


@st.cache_data(show_spinner=False)
def load_h2h(version) -> pd.DataFrame:
    return _with_date_cols(_read_sql("SELECT * FROM parkrun.v_head_to_head"))


@st.cache_data(show_spinner=False)
def load_targets(version) -> pd.DataFrame:
    return _read_sql(
        """
        SELECT a.athlete_name, t.mode, t.target_seconds, t.n_window,
               t.refresh_date
        FROM parkrun.current_targets t
        JOIN parkrun.athletes a USING (athlete_id)
        WHERE t.refresh_date = (SELECT max(refresh_date) FROM parkrun.current_targets)
          -- One row per (athlete, mode). A mode the athlete has no runs in
          -- carries n_window = 0 and a NULL target — drop those rather than
          -- render an empty tile.
          AND t.n_window >= 1
        """
    )


@st.cache_data(show_spinner=False)
def load_target_window_runs(version) -> pd.DataFrame:
    """The individual runs behind each athlete's current-form target: their runs
    in the window [latest refresh_date − 91, − 1] (the same window the target
    median is taken over). Drives the per-athlete 'runs in window' popover."""
    df = _read_sql(
        f"""
        WITH latest AS (SELECT max(refresh_date) AS d FROM parkrun.current_targets)
        SELECT a.athlete_name, r.run_date, e.short_name, r.time_seconds,
               r.is_buggy, r.mode
        FROM parkrun.v_results_moded r
        JOIN parkrun.athletes a USING (athlete_id)
        JOIN parkrun.events e USING (event_id)
        CROSS JOIN latest
        WHERE r.run_date BETWEEN latest.d - {TARGET_WINDOW_DAYS} AND latest.d - 1
        ORDER BY a.athlete_name, r.run_date DESC
        """
    )
    df["run_date"] = pd.to_datetime(df["run_date"])
    return df


@st.cache_data(show_spinner=False)
def load_personal_bests(version) -> pd.DataFrame:
    """Each athlete's fastest run in each of three scopes, plus their latest run.

    Scopes are anchored on the latest ``refresh_date`` (so they move with the
    data, not the wall clock) and both rolling windows are inclusive of the
    anchor day — a run earlier today counts. Ties on time break to the EARLIEST
    date: the first time they ran that fast.

    ``Latest run`` is the odd one out and is deliberately kept in the same
    frame: it is the same five facts (time, venue, date, mode, how many runs
    the scope covers) about one run, so the renderer can lay it out with the
    same fixed-height helpers. It ranks by date DESC — a same-day double breaks
    to the faster of the two, which is the one that would be quoted anyway.

    Note the 3-month window is *calendar* months, so it is a day or two wider
    than the 91-day form-target window used by the head-to-head — these answer
    different questions (best single run vs. baseline for a contest)."""
    return _read_sql(
        """
        WITH anchor AS (SELECT max(refresh_date) AS d FROM parkrun.current_targets),
        runs AS (
            SELECT a.athlete_name, r.run_date, e.short_name, r.time_seconds,
                   r.is_buggy
            FROM parkrun.v_results_moded r
            JOIN parkrun.athletes a USING (athlete_id)
            JOIN parkrun.events e USING (event_id)
            WHERE r.time_seconds IS NOT NULL
        ),
        scoped AS (
            SELECT 'All time' AS scope, 1 AS scope_ord, runs.* FROM runs
            UNION ALL
            SELECT 'Last 12 months', 2, runs.* FROM runs, anchor
            WHERE runs.run_date BETWEEN anchor.d - INTERVAL 12 MONTH AND anchor.d
            UNION ALL
            SELECT 'Last 3 months', 3, runs.* FROM runs, anchor
            WHERE runs.run_date BETWEEN anchor.d - INTERVAL 3 MONTH AND anchor.d
        ),
        fastest AS (
            SELECT scope, scope_ord, athlete_name, run_date, short_name,
                   time_seconds, is_buggy, n_runs
            FROM (
                SELECT *, count(*) OVER (PARTITION BY scope, athlete_name) AS n_runs,
                       row_number() OVER (PARTITION BY scope, athlete_name
                                          ORDER BY time_seconds, run_date) AS rn
                FROM scoped
            )
            WHERE rn = 1
        ),
        -- Ranked by date, not time, so it needs its own window function rather
        -- than another branch of `scoped`.
        latest AS (
            SELECT 'Latest run' AS scope, 4 AS scope_ord, athlete_name, run_date,
                   short_name, time_seconds, is_buggy, n_runs
            FROM (
                SELECT *, count(*) OVER (PARTITION BY athlete_name) AS n_runs,
                       row_number() OVER (PARTITION BY athlete_name
                                          ORDER BY run_date DESC, time_seconds) AS rn
                FROM runs
            )
            WHERE rn = 1
        )
        SELECT * FROM fastest
        UNION ALL
        SELECT * FROM latest
        ORDER BY scope_ord, time_seconds
        """
    )


@st.cache_data(show_spinner=False)
def load_saturday_targets(version) -> pd.DataFrame:
    return _with_date_cols(_read_sql("SELECT * FROM parkrun.v_saturday_targets"))


@st.cache_data(show_spinner=False)
def load_event_coords(version) -> pd.DataFrame:
    return _read_sql(
        "SELECT event_id, short_name, latitude, longitude FROM parkrun.events"
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _season_label(ts) -> str:
    """Year-qualified meteorological season, e.g. '2019 Spring' or '2018/19 Winter'.

    Winter spans the New Year: Dec YYYY and Jan/Feb YYYY+1 form one block labelled
    'YYYY/YY+1 Winter' (so Dec 2018 and Jan 2019 are both '2018/19 Winter').
    """
    m, y = ts.month, ts.year
    if 3 <= m <= 5:
        return f"{y} Spring"
    if 6 <= m <= 8:
        return f"{y} Summer"
    if 9 <= m <= 11:
        return f"{y} Autumn"
    y1, y2 = (y, y + 1) if m == 12 else (y - 1, y)
    return f"{y1}/{str(y2)[-2:]} Winter"


def _with_date_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Add the run_date/year/season_label columns the date-filtered tabs share."""
    df["run_date"] = pd.to_datetime(df["run_date"])
    df["year"] = df["run_date"].dt.year
    df["season_label"] = df["run_date"].map(_season_label)
    return df


def _ordered_seasons(df: pd.DataFrame) -> list:
    """Season labels present in df, in chronological (first-seen-by-date) order."""
    ordered = df.sort_values("run_date")["season_label"]
    return list(dict.fromkeys(ordered))


def _date_options(df: pd.DataFrame):
    """(year_opts, season_opts) for the given (already classification-filtered)
    rows, each led by 'All'. Drives the head-to-head-aware filter lists."""
    years = ["All"] + [str(y) for y in sorted(df["year"].unique())]
    seasons = ["All"] + _ordered_seasons(df)
    return years, seasons


def _clear_other(active_key: str, other_key: str) -> None:
    """Year/Season are mutually exclusive: picking a real value in one resets the
    other to 'All' (two dropdowns, auto-clear)."""
    if st.session_state.get(active_key, "All") != "All":
        st.session_state[other_key] = "All"


def _sanitize(key: str, opts: list) -> None:
    """Drop a stored selection no longer offered (e.g. after the classification
    changed) so the selectbox doesn't error on an out-of-range value."""
    if st.session_state.get(key, "All") not in opts:
        st.session_state[key] = "All"


def year_season_filters(df: pd.DataFrame, prefix: str, col_year, col_season):
    """Render the mutually-exclusive Year/Season dropdowns for `df` into the two
    given columns, keyed by `prefix`; return the (year, season) selections. Each
    defaults to 'All', options are limited to what `df` holds, and picking one
    auto-clears the other. Shared by every tab that offers date filtering."""
    yr_opts, se_opts = _date_options(df)
    yk, sk = f"{prefix}_year", f"{prefix}_season"
    _sanitize(yk, yr_opts)
    _sanitize(sk, se_opts)
    yr = col_year.selectbox("Year", yr_opts, key=yk,
                            on_change=_clear_other, args=(yk, sk))
    se = col_season.selectbox("Season", se_opts, key=sk,
                              on_change=_clear_other, args=(sk, yk))
    return yr, se


def _fmt_uk_dt(ts) -> str:
    """A timestamp shown in UK local time (DST-aware), e.g. '05 Jul 2026, 15:41'.
    Naive timestamps are assumed UTC (how scrape_timestamp is stored)."""
    if ts is None or pd.isna(ts):
        return "—"
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(UK_TZ).strftime("%d %b %Y, %H:%M")


def _fmt_uk_date(d) -> str:
    """A date shown as e.g. 'Sat 04 Jul 2026'."""
    if d is None or pd.isna(d):
        return "—"
    return pd.Timestamp(d).strftime("%a %d %b %Y")


def _render_window_runs(runs: pd.DataFrame, athlete_name: str) -> None:
    """One table of an athlete's target-window runs (date desc), covering BOTH
    modes, with the run(s) that set each mode's target highlighted.

    One box per athlete rather than one per target: the window is a single span
    of that athlete's running, and splitting it into two tables made you open
    two popovers to see one 91-day period. The highlight is computed PER MODE,
    though — each target is the median of its own runs, so a single highlighted
    pair would misrepresent which runs set which target.
    """
    g = (
        runs[runs["athlete_name"] == athlete_name]
        .sort_values("run_date", ascending=False)
        .reset_index(drop=True)
    )
    if g.empty:
        st.caption("No runs in the window.")
        return

    has_modes = "mode" in g.columns and g["is_buggy"].any()
    groups = (
        [(m, g[g["mode"] == m]) for m in ("nonbuggy", "buggy")] if has_modes
        else [(None, g)]
    )
    # Median by *time* within each mode: the middle 1 (odd) or 2 (even) rows.
    # Colour-coded by mode — two targets, two highlights, so the reader can see
    # at a glance which runs produced which number.
    med: dict[int, str] = {}
    for m, sub in groups:
        if sub.empty:
            continue
        by_time = sub["time_seconds"].sort_values().index.tolist()
        n_ = len(by_time)
        idx = ({by_time[n_ // 2]} if n_ % 2
               else {by_time[n_ // 2 - 1], by_time[n_ // 2]})
        style = HL_BUGGY if m == "buggy" else HL_REGULAR
        med.update({i: style for i in idx})

    # Glyph appended to the parkrun name rather than given a column of its own:
    # these rows are all one athlete, so there is no name to hang it on, and a
    # column used by a handful of rows costs more width than it earns.
    venue = g["short_name"]
    if has_modes:
        venue = [f"{v} {BUGGY_GLYPH}" if b else v
                 for v, b in zip(g["short_name"], g["is_buggy"])]
    disp = pd.DataFrame({
        "Date": g["run_date"].dt.strftime("%d %b %Y"),
        "parkrun": venue,
        "Time": g["time_seconds"].map(fmt_time),
    })

    def _hl(row):
        style = med.get(row.name, "")
        return [style for _ in row]

    st.dataframe(
        disp.style.apply(_hl, axis=1),
        hide_index=True,
        width="stretch",
    )
    if has_modes:
        counts = " · ".join(
            f"{len(sub)} {BUGGY_GLYPH if m == 'buggy' else REGULAR_LABEL}"
            for m, sub in groups if not sub.empty
        )
        st.caption(
            f"{counts}. Highlighted = the run(s) whose time **is** that "
            f"target — 🟨 {REGULAR_LABEL}, 🟦 {BUGGY_GLYPH} (the median, or "
            f"the two averaged for an even count)."
        )
    else:
        n_ = len(g)
        kind = ("median (= the target)" if n_ % 2
                else "the two runs averaged for the target")
        st.caption(f"🟨 Highlighted = {kind}.")


# One type size for the scope label and the where/when line, a larger one for
# the time itself — the block's whole point is that the times read first.
PB_SMALL = "0.82rem"
PB_BIG = "1.65rem"
PB_LINE = 1.35  # line-height of the small type, in em
PB_VENUE_LINES = 2  # venue block is ALWAYS this tall — see _venue
# The bordered container pads all four sides equally, but the athlete name's
# line box adds half-leading at the top that the last (small) line doesn't
# match — so the box reads tight at the bottom. This spacer squares it up.
PB_BOTTOM_PAD = "0.45rem"
PB_SCOPES = ["All time", "Last 12 months", "Last 3 months"]
# The latest run sits BELOW the three scopes, full box width, behind a rule —
# not as a fourth column. Two reasons: a fourth column would squeeze the time
# (the one thing the block exists to make read first) to the point where the
# buggy glyph wraps and breaks the fixed-height alignment; and "most recent" is
# a different kind of fact from "fastest", so reading it as a fourth PB would
# be a misreading the layout should prevent rather than invite.
PB_LATEST = "Latest run"
# Gap between a time and its 🛒. A plain space sets it in the *time's* tabular
# figures, which are wide and make the glyph read as a sixth digit; an explicit
# margin separates the mark from the number it annotates.
PB_GLYPH_GAP = "0.38em"
# Streamlit stacks columns below its own 640px breakpoint, which turns each
# athlete's three scopes into a vertical list. Kept horizontal here: the three
# are meant to be *compared*, and stacked they read as three unrelated facts.
# Scoped to the keyed scope rows (`st.container(key=...)` emits `st-key-<key>`)
# so the athlete boxes themselves still stack — three side by side on a phone
# would be unreadable — and so no other column layout in the app is touched.
PB_PHONE_BREAKPOINT = "640px"
PB_PHONE_BIG = "1.05rem"    # the time, shrunk to fit three across a phone
PB_PHONE_SMALL = "0.66rem"  # scope label, venue and date at that width

# Current-target box: the mode label small, the time itself large.
TGT_SMALL = "0.82rem"
TGT_BIG = "1.5rem"
TGT_GAP = "0.75rem"   # space between the last target and the popover button


def render_personal_bests(pb: pd.DataFrame) -> None:
    """One bordered box per athlete: the three scopes side by side, then that
    athlete's latest run full-width beneath a rule.

    Every line is fixed-height (the venue block included), so the times, venues
    and dates sit on the same levels across all three boxes and the boxes end
    up identical in height. The latest-run strip reuses the same three helpers
    for exactly that reason — a bespoke layout there would drift out of
    alignment the first time a venue name got long."""
    if pb.empty:
        st.info("No results yet — run a refresh.")
        return

    # Phone layout, in two parts that are deliberately scoped differently.
    #
    # Type sizing hangs off the WHOLE block (`st-key-pb-block`), not off the
    # scope rows: the latest-run strip sits outside those rows, so scoping the
    # sizes there left it at the desktop size and the four slots disagreed on a
    # phone. One selector for all four is what keeps them consistent — a slot
    # added later inherits it rather than having to be remembered.
    #
    # Un-stacking is scoped to the scope rows only. Streamlit stacks every
    # column below its own 640px breakpoint; `flex:1 1 0` with `min-width:0` is
    # what actually lets them shrink — Streamlit sets a per-column min-width
    # that forces the wrap regardless of `flex-wrap`. The athlete boxes are
    # left to stack: three of those side by side would be unreadable.
    st.markdown(
        f"""<style>
        @media (max-width: {PB_PHONE_BREAKPOINT}) {{
          .st-key-pb-block .pb-big {{
              font-size: {PB_PHONE_BIG} !important;
          }}
          .st-key-pb-block .pb-small,
          .st-key-pb-block .pb-venue {{
              font-size: {PB_PHONE_SMALL} !important;
          }}
          /* Slightly under the time's size so the mark annotates the number
             rather than competing with it, and never wraps it at this width. */
          .st-key-pb-block .pb-glyph {{
              margin-left: 0.24em !important;
              font-size: 0.85em;
          }}
          /* The rule and the bottom spacer are sized for desktop type; at
             phone type they leave the box looking loose. */
          .st-key-pb-block hr {{
              margin: 0.4rem 0 0.35rem !important;
          }}
          [class*="st-key-pb-scopes-"] [data-testid="stHorizontalBlock"] {{
              flex-wrap: nowrap !important;
              gap: 0.4rem !important;
          }}
          [class*="st-key-pb-scopes-"] [data-testid="stColumn"] {{
              flex: 1 1 0 !important;
              min-width: 0 !important;
              width: auto !important;
          }}
        }}
        </style>""",
        unsafe_allow_html=True,
    )

    all_time = pb[pb["scope"] == "All time"].sort_values("time_seconds")
    order = list(all_time["athlete_name"])

    # The class on each helper is what the phone media query re-sizes; the
    # inline style stays the desktop default so the block still renders
    # correctly if the stylesheet below ever fails to inject.
    def _small(text: str, muted: bool = True) -> str:
        op = "opacity:.72;" if muted else ""
        return (f"<div class='pb-small' style='font-size:{PB_SMALL};{op}"
                f"line-height:1.35'>{text}</div>")

    def _big(text: str) -> str:
        return (
            f"<div class='pb-big' style='font-size:{PB_BIG};font-weight:600;"
            f"line-height:1.15;font-variant-numeric:tabular-nums'>{text}</div>"
        )

    def _timed(row) -> str:
        """The time, with 🛒 appended when that run was pushed.

        One definition for all four slots — the three scopes and the latest-run
        strip: the glyph marks the exception, so an athlete who never uses a
        buggy sees nothing here and the slots can never disagree about when it
        shows. A fastest run is rarely a buggy run, so most of the time this is
        the plain time; that is the mark doing its job, not a missing feature."""
        t = fmt_time(row["time_seconds"])
        if not row.get("is_buggy"):
            return t
        return (f"{t}<span class='pb-glyph' style='margin-left:{PB_GLYPH_GAP}'>"
                f"{BUGGY_GLYPH}</span>")

    def _venue(text: str) -> str:
        """The venue on a block of FIXED height (PB_VENUE_LINES lines).

        Venue names vary wildly in length ('Woking' vs 'Holywell King George V
        Playing Fields'), and a wrapped name used to push that column's date
        down and stretch its box. Fixing the height keeps every date on one
        level and every box the same height; a name too long for the block is
        clamped with an ellipsis and kept in full in the tooltip."""
        safe = escape(str(text))
        return (
            f"<div class='pb-venue' title='{safe}' style='font-size:{PB_SMALL};"
            f"opacity:.72;line-height:{PB_LINE};"
            f"height:{PB_VENUE_LINES * PB_LINE:.2f}em;"
            "overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;"
            f"-webkit-line-clamp:{PB_VENUE_LINES}'>{safe}</div>"
        )

    # Keyed wrapper so the phone type rules above have one selector covering
    # every slot in the block, scope columns and latest-run strip alike.
    block = st.container(key="pb-block")
    for col, name in zip(block.columns(len(order)), order):
        with col, st.container(border=True):
            st.markdown(
                f"<div style='font-weight:600;font-size:1.05rem;margin-bottom:.35rem'>"
                f"<span style='color:{ATHLETE_COLORS[name]}'>●</span> {name}</div>",
                unsafe_allow_html=True,
            )
            # Keyed so the phone media query above can find this row and only
            # this row: the key becomes an `st-key-…` class on the wrapper.
            scope_row = st.container(key=f"pb-scopes-{name}")
            for scol, scope in zip(scope_row.columns(len(PB_SCOPES)), PB_SCOPES):
                m = pb[(pb["athlete_name"] == name) & (pb["scope"] == scope)]
                with scol:
                    st.markdown(_small(scope, muted=False), unsafe_allow_html=True)
                    if m.empty:
                        st.markdown(_big("—"), unsafe_allow_html=True)
                        st.markdown(_venue("no runs"), unsafe_allow_html=True)
                        st.markdown(_small("—"), unsafe_allow_html=True)
                        continue
                    r = m.iloc[0]
                    # Glyph beside the time, NOT a second box per athlete: the
                    # layout is fixed-height and pinned, and splitting it would
                    # break the alignment the whole block is built on.
                    st.markdown(_big(_timed(r)), unsafe_allow_html=True)
                    st.markdown(_venue(r["short_name"]), unsafe_allow_html=True)
                    st.markdown(
                        _small(pd.Timestamp(r["run_date"]).strftime("%d %b %Y")),
                        unsafe_allow_html=True,
                    )

            latest = pb[(pb["athlete_name"] == name) & (pb["scope"] == PB_LATEST)]
            st.markdown(
                "<hr style='margin:.55rem 0 .5rem;border:0;"
                "border-top:1px solid rgba(128,128,128,.25)'>",
                unsafe_allow_html=True,
            )
            st.markdown(_small(PB_LATEST, muted=False), unsafe_allow_html=True)
            if latest.empty:
                st.markdown(_big("—"), unsafe_allow_html=True)
                st.markdown(_venue("no runs"), unsafe_allow_html=True)
                st.markdown(_small("—"), unsafe_allow_html=True)
            else:
                r = latest.iloc[0]
                st.markdown(_big(_timed(r)), unsafe_allow_html=True)
                st.markdown(_venue(r["short_name"]), unsafe_allow_html=True)
                st.markdown(
                    _small(pd.Timestamp(r["run_date"]).strftime("%d %b %Y")),
                    unsafe_allow_html=True,
                )
            st.markdown(
                f"<div style='height:{PB_BOTTOM_PAD}'></div>",
                unsafe_allow_html=True,
            )


def _gap_filled_saturdays(sat: pd.DataFrame) -> pd.DataFrame:
    """Reindex each athlete's target series onto every Saturday between *their
    own* first and last target — inserting NaN where a Saturday has no target so
    the line *breaks* across a >91-day inactivity gap rather than bridging it.

    Reindexing per athlete (not the global span) means each trace's x-extent is
    only where that athlete actually has data — no leading/trailing NaN padding —
    so hiding one athlete via the legend lets both axes rescale to those shown."""
    out = []
    # Grouped by (athlete, mode): v_saturday_targets returns a row per mode, so
    # grouping by athlete alone would interleave two series into one line. The
    # NaN reindex then happens WITHIN each mode, which is what we want.
    keys = ["athlete_name", "mode"] if "mode" in sat.columns else ["athlete_name"]
    for key, g in sat.groupby(keys):
        key = key if isinstance(key, tuple) else (key,)
        g = g.sort_values("run_date")
        sats = pd.date_range(g["run_date"].min(), g["run_date"].max(), freq="W-SAT")
        s = (g.set_index("run_date")[["target_seconds", "n_window"]]
               .reindex(sats))
        for col, val in zip(reversed(keys), reversed(key)):
            s.insert(0, col, val)
        out.append(s.rename_axis("run_date").reset_index())
    df = pd.concat(out, ignore_index=True)
    df["target_fmt"] = df["target_seconds"].map(fmt_time)
    return df


def _pie_svg(wins: dict, diameter: int) -> str:
    """A small SVG pie for a venue marker — one slice per athlete, area split by
    their share of form-adjusted 1sts, coloured by ATHLETE_COLORS."""
    r = diameter / 2
    items = [(n, c) for n, c in wins.items() if c > 0]
    total = sum(c for _, c in items)
    if not items or total == 0:
        return ""
    head = (f'<svg width="{diameter}" height="{diameter}" '
            f'viewBox="0 0 {diameter} {diameter}" '
            f'style="filter:drop-shadow(0 1px 1px rgba(0,0,0,.4))">')
    if len(items) == 1:  # a full circle (one 360° arc won't render)
        name = items[0][0]
        return (head + f'<circle cx="{r}" cy="{r}" r="{r - 1}" '
                f'fill="{ATHLETE_COLORS.get(name, "#888888")}" '
                f'stroke="white" stroke-width="1"/></svg>')
    parts, a0 = [head], 0.0
    for name, c in items:
        a1 = a0 + (c / total) * 2 * math.pi
        x0, y0 = r + r * math.sin(a0), r - r * math.cos(a0)
        x1, y1 = r + r * math.sin(a1), r - r * math.cos(a1)
        large = 1 if (a1 - a0) > math.pi else 0
        parts.append(
            f'<path d="M{r},{r} L{x0:.2f},{y0:.2f} '
            f'A{r},{r} 0 {large},1 {x1:.2f},{y1:.2f} Z" '
            f'fill="{ATHLETE_COLORS.get(name, "#888888")}" '
            f'stroke="white" stroke-width="1"/>'
        )
        a0 = a1
    parts.append("</svg>")
    return "".join(parts)


def build_h2h_map(mh: pd.DataFrame, coords: pd.DataFrame):
    """Folium map of head-to-head venues. Each venue is a pie marker sized by the
    number of head-to-heads there and split by wins per athlete. `mh` is a
    (filtered) slice of v_head_to_head; `coords` maps event_id → lat/lon/name.
    Returns a folium.Map, or None when there's nothing to plot."""
    if mh.empty:
        return None
    n_h2h = mh.drop_duplicates(["event_id", "run_date"]).groupby("event_id").size()
    firsts = mh[mh["place_rank"] == 1]
    wins = firsts.groupby(["event_id", "athlete_name"]).size().unstack(fill_value=0)
    # Buggy wins counted PER ATHLETE. A trailing "of which 4 with buggy" would
    # be ambiguous about whose wins it counts.
    if "is_buggy" in firsts.columns:
        bw = (firsts[firsts["is_buggy"]]
              .groupby(["event_id", "athlete_name"]).size().unstack(fill_value=0))
    else:
        bw = pd.DataFrame()
    c = coords.set_index("event_id")

    venues = []
    for event_id, count in n_h2h.items():
        if event_id not in c.index:
            continue
        lat, lon = float(c.at[event_id, "latitude"]), float(c.at[event_id, "longitude"])
        wdict = wins.loc[event_id].to_dict() if event_id in wins.index else {}
        wdict = {k: int(v) for k, v in wdict.items() if v > 0}
        bdict = (bw.loc[event_id].to_dict()
                 if not bw.empty and event_id in bw.index else {})
        d = int(round(14 + 5 * math.sqrt(count)))
        breakdown = " · ".join(
            f"{k} {v}" + (f" ({int(bdict[k])} {BUGGY_GLYPH})"
                          if bdict.get(k) else "")
            for k, v in sorted(wdict.items(), key=lambda x: -x[1]))
        tip = (f"<b>{c.at[event_id, 'short_name']}</b><br>"
               f"{count} head-to-head{'s' if count != 1 else ''}<br>{breakdown}")
        venues.append((lat, lon, d, _pie_svg(wdict, d), tip))

    if not venues:
        return None
    lats = [v[0] for v in venues]
    lons = [v[1] for v in venues]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]
    fmap = folium.Map(location=center, zoom_start=11 if len(venues) == 1 else 5,
                      tiles="OpenStreetMap", control_scale=True)
    for lat, lon, d, svg, tip in venues:
        folium.Marker(
            [lat, lon],
            icon=folium.DivIcon(html=svg, icon_size=(d, d),
                                icon_anchor=(d // 2, d // 2)),
            tooltip=folium.Tooltip(tip),
        ).add_to(fmap)
    if len(venues) > 1:
        fmap.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
    return fmap


def render_occasion(rows: pd.DataFrame, victory: bool = False) -> None:
    """Render the detail block for a single head-to-head occasion; `victory`
    adds the scoreline one-liner + victory lollipops above the table."""
    first = rows.iloc[0]
    date_str = pd.to_datetime(first["run_date"]).strftime("%A %d %B %Y")
    st.markdown(f"#### {first['short_name']} — {date_str}")
    st.caption(f"**{first['classification']}**")
    if victory:
        st.markdown(_h2h_headline(rows))
        st.plotly_chart(_victory_fig(rows), width="stretch")
    d = rows.sort_values("place_rank").assign(
        Place=lambda d: d["place_rank"].map(PLACE_LABEL),
        Target=lambda d: d["target_seconds"].map(fmt_time),
        Actual=lambda d: d["actual_seconds"].map(fmt_time),
        **{"% vs form": lambda d: d["pct_diff"].map(lambda v: f"{v:+.2f}%")},
    ).rename(columns={"athlete_name": "Athlete"})
    # The glyph goes on the name rather than into a Mode column of its own: a
    # whole column earns its width only if most rows use it, and it reads as
    # another attribute of the runner rather than of the run.
    if "is_buggy" in d.columns:
        d["Athlete"] = [
            f"{n} {BUGGY_GLYPH}" if b else n
            for n, b in zip(d["Athlete"], d["is_buggy"])
        ]
    st.table(d[["Place", "Athlete", "Target", "Actual", "% vs form"]]
             .set_index("Place"))
    _render_basis_note(d)


def section(title: str, key: str, *, default: bool = True,
            icon: str = "") -> bool:
    """A titled, collapsible section. Returns True when the body should draw.

    A button rather than `st.expander` for two reasons. Streamlit forbids
    nesting one expander inside another, and tab 2 already has one ("How a
    head-to-head works") that would have had to be dismantled to fit. And an
    expander hides its title inside its own chrome, whereas these sections are
    the page's headings and should still read as headings when open.
    """
    k = f"sec_{key}"
    open_ = st.session_state.setdefault(k, default)
    # The button belongs at the right margin, on the heading's own line, at
    # every width. Streamlit stacks columns below 640px, which dropped it onto
    # a line of its own on a phone; the keyed container is what the CSS needs
    # to opt THIS row out of that without touching any other column layout —
    # the same trick `render_personal_bests` uses for the PB scope row.
    with st.container(key=f"sec-{key}"):
        head, btn = st.columns([8, 1], vertical_alignment="center")
        head.subheader(f"{icon} {title}".strip())
        if btn.button("Hide" if open_ else "Show", key=f"btn_{k}",
                      help=f"{'Hide' if open_ else 'Show'} this section"):
            st.session_state[k] = not open_
            st.rerun()
    return st.session_state[k]


# Shrinks the section buttons and pins them to the right margin. Scoped by the
# `st-key-sec-*` class the keyed container emits, so the sidebar's Reload button
# and every other control in the app keep their normal size and layout.
SECTION_CSS = """
<style>
/* Descendant, not child: `help=` wraps the button in a
   `stTooltipHoverTarget` span, so `> button` matched nothing and the buttons
   stayed at Streamlit's 40px default. */
div[class*="st-key-sec-"] [data-testid="stButton"] button {
    /* Streamlit's own min-height is 2.5rem, which kept these at 40px tall
       however little padding they were given — it has to be named. */
    padding: 0.05rem 0.5rem !important;
    min-height: 0 !important;
    height: 1.55rem !important;
    font-size: 0.72rem !important;
    line-height: 1.2 !important;
    border-radius: 0.35rem;
    opacity: 0.72;
    /* Without this the column is sized to the button's MIN-content, which for
       a wrapping label is one letter — "Hide" came out stacked vertically on a
       phone. Nowrap makes min-content the whole word. */
    white-space: nowrap !important;
}
/* Right-align inside the column rather than floating the button: a float is
   out of flow, so the column — sized to its content — collapsed to 24px on a
   phone and squeezed the button to nothing. */
div[class*="st-key-sec-"] [data-testid="stButton"] {
    display: flex;
    justify-content: flex-end;
    width: 100%;
}
/* Both of these, not just the wrapper: Streamlit sizes a button's element
   container to the button, so the wrapper inherited a 45px box and
   "right-aligned" the button inside itself — 56px shy of the margin at 1440.
   Only the two containers inside a section header row are touched. */
div[class*="st-key-sec-"] [data-testid="stElementContainer"] {
    width: 100% !important;
}
div[class*="st-key-sec-"] [data-testid="stButton"] button:hover {
    opacity: 1;
}
/* Streamlit stacks every column below 640px, which put the button under its
   own heading on a phone. `flex-wrap` alone does nothing — the per-column
   min-width is what forces the wrap, so both have to be named. Scoped to the
   section rows by their keyed container, so no other column layout moves. */
@media (max-width: 640px) {
    div[class*="st-key-sec-"] [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important;
    }
    div[class*="st-key-sec-"] [data-testid="stColumn"] {
        min-width: 0 !important;
        flex: 1 1 0 !important;
    }
    div[class*="st-key-sec-"] [data-testid="stColumn"]:last-child {
        flex: 0 0 auto !important;
    }
}
</style>
"""


def apply_filters(df: pd.DataFrame, cls: str = "All", yr: str = "All",
                  se: str = "All") -> pd.DataFrame:
    if cls != "All":
        df = df[df["classification"] == cls]
    if yr != "All":
        df = df[df["year"] == int(yr)]
    if se != "All":
        df = df[df["season_label"] == se]
    return df


def h2h_filter_row(prefix: str):
    """The classification + Year/Season filter row shared by the detail and map
    tabs: three columns, date options scoped to the picked classification.
    Returns the (classification, year, season) selections."""
    c1, c2, c3 = st.columns(3)
    cls = c1.selectbox("Head-to-head classification", CLASS_OPTS,
                       key=f"{prefix}_class")
    yr, se = year_season_filters(apply_filters(h2h, cls=cls), prefix, c2, c3)
    return cls, yr, se


def apply_calendar_click(hit: dict, cells: pd.DataFrame, h2h: pd.DataFrame, *,
                         sel, focus, in_pool: set, filters: tuple) -> bool:
    """Turn one click on the head-to-head calendar into session state.

    Three gestures on one square, in order: select it, focus its week, go back.
    Returns True when something changed and the page should rerun.

    It writes rather than returns because two of the three outcomes are widget
    values, and Streamlit refuses to set a widget's state after that widget has
    been created in the same run — hence `t3_pending`, applied at the top of
    the next one.
    """
    wk = (int(hit["year"]), int(hit["week"]))
    m = cells[(cells["iso_year"] == wk[0]) & (cells["iso_week"] == wk[1])]
    if m.empty:
        return False
    row = m.iloc[0]

    if focus == wk:
        # Third click on the focused square — back to the full view.
        st.session_state.pop("t3_focus", None)
        return True
    if sel is not None and sel in row["occ_keys"]:
        # Second click, on the square already selected: focus it.
        st.session_state["t3_focus"] = wk
        return True

    # First click on a new square: select it, and drop any focus.
    st.session_state.pop("t3_focus", None)
    keys = list(row["occ_keys"])
    # A week can hold two contests. Take the most recent, and prefer one the
    # filters already admit so a click inside the current filter never jumps
    # outside it.
    admitted = [k for k in keys if k in in_pool]
    clicked = (admitted or keys)[-1]
    st.session_state["t3_occ"] = clicked

    cls, yr, se = filters
    crow = h2h[(h2h["run_date"] == clicked[0])
               & (h2h["event_id"] == clicked[1])].iloc[0]
    pending = {}
    if cls != "All" and cls != crow["classification"]:
        pending["t3_class"] = crow["classification"]
    # Year and season are mutually exclusive in this app, so a year change
    # clears the season exactly as `_clear_other` does.
    if yr != "All" and int(yr) != clicked[0].year:
        pending["t3_year"] = str(clicked[0].year)
        pending["t3_season"] = "All"
    elif se != "All" and _season_label(clicked[0]) != se:
        pending["t3_season"] = _season_label(clicked[0])
        pending["t3_year"] = "All"
    if pending:
        st.session_state["t3_pending"] = pending
        st.session_state["t3_from_click"] = True
    return True


def cumulative_firsts(df: pd.DataFrame) -> pd.DataFrame:
    """Per-athlete 1st-place finishes with a running count — one row per winning
    *occasion*, so a same-day double at two events is two rows, each carrying its
    own parkrun. Ties for 1st across athletes each produce a row (in separate
    athlete groups), so both count.

    Columns: athlete_name, run_date, cum_firsts, short_name. Athletes with no 1st
    places have no rows (the caller notes them separately). The caller draws the
    0-baseline start and the step (hv) line up to each athlete's latest 1st.
    """
    firsts = (
        df[df["place_rank"] == 1]
        .sort_values(["athlete_name", "run_date"], kind="stable")
    )
    firsts = firsts.assign(
        cum_firsts=firsts.groupby("athlete_name").cumcount() + 1
    )
    return (
        firsts[["athlete_name", "run_date", "cum_firsts", "short_name"]]
        .reset_index(drop=True)
    )


def _nice_dtick(maxv: int) -> int:
    """Integer y-axis tick spacing that stays readable as the count grows — a
    tick every 1 is fine for small totals but unreadable for large ones."""
    if maxv <= 12:
        return 1
    for step in (2, 5, 10, 20, 25, 50, 100, 200):
        if maxv / step <= 10:
            return step
    return 500


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
try:
    # One cheap version read per rerun; drives the loaders' cache keys so the app
    # auto-picks-up a new pipeline refresh (see data_version).
    _ver = data_version()
    overlap = load_overlap(_ver)
    h2h = load_h2h(_ver)
    targets = load_targets(_ver)
    target_runs = load_target_window_runs(_ver)
    personal_bests = load_personal_bests(_ver)
    meta = load_data_meta(_ver)
except duckdb.IOException:
    st.error(
        "Could not open the database — something else is holding the lock, "
        "usually DBeaver or a refresh in progress. Close it and reload."
    )
    st.stop()

CLASS_OPTS = ["All"] + sorted(h2h["classification"].unique())

with st.sidebar:
    st.markdown("### 🏃 parkrun & brunch")
    st.markdown(
        f"**Latest parkrun:** {_fmt_uk_date(meta['latest_parkrun'])}  \n"
        f"**Pipeline last run:** {_fmt_uk_dt(meta['pipeline_last_run'])}  \n"
        f"**App last refreshed:** {_fmt_uk_dt(data_fetched_at(_ver))}"
    )
    st.caption(
        "**Latest parkrun** is the most recent run in the data. **Pipeline "
        "last run** is when the data was last scraped. **App last refreshed** "
        "is when this page last pulled it in. All times UK."
    )
    if st.button("🔄 Reload data"):
        st.cache_data.clear()
        st.rerun()

st.markdown(SECTION_CSS, unsafe_allow_html=True)

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["🏃 parkrun & brunch", "⚔️ Head-to-head summary", "🔎 Head-to-head detail",
     "📈 Form (target time)", "🗺️ Where they meet"]
)

# =========================================================================== #
# TAB 1 — intro + overlap
# =========================================================================== #
with tab1:
    st.title("🏃 parkrun & brunch ☕")
    if section("George, Duncan & Raju", "t1_intro"):
        st.markdown(
            """
    Every Saturday morning, three friends — **George**, **Duncan** and **Raju** —
    lace up for a **parkrun**: a free, timed 5k. Some weeks they line up together;
    other weeks they are scattered across the country chasing new venues. The one
    constant? **Brunch afterwards.** ☕🥐

    This app shows when they run, where their parkruns overlap, and turns every
    shared start line into a **form-adjusted** head-to-head.
            """
        )

    st.divider()
    if section("When do they run?", "t1_when"):
        st.caption(
            "Each square is a week. A coloured square means that person ran at "
            "least one parkrun that week — **hover** or **tap** it for the "
            "dates, parkruns and times. A hatched square "
            f'<img src="{cal.hatch_swatch()}" width="12" height="12" '
            'style="vertical-align:-1px">'
            " is a week run pushing a buggy.",
            unsafe_allow_html=True,
        )
        _cal_runs = cal.load_runs(_ver)
        _cal_weeks = cal.week_frame(_cal_runs)
        _cal_tot = cal.athlete_year_totals(_cal_runs)
        _all_years = sorted(_cal_weeks["iso_year"].unique(), reverse=True)

        # Sized so the two controls sit next to each other rather than at
        # opposite ends of the page: the radio needs about as much room as its
        # two options, and the rest is the year box plus a right-hand spacer.
        _c1, _c2, _ = st.columns([2, 3, 3])
        view = _c1.radio(
            "View", ["Side-by-side", "Individual"], horizontal=True,
            key="t1_calview",
        )
        _pick_years = _c2.multiselect(
            "Years", _all_years, default=[_all_years[0]], key="t1_calyears",
            placeholder="All years",
        )
        _cal_years = sorted(_pick_years) if _pick_years else sorted(_all_years)
        # A gap the reader created by deselecting a year is not a gap in
        # anyone's running, so the "nothing 2020-2025" break rows are drawn
        # only when the whole span is on show.
        _breaks = len(_cal_years) == len(_all_years)

        _svg, _h = (
            cal.render_side_by_side(_cal_weeks, _cal_years, _cal_tot,
                                    breaks=_breaks)
            if view == "Side-by-side"
            else cal.render_individual(_cal_weeks, _cal_years, _cal_tot,
                                       breaks=_breaks)
        )
        # Drawn at its designed cell size and scrolled sideways when it does
        # not fit, rather than squeezed to the window: 53 columns shrunk into a
        # phone is 4.9px a cell, too small to read and far too small to hit.
        # The detail then arrives as a sheet from the bottom of the page for a
        # tap, and as the usual floating label for a mouse.
        cal.embed_svg(cal.unscaled(_svg), _h, scroll_x=True)
        st.caption(
            "**Side-by-side** stacks all three inside each year, so you can "
            "compare who was out when. **Individual** gives each of them their "
            "own grid, starting at their first parkrun. **Years** picks which "
            "years to draw — clear the box to show every one of them.\n\n"
            "The number at the end of each year is parkruns run, with the "
            f"buggy count in brackets ({BUGGY_GLYPH}). It counts parkruns "
            "rather than squares, so a week holding two adds two.\n\n"
            "Weeks run from 1 January — week 1 is 1-7 Jan and every week after "
            "is a fixed 7 days, which leaves 31 December (30-31 in a leap "
            "year) over at the end. That last square is drawn only in the "
            "years it could hold a parkrun. On a narrow window or a phone the "
            "grid scrolls sideways rather than shrinking."
        )

    st.divider()
    if section("Where do they run together?", "t1_together"):
        st.caption(
            "Each number counts occasions they shared — the same parkrun on "
            "the same day. The regions do not overlap, so the middle is the "
            "three of them together and nothing else."
        )

        has = {"Raju": "has_raju", "Duncan": "has_duncan", "George": "has_george"}
        r, d, g = (overlap[has["Raju"]], overlap[has["Duncan"]], overlap[has["George"]])
        subsets = (
            int((r & ~d & ~g).sum()),  # Raju only
            int((~r & d & ~g).sum()),  # Duncan only
            int((r & d & ~g).sum()),  # Raju & Duncan
            int((~r & ~d & g).sum()),  # George only
            int((r & ~d & g).sum()),  # Raju & George
            int((~r & d & g).sum()),  # Duncan & George
            int((r & d & g).sum()),  # all three
        )

        col_v, col_b = st.columns([1, 1])
        with col_v:
            fig, ax = plt.subplots(figsize=(5, 5))
            v = venn3(
                subsets=subsets,
                set_labels=("Raju", "Duncan", "George"),
                set_colors=(
                    ATHLETE_COLORS["Raju"],
                    ATHLETE_COLORS["Duncan"],
                    ATHLETE_COLORS["George"],
                ),
                alpha=0.55,
                ax=ax,
            )
            for text in (v.set_labels or []):
                if text:
                    text.set_fontsize(13)
                    text.set_fontweight("bold")
            st.pyplot(fig)

        with col_b:
            # Per-athlete "company" breakdown.
            comp_rows = []
            others = {"Raju": ("Duncan", "George"), "Duncan": ("Raju", "George"),
                      "George": ("Raju", "Duncan")}
            for name, (y, z) in others.items():
                hx, hy, hz = overlap[has[name]], overlap[has[y]], overlap[has[z]]
                comp_rows += [
                    {"athlete": name, "category": "Solo", "count": int((hx & ~hy & ~hz).sum())},
                    {"athlete": name, "category": f"With {y}", "count": int((hx & hy & ~hz).sum())},
                    {"athlete": name, "category": f"With {z}", "count": int((hx & hz & ~hy).sum())},
                    {"athlete": name, "category": "With both", "count": int((hx & hy & hz).sum())},
                ]
            comp = pd.DataFrame(comp_rows)
            cmap = {"Solo": "#cfcfcf", "With both": "#444444",
                    **{f"With {n}": c for n, c in ATHLETE_COLORS.items()}}
            # "With both" stacked last (rightmost); George at the top.
            cat_order = ["Solo"] + [f"With {n}" for n in ATHLETE_COLORS] + ["With both"]
            athlete_order = ["Duncan", "Raju", "George"]
            fig2 = px.bar(
                comp, y="athlete", x="count", color="category", orientation="h",
                color_discrete_map=cmap,
                category_orders={"athlete": athlete_order, "category": cat_order},
                title="Each runner's parkrun company", text="count",
            )
            fig2.update_layout(
                xaxis_title="parkruns", yaxis_title=None, legend_title=None,
                margin=dict(t=50, b=0, l=0, r=0),
            )
            st.plotly_chart(fig2, width="stretch")

# =========================================================================== #
# TAB 2 — head-to-head summary
# =========================================================================== #
with tab2:
    st.header("⚔️ Head-to-head")

    if section("Personal bests", "t2_pbs"):
        st.caption(
            "Each runner's fastest parkrun over three periods — all time, "
            "the last 12 months and the last 3 months, the two rolling "
            "windows counted back from the latest refresh and including that "
            "day. Below the line is their most recent run, whatever the time."
        )
        render_personal_bests(personal_bests)

    st.divider()
    if section("How a head-to-head works", "t2_how", default=False):
        st.markdown(
            """
    A **head-to-head** is any occasion where two or more of them ran the same
    parkrun on the same day.

    As the **personal bests** above show, they run to very different clocks — their
    best times sit minutes apart. Ranking a shared parkrun by finish time would
    therefore hand the same person the win every week and say nothing about how any
    of them actually ran that day. That is exactly why the head-to-head exists: we
    don't compare raw finish times, we compare **performance against recent form**:

    1. Each runner's **target** is the *median* of their times over the **91 days
       before** the event (needs at least one run in that window).
    2. We take the **% difference** between their actual time and that target.
    3. Whoever beat their own form by the most comes **1st** (ties share a place).

    A 3-way where someone has no recent form becomes a 2-way between the other two.

    **Running with a buggy** 🛒 — George and Duncan sometimes run pushing one, which
    costs them time parkrun records nothing about. So each of them has **two**
    targets: one for their runs with the buggy and one for without, and a run is
    always compared against the matching kind. The target is a median, so a few
    slow buggy runs barely move it — pooling the two would judge a buggy run
    against a time it cannot hit, and flatter the ordinary runs a little.

    When someone has no runs of the right kind in the 91 days before a race, we
    bridge from the other kind using their buggy **handicap**. George's handicap
    is measured from his own runs; Duncan has too few buggy runs to measure one,
    so his is a placeholder. Either way the table says when a target was bridged,
    because a bridged target is an estimate rather than a measurement.

    **Labels can be estimates too** — where a run has not been confirmed by the
    runner, we work it out from the run itself, and correct it when it turns out
    wrong. And because a run's label decides which target it is judged against,
    **labelling an old run can change who won it** — the record below runs from
    2017 to today and is not frozen.
            """
        )

    if section("Current form targets", "t2_targets"):
        st.caption(
            "What each of them would be expected to run today: the median of "
            "their times over the last 91 days. This is the number a "
            "head-to-head is scored against. Open the popover to see the runs "
            "behind it."
        )
        if targets.empty:
            st.info("No current targets yet — run a refresh.")
        else:
            # ONE BOX PER ATHLETE, holding their non-buggy target first and their
            # buggy target second. Two athletes now have two current forms each, and
            # a flat row of five tiles buried whose target belonged to whom.
            # Athletes ordered by their primary (non-buggy) target, so the reading
            # order does not jump around as labels change.
            def _primary(name: str) -> float:
                g = targets[targets["athlete_name"] == name]
                nb = g[g["mode"] == "nonbuggy"] if "mode" in g else g
                return float((nb if not nb.empty else g)["target_seconds"].min())

            names = sorted(targets["athlete_name"].unique(), key=_primary)
            cols = st.columns(len(names))
            for col, name in zip(cols, names):
                g = targets[targets["athlete_name"] == name]
                with col.container(border=True):
                    st.markdown(
                        f"<div style='font-weight:600;font-size:1.05rem;"
                        f"margin-bottom:.35rem'>"
                        f"<span style='color:{ATHLETE_COLORS.get(name, '#888')}'>●</span>"
                        f" {name}</div>",
                        unsafe_allow_html=True,
                    )
                    # One line: regular target, then the buggy one after a slash,
                    # marked with the trolley. An athlete with no buggy runs shows
                    # a single time and nothing else — a label or separator would
                    # imply a second target exists.
                    parts, shown = [], 0
                    for mode in ("nonbuggy", "buggy"):
                        row = g[g["mode"] == mode] if "mode" in g else g
                        if row.empty:
                            continue
                        row = row.iloc[0]
                        n = int(row["n_window"])
                        if not n:
                            continue
                        t = fmt_time(row["target_seconds"])
                        parts.append(f"{BUGGY_GLYPH} {t}" if mode == "buggy" else t)
                        shown += n
                    st.markdown(
                        f"<div style='font-size:{TGT_BIG};font-weight:600;"
                        f"font-variant-numeric:tabular-nums;line-height:1.15'>"
                        f"{' / '.join(parts) if parts else '—'}</div>",
                        unsafe_allow_html=True,
                    )

                    # ONE popover per athlete, covering the whole window: it is a
                    # single 91-day span of their running, and a box per target
                    # meant opening two to see one period.
                    if shown:
                        st.markdown(
                            f"<div style='height:{TGT_GAP}'></div>",
                            unsafe_allow_html=True,
                        )
                        total = len(target_runs[target_runs["athlete_name"] == name])
                        with st.popover(f"{total} runs in window", width="stretch"):
                            st.markdown(
                                f"**{name} — {total} runs in the 91-day window**"
                            )
                            _render_window_runs(target_runs, name)
                    else:
                        st.caption("No runs in the last 91 days.")

    st.divider()
    if section("Latest head-to-head", "t2_latest"):
        pick = st.selectbox("Head-to-head classification", CLASS_OPTS, key="t2_class")
        latest_pool = apply_filters(h2h, cls=pick)
        if latest_pool.empty:
            st.info("No head-to-heads match that classification.")
        else:
            latest_date = latest_pool["run_date"].max()
            occ = latest_pool[latest_pool["run_date"] == latest_date]
            occ = occ[occ["event_id"] == occ.iloc[0]["event_id"]]
            render_occasion(occ)

    st.divider()
    if section("Head-to-head record", "t2_record"):
        st.caption(
            ("Showing **every head-to-head**." if pick == "All"
             else f"Showing **{pick}**, chosen above.")
            + " Filter by year *or* season — the two are alternatives, so "
              "picking one clears the other."
        )
        fc1, fc2 = st.columns(2)
        yr, se = year_season_filters(apply_filters(h2h, cls=pick), "t2", fc1, fc2)
        summ = apply_filters(h2h, cls=pick, yr=yr, se=se)

        if summ.empty:
            st.info("No head-to-heads in that year or season.")
        else:
            # 3rd place only exists in a 3-way contest, so only show it when the
            # 3-athlete head-to-head (2 "vs") or "All" is selected; a 2-way has none.
            show_third = pick == "All" or pick.count(" vs ") >= 2
            places = ["1st", "2nd", "3rd"] if show_third else ["1st", "2nd"]

            board = (
                summ.assign(place=summ["place_rank"].clip(upper=3))
                .pivot_table(index="athlete_name", columns="place", values="event_id",
                             aggfunc="count", fill_value=0)
                .rename(columns={1: "1st", 2: "2nd", 3: "3rd"})
                .rename_axis(index="Athlete", columns=None)
            )
            for c in places:
                if c not in board.columns:
                    board[c] = 0
            board = board[places].sort_values("1st", ascending=False)

            tidy = board.reset_index().melt(
                id_vars="Athlete", var_name="Place", value_name="count"
            )
            fig3 = px.bar(
                tidy, x="Athlete", y="count", color="Place", barmode="group",
                color_discrete_map=PLACE_COLORS,
                category_orders={"Place": places, "Athlete": list(board.index)},
                text="count",
            )
            fig3.update_layout(xaxis_title=None, yaxis_title="head-to-heads",
                               legend_title=None, margin=dict(t=10, b=0, l=0, r=0))
            st.plotly_chart(fig3, width="stretch")

            # How many of each placing were run with the buggy, in the same form as
            # the map tooltips: "83 (5 🛒)". The chart keeps the plain counts — a
            # bar cannot carry the split, and a second series would imply buggy and
            # regular placings are alternatives rather than a subset.
            shown = board
            if "is_buggy" in summ.columns and summ["is_buggy"].any():
                bug = (
                    summ[summ["is_buggy"]]
                    .assign(place=lambda d: d["place_rank"].clip(upper=3))
                    .pivot_table(index="athlete_name", columns="place",
                                 values="event_id", aggfunc="count", fill_value=0)
                    .rename(columns={1: "1st", 2: "2nd", 3: "3rd"})
                    .rename_axis(index="Athlete", columns=None)
                    .reindex(index=board.index, columns=places, fill_value=0)
                )
                shown = board.astype(str)
                for c in places:
                    shown[c] = [
                        f"{t} ({b} {BUGGY_GLYPH})" if b else str(t)
                        for t, b in zip(board[c], bug[c])
                    ]
            st.dataframe(shown, width="stretch")

            # ----- cumulative 1st-place finishes over the selected period ----- #
            st.divider()
    if section("Cumulative 1st-place finishes", "t2_trend"):
            if pick == "All":
                st.info(
                    "Pick a head-to-head classification above to see its "
                    "1st places add up over time."
                )
            else:
                period = yr if yr != "All" else (se if se != "All" else "the entire date range")
                st.caption(
                    f"Running total of 1st places in **{pick}** over "
                    f"**{period}**. A shared 1st counts for both of them."
                )
                trend = cumulative_firsts(summ)
                pstart = summ["run_date"].min()

                fig4 = go.Figure()
                no_wins = []
                for name in sorted(summ["athlete_name"].unique()):
                    w = trend[trend["athlete_name"] == name].sort_values("run_date")
                    color = ATHLETE_COLORS.get(name, "#888888")
                    if w.empty:
                        no_wins.append(name)
                        continue
                    # step line from a 0 baseline at the period start (no marker at 0)
                    fig4.add_trace(go.Scatter(
                        x=[pstart, *w["run_date"]], y=[0, *w["cum_firsts"]],
                        mode="lines", line_shape="hv", line=dict(color=color),
                        name=name, legendgroup=name, hoverinfo="skip",
                    ))
                    # markers only on real 1st places; hover names the winning parkrun
                    fig4.add_trace(go.Scatter(
                        x=w["run_date"], y=w["cum_firsts"], mode="markers",
                        marker=dict(color=color, size=8),
                        name=name, legendgroup=name, showlegend=False,
                        customdata=w[["short_name"]].to_numpy(),
                        hovertemplate=(
                            f"<b>{name}</b><br>"
                            "1st places: %{y}<br>"
                            "Date: %{x|%d/%m/%y}<br>"
                            "parkrun: %{customdata[0]}"
                            "<extra></extra>"
                        ),
                    ))

                if fig4.data:
                    fig4.update_yaxes(
                        dtick=_nice_dtick(int(trend["cum_firsts"].max())),
                        rangemode="tozero", tickformat="d", title="cumulative 1sts",
                    )
                    fig4.update_layout(legend_title=None, hovermode="closest",
                                       margin=dict(t=10, b=0, l=0, r=0))
                    st.plotly_chart(fig4, width="stretch")
                    for name in no_wins:
                        st.markdown(f"_{name} has no 1st-place finishes in this selection._")
                else:
                    st.info(f"No 1st places in **{pick}** over that period.")

# =========================================================================== #
# TAB 3 — head-to-head detail
# =========================================================================== #
with tab3:
    st.header("🔎 Head-to-head detail")

    # A click on the calendar cannot write to the filter widgets directly —
    # Streamlit refuses to set a widget's state after that widget has been
    # created in the same run. So the click stashes what it wants here and
    # reruns, and the change is applied before the widgets exist.
    _pending = st.session_state.pop("t3_pending", None)
    if _pending:
        st.session_state.update(_pending)

    show_pick = section("Choose a head-to-head", "t3_pick")

    # Everything below is computed whether or not the picker is on screen: the
    # result section needs the selection, and hiding the controls must not also
    # hide what they last chose.
    pick3, yr3, se3 = (h2h_filter_row("t3") if show_pick
                       else (st.session_state.get("t3_class", "All"),
                             st.session_state.get("t3_year", "All"),
                             st.session_state.get("t3_season", "All")))
    pool = apply_filters(h2h, pick3, yr3, se3)

    cells = cal.h2h_calendar_frame(h2h)
    in_pool = set(zip(pool["run_date"], pool["event_id"]))

    # Changing a filter re-selects the most recent contest that matches it, and
    # drops any single-week focus — a filter change is a move away from the one
    # week you were looking at. The exception is a filter the calendar itself
    # moved: a click has just said exactly which contest it wants, and
    # re-selecting the newest would throw that away in the same breath.
    _fkey = (pick3, yr3, se3)
    if st.session_state.get("t3_filters") != _fkey:
        st.session_state["t3_filters"] = _fkey
        if not st.session_state.pop("t3_from_click", False):
            st.session_state.pop("t3_occ", None)
            st.session_state.pop("t3_focus", None)

    sel = st.session_state.get("t3_occ")
    if sel is not None:
        sel = tuple(sel)
        if sel not in set(zip(h2h["run_date"], h2h["event_id"])):
            sel = None

    # Focus: a second click on the already-selected square narrows everything
    # to that one week. `focus` is (iso_year, iso_week) or None.
    focus = st.session_state.get("t3_focus")
    focus = tuple(focus) if focus else None
    focus_row = None
    if focus is not None:
        m = cells[(cells["iso_year"] == focus[0]) & (cells["iso_week"] == focus[1])]
        if m.empty:
            focus, st.session_state["t3_focus"] = None, None
        else:
            focus_row = m.iloc[0]

    if focus_row is not None:
        # The week's own contests, still honouring the filters — a click can
        # only focus a week the filters already admit, so this is never empty.
        wk = [k for k in focus_row["occ_keys"] if k in in_pool] \
             or list(focus_row["occ_keys"])
        scope = h2h[[k in set(wk) for k in zip(h2h["run_date"], h2h["event_id"])]]
        if show_pick:
            c1, c2 = st.columns([4, 1])
            c1.info(
                f"Showing the week of "
                f"**{focus_row['occ_keys'][0][0]:%-d %b %Y}** — "
                f"{len(wk)} head-to-head{'s' if len(wk) != 1 else ''}. "
                "Tap that square again to go back to all weeks."
            )
            if c2.button("Show all weeks", key="t3_unfocus"):
                st.session_state.pop("t3_focus", None)
                st.rerun()
    else:
        scope = pool

    occ = None
    if scope.empty:
        if show_pick:
            st.info("No head-to-heads match those filters. Pick a square on "
                    "the calendar below and the filters will move to it.")
    else:
        occasions = (
            scope[["run_date", "event_id", "short_name", "classification"]]
            .drop_duplicates()
            .sort_values("run_date", ascending=False)
        )
        labels = {
            f"{r.run_date:%Y-%m-%d} — {r.short_name} ({r.classification})":
            (r.run_date, r.event_id)
            for r in occasions.itertuples()
        }
        keys = list(labels)
        # Sorted newest-first, so index 0 IS the most recent match — which is
        # what an unset or filtered-away selection falls back to.
        idx = next((i for i, k in enumerate(keys) if labels[k] == sel), 0)
        if show_pick:
            choice = st.selectbox(f"Head-to-head ({len(labels)} found)", keys,
                                  index=idx)
            # The dropdown comes before the calendar, so its value drives the
            # highlight in the same run — no rerun, and the box cannot lag.
            sel = labels[choice]
        else:
            sel = labels[keys[idx]]
        st.session_state["t3_occ"] = sel
        occ = scope[(scope["run_date"] == sel[0]) & (scope["event_id"] == sel[1])]

    if show_pick:
        # Focused: only the focused square is lit. Otherwise the filters dim,
        # and every week is still drawn, so a dimmed square remains the way out.
        if focus is not None:
            lit = {focus}
        else:
            lit = {(int(r.iso_year), int(r.iso_week))
                   for r in cells.itertuples()
                   if any(k in in_pool for k in r.occ_keys)}

        # The box goes on the week holding the selected contest, not on the
        # contest — the drawing is week-grained.
        sel_week = None
        if sel is not None:
            m = cells[[sel in k for k in cells["occ_keys"]]]
            if not m.empty:
                sel_week = (int(m.iloc[0]["iso_year"]),
                            int(m.iloc[0]["iso_week"]))

        _cal_svg, _cal_h = cal.render_h2h_calendar(
            cells, sorted(cells["iso_year"].unique()), "Winner's colour",
            lit=lit, selected=sel_week, clickable=True,
            # Both tallies are counted off `pool`, so they say what the filters
            # currently select — the same set the lit squares show.
            win_totals=cal.h2h_win_totals(pool), standings="legend",
            year_wins=cal.h2h_year_wins(pool), year_style="Share bar",
        )
        # Same treatment as tab 1: drawn at its designed size and swiped when
        # it does not fit, so a cell stays a square you can hit with a thumb.
        hit = cal.svg_component(cal.unscaled(_cal_svg), _cal_h, key="t3_cal")
        st.caption(
            "Every head-to-head in one place — one square per week they raced "
            "each other, coloured by who won it. Winning here means beating "
            "your own form target by the most, not finishing first.\n\n"
            "A square can stand for more than one parkrun. **A square split "
            "into two colours means two winners that week**: either a single "
            "head-to-head that ended level, or two head-to-heads taken by "
            "different people.\n\n"
            "The bar at the end of each year is that year's wins, split "
            "between them and hatched over the share won pushing a buggy, "
            "with the number of wins beside it. Both it and the totals "
            "underneath count only what the filters select.\n\n"
            "Tap or click a square to select it, and again to narrow the list "
            "to just that week; a third time goes back. Squares the filters "
            "exclude are faded but still work, and a filter only moves if it "
            "would otherwise hide what you picked."
        )

        # The component returns its last value on every rerun, so the click
        # counter is what says whether this is a NEW click or the same one
        # coming round again. It is also what makes a second click on the same
        # square visible at all — that gesture is the one that focuses it.
        if hit and hit.get("seq") != st.session_state.get("t3_cal_seq"):
            st.session_state["t3_cal_seq"] = hit["seq"]
            if apply_calendar_click(hit, cells, h2h, sel=sel, focus=focus,
                                    in_pool=in_pool, filters=(pick3, yr3, se3)):
                st.rerun()

    st.divider()
    if section("The result", "t3_result"):
        if occ is None:
            st.info("Nothing selected yet — open **Choose a head-to-head** "
                    "above and pick one.")
        else:
            render_occasion(occ, victory=True)

# =========================================================================== #
# TAB 4 — form (target time by Saturday)
# =========================================================================== #
with tab4:
    st.header("📈 Target time by Saturday")
    st.caption(
        "Each runner's form target on every Saturday — the median of their "
        "times over the 91 days before it, which is the same target a "
        "head-to-head is scored against. Lower is faster, and a break in "
        "the line is a Saturday with no runs behind it."
    )
    sat = load_saturday_targets(_ver)
    if sat.empty:
        st.info("No form targets yet — run a refresh.")
    else:
        fc1, fc2 = st.columns(2)
        yr, se = year_season_filters(sat, "t4", fc1, fc2)
        st.caption(
            "Filter by year *or* season. Click a name in the legend to "
            "hide that runner — the axes rescale to whoever is left."
        )
        sat_f = apply_filters(sat, cls="All", yr=yr, se=se)

        if sat_f.empty:
            st.info("No form targets in that year or season.")
        else:
            plot_df = _gap_filled_saturdays(sat_f)
            # One line per (athlete, mode): solid without a buggy, dotted with.
            # No phantom buggy trace for an athlete who has never used one —
            # the view's `n_window >= 1` filter already dropped those rows.
            has_modes = "mode" in plot_df.columns and (plot_df["mode"] == "buggy").any()
            line_kw = dict(
                line_dash="mode",
                line_dash_map={"nonbuggy": "solid", "buggy": "dot"},
            ) if has_modes else {}
            fig = px.line(
                plot_df, x="run_date", y="target_seconds", color="athlete_name",
                color_discrete_map=ATHLETE_COLORS,
                category_orders={
                    "athlete_name": sorted(sat_f["athlete_name"].unique()),
                    "mode": ["nonbuggy", "buggy"],
                },
                custom_data=(["target_fmt", "n_window", "mode"] if has_modes
                             else ["target_fmt", "n_window"]),
                labels={"run_date": "", "target_seconds": "target time",
                        "athlete_name": ""},
                **line_kw,
            )
            fig.update_traces(
                connectgaps=False,
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "Date: %{x|%d/%m/%y}<br>"
                    "Target: %{customdata[0]}<br>"
                    "runs in window: %{customdata[1]:.0f}"
                    "<extra></extra>"
                ),
            )
            if has_modes:
                # px names a combined trace 'Duncan, buggy'; say it in words.
                # legendgroup per athlete so one click hides both their lines.
                def _rename(tr):
                    name, _, mode = tr.name.partition(", ")
                    tr.name = name + (f" {BUGGY_GLYPH}" if mode == "buggy" else "")
                    tr.legendgroup = name
                fig.for_each_trace(_rename)
                st.caption(
                    f"A dotted line is the target for runs **with the "
                    f"buggy** {BUGGY_GLYPH}. Where a runner has both, they "
                    f"had runs of each kind in the same 91-day window."
                )
            # y-axis tick labels as mm:ss at 2-minute steps; autorange on both axes
            # so hiding an athlete via the legend rescales to those still shown.
            lo = int(sat_f["target_seconds"].min() // 120 * 120)
            hi = int(math.ceil(sat_f["target_seconds"].max() / 120) * 120)
            tickvals = list(range(lo, hi + 1, 120))
            fig.update_yaxes(tickvals=tickvals,
                             ticktext=[fmt_time(v) for v in tickvals],
                             title="target time", autorange=True)
            fig.update_xaxes(autorange=True)
            fig.update_layout(legend_title=None, hovermode="closest",
                              margin=dict(t=10, b=0, l=0, r=0))
            st.plotly_chart(fig, width="stretch")

# =========================================================================== #
# TAB 5 — where the head-to-heads happen (map)
# =========================================================================== #
with tab5:
    st.header("🗺️ Where the head-to-heads happen")
    st.caption(
        "Every parkrun where two or more of them have raced each other. "
        "Each circle is sized by how many head-to-heads happened there and "
        "split by who won them, in their colours. Hover a circle for the "
        "breakdown."
    )
    pick5, yr5, se5 = h2h_filter_row("t5")

    if pick5 == "All":
        st.info("Pick a head-to-head classification above to show the map.")
    else:
        mh = apply_filters(h2h, cls=pick5, yr=yr5, se=se5)
        fmap = build_h2h_map(mh, load_event_coords(_ver))
        if fmap is None:
            st.info("No head-to-heads match those filters.")
        else:
            n_venues = mh["event_id"].nunique()
            n_occ = mh.drop_duplicates(["event_id", "run_date"]).shape[0]
            st.caption(f"**{n_venues}** venue{'s' if n_venues != 1 else ''} · "
                       f"**{n_occ}** head-to-head{'s' if n_occ != 1 else ''}")
            st_folium(fmap, height=520, returned_objects=[])

"""Shared UI layer for the parkrun apps.

Imported by both `app.py` (the real app) and `label_impact.py` (the dev-only
old-vs-new comparison, served separately). It holds everything the two have in
common — DB resolution, the palette, time formatting, the buggy-mode display
helpers, and the head-to-head scoreline / victory chart.

`_winning_margin` in particular MUST live here and nowhere else: the comparison
app diffs old against new, and a second copy of that arithmetic would make a
method difference indistinguishable from a rounding difference.
"""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from parkrun_core import SNAPSHOT


def _resolve_db_path() -> str:
    """Locate the DuckDB to read, in priority order:

    1. ``PARKRUN_DB`` env var (local dev against the full personal DB, or a
       MotherDuck connection string, e.g. ``md:parkrun_snapshot``).
    2. A ``PARKRUN_DB`` Streamlit secret (set in the hosting dashboard).
    3. The read-only ``parkrun``-only snapshot bundled with the repo — what a
       deployed/shared instance uses by default.
    """
    env = os.environ.get("PARKRUN_DB")
    if env:
        return env
    try:
        secret = st.secrets.get("PARKRUN_DB")
        if secret:
            return str(secret)
    except Exception:
        pass
    return str(SNAPSHOT)


def _ensure_motherduck_token() -> None:
    """Make the MotherDuck token available to DuckDB when serving from ``md:``.

    DuckDB reads ``motherduck_token`` from the environment. On a hosted deploy
    the token lives in a Streamlit secret instead, so mirror it into the env.
    """
    if os.environ.get("motherduck_token") or os.environ.get("MOTHERDUCK_TOKEN"):
        return
    try:
        tok = st.secrets.get("motherduck_token") or st.secrets.get("MOTHERDUCK_TOKEN")
    except Exception:
        tok = None
    if tok:
        os.environ["motherduck_token"] = str(tok)




DB_PATH = _resolve_db_path()
IS_MOTHERDUCK = DB_PATH.startswith("md:")
if IS_MOTHERDUCK:
    _ensure_motherduck_token()

# Fixed per-athlete colours, used consistently everywhere (Dark2 palette).
ATHLETE_COLORS = {"George": "#1b9e77", "Raju": "#d95f02", "Duncan": "#7570b3"}
PLACE_COLORS = {"1st": "#FFB300", "2nd": "#B0B0B0", "3rd": "#C77B30"}
PLACE_LABEL = {1: "🥇 1st", 2: "🥈 2nd", 3: "🥉 3rd"}
MEDAL = {p: label.split()[0] for p, label in PLACE_LABEL.items()}



def _read_sql(sql: str) -> pd.DataFrame:
    # MotherDuck connections don't take the read_only flag; the local snapshot
    # (and dev DB) open read-only so the app never holds a write lock.
    con = duckdb.connect(DB_PATH) if IS_MOTHERDUCK else duckdb.connect(
        DB_PATH, read_only=True
    )
    try:
        return con.execute(sql).fetchdf()
    finally:
        con.close()




@st.cache_data(ttl=60, show_spinner=False)
def data_version() -> str:
    """Cheap change-detector, re-checked at most once a minute. Passed as a
    *hashed* cache-key arg into the heavy loaders in both apps, so they
    auto-refetch exactly when a refresh writes new data and serve cache
    otherwise (an out-of-band pipeline refresh updates the backend; this is how
    a running app notices without a manual reload). Must NOT start with an
    underscore — Streamlit skips underscore-prefixed args when hashing the key.

    `run_modes` is included because labels are edited OUT OF BAND (direct SQL
    against the source-of-truth DB — see docs/DATA.md), which never advances
    `scrape_timestamp`. `count(*)` as well as `max(set_at)`: deleting a row does
    not move the maximum. Moot on the deployed instance, which only changes on
    redeploy, but it is the local editing workflow that needs it.

    Lives here, once, for the same reason `_winning_margin` does: both apps key
    their caches on this string, and two copies that drifted would have them
    disagreeing about whether the data had changed.
    """
    r = _read_sql(
        """
        SELECT (SELECT max(scrape_timestamp) FROM parkrun.results) AS scraped,
               (SELECT max(set_at) FROM parkrun.run_modes)         AS labelled,
               (SELECT count(*)    FROM parkrun.run_modes)         AS n_labels
        """
    ).iloc[0]
    return f"{r['scraped']}|{r['labelled']}|{r['n_labels']}"


def fmt_time(sec) -> str:
    if pd.isna(sec):
        return "—"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


UK_TZ = ZoneInfo("Europe/London")


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
def years_desc(values) -> list:
    """The distinct years in `values`, newest first — the one order every year
    filter in the app offers (docs/STYLE.md § Filters). The most recent year is
    the one most often wanted, so it leads rather than sitting at the end of a
    list that grows every January. Keeps the values' own type."""
    return sorted(set(values), reverse=True)


# --------------------------------------------------------------------------- #
# Pop-over panels — every one gets a Close button on a phone
# --------------------------------------------------------------------------- #
# A popover closes when you tap outside it, but on a phone a tall one fills
# the screen and there is no "outside" left to tap. The button closes it by
# setting the popover's own state, which Streamlit allows for a keyed popover
# with `on_change="rerun"`. Desktop keeps the old behaviour: the button is
# hidden above the 640px breakpoint, where the outside is always in reach.
POPOVER_CLOSE_PREFIX = "popclose-"
# Injected once per page by the page script (parkrun_app does, beside its
# SECTION_CSS): a <style> emitted inside each popover took a row of its own and
# left a gap above the button.
POPOVER_CLOSE_CSS = f"""<style>
[class*="st-key-{POPOVER_CLOSE_PREFIX}"],
[class*="st-key-{POPOVER_CLOSE_PREFIX}"] [data-testid="stButton"] {{
  display: flex; justify-content: flex-end; width: 100%;
}}
[class*="st-key-{POPOVER_CLOSE_PREFIX}"] button {{
  min-height: 0; padding: 2px 4px; width: auto !important; margin-left: auto;
}}
@media (min-width: 641px) {{
  [class*="st-key-{POPOVER_CLOSE_PREFIX}"] {{ display: none !important; }}
}}
</style>"""


def _close_popover(key: str) -> None:
    st.session_state[key] = False


def closable_popover(label: str, key: str, **kwargs):
    """`st.popover` with a Close ✕ button at the top of its panel (phone only).
    Use it for every popover in the app, so they all close the same way."""
    pop = st.popover(label, key=key, on_change="rerun", **kwargs)
    with pop:
        st.button("Close ✕", key=f"{POPOVER_CLOSE_PREFIX}{key}", type="tertiary",
                  on_click=_close_popover, args=(key,))
    return pop


def keep_widget_state(prefixes: tuple, skip: tuple = ()) -> None:
    """Keep widget values alive across runs where the widget is not drawn.

    Streamlit deletes a widget's session-state entry on any run that does not
    render that widget — a section hidden by its toggle, or the other view of
    tab 5 — so its filters came back at their defaults. Writing each key back
    to itself before the widgets are created is Streamlit's documented way to
    keep it. Call at the top of the block, every run.

    `skip` names keys that must not be written: a button's or a component's
    value cannot be set through session state, and Streamlit raises if one is.
    """
    ss = st.session_state
    for k in list(ss.keys()):
        if (isinstance(k, str) and k.startswith(prefixes)
                and not k.startswith(skip)):
            ss[k] = ss[k]


# --------------------------------------------------------------------------- #
# Stat slots — a headline number with its label above and a note below
# --------------------------------------------------------------------------- #
# The anatomy of the tab 2 personal-best scopes, shared so every headline
# number in the app reads the same way (docs/STYLE.md § Stat slots):
#
#     label   small, full opacity       "All time" / "right overall"
#     value   big, 600, tabular figures "21:36"    / "85%"
#     note    small, muted              "Bushy Park" / "of 34"
#
# The classes are what the phone media query (`stat_phone_css`) re-sizes; the
# inline styles stay the desktop default, so a slot still renders correctly if
# that stylesheet ever fails to inject.
STAT_SMALL = "0.82rem"
STAT_BIG = "1.65rem"
STAT_LINE = 1.35  # line-height of the small type, in em
STAT_PHONE_BREAKPOINT = "640px"
STAT_PHONE_BIG = "1.05rem"    # the value, shrunk to fit three or four across a phone
STAT_PHONE_SMALL = "0.66rem"  # label and note at that width


def stat_label(text: str, lines: int = 1) -> str:
    """`lines` > 1 reserves that many lines and sits the text on the bottom
    one, so a label that wraps on a narrow screen does not push its value below
    its neighbours'. The PB scopes use 1: they are fixed words that fit."""
    box = ""
    if lines > 1:
        box = (f"height:{lines * STAT_LINE:.2f}em;display:flex;"
               "flex-direction:column;justify-content:flex-end;")
    return (f"<div class='pb-small' style='font-size:{STAT_SMALL};"
            f"line-height:{STAT_LINE};{box}'><span>{text}</span></div>")


def stat_value(text: str, *, color: str | None = None) -> str:
    col = f"color:{color};" if color else ""
    return (f"<div class='pb-big' style='font-size:{STAT_BIG};font-weight:600;"
            f"line-height:1.15;font-variant-numeric:tabular-nums;{col}'>{text}</div>")


def stat_note(text: str) -> str:
    """Muted. An empty note still takes its line (a non-breaking space), so
    values sit on one level whether or not their neighbours carry a note."""
    return (f"<div class='pb-small' style='font-size:{STAT_SMALL};opacity:.72;"
            f"line-height:{STAT_LINE}'>{text or '&nbsp;'}</div>")


def stat_phone_css(block_key: str, row_key_prefix: str) -> str:
    """The phone rules for a block of stat slots, as a <style> string.

    Type sizes hang off the whole keyed block; un-stacking is scoped to the
    keyed slot rows. Streamlit stacks every column below its own 640px
    breakpoint and sets a per-column min-width that forces the wrap whatever
    `flex-wrap` says, so `flex:1 1 0` with `min-width:0` is what actually keeps
    the slots side by side. Only rows whose key starts with `row_key_prefix`
    are touched — no other column layout in the app."""
    return f"""
        @media (max-width: {STAT_PHONE_BREAKPOINT}) {{
          .st-key-{block_key} .pb-big {{
              font-size: {STAT_PHONE_BIG} !important;
          }}
          .st-key-{block_key} .pb-small {{
              font-size: {STAT_PHONE_SMALL} !important;
          }}
          [class*="st-key-{row_key_prefix}"] [data-testid="stHorizontalBlock"] {{
              flex-wrap: nowrap !important;
              gap: 0.4rem !important;
          }}
          [class*="st-key-{row_key_prefix}"] [data-testid="stColumn"] {{
              flex: 1 1 0 !important;
              min-width: 0 !important;
              width: auto !important;
          }}
        }}"""


# --------------------------------------------------------------------------- #
# Charts — every plotly chart in both apps goes through `show_chart`
# --------------------------------------------------------------------------- #
# Zoom and pan are locked. On a phone a swipe that starts on a chart was being
# taken as a pan/zoom of that chart, so the page could not be scrolled past it;
# nothing here needs zooming, and the date-filtered charts have year/season
# filters for narrowing the range. `fixedrange` stops the drag; the config stops
# wheel zoom and double-click reset and hides the mode bar, whose zoom buttons
# would otherwise still work. Legend clicks are untouched — they still hide a
# trace — and autorange still rescales to whatever is left visible.
PLOTLY_CONFIG = {"scrollZoom": False, "displayModeBar": False, "doubleClick": False}


def lock_zoom(fig: go.Figure) -> go.Figure:
    """Fix every axis so a drag on the chart cannot pan or zoom it."""
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    return fig


def show_chart(fig: go.Figure, **kwargs) -> None:
    """`st.plotly_chart` with the zoom lock applied. The one place a plotly
    chart is rendered — `tests/test_ui.py` asserts there is no other — so a new
    chart cannot forget it."""
    kwargs.setdefault("width", "stretch")
    st.plotly_chart(lock_zoom(fig), config=PLOTLY_CONFIG, **kwargs)




# --------------------------------------------------------------------------- #
# Buggy mode — display helpers
# --------------------------------------------------------------------------- #
# Shown only for athletes who actually have a buggy run in the loaded data, so
# an athlete who has never pushed one sees an unchanged UI. Computed once per
# render from v_results_moded (see `BUGGY_ATHLETES`).
BUGGY_GLYPH = "🛒"
# "Regular" rather than "without buggy": it is the athlete's normal running,
# and naming it by what it lacks makes the buggy the default in the reader's
# head. Raju has no buggy runs at all, so he is never labelled either way.
REGULAR_LABEL = "regular"


def mode_suffix(is_buggy) -> str:
    """Glyph appended to a runner's name in prose and tables.

    The glyph alone, not the words: it appears mid-sentence in the scoreline,
    where "(with buggy)" after every name made the line hard to read.

    An estimated label is deliberately NOT distinguished here. Both this and
    `mode_text` once carried an "(est.)" qualifier for `source='estimated'`,
    unreachable because no caller ever passed the source. Rather than wire it
    up, the distinction was dropped: an estimate is verified against the
    source-of-truth DuckDB (see docs/DATA.md), not by reading it off the page.
    """
    return f" {BUGGY_GLYPH}" if is_buggy else ""


def mode_text(is_buggy) -> str:
    """Value for a `Mode` column: "With buggy", or blank.

    Blank rather than "Regular": the column only appears when a buggy run is
    present, so it is there to mark the exceptions. Filling every other row
    with a word gives the eye something to read on rows that carry no
    information, and buries the ones that do.
    """
    return "With buggy" if is_buggy else ""




# Median highlights in the "runs in window" table. Two colours because there
# are two targets: one highlight for both would leave the reader unable to tell
# which runs made which number.
HL_REGULAR = "background-color:#ffe08a;color:#111"
HL_BUGGY = "background-color:#bcd8ff;color:#111"


def _surface_color() -> str:
    """The app's current chart surface, for surface-coloured marker rings."""
    try:
        return "#0e1117" if st.context.theme.type == "dark" else "#ffffff"
    except Exception:
        return "#ffffff"


def _h2h_headline(rows: pd.DataFrame) -> str:
    """One-line scoreline for an occasion (percentages/margins to 2 dp), with
    a comment on the third-placed finisher when there is one."""
    d = rows.sort_values(["place_rank", "pct_diff"])
    # Naming a runner without saying they had a buggy would misrepresent the
    # result, so every name in this sentence carries the suffix.
    def nm(r):
        return r["athlete_name"] + mode_suffix(r.get("is_buggy"))
    winners = d[d["place_rank"] == 1]
    w = winners.iloc[0]
    speed = (f"{abs(w['pct_diff']):.2f}% "
             f"{'faster' if w['pct_diff'] <= 0 else 'slower'} than form")
    if len(winners) > 1:
        names = " & ".join(nm(r) for _, r in winners.iterrows())
        line = f"🥇 **{names}** share 1st — both {speed}"
    else:
        ru = d[d["place_rank"] > 1].iloc[0]
        line = (f"🥇 **{nm(w)}** takes it — {speed}, "
                f"{_winning_margin(rows):.2f} points clear of {nm(ru)}")
    third = d[d["place_rank"] >= 3]
    if not third.empty:
        t = third.iloc[0]
        if t["pct_diff"] <= 0:
            line += (f"; **{nm(t)}** still beat their form in 3rd "
                     f"({t['pct_diff']:+.2f}%)")
        else:
            line += (f"; **{nm(t)}** trails in 3rd, "
                     f"{t['pct_diff']:.2f}% off form")
        if len(winners) > 1:    # 1st-place tie: one gap covers both
            line += (f" — {t['pct_diff'] - w['pct_diff']:.2f} pts behind the "
                     f"joint winners")
        else:
            gap2 = t["pct_diff"] - d.iloc[1]["pct_diff"]
            gap1 = t["pct_diff"] - w["pct_diff"]
            line += (f" — {gap2:.2f} pts behind 2nd, "
                     f"{gap1:.2f} pts behind 1st")
    return line


def _winning_margin(rows: pd.DataFrame) -> float | None:
    """1st-to-2nd gap in percentage points. Shared 1st -> 0.0; fewer than two
    ranked -> None.

    One definition, used by the headline, the victory bracket and the label-
    impact tab. A second copy would make a method difference indistinguishable
    from an arithmetic difference when the tab compares old against new.
    """
    d = rows.sort_values(["place_rank", "pct_diff"])
    if len(d) < 2:
        return None
    if (d["place_rank"] == 1).sum() > 1:
        return 0.0
    return float(d.iloc[1]["pct_diff"] - d.iloc[0]["pct_diff"])


BASIS_LABEL = {"buggy": f"{BUGGY_GLYPH} buggy", "nonbuggy": "regular"}
BASIS_HOVER = {
    "nonbuggy+handicap": "regular form + handicap",
    "buggy-handicap": f"{BUGGY_GLYPH} form ÷ handicap",
    "buggy": f"{BUGGY_GLYPH} form",
    "nonbuggy": "regular form",
}


def _basis_hover(r) -> str:
    b = r.get("target_basis")
    return f" ({BASIS_HOVER[b]})" if b in BASIS_HOVER else ""


def _victory_fig(rows: pd.DataFrame) -> go.Figure:
    """Victory lollipops for one occasion: each athlete's raw % vs form from
    the on-form baseline, x-axis reversed (positive/slower left, negative/
    faster right) so beating your form reads in the winning direction, with
    the 1st–2nd winning margin bracketed. Winner on top."""
    d = rows.sort_values(["place_rank", "pct_diff"]).copy()
    # `.get` throughout, not d["is_buggy"]: the label-impact tab feeds this the
    # LEGACY frame, which has neither is_buggy nor target_basis.
    d["medal_name"] = d.apply(
        lambda r: f"{MEDAL[int(r['place_rank'])]} {r['athlete_name']}"
                  + (f" {BUGGY_GLYPH}" if r.get("is_buggy") else ""), axis=1)
    surface = _surface_color()
    fig = go.Figure()
    for _, r in d.iterrows():
        pct = r["pct_diff"]
        fig.add_trace(go.Scatter(   # stem
            x=[0, pct], y=[r["medal_name"]] * 2, mode="lines",
            line=dict(color=ATHLETE_COLORS[r["athlete_name"]], width=3),
            hoverinfo="skip", showlegend=False))
        fig.add_trace(go.Scatter(   # head, labelled with the raw % vs form
            x=[pct], y=[r["medal_name"]], mode="markers+text",
            marker=dict(size=13, color=ATHLETE_COLORS[r["athlete_name"]],
                        line=dict(width=2, color=surface)),
            text=[f"{pct:+.2f}%"],
            # Reversed axis: negative (faster) sits on the right of screen.
            textposition="middle right" if pct <= 0 else "middle left",
            cliponaxis=False,
            hovertemplate=(f"<b>{r['athlete_name']}</b><br>"
                           f"Mode: {BASIS_LABEL.get(r.get('mode'), mode_text(r.get('is_buggy')))}<br>"
                           f"Target: {fmt_time(r['target_seconds'])}"
                           f"{_basis_hover(r)}<br>"
                           f"Actual: {fmt_time(r['actual_seconds'])}<br>"
                           f"{pct:+.2f}% vs form<extra></extra>"),
            showlegend=False))
    lo, hi = min(0.0, d["pct_diff"].min()), max(0.0, d["pct_diff"].max())
    pad = max(1.0, (hi - lo) * 0.30)
    fig.add_vline(x=0, line_width=1, line_color="#999999")
    # Winning-margin bracket between 1st and 2nd (skip on a shared 1st).
    if (d["place_rank"] == 1).sum() == 1 and len(d) > 1:
        w, ru = d.iloc[0], d.iloc[1]
        fig.add_shape(type="line", x0=ru["pct_diff"], x1=w["pct_diff"],
                      y0=-0.45, y1=-0.45, line=dict(color="#808080", width=1))
        for x in (ru["pct_diff"], w["pct_diff"]):
            fig.add_shape(type="line", x0=x, x1=x, y0=-0.45, y1=-0.28,
                          line=dict(color="#808080", width=1))
        fig.add_annotation(x=(w["pct_diff"] + ru["pct_diff"]) / 2, y=-0.75,
                           text=(f"winning margin "
                                 f"{_winning_margin(d):.2f} pts"),
                           showarrow=False,
                           font=dict(size=11.5, color="#808080"))
    fig.update_layout(
        height=120 + 52 * len(d),
        margin=dict(t=16, b=8, l=0, r=0),
        xaxis=dict(range=[hi + pad, lo - pad], ticksuffix="%",
                   title=dict(text="← slower than form · faster than form →",
                              font=dict(size=12, color="#808080"))),
        yaxis=dict(title=None,
                   range=[len(d) - 0.5, -1.1]),  # winner top + bracket headroom
    )
    return fig


# What each target_basis means, in words. A buggy run can no longer carry a
# plain 'nonbuggy' basis, so never test for that combination.
BASIS_NOTE = {
    "nonbuggy+handicap": ("had no buggy runs in the 91-day window, so their "
                          "target is their **regular** form **+ the buggy "
                          "handicap**"),
    "buggy-handicap": ("had no regular runs in the 91-day window, so their "
                       "target is their with-buggy form **÷ the buggy "
                       "handicap**"),
}


def _render_basis_note(rows: pd.DataFrame) -> None:
    """Say so whenever a target was bridged from the opposite mode. A bridged
    target is a different kind of claim from a measured one and the reader has
    to be told which they are looking at."""
    if "target_basis" not in rows.columns:
        return
    notes = [f"**{r['Athlete']}** {BASIS_NOTE[r['target_basis']]}"
             for _, r in rows.iterrows() if r["target_basis"] in BASIS_NOTE]
    if notes:
        st.caption("ℹ️ " + "; ".join(notes) + ".")



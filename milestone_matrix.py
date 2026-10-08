"""Tab 1 — the milestone matrix: each runner's milestone history in one table.
The arithmetic is in `milestones.py`; the milestone list and colours in
`milestone_config.py`. `venue_matrix.py` imports this module's loader and
table helpers, so the two tables drawn one above the other cannot drift.

"Junior" here means a runner's **buggy runs**, counted as a participant of
their own and drawn indented beneath that runner: the child in the buggy is
the one collecting them. Only juniors get the 10 milestone. The adult row
counts every parkrun, buggy or not, which is what parkrun's own total does.

An HTML table rather than `st.dataframe`, which cannot draw a per-cell border
or a tooltip (docs/STYLE.md § Tables). Drawn by `st.html` in the page, so it
scrolls sideways inside its own box on a phone with the names pinned.
"""

from __future__ import annotations

import datetime as dt
from html import escape

import pandas as pd
import streamlit as st

from milestone_config import (ALWAYS_SHOWN, FIRST_RUN_OUTLINE,
                              MILESTONE_COLOURS, MILESTONES)
from milestones import MilestoneSummary, milestone_summary
from parkrun_core import BUGGY_ATHLETE_IDS
from parkrun_ui import (ATHLETE_COLORS, BUGGY_GLYPH, UK_TZ, _read_sql,
                        _surface_color, fmt_n)
from streaks import uk_date

# 5k parkruns only (seriesid 1). Every stored result is one today — the
# scrape reads the adult /all/ page — but the filter says so rather than
# relying on it.
RUNS_SQL = """
SELECT r.athlete_id, a.athlete_name, r.run_date, r.is_buggy,
       r.event_id, e.short_name   -- for venue_matrix.py's first visits
FROM parkrun.v_results_moded r
JOIN parkrun.athletes a USING (athlete_id)
JOIN parkrun.events   e USING (event_id)
WHERE e.seriesid = 1
ORDER BY r.run_date, r.event_id
"""

# What a white or black badge falls back to where it would vanish into the
# page, and the ring every outlined badge carries.
OUTLINE_GREY = "#8c8c8c"
# The app's two page surfaces (parkrun_ui._surface_color).
LIGHT_SURFACE, DARK_SURFACE = "#ffffff", "#0e1117"
JUNIOR_LABEL = f"with buggy {BUGGY_GLYPH}"
# The last column's heading: days from a row's first parkrun to its latest.
SPAN_HEADER = "parkrun lifetime<br>(days)"


@st.cache_data(show_spinner=False)
def load_milestone_runs(version: str) -> pd.DataFrame:
    df = _read_sql(RUNS_SQL)
    df["run_date"] = pd.to_datetime(df["run_date"]).dt.date
    return df


def milestone_rows(runs: pd.DataFrame, order: list[str],
                   today: dt.date) -> list[dict]:
    """One row per runner in `order`, each buggy runner followed by their
    junior (buggy-runs) row."""
    rows = []
    for name in order:
        mine = runs[runs["athlete_name"] == name]
        if mine.empty:
            continue
        rows.append(dict(name=name, junior=False,
                         s=milestone_summary(mine["run_date"], today=today)))
        if int(mine["athlete_id"].iloc[0]) in BUGGY_ATHLETE_IDS:
            buggy = mine.loc[mine["is_buggy"].fillna(False).astype(bool), "run_date"]
            rows.append(dict(name=name, junior=True,
                             s=milestone_summary(buggy, junior=True, today=today)))
    return rows


def milestone_columns(summaries: list[MilestoneSummary]) -> list[int]:
    """Up to 500 always; anything above only once someone has reached it or
    is next to."""
    extra = {m.n for s in summaries for m in s.reached} | {
        s.next_milestone for s in summaries}
    return [m for m in MILESTONES if m in ALWAYS_SHOWN or m in extra]


# --- formatting --------------------------------------------------------------

def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{fmt_n(n)}{suffix}"


def days(n: int) -> str:
    return f"{fmt_n(n)} day{'' if n == 1 else 's'}"


def short_date(d: dt.date) -> str:
    """"14 Mar 26" — the cell's date; the tooltip spells the year in full."""
    return f"{d.day} {d:%b %y}"


def _luminance(hex_: str) -> float:
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def visible(hex_: str, surface: str) -> str:
    """The milestone colour, or grey where it is too close to the page to see
    (white on light, black on dark) — for tints and dashed borders."""
    a, b = sorted((_luminance(hex_), _luminance(surface)))
    return hex_ if (b + 0.05) / (a + 0.05) >= 1.6 else OUTLINE_GREY


def _rgb(hex_: str) -> str:
    return ",".join(str(int(hex_[i:i + 2], 16)) for i in (1, 3, 5))


def themed(prop: str, light: str, dark: str, dark_guess: bool) -> str:
    """A declaration that follows the page's theme. `light-dark()` reads the
    `color-scheme` Streamlit sets on `.stApp`, which is always right; the
    server's own idea of the theme (`st.context.theme`) can disagree with the
    page — it said dark under a forced light theme on a dark-mode Mac — so
    it is only the fallback for a browser without `light-dark()`."""
    return (f"{prop}:{dark if dark_guess else light};"
            f"{prop}:light-dark({light},{dark});")


def tint(hex_: str, dark_guess: bool) -> str:
    """A reached cell's background: a light tint of the milestone colour, in
    grey where the colour would vanish into that theme's page. A pale colour
    (the white 10) tinted as strongly as the rest washes out a dark theme's
    light text, so it gets less."""
    light = visible(hex_, LIGHT_SURFACE)
    dark = visible(hex_, DARK_SURFACE)
    dark_alpha = 0.14 if _luminance(dark) > 0.5 else 0.30
    return themed("background", f"rgba({_rgb(light)},.18)",
                  f"rgba({_rgb(dark)},{dark_alpha})", dark_guess)


# --- HTML --------------------------------------------------------------------

def table_css(p: str, dark_guess: bool, min_width: str, ring: str) -> str:
    """The rules every milestone-style table shares, under class prefix `p`
    (`ms` here, `vm` for venue_matrix.py): the swipe wrapper, cell shape, the
    pinned name column, the big total, the small sub-line, the to-go text, and
    the header badge — filled (`{p}-badge`) or outlined in `ring`
    (`{p}-badge {p}-badge-ring`).
    One copy, so a layout fix lands in both tables drawn one above the other.

    Names are pinned while the table swipes, so their cells need the opaque
    page surface or the milestone cells would show through."""
    empty = "rgba(128,128,128,.07)"
    return f"""
.{p}-wrap {{ overflow-x:auto; -webkit-overflow-scrolling:touch; max-width:100%; }}
.{p} {{ border-collapse:separate; border-spacing:3px; min-width:{min_width};
          width:100%; }}
.{p} th {{ font-size:.8rem; font-weight:600; padding:2px 4px; text-align:center;
          vertical-align:bottom; white-space:nowrap; }}
.{p} td {{ border-radius:6px; padding:3px 2px; text-align:center;
          border:2px solid transparent;
          vertical-align:middle; white-space:nowrap; font-size:.82rem;
          line-height:1.3; background:{empty}; }}
.{p} .{p}-name {{ position:sticky; left:0; z-index:1;
          {themed("background", LIGHT_SURFACE, DARK_SURFACE, dark_guess)}
          text-align:left; font-size:.95rem; font-weight:600; min-width:6.6rem; }}
.{p} .{p}-total {{ font-size:1.45rem; font-weight:700;
          font-variant-numeric:tabular-nums; background:transparent; }}
.{p}-sub {{ font-size:.68rem; opacity:.72; }}
.{p}-togo {{ font-weight:600; }}
.{p}-badge {{ display:inline-block; min-width:2.4rem; padding:2px 6px;
          border-radius:999px; font-weight:700; font-size:.78rem; }}
.{p}-badge-ring {{ border:2px solid {ring}; padding:0 6px; }}
"""


def reached_body(x, p: str) -> str:
    """A reached cell's contents: the date, then days since the first run and
    since the previous milestone."""
    return (f"{short_date(x.date)}<br><span class='{p}-sub'>"
            f"{fmt_n(x.days_since_first)}d · +{fmt_n(x.days_since_prev)}d</span>")


def togo_body(s, p: str) -> str:
    """The next milestone's cell contents: what is still to go."""
    return f"<span class='{p}-togo'>{fmt_n(s.runs_to_go)} to go</span>"


def ring_badge(label: str, p: str) -> str:
    """A header badge outlined rather than filled — the "1st" column."""
    return f"<span class='{p}-badge {p}-badge-ring'>{label}</span>"


def table_html(p: str, css: str, head: str, body: list[str]) -> str:
    """The swipeable table `table_css(p, …)` styles, around its header row and
    body rows — one copy for the same reason as `table_css`."""
    return (css + f"<div class='{p}-wrap'><table class='{p}'>"
            "<thead>" + head + "</thead><tbody>" + "".join(body)
            + "</tbody></table></div>")


def name_cell(name: str, p: str) -> str:
    """A runner's pinned name cell: their colour dot, then the name."""
    dot = ATHLETE_COLORS.get(name, OUTLINE_GREY)
    return (f"<td class='{p}-name'><span style='color:{dot}'>●</span> "
            f"{escape(name)}</td>")


def _css(dark_guess: bool) -> str:
    return f"""<style>{table_css("ms", dark_guess, "820px", FIRST_RUN_OUTLINE)}
/* No opacity here: the pinned cell must stay opaque or the table shows
   through it as it swipes. */
.ms .ms-jr {{ font-weight:400; font-size:.85rem; padding-left:1.1rem; }}
.ms .ms-first {{ box-shadow:inset 0 0 0 2px {FIRST_RUN_OUTLINE}; }}
.ms .ms-na {{ background:transparent; opacity:.55; }}
.ms .ms-days {{ background:transparent; font-variant-numeric:tabular-nums; }}
</style>"""


def _badge(m: int) -> str:
    c = MILESTONE_COLOURS[m]
    ring = f"box-shadow:0 0 0 1px {OUTLINE_GREY};" if c.outline else ""
    return (f"<span class='ms-badge' style='background:{c.bg};color:{c.fg};{ring}'>"
            f"{fmt_n(m)}</span>")


def _who(row: dict) -> str:
    return f"{row['name']} {JUNIOR_LABEL}" if row["junior"] else row["name"]


def _name_cell(row: dict) -> str:
    if row["junior"]:
        return f"<td class='ms-name ms-jr' title='{escape(_who(row))}'>↳ {escape(JUNIOR_LABEL)}</td>"
    return name_cell(row["name"], "ms")


def _first_cell(row: dict) -> str:
    s = row["s"]
    if s.first_date is None:
        return "<td class='ms-first'></td>"
    tip = f"{_who(row)} · 1st on {uk_date(s.first_date)}"
    return (f"<td class='ms-first' title='{escape(tip)}'>"
            f"{short_date(s.first_date)}</td>")


def _milestone_cell(row: dict, m: int, dark_guess: bool) -> str:
    s = row["s"]
    if m == 10 and not row["junior"]:
        return "<td class='ms-na' title='10 is a junior milestone'>–</td>"
    colour = MILESTONE_COLOURS[m].bg
    reached = {x.n: x for x in s.reached}
    if m in reached:
        x = reached[m]
        ladder = [r.n for r in s.reached]
        i = ladder.index(m)
        tip = (f"{_who(row)} · {ordinal(m)} on {uk_date(x.date)} · "
               f"{days(x.days_since_first)} since first")
        if i > 0:
            tip += f" · {days(x.days_since_prev)} since {ordinal(ladder[i - 1])}"
        return (f"<td style='{tint(colour, dark_guess)}' "
                f"title='{escape(tip)}'>{reached_body(x, 'ms')}</td>")
    if m == s.next_milestone:
        tip = f"{_who(row)} · {fmt_n(s.runs_to_go)} to go to the {ordinal(m)}"
        # A border, not an outline: an outline is painted above the pinned
        # name column when the row swipes underneath it.
        border = themed("border-color", visible(colour, LIGHT_SURFACE),
                        visible(colour, DARK_SURFACE), dark_guess)
        return (f"<td style='border-style:dashed;{border}' "
                f"title='{escape(tip)}'>{togo_body(s, 'ms')}</td>")
    return "<td></td>"


def _days_cell(row: dict) -> str:
    s = row["s"]
    if s.first_date is None:
        return "<td class='ms-days'>–</td>"
    tip = (f"{_who(row)} · {days(s.days_first_to_last)} from the 1st "
           f"({uk_date(s.first_date)}) to the latest ({uk_date(s.last_date)})")
    return (f"<td class='ms-days' title='{escape(tip)}'>"
            f"{fmt_n(s.days_first_to_last)}</td>")


def matrix_html(rows: list[dict], dark_guess: bool = False) -> str:
    columns = milestone_columns([r["s"] for r in rows])
    head = ("<tr><th class='ms-name'></th><th>Total runs</th>"
            f"<th>{ring_badge('1st', 'ms')}</th>"
            + "".join(f"<th>{_badge(m)}</th>" for m in columns)
            + f"<th>{SPAN_HEADER}</th></tr>")
    body = []
    for r in rows:
        cells = [_name_cell(r),
                 f"<td class='ms-total'>{fmt_n(r['s'].total)}</td>",
                 _first_cell(r)]
        cells += [_milestone_cell(r, m, dark_guess) for m in columns]
        cells.append(_days_cell(r))
        body.append("<tr>" + "".join(cells) + "</tr>")
    return table_html("ms", _css(dark_guess), head, body)


def render_milestone_matrix(version: str) -> None:
    runs = load_milestone_runs(version)
    if runs.empty:
        st.info("No parkruns yet.")
        return
    today = dt.datetime.now(UK_TZ).date()
    rows = milestone_rows(runs, list(ATHLETE_COLORS), today)
    st.html(matrix_html(rows, dark_guess=_surface_color() == DARK_SURFACE))
    st.caption(
        "Each milestone cell: date reached · days since first · +days since "
        "previous milestone. A dashed cell is the next milestone, with the "
        "runs still to go. Hover a cell for the full sentence.\n\n"
        f"The **{JUNIOR_LABEL}** row under a runner counts only the parkruns "
        "they ran pushing a buggy — the junior's own tally, so it is the only "
        "row that can reach the 10 (adults show –). Its days count from the "
        "first buggy run. Some buggy labels are the model's guesses (tab 6), "
        "so a relabel can move these dates. The runner's own row counts every "
        "parkrun, buggy or not."
    )

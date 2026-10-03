"""Tab 2 — win/loss streaks: the section's layout. The arithmetic, and the
rules for what counts as a win, are in `streaks.py`.

A heatmap: one row per runner, one column per company — any head-to-head,
"vs" each other runner, the three-way. Each cell is that runner's current
streak in that company, coloured green for wins and red for losses, deeper
the longer it is; beneath it, their best-ever win streak there. Chosen on the
`streaks_proto.py` bench over scorecards, diverging bars and a timeline.

Derived from `v_head_to_head` like every other tab 2 figure, with no view of
its own, so relabelling a buggy run moves a streak exactly as it moves the
placings it is counted from. Streaks ignore the classification and year/season
filters above them: a *current* streak only means something counted over the
whole history.
"""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from parkrun_ui import ATHLETE_COLORS, _surface_color, fmt_n
from streaks import best_period, heatmap_column, heatmap_columns, uk_date, win_streaks

# Green and red, as RGB so the cell can carry them at any depth. Text stays the
# theme's colour on top, so both read in light and dark mode; the cell always
# *says* win or loss, so the colour is never the only signal.
WIN_RGB = "26,127,55"     # #1a7f37
LOSS_RGB = "192,57,43"    # #c0392b
# Cell alpha runs from FAINT (a one-result streak) to FAINT + DEPTH (the
# longest current streak in the grid).
FAINT, DEPTH = 0.14, 0.46


def streak_text(n: int, won: bool) -> str:
    noun = ("win" if n == 1 else "wins") if won else ("loss" if n == 1 else "losses")
    return f"{fmt_n(n)} {noun}"


def _css(surface: str) -> str:
    """The name column is pinned while the grid swipes on a phone, so it needs
    an opaque background — the app surface — or the cells would show through."""
    return f"""<style>
.t2-hm-wrap {{ overflow-x:auto; -webkit-overflow-scrolling:touch; }}
.t2-hm {{ border-collapse:separate; border-spacing:4px; width:100%;
          min-width:560px; table-layout:fixed; }}
.t2-hm th {{ font-size:.8rem; font-weight:600; text-align:left; opacity:.8;
             padding:2px 6px; vertical-align:bottom; }}
.t2-hm .t2-hm-name {{ position:sticky; left:0; z-index:1; background:{surface};
             width:6.5rem; vertical-align:middle; font-size:.95rem; opacity:1;
             overflow-wrap:break-word; }}
.t2-hm td {{ border-radius:6px; padding:8px; vertical-align:top;
             overflow-wrap:break-word; }}
.t2-hm .t2-hm-self {{ background:repeating-linear-gradient(45deg,
             rgba(128,128,128,.12) 0 6px, transparent 6px 12px); }}
.t2-hm-val  {{ font-weight:700; font-size:1.05rem; line-height:1.25; }}
.t2-hm-note {{ font-size:.78rem; opacity:.8; line-height:1.35; }}
.t2-hm-live {{ display:inline-block; font-size:.66rem; font-weight:700;
               letter-spacing:.06em; border:1px solid rgb({WIN_RGB});
               border-radius:4px; padding:0 4px; margin-left:3px; }}
</style>"""


def _cell(name: str, column: str, r, top: int) -> str:
    current = streak_text(r["n"], r["won"])
    since = uk_date(r["since"])
    if r["best_n"] == 0:
        best, best_tip = "best: no wins yet", "no wins yet"
    else:
        period = best_period(r["best_since"].date(), r["best_until"].date(), r["live"])
        best = f"best {streak_text(r['best_n'], True)} · {period}"
        best_tip = f"{streak_text(r['best_n'], True)}, {period}"
    live = "<span class='t2-hm-live'>LIVE</span>" if r["live"] else ""
    alpha = FAINT + DEPTH * (r["n"] / top)
    rgb = WIN_RGB if r["won"] else LOSS_RGB
    who = f"{name} {column}" if column.startswith("vs ") else f"{name}, {column.lower()}"
    tip = f"{who}: {current} since {since}. Best: {best_tip}."
    return (f"<td style='background:rgba({rgb},{alpha:.2f})' title='{escape(tip)}'>"
            f"<div class='t2-hm-val'>{current}</div>"
            f"<div class='t2-hm-note'>since {since}</div>"
            f"<div class='t2-hm-note'>{best}{live}</div></td>")


def render_streak_heatmap(h2h: pd.DataFrame, order: list[str]) -> None:
    """Rows in `order` (the personal-best order); the "vs" columns follow it
    too, so each runner's own cell falls on the diagonal and is hatched."""
    streaks = win_streaks(h2h)
    if streaks.empty:
        st.info("No head-to-heads yet.")
        return

    order = [n for n in order if n in set(streaks["athlete_name"])]
    columns = heatmap_columns(order)
    top = max(int(streaks["n"].max()), 1)

    head = ("<tr><th class='t2-hm-name'></th>"
            + "".join(f"<th>{escape(c)}</th>" for c in columns) + "</tr>")
    body = []
    for name in order:
        mine = streaks[streaks["athlete_name"] == name]
        by_col = {heatmap_column(name, s): r
                  for s, r in zip(mine["scope"], mine.to_dict("records"))}
        dot = ATHLETE_COLORS.get(name, "#888")
        cells = [f"<th class='t2-hm-name'><span style='color:{dot}'>●</span> "
                 f"{escape(name)}</th>"]
        for c in columns:
            if c == f"vs {name}":
                cells.append("<td class='t2-hm-self' title='themselves'></td>")
            elif c in by_col:
                cells.append(_cell(name, c, by_col[c], top))
            else:
                cells.append("<td><div class='t2-hm-note'>no head-to-heads</div></td>")
        body.append("<tr>" + "".join(cells) + "</tr>")

    st.html(_css(_surface_color()) + "<div class='t2-hm-wrap'><table class='t2-hm'>"
            + head + "".join(body) + "</table></div>")

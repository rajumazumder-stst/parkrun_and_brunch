"""Design bench for the win/loss streaks — dev only, not the app.

    PARKRUN_DB=data/parkrun_dev.duckdb streamlit run streaks_proto.py --server.port 8504

Port 8504 leaves 8501 (the app), 8502 (label_impact) and 8503 (calendar_proto)
free. Nothing imports this file; deleting it costs the app nothing.

Four ways to draw the same numbers, side by side, so one can be picked by
looking rather than arguing: scorecards, diverging bars, a timeline and a
heatmap. Every view is fed by one `win_streaks()` call from `streaks.py` — the
same function tab 2 uses — so a view can only differ in how it draws, never in
what it counts.

Rules every view keeps: the W/L is always written, never only coloured; dates
are UK ("3 Oct 2026"); text inherits the theme's colour so dark mode works; and
the "long names" switch swaps in long athlete names to show what wraps.
"""

from __future__ import annotations

import datetime as dt
from html import escape
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from parkrun_ui import DB_PATH, _read_sql, data_version, show_chart
from streaks import ANY, opponents_label, scope_order, uk_date, win_streaks

st.set_page_config(page_title="Streaks bench", layout="wide")

# Green and red, each dark enough to carry white text at 4.5:1. Colour is never
# the only signal — every mark also says W or L — so these can be ordinary.
WIN = "#1a7f37"
LOSS = "#c0392b"
TRACK = "rgba(128,128,128,.28)"
MUTED = "rgba(128,128,128,.9)"

# Long enough to wrap anything that can wrap — the stress test for each view.
LONG_NAMES = {
    "George": "George Alexander Montgomery-Fotheringham",
    "Duncan": "Duncan Bartholomew Featherstonehaugh",
    "Raju": "Rajendra Venkataraman-Chakraborty",
}


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_h2h(version) -> pd.DataFrame:
    return _read_sql(
        "SELECT run_date, event_id, athlete_name, classification, place_rank "
        "FROM parkrun.v_head_to_head")


def _with_names(h2h: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """Swap athlete names everywhere they appear, classification included —
    before the streaks are computed, so every view sees the long names."""
    def cls(c: str) -> str:
        return " vs ".join(mapping.get(p, p) for p in c.split(" vs "))
    return h2h.assign(athlete_name=h2h["athlete_name"].map(lambda n: mapping.get(n, n)),
                      classification=h2h["classification"].map(cls))


def _today() -> dt.date:
    return dt.datetime.now(ZoneInfo("Europe/London")).date()


def _short(n: int, won: bool) -> str:
    return f"{n}{'W' if won else 'L'}"


def _long(n: int, won: bool) -> str:
    noun = ("win" if n == 1 else "wins") if won else ("loss" if n == 1 else "losses")
    return f"{n} {noun}"


def _best_text(r) -> str:
    if r["best_n"] == 0:
        return "no wins yet"
    if r["best_since"] == r["best_until"]:
        return f"{_long(r['best_n'], True)} on {uk_date(r['best_since'])}"
    return (f"{_long(r['best_n'], True)} between {uk_date(r['best_since'])} "
            f"and {uk_date(r['best_until'])}")


def _rows(streaks: pd.DataFrame) -> list[tuple[str, str, pd.Series]]:
    """(athlete, scope, row) in display order: athletes A–Z, then each
    athlete's scopes ANY → one-on-ones → three-way."""
    out = []
    for name in sorted(streaks["athlete_name"].unique()):
        mine = streaks[streaks["athlete_name"] == name].set_index("scope")
        for scope in scope_order(name, mine.index):
            out.append((name, scope, mine.loc[scope]))
    return out


# --------------------------------------------------------------------------- #
# Shared CSS — every colour that is text inherits the theme
# --------------------------------------------------------------------------- #
CSS = f"""
<style>
.sp-grid {{ display:grid; gap:12px;
           grid-template-columns:repeat(auto-fit, minmax(270px, 1fr)); }}
.sp-card {{ border:1px solid rgba(128,128,128,.35); border-radius:10px;
           padding:12px 14px; min-width:0; }}
.sp-name {{ font-weight:700; font-size:1.05rem; margin-bottom:6px;
           overflow-wrap:anywhere; }}
.sp-row  {{ padding:8px 0; border-top:1px solid rgba(128,128,128,.2); }}
.sp-cat  {{ font-size:.8rem; opacity:.75; overflow-wrap:anywhere; }}
.sp-line {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap;
           margin:2px 0; }}
.sp-badge {{ display:inline-block; min-width:1.6em; text-align:center;
            font-weight:700; color:#fff; border-radius:5px; padding:1px 6px;
            font-size:.85rem; }}
.sp-w {{ background:{WIN}; }}
.sp-l {{ background:{LOSS}; }}
.sp-big  {{ font-weight:600; font-size:1.05rem; }}
.sp-note {{ font-size:.8rem; opacity:.75; }}
.sp-live {{ font-size:.68rem; font-weight:700; letter-spacing:.06em;
           border:1px solid {WIN}; color:inherit; border-radius:4px;
           padding:0 5px; }}
.sp-hm-wrap {{ overflow-x:auto; }}
.sp-hm {{ border-collapse:separate; border-spacing:4px; width:100%;
         min-width:560px; table-layout:fixed; }}
.sp-hm th {{ font-size:.8rem; font-weight:600; text-align:left; opacity:.8;
            padding:2px 6px; overflow-wrap:break-word; hyphens:auto;
            vertical-align:bottom; }}
.sp-hm tr > th:first-child {{ vertical-align:middle; font-size:.95rem; width:15%;
            opacity:1; }}
.sp-hm td {{ border-radius:6px; padding:8px; vertical-align:top;
            overflow-wrap:anywhere; }}
.sp-hm .sp-self {{ background:repeating-linear-gradient(45deg,
            rgba(128,128,128,.12) 0 6px, transparent 6px 12px); }}
.sp-hm-val {{ font-weight:700; font-size:1.2rem; }}
</style>
"""


# --------------------------------------------------------------------------- #
# 1 — Scorecard grid
# --------------------------------------------------------------------------- #
def view_scorecards(streaks: pd.DataFrame) -> None:
    cards = []
    names = sorted(streaks["athlete_name"].unique())
    rows = _rows(streaks)
    for name in names:
        body = []
        for _, scope, r in [x for x in rows if x[0] == name]:
            won = r["won"]
            badge = (f"<span class='sp-badge {'sp-w' if won else 'sp-l'}'>"
                     f"{'W' if won else 'L'}</span>")
            live = " <span class='sp-live'>LIVE</span>" if r["live"] else ""
            label = opponents_label(name, scope)
            body.append(
                f"<div class='sp-row'>"
                f"<div class='sp-cat' title='{escape(scope)}'>{escape(label)}</div>"
                f"<div class='sp-line'>{badge}"
                f"<span class='sp-big'>{_long(r['n'], won)}</span>"
                f"<span class='sp-note'>since {uk_date(r['since'])}</span></div>"
                f"<div class='sp-note'>Best: {_best_text(r)}{live}</div>"
                f"</div>")
        cards.append(f"<div class='sp-card'><div class='sp-name'>{escape(name)}</div>"
                     + "".join(body) + "</div>")
    st.html(CSS + "<div class='sp-grid'>" + "".join(cards) + "</div>")


# --------------------------------------------------------------------------- #
# 2 — Diverging bars
# --------------------------------------------------------------------------- #
def _row_labels(rows) -> list[str]:
    return [f"{name} · {opponents_label(name, scope)}" for name, scope, _ in rows]


# A plotly axis label has no wrapping and no ellipsis: a long one widens the
# left margin until it eats the plot. Shorten each name to this many
# characters on the axis; the hover keeps the full label.
TICK_NAME_CHARS = 14


def _tick_text(rows) -> list[str]:
    def clip(text: str) -> str:
        return text if len(text) <= TICK_NAME_CHARS else text[:TICK_NAME_CHARS - 1] + "…"
    out = []
    for name, scope, _ in rows:
        opp = opponents_label(name, scope)
        if opp != ANY:
            opp = "vs " + " & ".join(clip(o) for o in opp[3:].split(" & "))
        out.append(f"{clip(name)} · {opp}")
    return out


def view_diverging(streaks: pd.DataFrame, key: str) -> None:
    rows = _rows(streaks)
    labels = _row_labels(rows)
    signed = [r["n"] if r["won"] else -r["n"] for _, _, r in rows]
    best = [r["best_n"] for _, _, r in rows]
    reach = max([abs(v) for v in signed] + best) + 1.8

    fig = go.Figure()
    # The grey track: how far right the best-ever win streak reached.
    fig.add_bar(y=labels, x=best, orientation="h", marker_color=TRACK, width=0.78,
                hovertemplate="%{y}<br>best ever: %{x} wins<extra></extra>",
                name="Best ever (track)")
    fig.add_bar(
        y=labels, x=signed, orientation="h", width=0.46,
        marker_color=[WIN if v > 0 else LOSS for v in signed],
        text=[_short(abs(v), v > 0) + ("  LIVE" if r["live"] else "")
              for v, (_, _, r) in zip(signed, rows)],
        textposition="outside", cliponaxis=False,
        customdata=[[f"since {uk_date(r['since'])}", _best_text(r)] for _, _, r in rows],
        hovertemplate="%{y}<br>%{text} %{customdata[0]}<br>best: %{customdata[1]}"
                      "<extra></extra>",
        name="Current streak")
    # The tick: the best-ever mark itself, so it reads even where the current
    # bar covers the track.
    fig.add_scatter(y=labels, x=[b if b else None for b in best], mode="markers",
                    marker=dict(symbol="line-ns", size=26,
                                line=dict(width=3, color=MUTED)),
                    hoverinfo="skip", name="Best ever (tick)")
    # A rule between athletes, so the twelve rows read as three groups.
    for i in range(1, len(rows)):
        if rows[i][0] != rows[i - 1][0]:
            fig.add_hline(y=i - 0.5, line=dict(color="rgba(128,128,128,.35)", width=1))
    fig.add_vline(x=0, line=dict(color=MUTED, width=1.5))
    fig.update_layout(
        barmode="overlay", height=60 + 38 * len(rows), showlegend=False,
        margin=dict(t=30, b=30, l=0, r=10),
        xaxis=dict(range=[-reach, reach], zeroline=False, tickformat="d",
                   title="← losses in a row  ·  wins in a row →"),
        yaxis=dict(autorange="reversed", categoryorder="array",
                   categoryarray=labels, tickvals=labels, ticktext=_tick_text(rows)),
    )
    show_chart(fig, key=f"div-{key}")
    st.caption("Grey track and tick: the best-ever win streak. Bars: the current "
               "streak, right for wins, left for losses.")


# --------------------------------------------------------------------------- #
# 3 — Streak timeline
# --------------------------------------------------------------------------- #
def view_timeline(streaks: pd.DataFrame, key: str) -> None:
    rows = _rows(streaks)
    labels = _row_labels(rows)
    today = _today()
    fig = go.Figure()
    for label, (_, _, r) in zip(labels, rows):
        if r["best_n"]:
            fig.add_scatter(
                x=[r["best_since"], r["best_until"]], y=[label, label],
                mode="lines+markers",
                line=dict(color=WIN, width=6, dash="dash"),
                marker=dict(size=7, color=WIN, symbol="line-ns-open",
                            line=dict(width=2, color=WIN)),
                hovertemplate=f"{escape(label)}<br>best: {_best_text(r)}<extra></extra>",
                showlegend=False)
        colour = WIN if r["won"] else LOSS
        fig.add_scatter(
            x=[r["since"], pd.Timestamp(today)], y=[label, label], mode="lines",
            line=dict(color=colour, width=12),
            hovertemplate=(f"{escape(label)}<br>{_long(r['n'], r['won'])} since "
                           f"{uk_date(r['since'])}<extra></extra>"),
            showlegend=False)
        fig.add_scatter(
            x=[pd.Timestamp(today)], y=[label], mode="text",
            text=[" " + _short(r["n"], r["won"]) + (" LIVE" if r["live"] else "")],
            textposition="middle right", hoverinfo="skip", showlegend=False)
    for i in range(1, len(rows)):
        if rows[i][0] != rows[i - 1][0]:
            fig.add_hline(y=i - 0.5, line=dict(color="rgba(128,128,128,.35)", width=1))
    fig.add_vline(x=pd.Timestamp(today), line=dict(color=MUTED, width=1, dash="dot"))
    first = min([r["best_since"] for _, _, r in rows if r["best_n"]]
                + [r["since"] for _, _, r in rows])
    fig.update_layout(
        height=60 + 38 * len(rows), margin=dict(t=30, b=30, l=0, r=10),
        xaxis=dict(range=[pd.Timestamp(first) - pd.Timedelta(days=60),
                          pd.Timestamp(today) + pd.Timedelta(days=200)],
                   tickformat="%Y"),
        yaxis=dict(autorange="reversed", categoryorder="array", categoryarray=labels,
                   tickvals=labels, ticktext=_tick_text(rows)),
    )
    show_chart(fig, key=f"tl-{key}")
    st.caption(f"Dashed: the best-ever win streak's span. Solid: the current streak, "
               f"from its first result to today ({uk_date(today)}), green for wins, "
               f"red for losses.")


# --------------------------------------------------------------------------- #
# 4 — Heatmap matrix
# --------------------------------------------------------------------------- #
def view_heatmap(streaks: pd.DataFrame) -> None:
    """Competitors × opponents. Columns are fixed — Any, vs each athlete, the
    three-way — so every row has the same columns and its own cell is hatched."""
    names = sorted(streaks["athlete_name"].unique())
    columns = [ANY] + [f"vs {n}" for n in names] + ["Three-way"]
    top = max(int(streaks["n"].max()), 1)

    def column_of(name: str, scope: str) -> str:
        if scope == ANY:
            return ANY
        if scope.count(" vs ") >= 2:
            return "Three-way"
        return opponents_label(name, scope)

    head = "<tr><th></th>" + "".join(f"<th>{escape(c)}</th>" for c in columns) + "</tr>"
    body = []
    for name in names:
        mine = streaks[streaks["athlete_name"] == name]
        by_col = {column_of(name, s): r for s, r in zip(mine["scope"], mine.to_dict("records"))}
        cells = [f"<th>{escape(name)}</th>"]
        for c in columns:
            r = by_col.get(c)
            if c == f"vs {name}":
                cells.append("<td class='sp-self' title='themselves'></td>")
                continue
            if r is None:
                cells.append("<td><span class='sp-note'>no head-to-heads</span></td>")
                continue
            alpha = 0.14 + 0.46 * (r["n"] / top)
            rgb = "26,127,55" if r["won"] else "192,57,43"
            best = "best —" if r["best_n"] == 0 else f"best {r['best_n']}W"
            live = " <span class='sp-live'>LIVE</span>" if r["live"] else ""
            cells.append(
                f"<td style='background:rgba({rgb},{alpha:.2f})' "
                f"title='{escape(name)} {escape(c)}: {_long(r['n'], r['won'])} since "
                f"{uk_date(r['since'])}. Best: {escape(_best_text(r))}'>"
                f"<div class='sp-hm-val'>{_short(r['n'], r['won'])}</div>"
                f"<div class='sp-note'>since {uk_date(r['since'])}</div>"
                f"<div class='sp-note'>{best}{live}</div></td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    st.html(CSS + "<div class='sp-hm-wrap'><table class='sp-hm'>" + head
            + "".join(body) + "</table></div>")
    st.caption(f"Deeper colour, longer streak (darkest = {top}). Green wins, red "
               "losses — and the cell says which.")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
st.title("Win/loss streaks — design bench")
st.caption(f"Data: `{DB_PATH}`. A win is 1st place; anything else is a loss; a "
           "dead heat for 1st is a win for both. Dev only — not part of the app.")

long_names = st.toggle("Stress-test long names", value=False)
h2h = load_h2h(data_version())
if long_names:
    h2h = _with_names(h2h, LONG_NAMES)
streaks = win_streaks(h2h)

with st.expander("Computed streaks — the one table every view draws from"):
    table = pd.DataFrame([{
        "Athlete": name, "Against": opponents_label(name, scope),
        "Current": f"{_long(r['n'], r['won'])} since {uk_date(r['since'])}",
        "Best ever": _best_text(r), "Live": "yes" if r["live"] else "",
    } for name, scope, r in _rows(streaks)])
    st.dataframe(table, hide_index=True, width="stretch")

t1, t2, t3, t4, t_all = st.tabs(
    ["1 · Scorecards", "2 · Diverging bars", "3 · Timeline", "4 · Heatmap",
     "All four"])
with t1:
    view_scorecards(streaks)
with t2:
    view_diverging(streaks, "solo")
with t3:
    view_timeline(streaks, "solo")
with t4:
    view_heatmap(streaks)
with t_all:
    left, right = st.columns(2)
    with left:
        st.subheader("1 · Scorecards")
        view_scorecards(streaks)
        st.subheader("3 · Timeline")
        view_timeline(streaks, "all")
    with right:
        st.subheader("2 · Diverging bars")
        view_diverging(streaks, "all")
        st.subheader("4 · Heatmap")
        view_heatmap(streaks)

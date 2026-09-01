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
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

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
    return str(Path(__file__).resolve().parent / "data" / "parkrun_snapshot.duckdb")


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




def fmt_time(sec) -> str:
    if pd.isna(sec):
        return "—"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


UK_TZ = ZoneInfo("Europe/London")




# --------------------------------------------------------------------------- #
# Buggy mode — display helpers
# --------------------------------------------------------------------------- #
# Shown only for athletes who actually have a buggy run in the loaded data, so
# an athlete who has never pushed one sees an unchanged UI. Computed once per
# render from v_results_moded (see `BUGGY_ATHLETES`).
BUGGY_GLYPH = "🍼"


def mode_badge(is_buggy, source: str | None = None) -> str:
    """HTML glyph for a run's mode. NEVER emoji-alone: the glyph always carries
    a title, because an unexplained icon is worse than no icon."""
    if not is_buggy:
        return ""
    what = "with buggy"
    if source == "estimated":
        what += ", estimated"
    return (f"<span title='{escape(what)}' "
            f"style='cursor:help'>{BUGGY_GLYPH}</span>")


def mode_suffix(is_buggy, source: str | None = None) -> str:
    """Plain-text equivalent for st.table / st.dataframe, which strip HTML.

    An estimated label must read differently from a confirmed one — given how
    poorly a per-run rule separates a buggy from a hard course, a guess has to
    be visibly a guess.
    """
    if not is_buggy:
        return ""
    return " (with buggy, estimated)" if source == "estimated" else " (with buggy)"


def mode_text(is_buggy, source: str | None = None) -> str:
    """Value for a real `Mode` column: always says something, never blank."""
    if not is_buggy:
        return "—"
    return "With buggy (est.)" if source == "estimated" else "With buggy"




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


BASIS_LABEL = {"buggy": "With buggy", "nonbuggy": "Without buggy"}
BASIS_HOVER = {
    "nonbuggy+handicap": "without-buggy form + handicap",
    "buggy-handicap": "with-buggy form ÷ handicap",
    "buggy": "with-buggy form",
    "nonbuggy": "without-buggy form",
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
                          "target is their without-buggy form **+ the buggy "
                          "handicap**"),
    "buggy-handicap": ("had no without-buggy runs in the 91-day window, so "
                       "their target is their with-buggy form **÷ the buggy "
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



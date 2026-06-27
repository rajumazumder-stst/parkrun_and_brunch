"""parkrun & brunch — comparison app for George, Duncan and Raju.

Reads the `parkrun` schema (read-only) from the local DuckDB and presents:
  Tab 1  intro + participation overlap (Venn) + per-athlete company
  Tab 2  head-to-head summary (current targets, latest result, leaderboard)
  Tab 3  head-to-head detail (drill into a single contest)

Run:  streamlit run app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px
import streamlit as st
from matplotlib_venn import venn3

def _resolve_db_path() -> str:
    """Locate the DuckDB to read, in priority order:

    1. ``PARKRUN_DB`` env var (local dev against the full personal DB, or a
       MotherDuck connection string later).
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


DB_PATH = _resolve_db_path()

# Fixed per-athlete colours, used consistently everywhere (Dark2 palette).
ATHLETE_COLORS = {"George": "#1b9e77", "Raju": "#d95f02", "Duncan": "#7570b3"}
PLACE_COLORS = {"1st": "#FFB300", "2nd": "#B0B0B0", "3rd": "#C77B30"}
PLACE_LABEL = {1: "🥇 1st", 2: "🥈 2nd", 3: "🥉 3rd"}
SEASONS = ["Spring", "Summer", "Autumn", "Winter"]

st.set_page_config(page_title="parkrun & brunch", page_icon="🏃", layout="wide")


# --------------------------------------------------------------------------- #
# Data access (read-only; cached so the DB lock is held only briefly)
# --------------------------------------------------------------------------- #
def _read_sql(sql: str) -> pd.DataFrame:
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        return con.execute(sql).fetchdf()
    finally:
        con.close()


@st.cache_data(show_spinner=False)
def load_overlap() -> pd.DataFrame:
    return _read_sql("SELECT * FROM parkrun.v_overlap")


@st.cache_data(show_spinner=False)
def load_h2h() -> pd.DataFrame:
    df = _read_sql("SELECT * FROM parkrun.v_head_to_head")
    df["run_date"] = pd.to_datetime(df["run_date"])
    df["year"] = df["run_date"].dt.year
    df["season"] = df["run_date"].dt.month.map(_season_of_month)
    return df


@st.cache_data(show_spinner=False)
def load_targets() -> pd.DataFrame:
    return _read_sql(
        """
        SELECT a.athlete_name, t.target_seconds, t.n_window, t.refresh_date
        FROM parkrun.current_targets t
        JOIN parkrun.athletes a USING (athlete_id)
        WHERE t.refresh_date = (SELECT max(refresh_date) FROM parkrun.current_targets)
        """
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _season_of_month(m: int) -> str:
    if 3 <= m <= 5:
        return "Spring"
    if 6 <= m <= 8:
        return "Summer"
    if 9 <= m <= 11:
        return "Autumn"
    return "Winter"


def fmt_time(sec) -> str:
    if pd.isna(sec):
        return "—"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render_occasion(rows: pd.DataFrame) -> None:
    """Render the detail block for a single head-to-head occasion."""
    first = rows.iloc[0]
    date_str = pd.to_datetime(first["run_date"]).strftime("%A %d %B %Y")
    st.markdown(f"#### {first['short_name']} — {date_str}")
    st.caption(f"Classification: **{first['classification']}**")
    disp = (
        rows.sort_values("place_rank")
        .assign(
            Place=lambda d: d["place_rank"].map(PLACE_LABEL),
            Target=lambda d: d["target_seconds"].map(fmt_time),
            Actual=lambda d: d["actual_seconds"].map(fmt_time),
            **{"% vs form": lambda d: d["pct_diff"].map(lambda v: f"{v:+.2f}%")},
        )
        .rename(columns={"athlete_name": "Athlete"})
    )[["Place", "Athlete", "Target", "Actual", "% vs form"]]
    st.table(disp.set_index("Place"))


def apply_filters(df: pd.DataFrame, cls: str = "All", yr: str = "All",
                  se: str = "All") -> pd.DataFrame:
    if cls != "All":
        df = df[df["classification"] == cls]
    if yr != "All":
        df = df[df["year"] == int(yr)]
    if se != "All":
        df = df[df["season"] == se]
    return df


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
try:
    overlap = load_overlap()
    h2h = load_h2h()
    targets = load_targets()
except duckdb.IOException:
    st.error(
        "Couldn't open the database (is DBeaver or a refresh holding the lock?). "
        "Close other connections and reload."
    )
    st.stop()

CLASS_OPTS = ["All"] + sorted(h2h["classification"].unique())
YEAR_OPTS = ["All"] + [str(y) for y in sorted(h2h["year"].unique())]
SEASON_OPTS = ["All"] + SEASONS

with st.sidebar:
    st.markdown("### 🏃 parkrun & brunch")
    if not targets.empty:
        st.caption(f"Data as of {targets['refresh_date'].max():%d %b %Y}")
    if st.button("🔄 Reload data"):
        st.cache_data.clear()
        st.rerun()

tab1, tab2, tab3 = st.tabs(
    ["🏃 parkrun & brunch", "⚔️ Head-to-head summary", "🔎 Head-to-head detail"]
)

# =========================================================================== #
# TAB 1 — intro + overlap
# =========================================================================== #
with tab1:
    st.title("🏃 parkrun & brunch ☕")
    st.subheader("George, Duncan & Raju")
    st.markdown(
        """
Every Saturday morning, three friends — **George**, **Duncan** and **Raju** —
lace up for a **parkrun**: a free, timed 5k. Some weeks they line up together;
other weeks they're scattered across the country chasing new venues. The one
constant? **Brunch afterwards.** ☕🥐

This app maps where their parkruns overlap, and turns every shared start line
into a friendly, *form-adjusted* head-to-head.
        """
    )

    st.divider()
    st.header("Where do they run together?")
    st.caption(
        "Each count is a shared *occasion* — the same event on the same day. "
        "Regions are exclusive (the centre is all three together)."
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
    with st.expander("How does a head-to-head work?"):
        st.markdown(
            """
A **head-to-head** is any occasion where two or more of them ran the same event
on the same day. Because they run at very different paces, we don't compare raw
finish times — we compare **performance against recent form**:

1. Each runner's **target** is the *median* of their times over the **91 days
   before** the event (needs at least one run in that window).
2. We take the **% difference** between their actual time and that target.
3. Whoever beat their own form by the most comes **1st** (ties share a place).

A 3-way where someone has no recent form becomes a 2-way between the other two.
            """
        )

    st.subheader("If they raced today, current-form targets would be…")
    if targets.empty:
        st.info("No current targets yet — run a refresh.")
    else:
        cols = st.columns(len(targets))
        for col, (_, row) in zip(cols, targets.sort_values("target_seconds").iterrows()):
            col.metric(
                row["athlete_name"],
                fmt_time(row["target_seconds"]) if row["n_window"] else "—",
                f"{int(row['n_window'])} runs in window",
            )

    st.divider()
    st.subheader("Latest head-to-head")
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
    st.subheader("Head-to-head record")
    st.caption(
        f"Classification: **{pick}** (set above) · filter by year and season below."
    )
    fc1, fc2 = st.columns(2)
    yr = fc1.selectbox("Year", YEAR_OPTS, key="t2_year")
    se = fc2.selectbox("Season", SEASON_OPTS, key="t2_season")
    summ = apply_filters(h2h, cls=pick, yr=yr, se=se)

    if summ.empty:
        st.info("No head-to-heads for that year/season.")
    else:
        board = (
            summ.assign(place=summ["place_rank"].clip(upper=3))
            .pivot_table(index="athlete_name", columns="place", values="event_id",
                         aggfunc="count", fill_value=0)
            .rename(columns={1: "1st", 2: "2nd", 3: "3rd"})
        )
        for c in ("1st", "2nd", "3rd"):
            if c not in board.columns:
                board[c] = 0
        board = board[["1st", "2nd", "3rd"]].sort_values("1st", ascending=False)

        tidy = board.reset_index().melt(
            id_vars="athlete_name", var_name="Place", value_name="count"
        )
        fig3 = px.bar(
            tidy, x="athlete_name", y="count", color="Place", barmode="group",
            color_discrete_map=PLACE_COLORS,
            category_orders={"Place": ["1st", "2nd", "3rd"],
                             "athlete_name": list(board.index)},
            text="count",
        )
        fig3.update_layout(xaxis_title=None, yaxis_title="head-to-heads",
                           legend_title=None, margin=dict(t=10, b=0, l=0, r=0))
        st.plotly_chart(fig3, width="stretch")
        st.dataframe(board, width="stretch")

# =========================================================================== #
# TAB 3 — head-to-head detail
# =========================================================================== #
with tab3:
    st.header("🔎 Head-to-head detail")
    d1, d2, d3 = st.columns(3)
    pick3 = d1.selectbox("Classification", CLASS_OPTS, key="t3_class")
    yr3 = d2.selectbox("Year", YEAR_OPTS, key="t3_year")
    se3 = d3.selectbox("Season", SEASON_OPTS, key="t3_season")
    pool = apply_filters(h2h, pick3, yr3, se3)

    if pool.empty:
        st.info("No head-to-heads match those filters.")
    else:
        occasions = (
            pool[["run_date", "event_id", "short_name", "classification"]]
            .drop_duplicates()
            .sort_values("run_date", ascending=False)
        )
        labels = {
            f"{r.run_date:%Y-%m-%d} — {r.short_name} ({r.classification})":
            (r.run_date, r.event_id)
            for r in occasions.itertuples()
        }
        choice = st.selectbox(f"Head-to-head ({len(labels)} found)", list(labels))
        sel_date, sel_event = labels[choice]
        occ = pool[(pool["run_date"] == sel_date) & (pool["event_id"] == sel_event)]
        st.divider()
        render_occasion(occ)

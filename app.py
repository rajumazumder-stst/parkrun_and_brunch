"""parkrun & brunch — comparison app for George, Duncan and Raju.

Reads the `parkrun` schema (read-only) from the local DuckDB and presents:
  Tab 1  intro + participation overlap (Venn) + per-athlete company
  Tab 2  head-to-head summary (targets, latest result, record, cumulative 1sts)
  Tab 3  head-to-head detail (drill into a single contest)
  Tab 4  form — target time by Saturday
  Tab 5  map — where the head-to-heads happen

Run:  streamlit run app.py
"""

from __future__ import annotations

import math
import os
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

st.set_page_config(page_title="parkrun & brunch", page_icon="🏃", layout="wide")


# --------------------------------------------------------------------------- #
# Data access (read-only; cached so the DB lock is held only briefly)
# --------------------------------------------------------------------------- #
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
    """Cheap change-detector: the latest scrape timestamp, re-checked at most
    once a minute. Passed as a *hashed* cache-key arg into the heavy loaders
    below, so they auto-refetch exactly when a refresh writes new data and serve
    cache otherwise (an out-of-band pipeline refresh updates the backend; this is
    how the running app notices without a manual reload). Must NOT start with an
    underscore — Streamlit skips underscore-prefixed args when hashing the key."""
    df = _read_sql("SELECT max(scrape_timestamp) AS v FROM parkrun.results")
    return str(df["v"].iloc[0])


@st.cache_data(show_spinner=False)
def load_overlap(version) -> pd.DataFrame:
    return _read_sql("SELECT * FROM parkrun.v_overlap")


@st.cache_data(show_spinner=False)
def load_h2h(version) -> pd.DataFrame:
    df = _read_sql("SELECT * FROM parkrun.v_head_to_head")
    df["run_date"] = pd.to_datetime(df["run_date"])
    df["year"] = df["run_date"].dt.year
    df["season_label"] = df["run_date"].map(_season_label)
    return df


@st.cache_data(show_spinner=False)
def load_targets(version) -> pd.DataFrame:
    return _read_sql(
        """
        SELECT a.athlete_name, t.target_seconds, t.n_window, t.refresh_date
        FROM parkrun.current_targets t
        JOIN parkrun.athletes a USING (athlete_id)
        WHERE t.refresh_date = (SELECT max(refresh_date) FROM parkrun.current_targets)
        """
    )


@st.cache_data(show_spinner=False)
def load_saturday_targets(version) -> pd.DataFrame:
    df = _read_sql("SELECT * FROM parkrun.v_saturday_targets")
    df["run_date"] = pd.to_datetime(df["run_date"])
    df["year"] = df["run_date"].dt.year
    df["season_label"] = df["run_date"].map(_season_label)
    return df


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


def fmt_time(sec) -> str:
    if pd.isna(sec):
        return "—"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _gap_filled_saturdays(sat: pd.DataFrame) -> pd.DataFrame:
    """Reindex each athlete's target series onto every Saturday between *their
    own* first and last target — inserting NaN where a Saturday has no target so
    the line *breaks* across a >91-day inactivity gap rather than bridging it.

    Reindexing per athlete (not the global span) means each trace's x-extent is
    only where that athlete actually has data — no leading/trailing NaN padding —
    so hiding one athlete via the legend lets both axes rescale to those shown."""
    out = []
    for name, g in sat.groupby("athlete_name"):
        g = g.sort_values("run_date")
        sats = pd.date_range(g["run_date"].min(), g["run_date"].max(), freq="W-SAT")
        s = (g.set_index("run_date")[["target_seconds", "n_window"]]
               .reindex(sats))
        s.insert(0, "athlete_name", name)
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
    wins = (mh[mh["place_rank"] == 1]
            .groupby(["event_id", "athlete_name"]).size()
            .unstack(fill_value=0))
    c = coords.set_index("event_id")

    venues = []
    for event_id, count in n_h2h.items():
        if event_id not in c.index:
            continue
        lat, lon = float(c.at[event_id, "latitude"]), float(c.at[event_id, "longitude"])
        wdict = wins.loc[event_id].to_dict() if event_id in wins.index else {}
        wdict = {k: int(v) for k, v in wdict.items() if v > 0}
        d = int(round(14 + 5 * math.sqrt(count)))
        breakdown = " · ".join(f"{k} {v}" for k, v in
                               sorted(wdict.items(), key=lambda x: -x[1]))
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
        df = df[df["season_label"] == se]
    return df


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
except duckdb.IOException:
    st.error(
        "Couldn't open the database (is DBeaver or a refresh holding the lock?). "
        "Close other connections and reload."
    )
    st.stop()

CLASS_OPTS = ["All"] + sorted(h2h["classification"].unique())

with st.sidebar:
    st.markdown("### 🏃 parkrun & brunch")
    if not targets.empty:
        st.caption(f"Data as of {targets['refresh_date'].max():%d %b %Y}")
    if st.button("🔄 Reload data"):
        st.cache_data.clear()
        st.rerun()

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["🏃 parkrun & brunch", "⚔️ Head-to-head summary", "🔎 Head-to-head detail",
     "📈 Form (target time)", "🗺️ Where they meet"]
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
        f"Classification: **{pick}** (set above) · filter by year *or* season below."
    )
    fc1, fc2 = st.columns(2)
    yr, se = year_season_filters(apply_filters(h2h, cls=pick), "t2", fc1, fc2)
    summ = apply_filters(h2h, cls=pick, yr=yr, se=se)

    if summ.empty:
        st.info("No head-to-heads for that year/season.")
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
        )
        for c in places:
            if c not in board.columns:
                board[c] = 0
        board = board[places].sort_values("1st", ascending=False)

        tidy = board.reset_index().melt(
            id_vars="athlete_name", var_name="Place", value_name="count"
        )
        fig3 = px.bar(
            tidy, x="athlete_name", y="count", color="Place", barmode="group",
            color_discrete_map=PLACE_COLORS,
            category_orders={"Place": places,
                             "athlete_name": list(board.index)},
            text="count",
        )
        fig3.update_layout(xaxis_title=None, yaxis_title="head-to-heads",
                           legend_title=None, margin=dict(t=10, b=0, l=0, r=0))
        st.plotly_chart(fig3, width="stretch")
        st.dataframe(board, width="stretch")

        # ----- cumulative 1st-place finishes over the selected period ----- #
        st.divider()
        st.subheader("Cumulative 1st-place finishes")
        if pick == "All":
            st.info(
                "Select a **head-to-head** (classification, above) to see its "
                "cumulative 1st-place trend. It defaults to the entire date range — "
                "filter by **year** or **season** to narrow it."
            )
        else:
            period = yr if yr != "All" else (se if se != "All" else "the entire date range")
            st.caption(
                f"Running total of form-adjusted 1sts for **{pick}** over **{period}** — "
                "ties for 1st each count. Filter by year or season above to narrow the range."
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
                st.info(f"No 1st-place finishes for **{pick}** in the selected period.")

# =========================================================================== #
# TAB 3 — head-to-head detail
# =========================================================================== #
with tab3:
    st.header("🔎 Head-to-head detail")
    d1, d2, d3 = st.columns(3)
    pick3 = d1.selectbox("Head-to-head classification", CLASS_OPTS, key="t3_class")
    yr3, se3 = year_season_filters(apply_filters(h2h, cls=pick3), "t3", d2, d3)
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

# =========================================================================== #
# TAB 4 — form (target time by Saturday)
# =========================================================================== #
with tab4:
    st.header("📈 Form — target time by Saturday")
    st.caption(
        "Each athlete's current-form **target** on every Saturday — the median of "
        "their times over the **91 days before** that Saturday (min 1 run in the "
        "window; the same target used for head-to-heads). Lower is faster. A broken "
        "line marks Saturdays with no runs in the preceding 91 days."
    )
    sat = load_saturday_targets(_ver)
    if sat.empty:
        st.info("No Saturday targets available.")
    else:
        fc1, fc2 = st.columns(2)
        yr, se = year_season_filters(sat, "t4", fc1, fc2)
        st.caption(
            "Filter by year *or* season · click a name in the legend to hide an "
            "athlete — the axes rescale to those still shown."
        )
        sat_f = apply_filters(sat, cls="All", yr=yr, se=se)

        if sat_f.empty:
            st.info("No targets for that year or season.")
        else:
            plot_df = _gap_filled_saturdays(sat_f)
            fig = px.line(
                plot_df, x="run_date", y="target_seconds", color="athlete_name",
                color_discrete_map=ATHLETE_COLORS,
                category_orders={"athlete_name": sorted(sat_f["athlete_name"].unique())},
                custom_data=["target_fmt", "n_window"],
                labels={"run_date": "", "target_seconds": "target time",
                        "athlete_name": ""},
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
        "Every venue where two or more of them have gone head-to-head. Each pie is "
        "sized by the number of head-to-heads there and split by who won "
        "(form-adjusted 1sts), in their colours. Hover a venue for the breakdown."
    )
    d1, d2, d3 = st.columns(3)
    pick5 = d1.selectbox("Head-to-head classification", CLASS_OPTS, key="t5_class")
    yr5, se5 = year_season_filters(apply_filters(h2h, cls=pick5), "t5", d2, d3)

    if pick5 == "All":
        st.info(
            "Select a **head-to-head classification** above to show the map."
        )
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

"""Label impact — what the buggy labels did, and what they measure.

A SEPARATE app from `app.py`, served on its own port, because it is a
development instrument rather than part of the product: it exists to answer
"what did labelling these runs actually change?", and it is meaningless to a
visitor.

    PARKRUN_LABEL_AUDIT=1 ./scripts/run_local.sh
    # or directly:
    PARKRUN_DB=data/parkrun_dev.duckdb streamlit run label_impact.py --server.port 8502

Two tabs:

**Head-to-head impact** compares the live views against `v_head_to_head_legacy`
— the frozen pre-buggy method, a single pooled 91-day median with no mode split
and no handicap bridge, created by `parkrun_pipeline.ensure_legacy_views` (dev
DBs only, never the source of truth or the deploy snapshot).

**With no labels written every occasion must read `Unchanged`.** That is the
zero-label equivalence check as a live page: the new views have to reproduce the
old ones exactly until a label says otherwise.

**Buggy handicap** is the working behind the `buggy_handicap` table: per
athlete, the runs between their first and last buggy run, split by mode, with
the raw and course-controlled estimates of what the buggy costs. Recomputed
from the labels on every load — nothing on that tab is a stored figure.

Needs scipy, which the dev venv has and `requirements.txt` deliberately does
not.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from scipy import stats as sps

from parkrun_ui import (
    BUGGY_GLYPH,
    DB_PATH,
    _h2h_headline,
    _read_sql,
    _victory_fig,
    _winning_margin,
    fmt_time,
)

st.set_page_config(page_title="Label impact", page_icon="🧪", layout="wide")


def _legacy_views_exist() -> bool:
    """Checked via information_schema rather than try/except on the query, so a
    genuine SQL error in a legacy view still surfaces instead of being read as
    "the views are missing"."""
    df = _read_sql(
        """
        SELECT count(*) AS n FROM information_schema.tables
        WHERE table_schema = 'parkrun'
          AND table_name IN ('v_head_to_head_legacy', 'v_saturday_targets_legacy')
        """
    )
    return int(df["n"].iloc[0]) == 2


st.title("🧪 Label impact")
st.caption(f"Comparing `v_head_to_head` against `v_head_to_head_legacy` — {DB_PATH}")


@st.cache_data(ttl=60, show_spinner=False)
def data_version() -> str:
    df = _read_sql(
        """
        SELECT (SELECT max(scrape_timestamp) FROM parkrun.results) AS scraped,
               (SELECT max(set_at) FROM parkrun.run_modes)         AS labelled,
               (SELECT count(*)    FROM parkrun.run_modes)         AS n_labels
        """
    )
    r = df.iloc[0]
    return f"{r['scraped']}|{r['labelled']}|{r['n_labels']}"


@st.cache_data(show_spinner=False)
def load_new(version) -> pd.DataFrame:
    return _read_sql("SELECT * FROM parkrun.v_head_to_head")


@st.cache_data(show_spinner=False)
def load_legacy(version) -> pd.DataFrame:
    return _read_sql("SELECT * FROM parkrun.v_head_to_head_legacy")


@st.cache_data(show_spinner=False)
def load_label_counts(version) -> pd.DataFrame:
    return _read_sql(
        """
        SELECT a.athlete_name AS "Athlete",
               m.source        AS "Source",
               m.is_buggy      AS "🛒",
               count(*)        AS "Runs"
        FROM parkrun.run_modes m JOIN parkrun.athletes a USING (athlete_id)
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """
    )


_ver = data_version()

# Occasion-level helpers. Module scope, not nested inside the tab: they close
# over nothing, and `_who` reads the winner out of a head-to-head frame, which
# is not a fact about the comparison tab.
def _occasions(df: pd.DataFrame) -> dict:
    """One frame per (event, date) — the unit a head-to-head is fought at."""
    return {k: g for k, g in df.groupby(["event_id", "run_date"])}


def _winners(g) -> frozenset:
    return frozenset(g.loc[g["place_rank"] == 1, "athlete_id"])


def _places(g) -> dict:
    return dict(zip(g["athlete_id"], g["place_rank"]))


def _who(g) -> str:
    if g is None:
        return "—"
    return " & ".join(sorted(g.loc[g["place_rank"] == 1, "athlete_name"]))


# Both tabs read the same database and the same freshness key, so the loaders
# and `_ver` stay at module scope. Everything below them is per-tab: the
# comparison needs the legacy views, the handicap analysis does not, so the
# gate moved inside the tab it actually gates. `return`, never `st.stop()` —
# stopping the script would take the other tab down with it.
def render_impact() -> None:
    if not _legacy_views_exist():
        st.error(
            "The legacy views are missing from this database, so there is nothing "
            "to compare against.\n\n"
            "They are built into a dev DB only:\n"
            "```bash\n"
            "PARKRUN_LABEL_AUDIT=1 python -c \"import duckdb, parkrun_pipeline as p; \\\n"
            "  c=duckdb.connect('data/parkrun_dev.duckdb'); p.ensure_views(c); \\\n"
            "  p.ensure_legacy_views(c)\"\n"
            "```"
        )
        return

    new, legacy = load_new(_ver), load_legacy(_ver)
    for df in (new, legacy):
        df["run_date"] = pd.to_datetime(df["run_date"])
    new_occ, old_occ = _occasions(new), _occasions(legacy)

    rows = []
    for key in sorted(set(new_occ) | set(old_occ), key=lambda k: k[1], reverse=True):
        n, o = new_occ.get(key), old_occ.get(key)
        base = (n if n is not None else o).iloc[0]
        nm, om = (_winning_margin(n) if n is not None else None,
                  _winning_margin(o) if o is not None else None)
        # First match wins. `Lost` must never occur — the handicap bridge can only
        # ADD rankable participants, never remove one (Verification C).
        if o is None:
            verdict = "Gained"
        elif n is None:
            verdict = "Lost"
        elif _winners(n) != _winners(o):
            verdict = "Winner changed"
        elif _places(n) != _places(o):
            verdict = "Places changed"
        elif int(n.iloc[0]["n_ranked"]) != int(o.iloc[0]["n_ranked"]):
            verdict = "Roster changed"
        elif nm is not None and om is not None and abs(nm - om) >= 0.01:
            verdict = "Margin only"
        else:
            verdict = "Unchanged"
        # Which participants got a bridged target (the other mode's form scaled by
        # the athlete's handicap). Named rather than a bare boolean: a bridge is a
        # statement about one runner's history, so "which one" is the whole point.
        bridged = sorted(
            n.loc[n["target_basis"].str.contains("handicap", na=False), "athlete_name"]
        ) if n is not None else []
        rows.append({
            "key": key,
            "Date": key[1],
            "parkrun": base["short_name"],
            "Classification": base["classification"],
            "Old winner": _who(o), "New winner": _who(n),
            "Old margin": om, "New margin": nm,
            "Δ margin": None if (nm is None or om is None) else round(nm - om, 2),
            "Verdict": verdict,
            "Bridged": " & ".join(bridged) if bridged else "—",
        })
    cmp = pd.DataFrame(rows)

    counts = cmp["Verdict"].value_counts()
    st.markdown(
        f"**{int(counts.get('Unchanged', 0))} of {len(cmp)}** head-to-heads "
        f"unchanged."
    )
    ORDER = ["Winner changed", "Places changed", "Roster changed", "Margin only",
             "Gained", "Lost"]
    for col, v in zip(st.columns(len(ORDER)), ORDER):
        col.metric(v, int(counts.get(v, 0)))

    if int(counts.get("Lost", 0)):
        st.error(
            "**Lost occasions exist.** The handicap bridge can only make more "
            "contests rankable, never fewer — this is a Verification C failure, "
            "not an effect of the labels."
        )
    if len(cmp) and int(counts.get("Unchanged", 0)) == len(cmp):
        st.success(
            "Everything unchanged — which is exactly right when no run is labelled "
            "as a buggy run. This is the zero-label equivalence check."
        )

    with st.expander("Labels in this database"):
        st.dataframe(load_label_counts(_ver), hide_index=True, width="stretch")

    VERDICT_COLOR = {
        "Winner changed": "#b3261e", "Places changed": "#a15c00",
        "Roster changed": "#a15c00", "Margin only": "#5b5b5b",
        "Gained": "#1b6e3c", "Lost": "#b3261e", "Unchanged": "#8a8a8a",
    }
    show = cmp.drop(columns=["key"]).copy()
    show["Date"] = pd.to_datetime(show["Date"]).dt.strftime("%Y-%m-%d")
    for c in ("Old margin", "New margin"):
        show[c] = show[c].map(lambda v: "—" if pd.isna(v) else f"{v:.2f}")

    # Two independent filters, ANDed. They answer different questions — "what did
    # the labels move?" and "where did the handicap stand in for missing history?" —
    # and their intersection ("a bridged target that changed the result") is the
    # most interesting cell of all, so they must be combinable rather than exclusive.
    n_bridged = int((cmp["Bridged"] != "—").sum())
    c_changed, c_bridged = st.columns(2)
    only_changed = c_changed.checkbox("Only the ones that changed", value=False)
    only_bridged = c_bridged.checkbox(
        f"Only the ones that used the handicap bridge ({n_bridged})",
        value=False, disabled=n_bridged == 0,
        help="A participant with no runs of that mode in the 91-day window, whose "
             "target was borrowed from the other mode and scaled by their handicap.",
    )
    keep = pd.Series(True, index=cmp.index)
    if only_changed:
        keep &= cmp["Verdict"] != "Unchanged"
    if only_bridged:
        keep &= cmp["Bridged"] != "—"
    table = show[keep]
    st.dataframe(
        table.style
             .map(lambda v: f"color:{VERDICT_COLOR.get(v, '')}", subset=["Verdict"])
             .format({"Δ margin": lambda v: "—" if pd.isna(v) else f"{v:+.2f}"}),
        hide_index=True, width="stretch", height=420,
    )

    st.divider()
    st.subheader("One head-to-head, both methods")

    # Date order, most recent first — the same order the table is in, so picking a
    # row from it and finding it in the dropdown takes no searching.
    # The checkboxes drive this list too: having filtered the table down to what
    # moved, the obvious next act is to open one of them, and a dropdown still
    # offering all 205 would make that a search.
    labels = {
        f"{pd.Timestamp(r['Date']):%Y-%m-%d} · {r['parkrun']} · "
        f"{r['Classification']} — {r['Verdict']}": r["key"]
        for _, r in cmp[keep].iterrows()
    }
    if not labels:
        st.info(
            "No head-to-head matches the filters above, so there is nothing to "
            "compare. Clear a checkbox to widen the list."
            if (only_changed and only_bridged)
            else "Nothing used the handicap bridge, so there is nothing to compare."
            if only_bridged
            else "Nothing changed, so there is nothing to compare."
        )
        return
    chosen = st.selectbox("Head-to-head", list(labels))
    key = labels[chosen]
    n, o = new_occ.get(key), old_occ.get(key)

    # Both panels share ONE x-range, computed here rather than inside _victory_fig:
    # each figure otherwise scales to its own frame, so an unchanged run would
    # appear to move simply because the other panel rescaled.
    pcts = pd.concat([g["pct_diff"] for g in (n, o) if g is not None])
    lo, hi = min(0.0, pcts.min()), max(0.0, pcts.max())
    pad = max(1.0, (hi - lo) * 0.30)
    XR = [hi + pad, lo - pad]        # reversed, as _victory_fig does

    st.markdown("##### Old method — one pooled 91-day median")
    if o is None:
        st.info(
            "Not rankable under the old method: no history in the window, so the "
            "handicap bridge is what made this contest exist at all."
        )
    else:
        st.markdown(_h2h_headline(o))
        f = _victory_fig(o)
        f.update_xaxes(range=XR)
        st.plotly_chart(f, width="stretch", key="old")

    st.markdown("##### New method — per-mode median + handicap bridge")
    if n is None:
        st.error("Absent from the new view — a Verification C failure.")
    else:
        st.markdown(_h2h_headline(n))
        f = _victory_fig(n)
        f.update_xaxes(range=XR)
        st.plotly_chart(f, width="stretch", key="new")

    if n is not None and o is not None:
        om, nm = _winning_margin(o), _winning_margin(n)
        st.caption(
            f"Winner **{_who(o)} → {_who(n)}** · margin "
            f"**{'—' if om is None else f'{om:.2f}'} → "
            f"{'—' if nm is None else f'{nm:.2f}'}** pts · ranked "
            f"**{int(o.iloc[0]['n_ranked'])} → {int(n.iloc[0]['n_ranked'])}**"
        )
        if (list(o.sort_values("place_rank")["athlete_id"])
                != list(n.sort_values("place_rank")["athlete_id"])):
            st.caption(
                "⚠️ The rows sit in a different order between the two panels — the "
                "charts sort by place, so that reordering *is* the finding, not a "
                "rendering glitch."
            )


# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — buggy handicap
#
# `buggy_handicap` is one number per athlete and the head-to-head bridge leans
# on it whenever a mode has no runs in the 91-day window, so it deserves to be
# derived on screen rather than asserted in a commit message. Everything here is
# computed from the labels currently in the database: change a label and this
# tab moves with it.
#
# Needs scipy (Welch CI, gaussian_kde). Dev-only, like the rest of this app —
# deliberately not in requirements.txt.
# ─────────────────────────────────────────────────────────────────────────────
# One pair for both athletes: the series here is the MODE, not the runner, so
# the same two colours must mean the same two things on every chart in the tab.
# Validated as a categorical pair (CVD ΔE 24.7, normal-vision ΔE 33.6).
MODE_COLOR = {"nonbuggy": "#2a78d6", "buggy": "#eb6834"}


def _fill(hex_color: str, alpha: float = 0.13) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"
MODE_LABEL = {"nonbuggy": "Without buggy", "buggy": f"With buggy {BUGGY_GLYPH}"}

# An extreme run is one beyond Q3 + 3·IQR of the whole window. Three IQRs, not
# the usual 1.5, because a parkrun field legitimately contains slow days — only
# a run that is not a race at all (a walk, a buggy pushed round with a toddler)
# should fall out, and at 1.5 the tail of ordinary bad days starts going too.
OUTLIER_K = 3.0


@st.cache_data(show_spinner=False)
def load_moded_runs(version) -> pd.DataFrame:
    return _read_sql(
        """
        SELECT a.athlete_name, r.run_date, e.short_name, r.time, r.time_seconds,
               coalesce(m.is_buggy, FALSE) AS is_buggy, m.source
        FROM parkrun.results r
        JOIN parkrun.athletes a USING (athlete_id)
        JOIN parkrun.events e   USING (event_id)
        LEFT JOIN parkrun.run_modes m
               ON m.athlete_id = r.athlete_id
              AND m.run_date   = r.run_date
              AND m.event_id   = r.event_id
        """
    )


@st.cache_data(show_spinner=False)
def load_handicaps(version) -> pd.DataFrame:
    return _read_sql(
        """
        SELECT a.athlete_name AS "Athlete", b.handicap AS "Handicap",
               b.n_buggy_labels AS "Labels", b.method AS "Method",
               b.computed_at AS "Computed"
        FROM parkrun.buggy_handicap b JOIN parkrun.athletes a USING (athlete_id)
        ORDER BY 1
        """
    )


@st.cache_data(show_spinner=False)
def load_bridged(version) -> pd.DataFrame:
    """The contests the handicap actually decides — the whole reason it exists."""
    return _read_sql(
        """
        SELECT h.run_date AS "Date", e.short_name AS "parkrun",
               h.athlete_name AS "Athlete", h.target_basis AS "Basis",
               h.place_rank AS "Place"
        FROM parkrun.v_head_to_head h JOIN parkrun.events e USING (event_id)
        WHERE h.target_basis LIKE '%handicap%'
        ORDER BY h.run_date DESC
        """
    )


def _welch(b, n) -> dict:
    """Difference in means as a percentage of the non-buggy mean, with a Welch
    interval. Percentage of the non-buggy mean, not of the pooled one, because
    that is the quantity `buggy_handicap` multiplies."""
    mb, mn = b.mean(), n.mean()
    sb, sn = b.std(ddof=1), n.std(ddof=1)
    d, se = mb - mn, np.sqrt(sb**2 / len(b) + sn**2 / len(n))
    dof = se**4 / ((sb**2 / len(b))**2 / (len(b) - 1)
                   + (sn**2 / len(n))**2 / (len(n) - 1))
    tc = sps.t.ppf(0.975, dof)
    return dict(pct=d / mn * 100, lo=(d - tc * se) / mn * 100,
                hi=(d + tc * se) / mn * 100,
                p=float(sps.ttest_ind(b, n, equal_var=False).pvalue))


def _event_fe(w: pd.DataFrame, with_trend: bool) -> dict | None:
    """log(time) ~ buggy + one fixed effect per course (+ a linear date term).

    Only courses run BOTH ways carry information about the buggy once a course
    effect is in the model, so the rest are dropped before fitting rather than
    left in to inflate the degrees of freedom. This is the estimate that is not
    confounded by which courses the buggy happened to visit."""
    both = w.groupby("short_name")["is_buggy"].agg(["min", "max"])
    evs = both[(~both["min"]) & (both["max"])].index
    s = w[w["short_name"].isin(evs)]
    if s.empty or s["is_buggy"].nunique() < 2:
        return None
    y = np.log(s["time_seconds"].astype(float).values)
    cols = [s["is_buggy"].astype(float).values]
    if with_trend:
        cols.append((s["run_date"] - s["run_date"].min()).dt.days.values / 365.25)
    dummies = pd.get_dummies(s["short_name"], drop_first=True).astype(float).values
    X = np.column_stack(cols + [dummies, np.ones(len(s))])
    beta, _, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = len(s) - rank
    if dof < 1:
        return None
    se = np.sqrt((resid @ resid / dof) * np.diag(np.linalg.pinv(X.T @ X)))
    b, sb = beta[0], se[0]
    tc = sps.t.ppf(0.975, dof)
    return dict(pct=(np.exp(b) - 1) * 100, lo=(np.exp(b - tc * sb) - 1) * 100,
                hi=(np.exp(b + tc * sb) - 1) * 100, n=len(s), n_events=len(evs),
                p=float(2 * (1 - sps.t.cdf(abs(b / sb), dof))))


def _dist_fig(name: str, w: pd.DataFrame):
    """Two density curves plus a rug of the real runs.

    The rug is not decoration: a smooth curve over five runs looks exactly as
    confident as one over forty, and the ticks are the only thing on the chart
    that shows which of those you are reading."""
    fig = go.Figure()
    # One grid and one density scale for the whole figure, settled before any
    # series is drawn. Deriving either inside the loop would make the frame
    # depend on which mode happened to be plotted first, and would put the two
    # rug rows at depths proportional to their own peaks — so a wide, flat mode
    # could surface above a narrow one and read as the other row.
    allv = w["time_seconds"].astype(float)
    grid = np.linspace(allv.min() - 60, allv.max() + 60, 240)
    series = {}
    for mode in ("nonbuggy", "buggy"):
        v = w.loc[w["is_buggy"] == (mode == "buggy"), "time_seconds"].astype(float).values
        if len(v) >= 2 and np.ptp(v) > 0:
            series[mode] = (v, sps.gaussian_kde(v)(grid))
    if not series:
        return fig
    peak = max(d.max() for _, d in series.values())

    for mode, (v, dens) in series.items():
        fig.add_trace(go.Scatter(
            x=grid, y=dens, mode="lines", name=MODE_LABEL[mode],
            legendgroup=mode,
            line=dict(color=MODE_COLOR[mode], width=2,
                      dash="dash" if mode == "buggy" else "solid"),
            fill="tozeroy", fillcolor=_fill(MODE_COLOR[mode]),
            hoverinfo="skip"))
        fig.add_trace(go.Scatter(
            x=v, y=np.full(len(v), -peak * (0.07 if mode == "nonbuggy" else 0.16)),
            mode="markers", name=MODE_LABEL[mode], legendgroup=mode, showlegend=False,
            marker=dict(color=MODE_COLOR[mode], size=9, symbol="line-ns-open",
                        line=dict(width=2, color=MODE_COLOR[mode])),
            customdata=w.loc[w["is_buggy"] == (mode == "buggy"),
                             ["short_name", "time", "run_date"]].values,
            hovertemplate="%{customdata[2]|%Y-%m-%d} · %{customdata[0]}"
                          "<br>%{customdata[1]}<extra></extra>"))
        fig.add_vline(x=v.mean(), line=dict(color=MODE_COLOR[mode], width=1, dash="dot"))
    ticks = np.arange(np.ceil(w["time_seconds"].min() / 60) * 60,
                      w["time_seconds"].max() + 60, 60)
    fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", y=1.12, x=0),
        title=dict(text=f"{name} — finish times inside the buggy window", x=0, font=dict(size=14)),
        hovermode="closest")
    fig.update_xaxes(tickvals=ticks, ticktext=[fmt_time(t) for t in ticks],
                     title="finish time")
    # Density has no unit anyone reads, and the rug sits below zero — numbering
    # that axis would invite a comparison of areas the curves do not support.
    fig.update_yaxes(showticklabels=False, showgrid=False, zeroline=False, title="")
    return fig


def render_handicap() -> None:
    st.markdown(
        "`buggy_handicap` is the multiplicative cost of pushing a buggy, one "
        "number per athlete. The head-to-head uses it **only** to bridge a "
        "target when an athlete has no runs of that mode in the 91-day window "
        "— it is not applied to `v_saturday_targets` or `current_targets`. "
        "Everything below is recomputed from the labels currently in this "
        "database."
    )
    st.dataframe(load_handicaps(_ver), hide_index=True, width="stretch")

    bridged = load_bridged(_ver)
    st.caption(
        f"The bridge currently decides **{len(bridged)}** head-to-head "
        f"target(s). A handicap with no bridged contest changes nothing today."
        if len(bridged) else
        "No head-to-head currently uses a bridged target, so the handicaps "
        "change nothing until an athlete turns up with no same-mode history."
    )
    if len(bridged):
        with st.expander("Bridged targets"):
            st.dataframe(bridged, hide_index=True, width="stretch")

    runs = load_moded_runs(_ver).copy()
    runs["run_date"] = pd.to_datetime(runs["run_date"])
    users = sorted(runs.loc[runs["is_buggy"], "athlete_name"].unique())
    if not users:
        st.info(
            "No run is labelled as a buggy run, so there is nothing to measure. "
            "Import the review sheet first — see `scripts/export_buggy_review.py`."
        )
        return

    for name in users:
        g = runs[runs["athlete_name"] == name]
        first, last = g.loc[g["is_buggy"], "run_date"].agg(["min", "max"])
        # The window is first buggy run to last, inclusive: outside it there is
        # no buggy behaviour to compare against, and including those runs would
        # be comparing the buggy against a different era of the athlete.
        w = g[(g["run_date"] >= first) & (g["run_date"] <= last)]
        q1, q3 = w["time_seconds"].quantile([0.25, 0.75])
        thr = q3 + OUTLIER_K * (q3 - q1)
        keep, dropped = w[w["time_seconds"] <= thr], w[w["time_seconds"] > thr]

        st.divider()
        st.subheader(name)
        st.caption(
            f"{first:%Y-%m-%d} → {last:%Y-%m-%d} · {(last - first).days} days · "
            f"{len(keep)} runs"
            + (f" · {len(dropped)} extreme run(s) set aside" if len(dropped) else "")
        )

        rows = []
        for mode in ("nonbuggy", "buggy"):
            v = keep.loc[keep["is_buggy"] == (mode == "buggy"), "time_seconds"].astype(float)
            rows.append({
                "Mode": MODE_LABEL[mode], "Runs": len(v),
                "Mean": fmt_time(v.mean()) if len(v) else "—",
                "SD": f"{v.std(ddof=1):.0f}s" if len(v) > 1 else "—",
                "Median": fmt_time(v.median()) if len(v) else "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

        b = keep.loc[keep["is_buggy"], "time_seconds"].astype(float).values
        n = keep.loc[~keep["is_buggy"], "time_seconds"].astype(float).values
        if len(b) < 2 or len(n) < 2:
            st.warning("Too few runs in one mode to estimate anything.")
            continue

        st.plotly_chart(_dist_fig(name, keep), width="stretch", key=f"dist_{name}")
        if len(dropped):
            st.caption(
                "Set aside as beyond Q3 + 3·IQR: "
                + " · ".join(f"{r.run_date:%Y-%m-%d} {r.short_name} {r.time}"
                             f"{BUGGY_GLYPH if r.is_buggy else ''}"
                             for r in dropped.itertuples())
            )

        raw = _welch(b, n)
        fe_ = _event_fe(keep, with_trend=False)
        fed = _event_fe(keep, with_trend=True)
        est = [("Raw difference in means", "every run in the window, ignoring course", raw)]
        if fe_:
            est.append(("Same course only",
                        f"{fe_['n_events']} course(s) run both ways, {fe_['n']} runs", fe_))
        if fed:
            est.append(("Same course, allowing for form drift",
                        "as above, plus a linear time trend", fed))
        st.dataframe(
            pd.DataFrame([{"Estimate": t, "Basis": s,
                           "Buggy cost": f"{e['pct']:+.1f}%",
                           "95% CI": f"{e['lo']:+.1f} to {e['hi']:+.1f}",
                           "p": f"{e['p']:.3f}"} for t, s, e in est]),
            hide_index=True, width="stretch")

        # A recommendation is only as good as its agreement: when the raw and
        # course-controlled estimates disagree in SIGN, the buggy is not what is
        # being measured — the courses are — and no single number is defensible.
        pcts = [e["pct"] for _, _, e in est]
        spans_zero = raw["lo"] < 0 < raw["hi"]
        disagree = min(pcts) < 0 < max(pcts)
        if spans_zero or disagree or len(b) < 8:
            why = []
            if len(b) < 8:
                why.append(f"only {len(b)} buggy run(s)")
            if spans_zero:
                why.append("the raw interval crosses zero")
            if disagree:
                why.append("the estimates disagree on the sign")
            st.warning(
                f"**Hold the default for {name}** — " + ", ".join(why) + ". "
                "The course a buggy happened to visit is doing more work here "
                "than the buggy is."
            )
        else:
            inside = [round(x, 2) for x in np.arange(0.05, 0.31, 0.01)
                      if all(e["lo"] <= x * 100 <= e["hi"] for _, _, e in est)]
            pick = min(inside, key=lambda x: abs(x * 100 - np.mean(pcts))) if inside else None
            st.success(
                f"**Measured for {name}: {pick:.2f}**" if pick else
                f"**Measured for {name}: no single value sits inside every interval**"
            )
            st.caption(
                f"Estimates span {min(pcts):+.1f}% to {max(pcts):+.1f}%"
                + (f"; {pick:.2f} is inside all {len(est)} confidence intervals "
                   f"and closest to their mean." if pick else ".")
                + f" Apply with `method='measured'`, `n_buggy_labels={len(b)}`."
            )


tab_impact, tab_handicap = st.tabs(["Head-to-head impact", "Buggy handicap"])
with tab_impact:
    render_impact()
with tab_handicap:
    render_handicap()

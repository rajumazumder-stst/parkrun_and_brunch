"""Buggy handicap — what pushing a buggy actually costs each athlete.

Imported by **two** apps: the dev-only `label_impact.py` (as a tab) and the
hosted `handicap_page.py`, served at `/buggy-handicap`. It lives here rather
than in either of them for the same reason `_winning_margin` lives once in
`parkrun_ui`: a second
copy of this arithmetic would make a method difference indistinguishable from a
rounding one, and this module's output is the argument for numbers two named
people are judged by.

`buggy_handicap` is one number per athlete and the head-to-head bridge leans on
it whenever a mode has no runs in the 91-day window, so it deserves to be
derived on screen rather than asserted in a commit message. Everything here is
computed from the labels currently in the database: correct a label and every
figure moves.

Needs scipy (Welch interval, course fixed effects, gaussian_kde) — which is why
scipy is in requirements.txt even though `app.py` itself does no statistics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from scipy import stats as sps

from parkrun_ui import BUGGY_GLYPH, _read_sql, fmt_time


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

# One pair for both athletes: the series here is the MODE, not the runner, so
# the same two colours must mean the same two things on every chart it draws.
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


def render_handicap(version: str) -> None:
    st.markdown(
        "`buggy_handicap` is the multiplicative cost of pushing a buggy, one "
        "number per athlete. The head-to-head uses it **only** to bridge a "
        "target when an athlete has no runs of that mode in the 91-day window "
        "— it is not applied to `v_saturday_targets` or `current_targets`. "
        "Everything below is recomputed from the labels currently in this "
        "database."
    )
    st.dataframe(load_handicaps(version), hide_index=True, width="stretch")

    bridged = load_bridged(version)
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

    runs = load_moded_runs(version).copy()
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

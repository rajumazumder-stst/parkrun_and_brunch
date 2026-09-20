"""Tab 6 — what the buggy estimator guessed, per run.

The estimator has labelled runs unattended since 5 Sep 2026 and nothing in the
app showed what it said. Its confidence reached a person once, in the Saturday
refresh notification, and only for runs it actually labelled; everything else
lived in a `git diff`. This tab is the standing view of it.

Reads `parkrun.model_estimates` — a **record**, written once per run and never
recomputed. So a row is what the model said when it was scored, not what it
would say today. That is also what makes a later hand correction non-destructive:
the estimate sits beside the corrected label instead of being overwritten by it.

Layout only, plus the derivations the page needs. Imports no `buggy_estimator`
and no scipy: the scoring happened in the refresh, and this is a plain SELECT.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from parkrun_core import BUGGY_ATHLETES
from parkrun_ui import (
    ATHLETE_COLORS,
    BUGGY_GLYPH,
    REGULAR_LABEL,
    _read_sql,
    _surface_color,
)

# Red for a wrong call. Deliberately local rather than in `parkrun_ui`: one
# module needs it, and `parkrun_core`'s docstring sets the rule that anything
# only one module needs stays in that module. It must not collide with an
# ATHLETE_COLORS value or a PLACE_COLORS medal — Raju never appears on this
# tab, so his orange is not in play here.
ERR = "#c0392b"

# Green for a right call. Borrowed from `method_impact.py`'s verdict palette so
# the two tables agree; the cross reuses ERR so it matches the chart's rings —
# within this tab, "wrong" is one colour wherever it appears.
OK = "#1b6e3c"

# Below this many calls a percentage is noise. The parkrun filter can reach
# denominators of five, and a figure over five should not look as solid as one
# over forty, so it is shown muted with the n still in its label.
THIN_N = 8

# The review states, in the order a reader wants them rather than alphabetical.
REVIEW_ORDER = ["Never checked", "Corrected", "Confirmed", "Athlete label", "Rule"]
OUTCOME_ORDER = ["Right", "Wrong"]


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_estimates(version) -> pd.DataFrame:
    """Every stored estimate, joined to the label it is judged against.

    Scope is the estimator's own: a buggy athlete's runs on or after their
    first buggy-labelled one. `build_model_estimates` writes exactly that set,
    so the frontier does not need restating here — an estimate row existing is
    the definition of in-scope.

    `review` is **derived, not stored**, and it answers one question: what has
    a person said about this run?

    It reads `reason` first, and deliberately. Timestamps alone cannot work
    for a backfilled estimate — `computed_at` is the backfill moment, which is
    after every historical label, so `set_at > computed_at` is false for all
    of them and a hand correction would be invisible. `reason` is the only
    surviving record that someone overturned a call, because correcting a
    label overwrites the `model` row that held the confidence. The strings are
    a documented convention (docs/DATA.md), not an accident.

    The timestamp comparison stays as the forward path: once an estimate is
    scored live, a `user` label written after it is a person reacting to that
    specific call, and whether they agreed is the label comparison.

    `Athlete label` is its own state, not a kind of confirmation: the athlete
    stated the run's mode on the review sheet without ever seeing the model's
    call. The truth is solid, the call was never audited. Naming that
    `Backfilled` — as an earlier version did — described how the estimate was
    produced rather than what a person had said, which are different facts.
    """
    # A snapshot built before this table existed simply does not carry it, and
    # this page script shares a process with tabs 1-5 — an uncaught error here
    # takes the whole app down, not just this tab. That is the real deploy
    # case: push the code, and until a refresh rebuilds the snapshot the
    # hosted app is serving a database with no `model_estimates` in it.
    try:
        df = _read_sql(
            """
            SELECT a.athlete_name,
                   e.run_date,
                   ev.short_name,
                   r.time,
                   e.is_buggy   AS called_buggy,
                   e.p,
                   e.confidence,
                   e.status,
                   e.basis,
                   m.is_buggy   AS actual_buggy,
                   m.source,
                   CASE
                     -- Nobody has weighed in: the label IS the model's guess.
                     WHEN m.source = 'model'              THEN 'Never checked'
                     -- What a person did, stated directly. docs/DATA.md defines
                     -- these strings; they are the ONLY record that a human
                     -- overturned a call, because correcting a label overwrites
                     -- the model row it replaced.
                     WHEN m.reason ILIKE 'corrected%'     THEN 'Corrected'
                     WHEN m.reason ILIKE 'confirmed model%' THEN 'Confirmed'
                     -- Forward path, for calls scored live: a user label written
                     -- after the estimate is a person reacting to that call.
                     WHEN m.source = 'user' AND m.set_at > e.computed_at
                          AND m.is_buggy <> e.is_buggy    THEN 'Corrected'
                     WHEN m.source = 'user' AND m.set_at > e.computed_at
                                                          THEN 'Confirmed'
                     WHEN m.source = 'rule'               THEN 'Rule'
                     -- The athlete stated the mode on the review sheet, independent
                     -- of the model. The truth is solid; the call was never audited.
                     ELSE 'Athlete label'
                   END AS review
            FROM parkrun.model_estimates e
            JOIN parkrun.results   r  USING (athlete_id, run_date, event_id)
            JOIN parkrun.run_modes m  USING (athlete_id, run_date, event_id)
            JOIN parkrun.athletes  a  USING (athlete_id)
            JOIN parkrun.events    ev USING (event_id)
            ORDER BY a.athlete_name, e.run_date DESC
        """
        )
    except duckdb.CatalogException:
        return pd.DataFrame()
    if df.empty:
        return df
    df["run_date"] = pd.to_datetime(df["run_date"])
    df["year"] = df["run_date"].dt.year.astype(str)
    scored = df["status"] == "scored"
    df["correct"] = scored & (df["called_buggy"] == df["actual_buggy"])
    df.loc[~scored, "correct"] = pd.NA
    df["outcome"] = df["correct"].map({True: "Right", False: "Wrong"})
    return df


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
# The four filters split in two, and the split decides what the tiles do.
#
# POPULATION (year, parkrun) chooses WHICH RUNS to look at, with no reference
# to how the model did on them — so an accuracy computed afterwards answers a
# real question: how reliable was it in 2026, or at Cassiobury.
#
# OUTCOME (model call, review) selects ON the result. "Wrong" and "Corrected"
# are near the same set, so an accuracy computed after them is circular and
# reads 0% or 100% by construction. The tiles ignore these two; the chart and
# the table honour all four.
POPULATION = [("year", "year"), ("venue", "short_name")]
OUTCOME = [("outcome", "outcome"), ("review", "review")]


def _apply(df: pd.DataFrame, sel: dict, spec) -> pd.DataFrame:
    """An empty selection means all — tab 1's year-multiselect convention,
    applied to every filter here so there is no synthetic 'All' option to keep
    in step with the real ones as the data grows."""
    out = df
    for key, col in spec:
        chosen = sel.get(key) or []
        if chosen:
            out = out[out[col].isin(chosen)]
    return out


def _options(df: pd.DataFrame, sel: dict, key: str, col: str, universe: list):
    """Option labels carrying a count of the rows the *other* filters leave.

    Pick 2025 and the Review menu should say `Never checked 0`, because there
    are none that year — not report 3 from the whole record. The universe is
    passed in so a selected option never vanishes at a count of zero and you
    can always deselect your way back out.
    """
    others = [p for p in POPULATION + OUTCOME if p[0] != key]
    counts = _apply(df, sel, others)[col].value_counts()
    return [f"{v}  ·  {int(counts.get(v, 0))}" for v in universe]


def _strip_count(labels) -> list:
    return [lb.split("  ·  ")[0] for lb in labels]


def filter_row(df: pd.DataFrame) -> dict:
    """The four multiselects, shared by both athletes.

    Every one is a multiselect on the same empty-means-all rule. Not
    `year_season_filters`: that is a mutually-exclusive year/season pair built
    for the head-to-head tabs, and this tab wants year *alongside* three
    unrelated filters rather than instead of a season.
    """
    sel: dict = {}
    for key in ("year", "venue", "outcome", "review"):
        sel[key] = _strip_count(st.session_state.get(f"t6_{key}", []))

    universes = {
        "year": sorted(df["year"].unique()),
        "venue": sorted(df["short_name"].unique()),
        "outcome": OUTCOME_ORDER,
        "review": [r for r in REVIEW_ORDER if r in set(df["review"])],
    }
    cols = st.columns(3)
    specs = [
        (cols[0], "year", "year", "Year", "All years"),
        (cols[1], "outcome", "outcome", "Model call", "Right and wrong"),
        (cols[2], "review", "review", "Review", "All review states"),
    ]
    for col, key, dfcol, label, placeholder in specs:
        col.multiselect(
            label,
            _options(df, sel, key, dfcol, universes[key]),
            key=f"t6_{key}",
            placeholder=placeholder,
            help="Leave empty for all",
        )
    # parkrun gets its own full-width row: chips need the space.
    st.multiselect(
        "parkrun",
        _options(df, sel, "venue", "short_name", universes["venue"]),
        key="t6_venue",
        placeholder="All parkruns",
        help="Leave empty for all",
    )
    return {k: _strip_count(st.session_state.get(f"t6_{k}", []))
            for k in ("year", "venue", "outcome", "review")}


def _phrase(chosen, noun) -> str | None:
    if not chosen:
        return None
    return " and ".join(chosen) if len(chosen) <= 2 else f"{len(chosen)} {noun}"


# --------------------------------------------------------------------------- #
# Pieces
# --------------------------------------------------------------------------- #
def _tiles(pop: pd.DataFrame) -> None:
    """Reliability, over the population-filtered set only.

    The n lives in the label rather than a tooltip: "85% right, of 34" and "of
    4" are different claims. A direction right less than half the time is
    coloured — worse than a coin flip on a binary call is a real line, not an
    invented threshold.
    """
    scored = pop[pop["status"] == "scored"]
    bug = scored[scored["called_buggy"]]
    reg = scored[~scored["called_buggy"]]

    def fig(part):
        if not len(part):
            return None
        return round(100 * part["correct"].sum() / len(part))

    overall, bug_pct, reg_pct = fig(scored), fig(bug), fig(reg)
    cells = [
        (f"{len(pop)}", "calls in scope", False, False),
        ("—" if overall is None else f"{overall}%", "right overall",
         False, len(scored) < THIN_N),
        ("—" if bug_pct is None else f"{bug_pct}%",
         f"{BUGGY_GLYPH} buggy calls right, of {len(bug)}",
         bug_pct is not None and bug_pct < 50, len(bug) < THIN_N),
        ("—" if reg_pct is None else f"{reg_pct}%",
         f"{REGULAR_LABEL} calls right, of {len(reg)}",
         reg_pct is not None and reg_pct < 50, len(reg) < THIN_N),
    ]
    for col, (big, label, bad, thin) in zip(st.columns(len(cells)), cells):
        style = "font-size:2rem;font-weight:700;line-height:1.1;" \
                "font-variant-numeric:tabular-nums;"
        if bad:
            style += f"color:{ERR};"
        if thin:
            style += "opacity:.45;font-weight:600;"
        col.markdown(
            f"<div style='{style}'>{big}</div>"
            f"<div style='font-size:13px;opacity:.72;line-height:1.35;"
            f"margin-top:2px'>{label}</div>",
            unsafe_allow_html=True,
        )


def _strip_fig(rows: pd.DataFrame, name: str) -> go.Figure:
    """Every call placed by confidence, in two lanes by direction.

    This is the tab's thesis in one picture. The lesson of Duncan's Cassiobury
    run is that **confidence matters more than direction** — a `regular` call
    at 0.52 was wrong where his others sit at a median of 0.92 — and only a
    chart with confidence as an axis can show that.

    Wrong calls are drawn last, as open rings, so they sit above the crowd:
    they are ~13 of 101 and they are the point. No threshold line and no
    shaded band — both calls ever made below 0.60 were wrong, but that is n=2,
    and drawing it would assert a rule the data does not support.
    """
    scored = rows[rows["status"] == "scored"]
    surface = _surface_color()
    lanes = [(f"Called {BUGGY_GLYPH} buggy", True), (f"Called {REGULAR_LABEL}", False)]
    fig = go.Figure()

    for lane_y, (label, is_bug) in enumerate(lanes):
        part = scored[scored["called_buggy"] == is_bug]
        for right, sub in ((True, part[part["correct"]]),
                           (False, part[~part["correct"]])):
            if not len(sub):
                continue
            # Deterministic jitter, never an RNG: a redraw must be reproducible.
            span, step = (5, 0.052) if right else (3, 0.065)
            offs = [((i % span) - span // 2) * step for i in range(len(sub))]
            marker = (dict(size=9, color=ATHLETE_COLORS[name], opacity=0.55,
                           line=dict(width=1, color=surface))
                      if right else
                      dict(size=15, color="rgba(0,0,0,0)",
                           line=dict(width=2.5, color=ERR)))
            fig.add_trace(go.Scatter(
                x=sub["confidence"] * 100,
                y=[lane_y + o for o in offs],
                mode="markers", marker=marker, showlegend=False,
                customdata=list(zip(
                    sub["run_date"].dt.strftime("%d %b %Y"), sub["short_name"],
                    sub["time"], sub["review"],
                )),
                hovertemplate=(
                    "%{customdata[0]} · %{customdata[1]}<br>"
                    f"{'Called ' + ('buggy' if is_bug else REGULAR_LABEL)} at "
                    "%{x:.0f}%<br>"
                    f"<b>{'Right' if right else 'Wrong'}</b> · %{{customdata[3]}}"
                    "<extra></extra>"),
            ))

    fig.update_layout(
        height=220, margin=dict(t=16, b=8, l=0, r=0),
        xaxis=dict(range=[48, 102], ticksuffix="%",
                   title=dict(text="← less confident · more confident →",
                              font=dict(size=12, color="#808080"))),
        yaxis=dict(range=[1.6, -0.6], tickmode="array",
                   tickvals=[0, 1], ticktext=[lanes[0][0], lanes[1][0]],
                   title=None),
    )
    return fig


def _table(rows: pd.DataFrame) -> pd.DataFrame:
    def mode(v):
        return f"{BUGGY_GLYPH} buggy" if v else REGULAR_LABEL
    d = rows.sort_values("run_date", ascending=False)
    return pd.DataFrame({
        "Date": d["run_date"].dt.strftime("%d %b %Y"),
        "parkrun": d["short_name"],
        "Time": d["time"],
        "Model said": [mode(v) if s == "scored" else "—"
                       for v, s in zip(d["called_buggy"], d["status"])],
        "Confidence": [f"{c:.0%}" if s == "scored" else "—"
                       for c, s in zip(d["confidence"], d["status"])],
        "Actually": [mode(v) for v in d["actual_buggy"]],
        # `pd.isna`, never `c is True`: the column holds numpy.bool_, which is
        # equal to True but is not the singleton, so an identity test silently
        # blanks every row. Blank here means no call was made (unfittable),
        # which is the only case with nothing to compare.
        "Right?": ["" if pd.isna(c) else ("✓" if c else "✗")
                   for c in d["correct"]],
        "Review": d["review"],
    })


def _athlete(df: pd.DataFrame, sel: dict, name: str) -> pd.DataFrame:
    pop = _apply(df[df["athlete_name"] == name], sel, POPULATION)
    rows = _apply(pop, sel, OUTCOME)

    st.markdown(
        f"<div style='font-weight:700;font-size:1.25rem;margin:.2rem 0 .6rem'>"
        f"<span style='color:{ATHLETE_COLORS[name]}'>●</span> {name}</div>",
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        if pop.empty:
            st.info(f"No calls for {name} in this year or at these parkruns.")
            return rows

        scope = _phrase(sel["year"], "years"), _phrase(sel["venue"], "parkruns")
        bits = " · ".join(b for b in scope if b) or "all calls"
        note = ("  ·  not narrowed by Model call or Review — filtering on the "
                "outcome would force these to 0% or 100%"
                if sel["outcome"] or sel["review"] else "")
        st.markdown(
            f"<div style='font-size:12px;letter-spacing:.05em;text-transform:"
            f"uppercase;opacity:.6;margin-bottom:.5rem'>Reliability — {bits} · "
            f"{len(pop)} call{'s' if len(pop) != 1 else ''}"
            f"<span style='text-transform:none;letter-spacing:0;color:#b07000'>"
            f"{note}</span></div>",
            unsafe_allow_html=True,
        )
        _tiles(pop)

        if rows.empty:
            st.info("No calls match the Model call / Review filters.")
            return rows

        if (rows["status"] == "scored").any():
            st.plotly_chart(_strip_fig(rows, name), width="stretch",
                            key=f"t6_strip_{name}")
        st.markdown(
            f"**Calls** {len(rows)}"
            + (f" of {len(pop)}" if len(rows) < len(pop) else "")
        )
        # Styler rather than emoji: ✅/❌ would sit heavier than the 🛒 already
        # in the row, and this is the house pattern for coloured cells
        # (method_impact.py does the same on its Verdict column).
        st.dataframe(
            _table(rows).style.map(
                lambda v: f"color:{OK};font-weight:700" if v == "✓"
                else (f"color:{ERR};font-weight:700" if v == "✗" else ""),
                subset=["Right?"],
            ),
            hide_index=True, width="stretch", height=420,
        )
    return rows


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def render_estimates(version) -> None:
    df = load_estimates(version)
    st.header("🤖 What the model guessed")
    if df.empty:
        st.info(
            "No estimates recorded yet — this database has none. They are "
            "written by the refresh; to record them now, run "
            "`python parkrun_pipeline.py estimates --backfill` against it."
        )
        return

    st.caption(
        "Every parkrun George and Duncan have run since their first with a "
        "buggy, and what the estimator called it — scored from that run's own "
        "past only. An estimate is kept as written and never recomputed, so it "
        "is what the model said at the time, not what it would say today."
    )

    with st.expander("How to read this"):
        st.markdown(f"""
The model is given four things about a run: how far off the pace it was for that
course, how it compares to the athlete's recent form, how hard the course is, and
how often that athlete has pushed a buggy **at that particular parkrun**. It is
never told about a buggy directly — parkrun records nothing about one.

**Confidence matters more than direction.** A buggy call and a regular call are
not equally trustworthy, but neither is reliably the safe one. On 12 September
2026 a regular call at 52% turned out to be a buggy run — the first of its kind,
and a reminder that a low-confidence call in *either* direction deserves a look.
Both calls ever made below 60% have been wrong, though that is only two calls,
so read it as a hint rather than a rule.

**Review** says what a person has said about the run, not how the estimate was
produced. *Athlete label* means the athlete stated the mode on the review sheet
without ever seeing the model's call — the truth is solid, the call was never
audited. *Never checked* is the one that needs you: the label there **is** the
model's guess, with nothing independent behind it.

**Correcting a label changes the record.** A run's mode decides which form target
it is judged against, so relabelling one moves its head-to-head result — and
possibly who won that day.
""")

    sel = filter_row(df)
    shown = pd.concat(
        [_apply(_apply(df[df["athlete_name"] == n], sel, POPULATION), sel, OUTCOME)
         for n in df["athlete_name"].unique()]
    ) if not df.empty else df
    wrong = int((shown["correct"] == False).sum())        # noqa: E712
    never = int((shown["review"] == "Never checked").sum())
    bits = [b for b in (_phrase(sel["year"], "years"),
                        _phrase(sel["venue"], "parkruns"),
                        _phrase(sel["outcome"], "outcomes"),
                        _phrase(sel["review"], "review states")) if b]
    st.caption(
        f"{len(shown)} call{'s' if len(shown) != 1 else ''} shown · {wrong} wrong"
        f" · {never} never checked"
        + (f"  —  filtered to {', '.join(bits)}" if bits else "")
    )

    for name in sorted(BUGGY_ATHLETES.values()):
        st.divider()
        _athlete(df, sel, name)

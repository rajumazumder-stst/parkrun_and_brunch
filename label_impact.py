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

import pandas as pd
import streamlit as st

from buggy_handicap import data_version, render_handicap
from parkrun_ui import (
    DB_PATH,
    _h2h_headline,
    _read_sql,
    _victory_fig,
    _winning_margin,
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


tab_impact, tab_handicap = st.tabs(["Head-to-head impact", "Buggy handicap"])
with tab_impact:
    render_impact()
with tab_handicap:
    render_handicap(_ver)

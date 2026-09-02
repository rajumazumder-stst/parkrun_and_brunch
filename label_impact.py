"""Label impact — the dev-only twin of the hosted /buggy-handicap page.

Both tabs here are the same code the hosted page runs; this app exists so they
can be driven against an isolated dev DB without touching the deploy snapshot:

    PARKRUN_LABEL_AUDIT=1 ./scripts/run_local.sh
    # or directly:
    PARKRUN_DB=data/parkrun_dev.duckdb streamlit run label_impact.py --server.port 8502

The views the comparison reads (`v_head_to_head_legacy`) are built into a dev DB
by `run_local.sh` under that flag, and into the deploy snapshot by
`build_snapshot`. See `method_impact.py` and `buggy_handicap.py` for the two
analyses; nothing but layout lives here.
"""

from __future__ import annotations

import streamlit as st

from buggy_handicap import data_version, render_handicap
from method_impact import render_impact
from parkrun_ui import DB_PATH

st.set_page_config(page_title="Label impact", page_icon="🧪", layout="wide")

st.title("🧪 Label impact")
st.caption(f"Dev copy of the hosted /buggy-handicap page — {DB_PATH}")

_ver = data_version()

tab_handicap, tab_impact = st.tabs(
    ["What the buggy costs", "What labelling changed"]
)
with tab_handicap:
    render_handicap(_ver)
with tab_impact:
    render_impact(_ver)

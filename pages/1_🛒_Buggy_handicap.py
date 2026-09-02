"""Hosted page: what pushing a buggy costs George and Duncan.

A page rather than a tab in `app.py` because it answers a different question
from the rest of the site. The five main tabs are about what happened; this one
is about how the comparison is *made* — the working behind `buggy_handicap`,
which decides how a buggy run is judged against a non-buggy one.

The analysis lives in `buggy_handicap.py`, shared with the dev-only
`label_impact.py`, so the hosted page and the local instrument can never drift
apart on the arithmetic.

Deliberately NOT hosted alongside it: the head-to-head method comparison. That
needs `v_head_to_head_legacy`, which `build_snapshot` never carries — the
legacy views freeze a superseded method, and a hosted app serving them invites
someone to read the old numbers as current.
"""

from __future__ import annotations

import streamlit as st

from buggy_handicap import data_version, render_handicap
from parkrun_ui import BUGGY_GLYPH

st.set_page_config(page_title="Buggy handicap", page_icon=BUGGY_GLYPH,
                   layout="wide")

st.title(f"{BUGGY_GLYPH} What the buggy costs")
st.caption(
    "George and Duncan sometimes run pushing a buggy. parkrun records nothing "
    "about it, so every run is labelled by hand — and this page is the working "
    "behind the number that decides how a buggy run is judged against a "
    "non-buggy one."
)

render_handicap(data_version())

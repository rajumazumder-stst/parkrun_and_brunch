"""Page script: what pushing a buggy costs George and Duncan.

Served at `/buggy-handicap` on the main app's domain, routed from `app.py`.
A page rather than a tab because it answers a different question from the rest
of the site — the five tabs are about what happened, this is about how the
comparison is *made* — and it is deliberately absent from any nav: the audience
is the two people it is about, reached by a link they are sent, not a visitor
browsing parkrun results.

No `st.set_page_config` here; `app.py` owns the single legal call, and this
page's browser-tab title comes from its `st.Page(title=...)`.

The analysis lives in `buggy_handicap.py`, shared with the dev-only
`label_impact.py`, so the hosted page and the local instrument can never drift
apart on the arithmetic.

Deliberately NOT here: the head-to-head method comparison. It needs
`v_head_to_head_legacy`, which `build_snapshot` never carries — the legacy views
freeze a superseded method, and serving them invites a reader to take the old
numbers as current.
"""

from __future__ import annotations

import streamlit as st

from buggy_handicap import data_version, render_handicap
from parkrun_ui import BUGGY_GLYPH

st.title(f"{BUGGY_GLYPH} What the buggy costs")
st.caption(
    "George and Duncan sometimes run pushing a buggy. parkrun records nothing "
    "about it, so every run is labelled by hand — and this page is the working "
    "behind the number that decides how a buggy run is judged against a "
    "non-buggy one."
)

render_handicap(data_version())

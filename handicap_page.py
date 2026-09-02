"""Page script: the buggy labels — what they cost, and what they changed.

Served at `/buggy-handicap`, routed from `app.py`. Two tabs:

  * **What the buggy costs** — the working behind `buggy_handicap`: per athlete,
    the runs between their first and last buggy run, split by mode.
  * **What labelling changed** — the head-to-head record under the pre-buggy
    method against the current one, occasion by occasion.

Both are the same modules the dev-only `label_impact.py` runs, so the hosted
page and the local instrument can never drift apart on the arithmetic.

A page rather than tabs in the main app because it answers a different question
from the rest of the site: the five tabs are about what happened, these are
about how the comparison is *made*. It is deliberately absent from any nav — the
audience is the two people it is about, reached by a link they are sent. Unlisted
is not access-controlled: anyone with the URL can read it.

No `st.set_page_config` here; `app.py` owns the single legal call, and this
page's browser-tab title comes from its `st.Page(title=...)`.
"""

from __future__ import annotations

import streamlit as st

from buggy_handicap import data_version, render_handicap
from method_impact import render_impact
from parkrun_ui import BUGGY_GLYPH

st.title(f"{BUGGY_GLYPH} The buggy labels")
st.caption(
    "George and Duncan sometimes run pushing a buggy. parkrun records nothing "
    "about it, so every run is labelled by hand. These are the two things worth "
    "asking about those labels: what the buggy costs, and what labelling "
    "changed."
)

_ver = data_version()

tab_cost, tab_changed = st.tabs(
    ["What the buggy costs", "What labelling changed"]
)
with tab_cost:
    render_handicap(_ver)
with tab_changed:
    render_impact(_ver)

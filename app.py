"""Entrypoint and router for the hosted app.

Two pages on one domain:

    /                 parkrun_app.py    the five-tab comparison app
    /buggy-handicap   handicap_page.py  the working behind buggy_handicap

`position="hidden"` is the point of routing through `st.navigation` at all.
A `pages/` directory would give the same URLs but force a nav list into the
sidebar, which put a statistical argument about two named people in front of
every visitor who came to look at parkrun results. Hidden navigation keeps the
path reachable by anyone sent the link and invisible to everyone else — the
page is unlisted, not access-controlled.

`st.set_page_config` lives here because only one call is legal per run; each
page's browser-tab title comes from its `st.Page(title=...)`.

Run:  streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

_ICON = Path(__file__).resolve().parent / "static" / "logo-512.png"

st.set_page_config(page_title="parkrun & brunch",
                   page_icon=str(_ICON) if _ICON.is_file() else "🏃",
                   layout="wide")

st.navigation(
    [
        st.Page("parkrun_app.py", title="parkrun & brunch", default=True),
        st.Page("handicap_page.py", title="What the buggy costs",
                url_path="buggy-handicap"),
    ],
    position="hidden",
).run()

"""Entrypoint for the hosted app: one page, `/` → parkrun_app.py.

It was a router for two pages until 8 Oct 2026, when the unlisted
`/buggy-handicap` page was removed (its analysis lives on in the dev-only
`label_impact.py`). `st.navigation` is kept with one hidden page so a second
page is one `st.Page` away and adds no nav list to the sidebar.

`st.set_page_config` lives here because only one call is legal per run; the
browser-tab title comes from the `st.Page(title=...)`.

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
    ],
    position="hidden",
).run()

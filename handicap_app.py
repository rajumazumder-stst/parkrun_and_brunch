"""Standalone Streamlit app: what pushing a buggy costs George and Duncan.

Its own entrypoint, deployed as a SECOND Streamlit Cloud app from this repo,
so it has its own URL and appears nowhere in `app.py`. It was briefly a
`pages/` entry instead; that put a nav link in the main app's sidebar, which
made a piece of working-out look like a sixth feature of the product. The
audience for this page is the two people it is about, reached by a link they
are sent — not a visitor browsing parkrun results.

Deploy: Streamlit Cloud → New app → this repo → main file `handicap_app.py`
→ pick its own subdomain. Same `requirements.txt`, same bundled snapshot, no
secrets.

The analysis lives in `buggy_handicap.py`, shared with the dev-only
`label_impact.py`, so this app and the local instrument can never drift apart
on the arithmetic.

Deliberately NOT here: the head-to-head method comparison. It needs
`v_head_to_head_legacy`, which `build_snapshot` never carries — the legacy
views freeze a superseded method, and serving them invites a reader to take
the old numbers as current.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from buggy_handicap import data_version, render_handicap
from parkrun_ui import BUGGY_GLYPH

ICON = Path(__file__).parent / "static" / "logo-512.png"

st.set_page_config(
    page_title="What the buggy costs",
    page_icon=str(ICON) if ICON.exists() else BUGGY_GLYPH,
    layout="wide",
)

st.title(f"{BUGGY_GLYPH} What the buggy costs")
st.caption(
    "George and Duncan sometimes run pushing a buggy. parkrun records nothing "
    "about it, so every run is labelled by hand — and this page is the working "
    "behind the number that decides how a buggy run is judged against a "
    "non-buggy one."
)

render_handicap(data_version())

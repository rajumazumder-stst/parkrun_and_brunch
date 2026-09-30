"""App-wide UI contracts: the chart zoom lock, the stat-slot anatomy, and one
smoke run of the whole app against the committed snapshot.

The smoke run is the one test in the suite that reads a project database — the
committed deploy snapshot, read-only — because the year/season filters live in
`parkrun_app.py`, a Streamlit page script that cannot be imported. It is
skipped if the snapshot is missing.
"""
from __future__ import annotations

import ast

import plotly.graph_objects as go
import pytest

import parkrun_core
import parkrun_ui as ui

REPO = parkrun_core.REPO
APP_MODULES = ["parkrun_app.py", "parkrun_ui.py", "estimator_tab.py",
               "buggy_handicap.py", "method_impact.py", "handicap_page.py",
               "label_impact.py", "where_next.py", "parkrun_calendar.py", "app.py"]


def test_every_plotly_chart_goes_through_show_chart():
    """A chart rendered with st.plotly_chart directly would skip the zoom lock
    and trap a phone's scroll again (docs/STYLE.md § Charts)."""
    offenders = []
    for mod in APP_MODULES:
        if mod == "parkrun_ui.py":
            continue
        for node in ast.walk(ast.parse((REPO / mod).read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "plotly_chart"):
                offenders.append(f"{mod}:{node.lineno}")
    assert not offenders, f"use parkrun_ui.show_chart: {offenders}"


def test_lock_zoom_fixes_every_axis_and_keeps_the_legend():
    fig = go.Figure(go.Scatter(x=[1, 2], y=[3, 4], name="a"))
    fig.update_layout(xaxis2=dict(), yaxis2=dict())
    ui.lock_zoom(fig)
    assert fig.layout.xaxis.fixedrange and fig.layout.yaxis.fixedrange
    assert fig.layout.xaxis2.fixedrange and fig.layout.yaxis2.fixedrange
    assert fig.layout.legend.itemclick is None      # default: click toggles
    assert ui.PLOTLY_CONFIG["scrollZoom"] is False


def test_stat_slot_reads_label_then_value_then_note():
    html = ui.stat_label("right overall") + ui.stat_value("85%") + ui.stat_note("of 34")
    assert html.index("right overall") < html.index("85%") < html.index("of 34")
    assert "class='pb-small'" in ui.stat_label("x")
    assert "class='pb-big'" in ui.stat_value("x")
    assert "opacity:.72" in ui.stat_note("x") and "opacity" not in ui.stat_label("x")


def test_an_empty_note_still_takes_its_line():
    assert "&nbsp;" in ui.stat_note("")


@pytest.mark.skipif(not parkrun_core.SNAPSHOT.exists(), reason="no snapshot")
def test_app_runs_and_year_filters_take_several_years(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("PARKRUN_DB", str(parkrun_core.SNAPSHOT))
    at = AppTest.from_file(str(REPO / "parkrun_app.py"), default_timeout=180)
    at.run()
    assert not at.exception
    assert len(at.tabs) == 7

    at.multiselect(key="t4_year").set_value(["2024", "2025"]).run()
    assert not at.exception
    # Choosing a season clears the years: the two stay mutually exclusive.
    season = at.multiselect(key="t4_season").options[0]
    at.multiselect(key="t4_season").set_value([season]).run()
    assert at.multiselect(key="t4_year").value == []

    # Tab 7: the head-to-head view has only the classification/year/season
    # row; the planner has none of those, and its own filters instead.
    at.selectbox(key="t7_class").set_value(
        at.selectbox(key="t7_class").options[1]).run()
    assert not at.exception
    at.session_state["t7_view"] = "Planner"
    at.run()
    assert not at.exception
    keys = {w.key for w in at.selectbox} | {w.key for w in at.multiselect}
    assert "t7_class" not in keys and "t7_year" not in keys
    at.multiselect(key="t7_countries").set_value(["United Kingdom"]).run()
    at.session_state["t7_done_Raju"] = "Not done"
    at.session_state["t7_h2h_George vs Raju"] = "Happened"
    at.run()
    assert not at.exception

    # Clear all filters: every planner filter back to its default, settings
    # (the view) untouched.
    at.button(key="t7_clear").click().run()
    assert not at.exception
    assert at.multiselect(key="t7_countries").value == []
    assert at.session_state["t7_done_Raju"] == "Any"
    assert at.session_state["t7_h2h_George vs Raju"] == "Any"
    assert at.session_state["t7_view"] == "Planner"

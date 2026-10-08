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
               "buggy_handicap.py", "method_impact.py",
               "label_impact.py", "where_next.py", "parkrun_calendar.py", "app.py",
               "milestone_matrix.py", "venue_matrix.py", "h2h_streaks.py"]


def _calls_outside_ui(attr: str) -> list:
    """file:line of every `….<attr>(…)` call in the app modules bar
    parkrun_ui.py, which holds the one sanctioned wrapper."""
    offenders = []
    for mod in APP_MODULES:
        if mod == "parkrun_ui.py":
            continue
        for node in ast.walk(ast.parse((REPO / mod).read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == attr):
                offenders.append(f"{mod}:{node.lineno}")
    return offenders


def test_every_plotly_chart_goes_through_show_chart():
    """A chart rendered with st.plotly_chart directly would skip the zoom lock
    and trap a phone's scroll again (docs/STYLE.md § Charts)."""
    offenders = _calls_outside_ui("plotly_chart")
    assert not offenders, f"use parkrun_ui.show_chart: {offenders}"


def test_no_count_is_written_without_its_thousands_comma():
    """A count interpolated as `{len(x)}` skips fmt_n's comma — 2394, not
    2,394 (docs/STYLE.md § Numbers). Counts are what grow past 999 here."""
    offenders = []
    for mod in APP_MODULES:
        for node in ast.walk(ast.parse((REPO / mod).read_text())):
            if (isinstance(node, ast.FormattedValue)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "len"):
                offenders.append(f"{mod}:{node.lineno}")
    assert not offenders, f"wrap the count in parkrun_ui.fmt_n: {offenders}"


def test_fmt_n_puts_a_comma_in_thousands():
    assert ui.fmt_n(2394) == "2,394" and ui.fmt_n(999) == "999"
    assert ui.fmt_n(1312.46, 1) == "1,312.5" and ui.fmt_n(12.0, 1) == "12.0"
    assert ui.fmt_n(None) == "—" and ui.fmt_n(float("nan")) == "—"


def test_every_country_in_the_lookup_has_a_flag():
    import csv
    with open(REPO / "data" / "country_lookup.csv") as f:
        names = [r["country_name"] for r in csv.DictReader(f)]
    missing = [n for n in names if n != "Unknown" and ui.flag(n) == ui.NO_FLAG]
    assert not missing, f"add to parkrun_ui.COUNTRY_ISO: {missing}"
    assert ui.flag("United Kingdom") == "🇬🇧" and ui.flag("Ireland") == "🇮🇪"
    assert ui.flag("Unknown") == ui.NO_FLAG


def test_every_child_country_has_a_flag():
    """Each child in data/event_countries.csv has an emoji or a drawn flag."""
    import csv
    with open(REPO / "data" / "event_countries.csv") as f:
        names = {r["child_country"] for r in csv.DictReader(f)}
    missing = [n for n in names if ui.flag(n) == ui.NO_FLAG]
    assert not missing, f"add to parkrun_ui.COUNTRY_ISO: {missing}"
    assert ui.flag("England") == "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
    assert ui.flag("Namibia") == "🇳🇦" and ui.flag("Eswatini") == "🇸🇿"
    # Northern Ireland: the drawn Ulster Banner in HTML, nothing in plain text.
    assert ui.flag("Northern Ireland") == ""
    assert ui.flag_html("Northern Ireland").startswith('<img src="data:image/svg+xml;base64,')
    assert ui.flag_html("Wales") == ui.flag("Wales")


def test_every_popover_has_a_close_button():
    """Every popover goes through parkrun_ui.closable_popover, so each gets the
    phone's Close button (docs/STYLE.md § Phones)."""
    offenders = _calls_outside_ui("popover")
    assert not offenders, f"use parkrun_ui.closable_popover: {offenders}"


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


def _run_app(monkeypatch):
    """The main app, run once against the committed snapshot."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("PARKRUN_DB", str(parkrun_core.SNAPSHOT))
    at = AppTest.from_file(str(REPO / "parkrun_app.py"), default_timeout=180)
    at.run()
    return at


@pytest.mark.skipif(not parkrun_core.SNAPSHOT.exists(), reason="no snapshot")
def test_app_runs_and_year_filters_take_several_years(monkeypatch):
    at = _run_app(monkeypatch)
    assert not at.exception
    assert len(at.tabs) == 6

    at.multiselect(key="t4_year").set_value(["2024", "2025"]).run()
    assert not at.exception
    # Choosing a season clears the years: the two stay mutually exclusive.
    season = at.multiselect(key="t4_season").options[0]
    at.multiselect(key="t4_season").set_value([season]).run()
    assert at.multiselect(key="t4_year").value == []

    # Tab 5 opens on the planner. The head-to-head view has only the
    # classification/year/season row; the planner has none of those.
    assert at.session_state["t5_view"] == "Planner"
    at.session_state["t5_view"] = "Head-to-heads"
    at.run()
    at.selectbox(key="t5_class").set_value(
        at.selectbox(key="t5_class").options[1]).run()
    assert not at.exception
    at.session_state["t5_view"] = "Planner"
    at.run()
    assert not at.exception
    keys = {w.key for w in at.selectbox} | {w.key for w in at.multiselect}
    assert "t5_class" not in keys and "t5_year" not in keys

    # The planner opens on parkruns nobody has run, ranked by George's and
    # Duncan's drives (when the snapshot carries drive times).
    for name in ("George", "Raju", "Duncan"):
        assert at.session_state[f"t5_done_{name}"] == "Not done"
    has_travel = "t5_rank_by" in {w.key for w in at.multiselect}
    if has_travel:
        assert at.multiselect(key="t5_rank_by").value == ["George", "Duncan"]
        # Each range row sits in the container RANGE_ROW_CSS keeps on one
        # line on a phone, and that CSS is on the page.
        import where_next
        found = _find_block(at, "rng-t5_min_George")
        assert found is not None and "t5_min_George_rng" in _keys_under(found)
        assert any(where_next.RANGE_ROW_CSS in m.value for m in at.markdown)

    at.multiselect(key="t5_countries").set_value(["United Kingdom"]).run()
    at.session_state["t5_exclude"] = [1]           # Bushy Park (event ids)
    at.session_state["t5_done_Raju"] = "Done"
    at.session_state["t5_h2h_George vs Raju"] = "Happened"
    if has_travel:
        at.session_state["t5_units"] = "km"
        at.session_state["t5_rank_metric"] = "Driving distance"
        at.multiselect(key="t5_rank_by").set_value(["Raju"])
    at.run()
    assert not at.exception
    # The panel is a draft: nothing reaches the map until Apply filters.
    assert at.session_state["t5_state_applied"]["filters"]["countries"] == []
    assert at.session_state["t5_state_applied"]["filters"]["done_filter"]["Raju"] == "Not done"
    at.button(key="t5_apply").click().run()
    assert not at.exception
    assert at.session_state["t5_state_applied"]["filters"]["countries"] == ["United Kingdom"]
    assert at.session_state["t5_state_applied"]["filters"]["exclude"] == [1]
    assert at.session_state["t5_state_applied"]["filters"]["done_filter"]["Raju"] == "Done"

    # Clear all filters: the neutral setting, not the opening one — units
    # and rank metric included, the view untouched. A clear applies at once.
    at.button(key="t5_clear").click().run()
    assert not at.exception
    assert at.session_state["t5_state_applied"]["filters"]["countries"] == []
    assert at.session_state["t5_state_applied"]["filters"]["done_filter"]["Raju"] == "Any"
    assert at.multiselect(key="t5_countries").value == []
    assert at.multiselect(key="t5_exclude").value == []
    for name in ("George", "Raju", "Duncan"):
        assert at.session_state[f"t5_done_{name}"] == "Any"
    assert at.session_state["t5_h2h_George vs Raju"] == "Any"
    if has_travel:
        assert at.session_state["t5_units"] == "miles"
        assert at.session_state["t5_rank_metric"] == "Driving time"
        assert at.multiselect(key="t5_rank_by").value == ["George", "Raju", "Duncan"]
    assert at.session_state["t5_view"] == "Planner"


def _find_block(at, key_suffix):
    """The block element whose id ends in `key_suffix` (a container key)."""
    stack = [at._tree]
    while stack:
        node = stack.pop()
        pid = getattr(getattr(node, "proto", None), "id", "") or ""
        if pid.endswith(key_suffix):
            return node
        stack.extend(getattr(node, "children", {}).values())
    return None


def _keys_under(node) -> set:
    out, stack = set(), [node]
    while stack:
        n = stack.pop()
        if getattr(n, "key", None):
            out.add(n.key)
        stack.extend(getattr(n, "children", {}).values())
    return out


@pytest.mark.parametrize("app", ["http://localhost:8501/",
                                 "https://parkrun-and-brunch.streamlit.app/~/+/"])
def test_manifest_start_url_opens_the_site_root(app):
    """A relative start_url resolves against the manifest's own address, not
    the page's: "." sent an Android install to /app/static/, a 404."""
    import json
    from urllib.parse import urljoin, urlsplit
    manifest = json.loads((REPO / "static" / "manifest.json").read_text())
    url = urljoin(urljoin(app, "app/static/manifest.json"), manifest["start_url"])
    assert urlsplit(url).path == "/"


def test_years_desc_is_newest_first_and_keeps_type():
    assert ui.years_desc([2019, 2025, 2019, 2023]) == [2025, 2023, 2019]
    assert ui.years_desc(["2019", "2025"]) == ["2025", "2019"]


@pytest.mark.skipif(not parkrun_core.SNAPSHOT.exists(), reason="no snapshot")
def test_filters_survive_being_off_screen(monkeypatch):
    """Review finding: Streamlit drops an undrawn widget's state, so hiding
    tab 3's picker or flipping tab 5's view used to reset the filters."""
    at = _run_app(monkeypatch)

    # Every year filter lists newest first (tab 5's sits in its
    # head-to-head view, so show that view first).
    at.session_state["t5_view"] = "Head-to-heads"
    at.run()
    for key in ("t2_year", "t3_year", "t4_year", "t5_year"):
        opts = [int(o) for o in at.multiselect(key=key).options]
        assert opts == sorted(opts, reverse=True), key

    # Tab 5: set a planner filter, go to the head-to-head view and back.
    at.session_state["t5_view"] = "Planner"
    at.run()
    at.session_state["t5_done_Raju"] = "Done"     # not the default
    at.run()
    at.session_state["t5_view"] = "Head-to-heads"
    at.run()
    at.session_state["t5_view"] = "Planner"
    at.run()
    assert not at.exception
    assert at.session_state["t5_done_Raju"] == "Done"

    # Tab 3: choose a year, hide the picker, run again.
    year = at.multiselect(key="t3_year").options[-1]
    at.multiselect(key="t3_year").set_value([year]).run()
    at.button(key="btn_sec_t3_pick").click().run()
    assert at.session_state["sec_t3_pick"] is False      # the picker is hidden
    at.run()
    assert not at.exception
    assert at.session_state["t3_year"] == [year]

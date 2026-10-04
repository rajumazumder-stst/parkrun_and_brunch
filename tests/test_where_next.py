"""Tab 5 (where they meet) — the planner, the done markers, the mainland rule and the travel
top-ups.

No app runtime, no project database and no network: every test builds its own
frames, and the routing adapters are driven with a fake HTTP session.
"""
from __future__ import annotations

import re

import pandas as pd
import pytest

import parkrun_core
import parkrun_pipeline
import parkrun_travel as tr
import where_next as wn

GEORGE, RAJU, DUNCAN = "George", "Raju", "Duncan"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def events():
    """Four mainland GB parkruns, one in Belfast, one in Jersey, one abroad,
    one defunct and one junior."""
    rows = [
        (1, "Bushy Park", 51.41, -0.34, True, 1, 97, "United Kingdom"),
        (2, "Richmond Park", 51.44, -0.28, True, 1, 97, "United Kingdom"),
        (3, "Finsbury Park", 51.57, -0.10, True, 1, 97, "United Kingdom"),
        (4, "Edinburgh", 55.95, -3.20, True, 1, 97, "United Kingdom"),
        (5, "Belfast Victoria", 54.61, -5.88, True, 1, 97, "United Kingdom"),
        (6, "Jersey", 49.20, -2.20, True, 1, 97, "United Kingdom"),
        (7, "Albert Melbourne", -37.84, 144.97, True, 1, 3, "Australia"),
        (8, "Victoria Dock", 51.51, 0.02, False, 1, 97, "United Kingdom"),
        (9, "Bushy juniors", 51.41, -0.34, True, 2, 97, "United Kingdom"),
    ]
    return pd.DataFrame(rows, columns=["event_id", "short_name", "latitude",
                                       "longitude", "live", "seriesid",
                                       "country_code", "country_name"])


def done():
    rows = [
        (GEORGE, 1, 12, "2026-06-14"),
        (RAJU, 1, 3, "2025-01-04"),
        (RAJU, 2, 40, "2026-09-26"),
        (DUNCAN, 3, 1, "2024-03-02"),
        (DUNCAN, 7, 1, "2019-12-28"),
    ]
    df = pd.DataFrame(rows, columns=["athlete_name", "event_id", "n_runs", "last_run"])
    df["last_run"] = pd.to_datetime(df["last_run"])
    return df


def travel():
    """Minutes and km from each home, for the four mainland events."""
    mins = {GEORGE: {1: 20, 2: 25, 3: 60, 4: 460},
            RAJU: {1: 15, 2: 20, 3: 55, 4: 480},
            DUNCAN: {1: 55, 2: 45, 3: 10, 4: 465}}
    rows = [(a, e, m * 60.0, m * 1000.0)
            for a, per in mins.items() for e, m in per.items()]
    return pd.DataFrame(rows, columns=["athlete_name", "event_id",
                                       "duration_s", "distance_m"])


ANY = {GEORGE: "Any", RAJU: "Any", DUNCAN: "Any"}


# --------------------------------------------------------------------------- #
# Mainland Great Britain
# --------------------------------------------------------------------------- #
MAINLAND_CASES = [pytest.param(*c, id=c[0]) for c in [
    ("Bushy Park", 51.411, -0.336, True),
    ("Lymington Woodside", 50.748, -1.545, True),   # beside the Isle of Wight
    ("Southsea", 50.785, -1.046, True),
    ("Newborough Forest (Anglesey)", 53.144, -4.387, True),
    ("Skinadin (Skye)", 57.259, -5.941, True),
    ("West Bay, Dunoon", 55.945, -4.924, True),     # beside Bute
    ("Crinan Canal", 56.042, -5.441, True),         # beside Jura
    ("Ganavan Sands (Oban)", 56.438, -5.469, True), # beside Mull
    ("Thurso", 58.59, -3.522, True),                # beside Orkney
    ("Land's End", 50.066, -5.714, True),
    ("Medina I.O.W.", 50.726, -1.143, False),
    ("Mount Stuart (Bute)", 55.793, -5.019, False),
    ("Lews Castle", 58.208, -6.397, False),
    ("Kirkwall", 58.982, -2.948, False),
    ("Bressay", 60.158, -1.12, False),
    ("Nobles (Isle of Man)", 54.166, -4.481, False),
    ("Jersey", 49.195, -2.204, False),
    ("Guernsey", 49.502, -2.53, False),
    ("Belfast Victoria", 54.609, -5.882, False),
    ("Portrush", 55.205, -6.649, False),
    ("Gibraltar Botanical Gardens", 36.132, -5.351, False),
    ("Cape Pembroke Lighthouse (Falklands)", -51.683, -57.758, False),
]]


@pytest.mark.parametrize("name, lat, lon, mainland", MAINLAND_CASES)
def test_is_mainland(name, lat, lon, mainland):
    assert parkrun_core.is_mainland(lat, lon) is mainland, name


def test_is_mainland_rejects_missing_coordinates():
    assert not parkrun_core.is_mainland(None, -0.1)
    assert not parkrun_core.is_mainland(float("nan"), -0.1)


@pytest.mark.parametrize("name, cc, lat, lon, routable, crossing", [
    ("Bushy Park", 97, 51.411, -0.336, True, False),
    ("Belfast Victoria", 97, 54.609, -5.882, True, True),
    ("Jersey", 97, 49.195, -2.204, True, True),
    ("Gibraltar Botanical Gardens", 97, 36.132, -5.351, True, True),
    ("Dublin, Marlay (inside GB_EXTENT)", 64, 53.27, -6.27, True, True),
    ("Zuiderpark, Den Haag", 74, 52.06, 4.28, True, True),
    ("Albert Melbourne", 3, -37.84, 144.97, False, True),
    ("Cape Pembroke Lighthouse (Falklands)", 97, -51.683, -57.758, False, True),
])
def test_routable_and_crosses_water(name, cc, lat, lon, routable, crossing):
    assert parkrun_core.routable(cc, lat, lon) is routable, name
    assert parkrun_core.crosses_water(cc, lat, lon) is crossing, name


def test_routable_rejects_missing_coordinates():
    assert not parkrun_core.routable(97, None, -0.1)
    assert not parkrun_core.routable(97, float("nan"), -0.1)


def test_travel_routes_every_road_reachable_live_5k():
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE SCHEMA parkrun")
    ev = events()
    ev.loc[len(ev)] = (10, "Marlay", 53.27, -6.27, True, 1, 64, "Ireland")
    con.register("ev", ev)
    con.execute("CREATE TABLE parkrun.events AS SELECT * FROM ev")
    got = tr.candidate_events(con)
    assert list(got.columns) == ["event_id", "latitude", "longitude"]
    # Mainland, Belfast, Jersey and Dublin; not Melbourne, not defunct, not junior.
    assert sorted(got["event_id"]) == [1, 2, 3, 4, 5, 6, 10]


# --------------------------------------------------------------------------- #
# plan_candidates
# --------------------------------------------------------------------------- #
def ids(df):
    return df["event_id"].tolist()


def test_candidates_are_every_live_5k_with_crossings_flagged():
    c = wn.plan_candidates(events(), done(), None, done_filter=ANY)
    assert sorted(ids(c)) == [1, 2, 3, 4, 5, 6, 7]     # not defunct, not junior
    assert set(c.loc[c["crossing"], "event_id"]) == {5, 6, 7}


def test_sea_crossings_can_be_left_out():
    c = wn.plan_candidates(events(), done(), None, done_filter=ANY,
                           include_crossings=False)
    assert sorted(ids(c)) == [1, 2, 3, 4]


def test_without_travel_the_list_is_alphabetical():
    c = wn.plan_candidates(events(), done(), None, done_filter=ANY)
    assert list(c["short_name"]) == sorted(c["short_name"])


def test_done_and_not_done_filters_are_anded():
    c = wn.plan_candidates(events(), done(), None,
                           done_filter={GEORGE: "Not done", RAJU: "Done",
                                        DUNCAN: "Any"})
    assert ids(c) == [2]   # Richmond: Raju yes, George no


def test_nobody_has_done_it():
    c = wn.plan_candidates(events(), done(), None,
                           done_filter={n: "Not done" for n in wn.ATHLETES})
    assert sorted(ids(c)) == [4, 5, 6]     # Edinburgh, Belfast, Jersey


def test_time_range_per_athlete():
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           minutes_range={DUNCAN: (0, 50)}, rank_by=[DUNCAN])
    assert sorted(ids(c)) == [2, 3]


def test_time_range_has_a_lower_end_too():
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           minutes_range={DUNCAN: (40, 60)}, rank_by=[DUNCAN])
    assert sorted(ids(c)) == [1, 2]      # 55 and 45; Finsbury (10) is too close


def test_distance_range_converts_units():
    # 25 km from George = Bushy (20 km) and Richmond (25 km) — in km.
    km = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                            distance_range={GEORGE: (0, 25)}, units="km")
    assert sorted(ids(km)) == [1, 2]
    # 15 miles is 24.1 km, which drops Richmond.
    mi = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                            distance_range={GEORGE: (0, 15)}, units="miles")
    assert ids(mi) == [1]


def h2h():
    """George vs Raju twice at Bushy (one occasion has two athlete rows each),
    and a three-way once at Finsbury."""
    rows = [(1, "2025-01-04", "George vs Raju"), (1, "2025-01-04", "George vs Raju"),
            (1, "2025-02-01", "George vs Raju"),
            (3, "2025-03-01", "Duncan vs George vs Raju")]
    return pd.DataFrame(rows, columns=["event_id", "run_date", "classification"])


def test_h2h_counts_are_occasions_not_rows():
    hc = wn.h2h_counts(h2h())
    got = {(r.event_id, r.classification): r.n for r in hc.itertuples()}
    assert got == {(1, "George vs Raju"): 2, (3, "Duncan vs George vs Raju"): 1}


def test_h2h_index_lists_the_most_frequent_classification_first():
    hc = pd.DataFrame({"event_id": [1, 1, 3], "n": [1, 4, 2],
                       "classification": ["Duncan vs George", "George vs Raju",
                                          "George vs Raju"]})
    assert wn.h2h_index(hc) == {1: [("George vs Raju", 4), ("Duncan vs George", 1)],
                                3: [("George vs Raju", 2)]}


def test_h2h_filter_happened_and_never():
    hc = wn.h2h_counts(h2h())
    yes = wn.plan_candidates(events(), done(), None, done_filter=ANY, h2h=hc,
                             h2h_filter={"George vs Raju": "Happened"})
    assert ids(yes) == [1]
    no = wn.plan_candidates(events(), done(), None, done_filter=ANY, h2h=hc,
                            h2h_filter={"George vs Raju": "Never",
                                        "Duncan vs George vs Raju": "Never"})
    assert sorted(ids(no)) == [2, 4, 5, 6, 7]


def test_regular_parkruns_keeps_a_defunct_one_somebody_ran():
    d = pd.concat([done(), pd.DataFrame(
        [(RAJU, 8, 1, pd.Timestamp("2019-01-05"))],
        columns=["athlete_name", "event_id", "n_runs", "last_run"])])
    got = set(wn.regular_parkruns(events(), d)["event_id"])
    assert 8 in got and 9 not in got              # juniors never
    assert 8 not in set(wn.regular_parkruns(events(), done())["event_id"])


def test_rank_by_total_drive_of_the_chosen_athletes():
    everyone = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                                  rank_by=wn.ATHLETES)
    assert ids(everyone)[-1] == 4            # Edinburgh: furthest for all three
    duncan = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                                rank_by=[DUNCAN])
    assert ids(duncan)[0] == 3               # Finsbury: 10 min for Duncan
    assert duncan["total_min"].iloc[0] == 10


def test_rank_ties_break_alphabetically():
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           rank_by=wn.ATHLETES)
    # Bushy 20+15+55 and Richmond 25+20+45 are both 90 minutes.
    assert c["total_min"].iloc[0] == c["total_min"].iloc[1] == 90
    assert list(c["short_name"].iloc[:2]) == ["Bushy Park", "Richmond Park"]


def test_exclusions_are_dropped():
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           rank_by=wn.ATHLETES, exclude=[1, 2])
    assert ids(c) == [3, 4]


def test_an_event_without_a_route_is_dropped_when_ranked():
    t = travel()
    t = t[~((t["athlete_name"] == DUNCAN) & (t["event_id"] == 4))]
    c = wn.plan_candidates(events(), done(), t, done_filter=ANY,
                           rank_by=wn.ATHLETES)
    assert 4 not in ids(c)


# --------------------------------------------------------------------------- #
# Done table, country filter, bounds
# --------------------------------------------------------------------------- #
def test_done_table_counts_and_last_visit():
    dt = wn.done_table(done())
    assert dt.loc[1, GEORGE] == 12 and dt.loc[1, RAJU] == 3 and dt.loc[1, DUNCAN] == 0
    assert dt.loc[2, f"last_{RAJU}"] == pd.Timestamp("2026-09-26")
    assert pd.isna(dt.loc[2, f"last_{GEORGE}"])


def test_country_filter_empty_means_all():
    ev = events()
    assert len(wn.filter_countries(ev, [])) == len(ev)
    assert set(wn.filter_countries(ev, ["Australia"])["event_id"]) == {7}


def _with_children():
    ev = events()
    child = {4: "Scotland", 5: "Northern Ireland", 6: "Jersey"}
    ev["child_country"] = [child.get(e, "England" if c == 97 else n)
                           for e, c, n in zip(ev["event_id"], ev["country_code"],
                                              ev["country_name"])]
    za = pd.DataFrame([(10, "Windhoek", -22.56, 17.08, True, 1, 85, "South Africa", "Namibia"),
                       (11, "Delta", -26.15, 28.0, True, 1, 85, "South Africa", "South Africa")],
                      columns=ev.columns)
    return pd.concat([ev, za], ignore_index=True)


def test_country_options_nest_children_under_their_parent():
    opts = wn.country_options(_with_children())
    keys = list(opts)
    assert keys[:1] == ["Australia"]                   # no children of its own
    assert keys.index("South Africa") < keys.index("South Africa › Namibia")
    assert "South Africa › South Africa" in opts       # the parent's own child
    assert keys.index("United Kingdom") < keys.index("United Kingdom › England")
    assert "Australia › Australia" not in opts
    assert opts["United Kingdom"] == "🇬🇧 United Kingdom (8)"
    assert opts["United Kingdom › Northern Ireland"] == "\u2003Northern Ireland (1)"
    assert opts["United Kingdom › Scotland"].endswith("Scotland (1)")


def test_country_filter_parent_takes_in_its_children():
    ev = _with_children()
    ids = lambda sel: set(wn.filter_countries(ev, sel)["event_id"])
    assert ids(["South Africa"]) == {10, 11}
    assert ids(["South Africa › South Africa"]) == {11}
    assert ids(["United Kingdom › Scotland", "Australia"]) == {4, 7}
    assert ids(["United Kingdom", "United Kingdom › Jersey"]) == ids(["United Kingdom"])


def test_results_table_draws_the_child_countrys_flag():
    ev = _with_children()
    c = wn.plan_candidates(ev, done(), None, done_filter=ANY)
    t = wn._results_table(c, "miles")
    html = wn.results_table_html(t)
    by_name = dict(zip(c["short_name"], t.text("Country")))   # ⛴ aside
    assert by_name["Belfast Victoria"].startswith("<img")       # Ulster Banner
    assert by_name["Windhoek"] == "🇳🇦" and by_name["Albert Melbourne"] == "🇦🇺"
    assert 'title="Northern Ireland"' in html and "&lt;img" not in html


def test_tip_names_the_child_country():
    ev = _with_children().set_index("event_id")
    row = pd.Series({"event_id": 4, "short_name": "Edinburgh",
                     "country_name": "United Kingdom",
                     "child_country": ev.loc[4, "child_country"]})
    assert "Scotland" in wn._title(row) and "United Kingdom" not in wn._title(row)


def test_new_parkruns_take_the_nearest_mapped_parkruns_child(tmp_path, monkeypatch):
    con = _empty_db()
    parkrun_pipeline.ensure_schema(con)
    con.execute("""INSERT INTO parkrun.events (event_id, short_name, country_code,
                   latitude, longitude, seriesid) VALUES
        (1, 'Bute Park', 97, 51.49, -3.19, 1), (2, 'Bushy Park', 97, 51.41, -0.34, 1),
        (3, 'New Cardiff', 97, 51.47, -3.17, 1), (4, 'New Sydney', 3, -33.8, 151.2, 1)""")
    (tmp_path / "event_countries.csv").write_text(
        "event_id,child_country,placed_by\n1,Wales,boundary\n2,England,boundary\n")
    monkeypatch.setattr(parkrun_pipeline, "DATA_DIR", tmp_path)
    for _ in range(2):                                  # idempotent
        parkrun_pipeline.apply_event_countries(con)
    rows = dict(con.execute("SELECT event_id, child_country || '/' || placed_by "
                            "FROM parkrun.event_countries").fetchall())
    assert rows == {1: "Wales/boundary", 2: "England/boundary",
                    3: "Wales/nearest-parkrun"}       # Sydney: no children


@pytest.mark.parametrize("width", [390, 1200])
def test_the_opening_view_holds_all_of_mainland_gb(width):
    (s, w), (n, e) = wn.view_bounds(wn.GB_CENTER, wn.GB_ZOOM, width, wn.MAP_HEIGHT)
    (gs, gw), (gn, ge) = wn.GB_MAINLAND_BOUNDS
    assert s < gs and n > gn and w < gw and e > ge
    # ...and every mainland fixture point sits inside that box.
    for _, lat, lon, mainland in [p.values for p in MAINLAND_CASES]:
        if mainland:
            assert gs <= lat <= gn and gw <= lon <= ge
    # Not a zoom further out than needed: zoom 6 would not fit a phone.
    (s6, _), (n6, _) = wn.view_bounds(wn.GB_CENTER, wn.GB_ZOOM + 1, 390,
                                      wn.MAP_HEIGHT)
    assert not (s6 < gs and n6 > gn)


def test_mainland_ids_leave_out_every_crossing():
    assert wn.mainland_ids(events()) == {1, 2, 3, 4, 8, 9}


def test_the_map_opens_fitted_to_the_opening_markers_on_mainland_gb():
    ev, d = events(), done()
    pr = wn.regular_parkruns(ev, d)
    t = travel()
    t.loc[len(t)] = (GEORGE, 5, 10 * 60.0, 9_000.0)      # Belfast, very near
    cands = wn.plan_candidates(ev, d, t, done_filter=ANY, rank_by=[GEORGE])
    fmap, counted = wn.build_planner_map(parkruns=pr, dt=wn.done_table(d),
                                         candidates=cands, h2h_by_event={})
    pts = wn.opening_points(fmap, counted)
    assert set(pts["event_id"]) == {1, 2, 3, 4, 5}       # the top layer only
    # Belfast is a recommendation but off the mainland: it does not widen it.
    (s, w), (n, e) = wn.opening_bounds(pts, wn.mainland_ids(ev))
    assert (s, n) == (51.41, 55.95) and (w, e) == (-3.20, -0.10)


def test_with_no_opening_marker_on_the_mainland_the_map_fits_gb():
    pts = pd.DataFrame({"event_id": [5], "latitude": [54.61], "longitude": [-5.88]})
    assert wn.opening_bounds(pts, wn.mainland_ids(events())) == wn.GB_MAINLAND_BOUNDS


def test_the_fit_waits_for_the_map_to_have_a_size():
    fmap = wn._base_map()
    wn.FitWhenSized([[51.4, -3.2], [55.95, -0.1]]).add_to(fmap)
    html = fmap.get_root().render()
    assert "fitBounds([[51.4,-3.2],[55.95,-0.1]]" in html
    assert "clientWidth) { setTimeout(fit, 200)" in html
    assert f"maxZoom: {wn.FIT_MAX_ZOOM}" in html


# --------------------------------------------------------------------------- #
# Markers — a square per athlete, fixed order
# --------------------------------------------------------------------------- #
def _squares(svg):
    return re.findall(r'class="sq" data-athlete="(\w+)" x="([\d.]+)" y="([\d.]+)"'
                      r'[^>]*fill="([^"]+)"', svg)


def test_lamps_mark_each_athlete_in_fixed_order():
    svg, *_ = wn.done_marker({GEORGE: True, RAJU: False, DUNCAN: True})
    sq = _squares(svg)
    assert [a for a, *_ in sq] == wn.ATHLETES
    fills = {a: f for a, _, _, f in sq}
    assert fills[GEORGE] == wn.ATHLETE_COLORS[GEORGE]
    assert fills[DUNCAN] == wn.ATHLETE_COLORS[DUNCAN]
    assert fills[RAJU] == wn.UNLIT
    xs = [float(x) for _, x, _, _ in sq]
    ys = [float(y) for _, _, y, _ in sq]
    assert xs == sorted(xs) and len(set(ys)) == 1     # a row, left to right


def test_lamps_sit_in_a_housing_padded_round_them():
    svg, w, h, *_ = wn.done_marker({})
    assert wn.HOUSING in svg
    assert (w, h) == (3 * wn.SQ + 2 * wn.SQ_GAP + 2 * wn.SQ_PAD,
                      wn.SQ + 2 * wn.SQ_PAD)


@pytest.mark.parametrize("rank", [3, 12])
def test_a_ranked_done_parkrun_keeps_its_lamps_on_the_location(rank):
    flags = {GEORGE: True}
    svg, w, h, ax, ay = wn.done_marker(flags, rank)
    assert f">{rank}</text>" in svg
    # In the housing: the number sits left of every lamp.
    num_x = float(re.search(r'class="rank" x="([\d.]+)"', svg).group(1))
    assert [a for a, *_ in _squares(svg)] == wn.ATHLETES
    # The anchor is the centre of the lamps, wherever the number went.
    sq = _squares(svg)
    xs = [float(x) for _, x, _, _ in sq]
    ys = [float(y) for _, _, y, _ in sq]
    assert (min(xs) + max(xs) + wn.SQ) / 2 == pytest.approx(ax)
    assert ys[0] + wn.SQ / 2 == pytest.approx(ay)
    assert num_x < min(xs)


def test_unranked_done_parkrun_is_plain_lamps():
    svg, *_ = wn.done_marker({RAJU: True})
    assert "rank" not in svg and wn.HOUSING in svg


def test_open_marker_is_a_black_circle_numbered_when_ranked():
    plain, d, *_ = wn.open_marker()
    ranked, D, *_ = wn.open_marker(4)
    assert "<circle" in plain and "text" not in plain
    assert ">4</text>" in ranked and D > d


def _layer(counted, name):
    return {e for e, *_ in counted.get(name, [])}


def test_planner_map_layers_overlap_by_what_each_parkrun_is():
    ev = events()
    dt = wn.done_table(done())
    pr = wn.regular_parkruns(ev, done())
    cands = wn.plan_candidates(ev, done(), travel(), done_filter=ANY,
                               rank_by=wn.ATHLETES)
    fmap, counted = wn.build_planner_map(parkruns=pr, dt=dt, candidates=cands,
                                         h2h_by_event={})
    assert "pr-inview" in fmap.get_root().render()
    top = _layer(counted, wn.LAYER_TOP)
    never = _layer(counted, wn.LAYER_NOT_DONE)
    by = {n: _layer(counted, wn.layer_done_by(n)) for n in wn.ATHLETES}
    assert top == set(cands["event_id"])     # 4 matches, all in the top 25
    # Each athlete's layer is exactly what they have run...
    for n in wn.ATHLETES:
        assert by[n] == set(dt.index[dt[n] > 0]) & set(pr["event_id"]), n
    done_any = set().union(*by.values())
    # ...and done / not done partition the parkruns, the top layer on top.
    assert done_any | never == set(pr["event_id"]) and not done_any & never
    assert top & never and top & done_any    # a recommendation is both


def test_planner_map_draws_each_parkrun_in_its_kind_once_per_athlete():
    pr = wn.regular_parkruns(events(), done())
    fmap, counted = wn.build_planner_map(parkruns=pr, dt=wn.done_table(done()),
                                         candidates=None, h2h_by_event={})
    assert wn.LAYER_TOP not in counted           # nothing to recommend yet
    for name, pts in counted.items():
        assert len(pts) == len({e for e, *_ in pts}), name


def test_maps_carry_the_phone_sheet_and_a_closable_layer_tree():
    pr = wn.regular_parkruns(events(), done())
    fmap, _ = wn.build_planner_map(parkruns=pr, dt=wn.done_table(done()),
                                   candidates=None, h2h_by_event={})
    html = fmap.get_root().render()
    assert "map-sheet" in html and "window.prSheet" in html
    assert ".collapse()" in html and "pr-layers-close" in html
    # A tree, open on a wide map, with the done layers under one parent.
    assert "L.control.layers.tree(" in html and '"collapsed": false' in html
    assert re.search(r"var tree_layer_control_\w+ = L\.control\.layers\.tree", html)
    assert '"selectAllCheckbox": true' in html
    # One background map, outside the layer box: no base tree.
    assert re.search(r"L\.control\.layers\.tree\(\s*null", html)


def test_the_sheet_uses_the_calendar_theme_colours():
    import parkrun_calendar
    t = parkrun_calendar.theme()
    html = wn._base_map().get_root().render()
    assert t.tip_bg in html and t.tip_fg in html


def test_h2h_view_map_uses_browser_markers_and_counts_venues():
    venues = [(1, 51.41, -0.34, 20, wn._pie_svg({GEORGE: 2}, 20), "<b>Bushy</b>")]
    fmap, counted = wn.build_h2h_view_map(venues)
    html = fmap.get_root().render()
    assert "window.prSheet" in html
    assert counted[wn.LAYER_H2H] == [(1, 51.41, -0.34)]
    # No layer box: the pies are the only layer, so it could only hide them.
    assert "L.control.layers" not in html


def test_planner_is_the_default_view():
    assert wn.VIEWS[0] == "Planner"


def test_rank_by_distance_orders_on_total_distance():
    t = travel()
    # Make distance disagree with time for George: Richmond far, Bushy near.
    t.loc[(t.athlete_name == GEORGE) & (t.event_id == 2), "distance_m"] = 90_000.0
    by_time = wn.plan_candidates(events(), done(), t, done_filter=ANY,
                                 rank_by=[GEORGE], rank_metric="time")
    by_dist = wn.plan_candidates(events(), done(), t, done_filter=ANY,
                                 rank_by=[GEORGE], rank_metric="distance")
    assert list(by_time["event_id"][:2]) == [1, 2]       # 20 then 25 min
    assert list(by_dist["event_id"][:3]) == [1, 3, 2]    # 20, 60, 90 km
    assert by_dist["total_dist"].is_monotonic_increasing


def _ranked_row(rank_by):
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           rank_by=rank_by, units="km")
    return next(c[c["event_id"] == 1].itertuples(index=False))


def test_runners_outside_the_ranking_are_grey_and_italic_in_the_hover():
    tip = wn._candidate_tip(1, _ranked_row([RAJU]), "km", {},
                            wn.done_table(done()), [RAJU])
    assert tip.count(wn.NOT_RANKED) == 2                  # George and Duncan
    assert tip.count(wn.GREY_STYLE) == 2
    assert "color:" in wn.GREY_STYLE and "italic" in wn.GREY_STYLE
    raju_line = [l for l in tip.split("<br>") if f"{RAJU} 15 min" in l][0]
    assert wn.GREY_STYLE not in raju_line                 # the ranked one is plain
    assert "(Raju)" in tip                                # totals name whose


def test_hover_distances_are_to_one_decimal_and_name_the_ranked_total():
    tip = wn._candidate_tip(1, _ranked_row(wn.ATHLETES), "km", {},
                            wn.done_table(done()), wn.ATHLETES, "distance")
    assert "20.0 km" in tip
    assert "<b>Total distance (George, Raju, Duncan)" in tip


def test_results_table_greys_runners_outside_the_ranking():
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           rank_by=[RAJU])
    t = wn._results_table(c, "km", [RAJU])
    assert "George time (not ranked)" in t.columns and "Raju time" in t.columns
    assert set(t.greyed) == {"George time (not ranked)", "George km (not ranked)",
                             "Duncan time (not ranked)", "Duncan km (not ranked)"}
    html = wn.results_table_html(t)
    # Grey AND italic, as on the map — what st.dataframe could not draw.
    assert html.count(wn.GREY_STYLE) == 4 * (len(c) + 1)     # cells + headers
    assert "italic" in wn.GREY_STYLE


@pytest.mark.parametrize("metric, first", [("time", "Total time (ranked)"),
                                           ("distance", "Total km (ranked)")])
def test_results_table_leads_with_the_total_it_ranked_on_in_bold(metric, first):
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           rank_by=wn.ATHLETES, units="km", rank_metric=metric)
    t = wn._results_table(c, "km", wn.ATHLETES, metric)
    assert t.columns[:4] == ["#", "parkrun", "Country", first]
    assert t.bold == [first]
    assert t.greyed == []
    html = wn.results_table_html(t)
    assert "font-weight:700" in html


def test_results_table_sorts_numbers_as_numbers():
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           rank_by=wn.ATHLETES)
    html = wn.results_table_html(wn._results_table(c, "km", wn.ATHLETES))
    assert 'data-num="1"' in html and "localeCompare" in html
    assert wn._sort_key(float("nan")) == "" and wn._sort_key(7) == "7.0"


def test_results_table_has_a_flag_column_named_in_its_tooltip():
    c = wn.plan_candidates(events(), done(), None, done_filter=ANY)
    t = wn._results_table(c, "km")
    assert t.columns[:2] == ["parkrun", "Country"]
    flags = dict(zip(c["short_name"], t.text("Country")))
    assert flags["Bushy Park"] == "🇬🇧" and flags["Albert Melbourne"] == "🇦🇺"
    html = wn.results_table_html(t)
    assert 'title="Australia">🇦🇺</td>' in html


def test_big_numbers_get_a_thousands_comma_in_the_table_and_hover():
    t = travel()
    t.loc[t.event_id == 4, "distance_m"] = 1_500_000.0       # Edinburgh, 1,500 km
    c = wn.plan_candidates(events(), done(), t, done_filter=ANY,
                           rank_by=wn.ATHLETES, units="km")
    tbl = wn._results_table(c, "km", wn.ATHLETES)
    assert "1,500.0" in tbl.text("George km")
    assert "4,500.0" in tbl.text("Total km")      # ranked on time: km is second
    row = next(c[c["event_id"] == 4].itertuples(index=False))
    tip = wn._candidate_tip(4, row, "km", {}, wn.done_table(done()), wn.ATHLETES)
    assert "1,500.0 km" in tip and "4,500.0 km" in tip


def test_the_corner_counter_writes_thousands_commas():
    pr = wn.regular_parkruns(events(), done())
    fmap, _ = wn.build_planner_map(parkruns=pr, dt=wn.done_table(done()),
                                   candidates=None, h2h_by_event={})
    assert "toLocaleString('en-GB')" in fmap.get_root().render()


def test_results_table_escapes_names():
    c = wn.plan_candidates(events(), done(), None, done_filter=ANY)
    c.loc[0, "short_name"] = "<b>x</b> & y"
    html = wn.results_table_html(wn._results_table(c, "km"))
    assert "<b>x" not in html and "&lt;b&gt;x&lt;/b&gt; &amp; y" in html


def test_results_table_distances_keep_one_decimal():
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           rank_by=wn.ATHLETES)
    t = wn._results_table(c, "miles", wn.ATHLETES)
    assert "12.4" in t.text("George mi")    # 20 km to Bushy for George = 12.43 mi


def test_the_map_opens_on_the_recommendations_alone():
    ev, d = events(), done()
    pr = wn.regular_parkruns(ev, d)
    cands = wn.plan_candidates(ev, d, travel(), done_filter=ANY,
                               rank_by=wn.ATHLETES)
    fmap, counted = wn.build_planner_map(parkruns=pr, dt=wn.done_table(d),
                                         candidates=cands, h2h_by_event={})
    assert fmap.pr_shown == {wn.LAYER_TOP: True, wn.LAYER_NOT_DONE: False,
                             **{wn.layer_done_by(n): False for n in wn.ATHLETES}}
    # The corner counter reads what is on the map, not a copy of the switches.
    assert "map.hasLayer(groups[name])" in fmap.get_root().render()


def test_the_map_shows_the_matches_alone_when_nothing_is_ranked():
    ev, d = events(), done()
    cands = wn.plan_candidates(ev, d, None, done_filter={RAJU: "Not done"})
    fmap, _ = wn.build_planner_map(parkruns=wn.regular_parkruns(ev, d),
                                   dt=wn.done_table(d), candidates=cands,
                                   h2h_by_event={})
    assert fmap.pr_shown[wn.LAYER_MATCH] is True
    assert not any(v for k, v in fmap.pr_shown.items() if k != wn.LAYER_MATCH)


def test_with_nothing_picked_out_every_layer_shows():
    ev, d = events(), done()
    fmap, _ = wn.build_planner_map(parkruns=wn.regular_parkruns(ev, d),
                                   dt=wn.done_table(d), candidates=None,
                                   h2h_by_event={})
    assert all(fmap.pr_shown.values())


def test_track_top_rounds_up_and_opens_past_the_cap():
    assert wn.track_top(455.0, wn.TIME_CAP_MIN) == (460, False)
    assert wn.track_top(2100.0, wn.TIME_CAP_MIN) == (720, True)   # Finland
    top, open_top = wn.track_top(1938.0, wn.DIST_CAP["mi"], float)
    assert (top, open_top) == (800.0, True) and isinstance(top, float)


def test_an_open_ended_range_still_keeps_the_far_parkruns():
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           minutes_range={GEORGE: (30, float("inf"))})
    assert 4 in ids(c)                       # Edinburgh, 460 min


def test_crossings_are_marked_in_the_hover_and_the_table():
    t = travel()
    t.loc[len(t)] = (GEORGE, 5, 400 * 60.0, 600_000.0)       # Belfast, by ferry
    c = wn.plan_candidates(events(), done(), t, done_filter=ANY,
                           rank_by=[GEORGE])
    row = next(c[c["event_id"] == 5].itertuples(index=False))
    tip = wn._candidate_tip(5, row, "km", {}, wn.done_table(done()), [GEORGE])
    assert wn.CROSSING in tip and "check-in" in tip
    names = wn._results_table(c, "km", [GEORGE]).text("parkrun")
    assert f"Belfast Victoria {wn.CROSSING}" in names
    assert "Bushy Park" in names                       # mainland: no mark
    bushy = next(c[c["event_id"] == 1].itertuples(index=False))
    assert wn.CROSSING not in wn._candidate_tip(
        1, bushy, "km", {}, wn.done_table(done()), [GEORGE])


def test_reseed_range_keeps_floats_for_distance():
    assert wn.reseed_range(None, None, 676.0) == (0.0, 676.0)
    lo, hi = wn.reseed_range((1, 12), 676.0, 676.0)
    assert (lo, hi) == (1.0, 12.0) and isinstance(lo, float)


def test_a_name_cannot_close_the_script_tag():
    assert "</script>" not in wn._js(["</script><b>x"])


def test_tooltip_names_head_to_heads():
    by = wn.h2h_index(wn.h2h_counts(h2h()))
    assert "<b>2</b> (George vs Raju 2)" in wn._h2h_line(1, by)
    assert wn._h2h_line(2, by) is None


def _tip_rows():
    ev = events().set_index("event_id")
    def row(eid):
        r = ev.loc[eid]
        return pd.Series({"event_id": eid, "short_name": r["short_name"],
                          "country_name": r["country_name"]})
    return row


def test_tip_for_a_parkrun_nobody_has_run_is_name_and_country_only():
    row = _tip_rows()(4)
    tip = wn._base_tip(row, wn.done_table(done()), {})
    assert tip == wn._title(row)
    assert "Edinburgh" in tip and "United Kingdom" in tip and "<br>" not in tip


def test_tip_lists_only_who_has_run_it():
    tip = wn._base_tip(_tip_rows()(1), wn.done_table(done()), {})
    assert "George — 12 runs, last 14 Jun 2026" in tip
    assert "Raju — 3 runs" in tip
    assert DUNCAN not in tip and "never" not in tip


def test_tip_shows_head_to_heads_only_where_there_were_some():
    by = wn.h2h_index(wn.h2h_counts(h2h()))
    dt = wn.done_table(done())
    assert "Head-to-heads" in wn._base_tip(_tip_rows()(1), dt, by)
    assert "Head-to-heads" not in wn._base_tip(_tip_rows()(2), dt, by)


def test_recommendation_tip_keeps_everyone_s_driving_times():
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           rank_by=wn.ATHLETES)
    row = next(c[c["event_id"] == 4].itertuples(index=False))   # nobody has run it
    tip = wn._candidate_tip(4, row, "km", {}, wn.done_table(done()))
    assert "recommendation #4" in tip
    for name in wn.ATHLETES:
        assert name in tip                    # driving line for each
    assert "run" not in tip.replace("Driving", "")   # no run lines
    assert "Head-to-heads" not in tip


# --------------------------------------------------------------------------- #
# Travel top-ups
# --------------------------------------------------------------------------- #
def cand():
    return pd.DataFrame({"event_id": [1, 2], "latitude": [51.41, 51.44],
                         "longitude": [-0.34, -0.28]})


def existing(lat2=51.44, provider="ors"):
    return pd.DataFrame({"athlete_id": [5672, 5672], "event_id": [1, 2],
                         "provider": [provider, provider],
                         "dest_lat": [51.41, lat2], "dest_lon": [-0.34, -0.28]})


def test_routed_pairs_are_not_routed_again():
    assert tr.pairs_to_route(cand(), existing(), [5672], "ors").empty


def test_new_athlete_and_new_provider_are_routed():
    assert len(tr.pairs_to_route(cand(), existing(), [5672, 3087156], "ors")) == 2
    assert len(tr.pairs_to_route(cand(), existing(), [5672], "other")) == 2


def test_an_event_that_moved_is_rerouted():
    # 0.003° of latitude is ~330 m — over the threshold; 0.001° (~110 m) is not.
    assert tr.pairs_to_route(cand(), existing(lat2=51.443), [5672], "ors")["event_id"].tolist() == [2]
    assert tr.pairs_to_route(cand(), existing(lat2=51.441), [5672], "ors").empty


def test_force_reroutes_everything():
    assert len(tr.pairs_to_route(cand(), existing(), [5672], "ors", force=True)) == 2


class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(self.status_code)


class FakeSession:
    """Stands in for ORS's matrix endpoint: 60 s and 1 km per place in the
    chunk, and no route to the chunk's last destination."""

    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        n = len(json["destinations"])
        self.calls.append((n, headers["Authorization"]))
        durs = [60.0 * (i + 1) for i in range(n)]
        durs[-1] = None
        return FakeResponse({"durations": [durs], "distances": [[1000.0] * n]})


def test_ors_chunks_the_destinations_and_keeps_order():
    s = FakeSession()
    dests = [(51.0 + i / 1000, -0.1) for i in range(tr.ORS_CHUNK + 5)]
    got = tr.route_ors((51.5, -0.12), dests, "k", session=s, sleep=lambda _: None)
    assert s.calls == [(tr.ORS_CHUNK, "k"), (5, "k")]
    assert len(got) == len(dests)
    assert got[0] == (60.0, 1000.0)
    assert got[tr.ORS_CHUNK - 1] is None and got[-1] is None


def test_ors_error_raises_with_the_status():
    class Refusing(FakeSession):
        def post(self, *a, **k):
            return FakeResponse({"error": "quota"}, status=403)
    with pytest.raises(RuntimeError, match="ORS HTTP 403"):
        tr.route_ors((51.5, -0.12), [(51.4, -0.3)], "k", session=Refusing(),
                     sleep=lambda _: None)


def test_there_is_one_router():
    assert tr.PROVIDER == "ors" == wn.TRAVEL_PROVIDER
    assert not hasattr(tr, "route_osrm")


def _empty_db():
    """An in-memory DuckDB with an empty `parkrun` schema."""
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE SCHEMA parkrun")
    return con


def test_no_homes_file_skips_quietly(tmp_path):
    con = _empty_db()
    msgs = []
    assert tr.update_travel_times(con, homes={}, log=msgs.append) == 0
    assert "skipped" in msgs[0]


# --------------------------------------------------------------------------- #
# Privacy contracts
# --------------------------------------------------------------------------- #
def test_travel_times_ships_in_the_snapshot():
    """Shipped since 1 Oct 2026, when the origins became neighbourhood
    centroids. What makes that safe is the next test: no origin column."""
    assert "travel_times" in parkrun_pipeline.SNAPSHOT_TABLES


def test_every_db_gets_the_travel_table():
    """It ships, so seed / snapshot / upload all expect it to exist."""
    import duckdb
    con = duckdb.connect()
    parkrun_pipeline.ensure_schema(con)
    assert con.execute("SELECT count(*) FROM information_schema.tables "
                       "WHERE table_name = 'travel_times'").fetchone()[0] == 1


def test_a_failed_travel_step_never_stops_the_refresh(monkeypatch):
    def boom(con, **kw):
        raise ValueError("bad origin 51.401234,-0.205678")

    monkeypatch.setattr(parkrun_pipeline.parkrun_travel, "update_travel_times", boom)
    msgs = []
    monkeypatch.setattr(parkrun_pipeline, "log", msgs.append)
    parkrun_pipeline.apply_travel_times(None)              # must not raise
    text = " ".join(msgs)
    assert "travel step failed" in text and "ValueError" in text
    assert "51.401234" not in text and "0.205678" not in text


def test_travel_can_be_switched_off(monkeypatch):
    called = []
    monkeypatch.setattr(parkrun_pipeline.parkrun_travel, "update_travel_times",
                        lambda con, **kw: called.append(1))
    monkeypatch.setattr(parkrun_pipeline, "log", lambda m: None)
    monkeypatch.setenv("PARKRUN_TRAVEL", "off")
    parkrun_pipeline.apply_travel_times(None)
    assert not called


def test_the_travel_table_holds_no_origin():
    con = _empty_db()
    tr.ensure_table(con)
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'travel_times'").fetchall()}
    assert not cols & {"home_lat", "home_lon", "origin_lat", "origin_lon",
                       "origin", "home"}


# --------------------------------------------------------------------------- #
# Review fixes (1 Oct 2026)
# --------------------------------------------------------------------------- #
class ErrorSession:
    """ORS refusing a point, with an error body that quotes it — as the real
    service does for an out-of-bounds coordinate."""

    def post(self, url, json=None, headers=None, timeout=None):
        lon, lat = json["locations"][0]
        return FakeResponse({"error": {"code": 6010, "message":
                            f"Source point(s) [0] out of bounds: {lat},{lon}"}},
                            status=404)


def test_an_ors_error_never_carries_the_home_into_the_message():
    with pytest.raises(RuntimeError) as err:
        tr.route_ors((51.401234, -0.205678), [(51.4, -0.3)], "k",
                     session=ErrorSession(), sleep=lambda _: None)
    msg = str(err.value)
    assert "6010" in msg and "404" in msg
    assert "51.40" not in msg and "0.205" not in msg


def test_a_failed_route_logs_no_coordinates(monkeypatch):
    con = _empty_db()
    con.execute("CREATE TABLE parkrun.events (event_id INT, latitude DOUBLE, "
                "longitude DOUBLE, seriesid INT, live BOOLEAN, country_code INT)")
    con.execute("INSERT INTO parkrun.events VALUES (1, 51.41, -0.34, 1, TRUE, 97)")

    def boom(origin, dests, key, **kw):
        raise ConnectionError(f"host unreachable while sending {origin[0]},{origin[1]}")

    monkeypatch.setattr(tr, "route_ors", boom)
    monkeypatch.setattr(tr, "ors_key", lambda: "k")
    msgs = []
    tr.update_travel_times(con, homes={5672: (51.401234, -0.205678)},
                           log=msgs.append)
    text = " ".join(msgs)
    assert "failed" in text and "ConnectionError" in text
    assert "51.401234" not in text and "0.205678" not in text


@pytest.mark.parametrize("row", ["5672,51.4x,-0.2", "5672,95.1234,-0.2", "x,51.4,-0.2"])
def test_a_bad_homes_row_is_reported_by_line_not_by_value(tmp_path, row):
    f = tmp_path / "homes.csv"
    f.write_text("athlete_id,latitude,longitude\n" + row + "\n")
    with pytest.raises(ValueError) as err:
        tr.load_homes(f)
    msg = str(err.value)
    assert "line 2" in msg
    assert "51.4" not in msg and "95.1234" not in msg and "0.2" not in msg
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_scrub_blanks_coordinate_shaped_numbers():
    assert tr.scrub("at 52.123456,-1.654321 now") == "at …,… now"


def test_reseed_range_follows_a_full_track_and_keeps_a_set_one():
    assert wn.reseed_range(None, None, 300) == (0, 300)          # first run
    assert wn.reseed_range((0, 300), 300, 360) == (0, 360)       # full follows
    assert wn.reseed_range((20, 90), 300, 360) == (20, 90)       # set is kept
    assert wn.reseed_range((20, 340), 360, 300) == (20, 300)     # clamped


def test_without_a_ranking_matches_are_an_unnumbered_layer():
    ev = events()
    dt = wn.done_table(done())
    pr = wn.regular_parkruns(ev, done())
    cands = wn.plan_candidates(ev, done(), None, done_filter={RAJU: "Not done"})
    assert "total_min" not in cands
    fmap, counted = wn.build_planner_map(parkruns=pr, dt=dt, candidates=cands,
                                         h2h_by_event={})
    assert wn.LAYER_TOP not in counted
    assert {e for e, *_ in counted[wn.LAYER_MATCH]} == set(cands["event_id"])
    html = fmap.get_root().render()
    assert "recommendation" not in html and 'class=\\u003c' not in html
    assert 'class=\\"rank\\"' not in html
    assert "#" not in wn._results_table(cands, "miles").columns


def test_results_table_rows_line_up_after_a_country_filter():
    c = wn.plan_candidates(events(), done(), travel(), done_filter=ANY,
                           rank_by=wn.ATHLETES)
    c = c[c["event_id"] != c["event_id"].iloc[0]]          # a gap in the index
    t = wn._results_table(c, "km")
    assert t.text("parkrun") == list(c["short_name"])
    assert all(t.text("Run by")) and t.text("#") == [str(i) for i in range(1, len(c) + 1)]


def test_plan_candidates_survives_an_empty_database():
    empty = events().iloc[0:0]
    assert wn.plan_candidates(empty, done(), None, done_filter=ANY).empty


def test_head_to_head_tooltips_escape_names():
    mh = pd.DataFrame([{"event_id": 1, "run_date": "2025-01-04",
                        "athlete_name": "<b>x</b>", "place_rank": 1,
                        "is_buggy": False}])
    coords = pd.DataFrame([{"event_id": 1, "short_name": "Hart & <i>Bosch</i>",
                            "latitude": 51.4, "longitude": -0.3}])
    tip = wn.h2h_venues(mh, coords)[0][5]
    assert "<i>" not in tip and "&amp;" in tip and "&lt;b&gt;x" in tip


def test_js_inlining_has_no_angle_brackets_at_all():
    out = wn._js(["<!--<script>", "</script>"])
    assert "<" not in out
    assert __import__("json").loads(out) == ["<!--<script>", "</script>"]

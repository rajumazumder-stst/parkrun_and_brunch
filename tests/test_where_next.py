"""Tab 7 — the planner, the done markers, the mainland rule and the travel
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
@pytest.mark.parametrize("name, lat, lon, mainland", [
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
])
def test_is_mainland(name, lat, lon, mainland):
    assert parkrun_core.is_mainland(lat, lon) is mainland, name


def test_is_mainland_rejects_missing_coordinates():
    assert not parkrun_core.is_mainland(None, -0.1)
    assert not parkrun_core.is_mainland(float("nan"), -0.1)


# --------------------------------------------------------------------------- #
# plan_candidates
# --------------------------------------------------------------------------- #
def ids(df):
    return df["event_id"].tolist()


def test_candidates_are_live_5k_mainland_only():
    c = wn.plan_candidates(events(), done(), None, done_filter=ANY)
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
    assert ids(c) == [4]


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


def test_h2h_filter_happened_and_never():
    hc = wn.h2h_counts(h2h())
    yes = wn.plan_candidates(events(), done(), None, done_filter=ANY, h2h=hc,
                             h2h_filter={"George vs Raju": "Happened"})
    assert ids(yes) == [1]
    no = wn.plan_candidates(events(), done(), None, done_filter=ANY, h2h=hc,
                            h2h_filter={"George vs Raju": "Never",
                                        "Duncan vs George vs Raju": "Never"})
    assert sorted(ids(no)) == [2, 4]


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


def test_london_bounds_count():
    ev = events()
    inside = ev[wn.in_bounds(ev, wn.LONDON_BOUNDS)]
    assert set(inside["event_id"]) == {1, 2, 3, 8, 9}


# --------------------------------------------------------------------------- #
# Markers — a square per athlete, fixed order
# --------------------------------------------------------------------------- #
def _squares(svg):
    return re.findall(r'class="sq" data-athlete="(\w+)" x="([\d.]+)" y="([\d.]+)"'
                      r'[^>]*fill="([^"]+)"', svg)


STYLES = ["Row", "Column", "Row, lit", "Column, lit"]


@pytest.mark.parametrize("style", STYLES)
def test_squares_mark_each_athlete_in_fixed_order(style):
    sq = _squares(wn.squares_svg({GEORGE: True, RAJU: False, DUNCAN: True}, style))
    assert [a for a, *_ in sq] == wn.ATHLETES
    fills = {a: f for a, _, _, f in sq}
    assert fills[GEORGE] == wn.ATHLETE_COLORS[GEORGE]
    assert fills[DUNCAN] == wn.ATHLETE_COLORS[DUNCAN]
    assert fills[RAJU] == (wn.UNLIT if style.endswith("lit") else wn.EMPTY)
    xs = [float(x) for _, x, _, _ in sq]
    ys = [float(y) for _, _, y, _ in sq]
    if style.startswith("Row"):
        assert xs == sorted(xs) and len(set(ys)) == 1
    else:
        assert ys == sorted(ys) and len(set(xs)) == 1


def test_lit_styles_have_a_housing_and_are_bigger():
    assert wn.HOUSING in wn.squares_svg({}, "Row, lit")
    assert wn.HOUSING not in wn.squares_svg({}, "Row")
    w, h = wn.square_size("Row")
    assert wn.square_size("Row, lit") == (w + 2 * wn.SQ_PAD, h + 2 * wn.SQ_PAD)
    assert wn.square_size("Column") == (h, w)


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


def test_planner_map_splits_done_from_never_done():
    ev = events()
    dt = wn.done_table(done())
    pr = wn.regular_parkruns(ev, done())
    cands = wn.plan_candidates(ev, done(), travel(), done_filter=ANY,
                               rank_by=wn.ATHLETES)
    fmap, counted = wn.build_planner_map(parkruns=pr, dt=dt, candidates=cands,
                                         h2h_by_event={})
    assert "pr-inview" in fmap.get_root().render()
    top = {e for e, *_ in counted[wn.LAYER_TOP]}
    done_ids = {e for e, *_ in counted[wn.LAYER_DONE]}
    never = {e for e, *_ in counted[wn.LAYER_NOT_DONE]}
    assert top == set(cands["event_id"])     # 4 matches, all in the top 25
    assert done_ids == {7}                   # run by someone, not a match
    assert never == set(pr["event_id"]) - top - done_ids
    assert sum(len(v) for v in counted.values()) == len(pr)


def test_planner_map_draws_each_parkrun_once():
    pr = wn.regular_parkruns(events(), done())
    fmap, counted = wn.build_planner_map(parkruns=pr, dt=wn.done_table(done()),
                                         candidates=None, h2h_by_event={})
    assert sum(len(v) for v in counted.values()) == len(pr)
    assert wn.LAYER_TOP not in counted           # nothing to recommend yet


@pytest.mark.parametrize("labels", wn.PHONE_LABELS)
def test_maps_carry_the_phone_label_mode_and_fold_the_layer_box(labels):
    pr = wn.regular_parkruns(events(), done())
    fmap, _ = wn.build_planner_map(parkruns=pr, dt=wn.done_table(done()),
                                   candidates=None, h2h_by_event={},
                                   phone_labels=labels)
    html = fmap.get_root().render()
    assert f"'{wn._LABEL_MODE[labels]}' : 'tooltip'" in html   # touch : hover
    assert ".collapse()" in html and "pr-compact" in html


def test_h2h_view_map_uses_browser_markers_and_counts_venues():
    venues = [(1, 51.41, -0.34, 20, wn._pie_svg({GEORGE: 2}, 20), "<b>Bushy</b>")]
    fmap, counted = wn.build_h2h_view_map(venues, "Panel")
    html = fmap.get_root().render()
    assert "'panel' : 'tooltip'" in html
    assert counted[wn.LAYER_H2H] == [(1, 51.41, -0.34)]


def test_a_name_cannot_close_the_script_tag():
    assert "</script>" not in wn._js(["</script><b>x"])


def test_tooltip_names_head_to_heads():
    hc = wn.h2h_counts(h2h())
    by = {}
    for r in hc.itertuples():
        by.setdefault(r.event_id, []).append((r.classification, r.n))
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
    hc = wn.h2h_counts(h2h())
    by = {}
    for r in hc.itertuples():
        by.setdefault(r.event_id, []).append((r.classification, r.n))
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


def test_no_homes_file_skips_quietly(tmp_path):
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE SCHEMA parkrun")
    msgs = []
    assert tr.update_travel_times(con, homes={}, log=msgs.append) == 0
    assert "skipped" in msgs[0]


# --------------------------------------------------------------------------- #
# Privacy contracts
# --------------------------------------------------------------------------- #
def test_travel_times_never_ships_in_the_snapshot():
    assert "travel_times" not in parkrun_pipeline.SNAPSHOT_TABLES


def test_the_travel_table_holds_no_origin():
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE SCHEMA parkrun")
    tr.ensure_table(con)
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'travel_times'").fetchall()}
    assert not cols & {"home_lat", "home_lon", "origin_lat", "origin_lon",
                       "origin", "home"}

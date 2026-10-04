"""Tab 5 — where they meet, and where to go next.

Built as a trial tab 7 beside the old head-to-head-only map, and moved into
that map's place — tab 5 — on 1 Oct 2026. A toggle switches between two views
of one Folium map, each with only its own filters:

- **Head-to-heads** — one pie per venue, sized by the number of head-to-heads
  and split by who won them (the old tab 5's map), filtered by
  classification, year and season.
- **Planner** (the default) — every regular (5k) parkrun in a layer tree
  whose layers overlap: the top recommendations (numbered), one layer per
  athlete of the parkruns they have run (a lamp per athlete in a dark
  housing) under a "done (min 1 person)" parent, and parkruns none of them
  has run (a black circle). It opens zoomed to the recommendations on
  mainland Great Britain, showing them alone, ranked by George's and Duncan's drives to parkruns
  none of them has run. Filtered — in a "⚙️ Filters" popover — by who has
  run it, which head-to-heads have happened there, sea crossings, driving
  time and distance from each home, country and exclusions; ranked by total
  driving time or distance.

Driving times come from `travel_times` (parkrun_travel.py), which every
refresh tops up and the deploy snapshot ships — to every parkrun reachable by
road, Channel Tunnel or ferry, the crossings flagged ⛴. A database without it still
works: the planner drops the driving filters and lists matches A–Z.

The planner draws ~2,400 parkruns. Built as one folium Marker each, that is
several MB of generated script, so the markers are built in the browser
instead (`JsMarkers`): one compact list of points, and each distinct icon
defined once — eight done-patterns, plus one per recommendation number.
"""

from __future__ import annotations

import json
import math
from html import escape

import duckdb
import folium
import pandas as pd
import streamlit as st
from branca.element import MacroElement
from jinja2 import Template
from folium.plugins import TreeLayerControl
from folium.template import Template as FoliumTemplate
from streamlit_folium import st_folium

from parkrun_calendar import theme as cal_theme
from parkrun_core import crosses_water
from parkrun_ui import (ATHLETE_COLORS, BUGGY_GLYPH, _read_sql, closable_popover,
                        flag, flag_html, fmt_n)

# Fixed athlete order for every per-athlete mark: a square means the same
# runner in every marker, so the order can never depend on the data.
ATHLETES = list(ATHLETE_COLORS)          # George, Raju, Duncan

VIEWS = ["Planner", "Head-to-heads"]   # the first is the default
DONE_CHOICES = ["Any", "Done", "Not done"]
H2H_CHOICES = ["Any", "Happened", "Never"]

# Square markers, drawn like the calendar cells (parkrun_calendar: rounded
# squares): a row of lamps in a dark housing, like traffic lights, one per
# athlete and lit in their colour where they have run it. Chosen on 30 Sep
# 2026 over a bare row and a column, bare or lit; those were then removed. A
# parkrun nobody has run is a black circle instead — there is no lamp worth
# drawing. A top recommendation's number goes inside the housing, to the left
# of the lamps (chosen 30 Sep 2026 over a badge above it and a tag beside it).
SQ, SQ_GAP, SQ_PAD, SQ_RX = 8, 2, 2, 1.5
LAMPS_W = 3 * SQ + 2 * SQ_GAP + 2 * SQ_PAD    # the housing, unnumbered
LAMPS_H = SQ + 2 * SQ_PAD
HOUSING = "#262a2e"
UNLIT = "#50565c"

# Each map opens zoomed to fit the markers it shows on opening that lie on
# mainland Great Britain (2 Oct 2026; London before, then briefly the whole
# island) — the top recommendations, say, or every head-to-head venue. A
# marker off the mainland (Belfast, Jersey, the continent) does not widen the
# view; the corner counter says it is outside. With no such marker the map
# fits GB_MAINLAND_BOUNDS — the Lizard to Dunnet Head, Ardnamurchan to
# Lowestoft.
#
# The fit is done in the browser once the map has a size (FitWhenSized):
# every tab's content is rendered while hidden, and Leaflet fits bounds to a
# zero-size box as the whole world. GB_CENTER / GB_ZOOM are only the view the
# map is created with, before that; zoom 5 holds all of mainland GB even on a
# 390 px phone.
GB_CENTER = (54.3, -2.25)
GB_ZOOM = 5
GB_MAINLAND_BOUNDS = [[49.95, -6.25], [58.7, 1.77]]
FIT_PADDING_PX = 30       # keeps a marker's icon clear of the map's edge
FIT_MAX_ZOOM = 12         # one marker on its own: a town, not a street
MAP_HEIGHT = 560


def view_bounds(center, zoom: int, width: int, height: int) -> list:
    """[[south, west], [north, east]] that a width × height px Web Mercator
    map shows at `center` and `zoom` — what Leaflet will draw."""
    scale = 256 * 2 ** zoom

    def to_px(lat, lon):
        y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
        return (lon + 180) / 360 * scale, (1 - y / math.pi) / 2 * scale

    def to_ll(x, y):
        lon = x / scale * 360 - 180
        n = math.pi * (1 - 2 * y / scale)
        return math.degrees(math.atan(math.sinh(n))), lon

    cx, cy = to_px(*center)
    s, w = to_ll(cx - width / 2, cy + height / 2)
    n, e = to_ll(cx + width / 2, cy - height / 2)
    return [[s, w], [n, e]]


LAYER_H2H = "Head-to-heads"
# The planner's layer box is a tree (2 Oct 2026): LAYER_DONE is a parent
# checkbox over one layer per athlete, not a layer itself. A parkrun is drawn
# in every layer it belongs to — a top recommendation nobody has run is in
# the top layer AND the not-done one; a parkrun two of them have run is in
# both their layers — so any combination of switches shows what it says.
LAYER_DONE = "parkruns done (min 1 person)"
LAYER_NOT_DONE = "parkruns not done by anyone"
LAYER_TOP = "top recommendations"


def layer_done_by(name: str) -> str:
    return f"parkruns done by {name}"

# Without driving times there is nothing to rank on — a database with no
# travel rows, or nobody chosen to rank by — so the matches are a plain layer
# of their own, unnumbered: numbering an alphabetical list would present A-to-Z
# as a recommendation.
LAYER_MATCH = "parkruns matching the filters"

CANDIDATE_PINS = 25   # the top recommendations, numbered on the map

# Map styling injected into each map: the close row a narrow map adds to the
# layer box (CollapseLayersWhenNarrow).
MAP_CSS = """
.pr-layers-close { text-align: right; font: 600 12px/1 sans-serif; color: #444;
  padding: 2px 2px 8px; cursor: pointer; user-select: none; }
"""


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
EVENTS_GEO_SQL = """
    SELECT e.event_id, e.short_name, e.latitude, e.longitude, e.live,
           e.seriesid, e.country_code,
           coalesce(c.country_name, 'Unknown') AS country_name,
           coalesce({child}, c.country_name, 'Unknown') AS child_country
    FROM parkrun.events e
    LEFT JOIN parkrun.country_lookup c USING (country_code)
    {join}
    WHERE e.latitude IS NOT NULL AND e.longitude IS NOT NULL
"""


@st.cache_data(show_spinner=False)
def load_events_geo(version) -> pd.DataFrame:
    """`country_name` is the country parkrun files an event under;
    `child_country` where it is (England, Namibia ...), the parent itself
    where the country has no children."""
    try:
        return _read_sql(EVENTS_GEO_SQL.format(
            child="x.child_country",
            join="LEFT JOIN parkrun.event_countries x USING (event_id)"))
    except duckdb.CatalogException:
        # A database built before event_countries: every child is its parent.
        return _read_sql(EVENTS_GEO_SQL.format(child="NULL", join=""))


@st.cache_data(show_spinner=False)
def load_done(version) -> pd.DataFrame:
    """One row per (athlete, parkrun) they have run: visits and last visit."""
    df = _read_sql(
        """
        SELECT a.athlete_name, r.event_id, count(*) AS n_runs,
               max(r.run_date) AS last_run
        FROM parkrun.results r
        JOIN parkrun.athletes a USING (athlete_id)
        GROUP BY ALL
        """
    )
    df["last_run"] = pd.to_datetime(df["last_run"])
    return df


# The router the planner reads (parkrun_travel.PROVIDER — not imported, since
# that module imports `requests`, which the app does not need).
TRAVEL_PROVIDER = "ors"


def travel_version() -> str | None:
    """None when this database has no `travel_times`; otherwise a change key.

    Its own key rather than `data_version`'s, which only watches results and
    labels: a `travel` run changes neither, so keyed on that the planner kept
    serving the drive times it had cached before the run. Existence is checked
    in information_schema rather than by try/except on the query, so a genuine
    SQL error still surfaces instead of being read as "no travel data"."""
    df = _read_sql(
        "SELECT count(*) AS n FROM information_schema.tables "
        "WHERE table_schema = 'parkrun' AND table_name = 'travel_times'"
    )
    if not df["n"].iloc[0]:
        return None
    r = _read_sql(
        "SELECT count(*) AS n, max(routed_at) AS at FROM parkrun.travel_times "
        f"WHERE provider = '{TRAVEL_PROVIDER}'"
    ).iloc[0]
    return f"{r['n']}|{r['at']}"


@st.cache_data(show_spinner=False)
def load_travel(travel_ver) -> pd.DataFrame:
    return _read_sql(
        f"""
        SELECT a.athlete_name, t.event_id, t.duration_s, t.distance_m
        FROM parkrun.travel_times t
        JOIN parkrun.athletes a USING (athlete_id)
        WHERE t.reachable AND t.provider = '{TRAVEL_PROVIDER}'
        """
    )


# --------------------------------------------------------------------------- #
# Pure helpers — no Streamlit, tested in tests/test_where_next.py
# --------------------------------------------------------------------------- #
def done_table(done: pd.DataFrame) -> pd.DataFrame:
    """event_id × athlete: visits (0 where never), plus last visit per athlete."""
    if done.empty:
        cols = ATHLETES + [f"last_{a}" for a in ATHLETES]
        return pd.DataFrame(columns=cols, index=pd.Index([], name="event_id"))
    n = (done.pivot_table(index="event_id", columns="athlete_name",
                          values="n_runs", aggfunc="sum", fill_value=0)
         .reindex(columns=ATHLETES, fill_value=0))
    last = (done.pivot_table(index="event_id", columns="athlete_name",
                             values="last_run", aggfunc="max")
            .reindex(columns=ATHLETES))
    last.columns = [f"last_{c}" for c in last.columns]
    return n.join(last)


def h2h_counts(h2h: pd.DataFrame) -> pd.DataFrame:
    """(event_id, classification, n) — head-to-heads per parkrun, one row per
    occasion however many athletes were in it."""
    if h2h is None or h2h.empty:
        return pd.DataFrame(columns=["event_id", "classification", "n"])
    occ = h2h.drop_duplicates(["event_id", "run_date"])
    return (occ.groupby(["event_id", "classification"]).size()
            .rename("n").reset_index())


def regular_parkruns(events: pd.DataFrame, done: pd.DataFrame) -> pd.DataFrame:
    """Every live 5k parkrun, plus any defunct one somebody ran — a finished
    parkrun still belongs on a map of where they have been."""
    ran = set(done["event_id"]) if not done.empty else set()
    five_k = events[events["seriesid"] == 1]
    return five_k[five_k["live"] | five_k["event_id"].isin(ran)]


# The Countries filter is two levels: a parent (the country parkrun files a
# parkrun under) and, for a parent with any, its child countries — United
# Kingdom over England, Scotland ...; South Africa over Eswatini, Namibia and
# South Africa itself. A child's option is "parent › child", so the South
# Africa child cannot collide with its parent.
CHILD_SEP = " › "


def _children(df: pd.DataFrame) -> pd.Series:
    return df["child_country"] if "child_country" in df else df["country_name"]


def country_options(df: pd.DataFrame) -> dict:
    """{option: label}, each parent followed by its children, A–Z, each label
    with its flag and count. A parent gets children only where some child
    differs from it, so Australia stays one option."""
    out = {}
    child = _children(df)
    for parent, n in sorted(df["country_name"].value_counts().items()):
        out[parent] = f"{flag(parent)} {parent} ({fmt_n(n)})"
        kids = child[df["country_name"] == parent].value_counts()
        if set(kids.index) == {parent}:
            continue
        for kid, m in sorted(kids.items()):
            glyph = flag(kid)
            out[parent + CHILD_SEP + kid] = (
                "\u2003" + (f"{glyph} " if glyph else "") + f"{kid} ({fmt_n(m)})")
    return out


def filter_countries(df: pd.DataFrame, countries) -> pd.DataFrame:
    """Empty means all — the app's multiselect convention. A parent takes in
    all its children; choices add up."""
    if not countries:
        return df
    keys = df["country_name"] + CHILD_SEP + _children(df)
    return df[df["country_name"].isin(countries) | keys.isin(countries)]


def h2h_index(hc: pd.DataFrame) -> dict:
    """event_id → [(classification, n)], most head-to-heads first — what the
    hover text lists (h2h_counts' rows, regrouped)."""
    out: dict = {}
    for r in hc.sort_values(["event_id", "n"], ascending=[True, False]).itertuples():
        out.setdefault(r.event_id, []).append((r.classification, int(r.n)))
    return out


def _unit(units: str) -> tuple[str, float]:
    """The short label and metres per unit for the planner's "miles" | "km"."""
    return ("mi", 1609.344) if units == "miles" else ("km", 1000.0)


def plan_candidates(events: pd.DataFrame, done: pd.DataFrame,
                    travel: pd.DataFrame | None, *, done_filter: dict,
                    h2h: pd.DataFrame | None = None,
                    h2h_filter: dict | None = None,
                    minutes_range: dict | None = None,
                    distance_range: dict | None = None, units: str = "miles",
                    rank_by: list | None = None, rank_metric: str = "time",
                    exclude=(), include_crossings: bool = True) -> pd.DataFrame:
    """The planner's shortlist.

    `events` is every event (load_events_geo); every live 5k one is a
    candidate, and `crossing` marks those reached across water from GB
    (`crosses_water`) — dropped when `include_crossings` is False. `done_filter` maps athlete → "Any" | "Done" | "Not done", and
    `h2h_filter` maps classification → "Any" | "Happened" | "Never", read
    against `h2h` (h2h_counts). `travel` is travel_times (load_travel), or
    None when there is none — then the driving ranges and the ranking are
    skipped and the list is alphabetical. `minutes_range` / `distance_range`
    map athlete → (low, high), inclusive; an athlete without one is
    unconstrained. `rank_by` names the athletes whose driving times are summed
    into `total_min` and whose distances are summed into `total_dist`;
    `rank_metric` ("time" | "distance") picks which of the two is the sort
    key, the other breaking ties. `exclude` holds event_ids to drop.
    """
    c = events[(events["seriesid"] == 1) & events["live"]].copy()
    # A Series, not a bare list: an empty list indexes *columns* (`c[[]]`),
    # so an empty database crashed instead of matching none.
    c["crossing"] = pd.Series(
        [crosses_water(cc, la, lo) for cc, la, lo in
         zip(c["country_code"], c["latitude"], c["longitude"])],
        index=c.index, dtype=bool)
    if not include_crossings:
        c = c[~c["crossing"]]
    c = c[~c["event_id"].isin(list(exclude))]
    dt = done_table(done)
    c = c.merge(dt[ATHLETES], left_on="event_id", right_index=True, how="left")
    c[ATHLETES] = c[ATHLETES].fillna(0).astype(int)

    for name, choice in done_filter.items():
        if choice == "Done":
            c = c[c[name] > 0]
        elif choice == "Not done":
            c = c[c[name] == 0]

    hc = h2h if h2h is not None else pd.DataFrame(columns=["event_id", "classification"])
    for cls, choice in (h2h_filter or {}).items():
        had = set(hc.loc[hc["classification"] == cls, "event_id"])
        if choice == "Happened":
            c = c[c["event_id"].isin(had)]
        elif choice == "Never":
            c = c[~c["event_id"].isin(had)]

    if travel is None or travel.empty:
        return c.sort_values("short_name").reset_index(drop=True)

    _, per = _unit(units)
    w = travel.pivot_table(index="event_id", columns="athlete_name",
                           values=["duration_s", "distance_m"], aggfunc="first")
    for name in ATHLETES:
        if ("duration_s", name) in w:
            c[f"min_{name}"] = c["event_id"].map(w["duration_s"][name] / 60)
            c[f"dist_{name}"] = c["event_id"].map(w["distance_m"][name] / per)

    for name, rng in (minutes_range or {}).items():
        if rng is not None and f"min_{name}" in c:
            c = c[c[f"min_{name}"].between(*rng)]
    for name, rng in (distance_range or {}).items():
        if rng is not None and f"dist_{name}" in c:
            c = c[c[f"dist_{name}"].between(*rng)]

    names = [n for n in (rank_by or []) if f"min_{n}" in c]
    if names:
        mins = [f"min_{n}" for n in names]
        dists = [f"dist_{n}" for n in names]
        c = c.dropna(subset=mins + dists)
        c["total_min"] = c[mins].sum(axis=1)
        c["total_dist"] = c[dists].sum(axis=1)
        keys = (["total_dist", "total_min"] if rank_metric == "distance"
                else ["total_min", "total_dist"])
        c = c.sort_values(keys + ["short_name"])
    else:
        c = c.sort_values("short_name")
    return c.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Marker drawings
# --------------------------------------------------------------------------- #
_SHADOW = "filter:drop-shadow(0 1px 1px rgba(0,0,0,.45))"


def _pie_svg(wins: dict, diameter: int) -> str:
    """A small SVG pie for a venue marker — one slice per athlete, area split by
    their share of form-adjusted 1sts, coloured by ATHLETE_COLORS."""
    r = diameter / 2
    items = [(n, c) for n, c in wins.items() if c > 0]
    total = sum(c for _, c in items)
    if not items or total == 0:
        return ""
    if len(items) == 1:  # a full circle (one 360° arc won't render)
        name = items[0][0]
        return _svg(diameter, diameter, [
            f'<circle cx="{r}" cy="{r}" r="{r - 1}" '
            f'fill="{ATHLETE_COLORS.get(name, "#888888")}" '
            f'stroke="white" stroke-width="1"/>'])
    parts, a0 = [], 0.0
    for name, c in items:
        a1 = a0 + (c / total) * 2 * math.pi
        x0, y0 = r + r * math.sin(a0), r - r * math.cos(a0)
        x1, y1 = r + r * math.sin(a1), r - r * math.cos(a1)
        large = 1 if (a1 - a0) > math.pi else 0
        parts.append(
            f'<path d="M{r},{r} L{x0:.2f},{y0:.2f} '
            f'A{r},{r} 0 {large},1 {x1:.2f},{y1:.2f} Z" '
            f'fill="{ATHLETE_COLORS.get(name, "#888888")}" '
            f'stroke="white" stroke-width="1"/>'
        )
        a0 = a1
    return _svg(diameter, diameter, parts)


def _svg(w, h, body: list) -> str:
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'style="{_SHADOW}">' + "".join(body) + "</svg>")


def _num(x, y, rank: int, size: int = 10) -> str:
    return (f'<text class="rank" x="{x}" y="{y}" dy=".35em" text-anchor="middle" '
            f'fill="white" style="font:700 {size}px sans-serif">{rank}</text>')


def _lamps(done: dict, extra_w: float = 0) -> list:
    """The housing and one lamp per athlete, in ATHLETES order left to right:
    lit in their colour where they have run it, unlit otherwise. `extra_w`
    widens the housing on the left, with the lamps moved over to make room —
    the space a top recommendation's number is written into."""
    parts = [f'<rect x="0" y="0" width="{LAMPS_W + extra_w}" height="{LAMPS_H}" '
             f'rx="3" fill="{HOUSING}"/>']
    for i, name in enumerate(ATHLETES):
        x = SQ_PAD + i * (SQ + SQ_GAP) + extra_w
        fill = ATHLETE_COLORS[name] if done.get(name) else UNLIT
        parts.append(f'<rect class="sq" data-athlete="{name}" x="{x}" y="{SQ_PAD}" '
                     f'width="{SQ}" height="{SQ}" rx="{SQ_RX}" fill="{fill}"/>')
    return parts


# Each marker builder returns (svg, width, height, anchor_x, anchor_y), the
# anchor being the point on the icon that sits on the parkrun's location.
def done_marker(done: dict, rank: int | None = None) -> tuple:
    """A parkrun at least one of them has run: the lamps, and for a top
    recommendation its number in the housing, left of the lamps. The anchor
    stays on the lamps, so a number never moves the parkrun."""
    nw = 0 if rank is None else 11 if rank < 10 else 15
    w, h = LAMPS_W + nw, LAMPS_H
    body = _lamps(done, extra_w=nw)
    if rank is not None:
        body.append(_num(SQ_PAD + (nw - 1) / 2, h / 2, rank, size=9))
    return _svg(w, h, body), w, h, nw + LAMPS_W / 2, h / 2


def open_marker(rank: int | None = None) -> tuple:
    """A parkrun none of them has run: a black circle, numbered — and larger —
    for a top recommendation."""
    d = 20 if rank else 9
    body = [f'<circle cx="{d / 2}" cy="{d / 2}" r="{d / 2 - 1}" fill="#111" '
            f'stroke="white" stroke-width="{1.5 if rank else 1.2}"/>']
    if rank:
        body.append(_num(d / 2, d / 2, rank))
    return _svg(d, d, body), d, d, d / 2, d / 2


def _icon(svg: str, w: int, h: int) -> folium.DivIcon:
    return folium.DivIcon(html=svg, icon_size=(w, h), icon_anchor=(w // 2, h // 2))


# --------------------------------------------------------------------------- #
# Head-to-head layer
# --------------------------------------------------------------------------- #
def h2h_venues(mh: pd.DataFrame, coords: pd.DataFrame) -> list:
    """[(event_id, lat, lon, diameter, svg, tooltip)] — one pie per venue,
    sized by the number of head-to-heads there and split by wins per athlete."""
    if mh.empty:
        return []
    n_h2h = mh.drop_duplicates(["event_id", "run_date"]).groupby("event_id").size()
    firsts = mh[mh["place_rank"] == 1]
    wins = firsts.groupby(["event_id", "athlete_name"]).size().unstack(fill_value=0)
    # Buggy wins counted PER ATHLETE. A trailing "of which 4 with buggy" would
    # be ambiguous about whose wins it counts.
    if "is_buggy" in firsts.columns:
        bw = (firsts[firsts["is_buggy"]]
              .groupby(["event_id", "athlete_name"]).size().unstack(fill_value=0))
    else:
        bw = pd.DataFrame()
    c = coords.set_index("event_id")

    venues = []
    for event_id, count in n_h2h.items():
        if event_id not in c.index:
            continue
        lat, lon = float(c.at[event_id, "latitude"]), float(c.at[event_id, "longitude"])
        wdict = wins.loc[event_id].to_dict() if event_id in wins.index else {}
        wdict = {k: int(v) for k, v in wdict.items() if v > 0}
        bdict = (bw.loc[event_id].to_dict()
                 if not bw.empty and event_id in bw.index else {})
        d = int(round(14 + 5 * math.sqrt(count)))
        # Escaped: names come from parkrun's events.json, not from us, and
        # this HTML becomes a tooltip's innerHTML on a public page.
        breakdown = " · ".join(
            f"{escape(str(k))} {fmt_n(v)}" + (f" ({fmt_n(bdict[k])} {BUGGY_GLYPH})"
                                       if bdict.get(k) else "")
            for k, v in sorted(wdict.items(), key=lambda x: -x[1]))
        tip = (f"<b>{escape(str(c.at[event_id, 'short_name']))}</b><br>"
               f"{fmt_n(count)} head-to-head{'s' if count != 1 else ''}<br>{breakdown}")
        venues.append((event_id, lat, lon, d, _pie_svg(wdict, d), tip))
    return venues


# --------------------------------------------------------------------------- #
# Browser-side pieces
# --------------------------------------------------------------------------- #
def _js(obj) -> str:
    """JSON for inlining in a <script>, with every `<` written as `\\u003c`.
    Escaping only `</` was not enough: `<!--<script` in a name switches the
    HTML parser into a state where the real `</script>` no longer ends the
    block. A JSON string decodes `\\u003c` back to `<`, so the text is
    unchanged once parsed."""
    return json.dumps(obj, separators=(",", ":")).replace("<", "\\u003c")


class JsMarkers(MacroElement):
    """Markers built in the browser into an existing FeatureGroup.

    `icons` maps a key → (svg, width, height, anchor_x, anchor_y), each defined
    once; `points` is [lat, lon, icon_key, tooltip_html, z?] — the optional z
    lifts a marker (a top recommendation) above its neighbours. Thousands of points
    cost a few hundred KB this way, against several MB as folium Markers.

    A pointer that can hover gets a Leaflet tooltip. One that cannot — a
    phone — gets the bottom sheet instead (`MapSheet`, which must be on the
    map first): a tooltip on a phone-width map ran off its edges."""

    _template = Template("""
{% macro script(this, kwargs) %}
(function () {
  var group = {{ this.group_name }};
  var defs = {{ this.icons_json }}, icons = {};
  Object.keys(defs).forEach(function (k) {
    var d = defs[k];
    icons[k] = L.divIcon({html: d[0], className: 'pr-icon',
                          iconSize: [d[1], d[2]], iconAnchor: [d[3], d[4]]});
  });
  var hover = window.matchMedia && window.matchMedia('(hover: hover)').matches;
  {{ this.points_json }}.forEach(function (p) {
    var m = L.marker([p[0], p[1]], {icon: icons[p[2]],
                                    zIndexOffset: (p[4] || 0) + {{ this.z }}});
    if (hover || !window.prSheet) {
      m.bindTooltip(p[3]);
    } else {
      m.on('click', function () { window.prSheet.show(p[3]); });
    }
    m.addTo(group);
  });
})();
{% endmacro %}
""")

    def __init__(self, group: folium.FeatureGroup, icons: dict, points: list,
                 z: int = 0):
        super().__init__()
        self._name = "JsMarkers"
        self.group_name = group.get_name()
        self.icons_json = _js(icons)
        self.points_json = _js(points)
        self.z = int(z)


class MapSheet(MacroElement):
    """The phone's detail sheet for a tapped marker, built to match the
    calendars' (components/calendar/detail.js) so the app has one way of
    showing a tapped thing's details.

    Like that sheet it is built in the PARENT document — a fixed element in
    the map's frame would be pinned to the frame, not the screen — rises
    from the bottom edge of what is actually on screen (visualViewport, so
    pinch-zoom does not push it off-screen or scale it up), uses the
    calendar theme's label colours, and any tap elsewhere — the map, or the
    page — dismisses it. It differs in one thing: it takes the tooltip's
    HTML rather than plain text, for the athlete-coloured squares; that HTML
    is built by this module with every name escaped."""

    _template = Template("""
{% macro script(this, kwargs) %}
(function () {
  var map = {{ this._parent.get_name() }};
  var BG = '{{ this.bg }}', FG = '{{ this.fg }}';
  var pd, vv;
  try {
    pd = window.parent.document; vv = window.parent.visualViewport;
    if (!pd.body) { throw new Error('no parent body'); }
  } catch (e) { pd = document; vv = window.visualViewport; }
  var HID = 'translateY(0)', SHOWN = 'translateY(-100%)';
  function fit(el) {
    if (!vv) { return; }
    var k = 1 / (vv.scale || 1);
    el.style.left = vv.offsetLeft + 'px'; el.style.right = 'auto';
    el.style.bottom = 'auto'; el.style.width = vv.width + 'px';
    el.style.top = (vv.offsetTop + vv.height) + 'px';
    el.style.fontSize = (13 * k) + 'px';
    el.style.padding = (14 * k) + 'px ' + (16 * k) + 'px calc(' +
      (16 * k) + 'px + env(safe-area-inset-bottom))';
    el.style.borderRadius = (14 * k) + 'px ' + (14 * k) + 'px 0 0';
    el.style.maxHeight = (vv.height * 0.5) + 'px';
    var bar = el.firstChild;
    bar.style.width = (38 * k) + 'px'; bar.style.height = (4 * k) + 'px';
    bar.style.margin = (-6 * k) + 'px auto ' + (10 * k) + 'px';
  }
  function sheet() {
    var el = pd.getElementById('map-sheet');
    if (el) { return el; }
    el = pd.createElement('div');
    el.id = 'map-sheet';
    el.style.cssText =
      'position:fixed;left:0;right:0;bottom:0;z-index:1000;box-sizing:border-box;' +
      'background:' + BG + ';color:' + FG + ';' +
      'padding:14px 16px calc(16px + env(safe-area-inset-bottom));' +
      'border-radius:14px 14px 0 0;box-shadow:0 -6px 24px rgba(0,0,0,.3);' +
      'font:13px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;' +
      'transform:' + (vv ? HID : 'translateY(110%)') + ';' +
      'transition:transform .22s ease-out;max-height:50vh;overflow:auto';
    var bar = pd.createElement('div');
    bar.style.cssText = 'width:38px;height:4px;border-radius:2px;opacity:.45;' +
      'margin:-6px auto 10px;background:' + FG;
    var body = pd.createElement('div');
    body.id = 'map-sheet-body';
    el.appendChild(bar); el.appendChild(body);
    el.addEventListener('click', hide);
    pd.addEventListener('click', function (ev) {
      if (!el.contains(ev.target)) { hide(); }
    });
    if (vv) {
      var track = function () { fit(el); };
      vv.addEventListener('resize', track); vv.addEventListener('scroll', track);
    }
    pd.body.appendChild(el);
    fit(el);
    return el;
  }
  function hide() {
    var el = pd.getElementById('map-sheet');
    if (el) { el.style.transform = vv ? HID : 'translateY(110%)'; }
  }
  window.prSheet = {
    show: function (html) {
      var sh = sheet(); fit(sh);
      pd.getElementById('map-sheet-body').innerHTML = html;
      sh.style.transform = vv ? SHOWN : 'translateY(0)';
    },
    hide: hide
  };
  map.on('click', hide);
})();
{% endmacro %}
""")

    def __init__(self, bg: str, fg: str):
        super().__init__()
        self._name = "MapSheet"
        self.bg, self.fg = bg, fg


class CollapseLayersWhenNarrow(MacroElement):
    """Fold the layer box into Leaflet's layers icon on a narrow map, and
    make it closable again once opened.

    Expanded, it covered the top third of a phone-width map, and Leaflet
    draws controls above tooltips — so a label near the top slid underneath
    it. A tap on the icon opens it; a tap on the map, or on its own "Close"
    row, folds it again. (Leaflet only wires the map tap for a control
    created collapsed, and this one is created open for wide maps — so a
    narrow map had no way to close it once opened.) Wide maps keep it open."""

    _template = Template("""
{% macro script(this, kwargs) %}
(function fold() {
  var map = {{ this._parent.get_name() }};
  var ctl = {{ this.control_name }};
  // A map drawn in a tab that is not showing yet has no width at all, and
  // deciding then folded the box on desktop too: wait until it has one.
  var w = map.getContainer().clientWidth;
  if (!w) { setTimeout(fold, 200); return; }
  if (w >= {{ this.max_width }}) { return; }
  ctl.collapse();
  map.on('click', function () { ctl.collapse(); });
  var list = ctl._section || ctl._form;
  if (list) {
    var x = L.DomUtil.create('div', 'pr-layers-close');
    x.textContent = 'Close \u2715';
    list.insertBefore(x, list.firstChild);
    L.DomEvent.on(x, 'click', function (e) { L.DomEvent.stop(e); ctl.collapse(); });
  }
})();
{% endmacro %}
""")

    def __init__(self, control: folium.LayerControl, max_width: int = 640):
        super().__init__()
        self._name = "CollapseLayersWhenNarrow"
        self.control_name = control.get_name()
        self.max_width = int(max_width)


class LayerTree(TreeLayerControl):
    """folium's TreeLayerControl with its control kept in a named variable —
    the plugin's template discards it, and CollapseLayersWhenNarrow needs it
    — and created open, as Leaflet's own layer box is here on a wide map."""

    _template = FoliumTemplate("""
{% macro script(this, kwargs) %}
var {{ this.get_name() }} = L.control.layers.tree(
    {{ this.base_tree|tojavascript }},
    {{ this.overlay_tree|tojavascript }},
    {{ this.options|tojavascript }}
).addTo({{ this._parent.get_name() }});
{% endmacro %}
""")

    def __init__(self, overlay_tree):
        super().__init__(overlay_tree=overlay_tree, collapsed=False,
                         closed_symbol="▸", opened_symbol="▾")


def _layer_control(fmap: folium.Map, tree: list) -> None:
    """`tree` is the overlay tree: [{"label", "layer"} | {"label",
    "selectAllCheckbox", "children"}]. One background map, so no base tree."""
    ctl = LayerTree(tree).add_to(fmap)
    CollapseLayersWhenNarrow(ctl).add_to(fmap)


class InViewCounter(MacroElement):
    """A corner label — "38 of 205 parkruns in view · 167 outside" — kept
    current in the browser on every pan, zoom and layer toggle.

    In the browser rather than through `st_folium(returned_objects=["bounds"])`
    because that reruns the whole app on every drag. Counts distinct parkruns
    across the layers currently on the map, so a venue drawn in two layers
    counts once. Which layers are on is read from the map itself
    (`hasLayer`), not from the layer box's labels, which carry HTML."""

    _template = Template("""
{% macro script(this, kwargs) %}
(function () {
  var map = {{ this._parent.get_name() }};
  var layers = {{ this.layers_json }};
  var groups = { {% for k, g in this.groups %}{{ k|tojson }}: {{ g }}{{ "," if not loop.last }}{% endfor %} };
  var Ctl = L.Control.extend({
    onAdd: function () {
      var d = L.DomUtil.create('div', 'pr-inview');
      d.style.cssText = 'background:rgba(255,255,255,.92);color:#222;' +
        'padding:3px 8px;border-radius:4px;font:12px/1.4 sans-serif;' +
        'box-shadow:0 1px 3px rgba(0,0,0,.3)';
      this._d = d;
      return d;
    }
  });
  var ctl = new Ctl({position: 'bottomleft'});
  ctl.addTo(map);
  function update() {
    var b = map.getBounds(), all = {}, inv = {};
    Object.keys(layers).forEach(function (name) {
      if (!map.hasLayer(groups[name])) return;
      layers[name].forEach(function (p) {
        all[p[0]] = 1;
        if (b.contains([p[1], p[2]])) inv[p[0]] = 1;
      });
    });
    var n = Object.keys(all).length, k = Object.keys(inv).length;
    var f = function (x) { return x.toLocaleString('en-GB'); };
    ctl._d.innerHTML = '<b>' + f(k) + '</b> of ' + f(n) + ' parkrun' +
      (n === 1 ? '' : 's') + ' in view · <b>' + f(n - k) + '</b> outside';
  }
  map.on('moveend zoomend overlayadd overlayremove', update);
  map.whenReady(update);
})();
{% endmacro %}
""")

    def __init__(self, layers: dict, groups: dict):
        super().__init__()
        self._name = "InViewCounter"
        self.layers_json = _js(
            {k: [[int(e), round(float(la), 5), round(float(lo), 5)]
                 for e, la, lo in pts] for k, pts in layers.items()})
        # name → the FeatureGroup's JS variable; layers not in it never count.
        self.groups = [(k, g.get_name()) for k, g in groups.items()]


# --------------------------------------------------------------------------- #
# Tooltips
# --------------------------------------------------------------------------- #
def _dot(name: str) -> str:
    return f"<span style='color:{ATHLETE_COLORS[name]}'>■</span>"


def _fmt_min(m) -> str:
    m = int(round(float(m)))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m} min"


# Hover text says only what is there: a parkrun nobody has run is its name
# and country; runs are listed for whoever has run it, nobody else; the
# head-to-head line appears only where one happened. Driving times are the
# exception, shown for all three on a recommendation — they are what it was
# ranked on.
def _h2h_line(eid, h2h_by_event: dict) -> str | None:
    """"Head-to-heads: 3 (George vs Raju 2, Duncan vs George 1)", or None."""
    parts = h2h_by_event.get(eid)
    if not parts:
        return None
    total = sum(n for _, n in parts)
    detail = ", ".join(f"{escape(c)} {fmt_n(n)}" for c, n in parts)
    return f"Head-to-heads: <b>{fmt_n(total)}</b> ({detail})"


def _runs_lines(eid, dt: pd.DataFrame) -> list:
    """One line per athlete who has run it; none for those who have not."""
    if eid not in dt.index:
        return []
    row = dt.loc[eid]
    out = []
    for name in ATHLETES:
        n = int(row[name])
        if n:
            last = pd.Timestamp(row[f"last_{name}"]).strftime("%d %b %Y")
            out.append(f"{_dot(name)} {name} — {fmt_n(n)} run{'s' if n != 1 else ''}, "
                       f"last {last}")
    return out


def _title(row, extra: str = "") -> str:
    return (f"<b>{escape(str(row.short_name))}</b> "
            f"<span style='opacity:.65'>"
            f"{escape(str(getattr(row, 'child_country', row.country_name)))}"
            f"{extra}</span>")


def _tip(lines: list, eid, h2h_by_event: dict) -> str:
    """The lines, then the head-to-head line where there is one."""
    h = _h2h_line(eid, h2h_by_event)
    return "<br>".join(lines + ([h] if h else []))


def _base_tip(row, dt, h2h_by_event) -> str:
    return _tip([_title(row)] + _runs_lines(row.event_id, dt),
                row.event_id, h2h_by_event)


# A runner left out of the ranking is shown, but grey and italic, tagged "not
# ranked" — on the map and in the table alike (chosen 1 Oct 2026 over greying
# the map only, or hiding them). The grey reads on both the white desktop
# tooltip and the dark phone sheet.
NOT_RANKED = "not ranked"
# A drive that needs the Channel Tunnel or a ferry (parkrun_core.crosses_water).
# Shown as the router gives it, flagged, with no allowance added: an allowance
# would be a guess, and a ranking built on a guess is hard to un-guess.
CROSSING = "⛴"
CROSSING_NOTE = ("crosses water — sailing time only, no check-in or waiting, "
                 "so the real journey is longer")
GREY = "#9aa0a6"
GREY_STYLE = f"color:{GREY};font-style:italic"


def _candidate_tip(rank: int, row, units: str, h2h_by_event: dict,
                   dt: pd.DataFrame, rank_by=None,
                   metric: str = "time") -> str:
    """A match's hover text: who has run it, everyone's drive, the totals.

    A runner not in `rank_by` had no say in this rank, so their drive is
    grey, italic and tagged "not ranked". Both totals are given,
    the one ranked on in bold, each naming whose journeys it sums."""
    unit, _ = _unit(units)
    rank_by = list(rank_by or [])
    lines = [_title(row, f" · recommendation #{rank}")]
    lines += _runs_lines(row.event_id, dt)
    drives = []
    for name in ATHLETES:
        m = getattr(row, f"min_{name}", None)
        d = getattr(row, f"dist_{name}", None)
        if m is None or pd.isna(m):
            continue
        text = f"{name} {_fmt_min(m)}, {fmt_n(d, 1)} {unit}"
        if rank_by and name not in rank_by:
            text = f"<span style='{GREY_STYLE}'>{text} · {NOT_RANKED}</span>"
        drives.append(f"{_dot(name)} {text}")
    if drives:
        lines.append("<span style='opacity:.65'>Driving from home</span>")
        lines += drives
        if getattr(row, "crossing", False):
            lines.append(f"{CROSSING} <i>{CROSSING_NOTE}</i>")
    tot_m = getattr(row, "total_min", None)
    if tot_m is not None and not pd.isna(tot_m):
        who = ", ".join(rank_by)
        t = f"Total driving time ({who}): {_fmt_min(tot_m)}"
        dd = f"Total distance ({who}): {fmt_n(row.total_dist, 1)} {unit}"
        lines += ([f"<b>{t}</b>", dd] if metric == "time" else [f"<b>{dd}</b>", t])
    return _tip(lines, row.event_id, h2h_by_event)


# --------------------------------------------------------------------------- #
# The maps
# --------------------------------------------------------------------------- #
def _base_map() -> folium.Map:
    # The tiles are added with control=False: there is one background map, so
    # the layer box's "openstreetmap" base-layer radio button chose between
    # one option and did nothing.
    fmap = folium.Map(location=list(GB_CENTER), zoom_start=GB_ZOOM,
                      tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", control=False).add_to(fmap)
    fmap.get_root().header.add_child(folium.Element(f"<style>{MAP_CSS}</style>"))
    t = cal_theme()
    MapSheet(t.tip_bg, t.tip_fg).add_to(fmap)
    return fmap


def build_h2h_view_map(venues: list):
    """The head-to-head view: a pie per venue, with the counter. Built with
    JsMarkers, like the planner, so both views label alike.

    No layer box (removed 2 Oct 2026): the pies are the only layer, so its
    one checkbox could only empty the map."""
    fmap = _base_map()
    g = folium.FeatureGroup(name=LAYER_H2H, show=True, control=False).add_to(fmap)
    icons, points = {}, []
    for eid, lat, lon, d, svg, tip in venues:
        icons[str(eid)] = (svg, d, d, d / 2, d / 2)
        points.append([round(lat, 5), round(lon, 5), str(eid), tip])
    JsMarkers(g, icons, points).add_to(fmap)
    counted = {LAYER_H2H: [(v[0], v[1], v[2]) for v in venues]}
    InViewCounter(counted, {LAYER_H2H: g}).add_to(fmap)
    return fmap, counted


def build_planner_map(*, parkruns: pd.DataFrame, dt: pd.DataFrame,
                      candidates: pd.DataFrame | None, h2h_by_event: dict,
                      units: str = "miles", rank_by=None,
                      metric: str = "time"):
    """The planner view. `parkruns` is every regular parkrun to draw (already
    country-filtered), `dt` the done_table, `candidates` the ranked matches
    (None when no filter is set).

    The layers overlap (2 Oct 2026; before, every parkrun was in exactly
    one): the top CANDIDATE_PINS recommendations, numbered; one layer per
    athlete of the parkruns they have run, under a "done (min 1 person)"
    parent; and the parkruns none of them has run. A recommendation is
    numbered in the top layer and drawn plain in its own kind's layers; the
    numbered marker is lifted above the plain one. Every match's tooltip
    gives its rank and driving times, in whichever layer.

    The candidates are a *ranking* only when they carry `total_min`. Without
    it (no driving times, or nobody chosen to rank by) the list is merely
    alphabetical, so the matches go in LAYER_MATCH, unnumbered, in place of
    the top layer."""
    fmap = _base_map()
    ranked, matched = {}, set()
    if candidates is not None:
        if "total_min" in candidates:
            ranked = {r.event_id: (i, r) for i, r in
                      enumerate(candidates.itertuples(index=False), start=1)}
        else:
            matched = set(candidates["event_id"])
    ran = set(dt.index[dt[ATHLETES].sum(axis=1) > 0]) if not dt.empty else set()
    done_by = [layer_done_by(n) for n in ATHLETES]

    # Per layer: (icons by key, JsMarkers points, the counter's points).
    layers = {name: ({}, [], []) for name in
              (LAYER_TOP, LAYER_MATCH, *done_by, LAYER_NOT_DONE)}

    def put(layer, key, make, row, tip, z=0):
        icons, points, pts = layers[layer]
        if key not in icons:
            icons[key] = make()
        points.append([round(float(row.latitude), 5), round(float(row.longitude), 5),
                       key, tip, z])
        pts.append((row.event_id, row.latitude, row.longitude))

    for row in parkruns.itertuples(index=False):
        eid = row.event_id
        rank, crow = ranked.get(eid, (None, None))
        top = rank if rank is not None and rank <= CANDIDATE_PINS else None
        tip = (_candidate_tip(rank, crow, units, h2h_by_event, dt, rank_by,
                              metric) if rank
               else _base_tip(row, dt, h2h_by_event))
        if eid in ran:
            r = dt.loc[eid]
            flags = {n: bool(r[n] > 0) for n in ATHLETES}
            key = "".join("1" if flags[n] else "0" for n in ATHLETES)
            for n in ATHLETES:
                if flags[n]:
                    put(layer_done_by(n), key, lambda: done_marker(flags), row, tip)
            if top:
                put(LAYER_TOP, f"{key}#{top}", lambda: done_marker(flags, top),
                    row, tip, 1000)
        else:
            put(LAYER_NOT_DONE, "dot", open_marker, row, tip)
            if top:
                put(LAYER_TOP, f"#{top}", lambda: open_marker(top), row, tip, 1000)
        if eid in matched:
            put(LAYER_MATCH, "m" + ("".join("1" if dt.loc[eid, n] > 0 else "0"
                                            for n in ATHLETES) if eid in ran else ""),
                lambda: (done_marker({n: dt.loc[eid, n] > 0 for n in ATHLETES})
                         if eid in ran else open_marker()), row, tip, 500)

    # The map opens on the picked-out parkruns alone — the recommendations, or
    # the matches where nothing is ranked — with every other parkrun one tap
    # away in the layer box. With nothing picked out, everything shows.
    picked = bool(layers[LAYER_TOP][1] or layers[LAYER_MATCH][1])
    groups, counted, shown = {}, {}, {}
    for name, (icons, points, pts) in layers.items():
        if name in (LAYER_TOP, LAYER_MATCH) and not points:
            continue   # nothing picked out, so no layer to switch
        shown[name] = name in (LAYER_TOP, LAYER_MATCH) or not picked
        g = folium.FeatureGroup(name=name, show=shown[name], control=False)
        g.add_to(fmap)
        JsMarkers(g, icons, points).add_to(fmap)
        groups[name], counted[name] = g, pts

    tree = [{"label": f" {n}", "layer": groups[n]}
            for n in (LAYER_TOP, LAYER_MATCH) if n in groups]
    tree.append({"label": f" {LAYER_DONE}", "selectAllCheckbox": True,
                 "children": [{"label": f" {_dot(n)} {layer_done_by(n)}",
                               "layer": groups[layer_done_by(n)]}
                              for n in ATHLETES]})
    tree.append({"label": f" {LAYER_NOT_DONE}", "layer": groups[LAYER_NOT_DONE]})
    _layer_control(fmap, tree)
    InViewCounter(counted, groups).add_to(fmap)
    fmap.pr_shown = shown        # for _show_map's caption, which counts these
    return fmap, counted


def mainland_ids(events: pd.DataFrame) -> set:
    """The event_ids on mainland Great Britain (not `crosses_water`)."""
    return {e for e, cc, la, lo in zip(events["event_id"], events["country_code"],
                                       events["latitude"], events["longitude"])
            if not crosses_water(cc, la, lo)}


def opening_points(fmap, counted: dict) -> pd.DataFrame:
    """The distinct parkruns in the layers the map opens with."""
    shown = getattr(fmap, "pr_shown", None)
    return pd.DataFrame(
        [(e, la, lo) for name, layer in counted.items()
         if shown is None or shown.get(name, True) for e, la, lo in layer],
        columns=["event_id", "latitude", "longitude"]).drop_duplicates("event_id")


def opening_bounds(pts: pd.DataFrame, mainland: set) -> list:
    """[[south, west], [north, east]] around the opening markers on mainland
    GB, or GB_MAINLAND_BOUNDS when there are none."""
    on = pts[pts["event_id"].isin(mainland)]
    if on.empty:
        return GB_MAINLAND_BOUNDS
    return [[float(on["latitude"].min()), float(on["longitude"].min())],
            [float(on["latitude"].max()), float(on["longitude"].max())]]


class FitWhenSized(MacroElement):
    """Fit the map to `bounds` once it has a size — not before: a map drawn
    in a tab that is not showing yet is 0 px wide, and Leaflet fits a
    zero-size box as the whole world."""

    _template = Template("""
{% macro script(this, kwargs) %}
(function fit() {
  var map = {{ this._parent.get_name() }};
  if (!map.getContainer().clientWidth) { setTimeout(fit, 200); return; }
  map.invalidateSize();
  map.fitBounds({{ this.bounds_json }},
                {padding: [{{ this.pad }}, {{ this.pad }}],
                 maxZoom: {{ this.max_zoom }}});
})();
{% endmacro %}
""")

    def __init__(self, bounds, pad: int = FIT_PADDING_PX,
                 max_zoom: int = FIT_MAX_ZOOM):
        super().__init__()
        self._name = "FitWhenSized"
        self.bounds_json = _js([[round(float(v), 5) for v in c] for c in bounds])
        self.pad, self.max_zoom = int(pad), int(max_zoom)


def _show_map(fmap, counted: dict, key: str, what: str, mainland: set) -> None:
    pts = opening_points(fmap, counted)
    n_all = len(pts)
    n_in = int(pts["event_id"].isin(mainland).sum())
    elsewhere = (f" The other **{fmt_n(n_all - n_in)}** are off it, outside the "
                 f"opening view." if n_all - n_in else "")
    st.caption(
        f"The map opens zoomed to the **{fmt_n(n_in)}** {what} shown on mainland "
        f"Great Britain.{elsewhere} The count in the map's corner follows "
        f"the actual view as you pan, zoom and switch layers."
        if n_in else
        f"The map opens on mainland Great Britain.{elsewhere} The count in "
        f"the map's corner follows the actual view as you pan, zoom and "
        f"switch layers."
    )
    FitWhenSized(opening_bounds(pts, mainland)).add_to(fmap)
    # No center/zoom here: st_folium would re-apply them over the fit.
    st_folium(fmap, height=MAP_HEIGHT, returned_objects=[], key=key,
              use_container_width=True)


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #
def _first(key: str, value):
    """A widget's default on the run that creates its state, None after.

    keep_widget_state writes every tab 5 key back each run, and Streamlit
    warns about a widget that has both a `default=` and a value set through
    session state. Once the state exists the default is ignored anyway."""
    return value if key not in st.session_state else None


def view_toggle() -> str:
    return st.segmented_control("View", VIEWS, default=_first("t5_view", VIEWS[0]),
                                key="t5_view",
                                label_visibility="collapsed") or VIEWS[0]


def _name_cell(col, name: str) -> None:
    col.markdown(f"<div style='font-weight:600'>{_dot(name)} {name}</div>",
                 unsafe_allow_html=True)


def reseed_range(stored, old_top, top: int) -> tuple[int, int]:
    """The range a range_filter starts this run with. Nothing stored, or the
    stored range was the whole of the old track: the whole of the new one.
    Otherwise the reader's range, clamped to the track."""
    cast = type(top)            # int for minutes, float for 0.1-step distances
    if stored is None or old_top is None or tuple(stored) == (0, old_top):
        return cast(0), top
    lo, hi = (min(cast(v), top) for v in stored)
    return lo, hi


# The driving tracks stop here even though the data runs further (a drive to
# Finland is about 35 h): a track that long cannot set a London-scale range.
# When the data runs past the cap, the track's top means "this or more".
TIME_CAP_MIN = 720
DIST_CAP = {"mi": 800.0, "km": 1300.0}


def track_top(data_max: float, cap: float, cast=int) -> tuple:
    """(top, open_top): the data's maximum rounded up to 10, or the cap when
    the data runs past it — then the top of the track is open-ended."""
    top = cast(math.ceil(data_max / 10) * 10)
    return (cast(cap), True) if top > cap else (top, False)


# On a phone, a range's two boxes and slider stay on one line (2 Oct 2026;
# Streamlit stacks columns below 640px, which gave each range three rows).
# Keyed on the `st-key-rng-*` container range_filter draws each row in — the
# same opt-out as the PB scopes and section headings. The boxes get a fixed
# width and the slider the rest (a column ratio is cancelled by the flex
# override): about 145px of slider in a ~300px-wide Filters panel at 390px.
# 4.5rem holds "1300.0" at 16px, and the font stays 16px because a smaller
# input makes iOS zoom the page on focus. Below 120px a box hides its +/-
# steppers by itself. The slider's thumb labels repeat the boxes and spill
# over them, so they go. Emitted once per page, beside SECTION_CSS: a <style>
# inside the popover would take a row of its own.
RANGE_ROW_CSS = """<style>
@media (max-width: 640px) {
  [class*="st-key-rng-"] [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important; gap: .4rem !important; }
  [class*="st-key-rng-"] [data-testid="stColumn"] { min-width: 0 !important; }
  [class*="st-key-rng-"] [data-testid="stColumn"]:first-child,
  [class*="st-key-rng-"] [data-testid="stColumn"]:last-child {
    flex: 0 0 4.5rem !important; width: 4.5rem !important; }
  [class*="st-key-rng-"] [data-testid="stColumn"]:nth-child(2) {
    flex: 1 1 0 !important; width: auto !important; }
  [class*="st-key-rng-"] [data-testid="stNumberInputField"] {
    padding-left: .35rem !important; padding-right: .35rem !important; }
  [class*="st-key-rng-"] [data-testid="stSliderThumbValue"] { display: none; }
}
</style>"""


def range_filter(label: str, key: str, top, step, unit: str,
                 open_top: bool = False):
    """A two-ended slider between two number boxes, kept in step.

    With `open_top` the track's top means "and over": a range reaching it
    has no upper limit (math.inf).

    Returns (low, high), or None while the range is the whole track — the full
    range means "no limit", so the furthest parkrun is never dropped for being
    exactly at the top. Callbacks keep the three widgets agreeing: the slider
    writes both boxes; a box writes the slider, swapping low and high if they
    cross.

    The track's top is remembered (`_top`), because it moves: a `travel` run
    that routes a farther parkrun raises it. A range left at the full track
    then follows it to the new full track — otherwise it would quietly turn
    into a cap that excludes the new parkrun. A range the reader set is kept,
    and only clamped if the track shrank under it."""
    rk, lk, hk, tk = (f"{key}_rng", f"{key}_lo", f"{key}_hi", f"{key}_top")
    ss = st.session_state
    lo, hi = reseed_range(ss.get(rk), ss.get(tk), top)
    ss[rk], ss[lk], ss[hk], ss[tk] = (lo, hi), lo, hi, top

    def from_slider():
        ss[lk], ss[hk] = ss[rk]

    cast = type(top)
    zero = cast(0)
    fmt = "%.1f" if cast is float else "%d"

    def from_boxes():
        lo = min(max(cast(ss[lk]), zero), top)
        hi = min(max(cast(ss[hk]), zero), top)
        lo, hi = min(lo, hi), max(lo, hi)
        ss[lk], ss[hk], ss[rk] = lo, hi, (lo, hi)

    row = st.container(key=f"rng-{key}")      # RANGE_ROW_CSS's hook
    c1, c2, c3 = row.columns([1, 3, 1], vertical_alignment="center")
    c1.number_input(f"{label}: from ({unit})", zero, top, step=step, key=lk,
                    format=fmt, on_change=from_boxes, label_visibility="collapsed")
    c2.slider(label, zero, top, step=step, key=rk, format=fmt,
              on_change=from_slider, label_visibility="collapsed")
    c3.number_input(f"{label}: to ({unit})", zero, top, step=step, key=hk,
                    format=fmt, on_change=from_boxes, label_visibility="collapsed")
    lo, hi = ss[rk]
    if (lo, hi) == (zero, top):
        return None
    return (lo, math.inf if open_top and hi == top else hi)


# What the planner opens on (2 Oct 2026): the parkruns nobody has run, ranked
# by George's and Duncan's drives. Clear all filters is NOT a way back here —
# it sets PLANNER_CLEARED, the neutral state; a reload gives the opening one.
DEFAULT_RANK_BY = ("George", "Duncan")
DEFAULT_DONE = "Not done"

# Keys Clear deletes rather than sets: each widget comes back at its default
# on the rerun ("Any" for a head-to-head row; the full track for a range —
# range_filter re-seeds its keys when they are missing).
PLANNER_RESEEDED_KEYS = ("t5_h2h_", "t5_min_", "t5_dist_")


def planner_cleared(routed) -> dict:
    """The values Clear all filters writes: miles, ranked on driving time by
    everyone with a route, anyone, nothing left out, every country, sea
    crossings in. Units and the rank metric are reset too — a "clear" that
    left km switched on read as broken."""
    out = {"t5_units": "miles", "t5_rank_metric": "Driving time",
           "t5_rank_by": list(routed), "t5_countries": [], "t5_exclude": [],
           "t5_crossings": True}
    out.update({f"t5_done_{n}": "Any" for n in ATHLETES})
    return out


def clear_planner_filters(routed=()) -> None:
    """The Clear button's on_click: runs before any widget of the rerun
    exists, which is what makes setting widget keys here legal."""
    ss = st.session_state
    for k in list(ss.keys()):
        if isinstance(k, str) and k.startswith(PLANNER_RESEEDED_KEYS):
            del ss[k]
    for k, v in planner_cleared(routed).items():
        ss[k] = v


def _choice_rows(title: str, rows: list, choices: list, key_prefix: str,
                 dot=True, default=None) -> dict:
    """One segmented control per row; `default` is each row's opening
    choice (the first choice when None)."""
    st.markdown(f"**{title}**")
    out = {}
    for label in rows:
        c1, c2 = st.columns([1.3, 2.7], vertical_alignment="center")
        if dot:
            _name_cell(c1, label)
        else:
            c1.markdown(escape(label))
        out[label] = c2.segmented_control(
            f"{title}: {label}", choices,
            default=_first(f"{key_prefix}_{label}", default or choices[0]),
            key=f"{key_prefix}_{label}", label_visibility="collapsed") or choices[0]
    return out


def _results_table(c: pd.DataFrame, units: str, rank_by=None,
                   metric: str = "time") -> ResultsTable:
    """The matches as table cells: the display text, a sort key per cell and
    the column roles that drive the styling. Distances to 0.1.

    Column order puts what decided the ranking first: `#`, the parkrun, the
    total ranked on (bold, "(ranked)"), the other total, then each runner's
    drive — greyed, italic and tagged for a runner left out of the ranking —
    and who has run it."""
    unit, _ = _unit(units)
    c = c.reset_index(drop=True)   # country-filtered upstream: realign rows
    rank_by = list(rank_by or [])
    ranked = "total_min" in c
    cols: list = []       # (header, texts, sort keys)
    greyed, bold, raw = [], [], []

    titles: dict = {}

    def add(header, texts, keys, titles_=None):
        cols.append((header, list(texts), list(keys)))
        if titles_ is not None:
            titles[header] = list(titles_)

    # A rank column only for a ranking: on an A-Z list a "#" reads as one.
    if ranked:
        add("#", map(str, range(1, len(c) + 1)), range(1, len(c) + 1))
    names = c["short_name"].astype(str)
    if "crossing" in c:
        names = names.where(~c["crossing"], names + f" {CROSSING}")
    add("parkrun", names, c["short_name"].str.lower())
    # The flag alone; the name is the cell's tooltip and its sort key. HTML,
    # because Northern Ireland's flag is a drawing (parkrun_ui.IMAGE_FLAG).
    if "country_name" in c:
        child = _children(c).astype(str)
        add("Country", child.map(flag_html), child, child)
        raw.append("Country")
    if ranked:
        t_col = ("Total time", c["total_min"].map(_fmt_min), c["total_min"])
        d_col = (f"Total {unit}", c["total_dist"].map(lambda v: fmt_n(v, 1)),
                 c["total_dist"])
        first, second = (d_col, t_col) if metric == "distance" else (t_col, d_col)
        add(f"{first[0]} ({RANKED})", first[1], first[2])
        bold.append(f"{first[0]} ({RANKED})")
        add(*second)
    for name in ATHLETES:
        if f"min_{name}" not in c:
            continue
        left_out = ranked and rank_by and name not in rank_by
        tag = f" ({NOT_RANKED})" if left_out else ""
        tcol, dcol = f"{name} time{tag}", f"{name} {unit}{tag}"
        mins, dists = c[f"min_{name}"], c[f"dist_{name}"]
        add(tcol, mins.map(lambda v: "—" if pd.isna(v) else _fmt_min(v)), mins)
        add(dcol, dists.map(lambda v: fmt_n(v, 1)), dists)
        if tag:
            greyed += [tcol, dcol]
    run_by = c.apply(
        lambda r: ", ".join(f"{n} ({fmt_n(r[n])})" for n in ATHLETES if r[n] > 0)
        or "nobody", axis=1)
    add("Run by", run_by, run_by.str.lower())
    return ResultsTable(cols, greyed, bold, titles, raw)


# The table is an HTML table drawn by st.html (in the page, not an iframe),
# chosen on 2 Oct 2026 over st.dataframe and streamlit-aggrid. st.dataframe
# reads only colour, background and weight from a Styler — no italic, and no
# header styling at all — so a runner left out of the ranking could not look
# as they do on the map. aggrid could, but is a 21 MB dependency in an iframe
# that does not follow the app's theme. This keeps sorting (click a header),
# column resizing (drag a header's right edge) and scrolling (a fixed-height
# box, the header pinned) with a few lines of script.
RANKED = "ranked"
TABLE_ROW_PX, TABLE_MAX_PX = 35, 420


class ResultsTable:
    """`cols` is [(header, cell texts, sort keys)]; `greyed` and `bold` name
    the headers styled as not ranked and as the ranking total; `titles` maps
    a header to each cell's tooltip (the country's name under its flag);
    `raw` names the headers whose cell texts are already HTML."""

    def __init__(self, cols, greyed, bold, titles=None, raw=()):
        self.cols, self.greyed, self.bold = cols, greyed, bold
        self.titles = titles or {}
        self.raw = set(raw)

    @property
    def columns(self) -> list:
        return [h for h, _, _ in self.cols]

    def text(self, header) -> list:
        return next(t for h, t, _ in self.cols if h == header)


def _sort_key(v) -> str:
    """A cell's sort key as text: numbers as numbers, a missing one empty
    (sorted last whichever way)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return repr(float(v))
    return str(v)


def results_table_html(t: ResultsTable, table_id: str = "t5-results") -> str:
    th = cal_theme()
    n = len(t.cols[0][1]) if t.cols else 0
    height = min(TABLE_MAX_PX, 38 + TABLE_ROW_PX * n)
    numeric = {h for h, _, keys in t.cols
               if all(isinstance(k, (int, float)) or k is None for k in keys)}

    def style(h, header=False):
        out = []
        if h in t.greyed:
            out.append(GREY_STYLE)
        if h in t.bold:
            out.append("font-weight:700")
        if h in numeric:
            out.append("text-align:right")
        return f' style="{";".join(out)}"' if out else ""

    head = "".join(
        f'<th data-num="{int(h in numeric)}"{style(h, True)} '
        f'title="Sort by {escape(h)}">{escape(h)}<span class="rz"></span></th>'
        for h, _, _ in t.cols)
    rows = []
    for i in range(n):
        cells = "".join(
            f'<td data-v="{escape(_sort_key(keys[i]))}"{style(h)}'
            + (f' title="{escape(t.titles[h][i])}"' if h in t.titles else "")
            + f'>{texts[i] if h in t.raw else escape(str(texts[i]))}</td>'
            for h, texts, keys in t.cols)
        rows.append(f"<tr>{cells}</tr>")
    return f"""
<style>
#{table_id}-box {{ max-height:{height}px; overflow:auto; border-radius:.5rem;
  border:1px solid rgba(128,128,128,.3); -webkit-overflow-scrolling:touch; }}
#{table_id} {{ border-collapse:separate; border-spacing:0; width:100%;
  font-size:.875rem; font-variant-numeric:tabular-nums; }}
#{table_id} th, #{table_id} td {{ padding:.4rem .6rem; white-space:nowrap;
  border-bottom:1px solid rgba(128,128,128,.18); }}
#{table_id} th {{ position:sticky; top:0; z-index:1; background:{th.surface};
  font-weight:600; cursor:pointer; user-select:none; text-align:left;
  border-bottom:1px solid rgba(128,128,128,.4); }}
#{table_id} th[data-num="1"] {{ text-align:right; }}
#{table_id} th .rz {{ position:absolute; top:0; right:0; width:8px; height:100%;
  cursor:col-resize; touch-action:none; }}
#{table_id} th.asc::after {{ content:" ▲"; font-size:.7em; }}
#{table_id} th.desc::after {{ content:" ▼"; font-size:.7em; }}
#{table_id} tbody tr:hover td {{ background:rgba(128,128,128,.08); }}
</style>
<div id="{table_id}-box"><table id="{table_id}">
<thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<script>
(function () {{
  var tb = document.getElementById({_js(table_id)});
  if (!tb || tb.dataset.wired) return;
  tb.dataset.wired = 1;
  var ths = tb.tHead.rows[0].cells;
  Array.prototype.forEach.call(ths, function (th, col) {{
    th.addEventListener('click', function (ev) {{
      if (ev.target.classList.contains('rz')) return;
      var asc = !th.classList.contains('asc');
      Array.prototype.forEach.call(ths, function (h) {{
        h.classList.remove('asc', 'desc'); }});
      th.classList.add(asc ? 'asc' : 'desc');
      var num = th.dataset.num === '1', body = tb.tBodies[0];
      var rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (a, b) {{
        var x = a.cells[col].dataset.v, y = b.cells[col].dataset.v;
        if (x === '' || y === '') return (x === '') - (y === '');
        var d = num ? parseFloat(x) - parseFloat(y) : x.localeCompare(y);
        return asc ? d : -d;
      }});
      rows.forEach(function (r) {{ body.appendChild(r); }});
    }});
    var rz = th.querySelector('.rz');
    rz.addEventListener('pointerdown', function (ev) {{
      ev.preventDefault(); ev.stopPropagation();
      var x0 = ev.clientX, w0 = th.offsetWidth;
      rz.setPointerCapture(ev.pointerId);
      function move(e) {{
        var w = Math.max(40, w0 + e.clientX - x0) + 'px';
        th.style.width = w; th.style.minWidth = w; th.style.maxWidth = w;
        Array.prototype.forEach.call(tb.tBodies[0].rows, function (r) {{
          var td = r.cells[col]; td.style.maxWidth = w;
          td.style.overflow = 'hidden'; td.style.textOverflow = 'ellipsis';
        }});
      }}
      function up() {{
        rz.removeEventListener('pointermove', move);
        rz.removeEventListener('pointerup', up);
      }}
      rz.addEventListener('pointermove', move);
      rz.addEventListener('pointerup', up);
    }});
    th.style.position = 'sticky';
  }});
}})();
</script>"""


def _legend_html(ranked_by_drive: bool) -> str:
    order = " · ".join(f"{_dot(n)} {n}" for n in ATHLETES)
    lamps = done_marker({ATHLETES[0]: True, ATHLETES[2]: True})[0]
    ranked = done_marker({ATHLETES[0]: True, ATHLETES[2]: True}, 3)[0]
    last = (f"{ranked}&nbsp;{open_marker(3)[0]}&nbsp; the top {CANDIDATE_PINS} "
            f"recommendations for the filters below, numbered. Hover any "
            f"parkrun that matches for its rank and driving times. "
            f"{CROSSING} marks a drive that {CROSSING_NOTE}."
            if ranked_by_drive else
            "Parkruns matching the filters below get a layer of their own, "
            "so the others can be switched off.")
    return (
        "<div style='font-size:.85rem;opacity:.85;margin:.25rem 0 .5rem;"
        "line-height:1.9'>"
        f"{lamps}&nbsp; run by at least one of them — a lamp per runner ({order}, "
        f"left to right), lit if they have run it.<br>"
        f"{open_marker()[0]}&nbsp; run by none of them.<br>{last}</div>")


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def render_h2h_view(version, mh: pd.DataFrame | None) -> None:
    """`mh` is the filtered head-to-head slice, or None when no classification
    is picked: pies pooled across 2-way and 3-way contests would not mean
    one thing."""
    if mh is None:
        st.info("Pick a head-to-head classification above to show the map.")
        return
    events = load_events_geo(version)
    venues = h2h_venues(mh, events)
    if not venues:
        st.info("No head-to-heads match those filters.")
        return
    n_occ = mh.drop_duplicates(["event_id", "run_date"]).shape[0]
    st.caption(f"**{fmt_n(len(venues))}** venue{'s' if len(venues) != 1 else ''} · "
               f"**{fmt_n(n_occ)}** head-to-head{'s' if n_occ != 1 else ''}. Each "
               f"circle is sized by how many head-to-heads happened there and "
               f"split by who won them.")
    fmap, counted = build_h2h_view_map(venues)
    _show_map(fmap, counted, "t5_map_h2h", "venues", mainland_ids(events))


# The planner's filters sit behind a "⚙️ Filters" button (chosen 1 Oct 2026
# over the sidebar, which every tab shares, so filters there stayed on show
# from other tabs). The panel belongs to this tab, keeps the map at the top
# of it on a phone, and has a Close button there (closable_popover).
def _planner_filters(*, countries, hc, travel, has_travel, base) -> dict:
    """Every planner filter, drawn into the current container — the Filters
    panel, about 300px wide inside on a 390px phone, so rows stack rather
    than sit side by side (bar the ranges: RANGE_ROW_CSS)."""
    out: dict = {}
    routed = ([n for n in ATHLETES
               if not travel[travel["athlete_name"] == n].empty]
              if has_travel else [])
    st.button("Clear all filters", key="t5_clear", width="stretch",
              on_click=clear_planner_filters, args=(routed,),
              help="Every filter to its neutral setting: miles, ranked on "
                   "driving time by everyone, anyone, any head-to-head, the "
                   "full driving ranges, every country, nothing left out.")

    units, metric, rank_by = "miles", "time", None
    if has_travel:
        c1, c2 = st.container(), st.container()
        units = c1.segmented_control("Units", ["miles", "km"],
                                     default=_first("t5_units", "miles"),
                                     key="t5_units") or "miles"
        metric_label = c2.segmented_control(
            "Rank by total", ["Driving time", "Driving distance"],
            default=_first("t5_rank_metric", "Driving time"),
            key="t5_rank_metric",
            help="What the recommendations are ordered by; the other breaks "
                 "ties.") or "Driving time"
        metric = "distance" if metric_label == "Driving distance" else "time"
        rank_by = st.multiselect(
            "… of", routed,
            default=_first("t5_rank_by",
                           [n for n in routed if n in DEFAULT_RANK_BY]),
            key="t5_rank_by",
            help="Whose journeys are summed — one, two or all three. A runner "
                 "left out is marked as not ranked.")
    out.update(units=units, metric=metric, rank_by=rank_by)

    # On by default: a crossing is a choice the viewer makes, not a rule
    # built into the data — the drive times are there, flagged.
    if "t5_crossings" not in st.session_state:
        st.session_state["t5_crossings"] = True
    out["include_crossings"] = st.toggle(
        f"Include sea crossings {CROSSING}", key="t5_crossings",
        help="Parkruns reached by Channel Tunnel or ferry — Northern Ireland, "
             "the islands, Ireland and Europe. Their driving times count "
             "sailing time only: no check-in, no wait for a departure.")

    classes = sorted(hc["classification"].unique())
    out["done_filter"] = _choice_rows("Who has run it", ATHLETES,
                                      DONE_CHOICES, "t5_done",
                                      default=DEFAULT_DONE)
    out["h2h_filter"] = _choice_rows("Head-to-heads there", classes,
                                     H2H_CHOICES, "t5_h2h", dot=False)

    minutes_range, distance_range = {}, {}
    if has_travel:
        unit, per = _unit(units)
        for title, metric_key in (("Driving time (min)", "min"),
                                  (f"Driving distance ({unit})", "dist")):
            tops = {}
            for name in ATHLETES:
                mine = travel[travel["athlete_name"] == name]
                if not mine.empty:
                    tops[name] = (
                        track_top(mine["duration_s"].max() / 60, TIME_CAP_MIN)
                        if metric_key == "min" else
                        track_top(mine["distance_m"].max() / per, DIST_CAP[unit],
                                  float))
            st.markdown(f"**{title}**")
            capped = [t for t, open_top in tops.values() if open_top]
            if capped:
                st.caption("The right-hand end means "
                           + (f"{_fmt_min(capped[0])} or more."
                              if metric_key == "min" else
                              f"{capped[0]:,.0f} {unit} or more."))
            for name in ATHLETES:
                _name_cell(st, name)
                cell = st.container()
                if name not in tops:
                    cell.caption("No home set — no driving times.")
                    continue
                top, open_top = tops[name]
                with cell:
                    if metric_key == "min":
                        minutes_range[name] = range_filter(
                            f"{name} driving time", f"t5_min_{name}", top, 5,
                            "min", open_top)
                    else:
                        # Keyed by unit: a mile range is not a km range.
                        distance_range[name] = range_filter(
                            f"{name} driving distance",
                            f"t5_dist_{name}_{units}", top, 0.1, unit, open_top)
    out.update(minutes_range=minutes_range, distance_range=distance_range)

    out["countries"] = st.multiselect(
        "Countries", list(countries),
        format_func=lambda c: countries.get(c, c),
        key="t5_countries", placeholder="All countries")

    # Session state only: a reload clears it. Options are every candidate, not
    # just the current matches, so changing a filter never orphans an exclusion.
    out["exclude"] = st.multiselect(
        "Leave out these parkruns", base["event_id"].tolist(),
        format_func=dict(zip(base["event_id"], base["short_name"])).get,
        key="t5_exclude", placeholder="None left out")
    return out


def render_planner(version, h2h: pd.DataFrame) -> None:
    events = load_events_geo(version)
    done = load_done(version)
    dt = done_table(done)
    hc = h2h_counts(h2h)

    parkruns = regular_parkruns(events, done)
    countries = country_options(parkruns)

    tv = travel_version()
    travel = load_travel(tv) if tv is not None else None
    has_travel = travel is not None and not travel.empty

    st.caption(
        "Every regular parkrun, with a square per runner for who has run it. "
        "The filters pick out possible next parkruns"
        + (", with driving times from each runner's neighbourhood to every "
           "parkrun reachable by road, Channel Tunnel or ferry — free-flow "
           "estimates, no traffic." if has_travel else
           ". There are no driving times in this database yet.")
    )
    base = plan_candidates(events, done, None, done_filter={})
    with closable_popover("⚙️ Filters", key="t5_filters_pop", width="stretch"):
        f = _planner_filters(countries=countries, hc=hc, travel=travel,
                             has_travel=has_travel, base=base)
    st.markdown(_legend_html(has_travel), unsafe_allow_html=True)

    units, rank_by = f["units"], f["rank_by"]
    cands = plan_candidates(
        events, done, travel, done_filter=f["done_filter"], h2h=hc,
        h2h_filter=f["h2h_filter"], minutes_range=f["minutes_range"],
        distance_range=f["distance_range"], units=units, rank_by=rank_by,
        rank_metric=f["metric"], exclude=f["exclude"],
        include_crossings=f["include_crossings"])
    cands = filter_countries(cands, f["countries"])
    filtered = (any(v != "Any" for v in f["done_filter"].values())
                or any(v != "Any" for v in f["h2h_filter"].values())
                or any(v is not None for v in f["minutes_range"].values())
                or any(v is not None for v in f["distance_range"].values())
                or bool(f["exclude"]) or bool(rank_by)
                or not f["include_crossings"])
    # Ranking alone counts: with every filter at "any" the top recommendations
    # are simply the nearest parkruns by the chosen total. Only the top
    # CANDIDATE_PINS are marked, so an unfiltered list does not cover the map.

    fmap, counted = build_planner_map(
        parkruns=filter_countries(parkruns, f["countries"]), dt=dt,
        candidates=cands if filtered else None, h2h_by_event=h2h_index(hc),
        units=units, rank_by=rank_by, metric=f["metric"])
    _show_map(fmap, counted, "t5_map_plan", "parkruns", mainland_ids(events))

    if not filtered:
        st.caption("Set a filter to pick out possible next parkruns.")
        return
    ranked = "total_min" in cands
    if ranked:
        tail = (f" — the first {CANDIDATE_PINS} are numbered on the map."
                if len(cands) > CANDIDATE_PINS else ".")
    else:
        tail = (", in A–Z order — there are no driving times to rank them "
                "by. Switch off the other layers to see only the matches.")
    st.markdown(f"**{fmt_n(len(cands))}** parkrun{'s' if len(cands) != 1 else ''} match"
                + ("" if len(cands) != 1 else "es") + tail)
    if not cands.empty:
        st.html(results_table_html(
                    _results_table(cands, units, rank_by, f["metric"])),
                unsafe_allow_javascript=True)

"""Tab 5 — where they meet, and where to go next.

Built as a trial tab 7 beside the old head-to-head-only map, and moved into
that map's place — tab 5 — on 1 Oct 2026. A toggle switches between two views
of one Folium map, each with only its own filters:

- **Head-to-heads** — one pie per venue, sized by the number of head-to-heads
  and split by who won them (the old tab 5's map), filtered by
  classification, year and season.
- **Planner** (the default) — every regular (5k) parkrun in three layers:
  the top recommendations (numbered), parkruns at least one of them has run
  (a lamp per athlete in a dark housing) and parkruns none of them has run (a
  black circle). The map opens on the recommendations alone. Filtered — in a
  "⚙️ Filters" popover — by country, who has run it, which head-to-heads have
  happened there and, when the database holds `travel_times`, driving time
  and distance from each home; ranked by total driving time or distance.

Driving times come from `travel_times` (parkrun_travel.py), which every
refresh tops up and the deploy snapshot ships. A database without it still
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

import folium
import pandas as pd
import streamlit as st
from branca.element import MacroElement
from jinja2 import Template
from streamlit_folium import st_folium

from parkrun_calendar import theme as cal_theme
from parkrun_core import UK_COUNTRY_CODE, is_mainland
from parkrun_ui import ATHLETE_COLORS, BUGGY_GLYPH, _read_sql, closable_popover

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

# The map opens on London. A centre and zoom rather than fit_bounds: every
# tab's content is rendered while hidden, and Leaflet fits bounds to a
# zero-size box as the whole world. LONDON_BOUNDS is Greater London, what the
# caption counts; the corner label counts the real view.
LONDON_CENTER = (51.49, -0.12)
LONDON_ZOOM = 10
LONDON_BOUNDS = [[51.28, -0.52], [51.70, 0.34]]

LAYER_H2H = "Head-to-heads"
LAYER_DONE = "parkruns done (min 1 person)"
LAYER_NOT_DONE = "parkruns not done by anyone"
LAYER_TOP = "top recommendations"
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
@st.cache_data(show_spinner=False)
def load_events_geo(version) -> pd.DataFrame:
    return _read_sql(
        """
        SELECT e.event_id, e.short_name, e.latitude, e.longitude, e.live,
               e.seriesid, e.country_code,
               coalesce(c.country_name, 'Unknown') AS country_name
        FROM parkrun.events e
        LEFT JOIN parkrun.country_lookup c USING (country_code)
        WHERE e.latitude IS NOT NULL AND e.longitude IS NOT NULL
        """
    )


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


def filter_countries(df: pd.DataFrame, countries) -> pd.DataFrame:
    """Empty means all — the app's multiselect convention."""
    return df[df["country_name"].isin(countries)] if countries else df


def in_bounds(df: pd.DataFrame, bounds) -> pd.Series:
    (s, w), (n, e) = bounds
    return df["latitude"].between(s, n) & df["longitude"].between(w, e)


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
                    exclude=()) -> pd.DataFrame:
    """The planner's shortlist.

    `events` is every event (load_events_geo); only live 5k mainland-GB ones are
    candidates. `done_filter` maps athlete → "Any" | "Done" | "Not done", and
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
    c = events[(events["seriesid"] == 1) & events["live"]
               & (events["country_code"] == UK_COUNTRY_CODE)]
    # A Series, not a bare list: an empty list indexes *columns* (`c[[]]`),
    # so an empty database crashed on the next line instead of matching none.
    c = c[pd.Series([is_mainland(la, lo) for la, lo in
                     zip(c["latitude"], c["longitude"])],
                    index=c.index, dtype=bool)]
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
            f"{escape(str(k))} {v}" + (f" ({int(bdict[k])} {BUGGY_GLYPH})"
                                       if bdict.get(k) else "")
            for k, v in sorted(wdict.items(), key=lambda x: -x[1]))
        tip = (f"<b>{escape(str(c.at[event_id, 'short_name']))}</b><br>"
               f"{count} head-to-head{'s' if count != 1 else ''}<br>{breakdown}")
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
(function () {
  var map = {{ this._parent.get_name() }};
  var ctl = {{ this.control_name }};
  if (map.getContainer().clientWidth >= {{ this.max_width }}) { return; }
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


def _layer_control(fmap: folium.Map) -> None:
    ctl = folium.LayerControl(collapsed=False).add_to(fmap)
    CollapseLayersWhenNarrow(ctl).add_to(fmap)


class InViewCounter(MacroElement):
    """A corner label — "38 of 205 parkruns in view · 167 outside" — kept
    current in the browser on every pan, zoom and layer toggle.

    In the browser rather than through `st_folium(returned_objects=["bounds"])`
    because that reruns the whole app on every drag. Counts distinct parkruns
    across the layers currently switched on, so a venue drawn in two layers
    counts once."""

    _template = Template("""
{% macro script(this, kwargs) %}
(function () {
  var map = {{ this._parent.get_name() }};
  var layers = {{ this.layers_json }};
  var visible = {{ this.visible_json }};
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
      if (!visible[name]) return;
      layers[name].forEach(function (p) {
        all[p[0]] = 1;
        if (b.contains([p[1], p[2]])) inv[p[0]] = 1;
      });
    });
    var n = Object.keys(all).length, k = Object.keys(inv).length;
    ctl._d.innerHTML = '<b>' + k + '</b> of ' + n + ' parkrun' +
      (n === 1 ? '' : 's') + ' in view · <b>' + (n - k) + '</b> outside';
  }
  map.on('moveend zoomend', update);
  map.on('overlayadd', function (e) { visible[e.name] = true; update(); });
  map.on('overlayremove', function (e) { visible[e.name] = false; update(); });
  map.whenReady(update);
})();
{% endmacro %}
""")

    def __init__(self, layers: dict, visible: dict | None = None):
        super().__init__()
        self._name = "InViewCounter"
        self.layers_json = _js(
            {k: [[int(e), round(float(la), 5), round(float(lo), 5)]
                 for e, la, lo in pts] for k, pts in layers.items()})
        # Must match each layer's `show`, or the count starts out counting
        # layers that are switched off.
        self.visible_json = _js({k: (visible or {}).get(k, True) for k in layers})


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
    detail = ", ".join(f"{escape(c)} {n}" for c, n in parts)
    return f"Head-to-heads: <b>{total}</b> ({detail})"


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
            out.append(f"{_dot(name)} {name} — {n} run{'s' if n != 1 else ''}, "
                       f"last {last}")
    return out


def _title(row, extra: str = "") -> str:
    return (f"<b>{escape(str(row.short_name))}</b> "
            f"<span style='opacity:.65'>{escape(str(row.country_name))}"
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
        text = f"{name} {_fmt_min(m)}, {d:.1f} {unit}"
        if rank_by and name not in rank_by:
            text = f"<span style='{GREY_STYLE}'>{text} · {NOT_RANKED}</span>"
        drives.append(f"{_dot(name)} {text}")
    if drives:
        lines.append("<span style='opacity:.65'>Driving from home</span>")
        lines += drives
    tot_m = getattr(row, "total_min", None)
    if tot_m is not None and not pd.isna(tot_m):
        who = ", ".join(rank_by)
        t = f"Total driving time ({who}): {_fmt_min(tot_m)}"
        dd = f"Total distance ({who}): {row.total_dist:.1f} {unit}"
        lines += ([f"<b>{t}</b>", dd] if metric == "time" else [f"<b>{dd}</b>", t])
    return _tip(lines, row.event_id, h2h_by_event)


# --------------------------------------------------------------------------- #
# The maps
# --------------------------------------------------------------------------- #
def _base_map() -> folium.Map:
    # The tiles are added with control=False: there is one background map, so
    # the layer box's "openstreetmap" base-layer radio button chose between
    # one option and did nothing.
    fmap = folium.Map(location=list(LONDON_CENTER), zoom_start=LONDON_ZOOM,
                      tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", control=False).add_to(fmap)
    fmap.get_root().header.add_child(folium.Element(f"<style>{MAP_CSS}</style>"))
    t = cal_theme()
    MapSheet(t.tip_bg, t.tip_fg).add_to(fmap)
    return fmap


def build_h2h_view_map(venues: list):
    """The head-to-head view: a pie per venue, opening on London, with the counter.
    Built with JsMarkers, like the planner, so both views label alike."""
    fmap = _base_map()
    g = folium.FeatureGroup(name=LAYER_H2H, show=True).add_to(fmap)
    icons, points = {}, []
    for eid, lat, lon, d, svg, tip in venues:
        icons[str(eid)] = (svg, d, d, d / 2, d / 2)
        points.append([round(lat, 5), round(lon, 5), str(eid), tip])
    JsMarkers(g, icons, points).add_to(fmap)
    counted = {LAYER_H2H: [(v[0], v[1], v[2]) for v in venues]}
    _layer_control(fmap)
    InViewCounter(counted).add_to(fmap)
    return fmap, counted


def build_planner_map(*, parkruns: pd.DataFrame, dt: pd.DataFrame,
                      candidates: pd.DataFrame | None, h2h_by_event: dict,
                      units: str = "miles", rank_by=None,
                      metric: str = "time"):
    """The planner view. `parkruns` is every regular parkrun to draw (already
    country-filtered), `dt` the done_table, `candidates` the ranked matches
    (None when no filter is set).

    Three layers, and every parkrun is in exactly one: the top
    CANDIDATE_PINS recommendations, then the rest split by whether any of them
    has run it. So switching the other two off leaves only the
    recommendations. A recommendation keeps the look of its kind — lamps if
    someone has run it, a black circle if not — with its number added, and
    every match's tooltip gives its rank and driving times.

    The candidates are a *ranking* only when they carry `total_min`. Without
    it (no driving times, or nobody chosen to rank by) the list is merely
    alphabetical, so every match goes in LAYER_MATCH, unnumbered, with the
    ordinary tooltip."""
    fmap = _base_map()
    ranked, matched = {}, set()
    if candidates is not None:
        if "total_min" in candidates:
            ranked = {r.event_id: (i, r) for i, r in
                      enumerate(candidates.itertuples(index=False), start=1)}
        else:
            matched = set(candidates["event_id"])
    ran = set(dt.index[dt[ATHLETES].sum(axis=1) > 0]) if not dt.empty else set()

    # Per layer: (icons by key, JsMarkers points, the counter's points).
    layers = {name: ({}, [], []) for name in
              (LAYER_DONE, LAYER_NOT_DONE, LAYER_TOP, LAYER_MATCH)}
    for row in parkruns.itertuples(index=False):
        eid = row.event_id
        rank, crow = ranked.get(eid, (None, None))
        top = rank if rank is not None and rank <= CANDIDATE_PINS else None
        tip = (_candidate_tip(rank, crow, units, h2h_by_event, dt, rank_by,
                              metric) if rank
               else _base_tip(row, dt, h2h_by_event))
        is_done = eid in ran
        if is_done:
            r = dt.loc[eid]
            flags = {n: bool(r[n] > 0) for n in ATHLETES}
            key = "".join("1" if flags[n] else "0" for n in ATHLETES)
            key += f"#{top}" if top else ""
        else:
            key = f"#{top}" if top else "dot"
        layer = (LAYER_TOP if top else LAYER_MATCH if eid in matched
                 else LAYER_DONE if is_done else LAYER_NOT_DONE)
        icons, points, pts = layers[layer]
        if key not in icons:
            icons[key] = done_marker(flags, top) if is_done else open_marker(top)
        points.append([round(float(row.latitude), 5), round(float(row.longitude), 5),
                       key, tip, 1000 if top else 0])
        pts.append((eid, row.latitude, row.longitude))

    counted: dict[str, list] = {}
    shown: dict[str, bool] = {}
    # The map opens on the picked-out parkruns alone — the recommendations, or
    # the matches where nothing is ranked — with every other parkrun one tap
    # away in the layer box. With nothing picked out, everything shows.
    picked = bool(layers[LAYER_TOP][1] or layers[LAYER_MATCH][1])
    # Stacking is per marker (latitude, then the z lift for a top
    # recommendation), not per layer, so this order only sets the layer box.
    for name in (LAYER_TOP, LAYER_MATCH, LAYER_DONE, LAYER_NOT_DONE):
        icons, points, pts = layers[name]
        if name in (LAYER_TOP, LAYER_MATCH) and not points:
            continue   # nothing picked out, so no layer to switch
        shown[name] = name in (LAYER_TOP, LAYER_MATCH) or not picked
        g = folium.FeatureGroup(name=name, show=shown[name]).add_to(fmap)
        JsMarkers(g, icons, points).add_to(fmap)
        counted[name] = pts

    _layer_control(fmap)
    InViewCounter(counted, visible=shown).add_to(fmap)
    fmap.pr_shown = shown        # for _show_map's caption, which counts these
    return fmap, counted


def _show_map(fmap, counted: dict, key: str, what: str) -> None:
    shown = getattr(fmap, "pr_shown", None)
    pts = pd.DataFrame(
        [(e, la, lo) for name, layer in counted.items()
         if shown is None or shown.get(name, True) for e, la, lo in layer],
        columns=["event_id", "latitude", "longitude"]).drop_duplicates("event_id")
    n_all = len(pts)
    n_in = int(in_bounds(pts, LONDON_BOUNDS).sum())
    st.caption(
        f"The map opens on London: **{n_in}** of **{n_all}** {what} are in "
        f"Greater London, **{n_all - n_in}** outside it. The count in the "
        f"map's corner follows the actual view as you pan, zoom and switch "
        f"layers."
    )
    st_folium(fmap, height=560, returned_objects=[], key=key,
              center=LONDON_CENTER, zoom=LONDON_ZOOM, use_container_width=True)


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


def range_filter(label: str, key: str, top, step, unit: str):
    """A two-ended slider between two number boxes, kept in step.

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

    c1, c2, c3 = st.columns([1, 3, 1], vertical_alignment="center")
    c1.number_input(f"{label}: from ({unit})", zero, top, step=step, key=lk,
                    format=fmt, on_change=from_boxes, label_visibility="collapsed")
    c2.slider(label, zero, top, step=step, key=rk, format=fmt,
              on_change=from_slider, label_visibility="collapsed")
    c3.number_input(f"{label}: to ({unit})", zero, top, step=step, key=hk,
                    format=fmt, on_change=from_boxes, label_visibility="collapsed")
    lo, hi = ss[rk]
    return None if (lo, hi) == (zero, top) else (lo, hi)


# Session keys the planner's filters live under. Clearing deletes them, and
# each widget comes back at its default on the rerun — for a range, the full
# track (range_filter re-seeds its three keys when they are missing). Units
# and the view toggle are settings, not filters, and are left alone.
PLANNER_FILTER_KEYS = ("t5_countries", "t5_done_", "t5_h2h_", "t5_min_",
                       "t5_dist_", "t5_rank_by", "t5_exclude")


def clear_planner_filters() -> None:
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith(PLANNER_FILTER_KEYS):
            del st.session_state[k]


def _choice_rows(title: str, rows: list, choices: list, key_prefix: str,
                 dot=True) -> dict:
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
            default=_first(f"{key_prefix}_{label}", choices[0]),
            key=f"{key_prefix}_{label}", label_visibility="collapsed") or choices[0]
    return out


def _results_table(c: pd.DataFrame, units: str, rank_by=None):
    """The matches as a table: a DataFrame, or a Styler when distances need
    formatting or runners outside the ranking are greyed and italic.
    Distances to 0.1."""
    unit, _ = _unit(units)
    c = c.reset_index(drop=True)   # country-filtered upstream: realign rows
    rank_by = list(rank_by or [])
    ranked = "total_min" in c
    # A rank column only for a ranking: on an A-Z list a "#" reads as one.
    out = pd.DataFrame({"parkrun": c["short_name"]})
    if ranked:
        out.insert(0, "#", range(1, len(c) + 1))
    greyed = []
    for name in ATHLETES:
        if f"min_{name}" not in c:
            continue
        left_out = ranked and rank_by and name not in rank_by
        tag = f" ({NOT_RANKED})" if left_out else ""
        tcol, dcol = f"{name} time{tag}", f"{name} {unit}{tag}"
        out[tcol] = c[f"min_{name}"].map(lambda v: "—" if pd.isna(v) else _fmt_min(v))
        out[dcol] = c[f"dist_{name}"].round(1)
        if tag:
            greyed += [tcol, dcol]
    if ranked:
        out["Total time"] = c["total_min"].map(_fmt_min)
        out[f"Total {unit}"] = c["total_dist"].round(1)
    out["Run by"] = c.apply(
        lambda r: ", ".join(f"{n} ({int(r[n])})" for n in ATHLETES if r[n] > 0)
        or "nobody", axis=1)
    fmt = {col: "{:.1f}" for col in out.columns
           if col.startswith(tuple(f"{n} {unit}" for n in ATHLETES))
           or col == f"Total {unit}"}
    if not (fmt or greyed):
        return out
    sty = out.style.format(fmt, na_rep="—")
    return sty.map(lambda _: GREY_STYLE, subset=greyed) if greyed else sty


def _legend_html(ranked_by_drive: bool) -> str:
    order = " · ".join(f"{_dot(n)} {n}" for n in ATHLETES)
    lamps = done_marker({ATHLETES[0]: True, ATHLETES[2]: True})[0]
    ranked = done_marker({ATHLETES[0]: True, ATHLETES[2]: True}, 3)[0]
    last = (f"{ranked}&nbsp;{open_marker(3)[0]}&nbsp; the top {CANDIDATE_PINS} "
            f"recommendations for the filters below, numbered. Hover any "
            f"parkrun that matches for its rank and driving times."
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
    venues = h2h_venues(mh, load_events_geo(version))
    if not venues:
        st.info("No head-to-heads match those filters.")
        return
    n_occ = mh.drop_duplicates(["event_id", "run_date"]).shape[0]
    st.caption(f"**{len(venues)}** venue{'s' if len(venues) != 1 else ''} · "
               f"**{n_occ}** head-to-head{'s' if n_occ != 1 else ''}. Each "
               f"circle is sized by how many head-to-heads happened there and "
               f"split by who won them.")
    fmap, counted = build_h2h_view_map(venues)
    _show_map(fmap, counted, "t5_map_h2h", "venues")


# The planner's filters sit behind a "⚙️ Filters" button (chosen 1 Oct 2026
# over the sidebar, which every tab shares, so filters there stayed on show
# from other tabs). The panel belongs to this tab, keeps the map at the top
# of it on a phone, and has a Close button there (closable_popover).
def _planner_filters(*, counts, hc, travel, has_travel, base) -> dict:
    """Every planner filter, drawn into the current container (the Filters
    panel, a column's width — so rows stack rather than sit side by side)."""
    out: dict = {}
    out["countries"] = st.multiselect(
        "Countries", list(counts.index),
        format_func=lambda c: f"{c} ({counts[c]})",
        key="t5_countries", placeholder="All countries")
    st.button("Clear all filters", key="t5_clear", width="stretch",
              on_click=clear_planner_filters,
              help="Every filter back to its default: all countries, anyone, "
                   "any head-to-head, the full driving ranges, nothing left "
                   "out. Units stay as they are.")

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
        routed = [n for n in ATHLETES
                  if not travel[travel["athlete_name"] == n].empty]
        rank_by = st.multiselect(
            "… of", routed, default=_first("t5_rank_by", routed),
            key="t5_rank_by",
            help="Whose journeys are summed — one, two or all three. A runner "
                 "left out is marked as not ranked.")
    out.update(units=units, metric=metric, rank_by=rank_by)

    classes = sorted(hc["classification"].unique())
    out["done_filter"] = _choice_rows("Who has run it", ATHLETES,
                                      DONE_CHOICES, "t5_done")
    out["h2h_filter"] = _choice_rows("Head-to-heads there", classes,
                                     H2H_CHOICES, "t5_h2h", dot=False)

    minutes_range, distance_range = {}, {}
    if has_travel:
        unit, per = _unit(units)
        for title, metric_key in (("Driving time (min)", "min"),
                                  (f"Driving distance ({unit})", "dist")):
            st.markdown(f"**{title}**")
            for name in ATHLETES:
                mine = travel[travel["athlete_name"] == name]
                _name_cell(st, name)
                cell = st.container()
                if mine.empty:
                    cell.caption("No home set — no driving times.")
                    continue
                with cell:
                    if metric_key == "min":
                        top = int(math.ceil(mine["duration_s"].max() / 60 / 10) * 10)
                        minutes_range[name] = range_filter(
                            f"{name} driving time", f"t5_min_{name}", top, 5, "min")
                    else:
                        top = float(math.ceil(mine["distance_m"].max() / per / 10) * 10)
                        # Keyed by unit: a mile range is not a km range.
                        distance_range[name] = range_filter(
                            f"{name} driving distance",
                            f"t5_dist_{name}_{units}", top, 0.1, unit)
    out.update(minutes_range=minutes_range, distance_range=distance_range)

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
    counts = parkruns["country_name"].value_counts()

    tv = travel_version()
    travel = load_travel(tv) if tv is not None else None
    has_travel = travel is not None and not travel.empty

    st.caption(
        "Every regular parkrun, with a square per runner for who has run it. "
        "The filters pick out possible next parkruns on mainland Great Britain"
        + (", with driving times from each runner's neighbourhood — "
           "free-flow estimates, no traffic." if has_travel else
           ". There are no driving times in this database yet.")
    )
    base = plan_candidates(events, done, None, done_filter={})
    with closable_popover("⚙️ Filters", key="t5_filters_pop", width="stretch"):
        f = _planner_filters(counts=counts, hc=hc, travel=travel,
                             has_travel=has_travel, base=base)
    st.markdown(_legend_html(has_travel), unsafe_allow_html=True)

    units, rank_by = f["units"], f["rank_by"]
    cands = plan_candidates(
        events, done, travel, done_filter=f["done_filter"], h2h=hc,
        h2h_filter=f["h2h_filter"], minutes_range=f["minutes_range"],
        distance_range=f["distance_range"], units=units, rank_by=rank_by,
        rank_metric=f["metric"], exclude=f["exclude"])
    cands = filter_countries(cands, f["countries"])
    filtered = (any(v != "Any" for v in f["done_filter"].values())
                or any(v != "Any" for v in f["h2h_filter"].values())
                or any(v is not None for v in f["minutes_range"].values())
                or any(v is not None for v in f["distance_range"].values())
                or bool(f["exclude"]) or bool(rank_by))
    # Ranking alone counts: with every filter at "any" the top recommendations
    # are simply the nearest parkruns by the chosen total. Only the top
    # CANDIDATE_PINS are marked, so an unfiltered list does not cover the map.

    fmap, counted = build_planner_map(
        parkruns=filter_countries(parkruns, f["countries"]), dt=dt,
        candidates=cands if filtered else None, h2h_by_event=h2h_index(hc),
        units=units, rank_by=rank_by, metric=f["metric"])
    _show_map(fmap, counted, "t5_map_plan", "parkruns")

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
    st.markdown(f"**{len(cands)}** parkrun{'s' if len(cands) != 1 else ''} match"
                + ("" if len(cands) != 1 else "es") + tail)
    if not cands.empty:
        st.dataframe(_results_table(cands, units, rank_by),
                     hide_index=True, width="stretch",
                     height=min(420, 38 + 35 * len(cands)))

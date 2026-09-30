"""Tab 7 — where they meet, and where to go next.

The enhanced successor to tab 5, run beside it until one is chosen (TODO.md §
Where they meet). A toggle switches between two views of one Folium map, each
with only its own filters:

- **Head-to-heads** — tab 5's pies (the same functions draw both), filtered by
  classification, year and season.
- **Planner** — every regular (5k) parkrun, with a square per athlete marking
  who has run it, and on top of that the planner's matches ("parkruns not
  done"): numbered for the top ranks, a plain badge for the rest. Filtered by
  country, who has run it, which head-to-heads have happened there and — when
  the database holds `travel_times` — driving time and distance from each home.

`travel_times` exists only in a local DB (parkrun_travel.py). The hosted app
gets every filter but the driving ones, and a note saying why.

The planner draws ~2,400 parkruns. Built as one folium Marker each, that is
several MB of generated script, so the markers are built in the browser
instead (`JsMarkers`): one compact list of points, and each distinct icon
defined once — there are only eight done-patterns per marker style.
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

from parkrun_core import UK_COUNTRY_CODE, is_mainland
from parkrun_ui import ATHLETE_COLORS, BUGGY_GLYPH, _read_sql

# Fixed athlete order for every per-athlete mark: a square means the same
# runner in every marker, so the order can never depend on the data.
ATHLETES = list(ATHLETE_COLORS)          # George, Raju, Duncan

VIEWS = ["Head-to-heads", "Planner"]
DONE_CHOICES = ["Any", "Done", "Not done"]
H2H_CHOICES = ["Any", "Happened", "Never"]

# Square markers, drawn like the calendar cells (parkrun_calendar: rounded
# squares, the same empty grey). `squares_svg` can draw a row or a column,
# bare or "lit" in a dark housing like traffic lights; the planner uses
# DONE_STYLE, chosen on 30 Sep 2026 from the four. A parkrun nobody has run
# is a black circle instead — there is no lamp worth drawing. A top
# recommendation's number goes inside the housing, to the left of the lamps
# (chosen 30 Sep 2026 over a badge above it and a tag beside it).
DONE_STYLE = "Row, lit"
SQ, SQ_GAP, SQ_PAD, SQ_RX = 8, 2, 2, 1.5
EMPTY = "#ebedf0"          # parkrun_calendar.EMPTY_LIGHT
EMPTY_EDGE = "#aeb4bb"     # so an empty square still shows against the map
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

CANDIDATE_PINS = 25   # the top recommendations, numbered on the map

# How a marker's details show on a phone (a pointer that cannot hover). Three
# ways, compared with a dev selector (TODO.md): a compact tooltip, a pop-up
# that pans the map until it fits, or a panel docked along the bottom of the
# map. A desktop always gets the tooltip, whatever is picked.
PHONE_LABELS = ["Tooltip", "Pop-up", "Panel"]
_LABEL_MODE = {"Tooltip": "tooltip", "Pop-up": "popup", "Panel": "panel"}

# Label styling, injected into each map. The compact tooltip wraps at a fixed
# width instead of Leaflet's single unbroken line, which ran off the side of a
# phone-width map; the panel spans the map's foot, so it always fits.
LABEL_CSS = """
.pr-compact.leaflet-tooltip { font: 11px/1.3 sans-serif; padding: 3px 6px;
  white-space: normal; width: max-content; max-width: 210px; }
.pr-pop .leaflet-popup-content { margin: 6px 8px; font: 11px/1.3 sans-serif; }
.pr-pop .leaflet-popup-content-wrapper { border-radius: 6px; }
.pr-pop .leaflet-popup-tip-container { display: none; }
.pr-panel { position: absolute; left: 0; right: 0; bottom: 0; z-index: 1000;
  background: rgba(255,255,255,.97); color: #222; font: 12px/1.35 sans-serif;
  padding: 6px 30px 7px 9px; box-shadow: 0 -1px 4px rgba(0,0,0,.25);
  max-height: 45%; overflow-y: auto; display: none; }
.pr-panel-x { position: absolute; top: 2px; right: 6px; border: 0;
  background: none; font: 18px/1 sans-serif; color: #555; padding: 4px; }
"""
KM_PER_MILE = 1.609344


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


def _within(series: pd.Series, rng) -> pd.Series:
    lo, hi = rng
    return series.between(lo, hi)


def plan_candidates(events: pd.DataFrame, done: pd.DataFrame,
                    travel: pd.DataFrame | None, *, done_filter: dict,
                    h2h: pd.DataFrame | None = None,
                    h2h_filter: dict | None = None,
                    minutes_range: dict | None = None,
                    distance_range: dict | None = None, units: str = "miles",
                    rank_by: list | None = None, exclude=()) -> pd.DataFrame:
    """The planner's shortlist.

    `events` is every event (load_events_geo); only live 5k mainland-GB ones are
    candidates. `done_filter` maps athlete → "Any" | "Done" | "Not done", and
    `h2h_filter` maps classification → "Any" | "Happened" | "Never", read
    against `h2h` (h2h_counts). `travel` is travel_times (load_travel), or
    None when there is none — then the driving ranges and the ranking are
    skipped and the list is alphabetical. `minutes_range` / `distance_range`
    map athlete → (low, high), inclusive; an athlete without one is
    unconstrained. `rank_by` names the athletes whose driving times are summed
    into `total_min`, the sort key. `exclude` holds event_ids to drop.
    """
    c = events[(events["seriesid"] == 1) & events["live"]
               & (events["country_code"] == UK_COUNTRY_CODE)]
    c = c[[is_mainland(la, lo) for la, lo in zip(c["latitude"], c["longitude"])]]
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

    per = 1609.344 if units == "miles" else 1000.0
    w = travel.pivot_table(index="event_id", columns="athlete_name",
                           values=["duration_s", "distance_m"], aggfunc="first")
    for name in ATHLETES:
        if ("duration_s", name) in w:
            c[f"min_{name}"] = c["event_id"].map(w["duration_s"][name] / 60)
            c[f"dist_{name}"] = c["event_id"].map(w["distance_m"][name] / per)

    for name, rng in (minutes_range or {}).items():
        if rng is not None and f"min_{name}" in c:
            c = c[_within(c[f"min_{name}"], rng)]
    for name, rng in (distance_range or {}).items():
        if rng is not None and f"dist_{name}" in c:
            c = c[_within(c[f"dist_{name}"], rng)]

    cols = [f"min_{n}" for n in (rank_by or []) if f"min_{n}" in c]
    if cols:
        c = c.dropna(subset=cols)
        c["total_min"] = c[cols].sum(axis=1)
        c = c.sort_values(["total_min", "short_name"])
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
    head = (f'<svg width="{diameter}" height="{diameter}" '
            f'viewBox="0 0 {diameter} {diameter}" '
            f'style="{_SHADOW}">')
    if len(items) == 1:  # a full circle (one 360° arc won't render)
        name = items[0][0]
        return (head + f'<circle cx="{r}" cy="{r}" r="{r - 1}" '
                f'fill="{ATHLETE_COLORS.get(name, "#888888")}" '
                f'stroke="white" stroke-width="1"/></svg>')
    parts, a0 = [head], 0.0
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
    parts.append("</svg>")
    return "".join(parts)


def square_size(style: str) -> tuple[int, int]:
    """(width, height) of a square marker in that style."""
    long_side = 3 * SQ + 2 * SQ_GAP
    w, h = (long_side, SQ) if style.startswith("Row") else (SQ, long_side)
    if style.endswith("lit"):
        w, h = w + 2 * SQ_PAD, h + 2 * SQ_PAD
    return w, h


def _squares_body(done: dict, style: str, dx: float = 0, dy: float = 0,
                  extra_w: float = 0) -> list:
    """The housing (lit styles) and the squares, offset by (dx, dy). `extra_w`
    widens the housing on the left, with the squares moved over to make room
    — the space "In the housing" writes the rank into."""
    lit = style.endswith("lit")
    row = style.startswith("Row")
    w, h = square_size(style)
    off = SQ_PAD if lit else 0
    parts = []
    if lit:
        parts.append(f'<rect x="{dx}" y="{dy}" width="{w + extra_w}" height="{h}" '
                     f'rx="3" fill="{HOUSING}"/>')
    for i, name in enumerate(ATHLETES):
        step = i * (SQ + SQ_GAP)
        x, y = (off + step, off) if row else (off, off + step)
        x, y = x + dx + extra_w, y + dy
        if done.get(name):
            fill, edge = ATHLETE_COLORS[name], "white" if not lit else "none"
        else:
            fill, edge = (UNLIT, "none") if lit else (EMPTY, EMPTY_EDGE)
        stroke = "" if edge == "none" else f' stroke="{edge}" stroke-width=".8"'
        parts.append(f'<rect class="sq" data-athlete="{name}" x="{x}" y="{y}" '
                     f'width="{SQ}" height="{SQ}" rx="{SQ_RX}" fill="{fill}"{stroke}/>')
    return parts


def _svg(w, h, body: list) -> str:
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'style="{_SHADOW}">' + "".join(body) + "</svg>")


def _num(x, y, rank: int, fill: str = "white", size: int = 10) -> str:
    return (f'<text class="rank" x="{x}" y="{y}" dy=".35em" text-anchor="middle" '
            f'fill="{fill}" style="font:700 {size}px sans-serif">{rank}</text>')


def squares_svg(done: dict, style: str) -> str:
    """One rounded square per athlete in ATHLETES order — left to right in a
    row, top to bottom in a column. Their colour where they have run it;
    otherwise the calendar's empty grey, or an unlit lamp in a "lit" style."""
    w, h = square_size(style)
    return _svg(w, h, _squares_body(done, style))


# Each marker builder returns (svg, width, height, anchor_x, anchor_y), the
# anchor being the point on the icon that sits on the parkrun's location.
def done_marker(done: dict, rank: int | None = None) -> tuple:
    """A parkrun at least one of them has run: DONE_STYLE lamps, and for a top
    recommendation its number in the housing, left of the lamps. The anchor
    stays on the lamps, so a number never moves the parkrun."""
    hw, hh = square_size(DONE_STYLE)
    if rank is None:
        return squares_svg(done, DONE_STYLE), hw, hh, hw / 2, hh / 2
    nw = 11 if rank < 10 else 15
    w, h = hw + nw, hh
    body = _squares_body(done, DONE_STYLE, extra_w=nw)
    body.append(_num(SQ_PAD + (nw - 1) / 2, h / 2, rank, size=9))
    return _svg(w, h, body), w, h, nw + hw / 2, h / 2


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
# Head-to-head layer (shared with tab 5)
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
        breakdown = " · ".join(
            f"{k} {v}" + (f" ({int(bdict[k])} {BUGGY_GLYPH})"
                          if bdict.get(k) else "")
            for k, v in sorted(wdict.items(), key=lambda x: -x[1]))
        tip = (f"<b>{c.at[event_id, 'short_name']}</b><br>"
               f"{count} head-to-head{'s' if count != 1 else ''}<br>{breakdown}")
        venues.append((event_id, lat, lon, d, _pie_svg(wdict, d), tip))
    return venues


def build_h2h_map(mh: pd.DataFrame, coords: pd.DataFrame):
    """Folium map of head-to-head venues (tab 5). `mh` is a (filtered) slice of
    v_head_to_head; `coords` maps event_id → lat/lon/name. Returns a folium.Map,
    or None when there's nothing to plot."""
    venues = h2h_venues(mh, coords)
    if not venues:
        return None
    lats = [v[1] for v in venues]
    lons = [v[2] for v in venues]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]
    fmap = folium.Map(location=center, zoom_start=11 if len(venues) == 1 else 5,
                      tiles="OpenStreetMap", control_scale=True)
    for _, lat, lon, d, svg, tip in venues:
        folium.Marker(
            [lat, lon], icon=_icon(svg, d, d), tooltip=folium.Tooltip(tip),
        ).add_to(fmap)
    if len(venues) > 1:
        fmap.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
    return fmap


# --------------------------------------------------------------------------- #
# Browser-side pieces
# --------------------------------------------------------------------------- #
def _js(obj) -> str:
    """JSON for inlining in a <script>: `</` escaped so a parkrun name can never
    close the tag."""
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


class JsMarkers(MacroElement):
    """Markers built in the browser into an existing FeatureGroup.

    `icons` maps a key → (svg, width, height, anchor_x, anchor_y), each defined
    once; `points` is [lat, lon, icon_key, tooltip_html, z?] — the optional z
    lifts a marker (a top recommendation) above its neighbours. Thousands of points
    cost a few hundred KB this way, against several MB as folium Markers.

    `label_mode` (PHONE_LABELS, as its _LABEL_MODE value) decides how the
    details show where the pointer cannot hover; a hovering pointer always
    gets a tooltip. The group must already be on the map — the panel mode
    hangs its panel off `group._map`."""

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
  var map = group._map;
  var touch = window.matchMedia && window.matchMedia('(hover: none)').matches;
  var mode = touch ? '{{ this.label_mode }}' : 'tooltip';
  function panel() {
    if (map._prPanel) return map._prPanel;
    var el = L.DomUtil.create('div', 'pr-panel', map.getContainer());
    var body = L.DomUtil.create('div', '', el);
    var x = L.DomUtil.create('button', 'pr-panel-x', el);
    x.innerHTML = '&times;';
    L.DomEvent.disableClickPropagation(el);
    L.DomEvent.disableScrollPropagation(el);
    var hide = function () { el.style.display = 'none'; };
    L.DomEvent.on(x, 'click', hide);
    map.on('click', hide);
    map._prPanel = {show: function (h) { body.innerHTML = h; el.style.display = 'block'; }};
    return map._prPanel;
  }
  {{ this.points_json }}.forEach(function (p) {
    var m = L.marker([p[0], p[1]], {icon: icons[p[2]],
                                    zIndexOffset: (p[4] || 0) + {{ this.z }}});
    if (mode === 'popup') {
      // Top padding clears the zoom buttons and the layers icon, which
      // Leaflet draws above pop-ups.
      m.bindPopup(p[3], {maxWidth: 230, minWidth: 140, closeButton: false,
                         autoPanPaddingTopLeft: [8, 84],
                         autoPanPaddingBottomRight: [8, 8], className: 'pr-pop'});
    } else if (mode === 'panel') {
      m.on('click', function () { panel().show(p[3]); });
    } else {
      m.bindTooltip(p[3], {className: touch ? 'pr-compact' : ''});
    }
    m.addTo(group);
  });
})();
{% endmacro %}
""")

    def __init__(self, group: folium.FeatureGroup, icons: dict, points: list,
                 z: int = 0, label_mode: str = "tooltip"):
        super().__init__()
        self._name = "JsMarkers"
        self.label_mode = label_mode if label_mode in _LABEL_MODE.values() else "tooltip"
        self.group_name = group.get_name()
        self.icons_json = _js(icons)
        self.points_json = _js(points)
        self.z = int(z)


class CollapseLayersWhenNarrow(MacroElement):
    """Fold the layer box into Leaflet's layers icon on a narrow map.

    Expanded, it covered the top third of a phone-width map, and Leaflet
    draws controls above tooltips and pop-ups — so a label near the top of
    the map slid underneath it and could not be read. A tap on the icon opens
    it. Wide maps keep it open, as before."""

    _template = Template("""
{% macro script(this, kwargs) %}
(function () {
  var map = {{ this._parent.get_name() }};
  var ctl = {{ this.control_name }};
  if (map.getContainer().clientWidth < {{ this.max_width }}) ctl.collapse();
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

    def __init__(self, layers: dict):
        super().__init__()
        self._name = "InViewCounter"
        self.layers_json = _js(
            {k: [[int(e), round(float(la), 5), round(float(lo), 5)]
                 for e, la, lo in pts] for k, pts in layers.items()})
        self.visible_json = _js({k: True for k in layers})


# --------------------------------------------------------------------------- #
# Tooltips
# --------------------------------------------------------------------------- #
def _dot(name: str, on: bool = True) -> str:
    col = ATHLETE_COLORS[name] if on else EMPTY_EDGE
    return f"<span style='color:{col}'>■</span>"


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


def _base_tip(row, dt, h2h_by_event) -> str:
    lines = [_title(row)] + _runs_lines(row.event_id, dt)
    h = _h2h_line(row.event_id, h2h_by_event)
    return "<br>".join(lines + ([h] if h else []))


def _candidate_tip(rank: int, row, units: str, h2h_by_event: dict,
                   dt: pd.DataFrame) -> str:
    unit = "mi" if units == "miles" else "km"
    lines = [_title(row, f" · recommendation #{rank}")]
    lines += _runs_lines(row.event_id, dt)
    drives = []
    for name in ATHLETES:
        m = getattr(row, f"min_{name}", None)
        d = getattr(row, f"dist_{name}", None)
        if m is not None and not pd.isna(m):
            drives.append(f"{_dot(name)} {name} {_fmt_min(m)}, {d:.0f} {unit}")
    if drives:
        lines.append("<span style='opacity:.65'>Driving from home</span>")
        lines += drives
    tot = getattr(row, "total_min", None)
    if tot is not None and not pd.isna(tot):
        lines.append(f"Total driving time: <b>{_fmt_min(tot)}</b>")
    h = _h2h_line(row.event_id, h2h_by_event)
    return "<br>".join(lines + ([h] if h else []))


# --------------------------------------------------------------------------- #
# The maps
# --------------------------------------------------------------------------- #
def _base_map() -> folium.Map:
    fmap = folium.Map(location=list(LONDON_CENTER), zoom_start=LONDON_ZOOM,
                      tiles="OpenStreetMap", control_scale=True)
    fmap.get_root().header.add_child(folium.Element(f"<style>{LABEL_CSS}</style>"))
    return fmap


def build_h2h_view_map(venues: list, phone_labels: str = PHONE_LABELS[0]):
    """The head-to-head view: tab 5's pies, opening on London, with the counter.
    Built with JsMarkers, like the planner, so both views label alike."""
    fmap = _base_map()
    g = folium.FeatureGroup(name=LAYER_H2H, show=True).add_to(fmap)
    icons, points = {}, []
    for eid, lat, lon, d, svg, tip in venues:
        icons[str(eid)] = (svg, d, d, d / 2, d / 2)
        points.append([round(lat, 5), round(lon, 5), str(eid), tip])
    JsMarkers(g, icons, points, label_mode=_LABEL_MODE[phone_labels]).add_to(fmap)
    counted = {LAYER_H2H: [(v[0], v[1], v[2]) for v in venues]}
    _layer_control(fmap)
    InViewCounter(counted).add_to(fmap)
    return fmap, counted


def build_planner_map(*, parkruns: pd.DataFrame, dt: pd.DataFrame,
                      candidates: pd.DataFrame | None, h2h_by_event: dict,
                      units: str = "miles", phone_labels: str = PHONE_LABELS[0]):
    """The planner view. `parkruns` is every regular parkrun to draw (already
    country-filtered), `dt` the done_table, `candidates` the ranked matches
    (None when no filter is set).

    Three layers, and every parkrun is in exactly one: the top
    CANDIDATE_PINS recommendations, then the rest split by whether any of them
    has run it. So switching the other two off leaves only the
    recommendations. A recommendation keeps the look of its kind — lamps if
    someone has run it, a black circle if not — with its number added, and
    every match's tooltip gives its rank and driving times."""
    fmap = _base_map()
    ranked = {}
    if candidates is not None:
        ranked = {r.event_id: (i, r) for i, r in
                  enumerate(candidates.itertuples(index=False), start=1)}
    ran = set(dt.index[dt[ATHLETES].sum(axis=1) > 0]) if not dt.empty else set()

    layers = {LAYER_DONE: ({}, [], []), LAYER_NOT_DONE: ({}, [], []),
              LAYER_TOP: ({}, [], [])}
    for row in parkruns.itertuples(index=False):
        eid = row.event_id
        rank, crow = ranked.get(eid, (None, None))
        top = rank if rank is not None and rank <= CANDIDATE_PINS else None
        tip = (_candidate_tip(rank, crow, units, h2h_by_event, dt) if rank
               else _base_tip(row, dt, h2h_by_event))
        if eid in ran:
            r = dt.loc[eid]
            flags = {n: bool(r[n] > 0) for n in ATHLETES}
            key = "".join("1" if flags[n] else "0" for n in ATHLETES)
            key += f"#{top}" if top else ""
            icons, points, pts = layers[LAYER_TOP if top else LAYER_DONE]
            if key not in icons:
                icons[key] = done_marker(flags, top)
        else:
            key = f"#{top}" if top else "dot"
            icons, points, pts = layers[LAYER_TOP if top else LAYER_NOT_DONE]
            if key not in icons:
                icons[key] = open_marker(top)
        points.append([round(float(row.latitude), 5), round(float(row.longitude), 5),
                       key, tip, 1000 if top else 0])
        pts.append((eid, row.latitude, row.longitude))

    counted: dict[str, list] = {}
    # Stacking is per marker (latitude, then the z lift for a top
    # recommendation), not per layer, so this order only sets the layer box.
    for name in (LAYER_TOP, LAYER_DONE, LAYER_NOT_DONE):
        icons, points, pts = layers[name]
        if name == LAYER_TOP and not points:
            continue   # no filter set, so no recommendations to switch
        g = folium.FeatureGroup(name=name, show=True).add_to(fmap)
        JsMarkers(g, icons, points,
                  label_mode=_LABEL_MODE[phone_labels]).add_to(fmap)
        counted[name] = pts

    _layer_control(fmap)
    InViewCounter(counted).add_to(fmap)
    return fmap, counted


# The phone's jump-to-map pill. It is rendered just above the map, and
# `position: sticky; bottom` holds it at the foot of the screen for as long as
# the map is still below — through the whole filter panel — then lets it
# settle into place once the map is reached. CSS only, so no rerun; hidden
# above the 640px breakpoint, where the filters sit beside each other and the
# map is a short scroll away. Rendered only when a map is, which is what makes
# it "only if the map is visible".
JUMP_ANCHOR = "t7-map"
JUMP_CSS = """<style>
[data-testid="stElementContainer"]:has(.pr-jump) {
  position: sticky; bottom: 12px; z-index: 999; pointer-events: none;
}
/* Streamlit gives a markdown block a -1rem bottom margin (to cancel its
   paragraphs' own), which left the sticky box 16px shorter than the pill —
   so the pill hung past the foot of the screen. */
[data-testid="stElementContainer"]:has(.pr-jump) [data-testid="stMarkdownContainer"] {
  margin: 0 !important;
}
.pr-jump {
  pointer-events: auto; display: block; width: max-content; margin-left: auto;
  padding: 7px 14px;
  border-radius: 999px; background: #262a2e; color: white !important;
  text-decoration: none !important; font-weight: 600; font-size: .9rem;
  box-shadow: 0 2px 6px rgba(0,0,0,.35);
}
@media (min-width: 641px) {
  [data-testid="stElementContainer"]:has(.pr-jump) { display: none; }
}
</style>"""


def _jump_to_map() -> None:
    st.markdown(JUMP_CSS + f'<a class="pr-jump" href="#{JUMP_ANCHOR}">'
                f'🗺️ Map ↓</a>', unsafe_allow_html=True)


def _show_map(fmap, counted: dict, key: str, what: str) -> None:
    _jump_to_map()
    st.markdown(f'<div id="{JUMP_ANCHOR}"></div>', unsafe_allow_html=True)
    pts = {e: (la, lo) for layer in counted.values() for e, la, lo in layer}
    n_all = len(pts)
    n_in = sum(1 for la, lo in pts.values()
               if LONDON_BOUNDS[0][0] <= la <= LONDON_BOUNDS[1][0]
               and LONDON_BOUNDS[0][1] <= lo <= LONDON_BOUNDS[1][1])
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
def phone_labels_selector() -> str:
    return st.segmented_control(
        "Phone labels (dev)", PHONE_LABELS, default=PHONE_LABELS[0],
        key="t7_phone_labels",
        help="How a tapped marker shows its details on a phone — a compact "
             "tooltip, a pop-up that moves the map until it fits, or a panel "
             "along the bottom of the map. Only a touch screen sees the "
             "difference; a mouse always gets the tooltip.",
    ) or PHONE_LABELS[0]


def view_toggle() -> str:
    return st.segmented_control("View", VIEWS, default=VIEWS[0], key="t7_view",
                                label_visibility="collapsed") or VIEWS[0]


def _name_cell(col, name: str) -> None:
    col.markdown(f"<div style='font-weight:600'>{_dot(name)} {name}</div>",
                 unsafe_allow_html=True)


def range_filter(label: str, key: str, top: int, step: int, unit: str):
    """A two-ended slider between two number boxes, kept in step.

    Returns (low, high), or None while the range is the whole track — the full
    range means "no limit", so the furthest parkrun is never dropped for being
    exactly at the top. Callbacks keep the three widgets agreeing: the slider
    writes both boxes; a box writes the slider, swapping low and high if they
    cross."""
    rk, lk, hk = f"{key}_rng", f"{key}_lo", f"{key}_hi"
    ss = st.session_state
    if rk not in ss or ss[rk][1] > top:
        ss[rk], ss[lk], ss[hk] = (0, top), 0, top

    def from_slider():
        ss[lk], ss[hk] = ss[rk]

    def from_boxes():
        lo = min(max(int(ss[lk]), 0), top)
        hi = min(max(int(ss[hk]), 0), top)
        lo, hi = min(lo, hi), max(lo, hi)
        ss[lk], ss[hk], ss[rk] = lo, hi, (lo, hi)

    c1, c2, c3 = st.columns([1, 3, 1], vertical_alignment="center")
    c1.number_input(f"{label}: from ({unit})", 0, top, step=step, key=lk,
                    on_change=from_boxes, label_visibility="collapsed")
    c2.slider(label, 0, top, step=step, key=rk, on_change=from_slider,
              label_visibility="collapsed")
    c3.number_input(f"{label}: to ({unit})", 0, top, step=step, key=hk,
                    on_change=from_boxes, label_visibility="collapsed")
    lo, hi = ss[rk]
    return None if (lo, hi) == (0, top) else (lo, hi)


# Session keys the planner's filters live under. Clearing deletes them, and
# each widget comes back at its default on the rerun — for a range, the full
# track (range_filter re-seeds its three keys when they are missing). Units
# and the view toggle are settings, not filters, and are left alone.
PLANNER_FILTER_KEYS = ("t7_countries", "t7_done_", "t7_h2h_", "t7_min_",
                       "t7_dist_", "t7_rank_by", "t7_exclude")


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
            f"{title}: {label}", choices, default=choices[0],
            key=f"{key_prefix}_{label}", label_visibility="collapsed") or choices[0]
    return out


def _results_table(c: pd.DataFrame, units: str) -> pd.DataFrame:
    unit = "mi" if units == "miles" else "km"
    out = pd.DataFrame({"#": range(1, len(c) + 1), "parkrun": c["short_name"]})
    for name in ATHLETES:
        if f"min_{name}" in c:
            out[f"{name} time"] = c[f"min_{name}"].map(
                lambda v: "—" if pd.isna(v) else _fmt_min(v))
            out[f"{name} {unit}"] = c[f"dist_{name}"].round(0)
    if "total_min" in c:
        out["Total time"] = c["total_min"].map(_fmt_min)
    out["Run by"] = c.apply(
        lambda r: ", ".join(f"{n} ({int(r[n])})" for n in ATHLETES if r[n] > 0)
        or "nobody", axis=1)
    return out


def _legend_html() -> str:
    order = " · ".join(f"{_dot(n)} {n}" for n in ATHLETES)
    lamps = squares_svg({ATHLETES[0]: True, ATHLETES[2]: True}, DONE_STYLE)
    ranked = done_marker({ATHLETES[0]: True, ATHLETES[2]: True}, 3)[0]
    return (
        "<div style='font-size:.85rem;opacity:.85;margin:.25rem 0 .5rem;"
        "line-height:1.9'>"
        f"{lamps}&nbsp; run by at least one of them — a lamp per runner ({order}, "
        f"left to right), lit if they have run it.<br>"
        f"{open_marker()[0]}&nbsp; run by none of them.<br>"
        f"{ranked}&nbsp;{open_marker(3)[0]}&nbsp; the top {CANDIDATE_PINS} "
        f"recommendations for the filters below, numbered. Hover any parkrun "
        f"that matches for its rank and driving times.</div>")


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def render_h2h_view(version, mh: pd.DataFrame | None) -> None:
    """`mh` is the filtered head-to-head slice, or None when no classification
    is picked — tab 5's rule."""
    if mh is None:
        st.info("Pick a head-to-head classification above to show the map.")
        return
    events = load_events_geo(version)
    venues = h2h_venues(mh, events[["event_id", "short_name", "latitude",
                                    "longitude"]])
    if not venues:
        st.info("No head-to-heads match those filters.")
        return
    n_occ = mh.drop_duplicates(["event_id", "run_date"]).shape[0]
    st.caption(f"**{len(venues)}** venue{'s' if len(venues) != 1 else ''} · "
               f"**{n_occ}** head-to-head{'s' if n_occ != 1 else ''}. Each "
               f"circle is sized by how many head-to-heads happened there and "
               f"split by who won them.")
    fmap, counted = build_h2h_view_map(venues, phone_labels_selector())
    _show_map(fmap, counted, "t7_map_h2h", "venues")


def render_planner(version, h2h: pd.DataFrame) -> None:
    events = load_events_geo(version)
    done = load_done(version)
    dt = done_table(done)
    hc = h2h_counts(h2h)
    h2h_by_event: dict = {}
    for r in hc.sort_values(["event_id", "n"], ascending=[True, False]).itertuples():
        h2h_by_event.setdefault(r.event_id, []).append((r.classification, int(r.n)))

    parkruns = regular_parkruns(events, done)
    counts = parkruns["country_name"].value_counts()

    tv = travel_version()
    travel = load_travel(tv) if tv is not None else None
    has_travel = travel is not None and not travel.empty
    st.caption(
        "Every regular parkrun, with a square per runner for who has run it. "
        "The filters pick out possible next parkruns on mainland Great Britain"
        + (", with driving times from each home — free-flow estimates, no "
           "traffic." if has_travel else
           ". Driving times and distances are only in the local version of "
           "the app.")
    )

    c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
    countries = c1.multiselect(
        "Countries", list(counts.index),
        format_func=lambda c: f"{c} ({counts[c]})",
        key="t7_countries", placeholder="All countries")
    c2.button("Clear all filters", key="t7_clear", on_click=clear_planner_filters,
              width="stretch",
              help="Every filter back to its default: all countries, anyone, "
                   "any head-to-head, the full driving ranges, nothing left "
                   "out. Units stay as they are.")
    st.markdown(_legend_html(), unsafe_allow_html=True)

    units = "miles"
    if has_travel:
        units = st.segmented_control("Units", ["miles", "km"], default="miles",
                                     key="t7_units") or "miles"

    c1, c2 = st.columns(2)
    with c1:
        done_filter = _choice_rows("Who has run it", ATHLETES, DONE_CHOICES,
                                   "t7_done")
    with c2:
        classes = sorted(hc["classification"].unique())
        h2h_filter = _choice_rows("Head-to-heads there", classes, H2H_CHOICES,
                                  "t7_h2h", dot=False)

    minutes_range, distance_range, rank_by = {}, {}, None
    if has_travel:
        per = 1609.344 if units == "miles" else 1000.0
        unit = "mi" if units == "miles" else "km"
        for title, metric in (("Driving time (min)", "min"),
                              (f"Driving distance ({unit})", "dist")):
            st.markdown(f"**{title}**")
            for name in ATHLETES:
                mine = travel[travel["athlete_name"] == name]
                c0, c1 = st.columns([1, 5], vertical_alignment="center")
                _name_cell(c0, name)
                if mine.empty:
                    c1.caption("No home set — no driving times.")
                    continue
                with c1:
                    if metric == "min":
                        top = int(math.ceil(mine["duration_s"].max() / 60 / 10) * 10)
                        minutes_range[name] = range_filter(
                            f"{name} driving time", f"t7_min_{name}", top, 5, "min")
                    else:
                        top = int(math.ceil(mine["distance_m"].max() / per / 10) * 10)
                        # Keyed by unit: a mile range is not a km range.
                        distance_range[name] = range_filter(
                            f"{name} driving distance", f"t7_dist_{name}_{units}",
                            top, 1, unit)
        routed = [n for n in ATHLETES if not travel[travel["athlete_name"] == n].empty]
        rank_by = st.multiselect(
            "Rank by total driving time of", routed, default=routed,
            key="t7_rank_by",
            help="Sum of these runners' driving times — one, two or all three.")

    base = plan_candidates(events, done, None, done_filter={})
    # Session state only: a reload clears it. Options are every candidate, not
    # just the current matches, so changing a filter never orphans an exclusion.
    exclude = st.multiselect(
        "Leave out these parkruns", base["event_id"].tolist(),
        format_func=dict(zip(base["event_id"], base["short_name"])).get,
        key="t7_exclude", placeholder="None left out")

    cands = plan_candidates(
        events, done, travel, done_filter=done_filter, h2h=hc,
        h2h_filter=h2h_filter, minutes_range=minutes_range,
        distance_range=distance_range, units=units, rank_by=rank_by,
        exclude=exclude)
    cands = filter_countries(cands, countries)
    filtered = (any(v != "Any" for v in done_filter.values())
                or any(v != "Any" for v in h2h_filter.values())
                or any(v is not None for v in minutes_range.values())
                or any(v is not None for v in distance_range.values())
                or bool(exclude) or bool(rank_by))
    # Ranking alone counts: with every filter at "any" the top recommendations
    # are simply the nearest parkruns by total driving time. Only the top
    # CANDIDATE_PINS are marked, so an unfiltered list no longer covers the map.

    fmap, counted = build_planner_map(
        parkruns=filter_countries(parkruns, countries), dt=dt,
        candidates=cands if filtered else None,
        h2h_by_event=h2h_by_event, units=units,
        phone_labels=phone_labels_selector())
    _show_map(fmap, counted, "t7_map_plan", "parkruns")

    if not filtered:
        st.caption("Set a filter above to pick out possible next parkruns.")
        return
    st.markdown(f"**{len(cands)}** parkrun{'s' if len(cands) != 1 else ''} match"
                + ("" if len(cands) != 1 else "es")
                + (f" — the first {CANDIDATE_PINS} are numbered on the map."
                   if len(cands) > CANDIDATE_PINS else "."))
    if not cands.empty:
        st.dataframe(_results_table(cands, units), hide_index=True,
                     width="stretch", height=min(420, 38 + 35 * len(cands)))

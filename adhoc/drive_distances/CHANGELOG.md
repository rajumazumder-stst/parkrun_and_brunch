# Changelog

## 2026-10-02
- The app now routes the crossings itself: `parkrun_travel` routes every
  live 5k parkrun in a Europe box (`parkrun_core.routable`), and tab 5's
  planner shows them flagged ⛴ with an "Include sea crossings" toggle.
  `build_page.py` reads `travel_times` alone and flags with
  `parkrun_core.crosses_water`; `scripts/route_crossings.py` and `.cache/`
  were deleted. Built from the dev DB: 1,309 routed, 472 across water.

## 2026-10-01 (legend)
- Drive-time colours: 5 bands (under 30m to 4h+) became 11 (under 20m, 20–40m,
  40m–1h, 1–1½h, 1½–2h, 2–3h, 3–4h, 4–6h, 6–10h, 10–15h, 15h+). The edges
  were chosen from the spread of times, so no band is near-empty; the old 4h+
  band held over half of every athlete's routes once crossings were added.
  The colours are an inferno ramp whose lightness falls at every step (OKLab
  L 0.95 to 0.22), replacing a green-to-red traffic light. Blue was ruled out
  against the sea. Legend dots get a ring so the darkest show on a dark page.

## 2026-10-01 (later)
- Map tiles: the page used OSM's own tile servers, which can 403 ("Access
  blocked") a `file://` page because it sends no `Referer`. Esri World Street
  Map is now the default, with OSM in the layer switcher and `?tiles=osm`.
  `scripts/serve.sh` serves over localhost so OSM gets a `Referer`. CARTO was
  tried and rejected: it watermarks tiles "API KEY REQUIRED".
- Added parkruns reached by Channel Tunnel or ferry: `scripts/route_crossings.py`
  routes 473 of them (NI, islands, Gibraltar, Ireland, ten European countries)
  into `.cache/`. The page flags them ⛴ and gains a "Sea crossings" filter.
  1,311 parkruns are routed in all; Inis Meáin has no car ferry, so no route.
- Found that `is_mainland` returns True for eastern Ireland unless the UK
  country filter is applied first. The crossing script applies it.

## 2026-10-01
- Created. `scripts/build_page.py` adds `travel_times` to
  `scripts/page_template.html` and writes `output/drive_distances.html`: a table
  of all live parkruns with each athlete's drive time and distance (839 routed,
  mainland GB), plus a map coloured by drive time and a miles/km toggle.

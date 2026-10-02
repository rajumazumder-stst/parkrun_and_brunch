# Drive distances

**Question:** how far is each athlete's drive to every parkrun?

**Answer:** a standalone HTML page, `output/drive_distances.html` (gitignored).
It has one row per live 5k parkrun (2,402 on 1 Oct 2026), with drive time and
distance for each athlete plus the total for all three, including parkruns
reached by Channel Tunnel or ferry. You can sort, search
and filter by country, switch between miles and km, and see a Leaflet map
coloured by drive time for one athlete or for the total.

## Decisions

- **1,311 of 2,402 parkruns have a drive time** (1 Oct 2026). 839 are on
  mainland GB; the other **473 can only be reached by Channel Tunnel or
  ferry**: Northern Ireland, the islands, Gibraltar, Ireland and ten European
  countries. The remaining ~1,090 (Australia, the US, the Falklands and so
  on) have no road route and show blanks.
- **All of them now come from `travel_times`** (2 Oct 2026). The app adopted
  this topic's approach — route everything in a Europe box, flag what crosses
  water — so `parkrun_travel` routes the crossings itself and
  `scripts/route_crossings.py` and its `.cache/` were retired. The flag is
  `parkrun_core.crosses_water`, which also carries the Dublin fix below.
- **Crossing times are flagged ⛴ and left as ORS returns them.** ORS routes
  across water happily (it is the *app* that excludes it), but it times a
  crossing at sailing speed only: no check-in (30+ min for the tunnel, 45–90 for
  a ferry), no wait for a departure, no overnight sailing. So those times are
  too short. ORS also files the Eurotunnel shuttle as a ferry, so the page
  cannot say which way it crossed. Every route to a parkrun off mainland GB
  crosses water by construction (it is reached by road from a home on the
  mainland), so no per-route check is needed for the flag.
- **`is_mainland` is a UK-only test.** Eastern Ireland (Dublin, −6.3°) is
  inside `GB_EXTENT` and outside every `NON_MAINLAND` box, so
  `is_mainland(Dublin)` is True. `parkrun_core.routable` and `crosses_water`
  test the country first.
- **Map tiles: Esri by default, OSM over http.** OSM's own tile servers require
  a `Referer`, and a page opened as a `file://` (or in an editor preview) sends
  none, so OSM may answer with a grey "Access blocked" 403 tile. Esri's World
  Street Map needs no `Referer` and no key. CARTO was tried first and now
  watermarks every tile "API KEY REQUIRED". To use OSM, run `scripts/serve.sh`
  and open `?tiles=osm` (the browser then sends `Referer: http://localhost:8510/`).
  Both are in the map's layer switcher. All four combinations were checked
  over the DevTools protocol on 1 Oct 2026: every tile came back 200.
- **Output stays local.** The page holds drive data from each athlete's
  neighbourhood, so it goes in `output/` and is never committed or published.
  `serve.sh` binds to localhost only, for the same reason.

## Rerun

```bash
source ~/Documents/Python\ scripts/env/bin/activate
python3 adhoc/drive_distances/scripts/build_page.py        # PARKRUN_DB=... to override
open adhoc/drive_distances/output/drive_distances.html     # Esri tiles
adhoc/drive_distances/scripts/serve.sh                     # or over http, for ?tiles=osm
```

There are no extra dependencies: the script uses the app venv (duckdb, plus
`parkrun_ui` for `ATHLETE_COLORS`). It reads whichever database
`resolve_db` picks — until the change reaches `main` and a refresh routes the
crossings, only the dev DB has them (`PARKRUN_DB=data/parkrun_dev.duckdb`). The page loads Leaflet from cdnjs
with SRI hashes and needs a network connection for map tiles.

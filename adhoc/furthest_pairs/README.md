# Which two parkruns are furthest apart?

**Question as asked:** determine the top 10 pairs of parkruns that are furthest
away from each other.

**Answer (7 Aug 2026):** **Gibraltar Botanical Gardens ↔ Warkworth Showgrounds
(New Zealand) — 19,986.5 km**, which is 99.86% of the 20,015 km maximum any two
points on Earth can be apart. All ten of the furthest pairs have Gibraltar at
one end.

| # | km | % of antipodal | End A | End B |
|---|---|---|---|---|
| 1 | 19,986.5 | 99.86 | Gibraltar Botanical Gardens (United Kingdom) | Warkworth Showgrounds (New Zealand) |
| 2 | 19,962.7 | 99.74 | Millwater (New Zealand) | Gibraltar Botanical Gardens (United Kingdom) |
| 3 | 19,962.6 | 99.74 | Whangarei (New Zealand) | Gibraltar Botanical Gardens (United Kingdom) |
| 4 | 19,961.7 | 99.73 | Gibraltar Botanical Gardens (United Kingdom) | Raumanga Stream (New Zealand) |
| 5 | 19,949.5 | 99.67 | Sherwood Reserve (New Zealand) | Gibraltar Botanical Gardens (United Kingdom) |
| 6 | 19,948.5 | 99.67 | Northern Pathway (New Zealand) | Gibraltar Botanical Gardens (United Kingdom) |
| 7 | 19,941.9 | 99.63 | Hobsonville Point (New Zealand) | Gibraltar Botanical Gardens (United Kingdom) |
| 8 | 19,940.0 | 99.62 | Tuff Crater Reserve (New Zealand) | Gibraltar Botanical Gardens (United Kingdom) |
| 9 | 19,933.3 | 99.59 | Western Springs (New Zealand) | Gibraltar Botanical Gardens (United Kingdom) |
| 10 | 19,932.5 | 99.59 | Ōrākei Bay (New Zealand) | Gibraltar Botanical Gardens (United Kingdom) |

Answer of record: [`results/furthest_pairs_top10.csv`](results/furthest_pairs_top10.csv)
(full precision, event IDs, locations, coordinates).

## Why every pair shares Gibraltar

Gibraltar's antipode — the point exactly opposite it on the globe — sits in the
sea just north-east of Auckland, and northern New Zealand is dense with
parkruns. No other parkrun has such a well-populated antipode, so once
Gibraltar is in the running it takes every top slot, and the ranking is really
a list of "which northern New Zealand event is nearest Gibraltar's antipode".
This is the raw top 10, as asked: repetition is the finding, not a flaw in it.

Two consequences worth knowing:

- The spread across the whole top 10 is 54 km — 0.3%. The ordering is
  effectively a tie, and a single new NZ event could reshuffle it.
- Because the pairs are near-antipodal, the shortest path between the ends is
  nearly free to go either way around the globe. Eight of the ten run south
  over Antarctica; two — #3 (Whangarei) and #4 (Raumanga Stream) — run
  north-east over Asia instead, topping out around 64°N. Each is a genuine
  shortest path, not a numerical accident: for a near-antipodal pair a few
  kilometres of position decides which way round is shorter.

## Decisions behind the answer

| Decision | Choice | Why |
|---|---|---|
| Universe | `seriesid = 1` (Saturday 5k), `live = TRUE` — 2,374 events | The question is about parkruns you could actually run today. Excludes 569 junior events and 6 defunct main-series events, including the manual Victoria Dock row. |
| Distance | Great-circle (haversine on a sphere, mean radius 6371.0088 km) | Asked for. A WGS84 geodesic would differ by well under 0.5%; at this scale it would not change the ranking, and it would add a dependency. |
| Ranking | Raw top 10 pairs, duplicates and all | Asked for. The alternative — forcing each event to appear once — would have hidden the Gibraltar finding above. |
| Search | Exhaustive: all 2,816,751 pairs | 2,374 events is small enough that brute force is seconds and exact; no spatial index, no approximation. |
| Country names | `parkrun.country_lookup` via `events.country_code` | Gibraltar's events sit under the UK country code (`www.parkrun.org.uk`), so the table reads "United Kingdom" for a territory that is not in the UK. That is parkrun's own grouping, left as-is. |
| Data source | `data/parkrun_snapshot.duckdb` (or `PARKRUN_DB`) | Same read-only resolution order as the app — never touches the dev DB. |

## The map

[`scripts/build_map.py`](scripts/build_map.py) writes
`output/furthest_pairs_map_top10.html` (gitignored — regenerate it, don't commit
it): every live parkrun as context, the ten pairs as true great-circle arcs, and
a ranked list that highlights one pair at a time.

Two map-specific notes:

- Arcs are drawn by interpolating along the great circle (slerp), not as
  straight Mercator lines — a straight line between these points is not the
  path and is not the distance.
- Vertices beyond ±85° latitude are dropped, because Web Mercator cannot draw
  the poles. The southern arcs therefore run off the bottom edge of the map
  rather than spiking to infinity.

## Rerunning

```bash
cd adhoc/furthest_pairs/scripts
./run_all.sh          # top 10 (the published result)
./run_all.sh 25       # any N
./serve.sh            # open the map at http://localhost:8000/…
```

`run_all.sh` activates the repo's venv if it is there. Extra dependency:
`numpy` (see [`requirements.txt`](requirements.txt)); `duckdb`, `pandas`,
`folium` and `branca` are already pinned at the repo root.

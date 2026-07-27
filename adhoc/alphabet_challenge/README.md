# The parkrun alphabet challenge — where should you live?

**Question.** The alphabet challenge means completing a parkrun starting with
each letter of the alphabet, excluding X (no parkrun begins with it). Using the
coordinates of live, non-junior parkruns, find the location with the shortest
total distance to complete it.

**Answer.** `-27.6000, 153.0200` — Pallara, south Brisbane, Queensland.
**1,112 km** by road; 839 km straight-line. London is a close second at 1,175 km.

Answered 27 July 2026 against the tracked snapshot (2,368 live main-series
events). See [CHANGELOG.md](CHANGELOG.md) for how the answer evolved.

---

## The decisions behind the answer

These were judgement calls, each of which changes the result. They are recorded
here because no amount of reading the code recovers them.

| Decision | Choice | Why it matters |
|---|---|---|
| **Scope** | Worldwide | Restricting to the UK gives a different winner (London) and makes Z impossible domestically. |
| **Objective** | Sum of 25 one-way distances from a single base | Models 25 separate out-and-back trips from home. A travelling-salesman tour or a minimise-the-worst-trip objective gives entirely different answers. |
| **Letter rule** | Literal first character of `short_name` | No accent folding and no stripping of a leading "The". Drops 10 events whose initials are Ō, Ś, Ż, Ł or Ö, so **2,358 of 2,368** events feed the letter math. |
| **Base point** | Any coordinate on land | Not restricted to an existing parkrun venue. The unconstrained optimum happened to land on land anyway. |
| **Distance metric** | Real road distance (OSRM) | Started as great-circle; switching to road did **not** change the ranking, but it did widen the margin. |
| **Ferries** | Included and counted as ordinary distance | They were already in OSRM's car profile from the start. Ranking is unchanged by making them visible; they are now drawn dashed and broken out per leg. |
| **Separation** | Minimum 20 km between reported points | Without it the "top N" degenerates into one metro area sampled N times. |

**Known limitation.** The 20 coordinates are *air-optimal, road-evaluated* —
each is the local great-circle optimum of its cell, then measured by road.
Finding the true road optimum inside every cell would need orders of magnitude
more routing. Expect each point to be within a km or two of its road-optimal
position; the ordering is not affected.

---

## Result — top 20 by road distance (all pairs ≥ 20 km apart)

| # | Coordinates | Road km | Air km | Detour | Nearest parkrun | Longest leg | Ferry km |
|---:|---|---:|---:|---:|---|---|---:|
| 1 | `-27.6000, 153.0200` | 1,112 | 839 | ×1.33 | Pallara | Q — Queen Elizabeth, Casino, 225 km | 0 |
| 2 | `51.4650, -0.1850` | 1,175 | 865 | ×1.36 | Battersea | Z — Zuiderpark, 487 km | 59 |
| 3 | `-27.3750, 153.0100` | 1,258 | 954 | ×1.32 | Chermside | Q — Queen Elizabeth, Casino, 242 km | 0 |
| 4 | `51.4500, 0.1250` | 1,354 | 901 | ×1.50 | Bexley | Z — Zuiderpark, 463 km | 59 |
| 5 | `-27.8750, 153.0250` | 1,513 | 1,100 | ×1.38 | Yarrabilba | D — Dalby, 228 km | 0 |
| 6 | `51.3800, -0.8750` | 1,566 | 1,127 | ×1.39 | California Country | Z — Zuiderpark, 538 km | 59 |
| 7 | `51.5300, -0.6250` | 1,569 | 1,081 | ×1.45 | Upton Court | Z — Zuiderpark, 534 km | 59 |
| 8 | `51.8750, -0.1250` | 1,646 | 1,140 | ×1.44 | Stevenage | Z — Zuiderpark, 527 km | 60 |
| 9 | `-27.1250, 152.9600` | 1,655 | 1,266 | ×1.31 | North Harbour | Q — Queen Elizabeth, Casino, 274 km | 0 |
| 10 | `52.1300, -0.5450` | 1,667 | 1,118 | ×1.49 | Great Denham | Z — Zuiderpark, 584 km | 59 |
| 11 | `52.3750, -0.8550` | 1,677 | 1,134 | ×1.48 | Brixworth Country | Z — Zuiderpark, 619 km | 59 |
| 12 | `51.1300, -0.1100` | 1,685 | 1,154 | ×1.46 | Tilgate | Z — Zuiderpark, 490 km | 59 |
| 13 | `-27.5950, 152.6250` | 1,696 | 1,237 | ×1.37 | Ipswich QLD | Q — Queen Elizabeth, Casino, 239 km | 0 |
| 14 | `52.1300, -0.8750` | 1,748 | 1,180 | ×1.48 | Salcey Forest | Z — Zuiderpark, 595 km | 59 |
| 15 | `52.9150, -1.2000` | 1,762 | 1,162 | ×1.52 | Beeston | Z — Zuiderpark, 696 km | 59 |
| 16 | `-27.6250, 153.4100` | 1,774 | 1,245 | ×1.43 | Redland Bay | D — Dalby, 250 km | **238** |
| 17 | `52.6300, -1.1750` | 1,790 | 1,196 | ×1.50 | Braunstone | Z — Zuiderpark, 653 km | 59 |
| 18 | `51.1300, 0.3750` | 1,809 | 1,275 | ×1.42 | Royal Tunbridge Wells | Z — Zuiderpark, 442 km | 59 |
| 19 | `52.3800, -1.6250` | 1,827 | 1,320 | ×1.38 | Coventry | Z — Zuiderpark, 654 km | 59 |
| 20 | `50.9450, -1.1700` | 1,843 | 1,375 | ×1.34 | Meon Valley Trail, Wickham | Z — Zuiderpark, 577 km | 59 |

Only **two regions** are competitive worldwide: south-east Queensland (6 entries)
and the UK (14). The next best region anywhere is Johannesburg at ~11,000 km
straight-line, an order of magnitude worse.

### What decides it

- **Brisbane wins on density, not on short legs.** 21 of its 25 legs are under
  60 km; the damage is Dalby (200 km) and Queen Elizabeth, Casino (225 km),
  together 38% of its total.
- **London's handicap is two outliers.** Y is Yarborough Leisure Centre in
  Lincoln (242 km) and Z is Zuiderpark in Den Haag (487 km, via the Dover–Calais
  ferry). There is **no live Z parkrun in the UK**, so a UK-based alphabet
  requires leaving the country. Strip those two legs and London is the densest
  cluster on Earth.
- **Road distance did not change the ranking**, though it was expected to.
  Brisbane's detour factor is *lower* (×1.29–1.34 vs the UK's ×1.36–1.52), so
  its win widens from 26 km on straight-line to 63 km by road.
- **#16 sits on Russell Island** in Moreton Bay. All 25 of its legs ferry to
  Redland Bay first — 238 km at sea. It is the only base where the ferry is
  structural rather than a single long leg.

### Ferries

OSRM's car profile routes over ferries and counts their distance, so they were
always included. Across all 500 legs: 16,319 driving steps and 40 ferry steps,
no Channel Tunnel shuttle (the router prefers the boat). 15 of the 20 bases use
a ferry — 14 of them the same 59.2 km Dover–Calais crossing for their Z leg.

---

## Why the top 20 is provably complete

Great-circle distance is a strict lower bound on road distance for the same
pair of points. So candidates are routed **in air-distance order**, and the
search stops as soon as the 20th-best road total falls below the next
candidate's air total — at that moment no unrouted candidate can possibly
qualify.

For this run the search closed at candidate 87 of 281: 20th-place road
1,842.9 km ≤ next candidate's air 1,848.4 km. This is a genuine completeness
guarantee at region level, not a heuristic stopping rule.

---

## Running it

```bash
cd adhoc/alphabet_challenge/scripts
./run_all.sh              # top 20, 20 km separation — the published result
./run_all.sh 10 10        # top 10, 10 km separation
```

Needs the topic's extra dependencies on top of the repo's:

```bash
pip install -r adhoc/alphabet_challenge/requirements.txt
```

Reads `data/parkrun_snapshot.duckdb` by default; override with `PARKRUN_DB`.

**Caching.** Every OSRM response is cached under `.cache/`, so the first full
run makes ~630 requests to the public demo server and every rerun makes none.
Delete `.cache/` to refetch. Please keep the courtesy pause in `osrm.py` if you
do — it is a free shared service.

### Files

| Path | Purpose |
|---|---|
| `scripts/common.py` | Paths, DB resolution, event loading, spherical geometry |
| `scripts/osrm.py` | Cached OSRM client (table + route services) |
| `scripts/solve_air.py` | Straight-line optimum, unconstrained and land-constrained |
| `scripts/solve_road.py` | Top N by road, with the completeness bound |
| `scripts/fetch_routes.py` | Full-detail geometry per leg, driving/ferry split |
| `scripts/build_map.py` | The interactive folium map |
| `scripts/run_all.sh` | All four in order |
| `results/road_top20.json` | The answer of record (tracked) |
| `results/air_optimum.json` | Straight-line optimum + its 25 legs (tracked) |
| `output/` | Generated map and geometry dump (~33 MB, gitignored) |
| `.cache/` | Cached OSRM responses (~33 MB, gitignored) |

---

## The map

`output/alphabet_map_top20.html` (17 MB). Layers: every live parkrun as
recessive context, the 20 numbered base points coloured by region, and a
dropdown that draws any one base point's 25 driving legs — ferry segments
dashed — or all 500 at once. Basemap toggles light/dark and the panels follow it.

Colours are categorical slots 1 and 2 of the design-system palette, validated
all-pairs in both light and dark modes. The context layer is deliberately below
the chroma floor so it reads as background rather than a third series.

### Caveats on the numbers

- Straight-line legs are great-circle; road legs are OSRM's car profile with no
  traffic, tolls or vehicle restrictions modelled.
- The Brisbane routes assume Queensland's toll motorways are usable.
- Distance, not time. A 59 km ferry takes 1.15 h; ranking by hours instead would
  penalise the UK cluster considerably and is a different question.
- The result is a snapshot. New events open and close constantly, and a single
  new Q, Y or Z event near a dense cluster could move the winner.

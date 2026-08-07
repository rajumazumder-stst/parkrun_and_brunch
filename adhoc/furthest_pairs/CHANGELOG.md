# Changelog — furthest pairs

## 2026-08-07 — first answer

- Topic created: which two parkruns are furthest apart, top 10 pairs.
- Scope fixed with the requester: `seriesid = 1` and `live = TRUE` only,
  haversine distance, **raw** top 10 (no dedup of repeated events), CSV +
  README + map as deliverables.
- `find_pairs.py`: exhaustive scan of all 2,816,751 pairs over 2,374 live
  main-series events; writes `results/furthest_pairs_top10.csv`.
- `build_map.py`: Folium world map, great-circle arcs (slerp-interpolated,
  split at the antimeridian, clipped at ±85° for Web Mercator), ranked list
  panel with single-pair highlight, light/dark basemap.
- Result: Gibraltar Botanical Gardens ↔ Warkworth Showgrounds, 19,986.5 km
  (99.86% of antipodal). All ten pairs share the Gibraltar end — its antipode
  lies just off Auckland, in the densest cluster of NZ parkruns.

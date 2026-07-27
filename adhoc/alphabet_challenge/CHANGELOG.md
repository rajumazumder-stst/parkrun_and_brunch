# Changelog — alphabet challenge

Newest first. Dates are absolute.

## 2026-07-27 — moved into the repo

Investigation lifted out of the session scratchpad into `adhoc/alphabet_challenge/`.
Scripts were consolidated while porting: several intermediate versions
(`alphabet.py`, `alphabet_road.py`, `alphabet_road2.py`, plus separate top-10
route/map builders) collapsed into one parameterised pipeline of four steps
sharing `common.py` and `osrm.py`. Paths now resolve from `__file__` rather than
the working directory, and the OSRM cache moved to `.cache/` so reruns are free.

Behaviour is unchanged — same inputs, same published numbers.

## 2026-07-27 — ferries made visible, extended to top 20

- Established that **ferries were already routed and counted** all along: the
  London Z leg's 487 km had contained a 59.2 km Dover–Calais crossing since the
  first road run. No re-ranking was needed. Ferry segments are now drawn dashed
  and broken out per leg (`drive_km` / `ferry_km`) via `steps=true`.
- Extended to the **top 20 at ≥ 20 km separation** (up from top 10 at ≥ 10 km,
  where the list degenerated into two metro areas sampled ten times). Result:
  6 Brisbane, 14 UK.
- Added the **dropdown route selector** to the map: any one base point's 25 legs,
  all 500 at once, or none. 500 routes at full detail = 17 MB.
- Found that **#16 is on Russell Island** — all 25 of its legs ferry to the
  mainland, 238 km at sea.

**Bug fixed: map JavaScript never ran.** Folium renders both `html` and `script`
children *before* it constructs the map object, so the panel script threw a
`ReferenceError` on the map variable and silently killed every handler in the
block — the dropdown did nothing. Now wrapped in a `window.addEventListener('load', …)`
handler with `vizShow` exposed on `window` for the popup buttons.

This bug was latent in the earlier top-10 map too: its jump buttons worked
(inline `onclick`, evaluated lazily) but the basemap-following theme logic never
executed, so panels silently followed the OS instead.

Also fixed while QA-ing screenshots: dark tiles were defaulting over light, the
world map repeated into three copies (`no_wrap`), the legend covered the scale
bar, and the legend's line-style swatches used a series colour when they encode
mode — now neutral `currentColor`.

## 2026-07-27 — switched to road distance

Replaced great-circle with real driving distance via the public OSRM demo
server, and produced a top 10 at ≥ 10 km separation.

**The ranking did not change**, contrary to the expectation stated when the
switch was proposed: Brisbane's detour factor (×1.29–1.34) is *lower* than
London's (×1.36–1.50) because its long legs are highway miles, so its lead
widened rather than flipping.

Introduced the completeness bound — air distance lower-bounds road distance, so
routing candidates in air order allows a provable stopping point rather than a
heuristic cutoff.

## 2026-07-27 — first answer, straight-line

Great-circle optimum over 2,368 live `seriesid = 1` events, solved as a
non-convex sum-of-min-distances by global sweep → basin refinement → Nelder–Mead.

- Unconstrained: `-27.6023, 153.0189`, 838.9 km.
- Land-constrained: same point — the free optimum already falls on land, so the
  constraint was non-binding.
- Runner-up London at 865.2 km; every other region ≥ 11,000 km.

Confirmed no live UK parkrun starts with Z, so a UK alphabet cannot be completed
without leaving the country.

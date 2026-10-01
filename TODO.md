# To-do

Ideas and requests not yet built. Move an item out when it ships (to the
**Current status** list in `CLAUDE.md`); delete it if it is rejected, with a
line on why.

---

## Visualisation ideas

Unscoped. Carried over from the *Future ideas* line that used to close
`CLAUDE.md` § Visualisations. Four have since shipped and were removed on
20 Sep 2026: **attendance timeline** (the participation calendars, tab 1 and
tab 3), **fastest times** (the personal-bests block, tab 2), **form (target)
over refreshes** (tab 4) and **age-grade progression** (dropped, not wanted).

- PB progression
- Event frequency

---

## Where they meet — promote tab 7 over tab 5

Tab 7 (`where_next.py`) runs beside tab 5 until these are settled. Built
30 Sep 2026; see `docs/DEV.md` § Tab 7 and the travel data.

- ~~Privacy — may the live app show driving times?~~ — **done** 1 Oct 2026:
  yes. The origins are neighbourhood centroids, so the times can give away a
  neighbourhood and nothing finer.
- ~~Pick the markers~~ — **done** 30 Sep 2026: "Row, lit" lamps, with the
  recommendation number inside the housing. Ring / Dots / Layers, the other
  square variants, and the badge-above / tag-beside placements are in git
  history.
- ~~Pick the phone label style~~ — **done** 1 Oct 2026: a bottom sheet like
  the calendars'. The tooltip and pop-up variants were removed.
- ~~Pick where the planner's filters sit~~ — **done** 1 Oct 2026: a
  "⚙️ Filters" button. The two sidebar placements were removed.
- ~~Pick how a runner left out of the ranking shows~~ — **done** 1 Oct 2026:
  grey and italic, on the map and in the table. The other two styles were
  removed.
- ~~Pick a router~~ — **done** 30 Sep 2026: ORS. The same distances as OSRM,
  but slower, more believable times for the short London trips that matter
  (median +3.4 min under an hour). The top-25 recommendations overlap 23/25.
  OSRM's adapter and the Routing selector are removed.
- ~~Wire `travel` into the refresh~~ — **done** 1 Oct 2026:
  `apply_travel_times` runs first in `_finalize`, and `travel_times` ships.
- **Replace tab 5 with tab 7.** Rename it back to "Where they meet" and drop
  tab 5's block from `parkrun_app.py`. `build_h2h_map` goes too if nothing
  else uses it.
- Tab 5's map renders 500px wide (`st_folium`'s default width). Tab 7 passes
  `use_container_width=True`. It is left as it is, because tab 5 is being
  retired.
- The PB scope label "Last 12 months" wraps on a mid-width desktop, which
  drops its time below "All time"'s. `stat_label(..., lines=2)` fixes that
  (tab 6 uses it). It was not applied to the PB block, to keep that change
  free of visual changes.

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

- **Privacy — decide whether the live app may show driving times.** Half
  done: since 1 Oct 2026 the homes are **neighbourhood centroids**, not exact
  addresses, so the most the drive times can give away is the neighbourhood.
  Still open: whether to ship `travel_times` at all. Doing so means adding it
  to `SNAPSHOT_TABLES` and wiring `travel` into the refresh. Showing only
  ranks or totals would not hide more: triangulation needs only the relative
  ordering of the times. Until then nothing reaches git or the hosted app.
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
- **Wire `travel` into the refresh.** Only once the privacy call is made: add
  `update_travel_times` after `reconcile_events` (it is incremental and
  non-fatal), and add `travel_times` to `SNAPSHOT_TABLES` if it is to ship.
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

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

## App: buggy-estimator tab

*Added 19 Sep 2026.*

A new tab that shows the buggy estimator's call per run — buggy or regular —
with its confidence as a percentage.

Things to settle before building:

- **What it lists.** Only the `model` rows in `run_modes`, or every run the
  model can score? Once a call is confirmed by hand it becomes a `user` row and
  its confidence is gone (`confidence` is NULL for `user`/`rule`), so as of the
  19 Sep 2026 snapshot the first option shows just **2 rows** (Duncan, both
  `regular`, 72-78%). A useful tab probably means the walk-forward harness's
  score for every George/Duncan run, from its own past only, set beside the
  current label. That is a computation, not a lookup, and it needs `scipy` on
  the hosted app.
- **Main app or `/buggy-handicap`.** A tab in the main app puts per-run
  statistical calls about two named people in front of every visitor, which is
  the reason the handicap analysis sits on the unlisted route. That route may be
  the better home.
- **It reverses a decision.** The UI deliberately shows estimated labels the
  same as confirmed ones, because verification is treated as a database job, not
  something the page shows (`CLAUDE.md` § Buggy mode in the UI). This tab would
  be the first place the page separates the two. That is fine, but it should be
  a deliberate choice.
- **Reliability beside confidence.** The refresh notification shows each call's
  confidence alongside the measured reliability of that *kind* of call. The tab
  should too, because a 52% `regular` call and a 92% one are not the same claim
  (see Duncan's Cassiobury run, 12 Sep 2026).

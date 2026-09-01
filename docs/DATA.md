# Data notes — the buggy labels

What cannot be read off the schema. For the schema itself see the data-model
tables in `CLAUDE.md`; for the method see *Feature 2 — head-to-head* there.

## Why labels exist

George and Duncan sometimes run pushing a buggy. parkrun records nothing about
it, so their times are slower for a reason the data cannot see. Every
form-adjusted comparison in the app is built on a 91-day median, so without a
label buggy and non-buggy runs pool into one target — which flatters the buggy
runs and penalises the non-buggy ones. `parkrun.run_modes` is the label store.

## The three `source` values are not interchangeable

| `source` | Written by | Trains the model? |
|---|---|---|
| `manual` | The review spreadsheet import, or your own SQL correction | **yes** |
| `estimated` | The model, on new runs | **yes** |
| `default` | Backfill of runs nobody reviewed; Raju's rows | **no** |

`default` is excluded from training on purpose. Those rows are *assumptions* —
"probably not a buggy" — and training on them would assert several hundred
unverified negatives as confirmed fact.

## The training set grows two ways

1. **A one-off backfill** — the review spreadsheet, imported once. It seeds the
   set; it is not a recurring cycle.
2. **Your corrections, thereafter.** The weekly refresh notification lists the
   week's guesses. A wrong one is corrected by hand to `source='manual'`, which
   outranks the estimate permanently.

**A correction is worth far more than an agreement.** An estimated label agrees
with the model by construction, so it adds sample size without moving the
decision boundary. A correction lands exactly where the boundary is wrong. This
is why the notification is the whole review step — reading it is the mechanism.

## Correcting a label by hand

Edits go to the **source of truth**, never to a dev copy or the snapshot:

```bash
duckdb ~/.config/parkrun/parkrun_local.duckdb
```

```sql
INSERT OR REPLACE INTO parkrun.run_modes
      (athlete_id, run_date, event_id, is_buggy, source, confidence, reason, set_at)
SELECT athlete_id, run_date, event_id,
       TRUE,                       -- the correct answer
       'manual',                   -- outranks any estimate, permanently
       NULL,
       'corrected by hand',
       now()
FROM parkrun.results
WHERE athlete_id = 5462426          -- Duncan
  AND run_date   = DATE '2026-03-14';
```

Changing a `default` row to `manual` is how an assumption becomes evidence — one
statement, and it starts training the model.

The change reaches the deployed app at the next refresh, which rebuilds
`data/parkrun_snapshot.duckdb` and pushes it. `data/parkrun_run_modes.csv` is
exported on every refresh, so the edit also shows up as a reviewable diff.

## Things that will surprise you

- **Labelling a past run changes past results.** A label changes that run's
  target, so its `pct_diff`, possibly its `place_rank`, and therefore the
  head-to-head leaderboard back to 2023. Intended, but not obvious.
- **Labels are write-once.** The estimator never re-scores a run that already
  has a row. A run scored on the Saturday it happened had only a backwards-
  looking window and keeps that verdict unless you correct it.
- **The handicap is recomputed every refresh**, and it feeds targets, so it can
  shift past head-to-heads even though the labels themselves are fixed. The
  blast radius is limited to runs whose target was bridged from the other mode.

## Course difficulty

`data/course_difficulty.csv` caches the published UK course-difficulty scores
(835 courses, 0–12 where 12 is hardest, from median finish times over roughly
1 Jan 2023 – 25 Jan 2025). `scripts/fetch_course_difficulty.py` fetches it; the
refresh only ever applies the cached CSV, so the scheduled path needs no network.

It is a **covariate for the buggy estimator**, not an adjustment to the
head-to-head target. A head-to-head still compares against form averaged across
whatever courses were in the window.

### Names that differ between the two sources

`alias_of` maps a published name onto our `events.short_name`. Where a venue
publishes several course variants, **every** row is aliased so the mapping stays
complete and traceable, and the loader decides which to use:

1. **If one variant is `(Main)`, that one is used alone.** Main is the course the
   event normally runs; a seasonal alternative may be used only a few weeks a
   year, and averaging it in drags the score toward a course they mostly did not
   run. Eastbourne takes `(Main)` 2.0 rather than the 2.3 average with
   `(Summer)`.
2. **Otherwise the variants are averaged.** For a true Winter/Summer pair there
   is no principled pick — we do not know which season a given run fell in, and
   choosing arbitrarily would be a silent coin-flip between scores that can
   differ a lot (Bromley: Winter 1.2, Summer 2.5).

Both rules live in `apply_course_difficulty`, not in the CSV, so they also apply
to any variant group aliased later. Current aliases:

| Published | → `short_name` |
|---|---|
| `Bromley (Winter)` + `Bromley (Summer)` | Bromley — averaged, 1.85 |
| `Eastbourne (Main)` + `Eastbourne (Summer)` | Eastbourne — **`(Main)` used alone, 2.0** |
| `Foots Cray Meadows (Winter/Summer)` | Foots Cray Meadows — averaged, 3.80 |
| `Jersey Farm (Winter/Summer)` | Jersey Farm — averaged, 3.45 |
| `Medina I.O.W. (Winter/Summer)` | Medina I.O.W. — averaged, 3.60 |

Plus one rename:

| Published | → `short_name` |
|---|---|
| `Greenwich` | Avery Hill |

**`Greenwich` is Avery Hill's former name** (confirmed by the athletes, 1 Sep
2026). The evidence agreed before the confirmation: Avery Hill's
`events.location` is *"Avery Hill Park, Greenwich"*; we have run it since
**2021-11-06**, so it existed throughout the dataset's Jan 2023 – Jan 2025
window and an essentially-complete UK list (835 courses against 896 live UK
events today) should contain it; and no row bore its current name.

It is **not** `Greenwich Peninsula`, which was the first guess on name
similarity alone and was withdrawn — our first run there was 2026-01-31, a year
after the dataset's cutoff, so it is almost certainly absent from the source
entirely. A renamed course is the general case to watch: the published name is a
snapshot of early 2025, so any parkrun that has since been renamed will look
unmatched under its current `short_name`.

### Currently unmatched (6 UK events, all by design)

Queenswood Country Park, Stanborough, Rothamsted Park and `Greenwich Peninsula`
postdate the dataset's Jan 2025 cutoff. `Jubilee, Bedford`'s two candidate
published names (`Jubilee`, `Bedford`) are both taken by other real parkruns.
`Holywell King George V Playing Fields` is not the source's `King George V
Playing Field`, which is a different parkrun we also run.

A missing score is never fatal — the run falls back with lower confidence.

### When to re-fetch

The scores do not age; courses do not get harder. **What ages is the course
list** — new parkruns launch regularly and Duncan runs a new venue most weeks.
`apply_course_difficulty` logs coverage on every refresh and warns when either
trigger fires:

1. the cached CSV is more than **90 days** old, or
2. UK run coverage falls below **95%**, or more than **20 UK events** are
   unmatched.

Baseline as built (1 Sep 2026): **163/204 events, 99% of UK runs, 6 UK events
unmatched.** Coverage is counted over UK events only — the non-UK ones are
missing by design and would swamp the signal.

A warning never blocks the refresh. It is a prompt to run
`python scripts/fetch_course_difficulty.py` when convenient, then re-check the
`alias_of` column for any new near-miss names.

### Variant groups not yet aliased

The source publishes 19 variant groups; only the 5 above are aliased, because
those are the only ones we have run. For the other 14 the base name matches
nothing (the source has `Cheltenham (Summer)`/`(Winter)` but no `Cheltenham`),
so the first run at any of them lands **unmatched** and needs its rows aliased:

Alton Water, Belvoir Castle, Bicester, Brockenhurst, Cheltenham, East Brighton,
Fell Foot, Horsham, Hunstanton Promenade, Isabel Trail, Marine Parade, Mount
Stuart, Nobles, Seven Fields.

Four of those have a `(Main)`/`(Alternative)` split rather than a seasonal one —
Horsham, Isabel Trail, Marine Parade, Nobles — and rule 1 handles them. Watch
Marine Parade in particular: `(Main)` is 1.2 and `(Summer)` is 5.2, so getting
the rule wrong there would be a whole-scale error.

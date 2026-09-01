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

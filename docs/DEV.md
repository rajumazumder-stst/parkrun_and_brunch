# Local development

How to make changes and preview them in a browser **without touching the live /
deployable version**.

## What "live" means here

There is no hosted preview (a deliberate choice). "Live" is the
**`main` branch** plus the tracked, deployable **`data/parkrun_snapshot.duckdb`**.
Local development is isolated from both:

| Concern | Isolation mechanism |
|---|---|
| Code changes | Work on the **`dev`** branch (or a feature branch), never `main`. |
| Data / experiments | The app runs against **`data/parkrun_dev.duckdb`** — a gitignored, parkrun-only copy of the snapshot. The tracked snapshot and the personal-finance dev DB are never written. |

## Run it

```bash
./scripts/run_local.sh
```

On first run this creates `data/parkrun_dev.duckdb` from the snapshot, activates
the project venv, and opens the app at http://localhost:8501. The app is
read-only, so previewing never mutates any database.

To point at a different DB (e.g. the full dev DB for the freshest data — it holds
`personal_finance` too, but the app only reads the `parkrun` schema):

```bash
PARKRUN_DB=~/Documents/duckdb/my_database.duckdb ./scripts/run_local.sh
```

## Refreshing the dev data

`data/parkrun_dev.duckdb` is a throwaway copy. To reset it to the current
snapshot, just delete it — the next `./scripts/run_local.sh` recreates it:

```bash
rm data/parkrun_dev.duckdb
```

A **new view** (e.g. `v_saturday_targets`) is defined once in the pipeline's
`ensure_views()`, then materialised into this dev DB by running that function
against it:

```bash
python -c "import duckdb, parkrun_pipeline as p; p.ensure_views(duckdb.connect('data/parkrun_dev.duckdb'))"
```

`build_snapshot()` also re-runs `ensure_views()`, so the view is folded into the
tracked snapshot on the next `python parkrun_pipeline.py snapshot` (for release).

## Promoting to "live"

When a change is ready: commit on `dev`, merge to `main`, and (if the change
touched the data model/views) regenerate `data/parkrun_snapshot.duckdb` via
`python parkrun_pipeline.py snapshot` so the deployable snapshot matches.

## The label-impact tab (dev only)

```bash
PARKRUN_LABEL_AUDIT=1 ./scripts/run_local.sh
```

Adds a sixth tab comparing the live head-to-head against
`v_head_to_head_legacy` — the frozen pre-buggy method (a single pooled 91-day
median, no mode split, no handicap bridge). It reports, per occasion, whether
the winner, the places, the ranked roster or just the margin moved, and lets you
put the old and new victory charts side by side for any one contest.

Two independent gates: the env var, and the legacy views actually existing.
`run_local.sh` builds them into the dev DB when the var is set; nothing builds
them into the source of truth or the deploy snapshot, so the hosted app can
never show the tab.

**With no labels written it must report every occasion `Unchanged`.** That is
the zero-label equivalence check as a live page: the new views have to reproduce
the old ones exactly until a label says otherwise. A `Lost` occasion should be
impossible — the bridge can only make *more* contests rankable — and the tab
calls one out in red if it ever appears.

To see it do something, write synthetic labels into the dev DB (never the source
of truth):

```sql
INSERT INTO parkrun.run_modes (athlete_id, run_date, event_id, is_buggy, source, reason)
SELECT athlete_id, run_date, event_id, TRUE, 'manual', 'synthetic'
FROM parkrun.results WHERE athlete_id = 5462426 AND run_date >= DATE '2026-03-01';
```

Then delete `data/parkrun_dev.duckdb` when you are done — it is disposable, and
leaving synthetic labels lying around in it is a trap.

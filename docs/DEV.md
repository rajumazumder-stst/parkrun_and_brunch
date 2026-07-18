# Local development

How to make changes and preview them in a browser **without touching the live /
deployable version**. This is stage 1 of `PLAN.md`.

## What "live" means here

There is no hosted preview (a deliberate choice — see `PLAN.md`). "Live" is the
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

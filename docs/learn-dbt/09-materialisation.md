# 9 — Materialisation and the deploy snapshot

## Goal
A worked-out plan for how dbt fits into the refresh **and** into
`data/parkrun_snapshot.duckdb`, the read-only file the hosted app serves.

## Concept
**Materialisation** is how a model lands: `view` (stored SQL, computed on
read), `table` (computed once at build), `incremental` (lesson 8) or
`ephemeral` (inlined as a CTE, never stored). Set it per folder in
`dbt_project.yml`, or per model with `{{ config(materialized=...) }}`.

`dbt-duckdb` writes every reference fully qualified, as
`"database"."schema"."model"`, where `database` comes from the file name
(lesson 1). **A view therefore remembers the catalog name of the file it was
built in.**

## In this project
How the snapshot is built today (`build_snapshot`, `parkrun_pipeline.py:1685`):
1. Copy the tables into a **new** file `parkrun_snapshot.duckdb.tmp` with
   `CREATE TABLE AS`.
2. Open that file **on its own** and run `ensure_views()`, so the views bind
   to the file's own `parkrun` schema.
3. Atomically rename the file over the committed snapshot.

Step 2 exists because a view copied from the dev DB would still point at the
dev DB's catalog. The same trap applies to dbt views. Built in the dev DB they
read `"parkrun_dev"."dbt_parkrun"…`, and the hosted app opens a file whose
catalog is `parkrun_snapshot`.

Two ways out:
- **A: marts as tables in the snapshot.** In step 2, run dbt with a
  `snapshot` target whose `path` is the temp file, `materialized: table` for
  the marts. Tables hold data, not references, so there is nothing to rebind.
  The hosted app is read-only, so "computed on read" buys it nothing anyway.
  Watch the derived name: `parkrun_snapshot.duckdb.tmp` gives catalog
  `parkrun_snapshot.duckdb`. Name the temp file `…/tmp/parkrun_snapshot.duckdb`
  instead, or set `database` explicitly.
- **B: keep views, rebuild in place.** The same as today, with dbt in
  place of `ensure_views`. It works, but it keeps the catalog-name coupling.

Then the single-definition rule for the vars:
**call dbt from Python**, so `TARGET_WINDOW_DAYS`, the cohort and the handicap
default are read from `parkrun_core` and handed over with `--vars`. dbt-core
has a programmatic entry point (`dbt.cli.main.dbtRunner`) that the pipeline can
call inside `_finalize`, in place of `ensure_views`.

## Exercise
1. Decide A or B and write down why.
2. Sketch `scripts/dbt_build.py`, or a function in the pipeline: it builds
   the `--vars` dict from `parkrun_core`, and invokes `dbtRunner().invoke([...])`
   with `--target` and `--profiles-dir`. It returns non-zero on failure.
3. Find **where in `_finalize`** (`:1919`) it runs. It must come after
   `update_current_targets` and before the snapshot is renamed into place.

## Check
`python -c "import parkrun_core as c; print(c.TARGET_WINDOW_DAYS, c.ATHLETE_NAMES)"`
is the only place those numbers come from. `grep -rn "91" dbt/` finds nothing.

<details><summary>Solution — sketch</summary>

```python
# scripts/dbt_build.py (sketch)
import json, sys
from dbt.cli.main import dbtRunner
import parkrun_core as c

def dbt_build(target: str) -> None:
    vars_ = {"target_window_days": c.TARGET_WINDOW_DAYS,
             "athletes": c.ATHLETE_NAMES,
             "buggy_handicap_default": 0.15}   # move this constant to parkrun_core first
    res = dbtRunner().invoke(["build", "--project-dir", "dbt", "--profiles-dir", "dbt",
                              "--target", target, "--vars", json.dumps(vars_)])
    if not res.success:
        sys.exit("dbt build failed")
```
**Recommendation: A.** The snapshot is a build artefact for a read-only
reader, which is what `table` is for. Confirm the `dbtRunner` call against
the dbt docs for the installed version before relying on it.
</details>

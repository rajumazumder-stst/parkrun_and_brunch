# 4 — Staging models

## Goal
One thin **staging** model per source table the derived layer uses:
`stg_results`, `stg_run_modes`, `stg_athletes`, `stg_events`,
`stg_buggy_handicap`.

## Concept
Layers make a dbt project readable:

| Layer | Prefix | Job |
|---|---|---|
| staging | `stg_` | One model per source table. Rename, cast, light cleaning. **No joins.** |
| intermediate | `int_` | Joins and reusable building blocks |
| marts | `fct_` / `dim_` | What the app reads |

Staging is where `source()` is called, **and nowhere else**. Every later model
uses `ref()`, which is how dbt learns the build order.

## In this project
The sources are already clean. The pipeline parses and casts on load
(`time_to_seconds`, `parkrun_pipeline.py:176`), so staging is close to
`select *`. Two things are still worth doing:
- **Name the columns explicitly.** `SELECT r.*` in `v_results_moded`
  (`:682`) means any column the pipeline adds flows silently into every view.
  An explicit list makes that change visible.
- **Filter early where a rule says so.** For example, if every derived model
  ignores runs without a time, decide it once here. Check first: does
  `v_head_to_head` ignore them? (`median`/`count` skip NULLs, but `parts`
  does not.)

## Exercise
1. Create `dbt/models/staging/stg_results.sql` selecting `results` columns by
   name from `{{ source('parkrun', 'results') }}`.
2. Do the same for `run_modes` (`athlete_id, run_date, event_id, is_buggy,
   source, reason`), `athletes`, `events` (just what the views use:
   `event_id, short_name`) and `buggy_handicap`.
3. `dbt build --profiles-dir . --select staging`

## Check
```sql
-- in duckdb data/parkrun_dev.duckdb
SELECT count(*) FROM dbt_parkrun.stg_results;   -- equals parkrun.results
SELECT count(*) FROM parkrun.results;
```

<details><summary>Hints</summary>

- `DESCRIBE parkrun.results;` lists the columns.
- Do not filter NULL times in staging. `v_head_to_head` keeps such rows in
  `parts`, so filtering here would break parity in lesson 6. Note it as a
  question instead.
</details>

<details><summary>Solution — stg_results</summary>

```sql
-- models/staging/stg_results.sql
select
    athlete_id,
    event_id,
    run_date,
    run_number,
    position,
    time,
    time_seconds,
    age_grade,
    pb_flag,
    scrape_timestamp
from {{ source('parkrun', 'results') }}
```
The other four follow the same pattern.
</details>

# 8 — `current_targets`: incremental, not snapshot

## Goal
A reasoned decision on whether dbt should build `current_targets`, and, if you
choose yes, the model.

## Concept
Two dbt features sound right for a table that grows each refresh and keeps its
history. Only one of them fits.

- A **snapshot** (`snapshots/`) records how a row **changes over time**:
  slowly changing dimensions, type 2. Each row gets `dbt_valid_from` /
  `dbt_valid_to`, and dbt closes the old version when a value changes.
- An **incremental model** appends (or upserts) only the **new** rows on each
  run: `{% if is_incremental() %}` limits the `SELECT` to what is new, and
  `unique_key` + an `incremental_strategy` (`append`, `delete+insert`) decide
  how they land. `--full-refresh` throws the table away and rebuilds it from
  the SQL.

## In this project
`current_targets` (`CLAUDE.md` § Data model) is **one new set of rows per
refresh date, never revised**. That is an append log, not a changing
dimension, so a snapshot is the wrong tool. It would also make the table's
key into validity ranges nobody reads.

An incremental model fits, but with a trap. **The SELECT cannot reproduce the
past.** Its rows record what the target *was* before later relabelling. On
`--full-refresh` an incremental model reruns its SQL over today's data and
writes a history that never existed. Answer these before writing it:
1. On the first build, where does the existing history (27 Jun 2026 onwards)
   come from? The old table, so the model reads its own predecessor as a source.
2. What stops `--full-refresh`? The `full_refresh: false` config.
3. Is that still simpler than leaving the 30-line `update_current_targets`
   (`parkrun_pipeline.py:1413`) where it is?

Also read `docs/DEV.md` § Open decisions 4 first. If tab 2 moves to a live
view, nothing in the app reads this table any more, and the question shrinks
to "where should a write-only history live".

## Exercise
Write your answer to the three questions. If you decide to build it:
`models/marts/fct_current_targets.sql`, incremental, `unique_key =
['refresh_date', 'athlete_id', 'mode']`, `delete+insert`, `full_refresh:
false`. On a first build it copies the old table plus today; on an
incremental run it inserts today only.

## Check
- Run `dbt build` twice on the same day. Today's rows exist once.
- `dbt build --full-refresh` refuses to rebuild this model.
- Every pre-today row is identical to `parkrun.current_targets`.

<details><summary>Solution — the recommendation</summary>

**Keep `current_targets` in Python for now.** It is state, the same kind as
`run_modes` (lesson 0). The pipeline writes it at one exact point in
`_finalize` (after labels, before the snapshot), and that ordering is load-bearing.
Revisit once Open decision 4 is settled.

If you build it anyway, as practice:
```sql
{{ config(materialized='incremental', unique_key=['refresh_date','athlete_id','mode'],
          incremental_strategy='delete+insert', full_refresh=false) }}
with today as (
    -- the grid + median as update_current_targets, anchored on current_date
    ...
)
{% if is_incremental() %}
select * from today
{% else %}
select refresh_date, athlete_id, mode, target_seconds, n_window
from {{ source('parkrun', 'current_targets') }}
where refresh_date < current_date
union all
select * from today
{% endif %}
```
</details>

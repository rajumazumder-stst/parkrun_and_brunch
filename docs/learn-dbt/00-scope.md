# 0 — What dbt owns here, and what it must not

## Goal
A clear line between what dbt will build and what stays in Python. Draw it
before you write a model. Every later lesson depends on it.

## Concept
dbt is a **build tool for derived data**. You write `SELECT` statements
(*models*), and dbt works out their dependencies and creates them as views or
tables, in order. Its default habit is **drop and recreate**: a model's
contents are whatever its SQL says now. That is perfect for anything that can
be recomputed from inputs, and dangerous for anything that cannot.

## In this project
Sort every object in the `parkrun` schema into one of three kinds:

| Kind | Objects | Who builds it |
|---|---|---|
| **Loaded** from outside | `events` (from `events.json`), `results` (scraped), `travel_times` (ORS), `course_difficulty`, `event_countries`, `country_lookup`, `athletes` | Python (`parkrun_pipeline.py`) — dbt reads them as **sources** |
| **State**: cannot be rebuilt | `run_modes` (844 hand labels from the review sheet), `model_estimates` (write-once by design), `current_targets` (frozen history), `buggy_handicap` (measured values) | Python. **Never dbt-materialised** (see below) |
| **Derived**: a pure function of the above | `v_results_moded`, `v_overlap`, `v_head_to_head`, `v_saturday_targets` (`parkrun_pipeline.py:670-832`) | **dbt** |

Why the state tables stay out:
- `CLAUDE.md` § `run_modes`: "no automated code path is **capable** of
  changing an existing `is_buggy`". A dbt model of `run_modes` would be exactly
  such a path. `dbt run --full-refresh` drops and recreates it.
- `CLAUDE.md` § `model_estimates`: write-once is what makes a hand correction
  non-destructive.
- `current_targets` is a table *because* it must not be recomputed (lesson 8
  revisits this).

Three open questions from `TODO.md` § dbt with DuckDB land in later lessons:
- read-only hosted snapshot → lesson 9;
- the 91-day window's one definition → lesson 5;
- the dev-only legacy views → lesson 11.

## Exercise
1. Open `parkrun_pipeline.py` and list every `CREATE TABLE` and `CREATE ...
   VIEW`. Check that each one is in the table above. Are any missing?
2. For each **derived** view, write down which objects it reads (its *parents*).
   Draw the graph on paper. This is the DAG dbt will draw for you in lesson 10.

## Check
Your graph should have `results` and `run_modes` at the bottom and
`v_head_to_head` at the top, with `v_results_moded` in between.

<details><summary>Solution — the graph</summary>

```
results ─┬──────────────────────────────► v_overlap
         └─► v_results_moded ◄─ run_modes
                 │
                 ├──► v_saturday_targets ◄─ athletes
                 └──► v_head_to_head ◄─ athletes, events, buggy_handicap
                        (parts also reads results directly)
```
`update_current_targets` (`:1413`) reads `v_results_moded` and `athletes` too,
but it *writes a state table*, so it is not a derived view.
</details>

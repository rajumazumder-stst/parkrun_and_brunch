# 3 — Seeds, and why most CSVs here aren't

## Goal
A decision, per CSV in `data/`, on whether it becomes a dbt **seed**.

## Concept
A **seed** is a CSV in `dbt/seeds/` that `dbt seed` loads into a table.
Seeds are meant for **small, static, hand-maintained** lookups that belong to
the dbt project: code to name maps, mappings, thresholds. They are **not** a
loader for data that arrives from elsewhere or that other code writes.

## In this project
Every CSV in `data/` already has an owner:

| CSV | Written by | Read by |
|---|---|---|
| `country_lookup.csv` | hand | pipeline seeds `country_lookup` |
| `athletes_lookup.csv` | hand | pipeline seeds `athletes` |
| `event_countries.csv` | hand (after the 4 Oct 2026 placement) | `apply_event_countries` |
| `course_difficulty.csv` | `scripts/fetch_course_difficulty.py` + hand `alias_of` | `apply_course_difficulty` |
| `parkrun_events.csv` | events dump + the Victoria Dock manual row | bootstrap seed, then reconcile |
| `parkrun_results.csv` | **pipeline export** | nothing (audit trail) |
| `parkrun_run_modes.csv` | **pipeline export** | **nothing, by rule** (`CLAUDE.md` § `run_modes`) |

## Exercise
For each CSV, answer three questions:
1. Is it hand-maintained?
2. Does anything other than SQL read it (the Python estimator, the app)?
3. Would a second loader create a second source of truth?

Then decide: seed, or leave it to the pipeline.

## Check
Your answer for `parkrun_run_modes.csv` must be **no**. That file has "one
writer and no readers", and a seed would be a reader. That would let it become a
competing source of truth, which is exactly what `CLAUDE.md` forbids.

<details><summary>Solution — a defensible split</summary>

- **Candidate seeds:** `country_lookup.csv`, `athletes_lookup.csv`. Both are
  small and hand-kept, and SQL only joins them. They could still stay pipeline-loaded
  for now: the pipeline seeds them at bootstrap, and moving them changes who
  owns the `athletes` table, which the app reads.
- **Not seeds:**
  - `event_countries.csv` and `course_difficulty.csv`: the pipeline enriches
    them in Python (nearest-parkrun placement, aliases).
  - `parkrun_events.csv`: reconcile updates the table it seeds.
  - The two exports: dbt must never read them.

A good answer for now is **no seeds at all**: everything stays a source. You
reach for seeds later, when dbt owns the bootstrap. Knowing *why* matters more
than having one.
</details>

# 2 — Sources

## Goal
dbt knows about the tables the pipeline loads, so models can say
`{{ source('parkrun', 'results') }}` instead of hard-coding `parkrun.results`.

## Concept
A **source** declares a table dbt reads but does not build. Declaring it buys
you three things:
- **lineage**: the DAG starts from a named source, not from an anonymous table
  name;
- **tests on raw data** (lesson 7);
- **freshness**: `dbt source freshness` warns when the newest row is older
  than you expect.

## In this project
- Everything the pipeline writes is a source, both the loaded tables and the
  state tables (lesson 0). They all live in schema `parkrun`.
- Freshness has a natural column: `results.scrape_timestamp`. It is the same
  fact the sidebar's "Pipeline last run" shows (`CLAUDE.md` § Visualisations).
  Refreshes run Saturday and Sunday, so data more than 8 days old means a
  refresh was missed.
- `run_modes.set_at` is another timestamp. Labels are written out of band, so
  a freshness check there means less. Leave it off.

## Exercise
Write `dbt/models/sources.yml` declaring source `parkrun` (schema `parkrun`)
with these tables:
- `results`, `run_modes`, `events`, `athletes`, `country_lookup`,
  `buggy_handicap`, `current_targets`, `model_estimates`, `event_countries`,
  `course_difficulty`, `travel_times`.

Then give `results` a freshness rule: warn after 8 days, error after 15.

## Check
```bash
dbt source freshness --profiles-dir .
```
It reports `results` as PASS or WARN, depending on how old your dev copy is.
That is a useful first sight of the check working.

<details><summary>Hints</summary>

`loaded_at_field` names the timestamp column. A `freshness:` block has
`warn_after` and `error_after`, each `{count: N, period: day}`.
</details>

<details><summary>Solution</summary>

```yaml
version: 2

sources:
  - name: parkrun
    schema: parkrun
    description: Tables written by parkrun_pipeline.py. dbt reads, never writes.
    tables:
      - name: results
        loaded_at_field: scrape_timestamp
        freshness:
          warn_after: {count: 8, period: day}
          error_after: {count: 15, period: day}
      - name: run_modes
        description: Per-run buggy label. STATE — cannot be rebuilt.
      - name: events
      - name: athletes
      - name: country_lookup
      - name: buggy_handicap
      - name: current_targets
        description: Frozen per-refresh form targets. STATE.
      - name: model_estimates
        description: Write-once estimator calls. STATE.
      - name: event_countries
      - name: course_difficulty
      - name: travel_times
```
</details>

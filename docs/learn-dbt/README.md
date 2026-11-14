# Learning dbt on this project

A course in twelve lessons for moving this project's **data-building layer**
to dbt (`dbt-duckdb`), designed for building it yourself. Every lesson is about
*this* repo: the tables you already have, the views in `parkrun_pipeline.py`,
the rules in `CLAUDE.md`. Nothing uses a made-up example.

**How to use it.** Each lesson has the same parts:

| Part | What it is |
|---|---|
| **Goal** | What exists at the end of the lesson |
| **Concept** | The dbt idea, kept short |
| **In this project** | Where the idea lands here, with file:line references |
| **Exercise** | What you write |
| **Check** | A command or query that proves it worked, usually a parity query against the existing view |
| **Hints** / **Solution** | Folded away. Open them only when you're stuck |

**The rule for the whole course: dbt builds into its own schema
(`dbt_parkrun`) in the dev DB, beside the pipeline's `parkrun` schema, until
lesson 11.** That way every model can be checked row for row against the view
it replaces, and the app, the refresh and the deploy snapshot stay untouched
while you learn.

| # | Lesson | You build |
|---|---|---|
| 0 | [What dbt owns here, and what it must not](00-scope.md) | A drawn line between the state tables and the derived layer |
| 1 | [Setup](01-setup.md) | `dbt/`, `profiles.yml`, `dbt debug` green |
| 2 | [Sources](02-sources.md) | `sources.yml` over the `parkrun` schema, with freshness |
| 3 | [Seeds, and why most CSVs here aren't](03-seeds.md) | A decision per CSV |
| 4 | [Staging models](04-staging.md) | `stg_results`, `stg_run_modes`, `stg_athletes`, … |
| 5 | [Intermediate models, vars and macros](05-intermediate.md) | `int_results_moded`, the window as a `var`, a `form_window` macro |
| 6 | [Marts](06-marts.md) | `fct_overlap`, `fct_saturday_targets`, `fct_head_to_head` |
| 7 | [Tests](07-tests.md) | Generic, singular and **unit** tests |
| 8 | [`current_targets`: incremental, not snapshot](08-incremental.md) | A design decision and (optionally) the model |
| 9 | [Materialisation and the deploy snapshot](09-materialisation.md) | The plan for `build_snapshot` |
| 10 | [Docs, lineage and Python models](10-docs-python.md) | `dbt docs`, and why the estimator stays Python |
| 11 | [Cut-over](11-cutover.md) | Parity everywhere, then `ensure_views` → `dbt build` |

**Versions checked on 2 Nov 2026:** `dbt-core` 1.12.6 and `dbt-duckdb` 1.11.1
install on Python 3.14 alongside the project's pinned `duckdb==1.5.4`.

**Related:** `TODO.md` § dbt with DuckDB (the open questions), `docs/DEV.md`
§ Open decisions 4 (the target arithmetic written out three times — a macro
removes it), `adhoc/target_methods/` (a target-method change would land in one
model instead of three places).

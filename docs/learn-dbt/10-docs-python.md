# 10 — Docs, lineage and Python models

## Goal
A browsable lineage graph of the project, and a clear answer on whether
`buggy_estimator.py` should become a dbt Python model.

## Concept
- **`dbt docs generate` / `dbt docs serve`** builds a site from your YAML
  `description:`s and the DAG. Descriptions belong on the things a reader
  meets: sources, marts and odd columns (`target_basis`, `n_window`).
- **Python models** (`.py` in `models/`, a `def model(dbt, session)` returning
  a DataFrame) let a step in the DAG be pandas instead of SQL. `dbt-duckdb`
  supports them; they run in the same process.

## In this project
- Lineage: lesson 0's hand-drawn graph, now generated. Compare the two.
- Descriptions already exist in prose in `CLAUDE.md` § Data model. Move the
  **one-line** version of each into YAML, and leave the reasoning in `CLAUDE.md`.
- The estimator (`buggy_estimator.py`) is the obvious Python-model candidate,
  and the wrong one:
  - It **writes state**. `apply_model_labels` inserts into `run_modes`, and
    `build_model_estimates` into `model_estimates`, write-once. A dbt model
    replaces its output on every build.
  - It is **forward-only**. Runs behind the label frontier are left for hand
    review (`CLAUDE.md` § Refresh pipeline). A model that recomputes
    everything has no frontier.
  - Its importance lies in being "side-effect free and testable without a
    database" (`CLAUDE.md`, `buggy_estimator.py` row). That is already true.

## Exercise
1. Add `description:` to the five sources and three marts you built. Run
   `dbt docs generate --profiles-dir . --vars '…' && dbt docs serve --profiles-dir .`.
2. In the graph, click `fct_head_to_head` and follow it upstream. Is it the
   graph from lesson 0?
3. Write two sentences: which part of the pipeline, if any, *would* be a good
   Python model? Look for something that is derived, deterministic and has no
   writes.

<details><summary>Solution — one answer</summary>

A candidate is the **spread / target-method comparison** in
`adhoc/target_methods/`. It is derived, deterministic, has no writes, and M3
(sequential smoothing) is awkward in SQL. If a target method like M3 were ever
adopted, a Python model computing per-run targets, feeding a SQL
`fct_head_to_head`, would be the natural shape. The estimator stays Python
outside dbt.
</details>

# 1 — Setup

## Goal
A `dbt/` project in the repo that connects to an **isolated copy** of the data
and passes `dbt debug`.

## Concept
- A **project** is a folder with `dbt_project.yml`, plus `models/`, `seeds/`,
  `tests/`, `macros/`.
- A **profile** (`profiles.yml`) says which database to connect to. One profile
  can have several **targets** (dev, snapshot, …); `--target` picks one.
- With `dbt-duckdb`, `path` is the DuckDB file. The **database name** dbt uses
  is derived from the file's basename: `parkrun_dev.duckdb` → `parkrun_dev`.
  That matters in lesson 9.

## In this project
- **Never point dbt at the source of truth** (`~/.config/parkrun/parkrun_local.duckdb`)
  or at `~/Documents/duckdb/my_database.duckdb`. Use the dev DB that
  `scripts/run_local.sh` builds: `data/parkrun_dev.duckdb`, created by
  `python parkrun_pipeline.py seed` and gitignored (`docs/DEV.md`).
- **Pin duckdb.** A plain `pip install dbt-duckdb` pulls `duckdb` 2.0, and the
  project pins `duckdb==1.5.4` (`requirements.txt`). A new DuckDB can upgrade
  the file format, and the hosted app would then fail to open the snapshot. Install with the
  requirements file as a **constraint**.
- DuckDB allows **one writer**. Stop the local app (or DBeaver) on the dev DB
  before `dbt build`.
- Keep `profiles.yml` **inside** `dbt/` (it holds no secret here) and point dbt
  at it with `--profiles-dir .` or `DBT_PROFILES_DIR`.

## Exercise
1. Install into the project venv:
   ```bash
   source ~/Documents/Python\ scripts/env/bin/activate
   pip install dbt-duckdb -c requirements.txt
   python -c "import duckdb; print(duckdb.__version__)"   # must still say 1.5.4
   ```
2. Create `dbt/dbt_project.yml` (project name `parkrun`) and `dbt/profiles.yml`
   with one target, `dev`, whose `path` is the dev DB and whose default
   `schema` is `dbt_parkrun`.
3. Make sure `data/parkrun_dev.duckdb` exists (`./scripts/run_local.sh` once,
   or `PARKRUN_PIPELINE_DB=data/parkrun_dev.duckdb python parkrun_pipeline.py seed`).
4. `cd dbt && dbt debug --profiles-dir .`

## Check
`dbt debug` ends with `All checks passed!`.

<details><summary>Hints</summary>

- `path` in `profiles.yml` is relative to the profiles file, so from `dbt/` the
  dev DB is `../data/parkrun_dev.duckdb`.
- `dbt init` asks questions interactively; writing the two files by hand is
  quicker and teaches more.
- Add `dbt/target/` and `dbt/logs/` to `.gitignore` — dbt writes compiled SQL
  and logs there.
</details>

<details><summary>Solution</summary>

`dbt/dbt_project.yml`
```yaml
name: parkrun
version: "1.0.0"
profile: parkrun
model-paths: ["models"]
seed-paths: ["seeds"]
test-paths: ["tests"]
macro-paths: ["macros"]
clean-targets: ["target", "dbt_packages"]

models:
  parkrun:
    +materialized: view
```

`dbt/profiles.yml`
```yaml
parkrun:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: ../data/parkrun_dev.duckdb
      schema: dbt_parkrun
      threads: 1
```
`threads: 1` because DuckDB has one writer and the models are small.
</details>

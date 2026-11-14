# 11 — Cut-over

## Goal
A plan, and then the change, that swaps `ensure_views()` for `dbt build`,
without the app, the refresh or the hosted snapshot noticing.

## Concept
A safe migration has three phases:
1. **Shadow.** dbt builds beside the old path into `dbt_parkrun`, and parity is
   checked on every build. Lessons 1-10 are this phase.
2. **Switch.** Consumers read the dbt output; the old path is kept, unused.
3. **Remove.** The old path is deleted once one or more real refreshes have
   gone through.

## In this project
Things the switch has to carry:
- **The app reads `parkrun.v_*` by name.** Either dbt builds into schema
  `parkrun` with the same names (set `alias` per model, schema `parkrun`), or
  the app's queries change. Same names means **no app change**, which is the
  smaller diff.
- **The tests.** `tests/test_ui.py` smoke-runs the app against the snapshot,
  so it covers the switch if the snapshot is built by dbt. Add the parity
  query as a pytest only for the shadow phase, then delete it.
- **Legacy views** (`v_head_to_head_legacy`, `ensure_legacy_views`, dev only,
  `PARKRUN_LABEL_AUDIT=1`): keep them outside dbt. They are frozen retired
  arithmetic for one dev tool, and dbt would make them part of every build.
- **The refresh is the delivery path** (`CLAUDE.md` § Current status: the
  push deploys). A dbt failure must fail the refresh *before* the snapshot is
  renamed into place, so a broken build never ships. `build_snapshot`'s
  temp-then-rename already gives you that.
- **Deploy dependency.** dbt is a pipeline dependency, like `scipy`. The
  hosted app never runs it (lesson 9, option A), but `requirements.txt` is one
  file that Streamlit Cloud installs. Put dbt in the pipeline section, or keep
  it out and install it only on the Mac. The second is lighter for the hosted app.
- **Docs.** `CLAUDE.md` § Analytics layer and § Repository files, plus
  `TODO.md` § dbt with DuckDB (move it to Current status when done).

## Exercise
Write the switch as a checklist in the order you would run it, each step with
how you would undo it. Then do the shadow phase for one full weekend refresh
before switching.

## Check
- One scheduled refresh runs with dbt in place.
- The snapshot opens in the hosted app.
- `git diff` on `data/parkrun_results.csv` and `parkrun_run_modes.csv` shows
  only the week's new rows.

<details><summary>Solution — a checklist</summary>

1. Shadow: `_finalize` runs `dbt_build('dev')` after `update_current_targets`,
   and logs parity counts. Undo: remove the call.
2. One weekend of clean parity.
3. Switch: marts aliased to `v_*` in schema `parkrun`; `build_snapshot` phase 2
   runs `dbt_build('snapshot')` instead of `ensure_views(snap)`. Undo: revert
   one commit; `ensure_views` still exists.
4. One weekend of clean refreshes, hosted app checked.
5. Remove: delete `ensure_views`'s view SQL. `ensure_legacy_views` stays.
   Update `CLAUDE.md`.
</details>

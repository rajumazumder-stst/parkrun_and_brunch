# 5 — Intermediate models, vars and macros

## Goal
- `int_results_moded`, the dbt twin of `v_results_moded`.
- The 91-day window as a dbt **var** with **no second definition**.
- A `form_window` **macro** for the window condition written out three times today.

## Concept
- **`var('name')`** reads a project variable, set in `dbt_project.yml` or on
  the command line with `--vars`. A var with **no default** makes dbt fail
  loudly if it is not passed. Here that is a feature.
- A **macro** is a Jinja function that returns SQL text. Use one when the same
  SQL fragment would otherwise be copied. Do not use one to hide simple SQL.

## In this project
- `TARGET_WINDOW_DAYS = 91` lives **once**, in `parkrun_core.py:50`.
  `CLAUDE.md` and four tests guard that single definition. Writing `91` in
  `dbt_project.yml` would make a second one. So leave it out of the project
  and pass it in from Python (lesson 9 wraps that). While learning, pass it by hand:
  `--vars '{target_window_days: 91}'`.
- The window condition `run_date BETWEEN d - 91 AND d - 1` appears in
  `v_head_to_head` (`:730`), `v_saturday_targets` (`:824`) and
  `update_current_targets` (`:1447`). That is `docs/DEV.md` § Open decisions 4.
  A macro is the cure.
- `v_results_moded` (`:681-689`) is a LEFT JOIN with
  `coalesce(m.is_buggy, FALSE)`. Read the comment at `:674-678` on **why** the
  coalesce exists. Carry the reason across as a SQL comment.

## Exercise
1. `models/intermediate/int_results_moded.sql`: `stg_results` LEFT JOIN
   `stg_run_modes`, adding `is_buggy`, `mode`, `mode_source`, `mode_reason`.
2. `macros/form_window.sql`: `form_window(date_col, anchor)` returns
   `<date_col> BETWEEN <anchor> - <window> AND <anchor> - 1`, reading the
   window from `var('target_window_days')`.
3. `dbt build --profiles-dir . --select int_results_moded --vars '{target_window_days: 91}'`

## Check
Parity against the existing view. Both lines must return 0:
```sql
SELECT count(*) FROM (SELECT * FROM parkrun.v_results_moded
                      EXCEPT SELECT * FROM dbt_parkrun.int_results_moded);
SELECT count(*) FROM (SELECT * FROM dbt_parkrun.int_results_moded
                      EXCEPT SELECT * FROM parkrun.v_results_moded);
```
`EXCEPT` compares by column **position**, so your column order must match the
view's (`results` columns first, in table order).

<details><summary>Hints</summary>

- `{{ ref('stg_results') }}`, never `source()`, outside staging.
- In a macro, `{{ var('target_window_days') }}` is rendered when the model
  compiles. Look at `target/compiled/…` to see the SQL dbt actually ran.
</details>

<details><summary>Solution</summary>

```sql
-- models/intermediate/int_results_moded.sql
-- An absent run_modes row means non-buggy. coalesce so a hole degrades to
-- the old behaviour instead of NULLing a mode and dropping a runner.
select
    r.*,
    coalesce(m.is_buggy, false)                                    as is_buggy,
    case when coalesce(m.is_buggy, false) then 'buggy' else 'nonbuggy' end as mode,
    m.source                                                       as mode_source,
    m.reason                                                       as mode_reason
from {{ ref('stg_results') }} r
left join {{ ref('stg_run_modes') }} m using (athlete_id, run_date, event_id)
```

```sql
-- macros/form_window.sql
{% macro form_window(date_col, anchor) -%}
    {{ date_col }} between {{ anchor }} - {{ var('target_window_days') }}
                       and {{ anchor }} - 1
{%- endmacro %}
```
</details>

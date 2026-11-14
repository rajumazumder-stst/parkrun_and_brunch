# 6 — Marts

## Goal
`fct_overlap`, `fct_saturday_targets` and `fct_head_to_head`, each identical
row for row to the view it replaces.

## Concept
Marts are what consumers read. In this project the consumer is the app,
which today reads `parkrun.v_*`. A mart should be **boring**: the hard logic
lives in well-named CTEs or intermediate models, and the mart puts them
together.

Model the **CTE chain** as you would by hand. Then ask which steps are
reusable enough to become their own intermediate model. For example, the
participant set `parts` could be one.

## In this project
Do them in increasing difficulty:
1. **`fct_overlap`** (`parkrun_pipeline.py:691-704`). The pipeline generates
   one `bool_or(athlete_id = <id>) AS has_<name>` per athlete from
   `ATHLETE_NAMES` in a Python loop. In dbt that loop is a **Jinja `for`**
   over a var holding the cohort. Again, pass it in; do not copy it.
2. **`fct_saturday_targets`** (`:801-830`). Uses `generate_series` for every
   Saturday and a grid of athlete × Saturday × mode. Use your `form_window`
   macro.
3. **`fct_head_to_head`** (`:708-786`): `parts → win → target → valid → h2h →
   ranked → placed → labelled`. The handicap bridge is in `target`. Read
   `CLAUDE.md` § Feature 2 first, especially why the bridge **divides**
   rather than multiplying by `(1 − h)`.

`BUGGY_HANDICAP_DEFAULT = 0.15` (`:112`) is another single definition. It is
the fallback inside `coalesce(bh.handicap, 0.15)`. Treat it like the window.

## Exercise
Write the three marts in `models/marts/`. Use `ref()` to your staging and
intermediate models and `form_window()` wherever the window appears.

## Check
For each mart, both directions of `EXCEPT` against the old view return 0 rows:
```sql
SELECT count(*) FROM (SELECT * FROM parkrun.v_head_to_head
                      EXCEPT SELECT * FROM dbt_parkrun.fct_head_to_head);
SELECT count(*) FROM (SELECT * FROM dbt_parkrun.fct_head_to_head
                      EXCEPT SELECT * FROM parkrun.v_head_to_head);
```
Then the counts: `v_head_to_head` has one row per ranked participant.

<details><summary>Hints</summary>

- `fct_overlap`'s loop: `{% for id, name in var('athletes').items() %} …
  {% if not loop.last %},{% endif %}{% endfor %}`, with
  `--vars '{athletes: {5672: raju, 5462426: duncan, 3087156: george}, …}'`.
  The order of the `has_*` columns follows the dict, so match the view's
  order for `EXCEPT` to pass.
- `classification` is `string_agg(athlete_name, ' vs ' ORDER BY athlete_name)`.
  Copy it exactly; changing the order is a separate decision (`docs/STYLE.md`,
  athlete order).
- If one `EXCEPT` direction fails, select the differing rows, not the count.
  It is usually a rounding or column-order slip.
</details>

<details><summary>Solution — fct_head_to_head (the target step)</summary>

```sql
-- models/marts/fct_head_to_head.sql (excerpt)
with parts as (
    select r.event_id, r.run_date, r.athlete_id,
           r.time_seconds as actual_seconds, r.is_buggy, r.mode
    from {{ ref('int_results_moded') }} r
    join (select event_id, run_date from {{ ref('stg_results') }}
          group by event_id, run_date having count(*) >= 2) o
      using (event_id, run_date)
),
win as (
    select p.event_id, p.run_date, p.athlete_id, p.actual_seconds, p.is_buggy, p.mode,
           median(h.time_seconds) filter (where h.mode =  p.mode) as own_target,
           count(h.time_seconds)  filter (where h.mode =  p.mode) as own_n,
           median(h.time_seconds) filter (where h.mode <> p.mode) as oth_target,
           count(h.time_seconds)  filter (where h.mode <> p.mode) as oth_n
    from parts p
    left join {{ ref('int_results_moded') }} h
      on h.athlete_id = p.athlete_id
     and {{ form_window('h.run_date', 'p.run_date') }}
    group by all
),
-- target / valid / h2h / ranked / placed / labelled: as parkrun_pipeline.py:739-781
...
```
The rest copies across almost verbatim. That is the point: dbt changes
**where the SQL lives and how it is checked**, not the SQL itself.
</details>

# 7 — Tests

## Goal
The rules in `CLAUDE.md` written as dbt tests that `dbt build` runs every time.

## Concept
dbt has three kinds of test:

| Kind | Where | Tests | Example |
|---|---|---|---|
| **Generic** (data test) | YAML under a column | the **built data** | `unique`, `not_null`, `accepted_values`, `relationships` |
| **Singular** (data test) | a `.sql` in `tests/` that returns the **bad rows** | the **built data** | "Venn regions sum to the total" |
| **Unit** | YAML `unit_tests:` with `given:` rows and `expect:` rows | the **logic**, on made-up inputs | "a tie gives 1-1-3" |

A data test passes when its query returns zero rows. A unit test runs your
model's SQL against the rows you give it, never touching real data. That
makes it the only kind that can test an edge case your data does not
contain yet.

## In this project
Rules waiting to become tests:

| Rule (`CLAUDE.md`) | Test |
|---|---|
| `run_modes.source` ∈ `user`/`model`/`rule` | `accepted_values` on the source column |
| results key `(athlete_id, run_date, event_id)` | `dbt_utils.unique_combination_of_columns`, or a singular test |
| every `results.event_id` resolves | `relationships` to `events.event_id` |
| **Decision 3's known issue**: one athlete, two rows on the same date with the same `run_number` = a re-keyed duplicate | singular test |
| Venn regions partition all occasions | singular test on `fct_overlap` |
| ties share a rank (1-1-3) | **unit test** on `fct_head_to_head` |
| demote rule: a 3-way with one target missing becomes a 2-way | **unit test** |
| the bridge divides | **unit test**: a regular run with only buggy history gets `buggy_median / 1.15` |

The pytest suite (`tests/`) stays. It tests Python and the app. dbt tests
cover the SQL, which pytest can only reach by querying the snapshot.

## Exercise
1. Generic: add `accepted_values` to `run_modes.source` in `sources.yml`, and
   `not_null` + `relationships` on `stg_results.event_id`.
2. Singular: `tests/assert_no_rekeyed_duplicates.sql`.
3. Unit: a tie test for `fct_head_to_head`. Three runners at one parkrun, two
   with identical `pct_diff`.

## Check
`dbt build --profiles-dir . --vars '…'` reports every test PASS. Then **break
one on purpose**: insert a fake `run_modes` row with `source = 'guess'` into the
dev DB, watch the test fail, and delete the row again.

<details><summary>Hints</summary>

- A unit test must give rows for **every** `ref()`/`source()` the model reads,
  even ones irrelevant to the case. Rows with only the columns used are enough.
- `fct_head_to_head` reads `int_results_moded` twice (`parts` and the
  history). One `given` input covers both.
- `athletes` and `events` are joined for names. Give them one row per id.
</details>

<details><summary>Solution — the singular test</summary>

```sql
-- tests/assert_no_rekeyed_duplicates.sql
-- Two rows for one (athlete, date) are legal (same-day doubles at different
-- parkruns). Two on one date with the same run_number are not: that is one
-- run stored under two event_ids. run_number is the EVENT's run number, so
-- it repeats across dates (119 times in the data) and must be paired with
-- the date.
select athlete_id, run_date, run_number, count(*) as n
from {{ ref('stg_results') }}
group by athlete_id, run_date, run_number
having count(*) > 1
```
</details>

<details><summary>Solution — a tie unit test (shape)</summary>

```yaml
unit_tests:
  - name: tie_shares_a_place
    model: fct_head_to_head
    given:
      - input: ref('int_results_moded')
        rows:   # history 7 days before, then the race day
          - {athlete_id: 1, event_id: 9, run_date: 2026-01-03, time_seconds: 1200, mode: nonbuggy, is_buggy: false}
          - {athlete_id: 2, event_id: 9, run_date: 2026-01-03, time_seconds: 1500, mode: nonbuggy, is_buggy: false}
          - {athlete_id: 3, event_id: 9, run_date: 2026-01-03, time_seconds: 1800, mode: nonbuggy, is_buggy: false}
          - {athlete_id: 1, event_id: 9, run_date: 2026-01-10, time_seconds: 1188, mode: nonbuggy, is_buggy: false}   # -1%
          - {athlete_id: 2, event_id: 9, run_date: 2026-01-10, time_seconds: 1485, mode: nonbuggy, is_buggy: false}   # -1%
          - {athlete_id: 3, event_id: 9, run_date: 2026-01-10, time_seconds: 1818, mode: nonbuggy, is_buggy: false}   # +1%
      - input: ref('stg_results')
        rows: [...]   # the same six keys
      - input: ref('stg_athletes')
        rows: [{athlete_id: 1, athlete_name: A}, {athlete_id: 2, athlete_name: B}, {athlete_id: 3, athlete_name: C}]
      - input: ref('stg_events')
        rows: [{event_id: 9, short_name: Test}]
      - input: ref('stg_buggy_handicap')
        rows: []
    expect:
      rows:   # only the race day forms a head-to-head with targets
        - {athlete_id: 1, run_date: 2026-01-10, place_rank: 1}
        - {athlete_id: 2, run_date: 2026-01-10, place_rank: 1}
        - {athlete_id: 3, run_date: 2026-01-10, place_rank: 3}
```
`expect` may list just the columns you care about.
</details>

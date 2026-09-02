# Parkrun Athlete Comparison Web App

## Objective

Build a web application that visualises and compares the parkrun histories of
three specific athletes. The first phase is a reliable data extraction and
loading pipeline; the visual analytics layer follows once the pipeline is stable.

This document is both the project brief **and** the agreed data-pipeline
specification. The spec sections reflect decisions made during design review —
where they differ from the original brief, **the spec wins**.

---

## Current status

- ✅ Extraction validated — `pd.read_html` works directly; the All Results table
  is the **last `<table>`** on each athlete page (server-rendered, no JS/API
  workaround needed). See `scrape_athlete()` in `parkrun_pipeline.py`.
- ✅ Global event list sourced and flattened (`data/parkrun_events.csv`).
- ✅ Country code → name lookup built (`data/country_lookup.csv`).
- ✅ Athlete lookup built (`data/athletes_lookup.csv`).
- ✅ Name → event_id resolution validated (see spec below).
- ✅ DuckDB loader / reconcile pipeline — built (`parkrun_pipeline.py`); bootstrap
  + refresh (Path A/B) tested against the live site. Data lives in the `parkrun`
  schema of `~/Documents/duckdb/my_database.duckdb`.
- ✅ Analytics layer — `v_overlap`, `v_head_to_head`, `v_saturday_targets` views
  and the `current_targets` table, built and wired into refresh (see Analytics
  layer below).
- ✅ Streamlit front end — `parkrun_app.py`, **5 tabs** (overlap/Venn · head-to-head
  summary · head-to-head detail · form target-time by Saturday · head-to-head
  map). Deployable: `parkrun_ui.py` resolves the DB via `PARKRUN_DB` env var > Streamlit
  secret > a bundled read-only snapshot (`data/parkrun_snapshot.duckdb`), so it
  can be hosted (e.g. Streamlit Community Cloud) from the repo alone.
  `requirements.txt` pins the runtime deps.
- ✅ Deployed to Streamlit Community Cloud —
  <https://parkrun-and-brunch.streamlit.app/> (serves the bundled read-only
  snapshot; auto-redeploys on push to the deployed branch).
- ✅ MotherDuck migration — `python parkrun_pipeline.py motherduck` (re)seeds a
  **parkrun-only** cloud DB (`md:parkrun_snapshot`; catalog name ≠ the `parkrun`
  schema so `parkrun.v_overlap` stays unambiguous). Was the runtime source of
  truth 18 Jul – 23 Aug 2026; **retired but kept documented** as the fallback
  if a non-laptop refresh host ever appears (`docs/DEPLOY.md`).
- ✅ Scheduled refresh — **launchd on the Mac** (Sat 14:30 + Sun 11:00 local,
  plus a missed-weekend login prompt), proven end-to-end on 19 Jul 2026.
  GitHub Actions was tried first and abandoned: parkrun's WAF 405-blocks
  GitHub-hosted runner IPs (the workflow was deleted 19 Jul 2026; history and
  rationale in `docs/DEPLOY.md`).
- ✅ **Local DuckDB is the source of truth** (23 Aug 2026). The scheduled
  refresh targets `~/.config/parkrun/parkrun_local.duckdb` — created on first
  run by `python parkrun_pipeline.py seed` from the committed snapshot (not a
  file copy: the snapshot has no primary keys, so a proper `ensure_schema()` DB
  is filled row-for-row, preserving `current_targets` history). The audit-file
  push is the delivery step, so it is **fatal on failure** and the freshness
  stamp is written only after it succeeds. The hosted app's `PARKRUN_DB` /
  `motherduck_token` secrets are removed — it serves the bundled snapshot
  again (`docs/DEPLOY.md` § History).
- ✅ **Buggy mode — promoted and live** (2 Sep 2026). George and Duncan
  sometimes run pushing a buggy, which parkrun records nothing about, so their
  times were being pooled into a single form target that flattered the buggy
  runs and penalised the rest. Shipped: the `run_modes` label store,
  `course_difficulty` and `buggy_handicap` tables, the `current_targets` primary
  key migration, the mode-aware views (`v_results_moded` + per-mode targets and
  the symmetric handicap bridge), the review-sheet export/import tooling, and
  the whole UI surface. The estimator that would label future runs automatically
  is still deliberately unwritten — it is supervised, and 36 buggy labels across
  two athletes is not yet a training set (Duncan's 5 are below the 8-per-class
  gate).
- ✅ **Buggy handicap hosted at `/buggy-handicap`** (2 Sep 2026) — the working
  behind each athlete's handicap, on the main app's own domain, so it is
  shareable rather than a screenshot of `localhost:8502`. `app.py` is now a
  router: `st.navigation([...], position="hidden")` over `parkrun_app.py` (the
  five tabs, at `/`) and `handicap_page.py`. Hidden navigation is the point —
  a `pages/` directory gives the same URLs but forces a nav list into the
  sidebar, putting a statistical argument about two named people in front of
  every visitor who came for parkrun results. The page is **unlisted, not
  access-controlled**. The analysis lives in `buggy_handicap.py`, imported by
  both it and `label_impact.py`. The head-to-head method comparison is
  deliberately **not** hosted: it needs `v_head_to_head_legacy`, which the
  deploy snapshot never carries.
- 🧪 Local dev/test workflow: work on the `dev` branch, `./scripts/run_local.sh` serves
  the app against an isolated `data/parkrun_dev.duckdb` (built through
  `pipeline seed`, gitignored) so previews never touch `main` or the deploy
  snapshot. `PARKRUN_LABEL_AUDIT=1` additionally starts `label_impact.py` on a
  second port. See `docs/DEV.md`.
- 📕 **Zero-label equivalence — spent** (2 Sep 2026), as designed. While
  `run_modes` was empty the mode-aware views reproduced the old ones **row for
  row** (verified: `v_head_to_head` 433 = 433, `v_saturday_targets` 1185 = 1185,
  zero rows either way), and that survived a full `default` backfill since those
  rows are all `FALSE`. The first confirmed **buggy** labels ended it: 175 of
  205 occasions unchanged, 6 winners flipped, 3 with places reordered, 0 lost.
  The check cannot be re-run against live data — it is kept documented because
  it is the reason the promotion was safe to make in stages, and
  `label_impact.py` still renders the comparison it was built on.
- ✅ **Labels imported; handicaps set** (2 Sep 2026). George and Duncan returned
  the review sheet fully answered — 160 `manual` labels (George 31 buggy / 44
  not, Duncan 5 / 80), the remaining 681 runs `default`. Measured from the runs
  between each athlete's first and last buggy run: **George 0.13**
  (`measured`, 31 labels — raw +12.0%, course fixed effects +15.3%, +12.7% once
  form drift is allowed for; 0.13 is inside all three intervals), **Duncan held
  at the 0.15 `default`** — five buggy runs, an interval crossing zero, and his
  one course run both ways pointing the other way (−3.7%), which is the course
  confound rather than the buggy. Zero-label equivalence is now **spent**: 175
  of 205 occasions unchanged, 6 winners flipped, 0 lost.

---

## Athletes

Tracked parkrun athlete IDs: **5672, 5462426, 3087156**.

Source URL: `https://www.parkrun.org.uk/parkrunner/{athlete_id}/all/`

Athlete metadata (name, DOB) is hand-maintained in `data/athletes_lookup.csv`.

---

## Data sources

| Source | URL | Used for |
|---|---|---|
| Athlete results | `parkrun.org.uk/parkrunner/{id}/all/` | The All Results table per athlete |
| Global events | `https://images.parkrun.com/events.json` | Event catalogue (id, name, location, coords, country) |

`events.json` is GeoJSON: `events.features[]` (one per event) plus a `countries`
block. **The `countries` block contains only `url` + `bounds` — no country
name** (verified), which is why a hand-maintained `country_lookup` exists.

---

## Design decisions (agreed in review)

1. **DuckDB is the runtime source of truth.** CSVs in `data/` are tracked
   seeds/snapshots; raw `events.json` is a transient download (never tracked).
2. **Results store `event_id`, not the event name.** Names are resolved to IDs
   at load time.
3. **Name → event_id is a join on `EventShortName`, filtered to `seriesid = 1`.**
   Validated: across all 2,364 seriesid=1 events the short name is globally
   unique, and all 197 distinct scraped names resolve to exactly one ID. The
   `seriesid = 1` filter is required so a name can't match an adult + junior
   event. The join **must never filter on `live`** — defunct events still need
   to resolve historical results.

   **Known issue — not fixed, deliberately deferred (1 Sep 2026).** That
   uniqueness is *validated but not enforced*. `resolve_event_ids` builds the
   name → id map with last-write-wins over an **unordered** `SELECT`, so if two
   `seriesid = 1` events ever share a short name (a new event taking a defunct
   one's name is the realistic route — the join never filters on `live`), which
   id wins can flip between refreshes with no data change at all. It logs
   `WARN: duplicate short_name … ambiguous match` and resolves anyway.

   Because every refresh re-resolves **all** historical names from scratch and
   the UPSERT keys on `(athlete_id, run_date, event_id)`, a flipped id does not
   move the existing row — it **inserts a second row for the same physical
   run**, and nothing deletes the first. One run then counts twice in `results`,
   `v_overlap` and potentially both sides of a head-to-head. Curation changes
   (editing `short_name` in `parkrun_events.csv`, the Victoria Dock manual row,
   or any future name-matching work) can trigger the same thing deliberately.

   Fixes when it is worth doing: (a) treat `dupes` in `resolve_event_ids` as
   fatal, or route those names to the unmatched report — never resolve a name
   that maps to more than one id; (b) `ORDER BY event_id` so the map is at least
   deterministic; (c) a refresh-time check for one athlete holding two rows with
   the same `run_number`, which is unambiguous evidence of a re-keyed duplicate
   (two rows for one `(athlete_id, run_date)` is *legal* — decision 7 allows
   same-day doubles at different events — so `run_number` is the tell).
4. **Country is stored as `country_code` (FK) on `events` only**, not on results.
   Names come from `country_lookup`.
5. **Soft delete, never hard delete.** Events dropping out of `events.json` are
   flagged `live = FALSE`, not removed.
6. **Events reconcile and results scrape are independent paths.** A failed
   events fetch must **not** block the results scrape.
7. **Full scrape + UPSERT, no incremental-by-date.** The `/all/` page returns the
   entire history in one request, so date-windowing saves nothing and risks
   missing same-day doubles and retroactive corrections.
8. **Victoria Dock parkrun (a former event, not in `events.json`)** is carried as
   a manual row: `event_id = 1868`, `source = 'manual'`, `live = FALSE`. Its ID
   is genuine (no clash risk). `source = 'manual'` protects curated rows during
   reconcile.

---

## Data model (DuckDB)

### `events`
Seeded from `data/parkrun_events.csv`; reconciled against live `events.json`.

| Column | Notes |
|---|---|
| event_id | PK |
| eventname | URL slug (e.g. `bushy`) |
| short_name | Display/join key (e.g. `Bushy Park`) |
| long_name | e.g. `Bushy parkrun` |
| location | Free text |
| country_code | FK → `country_lookup` |
| country_url | e.g. `www.parkrun.org.uk` |
| longitude, latitude | Coordinates |
| seriesid | 1 = main (Saturday 5k), 2 = junior |
| source | `events_json` or `manual` |
| live | Loader-managed: in latest good `events.json`? |
| first_seen | Loader-managed: date first stored |
| last_seen_live | Loader-managed: last reconcile it appeared live; NULL if never seen (e.g. manual rows) |

### `results`
Natural key: **`(athlete_id, run_date, event_id)`** — `event_id` (not name) and
allows >1 result per athlete per day at different events.

| Column | Notes |
|---|---|
| athlete_id | |
| event_id | Resolved from scraped name (see decision 3) |
| run_date | Event date |
| run_number | |
| position | |
| time | Raw scraped text (`MM:SS` or `H:MM:SS`) — kept for fidelity |
| time_seconds | INTEGER — `time` parsed to elapsed seconds (for sorting / averages / pace) |
| age_grade | |
| pb_flag | |
| scrape_timestamp | |

Note: all INSERT/COPY statements use **explicit column lists**, so physical
column order is irrelevant (a migrated DB may have `time_seconds` last while a
fresh bootstrap places it after `time`).

### `country_lookup`
Hand-maintained (`data/country_lookup.csv`). 21 rows.

| Column | Notes |
|---|---|
| country_code | PK |
| country_url | |
| country_name | Maintained by hand (not in `events.json`) |

### `athletes`
Hand-maintained (`data/athletes_lookup.csv`).

| Column |
|---|
| athlete_id (PK) |
| athlete_full_name |
| athlete_name |
| date_of_birth |

### `current_targets`
Materialised by the refresh: each athlete's current-form target **per mode** as
of the refresh date (a snapshot — recomputing live would silently drift). Keyed
on `(refresh_date, athlete_id, mode)` so form history accumulates over refreshes.

| Column | Notes |
|---|---|
| refresh_date | PK part; the date the snapshot was taken |
| athlete_id | PK part |
| mode | PK part; `buggy` or `nonbuggy` |
| target_seconds | 91-day median of same-mode `time_seconds` over `[refresh_date−91, refresh_date−1]`; NULL if no runs in window |
| n_window | Runs found in the window (target valid when ≥ 1) |

Only athletes who actually use a buggy get a `buggy` row — Raju never does, so
storing a permanently empty row for him would be noise in a table that grows
every refresh. George and Duncan **do** get an empty buggy row when they have no
buggy runs in the window: that is a real "none in the last 91 days", not an
impossibility. The app filters on `n_window >= 1`.

Historic rows (pre-mode) were migrated as `mode = 'nonbuggy'` and **not
recomputed** — they record what the targets *were* on those dates, which is the
entire reason this is a table and not a view.

### `run_modes`
The per-run buggy label. One row per run once backfilled; **an absent row means
non-buggy** and `is_buggy` is `NOT NULL` — there is no third state.

| Column | Notes |
|---|---|
| athlete_id, run_date, event_id | PK — same natural key as `results` |
| is_buggy | NOT NULL |
| source | `manual` \| `estimated` \| `default` — **not interchangeable**, see below |
| confidence | `max(p, 1−p)`; NULL for `manual`/`default` |
| reason | Free text |
| set_at | When the label was written |

| `source` | Written by | Trains the model? |
|---|---|---|
| `manual` | Spreadsheet import, or a hand SQL correction | **yes** |
| `estimated` | The model, on new runs | **yes** |
| `default` | Backfill of unreviewed runs; Raju's rows | **no** — an assumption, not an observation |

Exported to `data/parkrun_run_modes.csv` on every refresh, so hand-entered
labels have a diffable git history rather than living only inside a binary DB.

### `course_difficulty`
External per-course difficulty score, used as a covariate by the buggy
estimator. Deliberately **its own table, not a column on `events`** —
`reconcile_events` inserts into `events` *positionally* inside a try/except that
only logs, so a 15th column would become a silent weekly "reconcile rolled back"
and new parkruns would stop appearing.

| Column | Notes |
|---|---|
| event_id | PK |
| parkrun_name | Name as published by the source |
| difficulty | 0.8 – 11.6 on a 0–12 scale; 12 = hardest |
| speed_rank | 1 = fastest; reference only |
| source, fetched_at | |

### `buggy_handicap`
Each athlete's multiplicative cost of pushing a buggy — the bridge used by
`v_head_to_head` when one mode has no runs in the window.

| Column | Notes |
|---|---|
| athlete_id | PK |
| handicap | `0.15` = a buggy costs 15% |
| n_buggy_labels | Labels the measurement was based on |
| method | `default` (the placeholder) \| `measured` |
| computed_at | |

Seeded with one row per athlete at the placeholder, `INSERT OR IGNORE` so a
measured value is never overwritten. The row **must** exist: a missing one would
NULL a head-to-head target and silently drop that runner from the contest.

---

## Analytics layer

The comparison features are **derived from `results`**. They are deterministic
from the stored data, so they are DuckDB **views** (`v_results_moded`,
`v_overlap`, `v_head_to_head`, `v_saturday_targets`) — always live, no
duplication, no staleness; only the date-anchored `current_targets` is
materialised.

`v_results_moded` is the base view the mode-aware queries read: `results` plus
`coalesce(is_buggy, FALSE)` and a `mode` of `buggy`/`nonbuggy`. It is created
**first** in `ensure_views()` — DuckDB resolves a view body at creation time, so
anything referencing it must come after. The `coalesce` is deliberate: a missing
label degrades to the old non-buggy behaviour rather than NULLing a mode.
Created/refreshed by `ensure_views()` and `update_current_targets()`. The cohort
is fixed (3 athletes); `ATHLETE_NAMES` in the pipeline is the single source for
the per-athlete column names. The **cumulative 1st-place trend** (Tab 2) and the
**head-to-head map** (Tab 5) are derived in `parkrun_app.py` from `v_head_to_head`
(+ `events` coordinates for the map) — no extra views.

### Feature 1 — participation overlap (`v_overlap`)
A **shared occasion** is a unique `(event_id, run_date)` (same event, same day =
physically together). The view is occasion-level with `has_<name>` boolean flags
+ `n_athletes`. From it the app derives:
- **Venn** — the 7 **exclusive** regions (A-only, …, A&B-not-C, …, all three).
  Regions partition all occasions and sum to the total.
- **Per-athlete breakdown** — for each athlete: solo / +one other / +both
  ("alone" = relative to the three tracked athletes, not "only runner present").

### Feature 2 — head-to-head (`v_head_to_head`)
A **head-to-head** is an occasion where ≥ 2 of the cohort ran. Placing is
**form-adjusted**, not actual finish order (the three differ hugely in pace):

1. **Target** per participant = **median** `time_seconds` over their runs in
   `[date−91, date−1]` **of the same mode** (buggy / non-buggy — see
   `run_modes`), min **1** run. Window excludes the event day.
2. **Handicap bridge** — when that mode has **no** runs in the window, the
   target is bridged from the *other* mode using the athlete's `buggy_handicap`
   `h`: a buggy run gets `× (1 + h)`, a non-buggy run `÷ (1 + h)`. Division, not
   `× (1 − h)`: only division makes the two directions compose back to the
   starting value. `target_basis` records which of the four cases applied —
   `buggy`, `nonbuggy`, `nonbuggy+handicap`, `buggy-handicap`.
3. **`pct_diff` = round((actual − target) / target × 100, 2)** — faster than form
   is negative. **Unchanged** by the mode split.
4. **Placing** = `rank()` over `pct_diff` **ascending** (most-beat-your-form = 1st);
   **standard competition ranking** (ties share a rank, e.g. 1-1-3). **Unchanged.**
5. **Demote rule** — only participants with a valid target are ranked. Need ≥ 2
   rankable for a contest; a 3-way where one lacks a target becomes a 2-way
   (and `classification` reflects the ranked set). **Unchanged**, but it now
   bites far less often — see below.

The change is scoped to **the target only**. Steps 3–5 are the same arithmetic
they always were.

Each row carries `classification` (e.g. `George vs Raju`, `Duncan vs George vs
Raju`), `n_ranked`, `actual_seconds`, `target_seconds`, `n_window`, `pct_diff`,
`place_rank`, `is_buggy`, `target_basis`. The app shows a per-head-to-head table
+ a 1sts/2nds/3rds leaderboard.

**The bridge makes *more* contests rankable, not fewer.** Previously a
participant with no same-mode history had `n_window = 0` and was demoted; now
they get a bridged target instead. Only a participant with **no runs at all** in
the window is still demoted.

**Labelling a past run retroactively changes the record.** A new `run_modes` row
changes that run's target, therefore its `pct_diff`, possibly its `place_rank`,
and therefore the head-to-head leaderboard and the cumulative-1sts curve — back
to 2023. This is intended (the old numbers pooled buggy and non-buggy runs into
one target, which flattered buggy runs and penalised non-buggy ones), but it
means head-to-head results are **not** immutable. The tab-2 explainer says so.

Note: `v_overlap` counts **all** co-participations; `v_head_to_head` counts only
**rankable** ones — so the two totals can differ (occasions where a participant
has no runs at all in the window). Bridged targets narrow that gap without
closing it.

Caveat (accepted): the target averages across all courses in the window, but a
head-to-head is at one specific course — **course difficulty is still not
adjusted for here.** `course_difficulty` exists, but it is a covariate for the
buggy *estimator*, not an adjustment to the head-to-head target. Do not read the
table's presence as a fix for this.

### Feature 3 — Saturday form targets (`v_saturday_targets`)
Each athlete's current-form **target per mode** evaluated on **every Saturday**
in the data span, using the **same 91-day median** as the head-to-head target:
`median(time_seconds)` over `[Saturday−91, Saturday−1]` (excludes the day),
valid when ≥ 1 run in the window. Saturdays with no runs in the window are
omitted (the Form-tab line breaks there, never drops to zero). Columns:
`athlete_id`, `athlete_name`, `run_date` (the Saturday), `mode`,
`target_seconds`, `n_window`.

That `n_window >= 1` filter is also what stops an athlete who has never pushed a
buggy getting a phantom buggy line — no special-casing by athlete is needed.

**The handicap bridge is deliberately NOT applied here** (nor in
`current_targets`). Bridging every Saturday would draw an imaginary buggy form
line back to 2017, for a buggy that did not exist. A bridge is a device for
judging one specific contest fairly, not a claim about historical form. So this
view equals `v_head_to_head.target_seconds` on shared athlete/date pairs **only
where the head-to-head used a same-mode target** (`target_basis` of `buggy` or
`nonbuggy`); on a bridged row the two legitimately differ.

---

## Refresh pipeline spec

Triggered on a schedule (**Sat 14:30** and **Sun 11:00** Mac-local, via the
launchd agents, plus a missed-weekend login prompt) **and** ad-hoc
(`scripts/parkrun_refresh.sh`, or the pipeline CLI directly) — every trigger
runs the identical process. The two paths are independent.

### Path A — events reconcile (Transaction A)

1. Download live `events.json`.
2. **Corruption gate** — abort the reconcile (keep existing data, log a visible
   warning) unless ALL hold:
   - HTTP 200;
   - valid JSON with `countries` + non-empty `events.features`;
   - sampled feature has `id` + `properties.EventShortName`;
   - feature count ≥ **95%** of the stored event count (guards truncated
     downloads). *Skipped on first run — no prior count exists.*
   - A failed gate **does not block Path B.**
3. If the gate passes, in one transaction:
   - Insert new events (`live=TRUE`, `first_seen=today`, `last_seen_live=today`).
   - Update changed fields on existing events.
   - Matched events → `live=TRUE`, `last_seen_live=today`.
   - Missing events → `live=FALSE`. **Never touch `source='manual'` rows or
     overwrite curated fields** (only flip flags).
   - Unknown `country_code` → **warn only**, resolve name to `Unknown`; never
     block the load. Add the missing row to `country_lookup.csv` by hand later.

Notes: `live` may legitimately flap (event vanishes one week, returns the next) —
accepted, no hysteresis. `last_seen_live` means "last reconcile seen," not "last
Saturday" (a skipped gate doesn't advance it).

### Path B — results upsert (Transaction B, all 3 athletes together)

1. For each athlete, scrape `/all/`; parse the last `<table>`.
2. Resolve each scraped event name → `event_id` via `short_name` join
   (`seriesid = 1`). Unmatched names are flagged in an **unmatched-names report**
   (today only Victoria Dock, handled by its manual row).
3. UPSERT all rows on `(athlete_id, run_date, event_id)` — insert new, update
   changed (catches retroactive time/age-grade/PB corrections).
4. Wrap all three athletes in **one** transaction; if any athlete's page fails,
   roll back all three and retry (results stay internally consistent).

After Path B, the refresh runs `update_current_targets()` (snapshots today's
current-form targets), exports the results snapshot CSV and rebuilds
`data/parkrun_snapshot.duckdb`; `scripts/parkrun_refresh.sh` then commits and
pushes both — that push is what deploys the new data. The analytics views
are (re)created on every connection via `ensure_views()`.

### Bootstrap (empty DB)

1. Create schema (tables + analytics views).
2. Seed `events` from `data/parkrun_events.csv` (includes the Victoria Dock
   manual row); init `live`/`first_seen`/`last_seen_live` (manual row →
   `live=FALSE`, `last_seen_live=NULL`).
3. Seed `country_lookup` and `athletes` from their CSVs.
4. Full results scrape for all 3 athletes (no incremental shortcut).
5. Snapshot `current_targets`.
6. Corruption gate skips the 95% check on this first run.

---

## Version control

| Tracked (git) | Ignored |
|---|---|
| `data/parkrun_events.csv` (incl. Victoria Dock) | `*.duckdb` (binary source of truth) |
| `data/parkrun_results.csv` (versioned snapshots) | `data/events.json` (transient download) |
| `data/parkrun_run_modes.csv` (label audit trail) | |
| `data/course_difficulty.csv` (cached scores + aliases) | |
| `data/country_lookup.csv` | |
| `data/athletes_lookup.csv` | |
| `data/parkrun_snapshot.duckdb` (read-only, deploy snapshot) | |
| `requirements.txt` | |
| Python scripts | |

`parkrun_results.csv` is tracked deliberately: parkrun only serves *current*
results, so re-scraping cannot reproduce a past state — the committed snapshots
are the only historical record, and diffs form a per-refresh audit trail.

`data/parkrun_snapshot.duckdb` is a tracked **exception** to the `*.duckdb`
ignore (`!data/parkrun_snapshot.duckdb` in `.gitignore`). It is what a hosted
app serves, so it must be **parkrun-only** — built from scratch (parkrun tables
+ views copied across) so it can never carry the `personal_finance` schema that
lives in the dev DB `~/Documents/duckdb/my_database.duckdb`. Its catalog name
must differ from the `parkrun` schema (hence `parkrun_snapshot`, not
`parkrun.duckdb`) or `parkrun.v_overlap` becomes an ambiguous reference.
`bootstrap` and `refresh` rebuild it automatically (`build_snapshot()`);
`python parkrun_pipeline.py snapshot` rebuilds just that file. Commit + push the
regenerated snapshot to redeploy (Streamlit Cloud auto-redeploys on push).

---

## Repository files

| Path | Purpose |
|---|---|
| `parkrun_pipeline.py` | Loader: `bootstrap` / `refresh` / `status` / `snapshot` / `seed` / `motherduck` (Path A/B, DuckDB) + analytics views/targets + deploy-snapshot build + parkrun-only MotherDuck upload (`build_motherduck`). Also owns scraping (`scrape_athlete`) and time parsing (`time_to_seconds`). |
| `app.py` | **Entrypoint and router only.** `st.set_page_config` (one call is legal per run) + `st.navigation([...], position="hidden")` mapping `/` → `parkrun_app.py` and `/buggy-handicap` → `handicap_page.py`. Hidden, not a `pages/` directory, so the analysis has a URL but no nav link |
| `parkrun_app.py` | Streamlit front end (5 tabs: overlap · personal bests + head-to-head summary · head-to-head detail · form/target-time · head-to-head map) reading the `parkrun` schema read-only; DB path resolved via `PARKRUN_DB` env/secret (incl. `md:` MotherDuck), else the bundled snapshot. Auto-reloads on new data via a `data_version()` cache key — `max(scrape_timestamp)` **plus `max(set_at)` and `count(*)` from `run_modes`**, since labels are edited out of band and never move the scrape timestamp — 60s TTL; 🔄 Reload button clears the cache manually. A page script: no `set_page_config` of its own |
| `buggy_handicap.py` | The handicap measurement, imported by **both** `label_impact.py` and `handicap_page.py`: per athlete, the runs between their first and last buggy run, split by mode — mean/SD/median, density curves with a rug of the real runs, and three estimates (raw difference in means, course fixed effects, the same plus a form-drift term). Recommends a value only when the estimates agree in sign, the raw interval clears zero, and there are ≥ 8 buggy runs. **One implementation only** — the `_winning_margin` rule applies: a second copy would make a method difference indistinguishable from a rounding one. Needs `scipy` |
| `handicap_page.py` | Page script at `/buggy-handicap` — a thin wrapper over `render_handicap`. Unlisted by design; the audience is the two people it is about, reached by a link they are sent. The head-to-head comparison is **not** hosted beside it — that needs `v_head_to_head_legacy`, absent from the deploy snapshot by design |
| `parkrun_ui.py` | Shared UI layer imported by **both** apps: DB resolution, `ATHLETE_COLORS`/`MEDAL`, `fmt_time`, the buggy display helpers (`BUGGY_GLYPH`, `mode_suffix`, `mode_text`, highlight colours), `_h2h_headline`, `_victory_fig` and `_winning_margin`. `_winning_margin` must exist **once only** — `label_impact.py` diffs old against new, so a second copy of that arithmetic would make a method difference indistinguishable from a rounding one |
| `label_impact.py` | **Dev-only, separate app** (its own port), **2 tabs**: *head-to-head impact* — the pre-buggy method against the current one, per-occasion verdicts (filterable to what changed and/or what used the handicap bridge) and paired victory charts; *buggy handicap* — `render_handicap` from `buggy_handicap.py`, the same code the hosted page runs. Not part of the hosted site — keeping it in its own file means there is no deploy-time gate to get wrong. Tab 1 needs `v_head_to_head_legacy`, which only ever exists on a dev DB; the gate lives **inside** that tab and returns rather than calling `st.stop()`, which would take the other tab down with it. Its statistics come from `buggy_handicap.py`, so `scipy` is now a hosted dependency rather than a dev-only one |
| `scripts/run_local.sh` | Local dev launcher: venv + isolated `data/parkrun_dev.duckdb` (built via `pipeline seed`, **not** `cp` — the committed snapshot carries only the views it had when last rebuilt) + `streamlit run`. Under `PARKRUN_LABEL_AUDIT=1` it also builds the legacy views and starts `label_impact.py` on a second port (see `docs/DEV.md`) |
| `scripts/dev_fake_labels.py` | Dev-only: plausible fake buggy labels for previewing the UI before the real ones arrive. Labels runs that were slow *relative to that athlete's trailing 20-run median*. Refuses to write to the source of truth or the deploy snapshot |
| `scripts/parkrun_refresh.sh` | Master refresh from this Mac (manual or scheduled — the one code path): pull clone → seed the local source-of-truth DB if absent → pipeline → audit-file push (fatal; this is the deploy) → freshness stamp → notification |
| `scripts/parkrun_autorefresh.sh` | Scheduling policy calling the master (launchd agents run self-syncing deployed copies at `~/.config/parkrun/`, Sat 14:30 + Sun 11:00 + missed-weekend login prompt — see `docs/DEPLOY.md` § Scheduled refresh) |
| `scripts/sync_working_copy.sh` | `sync_working_copy()` — sourced by `parkrun_refresh.sh` (after the freshness stamp) and by `run_local.sh` (`--fetch-only`). Always fetches the `~/Documents` working copy; fast-forwards it only when the tree is clean **and** the branch is `main`. Every path returns 0 — it can never fail a refresh. No-op under launchd (TCC blocks `~/Documents`) |
| `scripts/fetch_course_difficulty.py` | One-off fetch of the published UK course-difficulty scores to `data/course_difficulty.csv`. Run by hand; the refresh applies the cached CSV and never touches the network for it |
| `scripts/export_buggy_review.py` | The buggy review sheet: **export** (one tab per athlete, evidence only — no estimate), **`--import`** a returned workbook, **`--backfill`** every unreviewed run as `default`. Dry-run by default, writes only with `--apply`, never overwrites an existing label, refuses to write to the deploy snapshot. Needs `openpyxl`, deliberately not in `requirements.txt` |
| `scripts/build_logo.py` | Builds the app logo — two variants, `ACTIVE` (currently `toast`) is the one rasterised into `static/`. Lettering is converted from DejaVu Sans Bold to SVG paths at build time, so the committed SVG needs no font installed (DejaVu, not a system font like Arial, because its licence permits redistributing outlines). Build-time only; needs `cairosvg` + `fontTools` + `matplotlib`, deliberately **not** in `requirements.txt` |
| `assets/logo-toast.svg` | Vector source, **active** logo: `PR&B` on a slice of toast, letters in the three athletes' colours (generated — edit `build_logo.py`, not this) |
| `assets/logo-runners.svg` | Vector source, alternative logo: three runners in `ATHLETE_COLORS` on a fried egg (generated) |
| `static/logo-512.png` | `page_icon` source: the browser-tab favicon |
| `static/apple-touch-icon.png` | 180×180 for the iOS "Add to Home Screen" icon, served at `/app/static/` |
| `.streamlit/config.toml` | `enableStaticServing = true` so `static/` is reachable at `/app/static/` |
| `docs/DEV.md` | Local dev workflow (incl. `PARKRUN_LABEL_AUDIT=1` for the label-impact app and the fake-label script) |
| `docs/DATA.md` | The buggy labels: what each `source` means, how the training set grows, how to correct a label by hand |
| `docs/DEPLOY.md` | Deploy/ops: local source-of-truth DB + snapshot delivery, scheduled refresh, rebuilding/seeding, retired MotherDuck path (secret flip, tokens, re-seed) |
| `requirements.txt` | Pinned runtime deps for hosting (Streamlit Cloud etc.) |
| `data/parkrun_events.csv` | Event catalogue (events.json dump + Victoria Dock) |
| `data/country_lookup.csv` | country_code → country_name |
| `data/athletes_lookup.csv` | Athlete names + DOB |
| `data/parkrun_results.csv` | Results snapshot exported by the pipeline (keyed on event_id) |
| `data/parkrun_run_modes.csv` | Buggy labels exported by the pipeline — the audit trail for hand-entered labels |
| `data/course_difficulty.csv` | Cached course-difficulty scores + hand-maintained `alias_of` column |
| `data/parkrun_snapshot.duckdb` | Read-only, parkrun-only DuckDB the deployed app serves |
| `adhoc/` | One-off investigations using the parkrun data but **outside the app** — see `adhoc/README.md` |

**`adhoc/` is not part of the app.** Nothing there is imported by `parkrun_app.py` or
`parkrun_pipeline.py`, nothing runs on the scheduled refresh, and its extra
dependencies stay in per-topic `requirements.txt` files rather than the root one
— the app must remain deployable if the whole folder is deleted. Each topic
tracks its README, changelog, scripts and small `results/`; generated artefacts
(`output/`) and cached API responses (`.cache/`) are gitignored.

Run the pipeline: `python parkrun_pipeline.py refresh` (auto-bootstraps an empty DB).
Run the app locally against the full dev DB: `PARKRUN_DB=~/Documents/duckdb/my_database.duckdb streamlit run app.py`.
Run the app against the bundled snapshot (as hosted): `streamlit run app.py`.

---

## Technology stack

Python · requests · pandas · BeautifulSoup4 · lxml · DuckDB. Front end:
Streamlit · plotly · matplotlib-venn · folium/streamlit-folium (map).

### Environment

- Python 3.14 venv: `~/Documents/Python scripts/env` (has requests, pandas,
  bs4, lxml, html5lib, duckdb 1.5.4; front end: streamlit, plotly, matplotlib,
  matplotlib-venn, folium, streamlit-folium).
- DuckDB database: `~/Documents/duckdb/my_database.duckdb`.

---

## Visualisations

**Built (local Streamlit, `parkrun_app.py`, 5 tabs)** — plus `label_impact.py`, a
**separate dev-only app** on its own port (`docs/DEV.md`):
- **Tab 1** participation overlap / Venn (`v_overlap`) + per-athlete company.
- **Tab 2** form-adjusted head-to-head summary (`v_head_to_head`,
  `current_targets`): **personal bests** (top of the tab — see below), the
  head-to-head explainer, current-form targets (see **Buggy mode in the UI**),
  latest head-to-head, record leaderboard (3rd place shown only for the 3-way /
  All, each placing annotated with how many were run with a buggy), and a
  **cumulative 1st-place finishes** trend (requires a head-to-head; year/season
  filterable; hover names the winning parkrun).
- **Tab 3** head-to-head detail (drill into a single contest): a scoreline
  one-liner (winner, % vs form, winning margin, note on any 3rd-placed
  finisher — all 2 dp), a **victory lollipop chart** (raw `pct_diff` per
  athlete from the on-form baseline, x-axis reversed so faster-than-form
  points right, 1st–2nd winning margin bracketed, winner on top), then the
  results table.
- **Tab 4** **form — target time by Saturday** (`v_saturday_targets`): one line
  per **(athlete, mode)** — solid regular, dotted with a buggy — mm:ss axis,
  year/season filter, line breaks across >91-day gaps, axes rescale when an
  athlete is hidden via the legend (one `legendgroup` per athlete, so a click
  hides both their lines).
- **Tab 5** **map — where the head-to-heads happen** (Folium + OpenStreetMap):
  one pie marker per venue, sized by count and split by wins per athlete; shown
  once a head-to-head classification is selected. Tooltips count buggy wins
  **per athlete** (`George 2 (1 🛒)`) — a trailing total would be ambiguous
  about whose wins it counted.

**Personal bests** (Tab 2, `load_personal_bests()` + `render_personal_bests()`
in `parkrun_app.py` — no view; the SQL lives in the loader). Each athlete's **fastest**
run in three scopes — **All time**, **Last 12 months**, **Last 3 months** — with
the parkrun and the date. Both rolling windows are calendar intervals anchored
on `max(refresh_date)` and **inclusive of the anchor day**, so a run earlier
today counts; ties on time break to the earliest date. Note the 3-month window
is deliberately *not* the head-to-head's 91-day form window — these answer
different questions (best single run vs. baseline for a contest).

Layout: one bordered box per athlete (ordered by all-time best), the three
scopes side by side inside it. Every line is fixed-height — the venue block is
pinned to `PB_VENUE_LINES` (2) lines and clamps a longer name with an ellipsis,
keeping the full name in the `title` tooltip — so times, venues and dates sit on
the same levels across all three boxes and the boxes match in height. Scope
label and venue/date share one type size (`PB_SMALL`); the time is larger
(`PB_BIG`) with tabular numerals. The block leads the tab because it motivates
the head-to-head: their bests sit minutes apart, so ranking a shared parkrun by
finish time would be meaningless — the explainer says so directly.

### Buggy mode in the UI

One glyph everywhere: **🛒**. The rule is that it marks the *exception*, and
that a label is only shown for an athlete who actually uses a buggy — Raju's UI
is unchanged from before the feature, as is everyone's on a database with no
labels.

- **Current-form targets** (Tab 2) — one bordered box per athlete, ordered by
  their regular target, showing **`21:36 / 🛒 24:37`** on one line. An athlete
  with no buggy runs shows a single time and no separator: a slash would imply a
  second target exists.
- **Runs in window** — *one* popover per athlete covering the whole 91-day span,
  not one per target. Buggy runs carry 🛒 after the parkrun name, and the
  highlight is computed **per mode**: 🟨 the run(s) forming the regular target,
  🟦 the run(s) forming the buggy one (the median, or the two averaged for an
  even count). One highlight across both would misrepresent which runs made
  which number.
- **Tables** carry no Mode column: 🛒 goes after the runner's name in the
  head-to-head table and after the parkrun name in the window-runs table. A
  column earns its width only if most rows use it.
- **Scorelines and charts** — 🛒 after the name in the `_h2h_headline` sentence
  and on the victory chart's axis labels; the hover names the mode and the
  target basis, so a **bridged** target (form borrowed from the other mode via
  the handicap) is never mistaken for a measured one. `render_occasion` also
  prints a note in words whenever a target was bridged.
- **Estimated labels read differently** from confirmed ones (`🛒 (est.)`).
  Given how poorly a per-run rule separates a buggy from a hard course, a guess
  has to be visibly a guess.
- Words, not the glyph, in the **explainer prose**, the bridged-target note and
  the `title` tooltip that explains the glyph — those are read rather than
  scanned.

All date-filtered tabs share one mutually-exclusive Year/Season control
(`year_season_filters`); "Season" is year-qualified (e.g. `2018/19 Winter`,
Dec–Feb).

The **sidebar** shows three update markers (UK local time) above the 🔄 Reload
button: **Latest parkrun** (most recent `run_date`), **Pipeline last run** (when
the data was last scraped, `max(scrape_timestamp)` — a server-side fact), and
**App last refreshed** (when this session last pulled data — `data_fetched_at`,
stamped on each version-keyed refetch).

Future ideas: attendance timeline · fastest times · PB progression · age-grade
progression · event frequency · form (target) over refreshes.

---

## MVP goal

1. Download the three athlete pages. ✅ (`parkrun_pipeline.py`)
2. Extract the All Results tables. ✅
3. Load into DuckDB with the reconcile pipeline above. ✅
4. Prevent duplicates via `(athlete_id, run_date, event_id)`. ✅ (UPSERT verified)
5. Support scheduled + manual refreshes. ✅ (launchd on the Mac Sat/Sun +
   login catch-up, proven 19 Jul 2026; manual via `scripts/parkrun_refresh.sh`
   — see `docs/DEPLOY.md`)

The MVP is complete end-to-end (pipeline, scheduler, analytics, front end).
The scheduled-refresh ops question is settled: GitHub-hosted runners are
405-blocked by parkrun's WAF, so launchd on the Mac is the scheduler of record
(`docs/DEPLOY.md`). That removed MotherDuck's original rationale (off-Mac
refreshes), so on 23 Aug 2026 the source of truth moved back to **local
DuckDB**: the refresh updates `~/.config/parkrun/parkrun_local.duckdb`, then
commits and pushes the rebuilt snapshot, which Streamlit Cloud redeploys. The
MotherDuck setup remains documented in `docs/DEPLOY.md` should a non-laptop
refresh host ever emerge.

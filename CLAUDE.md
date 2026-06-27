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
- ✅ Analytics layer — `v_overlap` + `v_head_to_head` views and the
  `current_targets` table built and wired into refresh (see Analytics layer below).
- ✅ Streamlit front end — **local** app built (`app.py`, 3 tabs). Online/hosted
  access (MotherDuck + scheduled refresh) is a separate future strand.
- ⏳ Scheduler (Saturday ~14:00) + manual Refresh button — not wired up.

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
Materialised by the refresh: each athlete's current-form target as of the
refresh date (a snapshot — recomputing live would silently drift). Keyed on
`(refresh_date, athlete_id)` so form history accumulates over refreshes.

| Column | Notes |
|---|---|
| refresh_date | PK part; the date the snapshot was taken |
| athlete_id | PK part |
| target_seconds | 91-day median of `time_seconds` over `[refresh_date−91, refresh_date−1]`; NULL if no runs in window |
| n_window | Runs found in the window (target valid when ≥ 1) |

---

## Analytics layer

The comparison features are **derived from `results`**. The two analytical
features are deterministic from the stored data, so they are DuckDB **views**
(always live, no duplication, no staleness); only the date-anchored
`current_targets` is materialised. Created/refreshed by `ensure_views()` and
`update_current_targets()`. The cohort is fixed (3 athletes); `ATHLETE_NAMES`
in the pipeline is the single source for the per-athlete column names.

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
   `[date−91, date−1]` (min **1** run). Window excludes the event day.
2. **`pct_diff` = round((actual − target) / target × 100, 2)** — faster than form
   is negative.
3. **Placing** = `rank()` over `pct_diff` **ascending** (most-beat-your-form = 1st);
   **standard competition ranking** (ties share a rank, e.g. 1-1-3).
4. **Demote rule** — only participants with a valid target are ranked. Need ≥ 2
   rankable for a contest; a 3-way where one lacks a target becomes a 2-way
   (and `classification` reflects the ranked set).

Each row carries `classification` (e.g. `George vs Raju`, `Duncan vs George vs
Raju`), `n_ranked`, `actual_seconds`, `target_seconds`, `n_window`, `pct_diff`,
`place_rank`. The app shows a per-head-to-head table + a 1sts/2nds/3rds leaderboard.

Note: `v_overlap` counts **all** co-participations; `v_head_to_head` counts only
**rankable** ones — so the two totals can differ (occasions with no valid target).

Caveat (accepted): the target averages across all courses in the window, but a
head-to-head is at one specific course — course difficulty is not adjusted for.

---

## Refresh pipeline spec

Triggered automatically (Saturday ~14:00 UK) **and** via a manual **Refresh
Data** button — both run the identical process. The two paths are independent.

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
current-form targets) then exports the results snapshot CSV. The analytics views
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
| `data/country_lookup.csv` | |
| `data/athletes_lookup.csv` | |
| Python scripts | |

`parkrun_results.csv` is tracked deliberately: parkrun only serves *current*
results, so re-scraping cannot reproduce a past state — the committed snapshots
are the only historical record, and diffs form a per-refresh audit trail.

---

## Repository files

| Path | Purpose |
|---|---|
| `parkrun_pipeline.py` | Loader: `bootstrap` / `refresh` / `status` (Path A/B, DuckDB) + analytics views/targets. Also owns scraping (`scrape_athlete`) and time parsing (`time_to_seconds`). |
| `app.py` | Streamlit front end (3 tabs) reading the `parkrun` schema read-only |
| `data/parkrun_events.csv` | Event catalogue (events.json dump + Victoria Dock) |
| `data/country_lookup.csv` | country_code → country_name |
| `data/athletes_lookup.csv` | Athlete names + DOB |
| `data/parkrun_results.csv` | Results snapshot exported by the pipeline (keyed on event_id) |

Run the pipeline: `python parkrun_pipeline.py refresh` (auto-bootstraps an empty DB).
Run the app: `streamlit run app.py`.

---

## Technology stack

Python · requests · pandas · BeautifulSoup4 · lxml · DuckDB. Front end:
Streamlit (preferred) or Shiny for Python.

### Environment

- Python 3.14 venv: `~/Documents/Python scripts/env` (has requests, pandas,
  bs4, lxml, html5lib, duckdb 1.5.4; front end: streamlit, plotly, matplotlib,
  matplotlib-venn).
- DuckDB database: `~/Documents/duckdb/my_database.duckdb`.

---

## Visualisations

**Built (local Streamlit, `app.py`)**: Tab 1 participation overlap / Venn
(`v_overlap`) + per-athlete company; Tab 2 form-adjusted head-to-head summary
(`v_head_to_head`, `current_targets`); Tab 3 head-to-head detail. Hosting it
online is a separate future strand.

Future ideas: attendance timeline · fastest times · PB progression · age-grade
progression · event frequency · tourism map (uses event coordinates) · form
(target time) over refreshes.

---

## MVP goal

1. Download the three athlete pages. ✅ (`parkrun_pipeline.py`)
2. Extract the All Results tables. ✅
3. Load into DuckDB with the reconcile pipeline above. ✅
4. Prevent duplicates via `(athlete_id, run_date, event_id)`. ✅ (UPSERT verified)
5. Support scheduled + manual refreshes. ⏳ (`refresh` command exists; scheduler/button pending)

The MVP data pipeline is complete. Next: scheduler + manual refresh trigger, then
the visual analytics layer.

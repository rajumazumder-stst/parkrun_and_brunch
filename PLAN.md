# Parkrun App — Development Plan

Planning document for the next round of changes. Complements `CLAUDE.md` (the
project brief + data-pipeline spec); this file is the **sequenced work plan**.

---

## Locked decisions

- **Testing = local run.** Development happens on a feature branch, viewed via
  `streamlit run app.py` on localhost. No hosted preview environment for now —
  branch isolation is enough to keep the deployed/committed version untouched.
- **MotherDuck migration is last.** The app already abstracts its backend
  (`PARKRUN_DB` env/secret → bundled snapshot), so no feature needs MotherDuck to
  be built. Migrating a known-good, feature-complete app once keeps the risk/cost
  checkpoint clean.
- **Hosted preview deferred.** Revisit only when it earns its keep (sharing a
  URL, or validating the MotherDuck cloud path) — which naturally coincides with
  the MotherDuck step, not before.

---

## Execution order

> **✅ Stage 7 — parkrun data pushed to MotherDuck (done 2026-07-05).**
> `python parkrun_pipeline.py motherduck` uploads the parkrun-only tables + views
> to a free-tier (Lite: 10 GB / 10 hrs-compute) MotherDuck database named
> `parkrun_snapshot`; verified parkrun-only (no `personal_finance` leak) with
> views executing server-side. Needs the `motherduck_token` env var (never
> committed).
>
> **▶ Stage 8 — scheduled auto-refresh + auto-reloading app (in progress).**
> Goal: the pipeline runs on a schedule **off the Mac** (and ad-hoc from
> anywhere), updates MotherDuck **in place**, and the hosted app reflects new data
> **on its own** (no manual click). Progress: **8.0 spike ✅**, **8.1 MotherDuck
> source-of-truth ✅** (refresh upserts into `md:` directly). **NEXT: 8.3
> version-marker auto-reload** in `app.py` (small, independent), then **8.2 GitHub
> Actions cron scheduler**. Final flip: point the hosted app at MotherDuck (the
> `PARKRUN_DB` + `motherduck_token` secrets). **8.3 auto-reload ✅ done too;**
> only **8.2 (GitHub Actions scheduler)** + the hosted-app secret flip remain.
> See Step 8 below.

| # | Change | Status | Why here | Depends on |
|---|--------|--------|----------|------------|
| 1 | Local dev/test environment | ✅ done | Foundation — a safe sandbox for everything after | — |
| 2 | Filter rework (Year/Season) | ✅ done | The date/selection model that 3rd-place + trend build on | 1 |
| 3 | 3rd-place show/hide | ✅ done | Same head-to-head filter surface as change 2 | 2 |
| 4 | Cumulative 1sts trend | ✅ done | Scoped by year/season from change 2 | 2 |
| 5 | Target time by Saturday | ✅ done | Independent visual; reuses 91-day target logic | 1 |
| 6 | Head-to-head map | ✅ done | Independent visual; joins to event coordinates | 1 |
| 7 | MotherDuck migration | ✅ done | Go-live step — migrated once, verified parkrun-only + free | all |
| 8 | Scheduled auto-refresh + auto-reload | ⏳ next | Keeps the live app current with no manual step | 7 |

**Decisions locked while building:**
- **Stage 2** — Winter = `YYYY/YY Winter` spanning Dec–Feb; Year/Season are two
  dropdowns that auto-clear each other; shared via `year_season_filters()`.
- **Stage 4** — cumulative 1sts requires a head-to-head selection; 0 baseline;
  markers = real 1sts (hover names the parkrun); line ends at the latest 1st;
  athletes with none get a written note; y-axis dtick scales with the count.
- **Stage 5** — Saturday target = the same 91-day median as the head-to-head
  target, in a `v_saturday_targets` view; Form tab gained Year/Season filters and
  legend-driven axis rescale.
- **Stage 6** — Folium + OpenStreetMap; one pie marker per venue (size = count,
  slices = wins per athlete); shown only once a head-to-head is selected.
- **Stage 7** — upload is parkrun-only (per-object copies, never a whole-DB
  dump); MotherDuck DB named `parkrun_snapshot` (catalog ≠ the `parkrun` schema);
  run explicitly via the `motherduck` command, not by bootstrap/refresh.
- **Stage 8** (locked 2026-07-05):
  - **8.0/8.1 probes passed** — GitHub Actions can scrape parkrun; the pipeline's
    write path (`ON CONFLICT`, `INSERT OR REPLACE`, txns, `register()+INSERT`) runs
    on `md:`. Caveat: the `md:` schema must carry PKs (via `ensure_schema`, not CTAS).
  - **Migration = re-seed preserving history** (decision 1): fix `build_motherduck`
    to build a constrained schema (`ensure_schema`) + `INSERT … SELECT` from the
    local dev DB, run once → `md:` becomes source of truth with `current_targets`
    history intact. Thereafter `refresh` targets `md:` directly.
  - **Scheduler commits the audit CSV back** (decision 2): the Action re-commits
    `data/parkrun_results.csv` after each cloud refresh (snapshot optional).
  - **Cron = 14:00 UK year-round** (decision 3): GitHub cron is UTC/DST-blind, so
    two entries (`13:00` + `14:00 UTC` Sat) + a `TZ=Europe/London` guard that runs
    only when London-local is 14:xx.

**Stages 1–7 shipped.** Remaining: Stage 8 (scheduled auto-refresh +
auto-reload), which needs the GitHub Actions scraping spike to pass first and
can't be validated from the local dev loop alone.

> Numbering above is **execution order**. In brackets below, the original change
> label from the conversation is given so nothing is lost.

---

## Detail per step

### Step 1 — Local dev/test environment  *(orig. change 1)*
- **Goal:** make changes, view them in a browser, without altering the live/
  committed version.
- **Approach:** a feature branch + documented local-run workflow. Run against the
  full dev DB (`PARKRUN_DB=~/Documents/duckdb/my_database.duckdb streamlit run
  app.py`) or the bundled snapshot. No cloud, no infra.
- **Done when:** a documented, repeatable local workflow exists and edits are
  visible on localhost while `main` / the committed snapshot stay untouched.
- **Open decisions:** none.

### Step 2 — Filter rework  *(orig. change 6)*
- **Goal:** three things:
  1. **Year and Season are mutually exclusive** — the user picks one *or* the
     other for date filtering.
  2. **Season becomes year-specific** — e.g. `2018 Autumn` = 1 Sep 2018 →
     30 Nov 2018 (a concrete date range in a given calendar year).
  3. **Head-to-head-aware options** — when the head-to-head filter is anything
     other than "All", only offer years/seasons that have data for that
     pairing/trio.
- **Approach:** rework the filter widgets in `app.py`; derive available
  years/seasons from the in-scope `v_head_to_head` (or `v_overlap`) rows.
- **Done when:** selecting a year disables/clears season (and vice versa);
  seasons resolve to correct per-year date ranges; option lists shrink to the
  selected head-to-head's data.
- **Open decisions (park until this step):**
  - Season→month mapping. Proposed meteorological: Winter Dec–Feb, Spring
    Mar–May, Summer Jun–Aug, Autumn Sep–Nov.
  - How "Winter" handles the Dec/Jan calendar-year split (e.g. is `2018 Winter`
    Dec 2018 → Feb 2019?).

### Step 3 — 3rd-place show/hide  *(orig. change 5)*
- **Goal:** in the head-to-head visuals, show 3rd places **only** when the
  3-athlete head-to-head (or "All") is selected; hide them otherwise (a 2-way
  contest has no 3rd).
- **Approach:** conditional column/entry rendering in the head-to-head tabs,
  driven by the head-to-head selection.
- **Done when:** 2-way selections show no 3rd-place column/row; 3-way and "All"
  show it.
- **Open decisions:** none beyond confirming "All" counts as 3-capable.

### Step 4 — Cumulative 1sts trend  *(orig. change 7)*
- **Goal:** a trended visual showing **cumulative 1st-place finishes** across the
  selected date period, per athlete. Initially scoped to a selected year or
  season.
- **Approach:** step lines over date within the chosen year/season, counting
  form-adjusted 1sts from `v_head_to_head` (`place_rank = 1`). Respects change 3's
  in-scope contest set.
- **Done when:** each athlete has a monotonic step line of cumulative 1sts over
  the selected period.
- **Open decisions:** whether ties for 1st (standard competition ranking allows
  1-1-3) each increment — assume yes unless decided otherwise.

### Step 5 — Target time by Saturday  *(orig. change 3)*
- **Goal:** a visual of each athlete's target time by date, evaluated **only on
  Saturdays**.
- **Approach:** a new Saturday-indexed target series — 91-day rolling median of
  `time_seconds` per athlete, evaluated at each Saturday. This is the same target
  definition already used inside `v_head_to_head`; generalise that logic rather
  than duplicate it. Likely a new view (e.g. `v_saturday_targets`) since
  `current_targets` only snapshots refresh dates and can't reconstruct history.
- **Done when:** a line per athlete plots target seconds across Saturdays.
- **Open decisions:** confirm target = 91-day rolling median of `time_seconds`
  (min 1 run in window), identical to the head-to-head target.

### Step 6 — Head-to-head map  *(orig. change 4)*
- **Goal:** a zoomable, interactive map of where head-to-heads took place.
- **Approach:** join `v_head_to_head` occasions to `events.longitude/latitude`,
  render an interactive map. Marker encoding (count, winner, etc.) TBD.
- **Done when:** an interactive, zoomable map shows head-to-head locations.
- **Open decisions:** map library; what each marker encodes; interaction with the
  date/head-to-head filters.

### Step 7 — MotherDuck migration  *(orig. change 2)*
- **Goal:** migrate the runtime data to MotherDuck **at no cost**, without leaking
  the `personal_finance` schema.
- **Approach:** push **parkrun-only** tables + views to a MotherDuck database
  (same discipline as `build_snapshot()`), point the app's `PARKRUN_DB`
  resolution at it. Verify usage sits inside the free tier.
- **Risks to manage:** (a) never upload `personal_finance`; (b) token/secret
  handling; (c) client/server DuckDB version compatibility; (d) confirm free-tier
  thresholds against the *current* MotherDuck pricing page before relying on
  "no cost".
- **Cheap hedge (optional, early):** a throwaway compatibility spike — upload a
  parkrun-only copy once, run the existing `v_overlap` / `v_head_to_head` views
  plus the new SQL shapes from steps 2/4/5, confirm they execute, then tear it
  down. De-risks the dialect question without building against the remote backend.
- **Done when:** the app reads from MotherDuck, contains only parkrun data, and
  usage is confirmed free. DBeaver can also connect (via the DuckDB driver +
  `md:` connection string / token).
- **Open decisions:** dev vs prod database split (only relevant if a hosted
  preview is later added).
- **Status:** ✅ done 2026-07-05 — parkrun-only upload verified against the live
  account. Pointing the *hosted* app at MotherDuck is folded into Step 8.

### Step 8 — Scheduled auto-refresh + auto-reloading app  *(orig. change 2, cont.)*
- **Goal:** the pipeline runs on a schedule **off the Mac** (and ad-hoc from
  anywhere — Mac, laptop, cloud), updates MotherDuck **in place**, and the hosted
  app reflects new data **automatically** (no manual "Reload data" click).
- **Shape:**
  ```
          ┌──────────── MotherDuck (persistent parkrun DB) ────────────┐
          │  results · events · current_targets · views                 │
          └──▲──────────────────────▲──────────────────────────▲───────┘
             │ refresh (upsert)      │ refresh (upsert)         │ read
     GitHub Actions (cron)         Mac / laptop (ad-hoc)      Streamlit app
  ```

- **8.0 — Spike (gates everything).** ✅ **PASS (2026-07-05).** A throwaway
  GitHub Actions workflow (`.github/workflows/scrape-spike.yml`) fetched all
  three athlete pages: HTTP 200, `server: Apache` (**not** Cloudflare — the
  `/parkrunner/` endpoint isn't behind the challenge), no challenge markers,
  results tables parsed (310 / 167 / 341 rows). The datacenter-IP worry didn't
  materialise → **GitHub Actions is a green light for 8.2**; launchd fallback not
  needed. The spike workflow is throwaway — delete it when 8.2's real scheduled
  workflow supersedes it.

- **8.1 — MotherDuck as the pipeline's source of truth.** ✅ **done (2026-07-05).**
  `PARKRUN_PIPELINE_DB` selects the target (unset = local dev DB; `md:` = cloud);
  all `parkrun.`-qualified SQL runs unchanged against either. `build_motherduck`
  now (re)seeds a **constrained** schema (via `ensure_schema`, PKs intact) by
  reading local tables into pandas and `register()+INSERT`-ing into `md:` with
  explicit column lists — preserving existing rows incl. `current_targets`
  history. Verified live: re-seed kept both refresh dates + installed all 5 PKs;
  a full `refresh` **directly against `md:`** scraped, upserted 818 rows, appended
  today's `current_targets` (6→9), and rebuilt the local CSV/snapshot from the
  `md:` connection. Commit `f048f63`.
  - Parameterise the target DB (a `PARKRUN_PIPELINE_DB` env / arg) so `bootstrap`
    / `refresh` / `status` can point at either the local dev DB **or** `md:`.
  - **Compatibility check** — ✅ **done (2026-07-05).** A throwaway probe ran the
    exact write shapes against `md:` — composite `PRIMARY KEY` DDL, `register()`
    a DataFrame + `INSERT ... SELECT`, `BEGIN/COMMIT`, `INSERT ... ON CONFLICT DO
    UPDATE`, `INSERT OR REPLACE`, `median()`/`CURRENT_DATE`, `generate_series` —
    **7/7 passed.** The write path is MotherDuck-compatible.
  - **⚠ Key finding:** `ON CONFLICT` / `INSERT OR REPLACE` need the table's
    `PRIMARY KEY` as their arbiter, but `build_motherduck()` copies tables via
    `CREATE TABLE AS SELECT`, which **drops constraints** — so the *current* cloud
    tables have no PK. The refactor must create the `md:` schema with
    `ensure_schema()` (constraint-carrying DDL), then load data — **not** CTAS.
    This is the concrete change 8.1 turns on.
  - Keep `personal_finance` unreachable: the pipeline only ever touches the
    `parkrun` schema, so operating on `md:` directly stays parkrun-only by
    construction (no `personal_finance` catalog is ever attached to `md:`).

- **8.2 — Scheduler.** GitHub Actions cron, Saturday ~14:00 UK (after results
  post). Runs `refresh` against `md:`; `motherduck_token` stored as a **GitHub
  Actions secret**. Optionally also commit the refreshed `parkrun_results.csv`
  back to the repo to keep the audit trail. (If 8.0 fails, this becomes a launchd
  job on the Mac instead.)

- **8.3 — Version-marker auto-reload (`app.py`).** ✅ **done (2026-07-05).**
  `data_version()` (60s TTL) reads `max(scrape_timestamp)` and is threaded as a
  hashed `version` arg into all five loaders, so they auto-refetch exactly when a
  refresh writes new data (upsert re-stamps every row, so the version advances
  each refresh). Verified against `md:`: version reflects the last refresh and
  the loaders serve correct data. The 🔄 Reload button (`cache_data.clear()`)
  stays as a manual override. Mechanism (for reference):
  - A small cached function with a **short TTL** (~60s) reads just a **data
    version** — e.g. `SELECT max(scrape_timestamp) FROM parkrun.results` (one
    scalar, negligible compute).
  - Feed that version value **as a cache key** into the heavy `load_*` loaders
    (an argument they ignore except for cache identity). When the pipeline writes
    new data the version changes → the heavy queries **auto-refetch exactly when
    data changes**, and stay cached otherwise.
  - Keep the existing **"🔄 Reload data"** button ([app.py:396](app.py#L396)) for
    a manual override. Note the button is a *reload* (re-reads the backend), not a
    pipeline *refresh* (does not scrape) — the two must not be conflated.
  - Flip the **hosted** app onto MotherDuck by setting the `PARKRUN_DB`
    (`md:parkrun_snapshot`) + `motherduck_token` Streamlit secrets — the go-live
    toggle. The bundled snapshot stays as the fallback.

- **Done when:** a scheduled run (off the Mac, or launchd if 8.0 fails) refreshes
  MotherDuck in place, and an open hosted app shows the new data within the
  version-marker window without anyone clicking anything.
- **Open decisions:** see Parked decisions below.

---

## Parked decisions (need answers before their step)

- **Step 2:** season→month mapping + Winter's Dec/Jan year handling.
- **Step 4:** do tied 1sts each increment the cumulative count?
- **Step 5:** confirm Saturday target definition matches the head-to-head target.
- **Step 6:** map library, marker semantics, filter interaction.
- **Step 8.0:** does GitHub Actions' IP get past parkrun/Cloudflare? (spike answers it)
- **Step 8.1:** operate on `md:` directly, or pull→refresh-local→push? (leaning
  direct-`md:` for simplicity, pending the compatibility check)
- **Step 8.3:** version-marker TTL (~60s?) and the exact version column
  (`max(scrape_timestamp)` from `results` vs a dedicated meta row).

**Note — "losing `current_targets` accumulation" (clarified 2026-07-05):** a
stateless runner would only ever hold *today's* `current_targets` row, but those
targets are **recomputable from `results`** (and `v_saturday_targets` already
reconstructs a target per Saturday), so the only genuine loss is point-in-time
fidelity against a retroactive parkrun time correction — cosmetic here. Making
MotherDuck the source of truth (8.1) removes the concern entirely by keeping the
DB persistent across runs.

---

## Cross-cutting notes

- All feature work (steps 2–6) is read-only query/UI logic in `app.py` plus, where
  needed, new **views** in `parkrun_pipeline.py` (`ensure_views()`), keeping the
  "views are always-live, only date-anchored data is materialised" principle.
- Snapshot discipline unchanged: any new view must be copied into
  `data/parkrun_snapshot.duckdb` by `build_snapshot()` so the deployable app
  keeps working.
- **Step 8 is the first change that isn't read-only UI work:** it adds a pipeline
  **write path to `md:`** (8.1) and a cache-key tweak in `app.py` (8.3). The
  read-only invariant for the *app* still holds — the app only ever reads; the
  writes live in `parkrun_pipeline.py`, which runs out-of-band (schedule / ad-hoc),
  never from the Streamlit process.

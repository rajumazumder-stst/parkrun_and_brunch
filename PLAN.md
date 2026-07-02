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

| # | Change | Status | Why here | Depends on |
|---|--------|--------|----------|------------|
| 1 | Local dev/test environment | ✅ done | Foundation — a safe sandbox for everything after | — |
| 2 | Filter rework (Year/Season) | ✅ done | The date/selection model that 3rd-place + trend build on | 1 |
| 3 | 3rd-place show/hide | ✅ done | Same head-to-head filter surface as change 2 | 2 |
| 4 | Cumulative 1sts trend | ✅ done | Scoped by year/season from change 2 | 2 |
| 5 | Target time by Saturday | ✅ done | Independent visual; reuses 91-day target logic | 1 |
| 6 | Head-to-head map | ✅ done | Independent visual; joins to event coordinates | 1 |
| 7 | MotherDuck migration | ⏳ next | Go-live step — migrate once, verified parkrun-only + free | all |

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

**Stages 1–6 shipped and refactored** (Dec–Feb filter logic DRY'd into
`year_season_filters`; module docstring + docs updated). Remaining: Stage 7
(MotherDuck), which needs the MotherDuck account/token and can't be done from
the dev loop alone.

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

---

## Parked decisions (need answers before their step)

- **Step 2:** season→month mapping + Winter's Dec/Jan year handling.
- **Step 4:** do tied 1sts each increment the cumulative count?
- **Step 5:** confirm Saturday target definition matches the head-to-head target.
- **Step 6:** map library, marker semantics, filter interaction.

---

## Cross-cutting notes

- All feature work (steps 2–6) is read-only query/UI logic in `app.py` plus, where
  needed, new **views** in `parkrun_pipeline.py` (`ensure_views()`), keeping the
  "views are always-live, only date-anchored data is materialised" principle.
- Snapshot discipline unchanged: any new view must be copied into
  `data/parkrun_snapshot.duckdb` by `build_snapshot()` so the deployable app
  keeps working.

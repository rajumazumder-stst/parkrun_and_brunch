# Deployment & operations

How the live app is served, refreshed, and kept current. Complements `DEV.md`
(local dev loop) and `CLAUDE.md` (project brief / data-pipeline spec).

---

## Architecture

```
Sat 14:00 UK ──┐
Sun 01:00 UK ──┴► GitHub Actions ─► refresh ─► MotherDuck (parkrun_snapshot)
  (refresh.yml)                                      │  source of truth
                                                     │
                          app's data_version() sees a new scrape_timestamp
                                                     ▼
                              hosted Streamlit app auto-reloads (≤ 60s)
```

- **MotherDuck (`md:parkrun_snapshot`) is the runtime source of truth.** It holds
  the parkrun-only tables + views (no `personal_finance` — enforced by
  construction; the pipeline only ever touches the `parkrun` schema).
- The **local dev DB** (`~/Documents/duckdb/my_database.duckdb`) is still where
  ad-hoc local work happens; it is *not* what the live app reads.
- The **bundled snapshot** (`data/parkrun_snapshot.duckdb`) is the zero-cost
  fallback the app serves when no `PARKRUN_DB` is configured.

---

## Backends the app can read (`_resolve_db_path` in `app.py`)

Priority order:

1. `PARKRUN_DB` env var — e.g. `md:parkrun_snapshot`, or a local file path.
2. `PARKRUN_DB` **Streamlit secret** (hosting dashboard).
3. The bundled read-only snapshot `data/parkrun_snapshot.duckdb` (default).

For a `md:` value the app also needs the token: it reads `motherduck_token` from
the environment, falling back to a `motherduck_token` Streamlit secret
(`_ensure_motherduck_token`). MotherDuck connections skip the `read_only` flag.

---

## Go-live: point the hosted app at MotherDuck

The scheduler already keeps `md:` current, but the **hosted app serves the
bundled snapshot until you flip it**. In the Streamlit Community Cloud dashboard
(share.streamlit.io) → your app → **Settings → Secrets**, add:

```toml
PARKRUN_DB = "md:parkrun_snapshot"
motherduck_token = "PASTE_TOKEN"
```

Save; the app reboots and now reads MotherDuck. **Use a read-scoped / read-only
MotherDuck token here** if available — the app only reads, and the secret lives
on Streamlit's servers, so a leaked read-only token can't mutate the cloud data.

**To revert:** delete those two secret lines → the app falls straight back to the
bundled snapshot (no redeploy needed beyond the auto-reboot).

---

## Tokens

- Get a token from the MotherDuck UI (Settings → Access Tokens). **Never** commit
  it or paste it into code/chat.
- **GitHub Actions** (the scheduler) reads it from the `MOTHERDUCK_TOKEN` repo
  secret:
  ```bash
  gh secret set MOTHERDUCK_TOKEN            # paste when prompted, or:
  gh secret set MOTHERDUCK_TOKEN < tokenfile
  ```
- **Local `md:` runs** read it from the `motherduck_token` env var:
  ```bash
  motherduck_token=$(cat /path/to/tokenfile) python parkrun_pipeline.py ...
  ```

---

## Scheduled refresh (`.github/workflows/refresh.yml`)

- Fires at **two UK slots year-round: Sat 14:00 and Sun 01:00** (the Sunday
  slot catches Saturday results that post late). GitHub cron is UTC/DST-blind,
  so each slot has two cron entries (the BST and GMT UTC-hours) and a
  `TZ=Europe/London` guard proceeds only at the intended local time — exactly
  one firing per slot per week.
- Runs `PARKRUN_PIPELINE_DB=md:parkrun_snapshot python parkrun_pipeline.py refresh`
  (upserts straight into MotherDuck), then commits the regenerated
  `data/parkrun_results.csv` back to `main` as the audit trail.
- **Ad-hoc cloud refresh** (skips the London guard):
  ```bash
  gh workflow run refresh.yml --ref main
  gh run watch "$(gh run list --workflow=refresh.yml --limit 1 --json databaseId -q '.[0].databaseId')"
  ```
- The workflow must live on the **default branch** (`main`) for the schedule and
  `workflow_dispatch` to be active.

### Operational status (as of 7 Jul 2026)

The scheduled refresh is **configured and live**; reliability is still being
proven. Run history so far (all `workflow_dispatch`, i.e. ad-hoc):

| Run (UK time) | Outcome |
|---|---|
| Sat 5 Jul, 15:19 | ✅ success — scraped, upserted into MotherDuck, audit CSV committed |
| Tue 7 Jul, 18:45 / 18:53 / 19:58 | ❌ all failed — **HTTP 405** on the athlete page |

**The 405 failures in detail.** Each failed run died in Path B's first fetch:

```
requests.exceptions.HTTPError: 405 Client Error: Not Allowed
for url: https://www.parkrun.org.uk/parkrunner/5672/all/
```

405 nominally means "method not allowed", but the pipeline sends an ordinary
GET — the same request that works from a home connection and that worked from
the runner on 5 Jul. What is actually happening: **parkrun fronts
`www.parkrun.org.uk` with bot protection (a WAF), and it answers requests it
scores as automated — e.g. from well-known cloud/datacentre IP ranges like
GitHub-hosted runners' — with a 405 block** rather than a 403/429. Two
observations support this reading:

1. In the *same* failed runs, Path A fetched `images.parkrun.com/events.json`
   fine (a CDN asset host, not behind the athlete-page WAF) — only the
   `www.parkrun.org.uk` fetch was rejected.
2. The 5 Jul run succeeded from a different runner IP, and the three 7 Jul
   failures came in a burst — the block looks IP-/reputation-dependent (and
   possibly rate-sensitive), not deterministic.

The failure mode is **safe**: `scrape_athlete` raises before anything is
written (Path B is all-or-nothing), so MotherDuck keeps its previous
consistent state and no audit CSV is committed; the run simply reports failure.

**Current plan: continue as-is** and see how the first *scheduled* slots fare
— Sat 11 Jul 14:00 UK and Sun 12 Jul 01:00 UK. A once-weekly request pattern
may not trip the WAF the way the 7 Jul ad-hoc burst did. If the scheduled runs
also 405: add a polite retry-with-backoff in `scrape_athlete`, and failing
that, run the refresh from a machine parkrun already serves (e.g. a
self-hosted runner / launchd job on the Mac) instead of GitHub-hosted runners.

---

## Ad-hoc refresh from your Mac

```bash
source ~/Documents/Python\ scripts/env/bin/activate

# Refresh the cloud directly (same as the scheduler does):
PARKRUN_PIPELINE_DB=md:parkrun_snapshot motherduck_token=$(cat tokenfile) \
  python parkrun_pipeline.py refresh

# Or refresh the local dev DB only (does not touch the cloud):
python parkrun_pipeline.py refresh
```

`status` accepts the same `PARKRUN_PIPELINE_DB` to inspect either backend.

---

## Rebuild / re-seed the cloud from local

If the cloud DB is ever wrong and you want to reset it from the local dev DB
(preserving `current_targets` history and re-installing the PK constraints):

```bash
motherduck_token=$(cat tokenfile) python parkrun_pipeline.py motherduck
```

`motherduck` drops and rebuilds the cloud `parkrun` schema via `ensure_schema`
(constraints intact) and re-loads the data. It refuses to run against an `md:`
target — it must source **from** the local DB. After a re-seed, MotherDuck is
again the source of truth and scheduled/ad-hoc `refresh` upserts into it.

---

## Did the flip work? (verifying the hosted app reads MotherDuck)

MotherDuck's query-history views are Business-plan only, so on the free Lite plan
use a **distinguishing edit**:

1. In the MotherDuck SQL UI, change one visible value **in the cloud only**:
   ```sql
   USE parkrun_snapshot;
   UPDATE parkrun.current_targets
   SET target_seconds = target_seconds + 600
   WHERE athlete_id = 5672
     AND refresh_date = (SELECT max(refresh_date) FROM parkrun.current_targets);
   ```
2. Force the app to re-read: the app caches with a 60s `data_version` TTL, so wait
   ~a minute, or click **🔄 Reload data**, or reboot the app from the dashboard.
3. Raju's Tab 2 target jumps by 10:00 → the app is on MotherDuck. Unchanged → the
   `PARKRUN_DB` secret didn't take (still on the snapshot).
4. Revert: the inverse `UPDATE (- 600)`, or re-run `python parkrun_pipeline.py
   motherduck` to rewrite a pristine copy.

---

## Cost / free-tier

MotherDuck **Lite** (free): 10 GB storage, 10 hrs compute/month. This dataset is
~2 MB and queries are light; the app's `data_version` marker keeps it off the
compute meter except when data actually changes. Well within the free tier.

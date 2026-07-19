# Deployment & operations

How the live app is served, refreshed, and kept current. Complements `DEV.md`
(local dev loop) and `CLAUDE.md` (project brief / data-pipeline spec).

---

## Architecture

```
Sat 14:30 local ──┐
Sun 11:00 local ──┼► launchd on the Mac ─► refresh ─► MotherDuck (parkrun_snapshot)
login catch-up  ──┘  (parkrun_refresh.sh)                  │  source of truth
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

> ✅ **Flipped 18 Jul 2026** — the secrets below are set, so the hosted app
> reads `md:parkrun_snapshot` (verification via the sidebar marker or the
> distinguishing-edit procedure below). The bundled snapshot remains the
> fallback if the secrets are ever removed.

To (re)do the flip: in the Streamlit Community Cloud dashboard
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
- **The launchd scheduler** (and manual `scripts/parkrun_refresh.sh` runs) reads
  it from `~/.config/motherduck/token` (chmod 600).
- **Local `md:` runs** read it from the `motherduck_token` env var:
  ```bash
  motherduck_token=$(cat /path/to/tokenfile) python parkrun_pipeline.py ...
  ```

---

## Why the refresh does NOT run on GitHub Actions

A GitHub Actions scheduler (`refresh.yml`, Jul 2026) was tried first and
**deleted on 19 Jul 2026** — retrieve it from git history if ever needed. The
short version, kept here so nobody re-treads this path:

- **parkrun fronts `www.parkrun.org.uk` with a WAF that 405-blocks
  cloud/datacentre IPs**, including GitHub-hosted runners. Every Actions run
  after 5 Jul 2026 failed with `HTTP 405: Not Allowed` on the first athlete
  page (`/parkrunner/5672/all/`), while `images.parkrun.com/events.json` (a
  CDN host, not behind the WAF) fetched fine in the same runs.
- **It's the IP range, not the request.** The decisive A/B (19 Jul): the
  identical pipeline code 405'd from a GitHub runner at Sun 03:39 UK but
  succeeded in full from this Mac's residential IP at 11:12. Chrome-like
  headers, a warmed-up cookie session, and 15 s/30 s retries (all still in
  `parkrun_pipeline.py` — they benefit the launchd path) made no difference
  from the runner.
- The failure mode was safe throughout: Path B is all-or-nothing, so a blocked
  scrape wrote nothing and MotherDuck kept its previous consistent state.
- Also fixed along the way: GitHub cron fires late (15 min – 3.4 h observed),
  so an exact-hour London-time guard made every scheduled run a silent no-op —
  any future cron guard must accept a window, not an hour.

If GitHub-hosted refreshes are ever retried: restore the workflow from history,
re-create the `MOTHERDUCK_TOKEN` repo secret (`gh secret set MOTHERDUCK_TOKEN`),
and expect 405 until parkrun's block changes.

---

## Scheduled refresh — launchd on the Mac (the scheduler of record)

Because parkrun's WAF serves residential IPs happily (and 405-blocks GitHub's
runners — § above), the Mac runs the refresh on a schedule via two launchd
agents (installed 18 Jul 2026,
`~/Library/LaunchAgents/com.raju.parkrun-refresh-{scheduled,login}.plist`):

- **scheduled** — Sat 14:30 + Sun 11:00 local. Mac asleep at slot time →
  launchd fires the job on next wake; Mac powered off → slot missed, handled
  by:
- **login** — runs at every login/agent load; if the last successful refresh
  predates the most recent slot (laptop was off all weekend), it shows a
  **"Refresh now?" dialog** — otherwise exits silently.

Two scripts, deployed as copies to `~/.config/parkrun/` (macOS TCC blocks
launchd from reading `~/Documents`, so the job is fully self-contained there:
its own repo clone, pulled to `origin/main` before each run, and its own venv):

- **`parkrun_refresh.sh`** — the master refresh, and the ONE code path for
  refreshing MotherDuck from this Mac (run it manually any time). Token → pull
  clone → pipeline → stamp `~/.config/parkrun/last_refresh_epoch` (manual runs
  count toward weekend freshness) → auto-commit + push the audit CSV/snapshot
  from its own clone → macOS notification either way.
- **`parkrun_autorefresh.sh`** — scheduling policy only (the agents call it);
  it invokes the master.

Everything logs to `~/Library/Logs/parkrun_refresh.log` (manual runs also
print to the terminal). Needs the MotherDuck token at
`~/.config/motherduck/token` (chmod 600). Diagnostics:
`~/.config/parkrun/parkrun_autorefresh.sh status`. The deployed copies
**self-sync**: each refresh pulls the clone and replaces them from
`repo/scripts/` if they differ, so a script edit goes live one push + one
refresh later (or immediately via a manual `scripts/parkrun_refresh.sh` run).

**Status: proven 19 Jul 2026** — first real weekend exercised the hard path:
the Mac was off through both slots, the login agent detected STALE (last
refresh Sat 17:58 vs missed Sun 11:00 slot), prompted, and the user-approved
run scraped, upserted into `md:parkrun_snapshot`, pushed the audit commit
(`data: local refresh 2026-07-19`), and the hosted app picked it up — no 405.

---

## Ad-hoc refresh from your Mac

```bash
# Refresh the cloud (identical to what the scheduler runs — token from
# ~/.config/motherduck/token, stamps freshness, pushes the audit files):
scripts/parkrun_refresh.sh

# Or refresh the local dev DB only (does not touch the cloud):
source ~/Documents/Python\ scripts/env/bin/activate
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

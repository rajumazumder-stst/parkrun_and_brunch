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
- **GitHub Actions** (manual canary only) reads it from the `MOTHERDUCK_TOKEN`
  repo secret:
  ```bash
  gh secret set MOTHERDUCK_TOKEN            # paste when prompted, or:
  gh secret set MOTHERDUCK_TOKEN < tokenfile
  ```
- **Local `md:` runs** read it from the `motherduck_token` env var:
  ```bash
  motherduck_token=$(cat /path/to/tokenfile) python parkrun_pipeline.py ...
  ```

---

## GitHub Actions refresh (`.github/workflows/refresh.yml`) — manual canary only

> ❌ **Retired as the scheduler on 19 Jul 2026.** The weekend A/B (below)
> confirmed GitHub-hosted runners are IP-blocked by parkrun's WAF, so the cron
> schedule + London-window guard were removed from the workflow (recoverable
> from git history). **Scheduled refreshes now run via launchd on the Mac**
> (§ next section). The workflow keeps `workflow_dispatch` as a spare/canary:
> a manual run that *succeeds* means the block has lifted and GitHub-hosted
> scheduling is viable again.

- **Ad-hoc cloud refresh / canary run** (expect HTTP 405 while the block holds):
  ```bash
  gh workflow run refresh.yml --ref main
  gh run watch "$(gh run list --workflow=refresh.yml --limit 1 --json databaseId -q '.[0].databaseId')"
  ```
- Runs `PARKRUN_PIPELINE_DB=md:parkrun_snapshot python parkrun_pipeline.py refresh`
  (upserts straight into MotherDuck), then commits the regenerated
  `data/parkrun_results.csv` back to `main` as the audit trail.
- The workflow must live on the **default branch** (`main`) for
  `workflow_dispatch` to be active.

### Why it was retired (run history)

| Run (UK time) | Outcome |
|---|---|
| Sat 5 Jul, 15:19 (ad-hoc) | ✅ success — scraped, upserted into MotherDuck, audit CSV committed |
| Tue 7 Jul, 18:45 / 18:53 / 19:58 (ad-hoc) | ❌ all failed — **HTTP 405** on the athlete page |
| Sat 11 – Sat 18 Jul, all *scheduled* slots | ⚪ **no-ops** — green in Actions, but every one was a guard skip (see below); none reached the scrape |
| Sat 18 Jul, 16:13 (ad-hoc) | ❌ failed — HTTP 405 again |
| Sat 18 Jul, 16:26 (ad-hoc, first run **with** the browser-session headers + retries) | ❌ failed — 405 on all 3 attempts (15 s/30 s backoff). Headers/cookies/retries don't beat the block |
| **Sun 19 Jul, 03:39 (scheduled — the decider)** | ❌ failed — the widened guard worked (proceeded inside the Sun 01–04 window), Path A reconciled fine, then **HTTP 405** on the first athlete page after all retries |
| Sun 19 Jul, 12:31 (ad-hoc) | ❌ failed — HTTP 405 |

**The weekend A/B verdict (19 Jul).** Two arms ran the identical code against
the same MotherDuck DB, differing only in network origin: **Arm A** (GitHub
runner, datacentre IP, Sun 03:39) → 405-blocked; **Arm B** (launchd on the Mac,
residential IP, Sun 11:12 via the missed-slot login prompt) → ✅ full success
(scrape, upsert, audit push, app updated). Conclusion: the block is on the IP
range, not the request — GitHub-hosted runners are a dead end, launchd is the
primary refresh path.

**The guard-skip bug (fixed 18 Jul).** Every "successful" scheduled run before
the fix completed in 6–10 s: GitHub cron fires late (observed 15 min – 3.4 h),
and the guard required the London hour to equal the slot hour *exactly*, so
every delayed firing stood down. Net effect: no scheduled run before 19 Jul
ever scraped. The widened window guard (Sat 14–17 / Sun 01–04 London) was
proven by the 19 Jul run — which then hit the 405 wall.

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

**Mitigations tried (18 Jul):** the pipeline sends a full Chrome-like header
set (not just a UA), uses a shared `requests.Session` warmed up on the parkrun
homepage (so any WAF cookies are held), and retries 403/405/429/5xx with
backoff (15 s then 30 s) — see `HEADERS` / `http_session()` /
`get_with_retry()` in `parkrun_pipeline.py`. None of it beats the IP-range
block from GitHub runners; the same code sails through from a residential IP
(and the retry/header hardening still benefits the launchd path).

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
`~/.config/parkrun/parkrun_autorefresh.sh status`. After editing the tracked
scripts: `cp scripts/parkrun_refresh.sh scripts/parkrun_autorefresh.sh ~/.config/parkrun/`.

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

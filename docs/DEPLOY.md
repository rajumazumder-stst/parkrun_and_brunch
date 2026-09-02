# Deployment & operations

How the live app is served, refreshed, and kept current. Complements `DEV.md`
(local dev loop) and `CLAUDE.md` (project brief / data-pipeline spec).

---

## Architecture

```
Sat 14:30 local ──┐
Sun 11:00 local ──┼► launchd on the Mac ─► refresh ─► ~/.config/parkrun/parkrun_local.duckdb
login catch-up  ──┘  (parkrun_refresh.sh)                        │  source of truth
                                                                 │  rebuild + commit + push
                                                                 ▼
                                          data/parkrun_snapshot.duckdb on origin/main
                                                                 │
                                                                 ▼
                                     Streamlit Cloud redeploys, app serves the snapshot
```

- **The local DuckDB `~/.config/parkrun/parkrun_local.duckdb` is the source of
  truth** (since 23 Aug 2026 — see § History below). It lives beside the
  scheduler's clone and venv because macOS TCC blocks launchd agents from
  reading `~/Documents`.
- **The push IS the delivery step.** Every refresh rebuilds
  `data/parkrun_snapshot.duckdb`, commits it from the scheduler's own clone and
  pushes; Streamlit Cloud redeploys on push. A failed push therefore fails the
  run (and leaves the freshness stamp unwritten, so the next slot retries).
- The **bundled snapshot** is what the app serves by default — no `PARKRUN_DB`
  secret, no cloud DB, no token in the hosted environment.
- The **local dev DB** (`~/Documents/duckdb/my_database.duckdb`) is still where
  ad-hoc local work happens; it is *not* the scheduler's DB and *not* what the
  live app reads.
- **MotherDuck is retired but kept documented** (§§ below) in case a non-laptop
  refresh host ever appears.

---

## History: why the source of truth moved back to local DuckDB

MotherDuck was adopted to serve a world where the refresh ran **off the Mac** —
a GitHub Actions scheduler updating a cloud DB no laptop needed to touch. That
premise died with the WAF 405-block (§ below): the refresh runs from this Mac
via launchd, so the laptop is in the loop regardless and the cloud hop no
longer buys machine-independence.

**Migrated 23 Aug 2026.** What changed:

1. `parkrun_refresh.sh` targets `~/.config/parkrun/parkrun_local.duckdb`,
   created on first run from the committed snapshot (which carries the full
   `current_targets` history — nothing is lost).
2. The audit-file push is **fatal on failure**, and the freshness stamp is
   written only after it succeeds — under local source of truth the push is the
   delivery step, so a silent push failure would freeze the live app.
3. The two Streamlit secrets (`PARKRUN_DB` + `motherduck_token`) are removed, so
   the app serves the bundled snapshot.

Note the seed is **not a file copy**: `build_snapshot()` writes its tables with
`CREATE TABLE AS`, so the snapshot has no primary keys and the results UPSERT
(`ON CONFLICT` on the natural key) cannot bind against it. `python
parkrun_pipeline.py seed` fills a proper `ensure_schema()` DB row-for-row
instead (§ Rebuild the local source-of-truth DB).

A non-laptop-dependent refresh host — the only thing that would again require
an online-accessible source of truth — is considered **unlikely**: it would
need a residential IP that parkrun's WAF accepts (a home server / Raspberry Pi
class of solution). **The MotherDuck sections below are deliberately kept for
reference** in case such a host is ever found: `python parkrun_pipeline.py
motherduck` re-seeds the cloud DB from local, and the secret flip (§ Go-live)
re-points the hosted app at it.

---

## What is and is not deployed

Streamlit Cloud serves **`app.py` (router: `/` the five tabs, `/buggy-handicap`
the handicap analysis) plus `data/parkrun_snapshot.duckdb` from the
same commit**, which is why the code and the regenerated snapshot must be
committed together (see the rollout note in the buggy-mode work).

`label_impact.py` is **never** deployed in any meaningful sense. It is a second
Streamlit entry point that only the local launcher starts, and it needs
`v_head_to_head_legacy` — a view built solely by `ensure_legacy_views`, which is
gated on `PARKRUN_LABEL_AUDIT=1` and is never called from `bootstrap` or
`refresh`. So the legacy views cannot reach the source-of-truth DB or the
snapshot, and the hosted app has nothing to serve even if the file is present.

`scripts/dev_fake_labels.py` likewise never runs outside a dev database: it
refuses to write to `~/.config/parkrun/parkrun_local.duckdb` or to the deploy
snapshot.

---

## Backends the app can read (`_resolve_db_path` in `parkrun_ui.py`)

Priority order:

1. `PARKRUN_DB` env var — e.g. `md:parkrun_snapshot`, or a local file path.
2. `PARKRUN_DB` **Streamlit secret** (hosting dashboard).
3. The bundled read-only snapshot `data/parkrun_snapshot.duckdb` (default —
   **what the hosted app uses today**; no secrets set).

For a `md:` value the app also needs the token: it reads `motherduck_token` from
the environment, falling back to a `motherduck_token` Streamlit secret
(`_ensure_motherduck_token`). MotherDuck connections skip the `read_only` flag.

---

## Go-live: point the hosted app at MotherDuck

> ⚠️ **Superseded 23 Aug 2026.** The flip below was live from 18 Jul to
> 23 Aug 2026; both secrets have since been **removed**, so the hosted app
> serves the bundled snapshot again (§ History). Kept as the procedure to
> re-point the app at MotherDuck should a non-laptop refresh host appear.

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

Only needed for the optional MotherDuck path — the refresh and the hosted app
no longer use a token at all.

- Get a token from the MotherDuck UI (Settings → Access Tokens). **Never** commit
  it or paste it into code/chat.
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
  refreshing the data from this Mac (run it manually any time). Pull clone →
  seed `~/.config/parkrun/parkrun_local.duckdb` if it doesn't exist yet →
  pipeline refresh against it → auto-commit + push the audit CSV/snapshot from
  its own clone → stamp `~/.config/parkrun/last_refresh_epoch` (manual runs
  count toward weekend freshness) → macOS notification either way. The stamp is
  written **after** a successful push, so a failed delivery leaves the weekend
  "uncovered" and the next slot / login catch-up retries it.
- **`parkrun_autorefresh.sh`** — scheduling policy only (the agents call it);
  it invokes the master.
- **`sync_working_copy.sh`** — sourced by the master; see below.

Everything logs to `~/Library/Logs/parkrun_refresh.log` (manual runs also
print to the terminal). No token or network credential is needed. Diagnostics:
`~/.config/parkrun/parkrun_autorefresh.sh status`. The deployed copies
**self-sync**: each refresh pulls the clone and replaces them from
`repo/scripts/` if they differ, so a script edit goes live one push + one
refresh later (or immediately via a manual `scripts/parkrun_refresh.sh` run).

### Working-copy sync (`scripts/sync_working_copy.sh`)

The refresh commits and pushes from its **own** clone under
`~/.config/parkrun/repo`, so the working copy you actually edit in
(`~/Documents/repos/parkrun_and_brunch`) silently falls behind `origin/main` —
two or three `data: local refresh` commits by Monday. After a successful push
the master sources this helper and calls `sync_working_copy`, which:

- **always fetches** (`--all --prune`), so `git status` and `run_local.sh` report
  against fresh remote refs;
- **fast-forwards only when it is safe** — the tree is clean **and** the branch is
  `main`. Anything else (uncommitted work, a feature branch, a detached HEAD, a
  diverged `main`) logs the reason and stops at the fetch.

`dev` is deliberately **never** auto-advanced: it may be checked out in another
worktree or sitting mid-feature, and auto-moving it loses work. The fetch keeps
it aware; advancing it stays a manual `git merge --ff-only main`.

Every path returns 0 and every git call is guarded. The master runs under
`set -euo pipefail` and the call sits **after** `date +%s >"$STAMP"`, so a sync
problem can never fail a refresh, block delivery or touch the freshness stamp.
Override the target with `PARKRUN_WORKING_COPY`.

`run_local.sh` sources the same helper in `--fetch-only` mode and prints how many
commits behind `origin/main` you are.

**Known limitation — this does not work under the scheduler.** macOS TCC blocks
launchd agents from reading `~/Documents`, so under the *scheduled* agent every
git call in the helper fails, logs
`fetch failed (offline, or TCC blocked ~/Documents) — skipped`, and returns 0.
It works on **manual/terminal** runs of `scripts/parkrun_refresh.sh`. That
asymmetry is expected, not a bug: granting launchd Full Disk Access would fix it
and is deliberately not done.

---

**Status: proven 19 Jul 2026** — first real weekend exercised the hard path:
the Mac was off through both slots, the login agent detected STALE (last
refresh Sat 17:58 vs missed Sun 11:00 slot), prompted, and the user-approved
run scraped, upserted into the cloud DB (the backend at the time), pushed the
audit commit (`data: local refresh 2026-07-19`), and the hosted app picked it
up — no 405. The scheduling behaviour is unchanged by the 23 Aug 2026 move to a
local source of truth; only the pipeline's target DB and the push's severity
changed.

---

## Two known warts

**The working-copy sync helper is tested before the pull.**
`parkrun_refresh.sh` sources `scripts/sync_working_copy.sh` at line ~89 but
pulls the clone at ~98, so the run that first brings a new helper into the
clone always installs the no-op stub instead and logs `working copy sync:
helper not in this clone — skipped`. It self-heals on the next run, and the
guard exists precisely because an older clone will not have the file — but the
ordering means a freshly-deployed helper is always skipped once. Moving the
check below the pull is a one-line fix if it ever matters.

**`st.components.v1.html` is past its removal date.** `parkrun_app.py` uses it
to inject the home-screen icons, and Streamlit says it "will be removed after
2026-06-01" — a date now passed. It works only because `requirements.txt` pins
`streamlit==1.58.0`. The first time that pin is bumped, expect the icon
injection to break; `st.iframe` is the replacement.

## Ad-hoc refresh from your Mac

```bash
# The real thing (identical to what the scheduler runs — refreshes the
# source-of-truth DB, stamps freshness, pushes the audit files = deploys):
scripts/parkrun_refresh.sh

# Or refresh the local dev DB only (does not deploy anything):
source ~/Documents/Python\ scripts/env/bin/activate
python parkrun_pipeline.py refresh
```

`status` accepts the same `PARKRUN_PIPELINE_DB` to inspect any backend:

```bash
PARKRUN_PIPELINE_DB=~/.config/parkrun/parkrun_local.duckdb \
  python parkrun_pipeline.py status
```

---

## Rebuild the local source-of-truth DB

The scheduler creates it automatically on first run, and the same command
rebuilds it if it is ever lost or corrupted — the committed snapshot is the
recovery point, and it carries the full `current_targets` history:

```bash
source ~/Documents/Python\ scripts/env/bin/activate
rm -f ~/.config/parkrun/parkrun_local.duckdb          # only if replacing one
PARKRUN_PIPELINE_DB=~/.config/parkrun/parkrun_local.duckdb \
  python parkrun_pipeline.py seed                     # defaults to data/parkrun_snapshot.duckdb
```

`seed` refuses to touch a DB that already holds data, and it is **not** a file
copy: it creates the tables via `ensure_schema()` (primary keys intact — the
results UPSERT needs them) and inserts the snapshot's rows with explicit column
lists. Pass a path argument to seed from a different snapshot file.

Never name a local DB `parkrun.duckdb`: the filename becomes the DuckDB catalog
name, and `parkrun.v_overlap` would then be ambiguous against the `parkrun`
schema.

---

## Rebuild / re-seed the cloud from local

If the cloud DB is ever wrong and you want to reset it from the local dev DB
(preserving `current_targets` history and re-installing the PK constraints):

```bash
motherduck_token=$(cat tokenfile) python parkrun_pipeline.py motherduck
```

`motherduck` drops and rebuilds the cloud `parkrun` schema via `ensure_schema`
(constraints intact) and re-loads the data. It refuses to run against an `md:`
target — it must source **from** a local DB. Re-seeding does *not* by itself
make MotherDuck the source of truth again: that also needs the secret flip
(§ Go-live) and `parkrun_refresh.sh` pointed back at `md:parkrun_snapshot`.

---

## Did the flip work? (verifying the hosted app reads MotherDuck)

> Historical — applies only while the MotherDuck secrets are set (they are not,
> since 23 Aug 2026). To verify a *snapshot*-served deploy instead, check the
> sidebar's **Pipeline last run** marker against the newest `data: local
> refresh` commit on `origin/main`.

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

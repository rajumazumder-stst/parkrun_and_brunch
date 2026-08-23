#!/usr/bin/env bash
#
# Master parkrun refresh — THE one way this Mac refreshes the parkrun data.
# Run it manually any time:
#   scripts/parkrun_refresh.sh          (or the deployed copy ~/.config/parkrun/parkrun_refresh.sh)
# The launchd scheduler (parkrun_autorefresh.sh) calls this same script, so
# manual and scheduled refreshes are one code path.
#
# What it does: git pull the agent clone (origin/main) → seed the local
# source-of-truth DB from the committed snapshot if it doesn't exist yet →
# pipeline refresh against that local DB → auto-commit + push the regenerated
# audit CSV/snapshot → stamp success (so the scheduler knows the weekend is
# covered) → macOS notification either way.
#
# The push IS the delivery step: the hosted app serves the committed
# data/parkrun_snapshot.duckdb, and Streamlit Cloud redeploys on push. A push
# failure therefore FAILS the run (no stamp) so the next slot / login catch-up
# retries it.
#
# Self-contained under ~/.config/parkrun (repo/ clone + venv/ + the local DB)
# because macOS TCC blocks launchd agents from reading ~/Documents.
# Log: ~/Library/Logs/parkrun_refresh.log (manual runs also print to the terminal).
#
# Self-deploying: each run pulls the clone and re-syncs the ~/.config/parkrun
# script copies from it, so edits land on the deployed copies one push +
# one run later — no manual cp step.
set -euo pipefail

STATE_DIR="$HOME/.config/parkrun"
REPO="${PARKRUN_REPO:-$STATE_DIR/repo}"
VENV="$STATE_DIR/venv"
LOG="$HOME/Library/Logs/parkrun_refresh.log"
STAMP="$STATE_DIR/last_refresh_epoch"
LOCKDIR="$STATE_DIR/refresh.lock"
# Source of truth. Filename (= DuckDB catalog name) must NOT be `parkrun`, else
# `parkrun.v_overlap` is ambiguous against the `parkrun` schema.
LOCAL_DB="$STATE_DIR/parkrun_local.duckdb"

mkdir -p "$STATE_DIR"
# Interactive runs: show output AND append to the log. Agent runs: log only.
if [[ -t 1 ]]; then
  exec > >(tee -a "$LOG") 2>&1
else
  exec >>"$LOG" 2>&1
fi

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

notify() { # $1 title, $2 body
  /usr/bin/osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 || true
}

# Commit + push the regenerated audit CSV and fallback snapshot from the
# agent's own clone (never touches any ~/Documents working copy). This is the
# delivery step for the hosted app, so the caller treats failure as fatal.
push_audit_files() {
  cd "$REPO"
  git add data/parkrun_results.csv data/parkrun_snapshot.duckdb
  if git diff --cached --quiet; then
    log "audit files unchanged — nothing to commit"
    return 0
  fi
  if git commit --quiet -m "data: local refresh $(date '+%F')" &&
    { git push --quiet || { git pull --rebase --quiet && git push --quiet; }; }; then
    log "audit files committed + pushed"
  else
    log "ERROR: audit commit/push failed — the hosted app will NOT see this refresh"
    return 1
  fi
}

# Single-instance lock (manual run + scheduled slot could otherwise overlap).
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  log "another refresh is already running — exiting"
  exit 0
fi
trap 'rmdir "$LOCKDIR"' EXIT

if [[ ! -r "$REPO/parkrun_pipeline.py" ]]; then
  log "ERROR: cannot read $REPO — re-clone with: git clone https://github.com/rajumazumder-stst/parkrun_and_brunch.git $REPO"
  notify "parkrun refresh" "❌ Agent repo clone missing/unreadable — see log"
  exit 1
fi

# Run the deployed pipeline code (origin/main). Pull failure (offline etc.)
# is logged but doesn't block the refresh.
git -C "$REPO" pull --ff-only --quiet || log "WARN: git pull failed — refreshing with existing clone"

# Self-deploy: keep the ~/.config/parkrun script copies in step with the
# freshly pulled clone (effective from the NEXT run — bash already holds this
# file). Replace via mv, a new inode: never rewrite a running script in place.
for f in parkrun_refresh.sh parkrun_autorefresh.sh; do
  src="$REPO/scripts/$f"; dst="$STATE_DIR/$f"
  if [[ -f "$src" ]] && ! cmp -s "$src" "$dst"; then
    cp "$src" "$dst.new" && chmod +x "$dst.new" && mv "$dst.new" "$dst"
    log "self-deploy: updated $f in $STATE_DIR"
  fi
done

# shellcheck disable=SC1091
source "$VENV/bin/activate"
cd "$REPO"

# First run under local source of truth: build the DB from the committed
# snapshot, which carries the full history (including current_targets) —
# nothing is lost. Not a file copy: the snapshot carries no primary keys, so
# `seed` refills a proper schema instead (see parkrun_pipeline.py).
if [[ ! -f "$LOCAL_DB" ]]; then
  if ! PARKRUN_PIPELINE_DB="$LOCAL_DB" python parkrun_pipeline.py seed; then
    rm -f "$LOCAL_DB" "$LOCAL_DB.wal"
    log "ERROR: seeding the local DB from the committed snapshot failed"
    notify "parkrun refresh" "❌ Cannot seed local DB — see log"
    exit 1
  fi
fi

log "refresh starting (target $LOCAL_DB)"
if PARKRUN_PIPELINE_DB="$LOCAL_DB" python parkrun_pipeline.py refresh; then
  log "refresh OK"
else
  log "refresh FAILED (see pipeline output above)"
  notify "parkrun refresh" "❌ Refresh FAILED — see ~/Library/Logs/parkrun_refresh.log"
  exit 1
fi

# Delivery. Stamp only once the data is actually pushed, so a failed push
# leaves the weekend "uncovered" and the next slot / login catch-up retries.
if push_audit_files; then
  date +%s >"$STAMP"
  notify "parkrun refresh" "✅ parkrun data refreshed"
else
  notify "parkrun refresh" "❌ Refresh ran but push FAILED — hosted app is stale, see log"
  exit 1
fi

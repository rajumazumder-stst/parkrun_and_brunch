#!/usr/bin/env bash
#
# Master parkrun refresh — THE one way this Mac refreshes MotherDuck.
# Run it manually any time:
#   scripts/parkrun_refresh.sh          (or the deployed copy ~/.config/parkrun/parkrun_refresh.sh)
# The launchd scheduler (parkrun_autorefresh.sh) calls this same script, so
# manual and scheduled refreshes are one code path.
#
# What it does: token → git pull the agent clone (origin/main) → pipeline
# refresh straight into md:parkrun_snapshot → stamp success (so the scheduler
# knows the weekend is covered) → auto-commit + push the regenerated audit
# CSV/snapshot → macOS notification either way. The hosted app picks up new
# data within ~60s of success.
#
# Self-contained under ~/.config/parkrun (repo/ clone + venv/) because macOS
# TCC blocks launchd agents from reading ~/Documents. Token:
# ~/.config/motherduck/token (chmod 600). Log: ~/Library/Logs/parkrun_refresh.log
# (manual runs also print to the terminal).
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
TOKEN_FILE="$HOME/.config/motherduck/token"

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

# Best-effort: commit + push the regenerated audit CSV and fallback snapshot
# from the agent's own clone (never touches any ~/Documents working copy).
# A push failure is logged and notified but doesn't fail the refresh — the
# data is already live in MotherDuck.
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
    log "WARN: audit commit/push failed — commit data/ files manually"
    notify "parkrun refresh" "⚠️ Refresh OK but audit-file push failed — see log"
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

token=""
[[ -f "$TOKEN_FILE" ]] && token="$(cat "$TOKEN_FILE")"
if [[ -z "$token" ]]; then
  log "ERROR: no MotherDuck token at $TOKEN_FILE"
  notify "parkrun refresh" "❌ No MotherDuck token at ~/.config/motherduck/token — see log"
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
log "refresh starting (target md:parkrun_snapshot)"
if PARKRUN_PIPELINE_DB=md:parkrun_snapshot motherduck_token="$token" \
  python parkrun_pipeline.py refresh; then
  date +%s >"$STAMP"
  log "refresh OK"
  push_audit_files || true
  notify "parkrun refresh" "✅ parkrun data refreshed"
else
  log "refresh FAILED (see pipeline output above)"
  notify "parkrun refresh" "❌ Refresh FAILED — see ~/Library/Logs/parkrun_refresh.log"
  exit 1
fi

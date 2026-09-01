#!/usr/bin/env bash
#
# Keep the ~/Documents working copy aware of (and, when safe, level with)
# origin/main after a refresh.
#
# The scheduled refresh commits and pushes from its OWN clone under
# ~/.config/parkrun/repo, so the working copy you actually edit in silently
# falls behind origin/main — two or three `data: local refresh` commits by
# Monday. This closes that gap without ever touching work in progress.
#
# Usage — source it, then call the function:
#     source "$REPO/scripts/sync_working_copy.sh"
#     sync_working_copy              # fetch, and fast-forward if it is safe
#     sync_working_copy --fetch-only # fetch only, never move the branch
#
# Contract: EVERY path returns 0. Callers run under `set -euo pipefail` and a
# sync problem must never fail a refresh, block a push, or change an exit code.
#
# Known limitation (macOS TCC): launchd agents cannot read ~/Documents, so under
# the *scheduled* agent every git call here fails, gets logged and returns 0.
# It works on manual/terminal runs. That asymmetry is expected — see docs/DEPLOY.md.

# Log through the caller's log() when it has one (parkrun_refresh.sh), else
# stand alone (run_local.sh has no logger). The $BASH_VERSION guard matters:
# zsh has a `log` BUILTIN, which `declare -F` happily matches, and calling it
# fails with "too many arguments" if this file is ever sourced from a zsh shell.
_swc_log() {
  if [[ -n "${BASH_VERSION:-}" ]] && declare -F log >/dev/null 2>&1; then
    log "$*"
  else
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
  fi
}

sync_working_copy() {
  local fetch_only=0
  [[ "${1:-}" == "--fetch-only" ]] && fetch_only=1

  local wc="${PARKRUN_WORKING_COPY:-$HOME/Documents/repos/parkrun_and_brunch}"

  if [[ ! -d "$wc/.git" ]]; then
    _swc_log "working copy sync: skipped — $wc is not a git working copy"
    return 0
  fi

  # Always fetch: even when the branch can't move, fresh remote refs make
  # `git status` / run_local.sh's branch warning tell the truth.
  if ! git -C "$wc" fetch --quiet --all --prune 2>/dev/null; then
    _swc_log "working copy sync: fetch failed (offline, or TCC blocked ~/Documents) — skipped"
    return 0
  fi

  if (( fetch_only )); then
    _swc_log "working copy sync: fetched $wc (fetch-only)"
    return 0
  fi

  # Uncommitted work: fetch is safe, pull is not.
  local dirty
  dirty="$(git -C "$wc" status --porcelain 2>/dev/null)" || {
    _swc_log "working copy sync: cannot read status — fetched only"
    return 0
  }
  if [[ -n "$dirty" ]]; then
    _swc_log "working copy sync: uncommitted changes — fetched only, branch left alone"
    return 0
  fi

  # Only main is auto-advanced. `dev` is deliberately NOT: it may be checked
  # out in another worktree or mid-feature, and auto-moving it loses work.
  local branch
  branch="$(git -C "$wc" branch --show-current 2>/dev/null)" || branch=""
  if [[ "$branch" != "main" ]]; then
    _swc_log "working copy sync: on '${branch:-detached HEAD}', not main — fetched only"
    return 0
  fi

  if git -C "$wc" pull --ff-only --quiet 2>/dev/null; then
    _swc_log "working copy sync: $wc fast-forwarded to origin/main"
  else
    _swc_log "working copy sync: fast-forward refused (diverged?) — fetched only"
  fi
  return 0
}

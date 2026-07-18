#!/usr/bin/env bash
#
# Scheduling policy for the parkrun refresh (launchd-driven). The actual
# refresh lives in parkrun_refresh.sh (same directory) — this script only
# decides WHEN to run it.
#
# Modes (first argument):
#   scheduled  Run the refresh unconditionally. Invoked by launchd at the
#              weekend slots (Sat 14:30, Sun 11:00 local). If the Mac is asleep
#              at slot time, launchd fires this on next wake; if the Mac is
#              powered off, the slot is missed entirely (handled by `login`).
#   login      Invoked at login/agent-load. If the last successful refresh
#              predates the most recent scheduled slot (laptop was off all
#              weekend), show a dialog offering to refresh now. Otherwise exit
#              silently.
#   status     Print stamp/slot/verdict to stdout (manual diagnostics; no log).
#
# Log: ~/Library/Logs/parkrun_refresh.log. The stamp read here
# (~/.config/parkrun/last_refresh_epoch) is written by parkrun_refresh.sh on
# every successful refresh — manual runs count toward freshness too.
#
# Deployed (with the master script) to ~/.config/parkrun/ because macOS TCC
# blocks launchd agents from reading ~/Documents. After editing:
#   cp scripts/parkrun_refresh.sh scripts/parkrun_autorefresh.sh ~/.config/parkrun/
set -euo pipefail

STATE_DIR="$HOME/.config/parkrun"
MASTER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/parkrun_refresh.sh"
LOG="$HOME/Library/Logs/parkrun_refresh.log"
STAMP="$STATE_DIR/last_refresh_epoch"

# Weekend slots, local time. Keep in sync with the StartCalendarInterval
# entries in com.raju.parkrun-refresh-scheduled.plist.
SAT_SLOT="14:30"
SUN_SLOT="11:00"

mode="${1:-login}"
mkdir -p "$STATE_DIR"
[[ "$mode" != "status" ]] && exec >>"$LOG" 2>&1

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

# Most recent scheduled slot at or before now ($1 = epoch). Echoes its epoch,
# or 0 if none found in the last week (shouldn't happen).
last_slot_epoch() {
  local now=$1 d day dow ts
  for d in 0 1 2 3 4 5 6 7; do
    day=$(date -r $((now - d * 86400)) '+%Y-%m-%d')
    dow=$(date -r $((now - d * 86400)) '+%u')
    case $dow in
      6) ts=$(date -j -f '%Y-%m-%d %H:%M' "$day $SAT_SLOT" +%s) ;;
      7) ts=$(date -j -f '%Y-%m-%d %H:%M' "$day $SUN_SLOT" +%s) ;;
      *) continue ;;
    esac
    if ((ts <= now)); then
      echo "$ts"
      return
    fi
  done
  echo 0
}

now=$(date +%s)
slot=$(last_slot_epoch "$now")
stamp=$(cat "$STAMP" 2>/dev/null || echo 0)

case "$mode" in
  scheduled)
    log "scheduled slot fired — invoking refresh"
    exec /bin/bash "$MASTER"
    ;;

  login)
    if ((stamp >= slot)); then
      log "login check: fresh (last refresh $(date -r "$stamp" '+%F %H:%M')) — nothing to do"
      exit 0
    fi
    last_human="never"
    ((stamp > 0)) && last_human=$(date -r "$stamp" '+%a %d %b %H:%M')
    log "login check: STALE (last refresh: $last_human; missed slot: $(date -r "$slot" '+%a %d %b %H:%M')) — prompting"
    btn=$(/usr/bin/osascript \
      -e "display dialog \"parkrun data wasn't refreshed this weekend.\n\nLast refresh: $last_human\n\nRefresh now?\" with title \"parkrun refresh\" buttons {\"Later\", \"Refresh now\"} default button \"Refresh now\" giving up after 600" \
      -e 'button returned of result' 2>/dev/null || echo "Later")
    if [[ "$btn" == "Refresh now" ]]; then
      log "user chose: Refresh now — invoking refresh"
      exec /bin/bash "$MASTER"
    else
      log "user chose: Later (or dialog timed out) — will ask again next login"
    fi
    ;;

  status)
    echo "last scheduled slot : $(date -r "$slot" '+%a %F %H:%M')"
    if ((stamp > 0)); then
      echo "last refresh (stamp): $(date -r "$stamp" '+%a %F %H:%M')"
    else
      echo "last refresh (stamp): never"
    fi
    ((stamp >= slot)) && echo "verdict             : fresh" || echo "verdict             : STALE — login prompt would fire"
    ;;

  *)
    log "unknown mode: $mode"
    exit 2
    ;;
esac

#!/usr/bin/env bash
#
# Launch the Streamlit app locally against an ISOLATED dev database, so changes
# can be previewed in the browser without touching the deployed/committed state.
#
# What "live" is protected from:
#   - code:  you run this from a feature branch (e.g. `dev`), never `main`.
#   - data:  this points the app at data/parkrun_dev.duckdb — a gitignored,
#            parkrun-only copy of the tracked snapshot — so experiments (and the
#            new views built in later stages) never dirty data/parkrun_snapshot.duckdb
#            and never touch the personal-finance dev DB.
#
# DB precedence:
#   1. $PARKRUN_DB, if you've already set it (point it wherever you like)
#   2. data/parkrun_dev.duckdb  (auto-created here on first run from the snapshot)
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_DB="$REPO/data/parkrun_dev.duckdb"
SNAPSHOT="$REPO/data/parkrun_snapshot.duckdb"

# Use the project venv if present (has streamlit, duckdb, plotly, matplotlib-venn).
VENV="$HOME/Documents/Python scripts/env"
if [[ -f "$VENV/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi

# Warn (don't block) if run from main — the point is to keep main untouched.
branch="$(git -C "$REPO" branch --show-current 2>/dev/null || echo '?')"
if [[ "$branch" == "main" ]]; then
  echo "⚠️  You're on 'main'. Switch to a feature branch (e.g. 'git switch dev') so"
  echo "    local changes don't land on the deployable branch." >&2
fi

if [[ -z "${PARKRUN_DB:-}" ]]; then
  if [[ ! -f "$DEV_DB" ]]; then
    echo "→ creating isolated dev DB: data/parkrun_dev.duckdb (copy of the snapshot)"
    cp "$SNAPSHOT" "$DEV_DB"
  fi
  export PARKRUN_DB="$DEV_DB"
fi

echo "→ branch:     $branch"
echo "→ PARKRUN_DB: $PARKRUN_DB"
echo "→ opening http://localhost:8501 …"
exec streamlit run "$REPO/app.py"

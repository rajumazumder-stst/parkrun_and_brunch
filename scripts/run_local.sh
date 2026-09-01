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
# Also fetches (never pulls) so the branch report below is based on fresh
# remote refs — see scripts/sync_working_copy.sh.
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_DB="$REPO/data/parkrun_dev.duckdb"
APP_PORT="${PARKRUN_PORT:-8501}"
LABEL_PORT="${PARKRUN_LABEL_PORT:-8502}"
SNAPSHOT="$REPO/data/parkrun_snapshot.duckdb"

# Use the project venv if present (has streamlit, duckdb, plotly, matplotlib-venn).
VENV="$HOME/Documents/Python scripts/env"
if [[ -f "$VENV/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi

# Refresh remote refs so the branch report below reflects reality — fetch only,
# this must never move a branch out from under work in progress.
if [[ -r "$REPO/scripts/sync_working_copy.sh" ]]; then
  # shellcheck disable=SC1091
  source "$REPO/scripts/sync_working_copy.sh"
  PARKRUN_WORKING_COPY="$REPO" sync_working_copy --fetch-only
fi

# Warn (don't block) if run from main — the point is to keep main untouched.
branch="$(git -C "$REPO" branch --show-current 2>/dev/null || echo '?')"
if [[ "$branch" == "main" ]]; then
  echo "⚠️  You're on 'main'. Switch to a feature branch (e.g. 'git switch dev') so"
  echo "    local changes don't land on the deployable branch." >&2
fi

# The scheduled refresh pushes from its own clone, so this copy drifts behind.
behind="$(git -C "$REPO" rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
if [[ "$behind" != "0" ]]; then
  echo "ℹ️  $behind commit(s) behind origin/main (data refreshes pushed by the scheduler)."
fi

if [[ -z "${PARKRUN_DB:-}" ]]; then
  if [[ ! -f "$DEV_DB" ]]; then
    # Built through the pipeline, NOT `cp`: the committed snapshot carries the
    # views as they were when it was last rebuilt, so a plain copy would be
    # missing anything added since (and has no primary keys). `seed` runs
    # ensure_schema + ensure_migrations + ensure_views, then fills it row for
    # row from the snapshot.
    echo "→ creating isolated dev DB: data/parkrun_dev.duckdb (from the snapshot)"
    PARKRUN_PIPELINE_DB="$DEV_DB" python "$REPO/parkrun_pipeline.py" seed \
      || { echo "✗ could not seed the dev DB" >&2; rm -f "$DEV_DB" "$DEV_DB.wal"; exit 1; }
  fi
  export PARKRUN_DB="$DEV_DB"
fi

# The dev-only "Label impact" app needs the frozen pre-buggy views alongside
# the live ones. Never built on the source of truth or in the deploy snapshot.
if [[ "${PARKRUN_LABEL_AUDIT:-}" == "1" ]]; then
  PARKRUN_LABEL_AUDIT=1 python - "$PARKRUN_DB" <<'PYEOF' || echo "⚠️  could not build the legacy views" >&2
import sys, duckdb, parkrun_pipeline as p
con = duckdb.connect(sys.argv[1])
p.ensure_views(con)
p.ensure_legacy_views(con)
con.close()
PYEOF
fi

echo "→ branch:     $branch"
echo "→ PARKRUN_DB: $PARKRUN_DB"

# The label-impact comparison is a SEPARATE app on its own port: it is a
# development instrument, not part of the product, and keeping it out of the
# real app means there is no gate to get wrong at deploy time.
if [[ "${PARKRUN_LABEL_AUDIT:-}" == "1" ]]; then
  streamlit run "$REPO/label_impact.py" --server.port "$LABEL_PORT" \
    --server.headless true >"${TMPDIR:-/tmp}/parkrun_label_impact.log" 2>&1 &
  echo "→ label impact: http://localhost:$LABEL_PORT  (separate app)"
fi

echo "→ opening http://localhost:$APP_PORT …"
exec streamlit run "$REPO/app.py" --server.port "$APP_PORT"

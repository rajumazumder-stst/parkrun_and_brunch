#!/usr/bin/env bash
# Rebuild the whole furthest-pairs analysis end to end.
#
#   ./run_all.sh          # top 10 (the published result)
#   ./run_all.sh 25       # any N
#
# No network calls and no cache: everything comes from the local DuckDB
# snapshot, so a rerun is a few seconds.
set -euo pipefail

TOP="${1:-10}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/Documents/Python scripts/env"

if [[ -f "$VENV/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi

cd "$HERE"
echo "==> top $TOP furthest pairs"
python3 find_pairs.py --top "$TOP"
echo "==> map"
python3 build_map.py --top "$TOP"
echo "done — answer in ../results/, map in ../output/"

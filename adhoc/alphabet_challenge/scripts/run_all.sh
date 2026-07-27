#!/usr/bin/env bash
# Rebuild the whole alphabet-challenge analysis end to end.
#
#   ./run_all.sh          # top 20, 20 km separation (the published result)
#   ./run_all.sh 10 10    # top 10, 10 km separation
#
# OSRM responses are cached under .cache/, so a rerun after the first is fast
# and makes no network calls. Delete .cache/ to refetch (~630 requests).
set -euo pipefail

TOP="${1:-20}"
SEP="${2:-20}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/Documents/Python scripts/env"

if [[ -f "$VENV/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi

cd "$HERE"
echo "==> straight-line optimum"
python3 solve_air.py
echo "==> top $TOP by road (>= $SEP km apart)"
python3 solve_road.py --top "$TOP" --sep "$SEP"
echo "==> route geometry + ferry split"
python3 fetch_routes.py --top "$TOP"
echo "==> map"
python3 build_map.py --top "$TOP"
echo "done — see ../output/"

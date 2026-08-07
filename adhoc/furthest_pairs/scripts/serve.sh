#!/usr/bin/env bash
# Serve this topic's output/ over HTTP so the map opens as a real URL.
#
#   ./serve.sh              # localhost only (default)
#   ./serve.sh 8000 lan     # also reachable from other devices on the wifi
#
# Ctrl-C to stop. Binding to localhost by default keeps the file server off the
# network unless you ask for it.
set -euo pipefail

PORT="${1:-8000}"
BIND="127.0.0.1"
[[ "${2:-}" == "lan" ]] && BIND="0.0.0.0"

OUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../output" && pwd)"
MAP="$(cd "$OUT" && ls -1 furthest_pairs_map_top*.html 2>/dev/null | head -1)"

if [[ -z "$MAP" ]]; then
  echo "No map in $OUT — run ./run_all.sh first." >&2
  exit 1
fi

echo "serving $OUT"
echo "  http://localhost:$PORT/$MAP"
if [[ "$BIND" == "0.0.0.0" ]]; then
  IP="$(ipconfig getifaddr en0 2>/dev/null || echo '<your-ip>')"
  echo "  http://$IP:$PORT/$MAP   (same wifi)"
fi
echo
cd "$OUT"
exec python3 -m http.server "$PORT" --bind "$BIND"

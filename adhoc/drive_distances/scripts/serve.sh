#!/usr/bin/env bash
# Serve this topic's output/ over HTTP on localhost, so the browser sends a
# Referer and OpenStreetMap's own tiles load (?tiles=osm). Opened as a file,
# the page falls back on Esri tiles, which need no Referer.
#
#   ./serve.sh [PORT]
#
# Localhost only, deliberately: the page carries drive times from each
# athlete's neighbourhood, so there is no option to serve it to the network.
set -euo pipefail

PORT="${1:-8510}"
OUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../output" && pwd)"
PAGE="drive_distances.html"

if [[ ! -f "$OUT/$PAGE" ]]; then
  echo "No page in $OUT — run build_page.py first." >&2
  exit 1
fi

echo "serving $OUT"
echo "  http://localhost:$PORT/$PAGE             (Esri tiles)"
echo "  http://localhost:$PORT/$PAGE?tiles=osm   (OpenStreetMap tiles)"
echo
cd "$OUT"
exec python3 -m http.server "$PORT" --bind 127.0.0.1

"""Step 3 — full-detail geometry for every leg, split into driving vs ferry runs.

OSRM's car profile already routes over ferries and counts their distance; asking
for steps=true exposes each step's travel mode, so consecutive same-mode steps
can be merged into runs that the map draws solid (driving) or dashed (ferry).

Run:  python3 fetch_routes.py [--top 20]
"""
import argparse
import json

import osrm
from common import LETTERS, OUTPUT, RESULTS

ap = argparse.ArgumentParser()
ap.add_argument("--top", type=int, default=20)
args = ap.parse_args()

NON_ROAD = {"ferry", "shuttle_train", "train"}  # drawn dashed

top = json.loads((RESULTS / f"road_top{args.top}.json").read_text())
out, deviations, modes_seen = [], [], {}

for t in top:
    legs, route_sum = [], 0.0
    for L in LETTERS:
        d0 = t["detail"][L]
        data = osrm.route((t["lat"], t["lon"]), (d0["lat"], d0["lon"]))
        runs, drive_km, ferry_km, total_km = [], 0.0, 0.0, None
        if data and data.get("code") == "Ok":
            r = data["routes"][0]
            total_km = r["distance"] / 1000.0
            for leg in r["legs"]:
                for s in leg["steps"]:
                    mode = s.get("mode", "driving")
                    modes_seen[mode] = modes_seen.get(mode, 0) + 1
                    km = s["distance"] / 1000.0
                    if mode in NON_ROAD:
                        ferry_km += km
                    else:
                        drive_km += km
                    pts = s.get("geometry", {}).get("coordinates", [])
                    if len(pts) < 2:
                        continue
                    kind = "ferry" if mode in NON_ROAD else "drive"
                    if runs and runs[-1]["kind"] == kind:
                        runs[-1]["coords"].extend([[p[1], p[0]] for p in pts[1:]])
                    else:
                        runs.append({"kind": kind, "coords": [[p[1], p[0]] for p in pts]})
        if total_km is None:  # unroutable: fall back to the table-service figure
            total_km = d0["road_km"]
        route_sum += total_km
        deviations.append(abs(total_km - d0["road_km"]))
        legs.append({"letter": L, "short_name": d0["short_name"],
                     "location": d0["location"], "country_url": d0["country_url"],
                     "lat": d0["lat"], "lon": d0["lon"],
                     "road_km": round(total_km, 2), "air_km": round(d0["air_km"], 2),
                     "drive_km": round(drive_km, 2), "ferry_km": round(ferry_km, 2),
                     "runs": runs})
    ferry_total = sum(l["ferry_km"] for l in legs)
    out.append({"rank": t["rank"], "lat": t["lat"], "lon": t["lon"],
                "air_km": t["air_km"], "road_km": t["road_km"],
                "route_sum_km": round(route_sum, 1), "ferry_km": round(ferry_total, 1),
                "legs": legs})
    print(f"#{t['rank']:2}  {t['lat']:8.4f},{t['lon']:9.4f}  road {t['road_km']:8.1f} km  "
          f"ferry {ferry_total:6.1f} km  legs with ferry: "
          f"{sum(1 for l in legs if l['ferry_km'] > 0)}")

path = OUTPUT / f"routes_top{args.top}.json"
path.write_text(json.dumps(out))
print(f"\nstep modes seen: {modes_seen}")
# consistency check: the route service should agree with the table service the
# ranking was built from.
print(f"max |route service - table service| per leg: {max(deviations):.3f} km")
print(f"wrote {path}")

"""Step 2 — the top N base points by real road distance.

Candidates are generated on a land grid, deduplicated to a minimum separation,
refined locally, then routed **in air-distance order**. Because air distance is
a strict lower bound on road distance, the search can stop the moment the
Nth-best road total falls below the next candidate's air total: no unrouted
point can then qualify. That makes the resulting list provably complete at
region level.

Run:  python3 solve_road.py [--top 20] [--sep 20]
"""
import argparse
import json

import numpy as np
from global_land_mask import globe

import osrm
from common import (LETTERS, R_EARTH, RESULTS, air_total, build_groups,
                    haversine, load_events, unit)

ap = argparse.ArgumentParser()
ap.add_argument("--top", type=int, default=20, help="how many base points to report")
ap.add_argument("--sep", type=float, default=20.0, help="min km between reported points")
ap.add_argument("--per-letter", type=int, default=4,
                help="nearest events per letter offered to the router")
ap.add_argument("--pool", type=int, default=400, help="candidate pool size")
args = ap.parse_args()

ev = load_events()
groups = build_groups(ev)
print(f"events in play: {len(ev)}")

# ---- candidate generation --------------------------------------------------
print("stage 1: global land grid @ 0.25 deg ...")
step = 0.25
LA, LO = np.meshgrid(np.arange(-60, 75 + step, step), np.arange(-180, 180, step),
                     indexing="ij")
LA, LO = LA.ravel(), LO.ravel()
mask = globe.is_land(np.clip(LA, -89.9, 89.9), LO)
LA, LO = LA[mask], LO[mask]
vals = air_total(unit(LA, LO), groups)
print(f"  land cells: {len(LA):,}")

coarse = []
for idx in np.argsort(vals)[:40000]:
    la, lo = float(LA[idx]), float(LO[idx])
    if all(haversine(la, lo, c[0], c[1]) >= args.sep for c in coarse):
        coarse.append((la, lo, float(vals[idx])))
    if len(coarse) >= args.pool:
        break
print(f"  coarse candidates >= {args.sep:g} km apart: {len(coarse)}")

print("stage 1b: local refinement ...")
refined = []
for la, lo, _ in coarse:
    s = 0.005
    A, O = np.meshgrid(np.arange(la - 0.125, la + 0.125 + s, s),
                       np.arange(lo - 0.125, lo + 0.125 + s, s), indexing="ij")
    A, O = A.ravel(), O.ravel()
    k = globe.is_land(np.clip(A, -89.9, 89.9), O)
    A, O = A[k], O[k]
    if len(A) == 0:
        continue
    v = air_total(unit(A, O), groups)
    j = int(np.argmin(v))
    refined.append((float(A[j]), float(O[j]), float(v[j])))

refined.sort(key=lambda r: r[2])
cands = []
for la, lo, v in refined:
    if all(haversine(la, lo, c[0], c[1]) >= args.sep for c in cands):
        cands.append((la, lo, v))
print(f"  refined candidates: {len(cands)}  air {cands[0][2]:.0f}-{cands[-1][2]:.0f} km")

# ---- route in air order, stop when the bound closes the list ---------------
print(f"stage 2: routing until the top {args.top} is provably closed ...")
results = []
for n, (la, lo, air) in enumerate(cands, 1):
    results.sort(key=lambda r: r["road_km"])
    if len(results) >= args.top and results[args.top - 1]["road_km"] <= air:
        print(f"  stop at candidate {n}: {args.top}th road "
              f"{results[args.top-1]['road_km']:.1f} km <= next candidate's air "
              f"{air:.1f} km -> no unrouted point can qualify")
        break

    P = unit(np.array([la]), np.array([lo]))
    dests, meta = [], []
    for L in LETTERS:
        V, idx = groups[L]
        d = R_EARTH * np.arccos(np.clip(P @ V.T, -1, 1))[0]
        for j in np.argsort(d)[: args.per_letter]:
            e = ev.loc[idx[j]]
            dests.append((float(e["latitude"]), float(e["longitude"])))
            meta.append((L, e["short_name"], e["location"], e["country_url"],
                         float(d[j]), float(e["latitude"]), float(e["longitude"])))

    per_letter = {}
    for (L, sn, loc, cu, air_km, dla, dlo), dist in zip(meta, osrm.table((la, lo), dests)):
        if dist is None:
            continue
        if L not in per_letter or dist < per_letter[L]["road_km"]:
            per_letter[L] = {"road_km": dist / 1000.0, "short_name": sn,
                             "location": loc, "country_url": cu,
                             "air_km": air_km, "lat": dla, "lon": dlo}
    if len(per_letter) < len(LETTERS):
        print(f"  [{n:3}] {la:8.4f},{lo:9.4f}  unroutable "
              f"{sorted(set(LETTERS) - set(per_letter))} -> skipped")
        continue

    total = sum(v["road_km"] for v in per_letter.values())
    results.append({"lat": la, "lon": lo, "air_km": air, "road_km": total,
                    "detail": per_letter})
    print(f"  [{n:3}] {la:8.4f},{lo:9.4f}  air {air:7.1f}  road {total:8.1f} km")

results.sort(key=lambda r: r["road_km"])
top = results[: args.top]
for i, r in enumerate(top, 1):
    r["rank"] = i

path = RESULTS / f"road_top{args.top}.json"
path.write_text(json.dumps(top, indent=1))

print("\n" + "=" * 76)
print(f"TOP {args.top} BY ROAD DISTANCE (all pairs >= {args.sep:g} km apart)")
print("=" * 76)
for r in top:
    far = max(r["detail"].items(), key=lambda kv: kv[1]["road_km"])
    print(f"{r['rank']:2}. {r['lat']:9.4f},{r['lon']:10.4f}  road {r['road_km']:8.1f} km  "
          f"air {r['air_km']:7.1f}  x{r['road_km']/r['air_km']:.2f}  "
          f"worst {far[0]}={far[1]['short_name'][:22]} {far[1]['road_km']:.0f} km")
print(f"\nwrote {path}")

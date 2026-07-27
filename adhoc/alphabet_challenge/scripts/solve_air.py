"""Step 1 — the straight-line (great-circle) optimum.

Finds the point minimising the total one-way distance to one live parkrun per
letter, both unconstrained and constrained to land. Non-convex (a sum of
min-distances), so: coarse global sweep -> refine the best basins -> fine pass
-> continuous polish.

Run:  python3 solve_air.py
"""
import json

import numpy as np
from global_land_mask import globe
from scipy.optimize import minimize

from common import (LETTERS, R_EARTH, RESULTS, air_total, build_groups,
                    haversine, load_events, unit)

ev = load_events()
groups = build_groups(ev)
print(f"events in play: {len(ev)}   letters: {len(LETTERS)}")


def f_scalar(x):
    return float(air_total(unit(np.array([x[0]]), np.array([x[1]])), groups)[0])


def grid(lat_lo, lat_hi, lon_lo, lon_hi, step, land_only):
    lats = np.arange(lat_lo, lat_hi + step / 2, step)
    lons = np.arange(lon_lo, lon_hi + step / 2, step)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    LA, LO = LA.ravel(), LO.ravel()
    if land_only:
        m = globe.is_land(np.clip(LA, -89.99, 89.99), ((LO + 180) % 360) - 180)
        LA, LO = LA[m], LO[m]
        if len(LA) == 0:
            return None
    return LA, LO, air_total(unit(LA, LO), groups)


def solve(land_only):
    LA, LO, vals = grid(-60, 75, -180, 180, 0.5, land_only)
    order = np.argsort(vals)[:300]
    best = (float(vals[order[0]]), float(LA[order[0]]), float(LO[order[0]]))
    for idx in order:  # refine every promising basin, not just the best cell
        la, lo = float(LA[idx]), float(LO[idx])
        r = grid(la - 0.5, la + 0.5, lo - 0.5, lo + 0.5, 0.02, land_only)
        if r is None:
            continue
        a, o, v = r
        j = int(np.argmin(v))
        if v[j] < best[0]:
            best = (float(v[j]), float(a[j]), float(o[j]))
    _, bla, blo = best
    r = grid(bla - 0.03, bla + 0.03, blo - 0.03, blo + 0.03, 0.0005, land_only)
    if r is not None:
        a, o, v = r
        j = int(np.argmin(v))
        if v[j] < best[0]:
            best = (float(v[j]), float(a[j]), float(o[j]))
    if not land_only:
        res = minimize(f_scalar, [best[1], best[2]], method="Nelder-Mead",
                       options={"xatol": 1e-9, "fatol": 1e-9, "maxiter": 20000})
        if res.fun < best[0]:
            best = (float(res.fun), float(res.x[0]), float(res.x[1]))
    return best


def breakdown(lat, lon):
    P = unit(np.array([lat]), np.array([lon]))
    rows = []
    for L in LETTERS:
        V, idx = groups[L]
        d = R_EARTH * np.arccos(np.clip(P @ V.T, -1, 1))[0]
        j = int(np.argmin(d))
        e = ev.loc[idx[j]]
        rows.append({"letter": L, "short_name": e["short_name"],
                     "location": e["location"], "country_url": e["country_url"],
                     "lat": float(e["latitude"]), "lon": float(e["longitude"]),
                     "air_km": round(float(d[j]), 2)})
    return sorted(rows, key=lambda r: r["letter"])


out = {}
for land_only in (False, True):
    total, lat, lon = solve(land_only)
    key = "land" if land_only else "unconstrained"
    rows = breakdown(lat, lon)
    out[key] = {"lat": lat, "lon": lon, "air_km": round(total, 1),
                "on_land": bool(globe.is_land(lat, lon)), "legs": rows}
    print(f"\n=== {key.upper()} ===")
    print(f"lat {lat:.6f}  lon {lon:.6f}   total {total:,.1f} km   "
          f"on land: {out[key]['on_land']}")
    for r in rows:
        print(f"  {r['letter']}  {r['air_km']:8.2f} km  {r['short_name']:<28} "
              f"{str(r['location'])[:38]:<38} {r['country_url']}")

sep = haversine(out["unconstrained"]["lat"], out["unconstrained"]["lon"],
                out["land"]["lat"], out["land"]["lon"])
print(f"\nunconstrained vs land optimum: {sep*1000:.0f} m apart")

(RESULTS / "air_optimum.json").write_text(json.dumps(out, indent=1))
print(f"wrote {RESULTS / 'air_optimum.json'}")

"""Step 1 — the top N pairs of live parkruns furthest apart on the ground.

Brute force over every pair: 2,374 events is 2.8 M pairs, which is a few
hundred MB of float64 and a couple of seconds — no spatial index needed, and
the exhaustive scan means the answer is exact by construction.

Run:  python3 find_pairs.py [--top 10]
Env:  PARKRUN_DB=<path>   (default: the repo's read-only snapshot)
"""
import argparse

import numpy as np
import pandas as pd

from common import ANTIPODAL_KM, PAIRS_CSV, R_EARTH, load_events, unit

ap = argparse.ArgumentParser()
ap.add_argument("--top", type=int, default=10)
args = ap.parse_args()

ev = load_events()
n = len(ev)
print(f"{n:,} live main-series events -> {n * (n - 1) // 2:,} pairs")

# Great-circle distance from the dot product of unit vectors. arccos loses
# precision for *near* points; here everything of interest is half a world away,
# where arccos is at its most accurate.
V = unit(ev["latitude"].to_numpy(), ev["longitude"].to_numpy())
D = R_EARTH * np.arccos(np.clip(V @ V.T, -1.0, 1.0))

iu = np.triu_indices(n, k=1)  # each unordered pair once, no self-pairs
d = D[iu]
top = np.argpartition(d, -args.top)[-args.top:]
top = top[np.argsort(-d[top])]

rows = []
for rank, k in enumerate(top, start=1):
    a, b = ev.iloc[iu[0][k]], ev.iloc[iu[1][k]]
    rows.append({
        "rank": rank,
        "distance_km": round(float(d[k]), 3),
        "pct_of_antipodal": round(float(d[k]) / ANTIPODAL_KM * 100, 2),
        "event_a_id": int(a.event_id), "event_a_name": a.short_name,
        "event_a_location": a.location, "event_a_country": a.country,
        "event_a_lat": a.latitude, "event_a_lon": a.longitude,
        "event_b_id": int(b.event_id), "event_b_name": b.short_name,
        "event_b_location": b.location, "event_b_country": b.country,
        "event_b_lat": b.latitude, "event_b_lon": b.longitude,
    })

out = pd.DataFrame(rows)
out.to_csv(PAIRS_CSV, index=False)
print(out[["rank", "distance_km", "event_a_name", "event_b_name"]].to_string(index=False))
print(f"\nwrote {PAIRS_CSV}")

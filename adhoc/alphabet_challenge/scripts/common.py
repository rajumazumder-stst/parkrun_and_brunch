"""Shared paths, data loading and spherical geometry for the alphabet challenge.

Everything resolves relative to this topic folder, so the scripts run from any
working directory.
"""
import os
from pathlib import Path

import duckdb
import numpy as np

TOPIC = Path(__file__).resolve().parents[1]
REPO = TOPIC.parents[1]
RESULTS = TOPIC / "results"
OUTPUT = TOPIC / "output"
CACHE = TOPIC / ".cache"
for d in (RESULTS, OUTPUT, CACHE):
    d.mkdir(exist_ok=True)

# Mirrors app.py's resolution order: explicit env var wins, else the tracked
# read-only snapshot in the repo (parkrun-only, so this never touches the dev DB).
DB = os.environ.get("PARKRUN_DB") or str(REPO / "data" / "parkrun_snapshot.duckdb")

R_EARTH = 6371.0088  # mean Earth radius, km

# X is excluded: no live parkrun starts with it.
LETTERS = list("ABCDEFGHIJKLMNOPQRSTUVWYZ")


def load_events():
    """Live, main-series (seriesid=1) events, one row per event, letter tagged.

    The letter is the *literal* first character of short_name — no accent
    folding and no stripping of a leading "The". Events whose initial is not a
    plain A-Z (10 of them: Ō, Ś, Ż, Ł, Ö) therefore drop out entirely.
    """
    con = duckdb.connect(DB, read_only=True)
    ev = con.sql("""
        select event_id, short_name, location, country_url, latitude, longitude
        from parkrun.events
        where live and seriesid = 1
          and latitude is not null and longitude is not null
    """).df()
    con.close()
    ev["letter"] = ev["short_name"].str.strip().str[0].str.upper()
    ev = ev[ev["letter"].isin(LETTERS)].reset_index(drop=True)
    missing = [L for L in LETTERS if L not in set(ev["letter"])]
    if missing:
        raise SystemExit(f"no live event for letter(s): {missing}")
    return ev


def build_groups(ev):
    """letter -> (unit vectors of that letter's events, their row indices)."""
    groups = {}
    for L in LETTERS:
        sub = ev[ev["letter"] == L]
        groups[L] = (unit(sub["latitude"].to_numpy(), sub["longitude"].to_numpy()),
                     sub.index.to_numpy())
    return groups


def unit(lat_deg, lon_deg):
    """(lat, lon) in degrees -> (n, 3) unit vectors on the sphere."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    return np.column_stack(
        [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]
    )


def air_total(P, groups, chunk=20000):
    """Sum over letters of the great-circle distance to that letter's nearest event.

    This is a strict lower bound on the equivalent road total, which is what
    makes the top-N search in solve_road.py provably complete.
    """
    out = np.zeros(len(P))
    for i in range(0, len(P), chunk):
        blk = P[i : i + chunk]
        s = np.zeros(len(blk))
        for L in LETTERS:
            s += R_EARTH * np.arccos(np.clip(blk @ groups[L][0].T, -1, 1)).min(axis=1)
        out[i : i + chunk] = s
    return out


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians([lat1, lon1]), np.radians([lat2, lon2])
    d = (np.sin((p2[0] - p1[0]) / 2) ** 2
         + np.cos(p1[0]) * np.cos(p2[0]) * np.sin((p2[1] - p1[1]) / 2) ** 2)
    return 2 * R_EARTH * np.arcsin(np.sqrt(d))

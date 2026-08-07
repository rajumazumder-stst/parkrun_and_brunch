"""Shared paths, data loading and spherical geometry for the furthest-pairs topic.

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
for d in (RESULTS, OUTPUT):
    d.mkdir(exist_ok=True)

# Mirrors app.py's resolution order: explicit env var wins, else the tracked
# read-only snapshot in the repo (parkrun-only, so this never touches the dev DB).
DB = os.environ.get("PARKRUN_DB") or str(REPO / "data" / "parkrun_snapshot.duckdb")

R_EARTH = 6371.0088  # mean Earth radius, km
ANTIPODAL_KM = np.pi * R_EARTH  # 20,015 km — the furthest two points can be

PAIRS_CSV = RESULTS / "furthest_pairs_top10.csv"


def load_events():
    """Live, main-series (seriesid=1) events with coordinates, country resolved.

    Defunct events (live = FALSE, including the manual Victoria Dock row) are
    excluded: the question is which two parkruns you could run today are
    furthest apart.
    """
    con = duckdb.connect(DB, read_only=True)
    ev = con.sql("""
        select e.event_id, e.short_name, e.location,
               coalesce(c.country_name, 'Unknown') as country,
               e.latitude, e.longitude
        from parkrun.events e
        left join parkrun.country_lookup c using (country_code)
        where e.live and e.seriesid = 1
          and e.latitude is not null and e.longitude is not null
        order by e.event_id
    """).df()
    con.close()
    return ev


def unit(lat_deg, lon_deg):
    """(lat, lon) in degrees -> (n, 3) unit vectors on the sphere."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    return np.column_stack(
        [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]
    )


def great_circle_points(lat1, lon1, lat2, lon2, n=200):
    """The great-circle path between two points, as [(lat, lon), ...].

    Slerp between the endpoints' unit vectors — the arc a plane would fly, which
    on a Mercator map is a curve, not the straight line a naive PolyLine draws.
    """
    a, b = unit([lat1], [lon1])[0], unit([lat2], [lon2])[0]
    omega = np.arccos(np.clip(a @ b, -1, 1))
    t = np.linspace(0, 1, n)[:, None]
    if omega < 1e-9:
        pts = np.repeat(a[None, :], n, axis=0)
    else:
        # Near-antipodal pairs have an ill-conditioned slerp (any great circle
        # joins them) but sin(omega) is still non-zero, so the formula holds.
        pts = (np.sin((1 - t) * omega) * a + np.sin(t * omega) * b) / np.sin(omega)
    pts /= np.linalg.norm(pts, axis=1, keepdims=True)
    lat = np.degrees(np.arcsin(np.clip(pts[:, 2], -1, 1)))
    lon = np.degrees(np.arctan2(pts[:, 1], pts[:, 0]))
    return list(zip(lat.tolist(), lon.tolist()))


def clip_mercator(points, limit=85.0):
    """Drop vertices outside Web Mercator's latitude range.

    Every one of these arcs is near-antipodal, so it passes within a degree or
    two of a pole — which Mercator cannot draw at all (y goes to infinity at
    ±90°). Left in, those vertices project to a spike off the bottom of the map.
    Dropped, each arc simply runs off the map edge, which is the truth.
    """
    return [p for p in points if abs(p[0]) <= limit]


def split_antimeridian(points):
    """Split a path into segments wherever it crosses ±180° longitude.

    Leaflet interpolates between consecutive vertices in map space, so a jump
    from +179 to -179 is drawn as a line straight back across the whole world.
    """
    if not points:
        return []
    segs, cur = [], [points[0]]
    for prev, nxt in zip(points, points[1:]):
        if abs(nxt[1] - prev[1]) > 180:
            segs.append(cur)
            cur = [nxt]
        else:
            cur.append(nxt)
    segs.append(cur)
    return [s for s in segs if len(s) > 1]

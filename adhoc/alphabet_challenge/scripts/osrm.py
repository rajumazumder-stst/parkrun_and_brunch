"""Cached OSRM client.

Every distinct query is fetched once and stored under .cache/, so reruns are
free and the public demo server is never asked the same thing twice. Delete
.cache/ to force a refetch (~630 requests for the full pipeline).
"""
import hashlib
import json
import time
import urllib.request

from common import CACHE

SERVER = "https://router.project-osrm.org"
PAUSE_S = 0.35  # courtesy spacing between live requests
CACHE_DIR = CACHE / "osrm"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get(url, tries=4, timeout=120):
    path = CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json")
    if path.exists():
        return json.loads(path.read_text())
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.loads(r.read())
            path.write_text(json.dumps(data))
            time.sleep(PAUSE_S)
            return data
        except Exception as e:  # transient: back off and retry
            last = e
            time.sleep(2 + 3 * attempt)
    print(f"    ! OSRM failed ({last}) for {url[:110]}")
    return None


def table(src, dests, chunk=49):
    """One source to many destinations -> list of road metres (None if unroutable).

    The demo server caps table size, hence the chunking.
    """
    out = []
    for i in range(0, len(dests), chunk):
        block = dests[i : i + chunk]
        coords = ";".join(f"{lo:.6f},{la:.6f}" for la, lo in [src] + block)
        d = get(f"{SERVER}/table/v1/driving/{coords}?sources=0&annotations=distance")
        if d and d.get("code") == "Ok":
            out.extend(d["distances"][0][1:])
        else:
            out.extend([None] * len(block))
    return out


def route(src, dst):
    """Full-detail route with per-step travel modes (so ferries can be split out)."""
    return get(f"{SERVER}/route/v1/driving/"
               f"{src[1]:.6f},{src[0]:.6f};{dst[1]:.6f},{dst[0]:.6f}"
               f"?steps=true&geometries=geojson&overview=false")

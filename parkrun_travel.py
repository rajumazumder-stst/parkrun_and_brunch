"""Driving time and distance from each athlete's home to every mainland-GB
parkrun — the data behind the tab 7 planner.

Run by hand, on the Mac only:

    PARKRUN_PIPELINE_DB=<target> python parkrun_pipeline.py travel \\
        [--athlete ID] [--force]

**Home coordinates never leave this machine except as the origin of a routing
request.** They are read from a file outside the repo (`HOMES_FILE`), are never
logged, and are not stored in the database — `travel_times` holds only the
result of each route and the *destination's* coordinates. The table is also
deliberately absent from `SNAPSHOT_TABLES`, so none of it reaches git or the
hosted app until a decision is made about exposing it (TODO.md).

The work is a one-off bulk route plus small top-ups. A pair is (re)routed when
it has no row, when the event has moved more than
`MOVE_THRESHOLD_M` since it was routed, or when `--force` says the home moved.
A network failure is logged and the pair is simply left for the next run.

The router is OpenRouteService (`ors`), with a free key read from
`ORS_API_KEY` or `ORS_KEY_FILE`. The public OSRM demo server was built beside
it and compared on 30 Sep 2026, then removed: the two agree on distance to
within a rounding error, but for trips under an hour OSRM ran a median 3.4
min (10%) faster — optimistic for London, where nearly all of these trips are
— and its server is a demo with no uptime promise. `provider` stays a column,
and part of the key, so a future switch is a new value, not a migration.

Neither router can tell us what "mainland" means (see `parkrun_core.NON_MAINLAND`),
so only events `parkrun_core.is_mainland` admits are routed at all.

Imports `requests`; never imported by the app.
"""

from __future__ import annotations

import csv
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
import requests

from parkrun_core import UK_COUNTRY_CODE, is_mainland

SCHEMA = "parkrun"
CONFIG_DIR = Path.home() / ".config" / "parkrun"
HOMES_FILE = Path(os.environ.get("PARKRUN_HOMES", CONFIG_DIR / "homes.csv"))
ORS_KEY_FILE = CONFIG_DIR / "ors_key"
PROVIDER = "ors"

# An event whose published coordinates move further than this since it was
# routed is routed again. Small enough to catch a course moving to the other
# side of a park, large enough that coordinate rounding in events.json never
# triggers it.
MOVE_THRESHOLD_M = 200

ORS_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"
ORS_CHUNK = 500
ORS_DELAY_SECONDS = 1.6    # free tier: 40 matrix requests a minute
TIMEOUT_SECONDS = 60
USER_AGENT = "parkrun-and-brunch/1.0 (personal project; github.com/rajumazumder-stst)"


Log = Callable[[str], None]


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def load_homes(path: Path = HOMES_FILE) -> dict[int, tuple[float, float]]:
    """{athlete_id: (lat, lon)} from a CSV with athlete_id,latitude,longitude.

    Returns {} when the file is absent — that is the normal state on any
    machine but the Mac that runs the refresh.

    A bad row raises with its line number only. Python's own message would
    quote the offending text — `could not convert string to float: '51.4x'`,
    a home coordinate — and a traceback reaches the log. `from None` drops
    that chained exception too. Out-of-range values are refused here for the
    same reason: ORS answers one with an error that repeats the coordinate.
    """
    if not path.exists():
        return {}
    homes = {}
    with path.open(newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            try:
                aid = int(row["athlete_id"])
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    f"{path.name} line {line}: needs numeric athlete_id, "
                    "latitude, longitude") from None
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"{path.name} line {line}: latitude or "
                                 "longitude out of range") from None
            homes[aid] = (lat, lon)
    return homes


def ors_key() -> str | None:
    key = os.environ.get("ORS_API_KEY")
    if key:
        return key.strip()
    if ORS_KEY_FILE.exists():
        return ORS_KEY_FILE.read_text().strip() or None
    return None


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def candidate_events(con) -> pd.DataFrame:
    """Live 5k events on mainland GB, with their current coordinates."""
    ev = con.execute(
        f"""
        SELECT event_id, latitude, longitude
        FROM {SCHEMA}.events
        WHERE seriesid = 1 AND live AND country_code = {UK_COUNTRY_CODE}
          AND latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY event_id
        """
    ).fetchdf()
    keep = pd.Series([is_mainland(la, lo) for la, lo in
                      zip(ev["latitude"], ev["longitude"])],
                     index=ev.index, dtype=bool)
    return ev[keep].reset_index(drop=True)


def pairs_to_route(candidates: pd.DataFrame, existing: pd.DataFrame,
                   athlete_ids, provider: str, force: bool = False) -> pd.DataFrame:
    """The (athlete_id, event_id, latitude, longitude) pairs that need routing.

    `existing` is travel_times (any providers). A pair is routed when it has no
    row for this provider, or the event has moved more than MOVE_THRESHOLD_M
    since, or `force` is set (the athlete moved)."""
    out = []
    ex = existing[existing["provider"] == provider].set_index(["athlete_id", "event_id"])
    for aid in athlete_ids:
        for ev in candidates.itertuples(index=False):
            key = (aid, ev.event_id)
            if not force and key in ex.index:
                old = ex.loc[key]
                moved = haversine_m(old["dest_lat"], old["dest_lon"],
                                    ev.latitude, ev.longitude)
                if moved <= MOVE_THRESHOLD_M:
                    continue
            out.append((aid, ev.event_id, ev.latitude, ev.longitude))
    return pd.DataFrame(out, columns=["athlete_id", "event_id", "latitude", "longitude"])


# --------------------------------------------------------------------------- #
# Providers — each returns [(duration_s, distance_m) | None] in dests order
# --------------------------------------------------------------------------- #
def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _ors_error_code(r) -> str:
    try:
        return str(int(r.json()["error"]["code"]))
    except Exception:  # noqa: BLE001 — any shape of body: just say unknown
        return "unknown"


_COORD = re.compile(r"-?\d{1,3}\.\d{2,}")


def scrub(text: str) -> str:
    """Blank anything shaped like a coordinate (2+ decimal places) out of a
    message before it is logged — a last line of defence for exception text
    this module did not write, such as a requests error."""
    return _COORD.sub("…", text)


def route_ors(origin, dests, key, session=None, sleep=time.sleep) -> list:
    session = session or _session()
    out = []
    for i, chunk in enumerate(_chunks(list(dests), ORS_CHUNK)):
        if i:
            sleep(ORS_DELAY_SECONDS)
        locs = [[lon, lat] for lat, lon in [origin, *chunk]]
        r = session.post(
            ORS_URL,
            json={"locations": locs, "sources": [0],
                  "destinations": list(range(1, len(locs))),
                  "metrics": ["duration", "distance"], "units": "m"},
            headers={"Authorization": key},
            timeout=TIMEOUT_SECONDS,
        )
        if r.status_code != 200:
            # Never the body: ORS repeats the offending coordinate in its error
            # text ("Source point(s) [0] out of bounds: 51.4,-0.2"), and point
            # 0 is a home. The status and ORS's numeric error code are enough
            # to look the failure up.
            raise RuntimeError(f"ORS HTTP {r.status_code}, error code "
                               f"{_ors_error_code(r)}")
        body = r.json()
        dur, dist = body["durations"][0], body["distances"][0]
        out.extend(None if d is None or m is None else (d, m)
                   for d, m in zip(dur, dist))
    return out


# --------------------------------------------------------------------------- #
# The update
# --------------------------------------------------------------------------- #
def ensure_table(con) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.travel_times (
            athlete_id BIGINT,
            event_id   INTEGER,
            provider   VARCHAR,      -- 'ors'
            duration_s DOUBLE,       -- NULL when the provider found no route
            distance_m DOUBLE,
            reachable  BOOLEAN NOT NULL,
            dest_lat   DOUBLE,       -- the EVENT's coordinates when routed
            dest_lon   DOUBLE,
            routed_at  TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (athlete_id, event_id, provider)
        );
        """
    )


def update_travel_times(con, athletes=None, force: bool = False,
                        log: Log = print, homes: dict | None = None) -> int:
    """Route whatever is missing or stale; return the number of rows written.

    Never raises for a routing failure — the refresh must not die for an
    optional feature, and a missing row is simply retried next time."""
    ensure_table(con)
    key = ors_key()
    if not key:
        log(f"travel: no ORS_API_KEY and no {ORS_KEY_FILE} — skipped")
        return 0
    homes = load_homes() if homes is None else homes
    if not homes:
        log(f"travel: no homes file at {HOMES_FILE} — skipped")
        return 0
    ids = [a for a in homes if athletes is None or a in athletes]
    if not ids:
        log("travel: none of the requested athletes has a home — skipped")
        return 0

    cand = candidate_events(con)
    existing = con.execute(
        f"SELECT athlete_id, event_id, provider, dest_lat, dest_lon "
        f"FROM {SCHEMA}.travel_times"
    ).fetchdf()
    log(f"travel: {len(cand)} mainland GB events, {len(ids)} home(s)")

    written = 0
    todo = pairs_to_route(cand, existing, ids, PROVIDER, force)
    if todo.empty:
        log("travel: up to date")
        return 0
    for aid, grp in todo.groupby("athlete_id"):
        dests = list(zip(grp["latitude"], grp["longitude"]))
        try:
            got = route_ors(homes[aid], dests, key)
        except Exception as e:  # noqa: BLE001 — logged, retried next run
            log(f"travel: failed for athlete {aid}: "
                f"{type(e).__name__}: {scrub(str(e))}")
            continue
        now = datetime.now(timezone.utc)
        rows = [
            (int(aid), int(ev), PROVIDER,
             None if g is None else float(g[0]),
             None if g is None else float(g[1]),
             g is not None, float(la), float(lo), now)
            for ev, la, lo, g in zip(grp["event_id"], grp["latitude"],
                                     grp["longitude"], got)
        ]
        con.executemany(
            f"""
            INSERT OR REPLACE INTO {SCHEMA}.travel_times
                (athlete_id, event_id, provider, duration_s, distance_m,
                 reachable, dest_lat, dest_lon, routed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        n_none = sum(1 for g in got if g is None)
        log(f"travel: athlete {aid}: {len(rows)} routed"
            + (f", {n_none} with no route" if n_none else ""))
        written += len(rows)
    return written

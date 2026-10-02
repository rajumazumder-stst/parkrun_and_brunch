"""Build a standalone HTML page of each athlete's drive to every live parkrun.

One row per live main-series parkrun (~2,400), a drive time and distance per
athlete, a Leaflet map coloured by drive time, and a miles/km toggle. Every
time comes from `travel_times`, which since 2 Oct 2026 routes Northern
Ireland, the islands, Ireland and Europe as well as mainland GB (the app
adopted this topic's approach; `route_crossings.py` and its cache retired).
Parkruns across the Channel Tunnel or a ferry are flagged
(`parkrun_core.crosses_water`), because their times leave out check-in and
waiting. Parkruns with no road route at all (Australia, the US, ...) are
listed with blanks.

Reads `travel_times` from the database `parkrun_core.resolve_db()` picks (local
source of truth, else the committed snapshot). The page carries drive times
from each athlete's neighbourhood centroid, so it is written to `output/`,
which is gitignored — never commit or publish it without deciding to.

    python adhoc/drive_distances/scripts/build_page.py
"""
import json
import sys
from pathlib import Path

import duckdb

TOPIC = Path(__file__).resolve().parents[1]
REPO = TOPIC.parents[1]
sys.path.insert(0, str(REPO))
from parkrun_core import ATHLETE_NAMES, crosses_water, resolve_db  # noqa: E402
from parkrun_ui import ATHLETE_COLORS  # noqa: E402

OUTPUT = TOPIC / "output"
PAGE = OUTPUT / "drive_distances.html"

QUERY = """
select e.event_id, e.short_name, coalesce(c.country_name, 'Unknown') as country,
       e.country_url, e.eventname, e.latitude, e.longitude, e.country_code,
       t.athlete_id, t.duration_s, t.distance_m, t.reachable
from parkrun.events e
left join parkrun.country_lookup c using (country_code)
left join parkrun.travel_times t
       on t.event_id = e.event_id and t.provider = 'ors'
where e.seriesid = 1 and e.live
order by e.short_name
"""


def load(db):
    con = duckdb.connect(db, read_only=True)
    rows = con.sql(QUERY).fetchall()
    routed_at = con.sql(
        "select max(routed_at)::date from parkrun.travel_times"
    ).fetchone()[0]
    con.close()

    # [(athlete_id, "George"), ...] in display order
    athletes = sorted(((a, n.capitalize()) for a, n in ATHLETE_NAMES.items()),
                      key=lambda x: x[1])
    col = {aid: i for i, (aid, _) in enumerate(athletes)}
    events = {}
    for (eid, name, country, curl, slug, lat, lon, cc,
         aid, dur, dist, reachable) in rows:
        ev = events.setdefault(eid, {
            "n": name, "c": country,
            "u": f"https://{curl}/{slug}/" if curl and slug else None,
            "lat": round(lat, 5), "lon": round(lon, 5),
            "t": [None] * len(athletes), "d": [None] * len(athletes),
        })
        if aid in col and reachable and dur is not None:
            ev["t"][col[aid]] = round(dur)
            ev["d"][col[aid]] = round(dist)
            if crosses_water(cc, lat, lon):
                ev["x"] = 1      # reached across water: the time is too short
    n_cross = sum(1 for e in events.values() if e.get("x"))
    return athletes, list(events.values()), routed_at, n_cross


def main():
    db = resolve_db()
    athletes, events, routed_at, n_cross = load(db)
    n_routed = sum(1 for e in events if any(v is not None for v in e["t"]))
    meta = {
        "athletes": [{"name": n, "color": ATHLETE_COLORS.get(n, "#888")}
                     for _, n in athletes],
        "routed_at": str(routed_at),
        "n_events": len(events),
        "n_routed": n_routed,
        "n_cross": n_cross,
    }
    template = (TOPIC / "scripts" / "page_template.html").read_text()
    html = (template
            .replace("/*__META__*/null", json.dumps(meta))
            .replace("/*__EVENTS__*/[]",
                     json.dumps(events, separators=(",", ":"))))
    OUTPUT.mkdir(exist_ok=True)
    PAGE.write_text(html)
    print(f"{db}: {len(events)} parkruns, {n_routed} routed "
          f"({n_cross} across water) -> {PAGE}")


if __name__ == "__main__":
    main()

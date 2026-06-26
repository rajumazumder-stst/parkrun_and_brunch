"""Parkrun data pipeline: bootstrap + refresh into DuckDB.

Implements the spec in CLAUDE.md:

  Path A  - events reconcile (corruption gate, soft-delete via `live`)
  Path B  - results upsert (full scrape, all 3 athletes in one transaction)
  Bootstrap - seed schema + static tables from tracked CSVs when DB is empty

The two paths are independent: a failed events reconcile never blocks the
results scrape.

Usage:
    python parkrun_pipeline.py bootstrap   # force first-time seed
    python parkrun_pipeline.py refresh     # normal run (auto-bootstraps if empty)
    python parkrun_pipeline.py status      # row counts
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import duckdb
import pandas as pd
import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DB_PATH = Path.home() / "Documents" / "duckdb" / "my_database.duckdb"
SCHEMA = "parkrun"
DATA_DIR = Path(__file__).parent / "data"

ATHLETE_IDS = [5672, 5462426, 3087156]
ATHLETE_URL = "https://www.parkrun.org.uk/parkrunner/{athlete_id}/all/"
EVENTS_JSON_URL = "https://images.parkrun.com/events.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
REQUEST_DELAY_SECONDS = 2
CORRUPTION_GATE_MIN_RATIO = 0.95  # new count must be >= 95% of stored count

RESULT_COLUMN_MAP = {
    "Event": "event",
    "Run Date": "run_date",
    "Run Number": "run_number",
    "Pos": "position",
    "Time": "time",
    "Age Grade": "age_grade",
    "PB?": "pb_flag",
}


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


def time_to_seconds(t: str) -> int | None:
    """Parse a parkrun time string (MM:SS or H:MM:SS) to total seconds."""
    if not isinstance(t, str) or ":" not in t:
        return None
    parts = [int(p) for p in t.split(":")]
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    h, m, s = parts
    return h * 3600 + m * 60 + s


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.country_lookup (
            country_code INTEGER PRIMARY KEY,
            country_url  VARCHAR,
            country_name VARCHAR
        );
        """
    )
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.athletes (
            athlete_id        BIGINT PRIMARY KEY,
            athlete_full_name VARCHAR,
            athlete_name      VARCHAR,
            date_of_birth     DATE
        );
        """
    )
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.events (
            event_id       INTEGER PRIMARY KEY,
            eventname      VARCHAR,
            short_name     VARCHAR,
            long_name      VARCHAR,
            location       VARCHAR,
            country_code   INTEGER,
            country_url    VARCHAR,
            longitude      DOUBLE,
            latitude       DOUBLE,
            seriesid       INTEGER,
            source         VARCHAR,
            live           BOOLEAN,
            first_seen     DATE,
            last_seen_live DATE
        );
        """
    )
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.results (
            athlete_id       BIGINT,
            event_id         INTEGER,
            run_date         DATE,
            run_number       INTEGER,
            position         INTEGER,
            time             VARCHAR,
            time_seconds     INTEGER,
            age_grade        VARCHAR,
            pb_flag          VARCHAR,
            scrape_timestamp TIMESTAMPTZ,
            PRIMARY KEY (athlete_id, run_date, event_id)
        );
        """
    )


def is_bootstrapped(con: duckdb.DuckDBPyConnection) -> bool:
    n = con.execute(f"SELECT count(*) FROM {SCHEMA}.events").fetchone()[0]
    return n > 0


# --------------------------------------------------------------------------- #
# Bootstrap (seed static tables + events from tracked CSVs)
# --------------------------------------------------------------------------- #
def seed_static_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        INSERT OR REPLACE INTO {SCHEMA}.country_lookup
        SELECT country_code, country_url, country_name
        FROM read_csv_auto('{DATA_DIR / 'country_lookup.csv'}', header=true);
        """
    )
    con.execute(
        f"""
        INSERT OR REPLACE INTO {SCHEMA}.athletes
        SELECT athlete_id, athlete_full_name, athlete_name, date_of_birth
        FROM read_csv('{DATA_DIR / 'athletes_lookup.csv'}', header=true,
                      dateformat='%d/%m/%Y');
        """
    )
    log(f"  seeded country_lookup + athletes")


def seed_events_from_csv(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        INSERT OR REPLACE INTO {SCHEMA}.events
        SELECT event_id, eventname, short_name, long_name, location,
               country_code, country_url, longitude, latitude, seriesid, source,
               (source = 'events_json')                              AS live,
               CURRENT_DATE                                          AS first_seen,
               CASE WHEN source = 'events_json' THEN CURRENT_DATE END AS last_seen_live
        FROM read_csv_auto('{DATA_DIR / 'parkrun_events.csv'}', header=true);
        """
    )
    n = con.execute(f"SELECT count(*) FROM {SCHEMA}.events").fetchone()[0]
    log(f"  seeded {n} events from CSV")


# --------------------------------------------------------------------------- #
# Path A: events reconcile
# --------------------------------------------------------------------------- #
def fetch_events_json() -> dict:
    r = requests.get(EVENTS_JSON_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()


def events_json_to_frame(data: dict) -> pd.DataFrame:
    countries = data["countries"]
    rows = []
    for feat in data["events"]["features"]:
        p = feat["properties"]
        lon, lat = feat["geometry"]["coordinates"]
        cc = p["countrycode"]
        rows.append(
            {
                "event_id": feat["id"],
                "eventname": p["eventname"],
                "short_name": p["EventShortName"],
                "long_name": p["EventLongName"],
                "location": p["EventLocation"],
                "country_code": cc,
                "country_url": countries.get(str(cc), {}).get("url"),
                "longitude": lon,
                "latitude": lat,
                "seriesid": p.get("seriesid"),
                "source": "events_json",
            }
        )
    return pd.DataFrame(rows)


def corruption_gate(
    live_df: pd.DataFrame, prev_count: int, bootstrap: bool
) -> tuple[bool, str]:
    """Return (passed, reason). On first run the volume check is skipped."""
    if live_df.empty:
        return False, "events list is empty"
    if not {"event_id", "short_name"}.issubset(live_df.columns):
        return False, "missing expected fields"
    if live_df["short_name"].isna().all():
        return False, "all short_names are null"
    if bootstrap or prev_count == 0:
        return True, "first run (volume check skipped)"
    ratio = len(live_df) / prev_count
    if ratio < CORRUPTION_GATE_MIN_RATIO:
        return False, (
            f"count {len(live_df)} < {CORRUPTION_GATE_MIN_RATIO:.0%} of "
            f"stored {prev_count} (ratio {ratio:.2%}) - likely truncated"
        )
    return True, f"count {len(live_df)} vs stored {prev_count} (ratio {ratio:.2%})"


def reconcile_events(con: duckdb.DuckDBPyConnection) -> None:
    """Path A. Download + gate + reconcile in one transaction. Never raises
    out to the caller in a way that blocks Path B."""
    log("Path A: events reconcile")
    try:
        data = fetch_events_json()
    except Exception as e:  # noqa: BLE001
        log(f"  WARN: events.json download failed ({e}); keeping existing copy")
        return

    live_df = events_json_to_frame(data)
    prev_count = con.execute(
        f"SELECT count(*) FROM {SCHEMA}.events WHERE source = 'events_json'"
    ).fetchone()[0]
    passed, reason = corruption_gate(live_df, prev_count, bootstrap=False)
    if not passed:
        log(f"  WARN: corruption gate FAILED - {reason}; reconcile skipped")
        return
    log(f"  corruption gate passed: {reason}")

    con.register("live_events", live_df)
    try:
        con.execute("BEGIN TRANSACTION;")

        # Warn (don't act) on manual rows that now collide with events.json.
        clashes = con.execute(
            f"""
            SELECT event_id FROM {SCHEMA}.events
            WHERE source = 'manual'
              AND event_id IN (SELECT event_id FROM live_events)
            """
        ).fetchall()
        for (eid,) in clashes:
            log(f"  WARN: manual event_id {eid} now appears in events.json - left untouched")

        # Insert genuinely new events.
        inserted = con.execute(
            f"""
            INSERT INTO {SCHEMA}.events
            SELECT le.event_id, le.eventname, le.short_name, le.long_name,
                   le.location, le.country_code, le.country_url, le.longitude,
                   le.latitude, le.seriesid, le.source,
                   TRUE, CURRENT_DATE, CURRENT_DATE
            FROM live_events le
            LEFT JOIN {SCHEMA}.events e ON e.event_id = le.event_id
            WHERE e.event_id IS NULL
            RETURNING event_id;
            """
        ).fetchall()

        # Update changed fields on existing non-manual events; mark live.
        con.execute(
            f"""
            UPDATE {SCHEMA}.events e
            SET eventname = le.eventname, short_name = le.short_name,
                long_name = le.long_name, location = le.location,
                country_code = le.country_code, country_url = le.country_url,
                longitude = le.longitude, latitude = le.latitude,
                seriesid = le.seriesid, live = TRUE, last_seen_live = CURRENT_DATE
            FROM live_events le
            WHERE e.event_id = le.event_id AND e.source <> 'manual';
            """
        )

        # Soft-delete: non-manual events no longer in events.json.
        deactivated = con.execute(
            f"""
            UPDATE {SCHEMA}.events e
            SET live = FALSE
            WHERE e.source <> 'manual'
              AND e.live = TRUE
              AND NOT EXISTS (SELECT 1 FROM live_events le WHERE le.event_id = e.event_id)
            RETURNING event_id;
            """
        ).fetchall()

        # Warn on unknown country codes (don't block).
        unknown = con.execute(
            f"""
            SELECT DISTINCT country_code FROM live_events
            WHERE country_code NOT IN (SELECT country_code FROM {SCHEMA}.country_lookup)
            """
        ).fetchall()
        for (cc,) in unknown:
            log(f"  WARN: unknown country_code {cc} - add to country_lookup.csv (name -> 'Unknown')")

        con.execute("COMMIT;")
        log(f"  reconciled: +{len(inserted)} new, {len(deactivated)} deactivated")
    except Exception as e:  # noqa: BLE001
        con.execute("ROLLBACK;")
        log(f"  ERROR: reconcile rolled back ({e})")
    finally:
        con.unregister("live_events")


# --------------------------------------------------------------------------- #
# Path B: results scrape + upsert
# --------------------------------------------------------------------------- #
def scrape_athlete(athlete_id: int) -> pd.DataFrame:
    url = ATHLETE_URL.format(athlete_id=athlete_id)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html5lib")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError(f"no tables found for athlete {athlete_id}")
    df = pd.read_html(StringIO(str(tables[-1])))[0].rename(columns=RESULT_COLUMN_MAP)
    df.insert(0, "athlete_id", athlete_id)
    return df


def resolve_event_ids(
    con: duckdb.DuckDBPyConnection, df: pd.DataFrame
) -> tuple[pd.DataFrame, list[str]]:
    """Map scraped event short-name -> event_id via seriesid=1. Returns
    (resolved_df, unmatched_names)."""
    rows = con.execute(
        f"SELECT short_name, event_id FROM {SCHEMA}.events WHERE seriesid = 1"
    ).fetchall()
    mapping: dict[str, int] = {}
    dupes = set()
    for short_name, event_id in rows:
        if short_name in mapping:
            dupes.add(short_name)
        mapping[short_name] = event_id
    for d in dupes:
        log(f"  WARN: duplicate short_name in seriesid=1: {d!r} - ambiguous match")

    df = df.copy()
    df["event_id"] = df["event"].map(mapping)
    unmatched = sorted(df.loc[df["event_id"].isna(), "event"].unique())
    resolved = df.loc[df["event_id"].notna()].copy()
    return resolved, unmatched


def upsert_results(con: duckdb.DuckDBPyConnection) -> None:
    """Path B. Scrape all athletes, then upsert in one transaction. If any
    athlete's page fails, nothing is written."""
    log("Path B: results upsert")
    scrape_ts = datetime.now(timezone.utc)
    frames = []
    for aid in ATHLETE_IDS:
        log(f"  scraping athlete {aid} ...")
        frames.append(scrape_athlete(aid))  # raises -> abort before any write
        time.sleep(REQUEST_DELAY_SECONDS)

    raw = pd.concat(frames, ignore_index=True)
    resolved, unmatched = resolve_event_ids(con, raw)
    if unmatched:
        log(f"  WARN: {len(unmatched)} unmatched event name(s) skipped: {unmatched}")

    # Normalise types.
    resolved["run_date"] = pd.to_datetime(
        resolved["run_date"], format="%d/%m/%Y"
    ).dt.date
    resolved["run_number"] = pd.to_numeric(
        resolved["run_number"], errors="coerce"
    ).astype("Int64")
    resolved["position"] = pd.to_numeric(
        resolved["position"], errors="coerce"
    ).astype("Int64")
    resolved["event_id"] = resolved["event_id"].astype("Int64")
    resolved["time_seconds"] = resolved["time"].map(time_to_seconds).astype("Int64")
    resolved["scrape_timestamp"] = scrape_ts
    stage = resolved[
        [
            "athlete_id", "event_id", "run_date", "run_number", "position",
            "time", "time_seconds", "age_grade", "pb_flag", "scrape_timestamp",
        ]
    ]

    con.register("results_stage", stage)
    try:
        con.execute("BEGIN TRANSACTION;")
        before = con.execute(f"SELECT count(*) FROM {SCHEMA}.results").fetchone()[0]
        con.execute(
            f"""
            INSERT INTO {SCHEMA}.results
                (athlete_id, event_id, run_date, run_number, position,
                 time, time_seconds, age_grade, pb_flag, scrape_timestamp)
            SELECT athlete_id, event_id, run_date, run_number, position,
                   time, time_seconds, age_grade, pb_flag, scrape_timestamp
            FROM results_stage
            ON CONFLICT (athlete_id, run_date, event_id) DO UPDATE SET
                run_number = excluded.run_number,
                position = excluded.position,
                time = excluded.time,
                time_seconds = excluded.time_seconds,
                age_grade = excluded.age_grade,
                pb_flag = excluded.pb_flag,
                scrape_timestamp = excluded.scrape_timestamp;
            """
        )
        after = con.execute(f"SELECT count(*) FROM {SCHEMA}.results").fetchone()[0]
        con.execute("COMMIT;")
        log(f"  upserted {len(stage)} rows ({after - before} new, {len(stage) - (after - before)} updated)")
    except Exception as e:  # noqa: BLE001
        con.execute("ROLLBACK;")
        log(f"  ERROR: results upsert rolled back ({e})")
        raise
    finally:
        con.unregister("results_stage")


def export_results_snapshot(con: duckdb.DuckDBPyConnection) -> None:
    out = DATA_DIR / "parkrun_results.csv"
    con.execute(
        f"""
        COPY (
            SELECT athlete_id, event_id, run_date, run_number, position,
                   time, time_seconds, age_grade, pb_flag, scrape_timestamp
            FROM {SCHEMA}.results
            ORDER BY athlete_id, run_date, event_id
        ) TO '{out}' (HEADER, DELIMITER ',');
        """
    )
    log(f"  exported snapshot -> {out}")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def bootstrap(con: duckdb.DuckDBPyConnection) -> None:
    log("BOOTSTRAP: empty DB")
    seed_static_tables(con)
    seed_events_from_csv(con)
    upsert_results(con)
    export_results_snapshot(con)


def refresh(con: duckdb.DuckDBPyConnection) -> None:
    if not is_bootstrapped(con):
        bootstrap(con)
        return
    reconcile_events(con)  # Path A (independent)
    upsert_results(con)  # Path B (runs regardless of Path A)
    export_results_snapshot(con)


def status(con: duckdb.DuckDBPyConnection) -> None:
    for t in ("events", "results", "country_lookup", "athletes"):
        n = con.execute(f"SELECT count(*) FROM {SCHEMA}.{t}").fetchone()[0]
        print(f"  {SCHEMA}.{t:<14} {n:>6} rows")
    live = con.execute(
        f"SELECT count(*) FROM {SCHEMA}.events WHERE live"
    ).fetchone()[0]
    print(f"  (events live = {live})")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        if cmd == "bootstrap":
            if is_bootstrapped(con):
                log("already bootstrapped; use 'refresh'")
            else:
                bootstrap(con)
        elif cmd == "refresh":
            refresh(con)
        elif cmd == "status":
            status(con)
        else:
            print(__doc__)
            sys.exit(1)
        print()
        status(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()

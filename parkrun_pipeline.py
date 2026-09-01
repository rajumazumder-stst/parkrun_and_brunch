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
    python parkrun_pipeline.py snapshot    # rebuild the deploy snapshot only
    python parkrun_pipeline.py seed [FILE] # fill an EMPTY DB from a snapshot
    python parkrun_pipeline.py motherduck  # push parkrun-only data to MotherDuck

bootstrap and refresh rebuild the deploy snapshot (data/parkrun_snapshot.duckdb)
automatically; `snapshot` rebuilds just that file from the current DB.
`seed` populates an empty local DB from a deploy snapshot (defaults to the
committed data/parkrun_snapshot.duckdb) — how a fresh source-of-truth DB is
created without re-bootstrapping, which would discard current_targets history.
`motherduck` (re)seeds the parkrun-only cloud DB FROM the local DB with a
constrained schema (needs the `motherduck_token` env var); run explicitly.

Target DB (env `PARKRUN_PIPELINE_DB`):
    unset               -> local dev DB (~/Documents/duckdb/my_database.duckdb)
    <path>.duckdb       -> that local file — the source of truth; the launchd
                           scheduler points here (~/.config/parkrun/parkrun_local.duckdb)
    md:parkrun_snapshot -> operate directly on MotherDuck (kept for reference;
                           see docs/DEPLOY.md)

The file name becomes the DuckDB catalog name, so never call a local DB
`parkrun.duckdb`: that makes `parkrun.v_overlap` ambiguous against the
`parkrun` schema.
"""

from __future__ import annotations

import os
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

# Read-only, parkrun-ONLY DuckDB the hosted app serves (see CLAUDE.md). Tables
# the snapshot carries; views are rebuilt from these by ensure_views().
SNAPSHOT_PATH = DATA_DIR / "parkrun_snapshot.duckdb"
SNAPSHOT_TABLES = (
    "athletes",
    "buggy_handicap",
    "country_lookup",
    "course_difficulty",
    "current_targets",
    "events",
    "results",
    "run_modes",
)

# Backfill values for columns newer than the snapshot being seeded from, keyed
# (table, column). Anything not listed defaults to NULL. `mode` must be listed:
# it is part of the current_targets primary key, so NULL would fail the insert.
SEED_COLUMN_DEFAULTS = {
    ("current_targets", "mode"): "'nonbuggy'",
}

# MotherDuck cloud target. The database name deliberately differs from the
# `parkrun` schema (same rule as the snapshot catalog) so `parkrun.v_overlap`
# never becomes an ambiguous catalog-vs-schema reference. Uploads carry the
# parkrun-ONLY tables + views — personal_finance can never reach the cloud.
MD_DATABASE = "parkrun_snapshot"

# Fixed cohort. The mapping drives the per-athlete columns in v_overlap.
ATHLETE_NAMES = {5672: "raju", 5462426: "duncan", 3087156: "george"}
ATHLETE_IDS = list(ATHLETE_NAMES)
TARGET_WINDOW_DAYS = 91  # head-to-head / current-form lookback
# Multiplicative cost of pushing a buggy, used to bridge a target when an
# athlete has no runs of the run's own mode in the window. A placeholder until
# measured per athlete from confirmed labels: the one course-controlled figure
# available is Duncan at Lordship Rec (2025 median 1450s -> 2026 median 1636s,
# +12.8%), rounded up so it errs in the buggy runner's favour.
BUGGY_HANDICAP_DEFAULT = 0.15
# Athletes who ever push a buggy: George and Duncan. Raju never does, so he has
# no buggy form to record and gets no buggy row at all — not an empty one. The
# analytics VIEWS stay symmetric (he falls out as all-nonbuggy on his own); this
# only narrows what the materialised current_targets grid stores.
BUGGY_ATHLETE_IDS = (3087156, 5462426)
ATHLETE_URL = "https://www.parkrun.org.uk/parkrunner/{athlete_id}/all/"
EVENTS_JSON_URL = "https://images.parkrun.com/events.json"

# Full Chrome-like header set (not just the UA): parkrun's WAF started
# rejecting bare-UA requests from GitHub runners with HTTP 405 (Jul 2026).
# No Accept-Encoding — requests' default (gzip, deflate) avoids advertising
# brotli, which it can't decode without an extra dependency.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}
WARMUP_URL = "https://www.parkrun.org.uk/"
REQUEST_DELAY_SECONDS = 2
RETRY_STATUSES = {403, 405, 429, 500, 502, 503, 504}
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 15  # wait 15s, then 30s
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


def _count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return con.execute(f"SELECT count(*) FROM {SCHEMA}.{table}").fetchone()[0]


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
    # Per-run buggy label. ABSENT ROW MEANS NON-BUGGY and is_buggy is NOT NULL:
    # there is no third state, which is what makes the zero-label equivalence
    # check exact (with this table empty the views reproduce the old ones row
    # for row). In practice the table is dense — the estimator's anti-join
    # writes a row for every result — but the views still coalesce, so a hole
    # (a re-keyed run, a skipped estimator) degrades to non-buggy rather than
    # NULLing a target and silently dropping a runner from a contest.
    #
    # `source` values are NOT interchangeable: 'manual' and 'estimated' train
    # the model, 'default' never does — those are assumptions, not observations.
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.run_modes (
            athlete_id BIGINT,
            run_date   DATE,
            event_id   INTEGER,
            is_buggy   BOOLEAN     NOT NULL,
            source     VARCHAR     NOT NULL,   -- 'manual' | 'estimated' | 'default'
            confidence DOUBLE,                 -- max(p, 1-p); NULL for manual/default
            reason     VARCHAR,
            set_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (athlete_id, run_date, event_id)
        );
        """
    )
    # Course difficulty, deliberately NOT a column on `events`: reconcile_events
    # inserts into that table POSITIONALLY (14 values) inside a try/except that
    # only logs, so a 15th column would turn into a silent weekly "reconcile
    # rolled back" and new parkruns would stop appearing. Own table, no risk.
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.course_difficulty (
            event_id     INTEGER PRIMARY KEY,
            parkrun_name VARCHAR,   -- name as published by the source
            difficulty   DOUBLE,    -- 0.8 .. 11.6 on a 0-12 scale, 12 = hardest
            speed_rank   INTEGER,   -- 1 .. 835, 1 = fastest (reference only)
            source       VARCHAR,
            fetched_at   TIMESTAMPTZ
        );
        """
    )
    # Per-athlete buggy handicap. Materialised rather than derived because
    # measuring it needs the residual model, which lives in Python not SQL.
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.buggy_handicap (
            athlete_id     BIGINT PRIMARY KEY,
            handicap       DOUBLE,    -- 0.15 = a buggy costs 15%
            n_buggy_labels INTEGER,
            method         VARCHAR,   -- 'default' | 'measured'
            computed_at    TIMESTAMPTZ
        );
        """
    )
    # Declared in the post-migration shape: `mode` in the PK, because each
    # athlete now gets one target row per mode per refresh. A fresh DB is
    # already correct and never runs ensure_migrations' rebuild.
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.current_targets (
            refresh_date   DATE,
            athlete_id     BIGINT,
            mode           VARCHAR,
            target_seconds DOUBLE,
            n_window       INTEGER,
            PRIMARY KEY (refresh_date, athlete_id, mode)
        );
        """
    )
    ensure_views(con)


def ensure_migrations(con: duckdb.DuckDBPyConnection) -> None:
    """Idempotent, forward-only schema migrations for EXISTING databases.

    ensure_schema is all CREATE TABLE IF NOT EXISTS: it adds new tables to an
    existing DB happily, but can never *change* one. Called from main() right
    after ensure_schema, so every entry point migrates whatever DB it touches.

    Two steps: backfill buggy_handicap defaults, and rebuild current_targets'
    primary key to gain `mode`. DuckDB
    (1.5.4) cannot alter a PK in place — DROP CONSTRAINT raises
    NotImplementedException and ADD PRIMARY KEY raises "can have only one
    primary key" — so it is rename/create/copy/drop.

    Existing rows backfill as 'nonbuggy' and are deliberately NOT recomputed:
    they record what the targets WERE on those dates, which is the entire
    reason current_targets is a table rather than a view.
    """
    # Backfill the handicap rows on an existing DB. seed_static_tables only
    # runs on bootstrap/seed, never on refresh, so without this an existing
    # source-of-truth DB would carry an empty buggy_handicap for ever and the
    # head-to-head bridge would fall back to its defensive coalesce. Cheap and
    # idempotent (INSERT OR IGNORE); a no-op while `athletes` is still empty.
    seed_buggy_handicap_defaults(con)

    has_mode = con.execute(
        """
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema = ? AND table_name = 'current_targets'
          AND column_name = 'mode'
        """,
        [SCHEMA],
    ).fetchone()[0]
    if has_mode:
        return

    log("migration: current_targets PK -> (refresh_date, athlete_id, mode)")
    con.execute("BEGIN;")
    try:
        con.execute(
            f"ALTER TABLE {SCHEMA}.current_targets RENAME TO current_targets_old;"
        )
        con.execute(
            f"""
            CREATE TABLE {SCHEMA}.current_targets (
                refresh_date   DATE,
                athlete_id     BIGINT,
                mode           VARCHAR,
                target_seconds DOUBLE,
                n_window       INTEGER,
                PRIMARY KEY (refresh_date, athlete_id, mode)
            );
            """
        )
        con.execute(
            f"""
            INSERT INTO {SCHEMA}.current_targets
                  (refresh_date, athlete_id, mode, target_seconds, n_window)
            SELECT refresh_date, athlete_id, 'nonbuggy', target_seconds, n_window
            FROM   {SCHEMA}.current_targets_old;
            """
        )
        con.execute(f"DROP TABLE {SCHEMA}.current_targets_old;")
        con.execute("COMMIT;")
    except Exception:
        con.execute("ROLLBACK;")
        raise
    log(f"  migrated {_count(con, 'current_targets')} current_targets rows "
        f"as mode='nonbuggy'")


def ensure_views(con: duckdb.DuckDBPyConnection) -> None:
    """(Re)create the derived analytics views. Deterministic from results."""
    # Base view every mode-aware query reads. Created FIRST: DuckDB resolves a
    # view body at creation time, so anything referencing it must come after.
    #
    # coalesce(is_buggy, FALSE): an absent run_modes row means non-buggy. The
    # table is dense in practice, but a hole (a re-keyed run, a skipped
    # estimator) must degrade to the old behaviour rather than NULL a mode and
    # silently drop a runner from a contest.
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.v_results_moded AS
        SELECT r.*,
               coalesce(m.is_buggy, FALSE) AS is_buggy,
               CASE WHEN coalesce(m.is_buggy, FALSE) THEN 'buggy'
                    ELSE 'nonbuggy' END    AS mode,
               m.source AS mode_source,
               m.reason AS mode_reason
        FROM {SCHEMA}.results r
        LEFT JOIN {SCHEMA}.run_modes m USING (athlete_id, run_date, event_id);
        """
    )
    flags = ",\n               ".join(
        f"bool_or(athlete_id = {aid}) AS has_{name}"
        for aid, name in ATHLETE_NAMES.items()
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.v_overlap AS
        SELECT event_id, run_date,
               {flags},
               count(*) AS n_athletes
        FROM {SCHEMA}.results
        GROUP BY event_id, run_date;
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.v_head_to_head AS
        WITH parts AS (
            SELECT r.event_id, r.run_date, r.athlete_id,
                   r.time_seconds AS actual_seconds,
                   r.is_buggy, r.mode
            FROM {SCHEMA}.v_results_moded r
            JOIN (SELECT event_id, run_date FROM {SCHEMA}.results
                  GROUP BY event_id, run_date HAVING count(*) >= 2) o
              USING (event_id, run_date)
        ),
        -- Both modes' medians in one pass. count(...) FILTER returns 0, not
        -- NULL, so own_n / oth_n are safe to compare directly.
        win AS (
            SELECT p.event_id, p.run_date, p.athlete_id, p.actual_seconds,
                   p.is_buggy, p.mode,
                   median(h.time_seconds) FILTER (WHERE h.mode =  p.mode) AS own_target,
                   count(h.time_seconds)  FILTER (WHERE h.mode =  p.mode) AS own_n,
                   median(h.time_seconds) FILTER (WHERE h.mode <> p.mode) AS oth_target,
                   count(h.time_seconds)  FILTER (WHERE h.mode <> p.mode) AS oth_n
            FROM parts p
            LEFT JOIN {SCHEMA}.v_results_moded h
              ON h.athlete_id = p.athlete_id
             AND h.run_date BETWEEN p.run_date - {TARGET_WINDOW_DAYS}
                               AND p.run_date - 1
            GROUP BY p.event_id, p.run_date, p.athlete_id, p.actual_seconds,
                     p.is_buggy, p.mode
        ),
        -- Same-mode form when it exists; otherwise bridge from the other mode
        -- via the athlete's handicap. Symmetric, and it DIVIDES on the way
        -- down: apply both directions and you return to the starting value,
        -- which x (1 - h) would not. coalesce is defensive — seed_static_tables
        -- guarantees a row, but a NULL handicap would NULL the target and drop
        -- the runner from the contest, too quiet a failure to accept.
        target AS (
            SELECT w.event_id, w.run_date, w.athlete_id, w.actual_seconds,
                   w.is_buggy,
                   CASE WHEN w.own_n >= 1     THEN w.own_target
                        WHEN w.mode = 'buggy' THEN w.oth_target
                             * (1 + coalesce(bh.handicap, {BUGGY_HANDICAP_DEFAULT}))
                        ELSE                       w.oth_target
                             / (1 + coalesce(bh.handicap, {BUGGY_HANDICAP_DEFAULT}))
                   END AS target_seconds,
                   CASE WHEN w.own_n >= 1 THEN w.own_n ELSE w.oth_n END AS n_window,
                   CASE WHEN w.own_n >= 1     THEN w.mode
                        WHEN w.mode = 'buggy' THEN 'nonbuggy+handicap'
                        ELSE                       'buggy-handicap'
                   END AS target_basis
            FROM win w
            LEFT JOIN {SCHEMA}.buggy_handicap bh USING (athlete_id)
        ),
        valid AS (SELECT * FROM target WHERE n_window >= 1),
        h2h AS (
            SELECT event_id, run_date FROM valid
            GROUP BY event_id, run_date HAVING count(*) >= 2
        ),
        ranked AS (
            SELECT v.*,
                   round((v.actual_seconds - v.target_seconds)
                         / v.target_seconds * 100, 2) AS pct_diff
            FROM valid v JOIN h2h USING (event_id, run_date)
        ),
        placed AS (
            SELECT r.*, a.athlete_name,
                   rank() OVER (PARTITION BY r.event_id, r.run_date
                                ORDER BY r.pct_diff) AS place_rank
            FROM ranked r JOIN {SCHEMA}.athletes a USING (athlete_id)
        ),
        labelled AS (
            SELECT *,
                   string_agg(athlete_name, ' vs ' ORDER BY athlete_name)
                     OVER (PARTITION BY event_id, run_date) AS classification,
                   count(*) OVER (PARTITION BY event_id, run_date) AS n_ranked
            FROM placed
        )
        SELECT l.event_id, l.run_date, e.short_name,
               l.athlete_id, l.athlete_name, l.classification, l.n_ranked,
               l.actual_seconds, l.target_seconds, l.n_window,
               l.pct_diff, l.place_rank, l.is_buggy, l.target_basis
        FROM labelled l JOIN {SCHEMA}.events e USING (event_id);
        """
    )
    # Each athlete's current-form target evaluated on *every Saturday* in the
    # data span, per mode. Same definition as the head-to-head target: median
    # time_seconds over the {TARGET_WINDOW_DAYS}-day window BEFORE the Saturday
    # ([S-91, S-1], excludes the day), valid when >= 1 run in the window.
    # Saturdays with no runs in the window are omitted (a gap in the line),
    # never zero — which is also what stops an athlete who has never pushed a
    # buggy getting a phantom buggy row.
    #
    # NO handicap bridge here (nor in current_targets), unlike the head-to-head:
    # bridging would draw an imaginary buggy form line back to 2017.
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.v_saturday_targets AS
        WITH bounds AS (
            SELECT min(run_date) AS d0, max(run_date) AS d1 FROM {SCHEMA}.results
        ),
        saturdays AS (
            SELECT d::date AS sat
            FROM bounds,
                 generate_series(d0::timestamp, d1::timestamp, INTERVAL 1 DAY) AS g(d)
            WHERE dayofweek(d::date) = 6            -- 6 = Saturday
        ),
        modes(mode) AS (VALUES ('nonbuggy'), ('buggy')),
        grid AS (
            SELECT a.athlete_id, a.athlete_name, s.sat, m.mode
            FROM {SCHEMA}.athletes a CROSS JOIN saturdays s CROSS JOIN modes m
        ),
        targets AS (
            SELECT g.athlete_id, g.athlete_name, g.sat, g.mode,
                   median(r.time_seconds) AS target_seconds,
                   count(r.time_seconds)  AS n_window
            FROM grid g
            LEFT JOIN {SCHEMA}.v_results_moded r
              ON r.athlete_id = g.athlete_id
             AND r.mode = g.mode
             AND r.run_date BETWEEN g.sat - {TARGET_WINDOW_DAYS} AND g.sat - 1
            GROUP BY g.athlete_id, g.athlete_name, g.sat, g.mode
        )
        SELECT athlete_id, athlete_name,
               sat AS run_date, mode, target_seconds, n_window
        FROM targets
        WHERE n_window >= 1;
        """
    )


# Pre-buggy view bodies, kept VERBATIM as frozen text for the zero-label
# equivalence check (Verification A) and the dev-only label-impact page. Never
# refactor these to share code with ensure_views — the whole point is to compare
# the new views against an independent copy of the old ones.
LEGACY_HEAD_TO_HEAD_SQL = """
CREATE OR REPLACE VIEW {schema}.v_head_to_head_legacy AS
WITH parts AS (
    SELECT r.event_id, r.run_date, r.athlete_id,
           r.time_seconds AS actual_seconds
    FROM {schema}.results r
    JOIN (SELECT event_id, run_date FROM {schema}.results
          GROUP BY event_id, run_date HAVING count(*) >= 2) o
      USING (event_id, run_date)
),
target AS (
    SELECT p.event_id, p.run_date, p.athlete_id, p.actual_seconds,
           median(h.time_seconds) AS target_seconds,
           count(h.time_seconds)  AS n_window
    FROM parts p
    LEFT JOIN {schema}.results h
      ON h.athlete_id = p.athlete_id
     AND h.run_date BETWEEN p.run_date - {window} AND p.run_date - 1
    GROUP BY p.event_id, p.run_date, p.athlete_id, p.actual_seconds
),
valid AS (SELECT * FROM target WHERE n_window >= 1),
h2h AS (
    SELECT event_id, run_date FROM valid
    GROUP BY event_id, run_date HAVING count(*) >= 2
),
ranked AS (
    SELECT v.*,
           round((v.actual_seconds - v.target_seconds)
                 / v.target_seconds * 100, 2) AS pct_diff
    FROM valid v JOIN h2h USING (event_id, run_date)
),
placed AS (
    SELECT r.*, a.athlete_name,
           rank() OVER (PARTITION BY r.event_id, r.run_date
                        ORDER BY r.pct_diff) AS place_rank
    FROM ranked r JOIN {schema}.athletes a USING (athlete_id)
),
labelled AS (
    SELECT *,
           string_agg(athlete_name, ' vs ' ORDER BY athlete_name)
             OVER (PARTITION BY event_id, run_date) AS classification,
           count(*) OVER (PARTITION BY event_id, run_date) AS n_ranked
    FROM placed
)
SELECT l.event_id, l.run_date, e.short_name,
       l.athlete_id, l.athlete_name, l.classification, l.n_ranked,
       l.actual_seconds, l.target_seconds, l.n_window,
       l.pct_diff, l.place_rank
FROM labelled l JOIN {schema}.events e USING (event_id);
"""

LEGACY_SATURDAY_TARGETS_SQL = """
CREATE OR REPLACE VIEW {schema}.v_saturday_targets_legacy AS
WITH bounds AS (
    SELECT min(run_date) AS d0, max(run_date) AS d1 FROM {schema}.results
),
saturdays AS (
    SELECT d::date AS sat
    FROM bounds,
         generate_series(d0::timestamp, d1::timestamp, INTERVAL 1 DAY) AS g(d)
    WHERE dayofweek(d::date) = 6
),
grid AS (
    SELECT a.athlete_id, a.athlete_name, s.sat
    FROM {schema}.athletes a CROSS JOIN saturdays s
),
targets AS (
    SELECT g.athlete_id, g.athlete_name, g.sat,
           median(r.time_seconds) AS target_seconds,
           count(r.time_seconds)  AS n_window
    FROM grid g
    LEFT JOIN {schema}.results r
      ON r.athlete_id = g.athlete_id
     AND r.run_date BETWEEN g.sat - {window} AND g.sat - 1
    GROUP BY g.athlete_id, g.athlete_name, g.sat
)
SELECT athlete_id, athlete_name,
       sat AS run_date, target_seconds, n_window
FROM targets
WHERE n_window >= 1;
"""


def ensure_legacy_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create the pre-buggy views alongside the live ones. DEV DBs ONLY.

    Deliberately NOT called from bootstrap/refresh: these must never reach the
    source-of-truth DB or the deploy snapshot. Gated on PARKRUN_LABEL_AUDIT=1,
    the same flag that reveals the app's label-impact tab.

    They exist for two things:
      * Verification A — with run_modes empty, the new views must reproduce
        these row for row. That property holds ONLY while the table is empty,
        so capture it before any label is written.
      * The dev-only label-impact page, which diffs old against new once labels
        exist.
    """
    if os.environ.get("PARKRUN_LABEL_AUDIT") != "1":
        log("legacy views: skipped (set PARKRUN_LABEL_AUDIT=1 to build them)")
        return
    con.execute(LEGACY_HEAD_TO_HEAD_SQL.format(
        schema=SCHEMA, window=TARGET_WINDOW_DAYS))
    con.execute(LEGACY_SATURDAY_TARGETS_SQL.format(
        schema=SCHEMA, window=TARGET_WINDOW_DAYS))
    log("legacy views: v_head_to_head_legacy + v_saturday_targets_legacy created")


def is_bootstrapped(con: duckdb.DuckDBPyConnection) -> bool:
    return _count(con, "events") > 0


# --------------------------------------------------------------------------- #
# Bootstrap (seed static tables + events from tracked CSVs)
# --------------------------------------------------------------------------- #
def seed_buggy_handicap_defaults(con: duckdb.DuckDBPyConnection) -> None:
    """One handicap row per athlete at the placeholder value.

    INSERT OR IGNORE, so a measured value is never overwritten by a later
    bootstrap or seed. The row must exist: the head-to-head bridge joins on it,
    and a missing row would NULL the target and silently drop that runner from
    the contest.
    """
    con.execute(
        f"""
        INSERT OR IGNORE INTO {SCHEMA}.buggy_handicap
              (athlete_id, handicap, n_buggy_labels, method, computed_at)
        SELECT athlete_id, {BUGGY_HANDICAP_DEFAULT}, 0, 'default', now()
        FROM {SCHEMA}.athletes;
        """
    )


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
    seed_buggy_handicap_defaults(con)
    log("  seeded country_lookup + athletes + buggy_handicap")


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
    log(f"  seeded {_count(con, 'events')} events from CSV")


# --------------------------------------------------------------------------- #
# HTTP: shared session + retry
# --------------------------------------------------------------------------- #
_session: requests.Session | None = None


def http_session() -> requests.Session:
    """Shared session with browser headers. First use warms up on the parkrun
    homepage so any WAF cookies are set before an athlete page is fetched."""
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        try:
            s.get(WARMUP_URL, timeout=30)
        except requests.RequestException as e:
            log(f"  WARN: warm-up request failed ({e}) - continuing without cookies")
        _session = s
    return _session


def get_with_retry(url: str, timeout: int) -> requests.Response:
    """GET with backoff on WAF/transient statuses (403/405/429/5xx). Raises on
    the final failure like raise_for_status."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        r = http_session().get(url, timeout=timeout)
        if r.status_code not in RETRY_STATUSES or attempt == RETRY_ATTEMPTS:
            r.raise_for_status()
            return r
        wait = RETRY_BACKOFF_SECONDS * attempt
        log(f"  HTTP {r.status_code} for {url} - retry {attempt}/{RETRY_ATTEMPTS - 1} in {wait}s")
        time.sleep(wait)
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------- #
# Path A: events reconcile
# --------------------------------------------------------------------------- #
def fetch_events_json() -> dict:
    r = get_with_retry(EVENTS_JSON_URL, timeout=60)
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


def corruption_gate(live_df: pd.DataFrame, prev_count: int) -> tuple[bool, str]:
    """Return (passed, reason). On first run the volume check is skipped."""
    if live_df.empty:
        return False, "events list is empty"
    if not {"event_id", "short_name"}.issubset(live_df.columns):
        return False, "missing expected fields"
    if live_df["short_name"].isna().all():
        return False, "all short_names are null"
    if prev_count == 0:
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
    passed, reason = corruption_gate(live_df, prev_count)
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
    r = get_with_retry(url, timeout=30)
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


def update_current_targets(con: duckdb.DuckDBPyConnection) -> None:
    """Snapshot each athlete's current-form target (91-day median, min 1 run)
    as of today. Stored per refresh_date so form history accumulates.

    One row per (athlete, mode), except that only BUGGY_ATHLETE_IDS get a
    buggy row: Raju never pushes a buggy, so an empty row for him would be
    noise stored for ever in a table that accumulates per refresh date.

    A buggy row with no runs in the window is still written (n_window = 0,
    NULL target) for the athletes who do use one — that is a real "no buggy
    runs in the last 91 days", not an impossibility. The app filters on
    n_window >= 1.

    No handicap bridge here (unlike the head-to-head): this records measured
    form, not a comparison.
    """
    con.execute(
        f"""
        INSERT OR REPLACE INTO {SCHEMA}.current_targets
              (refresh_date, athlete_id, mode, target_seconds, n_window)
        WITH modes(mode) AS (VALUES ('nonbuggy'), ('buggy')),
        grid AS (
            SELECT a.athlete_id, m.mode
            FROM {SCHEMA}.athletes a CROSS JOIN modes m
            WHERE m.mode = 'nonbuggy'
               OR a.athlete_id IN {BUGGY_ATHLETE_IDS}
        )
        SELECT CURRENT_DATE, g.athlete_id, g.mode,
               median(h.time_seconds) AS target_seconds,
               count(h.time_seconds)  AS n_window
        FROM grid g
        LEFT JOIN {SCHEMA}.v_results_moded h
          ON h.athlete_id = g.athlete_id
         AND h.mode = g.mode
         AND h.run_date BETWEEN CURRENT_DATE - {TARGET_WINDOW_DAYS}
                           AND CURRENT_DATE - 1
        GROUP BY g.athlete_id, g.mode;
        """
    )
    rows = con.execute(
        f"""
        SELECT a.athlete_name, t.mode,
               CASE WHEN t.target_seconds IS NULL THEN 'n/a'
                    ELSE printf('%d:%02d', t.target_seconds::int // 60,
                                           t.target_seconds::int % 60) END,
               t.n_window
        FROM {SCHEMA}.current_targets t
        JOIN {SCHEMA}.athletes a USING (athlete_id)
        WHERE t.refresh_date = CURRENT_DATE
        ORDER BY a.athlete_name, t.mode
        """
    ).fetchall()
    log("  current-form targets (as of today):")
    for name, mode, mmss, n in rows:
        log(f"    {name:<8} {mode:<9} {mmss:>7}  ({n} runs in window)")


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


def export_run_modes(con: duckdb.DuckDBPyConnection) -> None:
    """Export the buggy labels as a tracked CSV.

    parkrun_results.csv is the audit trail of the *scrape*; this is the audit
    trail of the *labels*, which are hand-entered or model-written and exist
    nowhere else outside a binary DB. Committed alongside the snapshot, so a
    label change is a reviewable diff.
    """
    out = DATA_DIR / "parkrun_run_modes.csv"
    con.execute(
        f"""
        COPY (
            SELECT athlete_id, run_date, event_id, is_buggy, source,
                   confidence, reason, set_at
            FROM {SCHEMA}.run_modes
            ORDER BY athlete_id, run_date, event_id
        ) TO '{out}' (HEADER, DELIMITER ',');
        """
    )
    log(f"  exported run modes -> {out} ({_count(con, 'run_modes')} rows)")


def build_snapshot(con: duckdb.DuckDBPyConnection) -> None:
    """Write the parkrun-ONLY DuckDB the hosted app serves.

    Built from scratch (never a file copy) so it can NEVER carry the
    personal_finance schema that shares the dev DB. Tables are copied with
    native CREATE TABLE AS (exact types preserved — the head-to-head view's
    date arithmetic needs run_date to stay DATE); views are then rebuilt by
    ensure_views() with the snapshot as its own default catalog, so their
    references rebind to the local `parkrun` schema when opened standalone.

    The catalog name (`parkrun_snapshot`, from the filename) deliberately
    differs from the `parkrun` schema, else `parkrun.v_overlap` is ambiguous.
    Writes to a temp file and atomically replaces, so a failure never corrupts
    the committed snapshot.
    """
    tmp = SNAPSHOT_PATH.with_name(SNAPSHOT_PATH.name + ".tmp")
    for p in (tmp, Path(str(tmp) + ".wal")):
        if p.exists():
            p.unlink()

    # Phase 1: copy tables from the open dev DB into the attached fresh file.
    con.execute(f"ATTACH '{tmp}' AS snap;")
    try:
        con.execute(f"CREATE SCHEMA snap.{SCHEMA};")
        for t in SNAPSHOT_TABLES:
            con.execute(
                f"CREATE TABLE snap.{SCHEMA}.{t} AS SELECT * FROM {SCHEMA}.{t};"
            )
    finally:
        con.execute("DETACH snap;")

    # Phase 2: rebuild views with the snapshot as the default catalog, so the
    # stored definitions resolve to its own `parkrun` schema standalone.
    snap = duckdb.connect(str(tmp))
    try:
        ensure_views(snap)
        snap.execute("CHECKPOINT;")
    finally:
        snap.close()
    wal = Path(str(tmp) + ".wal")
    if wal.exists():
        wal.unlink()

    os.replace(tmp, SNAPSHOT_PATH)
    log(f"  built deploy snapshot -> {SNAPSHOT_PATH}")


def seed_from_snapshot(con: duckdb.DuckDBPyConnection, src: Path) -> None:
    """Populate an EMPTY local DB from a committed deploy snapshot.

    build_snapshot() writes its tables with CREATE TABLE AS, so the snapshot
    file carries no primary keys — simply copying it would give a DB whose
    results UPSERT (ON CONFLICT on the natural key) cannot bind. So the target
    keeps its own proper schema (ensure_schema, PKs and all) and is filled
    row-for-row from the snapshot, preserving the full history — including the
    current_targets form record, which a re-bootstrap would discard.

    Columns are read from the target BEFORE the ATTACH (both catalogs expose a
    `parkrun` schema once attached) and listed explicitly, so physical column
    order in either file is irrelevant.
    """
    if not src.exists():
        raise SystemExit(f"seed source not found: {src}")
    if is_bootstrapped(con):
        log("target already holds data; refusing to seed "
            "(delete the DB file to re-seed from scratch)")
        return

    cols = {
        t: [
            r[0]
            for r in con.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_catalog = current_database()
                  AND table_schema = ? AND table_name = ?
                ORDER BY ordinal_position
                """,
                [SCHEMA, t],
            ).fetchall()
        ]
        for t in SNAPSHOT_TABLES
    }

    log(f"seeding from snapshot: {src}")
    con.execute(f"ATTACH '{src}' AS seedsrc (READ_ONLY);")
    try:
        # A snapshot older than a table simply doesn't carry it — including the
        # disaster-recovery path in docs/DEPLOY.md, which may reach for a
        # months-old file. Seed what is there, warn about the rest, never fail.
        present = {
            r[0]
            for r in con.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_catalog = 'seedsrc' AND table_schema = ?
                """,
                [SCHEMA],
            ).fetchall()
        }
        con.execute("BEGIN;")
        try:
            for t in SNAPSHOT_TABLES:
                if t not in present:
                    log(f"  WARN: {t} absent from snapshot — seeded empty "
                        f"(older snapshot)")
                    continue
                src_cols = {
                    r[0]
                    for r in con.execute(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_catalog = 'seedsrc'
                          AND table_schema = ? AND table_name = ?
                        """,
                        [SCHEMA, t],
                    ).fetchall()
                }
                # A column may be newer than the snapshot too (current_targets
                # gained `mode`). Take it from the source when it is there,
                # else from SEED_COLUMN_DEFAULTS — same backfill value the
                # migration uses, because this is the same situation.
                missing = [c for c in cols[t] if c not in src_cols]
                if missing:
                    log(f"  WARN: {t} missing column(s) {missing} in snapshot "
                        f"— defaulted (older snapshot)")
                sel = ", ".join(
                    c if c in src_cols
                    else f"{SEED_COLUMN_DEFAULTS.get((t, c), 'NULL')} AS {c}"
                    for c in cols[t]
                )
                collist = ", ".join(cols[t])
                con.execute(
                    f"INSERT INTO {SCHEMA}.{t} ({collist}) "
                    f"SELECT {sel} FROM seedsrc.{SCHEMA}.{t};"
                )
                log(f"  seeded {t}: {_count(con, t)} rows")
            con.execute("COMMIT;")
        except Exception:
            con.execute("ROLLBACK;")
            raise
    finally:
        con.execute("DETACH seedsrc;")

    # Re-assert the handicap defaults: a pre-feature snapshot carries no
    # buggy_handicap rows, and a missing row NULLs a head-to-head target.
    seed_buggy_handicap_defaults(con)


def build_motherduck(con: duckdb.DuckDBPyConnection) -> None:
    """(Re)seed the parkrun-ONLY MotherDuck database from the local `con`.

    Same discipline as build_snapshot(): built from scratch (per-object copies,
    never a whole-DB upload) so the personal_finance schema that shares the dev
    DB can NEVER reach the cloud. The MotherDuck database is named MD_DATABASE
    (`parkrun_snapshot`), deliberately != the `parkrun` schema, so the app's
    `parkrun.v_overlap` queries stay unambiguous.

    Unlike a plain CTAS copy, the cloud schema is built with `ensure_schema()`
    so it keeps its PRIMARY KEYs — required for the in-place `refresh` upserts
    once MotherDuck is the source of truth (Stage 8.1). Data crosses via pandas
    (read from local `con`, register + INSERT into `md:`), with explicit column
    lists so physical column order can differ safely. Existing rows are carried,
    so `current_targets` history is preserved.

    Requires the `motherduck_token` env var (never hard-coded / committed).
    Idempotent: the `parkrun` schema is dropped and rebuilt on every run.
    """
    token = os.environ.get("motherduck_token") or os.environ.get("MOTHERDUCK_TOKEN")
    if not token:
        raise RuntimeError(
            "motherduck_token env var not set; cannot upload to MotherDuck. "
            "Get a token from the MotherDuck UI and export it (never paste it "
            "into source or chat)."
        )

    # Read the source tables out of the local DB first (small; held in memory).
    frames = {
        t: con.execute(f"SELECT * FROM {SCHEMA}.{t}").fetchdf() for t in SNAPSHOT_TABLES
    }

    # Ensure the MotherDuck database exists, then build a constrained schema and
    # load the data on a direct `md:` connection (its default catalog is
    # MD_DATABASE, so ensure_schema's views bind to the local `parkrun` schema).
    boot = duckdb.connect("md:")
    try:
        boot.execute(f"CREATE DATABASE IF NOT EXISTS {MD_DATABASE};")
    finally:
        boot.close()

    md = duckdb.connect(f"md:{MD_DATABASE}")
    try:
        md.execute(f"USE {MD_DATABASE};")
        md.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;")
        ensure_schema(md)  # constrained tables + views, parkrun-only
        for t in SNAPSHOT_TABLES:
            df = frames[t]
            cols = ", ".join(f'"{c}"' for c in df.columns)
            md.register("md_stage", df)
            try:
                md.execute(
                    f"INSERT INTO {SCHEMA}.{t} ({cols}) SELECT {cols} FROM md_stage;"
                )
            finally:
                md.unregister("md_stage")
    finally:
        md.close()
    log(f"  (re)seeded MotherDuck -> md:{MD_DATABASE} (constrained schema)")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _finalize(con: duckdb.DuckDBPyConnection) -> None:
    """Post-write steps shared by bootstrap and refresh: snapshot current-form
    targets, export the results CSV, and rebuild the deploy snapshot."""
    update_current_targets(con)
    export_results_snapshot(con)
    export_run_modes(con)
    build_snapshot(con)


def bootstrap(con: duckdb.DuckDBPyConnection) -> None:
    log("BOOTSTRAP: empty DB")
    seed_static_tables(con)
    seed_events_from_csv(con)
    upsert_results(con)
    _finalize(con)


def refresh(con: duckdb.DuckDBPyConnection) -> None:
    if not is_bootstrapped(con):
        bootstrap(con)
        return
    reconcile_events(con)  # Path A (independent)
    upsert_results(con)  # Path B (runs regardless of Path A)
    _finalize(con)


def status(con: duckdb.DuckDBPyConnection) -> None:
    for t in SNAPSHOT_TABLES:
        print(f"  {SCHEMA}.{t:<18} {_count(con, t):>6} rows")
    live = con.execute(
        f"SELECT count(*) FROM {SCHEMA}.events WHERE live"
    ).fetchone()[0]
    print(f"  (events live = {live})")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    # Target DB: local dev file by default, or a MotherDuck connection string
    # (e.g. md:parkrun_snapshot) when MotherDuck is the source of truth. All
    # pipeline SQL is `parkrun.`-schema-qualified, so it operates on whichever
    # catalog is the connection default.
    target = os.environ.get("PARKRUN_PIPELINE_DB", str(DB_PATH))
    is_md = target.startswith("md:")
    log(f"target DB: {target}")
    con = duckdb.connect(target)
    try:
        ensure_schema(con)
        ensure_migrations(con)
        if cmd == "bootstrap":
            if is_bootstrapped(con):
                log("already bootstrapped; use 'refresh'")
            else:
                bootstrap(con)
        elif cmd == "refresh":
            refresh(con)
        elif cmd == "status":
            status(con)
        elif cmd == "snapshot":
            if is_bootstrapped(con):
                build_snapshot(con)
            else:
                log("nothing to snapshot; bootstrap/refresh first")
        elif cmd == "seed":
            # Fill an empty local DB from a deploy snapshot (default: the
            # committed one) instead of re-bootstrapping, which would lose the
            # accumulated current_targets history.
            if is_md:
                log("refusing 'seed' with an md: target — seed a local DB file")
                sys.exit(1)
            seed_from_snapshot(
                con, Path(sys.argv[2]) if len(sys.argv) > 2 else SNAPSHOT_PATH
            )
        elif cmd == "motherduck":
            # (Re)seed the cloud FROM a local DB; sourcing from md: is nonsensical.
            if is_md:
                log("refusing 'motherduck' with an md: target — run it against "
                    "the local DB (unset PARKRUN_PIPELINE_DB)")
                sys.exit(1)
            elif is_bootstrapped(con):
                build_motherduck(con)
            else:
                log("nothing to upload; bootstrap/refresh first")
        else:
            print(__doc__)
            sys.exit(1)
        print()
        status(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()

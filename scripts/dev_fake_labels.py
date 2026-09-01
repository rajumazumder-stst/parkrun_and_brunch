#!/usr/bin/env python3
"""Write PLAUSIBLE FAKE buggy labels into a dev database, for previewing the UI.

The real labels come from George and Duncan via the review spreadsheet
(scripts/export_buggy_review.py). Until that comes back, the buggy-mode UI has
nothing to show, so this fabricates a set that exercises every path: a sustained
buggy era, a scattering of one-off buggy runs, both `manual` and `estimated`
sources, and an athlete (Raju) who never uses one.

It labels a portion of George's and Duncan's SLOWER runs, measured against each
athlete's own contemporaneous form rather than a flat time, so the fake data
looks like a real buggy penalty instead of noise.

DEV ONLY. Refuses to touch the source of truth or the deploy snapshot.

    python scripts/dev_fake_labels.py [--db data/parkrun_dev.duckdb] [--clear]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))      # so `import parkrun_pipeline` works from scripts/
SNAPSHOT = REPO / "data" / "parkrun_snapshot.duckdb"
SOURCE_OF_TRUTH = Path.home() / ".config" / "parkrun" / "parkrun_local.duckdb"

DUNCAN, GEORGE, RAJU = 5462426, 3087156, 5672

# Duncan gets a sustained era, which is what a real buggy looks like in the data
# (and what the head-to-head's per-mode median needs in order to have anything
# to average). George gets scattered one-offs, which is the harder case for the
# eventual estimator and the more interesting one for the UI.
ERA_START = "2026-03-01"
ERA_SLOWER_THAN = 0.02      # in the era, label runs >2% off form
SCATTER_QUANTILE = 0.75     # George: his slowest quarter, since 2025
SCATTER_FROM = "2025-01-01"
ESTIMATED_SHARE = 4         # every Nth fake label is an 'estimated' one


def guard(db: Path) -> None:
    for forbidden, what in ((SOURCE_OF_TRUTH, "source of truth"),
                            (SNAPSHOT, "deploy snapshot")):
        if forbidden.exists() and db.resolve() == forbidden.resolve():
            raise SystemExit(f"refusing to write fake labels to the {what} ({db})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=REPO / "data" / "parkrun_dev.duckdb")
    ap.add_argument("--clear", action="store_true",
                    help="delete existing labels first")
    args = ap.parse_args()
    guard(args.db)
    if not args.db.exists():
        raise SystemExit(f"{args.db} does not exist — run ./scripts/run_local.sh first")

    con = duckdb.connect(str(args.db))
    if args.clear:
        con.execute("DELETE FROM parkrun.run_modes")

    runs = con.execute(
        """
        SELECT athlete_id, run_date, event_id, time_seconds
        FROM parkrun.results
        WHERE time_seconds IS NOT NULL
        ORDER BY athlete_id, run_date
        """
    ).fetchdf()
    runs["run_date"] = pd.to_datetime(runs["run_date"])

    # Form = each athlete's trailing 20-run median, so "slower" means slower
    # than they were running AT THE TIME, not slower than their career.
    runs["form"] = (
        runs.groupby("athlete_id")["time_seconds"]
            .transform(lambda s: s.shift(1).rolling(20, min_periods=5).median())
    )
    runs["excess"] = runs["time_seconds"] / runs["form"] - 1

    buggy = pd.Series(False, index=runs.index)

    era = ((runs.athlete_id == DUNCAN)
           & (runs.run_date >= ERA_START)
           & (runs.excess > ERA_SLOWER_THAN))
    buggy |= era

    g = runs[(runs.athlete_id == GEORGE) & (runs.run_date >= SCATTER_FROM)]
    if not g.empty:
        cut = g["excess"].quantile(SCATTER_QUANTILE)
        buggy |= runs.index.isin(g[g["excess"] > cut].index)

    picked = runs[buggy].copy()
    # A few labelled 'estimated' so the "(estimated)" marker is exercised — a
    # guess must read differently from a confirmation in the UI.
    picked = picked.reset_index(drop=True)
    picked["source"] = ["estimated" if i % ESTIMATED_SHARE == 0 else "manual"
                        for i in range(len(picked))]
    picked["confidence"] = [0.62 + 0.3 * ((i * 7) % 10) / 10 if s == "estimated"
                            else None
                            for i, s in enumerate(picked["source"])]
    picked["is_buggy"] = True
    picked["reason"] = "FAKE dev label"

    con.register("fake", picked[["athlete_id", "run_date", "event_id",
                                 "is_buggy", "source", "confidence", "reason"]])
    con.execute(
        """
        INSERT OR REPLACE INTO parkrun.run_modes
              (athlete_id, run_date, event_id, is_buggy, source, confidence,
               reason, set_at)
        SELECT athlete_id, run_date::date, event_id, is_buggy, source,
               confidence, reason, now() FROM fake
        """
    )
    # Everything else is non-buggy, so the anti-join converges exactly as it
    # will after the real import.
    con.execute(
        """
        INSERT INTO parkrun.run_modes
              (athlete_id, run_date, event_id, is_buggy, source, reason, set_at)
        SELECT r.athlete_id, r.run_date, r.event_id, FALSE, 'default',
               'FAKE dev backfill', now()
        FROM parkrun.results r
        WHERE NOT EXISTS (SELECT 1 FROM parkrun.run_modes m
                          WHERE m.athlete_id = r.athlete_id
                            AND m.run_date  = r.run_date
                            AND m.event_id  = r.event_id)
        """
    )
    con.unregister("fake")

    import parkrun_pipeline as p  # noqa: E402  (imported late: dev-only path)
    p.update_current_targets(con)

    print(con.execute(
        """
        SELECT a.athlete_name, m.source, m.is_buggy, count(*) AS runs
        FROM parkrun.run_modes m JOIN parkrun.athletes a USING (athlete_id)
        GROUP BY 1,2,3 ORDER BY 1,2,3
        """
    ).fetchdf().to_string(index=False))
    print("\nhead-to-head target bases:")
    print(con.execute(
        "SELECT target_basis, count(*) AS rows FROM parkrun.v_head_to_head "
        "GROUP BY 1 ORDER BY 2 DESC"
    ).fetchdf().to_string(index=False))
    con.close()
    print(f"\nFAKE labels written to {args.db}. Delete the file to reset.")


if __name__ == "__main__":
    main()

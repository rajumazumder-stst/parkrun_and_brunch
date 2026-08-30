#!/usr/bin/env python3
"""Export a buggy/non-buggy confirmation spreadsheet for George and Duncan.

One tab per athlete, one row per parkrun from --since (default 2025-01-01),
carrying the estimator's guess plus the evidence behind it so the athlete is
correcting against numbers rather than recalling dates. The `Confirmed` column
is theirs to fill in (dropdown: With buggy / Without buggy).

The estimator is deliberately a high-precision tripwire, not an era detector —
see the plan/docs. Per-run thresholds cannot separate a buggy (+10-14%) from a
hard course (+20-33%), so almost every row comes back "Without buggy" and the
real signal is the `Excess` column read as a run of consecutive same-sign
deviations.

Build-time only. Needs openpyxl, which is deliberately NOT in requirements.txt
(same convention as scripts/build_logo.py) — install it into the dev venv.

Usage:
    python scripts/export_buggy_review.py [--since 2025-01-01] [--out PATH]
"""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

REPO = Path(__file__).resolve().parent.parent

# Athletes with a buggy dimension. Raju (5672) never uses one and is the
# negative control the threshold is calibrated against — he is not exported.
ATHLETES = ("George", "Duncan")

# Calibrated so Raju is never flagged. At 0.08 the rule flags ~40 of his runs.
BUGGY_THRESHOLD = 0.50
BUGGY_MIN_EVENT_RUNS = 2
BUGGY_MIN_WINDOW_RUNS = 8
BUGGY_WINDOW_DAYS = 182

# Ground truth supplied by the athletes: runs at these parkruns are never with
# a buggy, whatever the times say. Overrides the rule.
NEVER_BUGGY = {
    "George": {"Egham Orbit"},
}

WITH_BUGGY = "With buggy"
WITHOUT_BUGGY = "Without buggy"

COLUMNS = [
    ("Date", 12),
    ("parkrun", 38),
    ("Time", 9),
    ("Position", 9),
    ("Age grade", 11),
    ("Baseline", 10),
    ("Excess", 9),
    ("Basis", 9),
    ("Estimate", 14),
    ("Why", 46),
    ("Confirmed", 14),
]

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
FLAG_FILL = PatternFill("solid", fgColor="FFE699")


def resolve_db() -> str:
    """Same fallback order as app.py: env var, then the bundled snapshot."""
    env = os.environ.get("PARKRUN_DB")
    if env:
        return os.path.expanduser(env)
    local = Path.home() / ".config" / "parkrun" / "parkrun_local.duckdb"
    if local.exists():
        return str(local)
    return str(REPO / "data" / "parkrun_snapshot.duckdb")


def load_results(db: str) -> pd.DataFrame:
    con = duckdb.connect(db, read_only=True)
    try:
        df = con.execute(
            """
            SELECT r.athlete_id, a.athlete_name, r.run_date, r.event_id,
                   e.short_name, r.time, r.time_seconds, r.position, r.age_grade
            FROM parkrun.results r
            JOIN parkrun.athletes a USING (athlete_id)
            JOIN parkrun.events e USING (event_id)
            """
        ).fetchdf()
    finally:
        con.close()
    df["run_date"] = pd.to_datetime(df["run_date"])
    return df


def fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def add_estimates(df: pd.DataFrame) -> pd.DataFrame:
    """Attach expected time, basis and excess to every run.

    Baselines, first rule that fires:
      1. `event`  — median of that athlete's OTHER runs at the same parkrun.
                    Course-controlled, so the only clean baseline.
      2. `window` — 25th percentile of their runs within +/-182 days. q25 not
                    median: a buggy only ever makes you slower, so the fast tail
                    is the buggy-immune part of the distribution.
      3. `abstain` — not enough history either way.
    """
    out = []
    for i, r in df.iterrows():
        others = df[(df.athlete_id == r.athlete_id) & (df.index != i)]
        same_event = others[others.event_id == r.event_id]
        if len(same_event) >= BUGGY_MIN_EVENT_RUNS:
            out.append((same_event.time_seconds.median(), "event", len(same_event)))
            continue
        near = others[
            (others.run_date - r.run_date).abs().dt.days <= BUGGY_WINDOW_DAYS
        ]
        if len(near) >= BUGGY_MIN_WINDOW_RUNS:
            out.append((near.time_seconds.quantile(0.25), "window", len(near)))
        else:
            out.append((np.nan, "abstain", 0))

    df[["expected", "basis", "n_base"]] = pd.DataFrame(out, index=df.index)
    df["excess"] = df.time_seconds / df.expected - 1
    return df


def estimate_row(r: pd.Series) -> tuple[str, str]:
    """Return (estimate, why) for one run."""
    never = NEVER_BUGGY.get(r.athlete_name, set())
    if r.short_name in never:
        return WITHOUT_BUGGY, f"{r.short_name} confirmed never with buggy"
    if r.basis == "abstain":
        return WITHOUT_BUGGY, "no baseline — too little history"

    label = "event median" if r.basis == "event" else "6-month q25"
    why = (
        f"{r.time} vs {fmt_time(r.expected)} {label} "
        f"({r.excess:+.1%}, n={r.n_base})"
    )
    if r.excess > BUGGY_THRESHOLD:
        return WITH_BUGGY, why
    return WITHOUT_BUGGY, why


def write_sheet(ws, rows: pd.DataFrame) -> None:
    ws.append([c for c, _ in COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")

    for _, r in rows.iterrows():
        ws.append(
            [
                r.run_date.date(),
                r.short_name,
                r.time,
                int(r.position) if pd.notna(r.position) else None,
                r.age_grade,
                fmt_time(r.expected) if pd.notna(r.expected) else "",
                round(float(r.excess), 4) if pd.notna(r.excess) else None,
                r.basis,
                r.estimate,
                r.why,
                None,
            ]
        )

    for idx, (_, width) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    for row in ws.iter_rows(min_row=2, min_col=1, max_col=len(COLUMNS)):
        row[0].number_format = "yyyy-mm-dd"
        row[6].number_format = "+0.0%;-0.0%"

    # Highlight the rows the estimator actually flagged.
    est_idx = COLUMNS.index(("Estimate", 14))
    for row in ws.iter_rows(min_row=2, max_col=len(COLUMNS)):
        if row[est_idx].value == WITH_BUGGY:
            for cell in row:
                cell.fill = FLAG_FILL

    # showDropDown is inverted in the spec: False means "show the arrow".
    # showErrorMessage must be True or Google Sheets discards the validation
    # entirely on import.
    dv = DataValidation(
        type="list",
        formula1=f'"{WITH_BUGGY},{WITHOUT_BUGGY}"',
        allow_blank=True,
        showDropDown=False,
        showErrorMessage=True,
        errorTitle="Pick one",
        error=f"Choose {WITH_BUGGY} or {WITHOUT_BUGGY}.",
    )
    ws.add_data_validation(dv)
    confirmed_col = get_column_letter(len(COLUMNS))
    dv.add(f"{confirmed_col}2:{confirmed_col}{ws.max_row}")

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{ws.max_row}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2025-01-01")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    db = resolve_db()
    df = add_estimates(load_results(db))

    out = Path(args.out) if args.out else (
        REPO / "data" / f"buggy_review_{date.today():%Y-%m-%d}.xlsx"
    )

    # Built directly with openpyxl rather than through pandas' ExcelWriter: the
    # rows need per-cell formatting and a dropdown, and the workbook needs the
    # Google Sheets fixes below, neither of which to_excel can express.
    book = Workbook()
    book.remove(book.active)
    # openpyxl emits an empty <workbookProtection/> by default. Google Sheets
    # refuses to import a workbook carrying it, so drop it.
    book.security = None

    for name in ATHLETES:
        rows = df[
            (df.athlete_name == name) & (df.run_date >= args.since)
        ].sort_values("run_date").copy()
        est = rows.apply(estimate_row, axis=1, result_type="expand")
        rows["estimate"], rows["why"] = est[0], est[1]
        ws = book.create_sheet(title=name)
        write_sheet(ws, rows)
        n_flag = int((rows.estimate == WITH_BUGGY).sum())
        print(f"{name:8} {len(rows):3} runs since {args.since}  "
              f"{n_flag} estimated with buggy")

    book.save(out)

    print(f"\nsource DB : {db}")
    print(f"written   : {out}")
    print(f"generated : {datetime.now():%Y-%m-%d %H:%M}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch the published UK parkrun course-difficulty scores to a tracked CSV.

One HTTP GET of the Running Channel's article; the full 835-course dataset is
in the page, so there is nothing to crawl. Run it by hand — the scheduled
refresh never touches the network for this, it applies the cached CSV.

Source semantics (per the article): the first number is the course's rank from
fastest (1) to slowest (835); the second (0.8-11.6) is its difficulty on a 0-12
scale, 12 being hardest, based on median finish times. Derived from RunBritain
SSS over roughly 1 Jan 2023 - 25 Jan 2025.

Why this source over 13milers.com: both publish the same 835-course dataset, but
this one exposes the difficulty MAGNITUDE where 13milers exposes only the rank,
and a rank throws away the spacing between courses. Measured against our own
data the magnitude wins for every athlete (R2 14.0/6.2/21.5% vs 12.3/6.0/19.9%).
Keep 13milers as the documented fallback if this page is ever reformatted.

Fragility, accepted: this parses a WordPress article's markup, so a reformat
breaks the parser. That is fine — the CSV is the artefact of record, it is
tracked and hand-correctable, and a breakage here can never block a refresh.

Usage:
    python scripts/fetch_course_difficulty.py [--out data/course_difficulty.csv]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "data" / "course_difficulty.csv"

URL = "https://therunningchannel.com/fastest-and-slowest-parkruns-uk/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

# "<rank> <name> <difficulty>", e.g. "1 Pegwell Bay 0.8". The difficulty always
# carries a decimal point, which is what keeps a name ending in a number from
# being mistaken for it.
ROW_RE = re.compile(r"^(\d+)\s+(.+?)\s+(\d+\.\d+)$")

EXPECTED_ROWS = 835     # what the article publishes; a big drop means a reformat
MIN_ROWS = 700          # below this, refuse to overwrite the cached CSV

COLUMNS = ["parkrun_name", "difficulty", "speed_rank", "alias_of", "fetched_at"]


def parse(html: str) -> list[dict]:
    """Extract (rank, name, difficulty) rows from the article.

    The data is a <ul> of <li>, not a <table> — no <table> element exists on the
    page. The list is rendered TWICE (by rank, then alphabetically), so dedupe
    by name, keeping the first occurrence. Watch near-duplicate names: 'Y
    Promenâd' (rank 472) and 'Y Promenâd, Abermaw' (rank 109) are distinct
    courses, which is why the key is the full name and not a prefix.
    """
    soup = BeautifulSoup(html, "lxml")
    seen: dict[str, dict] = {}
    skipped = 0
    for li in soup.find_all("li"):
        m = ROW_RE.match(li.get_text(" ", strip=True))
        if not m:
            continue
        rank, name, difficulty = m.group(1), m.group(2).strip(), m.group(3)
        if name in seen:
            skipped += 1
            continue
        seen[name] = {
            "parkrun_name": name,
            "difficulty": float(difficulty),
            "speed_rank": int(rank),
            "alias_of": "",
            "fetched_at": date.today().isoformat(),
        }
    print(f"  parsed {len(seen)} courses ({skipped} duplicate rows skipped)")
    return sorted(seen.values(), key=lambda r: r["speed_rank"])


def merge_aliases(rows: list[dict], out: Path) -> None:
    """Carry hand-maintained `alias_of` values across a re-fetch.

    alias_of maps a published name onto the `events.short_name` it corresponds
    to, for the courses whose names differ between the two sources. It is
    entered by hand and must survive re-running this script.
    """
    if not out.exists():
        return
    with out.open(newline="", encoding="utf-8") as fh:
        existing = {
            r["parkrun_name"]: r.get("alias_of", "")
            for r in csv.DictReader(fh)
            if r.get("alias_of")
        }
    kept = 0
    for r in rows:
        if existing.get(r["parkrun_name"]):
            r["alias_of"] = existing[r["parkrun_name"]]
            kept += 1
    if kept:
        print(f"  carried {kept} hand-maintained alias_of value(s) forward")
    lost = set(existing) - {r["parkrun_name"] for r in rows}
    if lost:
        print(f"  WARNING: {len(lost)} aliased name(s) no longer published: "
              f"{sorted(lost)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    print(f"fetching {URL}")
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    print(f"  HTTP {resp.status_code}, {len(resp.text)} bytes")

    rows = parse(resp.text)
    if len(rows) < MIN_ROWS:
        sys.exit(
            f"ERROR: only {len(rows)} courses parsed (expected ~{EXPECTED_ROWS}). "
            f"The page has probably been reformatted — the cached CSV at "
            f"{args.out} is left untouched. Fix the parser, or fall back to "
            f"13milers.com (see the module docstring)."
        )
    if len(rows) != EXPECTED_ROWS:
        print(f"  NOTE: {len(rows)} courses, expected {EXPECTED_ROWS} — the "
              f"dataset may have been updated")

    merge_aliases(rows, args.out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows)} rows -> {args.out}")
    print("Next: python parkrun_pipeline.py refresh (or `status`) applies it "
          "and logs coverage.")


if __name__ == "__main__":
    main()

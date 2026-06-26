"""Extract parkrun run times (All Results table) for the tracked athletes.

Fetches each athlete's full results page, parses the All Results table, and
writes a combined dataset with athlete_id and scrape_timestamp added.
"""

import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

ATHLETE_IDS = [5672, 5462426, 3087156]
BASE_URL = "https://www.parkrun.org.uk/parkrunner/{athlete_id}/all/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
REQUEST_DELAY_SECONDS = 2

COLUMN_MAP = {
    "Event": "event",
    "Run Date": "run_date",
    "Run Number": "run_number",
    "Pos": "position",
    "Time": "time",
    "Age Grade": "age_grade",
    "PB?": "pb_flag",
}


def fetch_results(athlete_id: int) -> pd.DataFrame:
    """Return the All Results table for one athlete as a DataFrame."""
    url = BASE_URL.format(athlete_id=athlete_id)
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html5lib")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError(f"No tables found for athlete {athlete_id}")

    # The All Results table is the last table on the page.
    df = pd.read_html(StringIO(str(tables[-1])))[0]
    df = df.rename(columns=COLUMN_MAP)
    df.insert(0, "athlete_id", athlete_id)
    return df


def main() -> None:
    scrape_timestamp = datetime.now(timezone.utc)
    frames = []

    for athlete_id in ATHLETE_IDS:
        print(f"Fetching athlete {athlete_id} ...")
        df = fetch_results(athlete_id)
        print(f"  -> {len(df)} results")
        frames.append(df)
        time.sleep(REQUEST_DELAY_SECONDS)

    combined = pd.concat(frames, ignore_index=True)
    combined["run_date"] = pd.to_datetime(
        combined["run_date"], format="%d/%m/%Y"
    ).dt.date
    combined["scrape_timestamp"] = scrape_timestamp

    # Normalise time strings (MM:SS or H:MM:SS) to a duration for comparison.
    parts = combined["time"].str.split(":", expand=True).astype(float)
    if parts.shape[1] == 2:
        parts.insert(0, "h", 0.0)
    time_seconds = parts.iloc[:, 0] * 3600 + parts.iloc[:, 1] * 60 + parts.iloc[:, 2]

    out_dir = Path(__file__).parent / "data"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "parkrun_results.csv"
    combined.to_csv(out_path, index=False)

    print(f"\nTotal results: {len(combined)}")
    print(combined.groupby("athlete_id").size().to_string())
    print(f"\nFastest time per athlete:")
    fastest = combined.loc[time_seconds.groupby(combined["athlete_id"]).idxmin()]
    print(fastest[["athlete_id", "event", "run_date", "time", "age_grade"]].to_string(index=False))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()

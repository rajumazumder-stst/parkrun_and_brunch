"""Milestone arithmetic for the tab 1 milestone matrix — pure, no streamlit,
no database. `milestone_matrix.py` draws the table; this decides what it says.

The Nth run is the Nth entry of the athlete's dates sorted, never the scraped
`run_number`: that column is the *event's* run number, shared by everyone who
ran that event that day.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable

from milestone_config import milestones_for


@dataclass(frozen=True)
class Milestone:
    n: int
    date: dt.date
    days_since_first: int
    # From the previous milestone reached, or from the first run when this is
    # the first one.
    days_since_prev: int


@dataclass(frozen=True)
class MilestoneSummary:
    total: int
    first_date: dt.date | None
    last_date: dt.date | None
    days_since_first: int | None
    days_since_last: int | None
    # First run to latest run — how long they have been at it, independent
    # of today.
    days_first_to_last: int | None
    reached: list[Milestone] = field(default_factory=list)
    next_milestone: int | None = None
    runs_to_go: int | None = None


def milestone_summary(run_dates: Iterable[dt.date], *, junior: bool = False,
                      today: dt.date | None = None) -> MilestoneSummary:
    """Totals, milestones reached and the next one, from a participant's run
    dates. A date appears once per run, so a same-day double is two entries.
    `today` defaults to the local date; pass it to pin the day counts."""
    dates = sorted(run_dates)
    today = today or dt.date.today()
    total = len(dates)
    ladder = milestones_for(junior)

    reached: list[Milestone] = []
    if dates:
        first = dates[0]
        prev = first
        for m in ladder:
            if m > total:
                break
            d = dates[m - 1]
            reached.append(Milestone(m, d, (d - first).days, (d - prev).days))
            prev = d

    upcoming = [m for m in ladder if m > total]
    nxt = upcoming[0] if upcoming else None
    return MilestoneSummary(
        total=total,
        first_date=dates[0] if dates else None,
        last_date=dates[-1] if dates else None,
        days_since_first=(today - dates[0]).days if dates else None,
        days_since_last=(today - dates[-1]).days if dates else None,
        days_first_to_last=(dates[-1] - dates[0]).days if dates else None,
        reached=reached,
        next_milestone=nxt,
        runs_to_go=(nxt - total) if nxt is not None else None,
    )

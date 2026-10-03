"""Win/loss streaks — the arithmetic, and nothing else.

For each athlete, one streak per head-to-head classification they are part
of, plus one over all of them (ANY). A streak is the run of identical results
counted back from their most recent head-to-head, so it is a win streak or a
loss streak depending on how that last one went. Beside it, their best-ever
win streak in the same scope.

**A win is 1st place** on the form-adjusted ranking — the same win the record
leaderboard and the cumulative-1sts chart count. Anything else is a loss, so a
2nd in a three-way is a loss. A dead heat for 1st is a win for both runners,
as in the calendar's tallies.

Imports nothing but pandas, so it is testable without an app runtime, and the
tab 2 section (`h2h_streaks.py`) and the dev bench (`streaks_proto.py`) share
one implementation — two copies would let the bench and the app disagree
about what a streak is.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

# The scope that pools every classification an athlete is in.
ANY = "Any"
# The heatmap column for a head-to-head of all three.
THREE_WAY = "Three-way"

COLUMNS = ["athlete_name", "scope", "won", "n", "since", "last_date",
           "best_n", "best_since", "best_until", "live"]


def _runs(results: pd.DataFrame) -> pd.DataFrame:
    """Collapse one athlete's date-ordered results in one scope into runs of
    identical outcome: won, n, since, until — oldest run first."""
    won = results["won"]
    run_id = (won != won.shift()).cumsum()
    return (results.groupby(run_id, sort=True)
            .agg(won=("won", "first"), n=("won", "size"),
                 since=("run_date", "min"), until=("run_date", "max"))
            .reset_index(drop=True))


def win_streaks(h2h: pd.DataFrame) -> pd.DataFrame:
    """One row per (athlete, scope): the current streak and the best-ever win
    streak. `scope` is a classification, or ANY for all of them pooled.

    Results are ordered by date, then `event_id`: no athlete has two
    head-to-heads on one day today, so the tie-break only keeps the answer
    deterministic. Of two equally long win streaks the later is the best, so
    a current win streak that equals the record *is* the record, and `live`
    says so. A losing athlete is never live.
    """
    if h2h.empty:
        return pd.DataFrame(columns=COLUMNS)

    base = h2h[["athlete_name", "classification", "run_date", "event_id",
                "place_rank"]].assign(won=lambda d: d["place_rank"] == 1)
    scoped = pd.concat([
        base.rename(columns={"classification": "scope"}),
        base.drop(columns="classification").assign(scope=ANY),
    ])

    rows = []
    for (name, scope), part in scoped.groupby(["athlete_name", "scope"], sort=True):
        runs = _runs(part.sort_values(["run_date", "event_id"]))
        cur = runs.iloc[-1]
        wins = runs[runs["won"]]
        if wins.empty:
            best_n, best_since, best_until = 0, pd.NaT, pd.NaT
        else:
            # Stable sort, oldest first, so the last of the longest is the latest.
            best = wins.sort_values("n", kind="stable").iloc[-1]
            best_n, best_since, best_until = int(best["n"]), best["since"], best["until"]
        rows.append(dict(
            athlete_name=name, scope=scope, won=bool(cur["won"]), n=int(cur["n"]),
            since=cur["since"], last_date=cur["until"],
            best_n=best_n, best_since=best_since, best_until=best_until,
            live=bool(cur["won"]) and best_until == cur["until"],
        ))
    return pd.DataFrame(rows, columns=COLUMNS)


def opponents_label(name: str, scope: str) -> str:
    """Who the streak is against, for a display that already names the
    athlete: "George vs Raju" seen from George is "vs Raju"."""
    if scope == ANY:
        return ANY
    others = [p for p in scope.split(" vs ") if p != name]
    return "vs " + " & ".join(others)


def heatmap_column(name: str, scope: str) -> str:
    """The heatmap column a scope lands in, seen from `name`'s row: ANY, the
    opponent of a one-on-one ("vs Raju"), or THREE_WAY."""
    if scope == ANY:
        return ANY
    if scope.count(" vs ") >= 2:
        return THREE_WAY
    return opponents_label(name, scope)


def heatmap_columns(order) -> list[str]:
    """Fixed columns for every row: ANY, "vs" each athlete in row order, then
    THREE_WAY. Following the row order puts each athlete's own cell on the
    diagonal."""
    return [ANY] + [f"vs {n}" for n in order] + [THREE_WAY]


def scope_order(name: str, scopes) -> list[str]:
    """ANY first, then the one-on-ones by opponent, then the three-way."""
    rest = [s for s in scopes if s != ANY]
    rest.sort(key=lambda s: (s.count(" vs "), opponents_label(name, s)))
    return [ANY] + rest


def uk_date(d) -> str:
    """"3 Oct 2026": day without a leading zero. Built by hand because `%-d`
    is a glibc/BSD extension that Windows' strftime rejects."""
    d = pd.Timestamp(d)
    return f"{d.day} {d:%b %Y}"


def best_range(since: dt.date, until: dt.date) -> str:
    """Month span of a streak, as short as is unambiguous: "Mar 2024",
    "Mar–Jul 2024", "Nov 2023–Feb 2024"."""
    if (since.year, since.month) == (until.year, until.month):
        return since.strftime("%b %Y")
    if since.year == until.year:
        return f"{since:%b}–{until:%b %Y}"
    return f"{since:%b %Y}–{until:%b %Y}"


def best_period(since: dt.date, until: dt.date, live: bool) -> str:
    """The best streak's months. A live one is still running, so it ends
    "now" rather than at its latest result: "Apr 2026–now"."""
    if live:
        return f"{since:%b %Y}–now"
    return best_range(since, until)

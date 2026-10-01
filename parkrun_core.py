"""Facts every part of the project has to agree on.

**This module imports nothing but the standard library, and it must stay that
way.** That constraint is the whole reason it exists. `parkrun_pipeline.py`
owns most of these definitions naturally, but it imports `requests` and
`bs4` at module level, and the hosted app installs neither — so the app and
the estimator could not import the pipeline even to read one integer. Each
kept its own copy instead, and `TARGET_WINDOW_DAYS` ended up written out three
times, with a test that read the pipeline's *source text* to check they still
matched.

Anything here is something two or more of the pipeline, the app, the estimator
and the review script must not disagree about. Anything only one of them needs
belongs in that one.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent
SNAPSHOT = REPO / "data" / "parkrun_snapshot.duckdb"
LOCAL_DB = Path.home() / ".config" / "parkrun" / "parkrun_local.duckdb"

SCHEMA = "parkrun"

# --- the cohort -------------------------------------------------------------
# Fixed. The mapping drives the per-athlete columns in v_overlap.
ATHLETE_NAMES = {5672: "raju", 5462426: "duncan", 3087156: "george"}
ATHLETE_IDS = list(ATHLETE_NAMES)

# Athletes who ever push a buggy. Raju never does, so he has no buggy form to
# record and gets no buggy row at all — not an empty one. The analytics views
# stay symmetric (he falls out as all-nonbuggy on his own); this only narrows
# what the materialised current_targets grid stores, and who the estimator
# scores. Display capitalisation, because the estimator's report and the
# refresh notification both print these names.
BUGGY_ATHLETES = {3087156: "George", 5462426: "Duncan"}
BUGGY_ATHLETE_IDS = tuple(BUGGY_ATHLETES)

# --- the form window --------------------------------------------------------
# Head-to-head target, v_saturday_targets, current_targets, the app's
# "runs in window" popover, and the estimator's form residual all use this one
# number. They must agree: the popover exists to show *which runs made the
# target*, so a drift between them would list a different window than the
# median was actually taken over — and it would look right.
TARGET_WINDOW_DAYS = 91

# --- label provenance -------------------------------------------------------
# Who said so, which is what `run_modes.source` means.
#   user  — you: the review sheet, a hand correction, a blanket assertion
#   model — buggy_estimator, on a run with no label
#   rule  — a deterministic rule, no judgement involved
LABEL_SOURCES = {"user", "model", "rule"}

# Only these train the estimator. A `rule` row states what a rule says must be
# true, not evidence about the run, and hundreds of them would swamp the
# evidence that exists. Rule rows are still used for *features*: a real run with
# a real time belongs in a form window and a course baseline whoever labelled
# it. It is only its label that carries no information.
TRAINING_SOURCES = ("user", "model")


# --- mainland Great Britain ------------------------------------------------
# The tab 5 planner and parkrun_travel must agree on which parkruns are
# candidates: the travel step routes only these, and the app lists only these
# when it has no drive times to go on.
class Box(NamedTuple):
    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def holds(self, lat: float, lon: float) -> bool:
        return (self.lat_min <= lat <= self.lat_max
                and self.lon_min <= lon <= self.lon_max)


# Everything parkrun files under the UK (country 97) that is NOT reachable from
# Great Britain by road. Country 97 is wider than it looks: it includes the
# Falklands, St Helena, the Cayman Islands and Gibraltar as well as Northern
# Ireland and the Crown Dependencies. Road-bridged islands — Anglesey, Skye,
# Hayling — are mainland here and must stay outside every box; the tests pin
# the awkward neighbours (Lymington beside the Isle of Wight, Dunoon beside
# Bute, Oban and Crinan beside Mull and Jura, Skye beside the Hebrides).
#
# Boxes rather than a routing flag because the OSRM demo server rejects
# `exclude=ferry`, and ORS's matrix endpoint cannot avoid ferries either: both
# happily return a drive to Belfast with the crossing folded into the time.
UK_COUNTRY_CODE = 97
GB_EXTENT = Box("Great Britain", 49.85, 60.95, -8.7, 1.8)
NON_MAINLAND = (
    Box("Channel Islands", 49.0, 49.8, -3.0, -2.0),
    Box("Isles of Scilly", 49.85, 50.0, -6.5, -6.1),
    Box("Northern Ireland", 54.0, 55.35, -8.3, -5.4),
    Box("Isle of Man", 54.03, 54.43, -4.85, -4.3),
    # The west end stops short of Lymington (50.748, -1.545), which is not.
    Box("Isle of Wight (west)", 50.57, 50.725, -1.6, -1.45),
    Box("Isle of Wight", 50.57, 50.775, -1.45, -1.05),
    Box("Arran", 55.43, 55.72, -5.45, -5.05),
    Box("Bute", 55.72, 55.92, -5.2, -4.98),
    Box("Islay and Jura", 55.55, 56.15, -6.6, -5.85),
    Box("Mull", 56.27, 56.65, -6.5, -5.8),
    Box("Outer Hebrides (Lewis, Harris)", 57.75, 58.6, -7.2, -6.1),
    Box("Outer Hebrides (Uists, Barra)", 56.75, 57.75, -7.8, -6.9),
    Box("Orkney and Shetland", 58.7, 61.0, -3.5, -0.5),
)


def is_mainland(lat, lon) -> bool:
    """True when (lat, lon) is on Great Britain or a road-bridged island."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if math.isnan(lat) or math.isnan(lon) or not GB_EXTENT.holds(lat, lon):
        return False
    return not any(b.holds(lat, lon) for b in NON_MAINLAND)


def resolve_db(db: str | None = None) -> str:
    """The database to read, in the order every entry point uses.

    Explicit argument, then ``PARKRUN_DB``, then the local source of truth,
    then the committed deploy snapshot. The snapshot is last because it is a
    build artefact: correct to read, never correct to write (see
    ``scripts/export_buggy_review.resolve_db``, which refuses to).
    """
    if db:
        return os.path.expanduser(db)
    env = os.environ.get("PARKRUN_DB")
    if env:
        return os.path.expanduser(env)
    return str(LOCAL_DB if LOCAL_DB.exists() else SNAPSHOT)

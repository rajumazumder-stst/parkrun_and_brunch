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

import os
from pathlib import Path

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

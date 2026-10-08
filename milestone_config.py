"""The parkrun milestones and their official colours — defined here and nowhere
else. `milestones.py` (the arithmetic) and `milestone_matrix.py` (the tab 1
table) both read from this module. Also the unofficial unique-parkruns ladder
(`UNIQUE_MILESTONES`) read by `venue_matrix.py`, which has no colours.

Standard library only, like `parkrun_core.py`, so the pure milestone module
and its tests import nothing heavier.
"""

from __future__ import annotations

from typing import NamedTuple


class MilestoneColour(NamedTuple):
    bg: str
    fg: str
    # A thin grey ring for the badges that would otherwise vanish into the
    # page: white (10) on a light theme, black (100) on a dark one.
    outline: bool = False


# Official milestone colours, background / text.
MILESTONE_COLOURS: dict[int, MilestoneColour] = {
    10: MilestoneColour("#FFFFFF", "#000000", outline=True),
    25: MilestoneColour("#695D9E", "#FFFFFF"),
    50: MilestoneColour("#C81D30", "#FFFFFF"),
    100: MilestoneColour("#000000", "#FFFFFF", outline=True),
    200: MilestoneColour("#4EA5DA", "#252E5F"),
    250: MilestoneColour("#2D514B", "#FFFFFF"),
    300: MilestoneColour("#E7612D", "#000000"),
    400: MilestoneColour("#890030", "#FFFFFF"),
    500: MilestoneColour("#2E4EA9", "#FFFFFF"),
    600: MilestoneColour("#B3D602", "#000000"),
    700: MilestoneColour("#252E5F", "#FFFFFF"),
    800: MilestoneColour("#7F217E", "#FFFFFF"),
    900: MilestoneColour("#5D8C2F", "#FFFFFF"),
    1000: MilestoneColour("#FDE148", "#000000"),
}

# Every milestone, in order. 10 belongs to juniors only.
MILESTONES: tuple[int, ...] = tuple(sorted(MILESTONE_COLOURS))
JUNIOR_ONLY: frozenset[int] = frozenset({10})

# The matrix always draws these columns; anything above 500 appears only once
# someone has reached it or is next to.
ALWAYS_SHOWN: tuple[int, ...] = tuple(m for m in MILESTONES if m <= 500)

# The unique-parkruns matrix (`venue_matrix.py`): after the first new parkrun,
# every multiple of 50. Not official milestones, so no official colours —
# that matrix is drawn in neutral greys. 2,000 is past every parkrun there is.
UNIQUE_STEP = 50
UNIQUE_MILESTONES: tuple[int, ...] = tuple(range(UNIQUE_STEP, 2001, UNIQUE_STEP))

# The "1st" (start date) column's outline.
FIRST_RUN_OUTLINE = "#E08A00"


def milestones_for(junior: bool) -> tuple[int, ...]:
    """The milestones that apply to a participant."""
    return MILESTONES if junior else tuple(
        m for m in MILESTONES if m not in JUNIOR_ONLY)

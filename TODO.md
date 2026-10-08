# To-do

Ideas and requests not yet built. Move an item out when it ships (to the
**Current status** list in `CLAUDE.md`); delete it if it is rejected, with a
line on why.

---

## Visualisation ideas

Unscoped. Carried over from the *Future ideas* line that used to close
`CLAUDE.md` § Visualisations. Four have since shipped and were removed on
20 Sep 2026: **attendance timeline** (the participation calendars, tab 1 and
tab 3), **fastest times** (the personal-bests block, tab 2), **form (target)
over refreshes** (tab 4) and **age-grade progression** (dropped, not wanted).

- PB progression
- Event frequency

---

## Planner: default country for an unranked list

Left over from the drive-times-everywhere work, which went live on 2 Oct
2026 (`parkrun_core.routable`: mainland GB plus a Europe box; crossings
flagged ⛴ with an "Include sea crossings" toggle). **Still open:** the
planner's default country. Moot while the planner opens ranked, it matters
for an unranked list, which runs to ~2,000 parkruns worldwide in A–Z order.
The original plan is in git history (`TODO.md` before 8 Oct 2026).

## Tab 5 — parkrun challenge filters

Requested 1 Oct 2026. Add a **Challenges** group to the planner's
⚙️ Filters popover (`where_next._planner_filters`). Each challenge narrows the
candidates to the parkruns that would count towards it. The group is built so
that more challenges slot in later. Planner only; the head-to-head view keeps
its own filters.

### First: the alphabet challenge (filter by first letter)

A parkrun for every letter, A–Z except X (no parkrun starts with X).

- **The filter.** A letter picker: a multiselect, with the empty-means-all rule
  every other filter uses (`docs/STYLE.md`). Each option carries its count of
  candidates, like tab 6's filters.
- **The useful shortcut.** "Letters still needed by …" with one choice per
  athlete, or by everyone selected. It preselects the letters that athlete has
  no parkrun for yet, read from `done_table`. That is what the challenge is
  actually for: where to go for the letters you are missing.
- **Progress line.** Per athlete, e.g. "George 19/25 — missing J, Q, U, V, Y,
  Z", as a stat slot, in each athlete's colour.
- **Letter rule.** Start from the one `adhoc/alphabet_challenge/` settled: the
  literal first character of `short_name`, with no accent folding and no
  stripping of "The". Put it in **one** function (in `where_next.py`, or in
  `parkrun_core.py` if the adhoc topic should share it), with tests.
- **Interaction with drive times.** Ranking still works inside the filter, so
  "nearest parkruns for my missing letters" falls out of the existing total-time
  sort. Note that **no live UK parkrun starts with Z** (found by the alphabet
  topic; the nearest is Zuiderpark, Den Haag). A UK-only Z search therefore
  returns nothing, and the drive-times-everywhere item above is what makes Z
  rankable — live since 2 Oct 2026, Zuiderpark is routed.

**Open questions**
- **Accents.** 10 live parkruns start with Ś, Ż, Ł, Ō or Ö (checked 1 Oct
  2026). Should they count as S, Z, L, O and O? Folding adds Polish Ż
  parkruns, which might be the nearest Z for someone in Europe. What
  rule do the community's alphabet trackers use? Check before choosing; the
  adhoc topic chose literal characters to keep the arithmetic simple, not
  because it is the rule.
- **"The …" names.** 18 live `short_name`s start with "The" (The Leas, The
  Ponds, The Lost Gardens of Heligan, …). Does the challenge count them as T,
  or by the next word?
- **Which name.** `short_name` (e.g. "Bushy Park") or `long_name` ("Bushy
  parkrun")? For 93 live parkruns they start with different letters, so this
  choice changes results.
- **Who is "done".** Any visit by that athlete counts, from `results`; buggy
  runs count too.

### Later challenges (unscoped — confirm each definition before building)

Candidates that are a filter on *which* parkrun, so they fit here. Challenges
about times (e.g. stopwatch bingo) or counts (tourist totals) do not.
- **Compass** — names containing North, South, East and West.
- **Pirates** — seven Cs and an R ("arrr").
- **Name badge** — spell an athlete's name in first letters.
- **Specific venues** — e.g. Bushy Park, the first parkrun.

Each one reuses the same parts: a predicate on the event, a "still needed by"
shortcut and a progress line.

---

## Small fixes

- The PB scope label "Last 12 months" wraps on a mid-width desktop, which
  drops its time below "All time"'s. `stat_label(..., lines=2)` fixes that
  (tab 6 uses it). It was not applied to the PB block, to keep that change
  free of visual changes.

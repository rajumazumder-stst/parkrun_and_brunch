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

## Drive times to every parkrun

Requested 1 Oct 2026. Route every athlete to **every live 5k parkrun**, and drop
the hard-coded "mainland GB" and "no ferries" filters from the travel step and
the tab 5 planner. Let the router decide what is reachable, and **flag** routes
that cross water rather than excluding them.

**Built on `dev` (2 Oct 2026), at Europe scope, by the adhoc method — not
this plan.** `parkrun_core.routable` routes mainland GB plus a Europe box
(about 1,310 parkruns per athlete; only the dev DB has them until `dev`
reaches `main`), and the planner flags crossings ⛴ with an "Include sea
crossings" toggle. Against the plan below: step 1 (worldwide probe) is moot
— nothing outside the box is sent; step 3 (`crossing_m` from the directions
endpoint) is **superseded** — the crossing is derived at read time by
`parkrun_core.crosses_water`; step 4 is **reversed** — `is_mainland` stays,
as the crossing test; steps 2, 5, 7 and 8 are done; step 6 needed nothing
(no new column). Open questions settled: no time allowance (flag only), a
crossings toggle (default on), and the slider (capped at 12 h / 800 mi /
1,300 km, top end "or more"). **Still open:** the planner's default country
— moot while the planner opens ranked, it matters for an unranked list,
which now runs to ~2,000 parkruns worldwide in A–Z order. Delete this
section once it is live, keeping that question if still unanswered. The
rest below is the plan as written on 1 Oct, kept for the record.

**Where things stood.** `travel_times` held 839 parkruns per athlete. Two
filters keep it there, and they must change together:
`parkrun_travel.candidate_events` routes only `country_code = 97` and then
`parkrun_core.is_mainland`, and `where_next.plan_candidates` applies the same
pair to choose planner candidates. The filters exist because ORS's matrix
endpoint cannot avoid ferries: it folds a crossing into the time without
saying so.

**Measured in `adhoc/drive_distances/` (1 Oct 2026):**
- ORS routes across water without complaint: the Channel Tunnel, Irish Sea
  ferries, Isle of Wight, Isle of Man.
- It returns `null` for a destination it cannot reach (Inis Meáin has no car
  ferry), and does not fail the whole request.
- 473 more parkruns are reachable that way, taking the total to 1,311 of
  2,402. About 1,090 have no road route (Australia, the US, the Falklands and
  so on).
- Crossing times are too short: sailing speed only, with no check-in (30+ min
  for the tunnel, 45–90 for a ferry), no wait for a departure and no overnight
  sailing.
- ORS files the Eurotunnel shuttle as a ferry, so it cannot say which way it
  crossed.
- `is_mainland` is wrong on its own: it returns True for Dublin. It is only
  correct after the UK country filter.

**Plan**

1. **Probe first.** Send one matrix request from a neutral origin to a sample
   of every country, Australia and Japan included. Confirm that a destination
   with no road connection comes back `null` rather than as an error (for
   example ORS 6099, "point not found within radius") that would fail its
   whole 500-destination chunk. If any country errors, keep a country
   *exclusion* list — the countries the router cannot handle, which is a
   different thing from a mainland *inclusion* rule.
2. **`parkrun_travel.candidate_events`: every live 5k event with
   coordinates.** Drop the country and `is_mainland` filters. Unreachable pairs
   are stored as `reachable = FALSE` (already supported). `pairs_to_route`
   skips existing rows, so they are routed once, not every week. The bootstrap
   is about 7,200 pairs, or 15 matrix requests, well inside the free ORS quota.
   Weekly top-ups stay at near zero.
3. **Record the crossing.** Add `crossing_m DOUBLE` to `travel_times`, the
   distance the route spends on a ferry or shuttle: 0 for none, NULL for not
   yet checked. `ensure_table` adds it with `ADD COLUMN IF NOT EXISTS`. It
   comes from the ORS **directions** endpoint with
   `extra_info: ["waytype"]` (waytype 9 = ferry or shuttle), once per
   reachable pair, because the matrix endpoint gives no breakdown. That is
   about 3,900 calls against a free limit of 2,000 a day. Cap it per refresh;
   the step is already incremental and non-fatal, so it finishes over a few
   refreshes. This measures what the router actually did, so no
   hand-maintained boxes are needed: a new island parkrun is handled without
   editing anything.
4. **Retire the mainland rule.** Once nothing filters on it, delete
   `GB_EXTENT`, `NON_MAINLAND`, `is_mainland` and `Box` from `parkrun_core.py`.
   Replace their tests (`test_is_mainland`, `test_candidates_are_live_5k_mainland_only`
   and the Belfast/Jersey/abroad fixture rows) with tests that the candidates
   are every live 5k event, and that a `null` route is stored unreachable.
5. **Planner (`where_next.py`).**
   - `plan_candidates` takes every live 5k event. Ranking already drops rows
     with no route (`dropna`). Unranked lists then include Australia and the
     rest, so this needs a country default (see open questions).
   - Mark crossing routes ⛴ on the map, in hover text and in the table, with
     the "too short" caveat in words.
   - Rewrite the caption that says "on mainland Great Britain"
     (`where_next.py` ~1206).
   - Fix the driving-time slider: `top` is the athlete's maximum, which
     becomes about 35 h (Finland) and makes a London-scale range impossible
     to set.
6. **Snapshot and privacy.** `travel_times` already ships in the deploy
   snapshot. More rows reveal nothing new: the origin is still only a
   neighbourhood centroid, never stored. Check that `build_snapshot` copies the
   new column, and that the ORS error scrubbing covers the directions endpoint
   too, because its error bodies also echo coordinates.
7. **Retire the adhoc workaround.** `adhoc/drive_distances/build_page.py`
   then reads `travel_times` alone, and `route_crossings.py` and its `.cache/`
   are deleted. Note it in that topic's CHANGELOG.
8. **Docs.** Update `CLAUDE.md` (the `travel_times` section, the tab 5 status
   entry, the refresh spec and the `parkrun_travel.py` file row) and
   `docs/DEV.md` § the "Mainland means" bullet.

**Open questions**
- **Planner default country.** Should it open on the United Kingdom, on
  "reachable by road", or on everything? The ranked top 25 is unaffected
  either way: nothing overseas beats a home-county parkrun on total time.
- **Time allowance for a crossing.** The adhoc page shows raw times with a
  flag. Should the app do the same, or add a fixed allowance (for example
  +45 min for the tunnel or a short ferry, +90 min for a long one) so the
  ranking is fairer? An allowance is a guess, not a measurement, and it is
  hard to un-guess once a ranking depends on it.
- **A crossings toggle.** Should viewers get an "include sea crossings" switch
  in ⚙️ Filters? It is a choice the viewer makes, not a hard-coded rule, so it
  does not conflict with removing the data filters.
- **Slider shape.** Cap the slider at something like 12 h with an "and over"
  end, or use a non-linear scale?

**Done when** `travel_times` has a row (reachable or not) for every live 5k
parkrun and athlete, `crossing_m` is filled for every reachable row, the planner
shows crossings flagged, the mainland code and its tests are gone, and the
adhoc page builds from `travel_times` alone.

---

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
  rankable — on `dev` since 2 Oct 2026, Zuiderpark is routed.

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

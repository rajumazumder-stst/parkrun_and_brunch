# Local development

How to make changes and preview them in a browser **without touching the live /
deployable version**.

## What "live" means here

There is no hosted preview (a deliberate choice). "Live" is the
**`main` branch** plus the tracked, deployable **`data/parkrun_snapshot.duckdb`**.
Local development is isolated from both:

| Concern | Isolation mechanism |
|---|---|
| Code changes | Work on the **`dev`** branch (or a feature branch), never `main`. |
| Data / experiments | The app runs against **`data/parkrun_dev.duckdb`** — a gitignored, parkrun-only copy of the snapshot. The tracked snapshot and the personal-finance dev DB are never written. |

## Run it

```bash
./scripts/run_local.sh
```

On first run this creates `data/parkrun_dev.duckdb` from the snapshot, activates
the project venv, and opens the app at http://localhost:8501. The app is
read-only, so previewing never mutates any database.

To point at a different DB (e.g. the full dev DB for the freshest data — it holds
`personal_finance` too, but the app only reads the `parkrun` schema):

```bash
PARKRUN_DB=~/Documents/duckdb/my_database.duckdb ./scripts/run_local.sh
```

## Refreshing the dev data

`data/parkrun_dev.duckdb` is a throwaway copy. To reset it to the current
snapshot, just delete it — the next `./scripts/run_local.sh` recreates it:

```bash
rm data/parkrun_dev.duckdb
```

A **new view** (e.g. `v_saturday_targets`) is defined once in the pipeline's
`ensure_views()`, then materialised into this dev DB by running that function
against it:

```bash
python -c "import duckdb, parkrun_pipeline as p; p.ensure_views(duckdb.connect('data/parkrun_dev.duckdb'))"
```

`build_snapshot()` also re-runs `ensure_views()`, so the view is folded into the
tracked snapshot on the next `python parkrun_pipeline.py snapshot` (for release).

## Promoting to "live"

When a change is ready: commit on `dev`, merge to `main`, and (if the change
touched the data model/views) regenerate `data/parkrun_snapshot.duckdb` via
`python parkrun_pipeline.py snapshot` so the deployable snapshot matches.

## The buggy-labels page, and its dev twin

Two tabs, two questions about the hand-written labels:

| Tab | Module | Question |
|---|---|---|
| What the buggy costs | `buggy_handicap.py` | how much slower is a buggy run? |
| What labelling changed | `method_impact.py` | what did labelling do to the record? |

**Hosted** at `/buggy-handicap` (`handicap_page.py`). **Dev twin** at
`localhost:8502` (`label_impact.py`), started by:

```bash
PARKRUN_LABEL_AUDIT=1 ./scripts/run_local.sh
```

which runs the real app on `:8501` and the twin on `:8502` (`PARKRUN_PORT` /
`PARKRUN_LABEL_PORT` to move them). Both files are layout only — same tabs, same
modules — so the local instrument and the hosted page cannot drift apart on the
arithmetic. The twin exists to drive them against an isolated dev DB.

### What the buggy costs

For every athlete with a buggy label: the runs between their first and last
buggy run, split by mode, with mean/SD/median, density curves over a rug of the
real runs, and three estimates — the raw difference in means, the same-course
estimate (course fixed effects, which keeps only courses run both ways), and the
same with a linear form-drift term. It recommends a value only when the
estimates agree in sign, the raw interval clears zero, and there are at least 8
buggy runs; otherwise it says hold the default, and why. Nothing is hardcoded:
correct a label and every number moves.

Runs beyond Q3 + 3·IQR are set aside and listed. Three IQRs, not the usual 1.5,
because a parkrun field legitimately contains slow days — only a run that is not
a race at all should fall out.

### What labelling changed

The live head-to-head against `v_head_to_head_legacy` — the frozen pre-buggy
method (one pooled 91-day median, no mode split, no handicap bridge). Per
occasion it reports whether the winner, the places, the ranked roster or just
the margin moved, and stacks the old and new victory charts on a shared x-axis
for whichever contest you pick. Two checkboxes filter it — what changed, and
what used the handicap bridge — ANDed rather than exclusive, because "a bridged
target that changed the result" is the cell worth looking at.

**These are retired numbers on a public page.** The legacy views ship in the
deploy snapshot (`build_snapshot` calls `ensure_legacy_views(force=True)`) purely
so this tab can exist; they were previously kept out of anything hosted so a
superseded method could not be queried. What guards against misreading them is
now the framing — the tab title, the `Old winner` / `New winner` columns, the
docstring in `method_impact.py`. Loosen that and the protection goes with it.

`run_local.sh` still builds the views into a dev DB under
`PARKRUN_LABEL_AUDIT=1`, because `pipeline seed` copies tables and rebuilds
views from `ensure_views` alone. They are **not** built into the source of
truth. If they are missing, the tab says so and returns — never `st.stop()`,
which would take the other tab down with it.

**A `Lost` occasion should be impossible** — the bridge only ever makes *more*
contests rankable — and one is called out in red if it appears.

Historical note: while `run_modes` was empty this tab had to report every
occasion `Unchanged`. That was the zero-label equivalence check as a live page,
and it is spent — the confirmed buggy labels ended it, which is what the tab now
exists to show.

scipy is a **hosted** dependency, pinned in `requirements.txt`. It is the only
one there that the five-tab app does not itself use.

## Fake labels — removed

There was a `scripts/dev_fake_labels.py` that fabricated plausible buggy labels
so the buggy-mode UI had something to show before the real ones existed. It
labelled runs that were slow relative to each athlete's trailing form, and
marked a quarter of them `estimated` so the old `🛒 (est.)` marker could be
seen on screen.

Both reasons are spent. The review sheet came back on 2 Sep 2026, so the deploy
snapshot now ships **161 real labels, 37 of them buggy** — a dev DB seeded from
it has plenty for the UI to render. And the `(est.)` marker was deleted rather
than wired up: an estimate is checked in the database (`docs/DATA.md`), not read
off the page.

It is not worth reviving to test the estimator, either. It planted "slow
relative to form" as its buggy signal, which is close to what the estimator
looks for, so a model tested against it would score well for circular reasons.
The walk-forward over real labels is the honest measure. Recover it from git
history if some future need proves otherwise.

## Tests

```bash
pytest                 # from the repo root
pytest -q tests/test_buggy_estimator.py
```

**pytest is a dev tool, deliberately not in `requirements.txt`** — same
convention as `openpyxl` for the review sheet and `cairosvg` for the logo. The
hosted app must stay deployable without it. Install it into the project venv:

```bash
pip install pytest
```

`tests/test_buggy_estimator.py` needs no database — every case builds its own
frame. The ones worth knowing about, because they encode decisions rather than
mechanics:

* **causality** — appending a later run must leave every earlier row's features
  byte-identical. This is the property the whole walk-forward rests on; break it
  and the model quietly scores itself using answers it could not have had.
* **`rule` rows never train** — a `rule` row records what a rule dictates, not
  what anyone observed (`CLAUDE.md`'s three-source rule). They *are* still used
  for features: a real run with a real time belongs in a form window whoever
  labelled it.
* **effective positives ignores class balancing** — balancing rescales the
  positives to match the negatives, so folding it in would hide exactly the
  loss the half-life floor exists to catch.
* **the form window matches the pipeline's `TARGET_WINDOW_DAYS`** — the
  estimator cannot import it (`parkrun_pipeline` pulls in requests and bs4), so
  the test reads the constant out of the source instead.

## Screenshots

Playwright drives the running app for phone-sized captures (390×844, 3×), which
is the only reliable way to see the layout as it lands on a phone — the tabs,
popovers, legend isolation and map tooltips all need real interaction. Install
it into a throwaway venv rather than the project one; it is not a runtime
dependency:

```bash
python3 -m venv /tmp/shotenv
/tmp/shotenv/bin/pip install playwright
/tmp/shotenv/bin/playwright install chromium
```

Hide Streamlit's own chrome first, or the shots look like a dev session rather
than the app:

```python
p.add_style_tag(content='[data-testid="stToolbar"],[data-testid="stDecoration"],'
                        '.modebar-container{display:none!important}')
```

Two gotchas. `full_page=True` gives you only the viewport — Streamlit scrolls an
inner container, not the document — so scroll the target into view and take a
normal screenshot. And every tab's DOM is present at once, so scope selectors
with `:visible` or you will match a hidden element on another tab.


## Deferred refactors

Streamlining that has been *identified and costed* but not done, so the
analysis is not repeated every time someone reads the code and thinks "this
could be tidier". Ordered by value for the risk taken.

Already done, for contrast: `data_version()` deduplicated into `parkrun_ui.py`,
the dead `mode_badge()` removed, and the two `fmt_time` copies aligned
(`ca8ccf6`). Two known issues are documented elsewhere and are **not** repeated
here — the `resolve_event_ids` duplicate-`short_name` hazard (`CLAUDE.md`,
design decision 3) and the shell `log()` duplication (considered and rejected
in `ca8ccf6`: sourcing a file for one `printf` adds a failure mode to a
scheduled path that must not break).

### 1. The 91-day window is a magic number in the app

`parkrun_app.py`'s `load_target_window_runs()` hardcodes `latest.d - 91`, while
the pipeline owns the same figure as `TARGET_WINDOW_DAYS`. That popover exists
to show *which runs made the target*, so if the pipeline constant ever changed
the popover would list a different window than the median was taken over — and
it would look right. Silent, and exactly the kind of wrong this app is meant to
avoid.

Not a one-line import fix: `parkrun_pipeline.py` imports `requests` and `bs4` at
module level, and the hosted app does not install those, so importing the
constant would break the deploy. It needs a small shared constants module that
neither side's dependencies reach into — or, cheaper, the literal kept where it
is with a comment naming `TARGET_WINDOW_DAYS` as its source of truth.

**Value: high. Risk: low. Size: small.**

### 2. Test coverage is thin

**Partly addressed.** `tests/test_buggy_estimator.py` now covers the estimator
(38 cases, no DB). Everything else remains untested: the pipeline's views, the
head-to-head arithmetic, `_winning_margin`, the handicap gate. The reasoning
below still applies to those.

The project leaned on verification narratives instead — the zero-label equivalence check, the label-impact
comparison — and those were genuinely good, but they were one-off and are now
spent.

The highest-value targets are the pure functions, which need no database:
`time_to_seconds`, `fmt_time` (both copies, as a parity test — that is exactly
the bug that was found), `_winning_margin`, and the handicap gate logic in
`buggy_handicap.py`. After that, the views: seed a temporary DuckDB with a
handful of rows and assert `v_head_to_head` ranks and bridges as documented.

**Value: high. Risk: none. Size: medium, and splittable.**

### 3. `parkrun_pipeline.py` is 1,500 lines

Scraping, schema, migrations, views, reconcile, upsert, snapshot build and the
MotherDuck push all live in one file. The natural seams are already visible in
its own section comments. A split into `pipeline/` submodules would make each
piece testable in isolation (see 2).

Against it: one file means one place to look, the CLI is a single entry point,
and the current structure has not actually caused a bug. Worth doing *with* the
tests, not before them.

**Value: medium. Risk: medium. Size: large.**

### 4. `parkrun_app.py` mixes data access and rendering

1,370 lines holding nine `@st.cache_data` loaders and every render function.
Lifting the loaders into a `queries.py` would leave the app file as layout, and
would let the loaders be tested without Streamlit.

**Value: medium. Risk: low-medium. Size: medium.**

### 5. Long render functions

`render_impact` (191 lines), `render_personal_bests` (164), `ensure_views`
(162). Conventional advice says split them. The counter-argument is real: these
are cohesive, heavily commented, and `CLAUDE.md` explains the shape of several
of them, so a split trades one kind of readability for another and produces a
diff too large to review line by line.

**Value: low. Risk: medium. Size: large.**

### 6. Inline styles in `parkrun_app.py`

Twenty-one hand-built `unsafe_allow_html` style strings. A shared style helper
or one stylesheet would centralise them. It touches every visual in the app,
which is why it has not been done casually — a subtle rendering regression here
is easy to ship and hard to notice.

**Value: low. Risk: high. Size: large.**

### 7. `_read_sql` opens a connection per call

Roughly twenty call sites, each opening and closing its own DuckDB connection.
This is deliberate — the app never holds a write lock — and at this data size
it costs nothing measurable. Listed only so the next reader knows it was a
choice rather than an oversight.

**Value: none today. Leave it.**

### 8. Trivia

`requirements.txt:6` names `handicap_app.py`, a file that does not exist; it
means `handicap_page.py`. One word.


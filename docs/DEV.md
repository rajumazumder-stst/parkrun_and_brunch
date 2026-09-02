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

## Fake labels for previewing the buggy UI

Until the real labels come back there is nothing for the buggy-mode UI to show:

```bash
python scripts/dev_fake_labels.py           # writes plausible fake labels
python scripts/dev_fake_labels.py --clear   # start over
```

It labels runs that were slow *relative to that athlete's trailing 20-run
median*, so the fakes track contemporaneous form rather than a flat time:
Duncan gets a sustained era (what a real buggy looks like), George scattered
one-offs, a quarter of them `estimated` so that marker is exercised too. It
refuses to write to the source of truth or the deploy snapshot.

Delete `data/parkrun_dev.duckdb` when you are done — it is disposable, and
leaving fake labels in it is a trap.

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

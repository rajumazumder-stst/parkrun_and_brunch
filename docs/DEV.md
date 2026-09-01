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

## The label-impact app (dev only)

```bash
PARKRUN_LABEL_AUDIT=1 ./scripts/run_local.sh
```

Starts **two** apps: the real one on `:8501` and the comparison on `:8502`
(`PARKRUN_PORT` / `PARKRUN_LABEL_PORT` to move them). It is a separate app
rather than a sixth tab because it is a development instrument, not part of the
product — and keeping it out of `app.py` means there is no gate to get wrong at
deploy time.

`label_impact.py` compares the live head-to-head against
`v_head_to_head_legacy` — the frozen pre-buggy method (a single pooled 91-day
median, no mode split, no handicap bridge). Per occasion it reports whether the
winner, the places, the ranked roster or just the margin moved, and draws the
old and new victory charts stacked on a shared x-axis for whichever contest you
pick. The dropdown is in date order, most recent first, matching the table.

`run_local.sh` builds the legacy views into the dev DB when the var is set;
nothing builds them into the source of truth or the deploy snapshot, so the
hosted app can never serve them. If they are missing the app says so and stops.

**With no labels written it must report every occasion `Unchanged`.** That is
the zero-label equivalence check as a live page: the new views have to reproduce
the old ones exactly until a label says otherwise. A `Lost` occasion should be
impossible — the bridge only ever makes *more* contests rankable — and it is
called out in red if one appears.

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

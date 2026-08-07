# Ad-hoc topics

One-off investigations that use this repo's parkrun data but are **not part of
the app**. Nothing in here is imported by `app.py` or `parkrun_pipeline.py`, and
nothing here runs on the scheduled refresh — the app must stay deployable if
this whole folder is deleted.

Topics read the tracked read-only snapshot (`data/parkrun_snapshot.duckdb`) via
`PARKRUN_DB` with the same fallback order the app uses, so they never touch the
dev database.

## Topics

| Folder | Question | Status |
|---|---|---|
| [`alphabet_challenge/`](alphabet_challenge/) | Where should you live to complete the parkrun alphabet (A–Z minus X) with the least travel? | Answered 27 Jul 2026 |
| [`furthest_pairs/`](furthest_pairs/) | Which two parkruns are furthest apart? (top 10 pairs) | Answered 7 Aug 2026 |

## Layout convention

Each topic is a self-contained folder:

```
<topic>/
  README.md          the question, the decisions behind it, the answer, how to rerun
  CHANGELOG.md       dated log of what changed and why
  requirements.txt   extra deps for this topic only (never merged into the root one)
  scripts/           the code, runnable from any working directory
  results/           small, tracked result files — the answer of record
  output/            large generated artefacts (maps, geometry dumps) — gitignored
  .cache/            cached third-party API responses — gitignored
```

**Tracked vs ignored.** `results/` holds the answer in a form small enough to
diff and review. `output/` and `.cache/` are regenerable and are ignored via the
root `.gitignore` (`adhoc/*/output/`, `adhoc/*/.cache/`) — a 17 MB map does not
belong in git history.

## Starting a new topic

1. `mkdir -p adhoc/<topic>/{scripts,results,output}` — the gitignore rules apply
   automatically by pattern.
2. Write `README.md` first: the question as asked, and every judgement call that
   shapes the answer. Later readers cannot recover those from the code.
3. Keep scripts path-independent (resolve from `__file__`, not the cwd) and cache
   any external API calls, so a rerun is cheap and reproducible.

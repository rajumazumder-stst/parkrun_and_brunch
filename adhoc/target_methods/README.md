# Target methods

**Question (2 Nov 2026).** The head-to-head target is each runner's median
time over the 91 days before the race, per mode (regular or buggy). Would a
different target change who wins? Three candidates were tested against it:

| | Target | Too few runs |
|---|---|---|
| **M0** (current) | median of the same-mode runs in the 91 days before | — |
| **M1** | same 91 days; midpoint of the best and 3rd-best time | 2 runs: their midpoint · 1 run: that run |
| **M2** | the latest 13 same-mode runs in the 182 days before; midpoint of best and 3rd-best | as M1, applied to that set |
| **M3** | asymmetric smoothing: after each run the target moves **½** of the way to a faster time, **⅒** of the way to a slower one | needs a same-mode run in the last 91 days, as now |

M3 in plain words: the target is a marker that moves after every run. A good
run pulls it half the way to the new time at once. A bad one nudges it only a
tenth of the way. So one good run raises the bar quickly, one bad run barely
moves it, and a long spell of slower runs brings it back over a couple of
months.

## Judgement calls

- **Only the target changes.** The handicap bridge (from the other mode when
  this one has no runs, with each method's own window), the demote rule, the
  rounding of `pct_diff` and the standard competition ranking are the app's.
  M0 is reproduced row for row against `v_head_to_head` before anything is
  compared (`check_m0`). The script stops if that fails.
- **M3's start.** The target starts at the runner's first run of that mode and
  carries on from there through every later run, however old. The speeds
  ½ / ⅒ were chosen, not fitted. The report also counts what changes at
  ⅓ / ½ / ⅔ × 1⁄20 / 1⁄10 / 1⁄5.
- **Verdicts** use `method_impact.py`'s ladder against M0. First match wins:
  winner changed, places changed, roster changed, gained / lost, margin only
  (≥ 0.01 pts), unchanged.
- **Spread chart.** For each Saturday from the first head-to-head on, per runner
  and mode, it takes every run in that method's window as a % from that
  Saturday's target and shows the best, Q1, median, Q3 and worst. There is no
  bridge, as in `v_saturday_targets`. For M3 the window is the 91 days.

## Answer (data to 1 Nov 2026, 214 head-to-heads)

| | 1sts George | Raju | Duncan | Winner changed | Places changed | Margin only | Unchanged |
|---|---|---|---|---|---|---|---|
| M0 | 82 | 95 | 38 | | | | |
| M1 | 96 | 90 | 28 | 39 | 6 | 166 | 3 |
| M2 | 96 | 88 | 30 | 39 | 5 | 169 | 1 |
| M3 | 99 | 85 | 30 | 41 | 5 | 166 | 2 |

(1sts sum to more than 214: a dead heat credits both.) No head-to-head is
gained or lost: every method ranks the same occasions.

- **About 1 in 5 winners change (39–41), whichever proposal is used.** The
  three proposals mostly agree with one another, and the winners change in
  the same direction: **George gains 14–17 wins, Raju and Duncan lose them.**
- **Why: every proposal sets a faster target than the median, by more for the
  runners whose times spread wider.** Median drop in target against M0, per
  runner and mode:

  | | George | George 🛒 | Raju | Duncan | Duncan 🛒 |
  |---|---|---|---|---|---|
  | M1 | 3.0% | 1.6% | 3.3% | 4.5% | 0.0% |
  | M2 | 3.4% | 2.6% | 4.2% | 5.0% | 3.1% |
  | M3 | 1.6% | 1.5% | 2.5% | 3.0% | 1.7% |

  A target near your best is harder to beat the further your best sits from
  your typical run. So the most consistent runner gains, and George's buggy
  targets fall least of all. Duncan's buggy M1 figure is 0.0% because he
  rarely has three buggy runs in 91 days, and with one or two the midpoint
  is the median.
- **That is a change of meaning, not just of numbers.** The median asks "who
  beat their typical run by most". The proposals ask "who came closest to
  their good days", which rewards consistency. Choose by the question, not
  by the scores.

## Rerun

```bash
source ~/Documents/Python\ scripts/env/bin/activate
python adhoc/target_methods/scripts/build_report.py
```

It writes `output/target_methods.html` (about 5 MB, gitignored; open it
directly or through `python -m http.server`) and `results/summary.csv`. Run
time is about 20 s. It reads the database `parkrun_core.resolve_db()` picks,
read-only. The page loads plotly.js from jsDelivr, so viewing it needs a
network connection.

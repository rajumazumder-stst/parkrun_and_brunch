# Target methods

**Question (2 Nov 2026).** The head-to-head target is each runner's median
time over the 91 days before the race, per mode (regular or buggy). Would a
different target change who wins? Three candidates were tested against it:

| | Target | Too few runs |
|---|---|---|
| **M0** (current) | median of the same-mode runs in the 91 days before | — |
| **M1** | same 91 days; midpoint of the best and 3rd-best time | 2 runs: their midpoint · 1 run: that run |
| **M2** | the latest 13 same-mode runs in the 182 days before; midpoint of best and 3rd-best | as M1, applied to that set |
| **M4** | the latest 13 same-mode runs in the 182 days before; their median | — |

M4 is the current method with a fixed number of runs instead of a fixed number
of weeks. **M3** (asymmetric smoothing: the target moves ½ of the way to a
faster run and ⅒ of the way to a slower one) was tried and dropped on
2 Nov 2026. It changed 41 winners, all in the same direction as M1 and M2.

## Judgement calls

- **Only the target changes.** The handicap bridge (from the other mode when
  this one has no runs, with each method's own window), the demote rule, the
  rounding of `pct_diff` and the standard competition ranking are the app's.
  M0 is reproduced row for row against `v_head_to_head` before anything is
  compared (`check_m0`). The script stops if that fails.
- **Verdicts** use `method_impact.py`'s ladder against M0. First match wins:
  winner changed, places changed, roster changed, gained / lost, margin only
  (≥ 0.01 pts), unchanged.
- **Spread chart.** For each Saturday from the first head-to-head on, per runner
  and mode, it takes every run in that method's window as a % from that
  Saturday's target and shows the best, Q1, median, Q3 and worst. There is no
  bridge, as in `v_saturday_targets`.
- **Working tables.** Every target that decided a result says how it was
  worked out:
  - A median of an odd number of runs is one run, given by its date.
  - A median of an even number is the midpoint of the two middle times.
  - M1 and M2 list the best, 2nd and 3rd times.
  - A bridged target shows the other kind's target, the handicap and the
    working behind it.

## Answer (data to 1 Nov 2026, 214 head-to-heads)

| | 1sts George | Raju | Duncan | Winner changed | Places changed | Margin only | Unchanged |
|---|---|---|---|---|---|---|---|
| M0 | 82 | 95 | 38 | | | | |
| M1 | 96 | 90 | 28 | 39 | 6 | 166 | 3 |
| M2 | 96 | 88 | 30 | 39 | 5 | 169 | 1 |
| M4 | 80 | 97 | 37 | 19 | 4 | 174 | 17 |

(1sts sum to more than 214: a dead heat credits both.) No head-to-head is
gained or lost: every method ranks the same occasions.

- **M1 and M2 change about 1 in 5 winners (39), all the same way.** George
  gains 14 wins; Raju and Duncan lose them. Both set a faster target than the
  median, by more for runners whose times spread wider. Median drop in target
  against M0, per runner and mode:

  | | George | George 🛒 | Raju | Duncan | Duncan 🛒 |
  |---|---|---|---|---|---|
  | M1 | 3.0% | 1.6% | 3.3% | 4.5% | 0.0% |
  | M2 | 3.4% | 2.6% | 4.2% | 5.0% | 3.1% |
  | M4 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

  A target near your best is harder to beat the further your best sits from
  your typical run, so the most consistent runner gains. That changes what a
  head-to-head means: "who came closest to their good days" instead of "who
  beat their typical run by most".
- **M4 changes 19 winners, with no lean.** Its targets are no faster or slower
  than today's (median change 0.0% for everyone), and the wins tally barely
  moves: George −2, Raju +2, Duncan −1. What changes is how many runs a target
  rests on.

  | Median runs behind a target | George | George 🛒 | Raju | Duncan | Duncan 🛒 |
  |---|---|---|---|---|---|
  | M0 (91 days) | 10 | 4 | 10 | 12 | 3 |
  | M4 (latest 13 in 182 days) | 13 | 7.5 | 13 | 13 | 5 |

  The buggy targets gain most: they rest on 5–7.5 runs instead of 3–4.
  So M4 keeps today's question and answers it from more evidence.

## Rerun

```bash
source ~/Documents/Python\ scripts/env/bin/activate
python adhoc/target_methods/scripts/build_report.py
```

It writes `output/target_methods.html` (about 6 MB, gitignored; open it
directly or through `python -m http.server`) and `results/summary.csv`. Run
time is about 20 s. It reads the database `parkrun_core.resolve_db()` picks,
read-only. The page loads plotly.js from jsDelivr, so viewing it needs a
network connection.

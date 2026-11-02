# Changelog

## 2026-11-02 (M4)
- **M3 dropped** (asymmetric smoothing, and its speed grid). **M4 added**: the
  median of the latest 13 same-mode runs within 182 days. M4 changes 19
  winners with no lean; its targets match today's in level (median change
  0.0%) but rest on more runs, buggy ones most of all.
- Every rule now returns how its target was worked out. New section **How
  each target was worked out**: one row per runner per head-to-head per
  method, filterable by runner, mode and method. The side-by-side panels gain
  a **How** column. Bridged targets show the handicap step.
- Classifications in display order (George, Raju, Duncan), as the app now does.

## 2026-11-02 (later)
- Hover percentages on the spread and scatter charts are formatted in Python
  (`pct2`). Plotly's d3 format strings were not applied to those values in the
  browser, which showed up to 13 decimal places.
- **How far each target moves** gets the same runner / mode / method
  checkboxes: one trace per combination; colour = method, shape = runner,
  open shape = buggy. One `filterable()` drives both charts' filters.
- New section **Target time over time**: every method's target on each
  Saturday, per runner and mode (no bridge, as on the app's Form tab), with
  checkboxes for runner, mode and method. Colour is the runner, line style the
  method; buggy lines are thinner and lighter.
- Percentages are never shown to more than 2 dp: % axes use `.2~f`, and the
  spread hover is now 2 dp (was 1).

## 2026-11-02
- Topic created. `build_report.py` re-scores every head-to-head under M0
  (current, verified against `v_head_to_head`), M1, M2 and M3, then writes the
  HTML report and `results/summary.csv`. Run against the local source of
  truth (data to 1 Nov 2026): 214 head-to-heads, 39–41 winners change under
  each proposal, all in George's favour.

# Changelog

## 2026-11-02 (later)
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

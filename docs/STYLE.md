# Style guide

The conventions every new piece of UI follows. Most of these started as a fix in
one place. They are written down here so the next addition gets them from the
start, rather than only after someone notices a mismatch.

---

## Stat slots

A **stat slot** is a headline number with its label above it and an optional
note below. It is the anatomy of the tab 2 personal-best scopes. Tab 6's
reliability tiles use it too. **Any new headline number in the app uses it.**

```
All time          right overall        ← label   small, full opacity
20:42             71%                  ← value   big, weight 600, tabular figures
Brooklands        of 28                ← note    small, muted (opacity .72)
```

Read top to bottom: first what the number is, then the number, then what it
rests on. A label *under* the number, which tab 6 had until 30 Sep 2026, makes
the reader take in the figure before knowing what it measures.

**Use the helpers in `parkrun_ui.py`. Never hand-write the HTML.**

| Helper / constant | What it is |
|---|---|
| `stat_label(text, lines=1)` | The label. `lines=2` reserves two lines and sits the text on the bottom one. Use it wherever a label can wrap, so a wrapped label never pushes its value below its neighbours' values. |
| `stat_value(text, color=None)` | The value. |
| `stat_note(text)` | The note, muted. An empty note still takes its line (`&nbsp;`), so values sit level whether or not their neighbours have a note. |
| `STAT_SMALL` / `STAT_BIG` / `STAT_LINE` | 0.82rem / 1.65rem / 1.35em. The only sizes a slot uses. |
| `stat_phone_css(block_key, row_prefix)` | The phone rules (below). |

Rules:

- **Everything lines up across slots.** Any line whose content varies in length
  gets a fixed height. For a label that means `stat_label(..., lines=2)`. For
  the PB venue it means the two-line clamped `_venue` block. Values across a
  row, and across boxes, must sit on one level.
- **The n goes on the page, never in a tooltip.** "71%, of 28" and "71%, of 4"
  are different claims.
- **Colour means a real threshold, and nothing else.** Tab 6 colours a
  direction in `ERR` only when it is right less than half the time, which is
  worse than a coin flip on a binary call. Don't invent a threshold just to
  justify a colour.
- **Thin evidence is dimmed, not hidden.** Put the whole slot at `opacity:.45`
  when its n is below the module's threshold (`THIN_N` in tab 6).
- **A glyph beside a value is spaced by margin, never a space.** Use the
  `PB_GLYPH_GAP` margin. A space is set in the value's tabular figures, and at
  that width the glyph reads as another digit.
- **On a phone, slots stay side by side.** Streamlit stacks every column below
  640px. Put the row in `st.container(key=...)` and emit
  `stat_phone_css(block_key, row_prefix)`. That keeps that row, and only that
  row, on one line, and shrinks the type to `STAT_PHONE_BIG` /
  `STAT_PHONE_SMALL`. Size the block so the slots fit at 393px: three PB
  scopes, or four tab 6 tiles.

## Charts

- **Every plotly chart goes through `parkrun_ui.show_chart`.** Never call
  `st.plotly_chart` directly; `tests/test_ui.py` fails if anything does.
  `show_chart` locks both axes (`fixedrange`), and turns off scroll-zoom,
  double-click reset and the mode bar. Before this, a swipe that started on a
  chart panned the chart instead of scrolling the page, so on a phone you
  could not scroll past it.
- **Legends stay clickable.** Clicking a legend item hides that trace, and
  autorange still rescales to what is left. Group per athlete
  (`legendgroup`) so one click hides all of that runner's traces.
- **Narrow by filters, not by zooming.** A date range belongs in the shared
  Year/Season multiselects, not in a zoom gesture.
- **Folium maps are not locked.** A map has to zoom. Leave its default
  behaviour alone.

## Filters

- **Every multiselect filter treats an empty selection as "all".** Set the
  placeholder to say so ("All years").
- **Year and Season are both multiselects and mutually exclusive.** Choosing
  anything in one empties the other (`year_season_filters`).
- When a click elsewhere in the app changes a filter (tab 3's calendar),
  **add** to the reader's selection. Never replace it.

- **A range is a two-ended slider between two number boxes**
  (`where_next.range_filter`): the boxes for exact values, the slider for a
  quick sweep. Callbacks keep all three in step, and a low and high that cross
  are swapped. The full range means "no limit", so nothing is dropped for
  sitting exactly at the end of the track.

- **A panel of several filters gets a "Clear all filters" button** that
  deletes the filters' session keys, so every widget comes back at its
  default (`where_next.clear_planner_filters`). It resets filters only.
  Settings such as units or the view toggle are left alone.

## Hover text

- **Say only what is there.** Leave out lines that would read "never" or
  "none". A place none of them has been is its name and country. Runs are
  listed only for whoever has run it. The head-to-head line appears only where
  one happened. An absence is already shown by the marker; the hover text
  adds detail.
- The exception is data the reader is choosing on: a tab 7 recommendation
  shows everyone's driving time, because that is what it was ranked on.

## Phones

- **Test at 390px, with touch and without hover.** Controls, labels and
  pinned elements behave differently there.
- **A long filter panel above a result gets a sticky jump pill** (tab 7's
  "Map ↓"). It is CSS only (`position: sticky; bottom`), it is rendered only
  when its target is, and it is hidden above 640px. Streamlit's markdown
  block has a -1rem bottom margin that must be zeroed, or the pill overhangs
  the screen edge.
- **Map controls must not cover map labels.** Leaflet draws controls above
  tooltips and pop-ups, so a layer box folds to its icon on a narrow map
  (`CollapseLayersWhenNarrow`), and pop-ups pan with top padding that clears
  the zoom buttons.
- **Map labels on touch are compact and wrap** (11px, 210px wide at most),
  rather than one unbroken line running off the side of the map.

## Colour and marks

- Athletes are always drawn in `ATHLETE_COLORS`, in that fixed order. Any mark
  that has one slot per runner uses that order, so a position means the same
  runner everywhere. That includes the tab 7 squares: left to right in a row,
  top to bottom in a column.
- A per-runner "has / hasn't" mark is a **rounded square**, like a calendar
  cell. On the tab 7 map the squares are lamps in a dark traffic-light housing
  ("Row, lit"): lit in the runner's colour for yes, unlit for no. A place
  none of them has been is a **black circle**. There are no three unlit lamps
  to draw.
- A rank is a number **on the place's own marker**, never a second marker
  stacked on top. The marker must still sit exactly on the location.
- 🛒 marks the exception (a buggy run). It is never shown for an athlete who
  has never pushed one.
- No coffee imagery.

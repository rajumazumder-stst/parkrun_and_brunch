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

## Numbers

- **A comma for thousands, everywhere a number is shown**: 2,394 parkruns,
  1,312.5 km (2 Oct 2026). Every count or measure written into text goes
  through `parkrun_ui.fmt_n(x, dp)`, so the rule lives in one place; a
  missing value is a dash. Counts that are small today get it too — they
  grow with every refresh, and the rule is "would use one", not "uses one
  now". `tests/test_ui.py` fails on any `{len(...)}` written into an
  f-string without it.
- Charts: a count axis is `tickformat=",d"` and bar labels
  `texttemplate="%{text:,}"`. A count column in `st.dataframe` is
  `NumberColumn(format="localized")`. Browser-side counts (the map's corner
  counter) use `toLocaleString('en-GB')`.
- **Exceptions**: a number you type into — a `number_input` or a slider's
  value — keeps Streamlit's printf format, which has no thousands flag, and
  a comma in an editable box gets in the way of typing anyway. Times are
  `fmt_time` / `_fmt_min`, never comma'd. A value quoted as code
  (`n_buggy_labels=31`) is code, not a figure.

## Flags

- **A country is shown by its flag** where a table has no room for the name
  (tab 5's results table: the flag alone, the name in the cell's tooltip and
  as its sort key), and **flag then name** in a filter
  (`🇬🇧 United Kingdom (n)`, n with its comma). `parkrun_ui.flag(name)` builds it from
  `COUNTRY_ISO`; a country missing there shows 🏳️ and fails
  `test_every_country_in_the_lookup_has_a_flag`. Windows draws flag emoji as
  two letters (GB), which still reads.
- **The flag is the child country's** (`event_countries`): 🏴󠁧󠁢󠁷󠁬󠁳󠁿 for a Welsh
  parkrun, 🇳🇦 for Windhoek, not the 🇬🇧 / 🇿🇦 of the site parkrun files them
  under. England, Scotland and Wales are emoji tag sequences
  (`SUBDIVISION_FLAG`), which Windows draws as a plain black flag. Northern
  Ireland has no emoji: it gets the **Ulster Banner**, drawn as SVG
  (`ULSTER_BANNER_SVG`, chosen 4 Oct 2026), so it appears only where HTML is
  drawn (`flag_html`) — in plain text, a multiselect option, the name stands
  alone. A child missing a flag fails `test_every_child_country_has_a_flag`.
- **The country filter is two levels**: each parent, then its children
  indented under it (an em space — `country_options`). A parent takes in all
  its children; a parent whose every child is itself (Australia) has none.

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
- **Years are listed newest first, everywhere, and the label is "Year".** Use
  `parkrun_ui.years_desc` for the options. It is the one place the order is
  decided, so a new year filter cannot drift from the rest. (Seasons stay in
  calendar order.)
- **A filter keeps its value while it is off screen.** Streamlit discards a
  widget's state on any run where the widget is not drawn: a hidden section,
  or the other view of a toggle. `parkrun_ui.keep_widget_state(prefixes)` at
  the top of the block writes those keys back to themselves, which is
  Streamlit's documented way to keep them.
- When a click elsewhere in the app changes a filter (tab 3's calendar),
  **add** to the reader's selection. Never replace it.

- **A range is a two-ended slider between two number boxes**
  (`where_next.range_filter`): the boxes for exact values, the slider for a
  quick sweep. Callbacks keep all three in step, and a low and high that cross
  are swapped. The full range means "no limit", so nothing is dropped for
  sitting exactly at the end of the track. A track whose data runs far past
  any useful setting (a 35 h drive to Finland) is **capped**, and its top then
  means "this or more" — said in a caption above the rows, since a printf
  format cannot write "720+".
- **On a phone a range stays on one line**: box · slider · box. Each row sits
  in an `st-key-rng-*` container and `where_next.RANGE_ROW_CSS` (emitted once,
  beside `SECTION_CSS`) opts it out of Streamlit's column stacking, with
  fixed-width boxes and the slider taking the rest. Keep the boxes' font at
  16px — smaller makes iOS zoom the page when a box is tapped.

- **A panel of several filters gets a "Clear all filters" button.** It sets
  the panel to a **neutral** state, written out as values
  (`where_next.planner_cleared`), not by deleting keys — the opening
  defaults are a recommendation (tab 5 opens on George and Duncan's
  never-run parkruns), and Clear is not a way back to a recommendation. Since
  2 Oct 2026 it resets units and the rank metric too (a Clear that left km
  on read as broken); only the view toggle is left alone. Keys whose widget
  re-seeds itself well (head-to-head rows, ranges) are deleted instead.

## Tables

- **A table that has to style a column uses an HTML table**
  (`where_next.results_table_html`, drawn by `st.html` in the page, not an
  iframe), with sorting, column resizing and a scrolling box with the header
  pinned. `st.dataframe` reads only colour, background and weight from a
  Styler and never styles a header, so a runner left out of a ranking could
  not be grey *and italic* as on the map. Escape every cell.
- **Lead with what decided the order**: the ranking total comes straight
  after the name, bold, headed "(ranked)"; the other total follows plain.

## Hover text

- **Say only what is there.** Leave out lines that would read "never" or
  "none". A place none of them has been is its name and country. Runs are
  listed only for whoever has run it. The head-to-head line appears only where
  one happened. An absence is already shown by the marker; the hover text
  adds detail.
- The exception is data the reader is choosing on: a tab 5 recommendation
  shows everyone's driving time, because that is what it was ranked on.

## Phones

- **Test at 390px, with touch and without hover.** Controls, labels and
  pinned elements behave differently there.
- **A long panel of filters goes behind a button**, a `closable_popover`,
  so the result it filters stays at the top of the screen. (Tab 5's planner once had a
  sticky "Map ↓" jump pill past an inline panel; the Filters button made it
  pointless, and it was removed.)
- **Map controls must not cover map labels.** Leaflet draws controls above
  tooltips and pop-ups, so a layer box folds to its icon on a narrow map
  (`CollapseLayersWhenNarrow`), and pop-ups pan with top padding that clears
  the zoom buttons.
- **A tapped thing's details on a phone rise in a bottom sheet**, the same
  one everywhere: the calendars' (`components/calendar/detail.js`) and the
  map's (`where_next.MapSheet`) share its look. It is built in the parent
  document, sits on the visual viewport's bottom edge, uses the calendar
  theme's `tip_bg`/`tip_fg`, and any tap elsewhere dismisses it. A pointer
  that can hover gets a tooltip instead.
- **A control a phone can open must also close.** The layer box closes on a
  map tap or on its own Close row. Every popover is made with
  `parkrun_ui.closable_popover`, which puts a "Close ✕" button at the top
  right of the panel on a phone (a tall panel leaves no "outside" to tap).
  `tests/test_ui.py` fails on a bare `st.popover`. The button's CSS,
  `POPOVER_CLOSE_CSS`, is injected once per page by the page script.
- **Distances show to 0.1** (mi or km), in hover text, tables and range
  boxes; times to the minute.

## Colour and marks

- Athletes are always drawn in `ATHLETE_COLORS`, in that fixed order. Any mark
  that has one slot per runner uses that order, so a position means the same
  runner everywhere. That includes the tab 5 lamps, left to right.
- A per-runner "has / hasn't" mark is a **rounded square**, like a calendar
  cell. On the tab 5 map the squares are lamps in a dark traffic-light housing
  ("Row, lit"): lit in the runner's colour for yes, unlit for no. A place
  none of them has been is a **black circle**. There are no three unlit lamps
  to draw.
- A rank is a number **on the place's own marker**, never a second marker
  stacked on top. The marker must still sit exactly on the location.
- 🛒 marks the exception (a buggy run). It is never shown for an athlete who
  has never pushed one.
- No coffee imagery.

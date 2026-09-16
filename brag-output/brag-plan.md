# Brag Plan: parkrun & brunch

## What is this app?
A Streamlit app that tracks three friends' parkrun histories and turns every
shared start line into a **form-adjusted** head-to-head, where the winner is
whoever beat their own recent form by the most. Finish order doesn't decide it.

## The angle
**"Finished last. Won anyway."** One real contest makes the case: Higginson
parkrun, Marlow, 1 Aug 2026. George crossed the line in 21:36, Duncan in 25:26,
Raju in 40:17, nearly 19 minutes behind George. Under the app's rules Raju
**won**, finishing 4.75% faster than form. That twist is the product. Everything
after it explains the twist and shows that the app has nine years of results
like it.

## Hook (first 2-3 seconds)
A plain finish board for that parkrun. The three times drop in one by one in
finish order, and Raju's 40:17 lands last with a big visible gap. The viewer
reads "Raju came last". Then the flip.

## Key moments (the middle)
- **The flip.** The board re-sorts by % against form. Raju climbs to 🥇 and the
  line "Raju won." slams in.
- **Why.** Each runner gets a target, the median of their last 91 days. Target
  becomes actual as a ticking counter: Raju 42:18 → 40:17 (−4.75%), George
  22:20 → 21:36 (−3.28%), Duncan 26:14 → 25:26 (−3.05%).
- **The lollipop chart.** A recreation of tab 3's victory chart: three dots
  shoot right from 0%, with the winning-margin bracket.
- **Scale.** Three stat cards arrive one at a time: 207 head-to-heads since
  2017 · 115 venues · buggy runs 🛒 judged against their own target.
- **The record.** Win bars grow: Raju 92 · George 82 · Duncan 34.

## Outro / punchline
The toast logo (PR&B in the three athletes' colours) and the name
**parkrun & brunch**, with the tagline "Beat your own form. Then brunch." and
the URL `parkrun-and-brunch.streamlit.app`.

## User flow worth showing
Pick a head-to-head (tab 3), read the scoreline and lollipop chart, then check
the all-time record (tab 2). The centrepiece scenes recreate the tab 3 result
view with the real Higginson data.

## Tone
- Preset: default
- Creative direction: Saturday-morning sports broadcast for three mates, like a results graphic with a twist
- Interpretation: playful and clean, and the numbers are played straight. The joke is that the slowest runner wins, so it gets a clear setup and a beat of hold before the flip.

## Format: landscape — 1920x1080
## Duration: 21s

## Visual identity (from the project)
- Background: `#23201d` (the logo's dark ground)
- Accent: `#f7cf8e` toast crumb, with `#c8822f` crust as the secondary colour
- Text: `#f7f3ee` on dark (the app's own light-theme text is `#31333F`)
- Athlete colours: George `#1b9e77`, Raju `#d95f02`, Duncan `#7570b3`
- Medal colours: `#FFB300` / `#B0B0B0` / `#C77B30`
- Display font: Source Sans 3, bold (Streamlit's default face), with tabular numerals for times
- Body font: Source Sans 3
- Strongest visual element: the victory lollipop chart plus the scoreline, and `assets/logo-toast.svg`
- **Hard rule: no coffee imagery.** This is the user's standing instruction. The app title's ☕ must not appear. Brunch motifs are allowed: toast, fried egg.
- **People:** first names only, as the app shows them. Nothing from the unlisted `/buggy-handicap` page.

## Share copy (draft)
Came last at parkrun. Won anyway. parkrun & brunch ranks my mates and me by how far we beat our own form, not by who finished first.

## Audio direction
- Role: warm upbeat bed with sports-graphic accents
- Music: `happy-beats-business-moves-vol-1` (120 BPM)
- Music treatment: starts at 0, sits low under the hook, rises slightly at the flip, fades out over the last 1.5s
- Music cue guidance: preset read. The beat grid runs from 3.02s at 0.5s spacing. Strong cues at 16.02 / 17.02 / 18.52 / 20.02s: use about 16.0 for the record bars and about 18.5 for the logo land. Stat cards: snap to every other beat (about 11.0 / 12.0 / 13.0) and hold all three together.
- Audio-reactive treatment: subtle. The logo glow breathes on the outro only.
- SFX posture: moderate and motion-matched
- Audio-coupled moments: finish rows landing (soft UI ticks), the flip (one whoosh or impact), counter ticks, the stat cards (card place), the logo (one clean hit)
- Restraint rule: no SFX on every line, and no sound that competes with the flip

## Storyboard

### Scene 1 — Finish board — 3.5s (0.0–3.5)
Caption: "Higginson parkrun, Marlow · Sat 1 Aug 2026". Three rows drop in at
about 0.4s intervals, each with a colour dot, name and time: George 21:36,
Duncan 25:26, Raju 40:17. Raju's row lands with visible space above it. Header:
"Finish order". All three rows hold together for at least 1.5s.
Sequential/interaction: yes, three rows one by one.
Audio intent: light, curious.
Audio-coupled idea: a tick per row.
Transition mood: hard, the rows re-sort in place → Scene 2

### Scene 2 — The flip — 3.5s (3.5–7.0)
The rows animate into a new order with medals: 🥇 Raju −4.75%, 🥈 George −3.28%,
🥉 Duncan −3.05%. The header changes to "vs their own form". Big line: **"Raju
won."** It holds at least 1.2s.
Sequential/interaction: yes, a reorder animation.
Audio intent: the payoff lands.
Audio-coupled idea: one whoosh for the reorder, one hit on "Raju won."
Transition mood: clean wipe → Scene 3

### Scene 3 — Beat your own form — 4.5s (7.0–11.5)
Headline: "Your target: your own last 91 days." Three compact athlete cards
with counters ticking from target to actual (42:18 → 40:17 and so on), then the
recreated lollipop chart: dots shoot right from 0%, and the bracket reads
"winning margin 1.47 pts". The headline holds at least 1.8s.
Sequential/interaction: yes, counters and then the chart.
Audio intent: momentum.
Audio-coupled idea: counter ticks, and a soft pop as each dot lands.
Transition mood: slide → Scene 4

### Scene 4 — Since 2017 — 4.5s (11.5–16.0)
Three cards arrive at about 1s intervals and hold together:
"207 head-to-heads" · "115 venues" · "🛒 Buggy runs get their own target".
Sequential/interaction: yes, cards one by one, with the full set holding at least 1.5s.
Audio intent: confident.
Audio-coupled idea: a card-place sound per card.
Transition mood: clean → Scene 5

### Scene 5 — The record — 2.5s (16.0–18.5)
Title: "All-time wins". Horizontal bars grow in athlete colours:
Raju 92 · George 82 · Duncan 34, with counters. The numbers settle by about 17.3s.
Sequential/interaction: yes, the bars grow.
Audio intent: a lift on the strong cue at 16.0.
Audio-coupled idea: bars start on the beat.
Transition mood: soft → Scene 6

### Scene 6 — Outro — 2.5s (18.5–21.0)
The toast logo scales in on the cue at about 18.5. "parkrun & brunch" appears,
then "Beat your own form. Then brunch." and then the URL. The music fades out.
Audio intent: warm button.
Audio-coupled idea: a logo hit and a subtle glow.

**Music mood for this video:** upbeat
**Audio summary:** a light bed under a quiet setup, a clear hit on the flip, a rising middle, and a warm fade on the logo.

Total: 3.5 + 3.5 + 4.5 + 4.5 + 2.5 + 2.5 = **21.0s**

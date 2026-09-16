# Hyperframes Composition Brief: parkrun & brunch

## Objective
Create a short launch-style brag video for parkrun & brunch.

## Output
- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape, 1920x1080
- Duration: 21 seconds

## Source Material
- Project root: `parkrun_and_brunch/`
- Primary files read: `CLAUDE.md` (the spec), `parkrun_app.py` (tab titles, intro and explainer copy), `parkrun_ui.py` (`ATHLETE_COLORS`, `PLACE_COLORS`, `fmt_time`), `assets/*.png` (phone screenshots of tabs 2, 3 and 5), `assets/logo-toast.svg`, and `data/parkrun_snapshot.duckdb` (all numbers below)
- Product name: parkrun & brunch
- Strongest claim: finish order doesn't decide the winner; beating your own form does
- Key UI to recreate: the tab 3 result view, meaning the scoreline plus the victory lollipop chart (dots out from 0%, the x-axis reversed so faster points right, the winning-margin bracket) plus the medal table
- Real data, Higginson parkrun (Marlow), Sat 1 Aug 2026:
  - Raju: target 42:18, actual 40:17, −4.75%, 🥇
  - George: target 22:20, actual 21:36, −3.28%, 🥈
  - Duncan: target 26:14, actual 25:26, −3.05%, 🥉
  - Winning margin: 1.47 pts
- Other stats: 207 head-to-heads since 2017, at 115 venues. All-time wins: Raju 92, George 82, Duncan 34.
- Copy that must appear verbatim or near-verbatim:
  - "faster than form" (the app's own wording)
  - "winning margin"
  - "parkrun & brunch"

## Creative Direction
- Tone preset: default
- Creative direction: a Saturday-morning sports-broadcast results graphic, with a twist
- Interpretation: playful and clean, with the numbers played straight. A clear setup, a beat of hold, then the flip.
- Angle: "Finished last. Won anyway." Raju crossed the line nearly 19 minutes after George and still won, because the app ranks by how far each runner beat their own 91-day median.
- Hook: the finish board lands in finish order, with Raju's 40:17 last.
- Outro / punchline: toast logo, "parkrun & brunch", then "Beat your own form. Then brunch.", then the URL.
- Avoid:
  - Generic SaaS language
  - Abstract filler visuals
  - **Any coffee imagery** (the user's standing rule; the app title's ☕ is excluded too)
  - Anything from the unlisted `/buggy-handicap` page; first names only

## Visual Identity
- Background: `#23201d` (logo ground), with a warm radial lift
- Text: `#f7f3ee`
- Accent: `#f7cf8e` toast, `#c8822f` crust
- Athletes: George `#1b9e77`, Raju `#d95f02`, Duncan `#7570b3`
- Medals: `#FFB300` / `#B0B0B0` / `#C77B30`
- Fonts: Source Sans 3 variable (Streamlit's face, bundled locally) for display and body, with tabular numerals for times
- Visual references: the lollipop chart, the bordered result cards, and the PR&B toast logo

## Storyboard
The creative contract is `brag-output/brag-plan.md`.
1. Finish board — 3.5s — three rows in finish order, Raju last
2. The flip — 3.5s — rows re-sort by % vs form; "Raju won."
3. Beat your own form — 4.5s — the 91-day target line, target → actual counters, lollipop chart
4. Since 2017 — 4.5s — three stat cards
5. The record — 2.5s — win bars
6. Outro — 2.5s — logo, name, tagline, URL

## Audio
- Role: warm upbeat bed with sports-graphic accents
- Arc: low under the hook, a hit at the flip, a lift at the record, a warm fade on the logo
- Music: `assets/music/happy-beats-business-moves-vol-1-by-ende-dot-app.mp3`, volume 0.35, fading out over the last 1.5s
- Cue guidance: `~/.claude/skills/brag/assets/music/cues/happy-beats-business-moves-vol-1-by-ende-dot-app.music-cues.json`. Strong cues at 16.02 and 18.52. The stat cards use every other beat from 11.02.
- Audio-reactive: subtle. The RMS level breathes the background's warm glow and the outro logo glow (`audio-data.js`).
- SFX (chosen to match the implemented motion): `interface/drop_*` for the finish rows, `casino/card-slide-1` for the re-sort, `impact/impactSoft_medium_001` for "Raju won.", `interface/click_003` for the chart dots, `casino/card-place-1` for the stat cards, `casino/chips-stack-1` for the bars, `impact/impactBell_heavy_000` for the logo

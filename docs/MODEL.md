# The buggy estimator, as built

George and Duncan sometimes run pushing a buggy. parkrun records nothing about
it, so without a label their times pool into one form target. That target is a
median, which a slow minority barely shifts — so the buggy runs end up measured
against ordinary form and penalised by around 4.5 percentage points, while the
ordinary runs are flattered by about 0.2. (Medians throughout: George has one
54:20 buggy walk that puts the mean out by several points.) This model supplies
the missing label.

`buggy_estimator.py` holds the model; `parkrun_pipeline.apply_model_labels`
does the writing. The split is deliberate — the model is side-effect free and
testable without a database.

**The fitted numbers below move every refresh.** They are recorded as of
12 Sep 2026 so a later reading has something to compare against. The reasoning
is the durable part — and the 12 Sep refit demonstrates why: one corrected
label moved Duncan's coefficients further than any week of new runs has (see
*A regular call has now been wrong*).

---

## The shape

Logistic regression:

```
score = intercept + Σ (coefficient × feature)
p     = 1 / (1 + e^−score)
```

`p > 0.5` is a buggy call. `confidence = max(p, 1−p)`.

Two models, one per athlete — same machinery, independently fitted. George's
coefficients say nothing about Duncan's, and the two have ended up emphasising
almost opposite things.

Fitted with `scipy.optimize.minimize` (L-BFGS-B) rather than scikit-learn: no
new dependency, and it matches how `buggy_handicap.py` already fits.

---

## The four features

All computed from strictly earlier runs. A test appends a run and asserts every
earlier row's features are byte-identical — that is the guard against lookahead,
and it is the property the whole walk-forward rests on.

### `excess` — slow *for this course*?

`time ÷ expected − 1`, where `expected` comes from a cascade
(`baseline_for`), first rule that fires:

1. **≥ 2 prior runs at this same event** → their median. The clean comparison:
   same hills, same surface.
2. **≥ 8 runs anywhere within ±182 days** → their **25th percentile**, not the
   median. A buggy only ever makes you slower, so the baseline should sit near
   the athlete's good days; a median would let a spell of buggy runs raise the
   bar they are judged against.
3. Otherwise `NaN`, median-imputed at fit time.

Leave-one-out — a run is never its own baseline. `add_baselines` is shared with
`scripts/export_buggy_review.py`, so the review sheet and the model see
identical evidence.

**Do not read its small coefficient as unimportance.** Removing it costs George
nine points of accuracy — it is his second most load-bearing feature. A
coefficient from one final fit does not measure what a feature contributes
across seventy sequential refits.

### `form_resid` — slow *for him, lately*?

`log(time ÷ median of his non-buggy times over the previous 91 days)`,
excluding the day itself.

91 days is `TARGET_WINDOW_DAYS`, matched to the head-to-head's form window on
purpose: "slow" should mean the same thing in the estimator as it does in the
app. It is defined once, in `parkrun_core.py` — a module with no third-party
imports, which is what lets the pipeline, the app and this all read the same
integer.

Non-buggy runs only in the baseline, or a buggy spell drags its own reference
upward and hides itself.

### `course_diff` — how hard is this parkrun?

From the `course_difficulty` table, 0.8–11.6 where 12 is hardest. The estimator
is that table's only reader.

Median-imputed when NULL — `course_difficulty` covers the published UK set, and
dropping the rest would quietly exclude every overseas parkrun.

Negative in both models, correctly: a hard course explains a slow time without
a buggy, so hardness argues against.

### `event_buggy_share` — does he push *here*?

Share of his runs at this event that were pushed, counted from his first buggy
run onward, shrunk toward his era-wide rate:

```
(k + EVENT_RATE_PRIOR × base) / (n + EVENT_RATE_PRIOR)
```

**The era cutoff.** Counting from the start of history would read George's
Egham Orbit as 0-of-40 rather than 0-of-14, and the feature would mostly
measure how long someone has been running. The start date comes from prior runs
only, so it moves forward with history rather than being imported from the
future.

**The shrinkage** is how the run count earns its say. A raw share cannot tell
1-of-1 from 8-of-8, and a course never visited would assert a confident 0%.
Shrunk, an unvisited course says what is true of it: nothing beyond what the
athlete does in general. It also keeps the feature defined everywhere, so no
run drops out of training over it.

This is the only feature that reads back the model's own labels, which makes it
the feedback channel — see **The loop** below.

---

## Removed on evidence

Both looked plausible enough to build. A walk-forward ablation, dropping each
feature in turn and refitting from scratch, found neither earning its place.

**`run_len`** — the signed length of the current same-sign form-residual
streak. The one feature aimed at sequence rather than a single run: a buggy
comes in spells, so consecutive slow weeks should mean more than one. It fitted
with *opposite signs* for the two athletes, which is the signature of noise, and
dropping it improved every metric for both.

**`prior_rate`** — the buggy share of the previous 10 labelled runs. A
near-substitute for `event_buggy_share`: the same "is he in a buggy phase"
question asked temporally rather than by course. **Keeping both scored worse
than keeping either.** `event_buggy_share` survived because it also carries
*where* he pushes, which is the course confound the estimator has always been
weakest on.

Removing `prior_rate` had a second effect worth knowing: it closed Duncan's
feedback loop, and it pushed George's tuned half-life from 24 to 36 months —
with no other way to track a change in habit, the tuner reaches for a longer
memory rather than a shorter one.

---

## Standardisation

Three features are z-scored against the training set's median and SD.
`event_buggy_share` is not (`RAW_FEATURES`).

It is a proportion already bounded on [0, 1], and it sits at exactly **0** for
the entire pre-buggy decade. Z-scoring against that pulls the median to 0 and
the SD to a fraction of the real spread, so every in-era value lands two SDs
out. Measured, that broke the model — the then-present `prior_rate` fitted
*negative*, asserting that a recent buggy spell makes a buggy less likely.

```
                   George acc   Duncan acc   prior_rate coef
z-score both          0.86         0.69           −0.60
raw prior_rate        0.89         0.69           −0.70
raw both              0.89         0.77           +0.40   ← adopted
era-only scaling      0.84         0.62           −0.60
```

Imputation still applies to raw features; only the rescaling is skipped.

---

## Class balancing — required, not optional

Positives are reweighted by the imbalance ratio so both classes carry equal
total weight.

Without it, "always say regular" scores 91% for George and 97% for Duncan, and
the fit takes that deal: **zero true positives, the model never fires once.**
This, not recency, is what makes a low-base-rate model functional at all.

---

## Recency weighting

Exponential decay by age — a run today weighs 1, a run one half-life old
weighs 0.5 — multiplied by the class weight.

The half-life is tuned per athlete by an inner sweep on prior data only, never
on the run being scored. Two constraints, both learned from real failures:

**Scored on log-loss, not accuracy.** Accuracy is degenerate at these base
rates — for Duncan it returns the identical number at every half-life, because
always-guess-regular scores it, and the tie-break then silently picks "no
decay": the opposite of what a regime change needs.

**Floored on effective positives.** A half-life that discounts confirmed buggy
runs below `MIN_EFFECTIVE_POSITIVES` is refused however well it scores — it is
buying adaptivity with the only evidence there is.

That floor measures the **sum of recency weights over the positives**, not a
Kish effective sample size. Kish measures how *uneven* weights are, so three
equally-old positives score 3.0 however hard they are discounted — blind to the
thing the floor exists to catch. A test enforces the distinction.

Retained by decision. Measured on all the data there is, turning it off would
gain George two runs of the seventy scored, which is not a finding on this
sample — and it is now the only mechanism that would catch a change in habit.

---

## The constants

| | | |
|---|---|---|
| `BASE_MIN_EVENT_RUNS` | 2 | prior runs at a course before it can be a baseline |
| `BASE_MIN_WINDOW_RUNS` | 8 | runs needed for the ±182d fallback baseline |
| `BASE_WINDOW_DAYS` | 182 | that fallback's half-width |
| `TARGET_WINDOW_DAYS` | 91 | form window; matched to the head-to-head's |
| `EVENT_RATE_PRIOR` | 3 | pseudo-runs of shrinkage on the per-event share |
| `L2` | 1.0 | ridge penalty on the slopes; **never the intercept** |
| `HALF_LIVES_MONTHS` | 3…36, None | the tuning sweep |
| `DEFAULT_HALF_LIFE` | 12 | cold start, below `MIN_TUNE_HISTORY` |
| `MIN_TUNE_HISTORY` | 25 | labelled runs before the sweep is trusted |
| `MIN_EFFECTIVE_POSITIVES` | 3.0 | floor the half-life may not breach |
| `MIN_TRAIN_ROWS` | 15 | below this, no model at all — returns `None` |

The intercept is unpenalised on purpose: it carries the athlete's base rate, and
shrinking it toward zero would assert a 50/50 prior that is wrong for both.

---

## Fitted, 12 Sep 2026

### George

```
labelled 351, buggy 31, base rate 8.8%
half-life none (tuned)          class weight on a positive 10.3×

                       coef     per 1 SD
form_resid           +1.970      +1.97
event_buggy_share    +4.695      +1.14
course_diff          −0.807      −0.81
excess               +0.285      +0.29
intercept            −2.703                 6.3% at feature-zero

walk-forward, 72 scored (279 warm-up)   TP 28  FP 5  FN 2  TN 37
precision 0.85   recall 0.93   accuracy 0.90   log-loss 0.410
always-majority baseline 0.58 (on the scored set: 42 of the 72 were regular)

says buggy   → right 85% (n=33)
says regular → right 95% (n=39)
```

Reads as **"slow for him, at a course he pushes at."** Calibration is sound
above 0.7 and his calls cluster high: his `regular` calls have a median
confidence of 0.91 and none below 0.62.

His tuned half-life moved from 36 months to **none** this week. Nothing about
his running changed — his two `model` rows simply pushed the labelled set past
a point where the inner sweep preferred the longer memory outright. Worth
watching rather than reading into: with `prior_rate` removed, the tuner has no
other way to track a change of habit, so it has been reaching for longer
memories all along.

### Duncan

```
labelled 177, buggy 7, base rate 4.0%
half-life none (tuned — his positives are all 2026; decay would only lose them)
class weight on a positive 24.3×

                       coef     per 1 SD
course_diff          −3.085      −3.09
form_resid           +0.962      +0.96
excess               +0.828      +0.83
event_buggy_share    +1.751      +0.29
intercept            −2.686                 6.4% at feature-zero

walk-forward, 27 scored (150 warm-up)   TP 5  FP 6  FN 1  TN 15
precision 0.45   recall 0.83   accuracy 0.74   log-loss 0.674
always-majority baseline 0.78 (on the scored set: 21 of the 27 were regular)

says buggy   → right 45% (n=11)
says regular → right 94% (n=16)
```

Reads as **"slow for this course, and don't be fooled by a hard one."**
`course_diff` still outweighs the other three put together, but by half again
rather than the double it was a week ago — and the order beneath it changed.
The 12 Sep correction halved `form_resid` (+1.64 → +0.96) and tripled `excess`
(+0.28 → +0.83), moving his model off "slow for him lately" and onto "slow for
this course". That is the right lesson from a buggy run that finished on form,
and a demonstration of how much a single label moves a fit at seven positives.

His accuracy sits below the 0.78 you would get by saying "regular" every time —
but that null model catches none of his buggy runs and his catches five of six.
**The asymmetry is still the thing to hold onto**, in its surviving form: a
`regular` call from him is right 94% of the time and a `buggy` call is a coin
flip slightly against. Until 12 Sep 2026 the first half of that read *never
wrong, 15 of 15*; see below for the run that ended it.

His false positives cluster at Lordship. `event_buggy_share` does not fix that
and cannot: Lordship is where he pushes *most* (4 of 9), so the feature honestly
reports that he brings it there. The confound was never "a course he never
buggies at" — it is a hard course where he does both.

---

## The loop

`model` rows train later fits, by decision. That is what lets the training set
grow without anyone doing anything, and it is the model's main risk: an
uncorrected wrong label becomes evidence for the next call.

`event_buggy_share` is the only channel — the other three read times, dates and
an external difficulty score, none of which the model can contaminate.

Measured with every prediction fed back and **never** corrected:

```
              corrected    uncorrected
George          0.90          0.47
Duncan          0.74          0.78
```

**George is the one whose errors compound**, which is not where the risk was
expected. He pushes at a consistent handful of courses, so `event_buggy_share`
is high-leverage for him: mislabel one run at Osterley and Osterley's share
rises, which makes the next Osterley run more likely to be flagged. Duncan's
model leans on course difficulty — a fixed external number — so his loop is
inert.

**Compounding is not the same as frequency, and conflating the two gives the
wrong advice.** Duncan's model is wrong *more often*: 6 of his 11 buggy calls
against George's 5 of 33. Each of those still misstates a head-to-head result
until someone corrects it; it simply does not also degrade the next fit.

That bound **overstates the danger**: the simulation discards confirmed labels
as it walks, whereas `run_modes` is write-once and every `user` label is a
permanent anchor no estimate can overwrite. Real drift sits between the two
columns, nearer the top, and only moves down if corrections stop.

Two countermeasures, both live:

- **The notification** carries every call the same day with its confidence and
  the measured reliability of that *kind* of call.
- **The drift check** logs walk-forward accuracy twice each refresh — training
  on `user`+`model`, and on `user` only. While those agree the loop is harmless.
  The first drifting above the second is the model scoring well against its own
  opinions, which is what self-reinforcement looks like from the inside.

**Practically: both athletes' buggy calls need review, for different reasons.**
Duncan's are more likely to be wrong; George's do more damage when they are.

## A regular call has now been wrong

For the model's first week the rule that needed no caveat was **"it is always a
buggy call that goes wrong"** — every error either model had made, in either
direction, was a run it called buggy. That rule **broke on 12 Sep 2026** and is
recorded here rather than deleted, because how it broke is the useful part.

Duncan ran Cassiobury in 26:52. The model called it `regular` at a confidence
of **0.52** — the least confident verdict either model has produced — and he
confirmed he had the buggy. His first false negative: recall 1.00 → 0.83,
`regular` calls 15 of 15 → 15 of 16.

It failed where the features go blank rather than where they disagree. Three of
the four had nothing to say about that run:

- **`excess`** — one prior run at Cassiobury, below `BASE_MIN_EVENT_RUNS`, so
  the baseline fell through to the ±182-day q25 (24:54). The run it could not
  use was 26:53, one second *slower* than the run being scored.
- **`event_buggy_share`** — never at that course in the buggy era, so the
  shrinkage returned his era rate exactly. A statement about Duncan, not about
  Cassiobury.
- **`form_resid`** — 26:52 against a 26:43 median. Nine seconds.

That left `course_diff`, which supplied **72%** of the push toward buggy purely
because Cassiobury is easy (1.8/12). The verdict was about the course, not the
runner. `baseline_for`'s cascade degrades quietly by design, and this is what
that costs: a fallback baseline reads as weak evidence rather than as none.

**The durable reading.** Direction is the weaker guide; **confidence is the
better one.** Of the two calls Duncan's model has ever made below 0.60, both
were wrong, while his other `regular` calls sit at a median confidence of 0.92.
A `regular` call is right 94-95% for both athletes and a `buggy` call is not —
but a *low-confidence* call is unreliable whichever way it points.

Refitting on the correction moved his coefficients more than one label should:
`form_resid` +1.635 → +0.962 and `excess` +0.279 → +0.828. That is the model
learning that an on-form time can still be a buggy run — the right lesson, and
a reminder of how much leverage a single label carries at seven positives.

---

## Reproducing any of this

```bash
python buggy_estimator.py                 # walk-forward report, both athletes
python buggy_estimator.py --athlete George   # or Duncan; default both
pytest tests/                             # 44 cases, no database
```

The module writes nothing. `score_unlabelled` returns the calls and the
pipeline does the insert.

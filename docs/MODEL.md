# The buggy estimator, as built

George and Duncan sometimes run pushing a buggy. parkrun records nothing about
it, so without a label their times pool into one form target that flatters the
buggy runs and penalises the rest. This model supplies the missing label.

`buggy_estimator.py` holds the model; `parkrun_pipeline.apply_model_labels`
does the writing. The split is deliberate — the model is side-effect free and
testable without a database.

**The fitted numbers below move every refresh.** They are recorded as of
5 Sep 2026 so a later reading has something to compare against. The reasoning
is the durable part.

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
app. A test reads the constant out of `parkrun_pipeline.py` to keep them in
step, because this module cannot import the pipeline (it pulls in requests and
bs4).

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

## Fitted, 5 Sep 2026

### George

```
labelled 349, buggy 31, base rate 8.9%
half-life 36 months (tuned)     class weight on a positive 10.3×

                       coef     per 1 SD
form_resid           +1.974      +1.98
event_buggy_share    +3.479      +0.85
course_diff          −0.686      −0.69
excess               +0.069      +0.07
intercept            −1.822                13.9% at feature-zero

walk-forward, 70 scored (279 warm-up)   TP 28  FP 5  FN 2  TN 35
precision 0.85   recall 0.93   accuracy 0.90   log-loss 0.415
always-majority baseline 0.57 (on the scored set: 40 of the 70 were regular)

says buggy   → right 85% (n=33)
says regular → right 95% (n=37)
```

Reads as **"slow for him, at a course he pushes at."** Calibration is sound
above 0.7 and 66 of his 70 calls sit above 0.8.

### Duncan

```
labelled 176, buggy 6, base rate 3.4%
half-life none (tuned — his positives are all 2026; decay would only lose them)
class weight on a positive 28.3×

                       coef     per 1 SD
course_diff          −2.897      −3.21
form_resid           +1.635      +1.65
event_buggy_share    +1.613      +0.27
excess               +0.279      +0.29
intercept            −2.901                 5.2% at feature-zero

walk-forward, 26 scored (150 warm-up)   TP 5  FP 6  FN 0  TN 15
precision 0.45   recall 1.00   accuracy 0.77   log-loss 0.672
always-majority baseline 0.81 (on the scored set: 21 of the 26 were regular)

says buggy   → right 45% (n=11)
says regular → right 100% (n=15)
```

Reads as **"slow for him, and don't be fooled by a hard course."**
`course_diff` is nearly twice everything else combined.

His accuracy sits below the 0.81 you would get by saying "regular" every time —
but that null model catches none of his buggy runs and his catches all five.
**The asymmetry is the thing to hold onto:** when he says regular he has never
been wrong; when he says buggy it is a coin flip slightly against.

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
George          0.90          0.49
Duncan          0.77          0.81
```

**George is the one at risk**, which is not where the risk was expected. He
pushes at a consistent handful of courses, so `event_buggy_share` is
high-leverage for him: mislabel one run at Osterley and Osterley's share rises,
which makes the next Osterley run more likely to be flagged. Duncan's model
leans on course difficulty — a fixed external number — so his loop is inert.

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

**Practically: George's buggy calls are the ones worth checking.**

---

## Reproducing any of this

```bash
python buggy_estimator.py                 # walk-forward report, both athletes
python buggy_estimator.py --athlete George   # or Duncan; default both
pytest tests/                             # 43 cases, no database
```

The module writes nothing. `score_unlabelled` returns the calls and the
pipeline does the insert.

# Method

How the weights in `usage_weights/data/weights/` were measured. Times are UTC.

## The question

The weekly allowance on a Claude subscription is shown as a percent on two
meters: all models, and Fable (which can use at most half of the week). The
vendor publishes no formula; it says only that usage depends on conversation
length, features, model and effort. Outside measurers have fitted weighted
token counts (a weight per model and token type). The prior used here is the
Atlas ADE table, "Plan usage burn data"
(https://atlas-ade.dev/docs/plan-usage-burn-data/, model a5v3, checked
2026-10-29, one Ultra account). The earlier she-llac reverse engineering
(https://she-llac.com/claude-limits, 2026-01-25) gives the Haiku to Opus ratio
and the "credits" idea; its absolute numbers no longer fit this account.

Two runs tested the prior on one Max 20x account and replaced what they could.
Weights are points per million tokens; a point is 1% of the week on that meter.

## Readings are inequalities

Both ways of reading the meter show whole percents.

- The CLI returns a fraction with two decimals on every call (`seven_day` for
  all models, `seven_day_overage_included` for Fable). It truncates, so a
  reading of 67 means the true value is in [67, 68). It is taken when the
  call starts.
- A 5-minute usage collector polls the usage endpoint. It rounds to the nearest
  percent, so 67 means [66.5, 67.5). Run 2 settled this: with rounding every
  reading fits; with truncation no weights fit.

Using both readers where they overlap gives half-point steps. Every reading
becomes two inequalities on the model: the meter value at that time, which is a
start offset plus the sum over token types of weight times tokens charged so
far, lies inside the reading's range.

## Charge at call end

A call's tokens count once the call has ended. Charging each call at its start,
or spreading it over its run, fits no set of weights. In run 2's cache-write
blocks each all-models tick showed on the first probe after a call ended, 0 to
7 seconds later, so there is no minutes-long lag.

## The linear-program fit (`kit/fit.py`)

Unknowns: one start offset per meter, the free weights (one per meter), and a
margin d. Each reading with range [lo, hi) gives `lo + d <= V` and
`V <= hi - 1e-6 - d`. Weights are at least 0. The point estimate maximizes d.
A tolerance lets d go as low as -0.03 points, because a few readings taken a
second or two around another call's end miss by 0.003 to 0.019 points. The
range of each weight is its smallest and largest value with d held at the
tolerance. The fit is described by `fit-spec.json`:

- `free`: weights to fit; their token types share one weight (uncached input
  is counted with 5-minute cache writes, as it was under 1,500 tokens all night).
- `fixed`: types too small to fit, held at a factor times the prior (Opus
  output and cache writes at 1.22 times the prior, from run 1; Haiku probes at
  the prior).
- `charge`: `end`, `start` or `linear`.

Three solves run: the collector as a rounding reader (the real one), the
collector as a truncating reader (a what-if, which is infeasible) and CLI
readings only.

## The interval method (`kit/intervals.py`)

When token types cannot be separated (run 1, real agent work), use ticks. A
tick is the first CLI all-models reading at a new value. Between two ticks the
meter moved exactly one point. Sum the prior's points for the tokens charged in
that interval; the factor is 1 divided by that sum, how many times the prior
the workload moved the meter. Tokens are charged at call end or spread over
each call's run; the two differ by 3% at most.

## Guarding a run against outside use (`kit/guard.py`)

Any call that is not the kit's moves the meter without the kit's tokens and
biases a fitted weight low. `kit/blocks.py` guards in three stages.

- **Before.** It looks for other `claude` processes and transcripts changed in
  the last 10 minutes, and refuses to start until clear. `--allow-outside-use`
  overrides, and the run is marked `unguarded`. It prints what it cannot see
  (other devices, Claude chat, scheduled tasks and routines) and a suggested
  quiet `--start`. `--idle-minutes N` probes an idle stretch first. A tick with
  no kit tokens there is outside use; on small plans the stretch is short, so it
  is advisory.
- **During.** Each block does the same unit of work per call, so each whole
  point costs about the same tokens; an interval with under half the median kit
  prior points of the others is flagged. A meter that should not move (the
  Fable meter in a block that uses no Fable model) is watched, and
  `--gap-minutes N` leaves probed idle gaps between blocks. A tick between two
  readings both inside an idle stretch is outside use or meter lag.
- **After.** Transcripts stamped inside the run window are listed. The kit's own
  calls run with `--no-session-persistence`, so they leave none.

The driver writes `guard.json` (the `guard` record, for example
`{"other_sessions_seen": 0, "idle_minutes": 240, "idle_ticks": 0, "unguarded": false}`,
and the evidence rows `kind`, `at`, `detail`). `kit/intervals.py` reads it and
puts `evidence` and a `grade` on every interval: `clean` (no evidence),
`corrected` (every item has a recorded `correction`) or `contaminated`. Only
`clean` and `corrected` intervals feed `kit/build_table.py`. `kit/fit.py`
refuses a run whose `guard.json` has uncorrected evidence, because outside use
shifts every later reading. Runs with no `guard.json`, the two committed ones,
were kept clean by scheduling (quiet windows) and are graded by schedule only:
their intervals carry no `grade` and count as usable.

## Run 1: a mixed workload

79 Opus runs then 106 Sonnet runs of real agent tasks (742 calls, 12 fixtures,
no judge calls) from 08:00 to 08:50 on 2026-11-19. Cache writes were 62% (Opus)
to 72% (Sonnet) of the prior's points, output 20% to 30%, cache reads 7% to 8%.
The meter went from 37 to 42. The factors were about 1.2 for Opus and 1.5 for
Sonnet. Because the mix is fixed, run 1 measures output and cache writes only
together, which is why those rows have basis `joint`.

## Run 2: one type at a time (`kit/blocks.py`)

Synthetic calls with no tools, no settings and thinking off. An environment
variable sets the prompt-caching mode per call. Plain-English filler text is
framed as a load test, because unframed filler drew safety-classifier refusals.

| Block | What each call does |
| --- | --- |
| out | writes a 4,500-word story (about 9k output tokens) |
| cr | re-sends one fixed prompt of about 173k tokens (one warm call first), replies "ok" |
| cw5 | a fresh 173k-token prompt each time, 5-minute cache, one call at a time |
| cw1h | the same with 1-hour caching |

Each block runs until the all-models meter is a set number of points above its
first reading. Dollar and minute limits are safeties only. Small probe calls
every 10 seconds add readings between calls during the cache-write blocks.
Run 2 used Fable 5.2 for all four blocks and Opus 5.5 for one cache-read block,
from 08:00 to 08:50 on 2026-11-20.

## Putting the runs together (`kit/build_table.py`)

- Fable rows come straight from run 2's fit. Both meters are fitted, so the
  Fable meter has its own table (it is not exactly twice the all-models one).
- Opus cache read is run 2's fit. Run 1's pure-Opus intervals give a factor k
  for the whole mix; with cache read's measured weight and its share s of the
  interval's prior points, the shared factor for the rest is
  f = (k - s r) / (1 - s), where r is the measured Opus cache-read weight over
  the prior's. Opus output and cache writes get weight f times the prior.
- Sonnet output and cache writes get run 1's factor times the prior.
- A joint range is never narrower than 3% either side of its point. Two
  intervals of the same Opus mix gave factors 2.3% either side of their mean,
  and the Sonnet factor rests on a single interval.
- Everything else is a stand-in: `assumed`, with a range of 0.5 to 2 times the
  point. That factor comes from the directly measured weights, which landed
  between 0.53 and 1.79 times the prior.

## What was held fixed and what was checked

- Held fixed: model, prompts (synthetic or fixed fixtures), no tools, thinking
  off in run 2, contexts under 200k tokens.
- Background: in run 1 the meter sat at 37 for 9.5 hours before the window, so
  other use was under 0.1 points an hour. In run 2 the collector's readings
  before and after the window were flat, and no other use of the account ran
  in the window.
- The Fable meter stayed flat all night in run 1 (no Fable calls), a check
  that nothing else ran.
- Fable 5.2's cache-read price reproduces the CLI's dollar figure for the
  cache-read block ($8.27 computed, $8.29 reported).

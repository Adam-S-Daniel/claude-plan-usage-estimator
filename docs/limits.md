# Limits

What these tables can and cannot tell you.

- **One account, two nights.** Everything was measured on one Max 20x account
  (renamed Ultra). Other plans or accounts may weigh tokens differently.
- **Some weights rest on one or two ticks.** Opus cache read rests on one
  all-models tick, and Fable 1-hour cache writes on one all-models and one
  Fable-meter tick. Their ranges are wide for that reason.
- **Fable rows were measured during the Fable 5.2 launch offer.** The Fable
  meter may be more generous then than later. Re-run the Fable blocks after
  the offer ends; `kit/blocks.py` and the plan need no changes.
- **Contexts under 200k tokens only.** Long-context calls may be weighted
  differently.
- **Not measured:** Haiku and Sonnet cache reads, uncached input on any model,
  and output separate from cache writes on Opus and Sonnet. Those rows are
  stand-ins (`assumed`, range 0.5 to 2 times the point) or `joint` (accurate
  for mixes like run 1's, roughly two-thirds cache writes and a quarter
  output).
- **Run 1 is one workload shape and two models.** Haiku, Fable and 1-hour cache
  writes were not in it. All its cache writes were 5-minute writes.
- **Readings are whole percents.** A point is the finest step. Short work
  moves the meter less than one tick; estimates are for totals over many calls.
- **Meter lag could not be fully tested in run 1** (steady load hides it); run
  2's cache-write blocks showed 0 to 7 seconds.
- **No rate was changed by these runs, and nothing here is confirmed by the
  vendor.** The weights are inferred.

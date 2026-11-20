# Night 1: 79 Opus and 106 Sonnet runs of real agent workloads

A mixed workload on one account, quiet otherwise. 79 Opus runs (318 calls)
and then 106 Sonnet runs (424 calls) of real agent tasks with no judge calls:
742 calls in all, 12 fixtures, one trial each. Every cache write was a
5-minute write (13.2M tokens, no 1-hour writes). The all-models meter went
from 37 to 42 on the CLI's reading.

Files:

- `calls.jsonl`: one row per model call, sorted by `ended`: `tag` (the call's
  role in the agent run), `model`, `started`, `ended` (UTC), `tokens` by the
  five token types, `cost_usd` (the CLI's own dollars).
- `readings.jsonl`: one row per CLI meter reading, sorted by `at`: `meter`,
  `reader`, whole `percent`. Each reading was taken as a call began.
- `intervals.json`: output of `kit/intervals.py` (ticks, and for each tick
  interval the prior's points and the factor 1 / points).

Reproduce:

    python kit/intervals.py data/2026-11-19-night1 --prior atlas-2026-10-29

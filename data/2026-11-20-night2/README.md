# Night 2: one token type at a time

Synthetic `claude -p` calls built so one token type dominates each block
(`kit/blocks.py` with `kit/plan.example.json`). Every context stayed under
200k tokens, in a 50-minute window with no failures. 755 calls (708 block
calls and 47 small probe calls), 999 CLI readings and 24 readings from a
5-minute usage collector.

| Block | Calls | API dollars | All-models points (fitted) | Fable-meter points (fitted) |
| --- | --- | --- | --- | --- |
| fable-out (stories, about 9.7k output tokens each) | 80 | 32.44 | 2.71 | 5.69 |
| fable-cr (re-sends a 173k-token prompt) | 147 | 8.29 | 0.99 | 1.56 |
| fable-cw5 (fresh prompt, 5-minute cache) | 11 | 18.95 | 2.06 | 3.11 |
| fable-cw1h (fresh prompt, 1-hour cache) | 6 | 16.63 | 0.86 | 1.72 |
| opus-cr (re-sends a 173k-token prompt) | 464 | 19.47 | 1.03 | 0 |
| Total | 708 | 95.78 | 7.64 | 12.10 |

Files: `calls.jsonl` (one row per call, as in night 1; tags `probe` are the
probe calls), `readings.jsonl` (`reader` is `cli`, taken at each call's start,
or `usage_endpoint`), `fit-spec.json` (which weights are free and which are
held), `fit.json` (the three solves with point values and ranges).

Reproduce (needs `pip install ".[fit]"`):

    python kit/fit.py data/2026-11-20-night2

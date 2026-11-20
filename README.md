# claude-plan-usage-estimator

Predict how much of a Claude subscription's weekly allowance a piece of work
used, from its token counts by type.

This repo holds dated weight tables, one estimate function, the measurement
kit that produced the tables, the data from the two measurement runs, and the
method. It is one account's measurements (Max 20x, now called Ultra). It takes
no data from other accounts and it is not a usage monitor: it turns token
counts you already have into points. A point is 1% of the weekly allowance on
one meter.

## Weights

Millions of tokens that move a meter by one point (1 / weight), with the range
in parentheses. `measured` rows were fitted on their own, `joint` rows were
measured only together with other token types, `assumed` rows are stand-ins
(range is a factor of 2 either way), `published` rows are copied from an
outside table. Details are in `usage_weights/data/weights/`.

<!-- weights:begin -->

All-models weekly meter: millions of tokens per point (range), table `2026-11-20`.

| Model | Token type | M tokens per point | Basis |
| --- | --- | --- | --- |
| claude-fable-5-2 | input | 0.7 (0.35 to 1.4) | assumed |
| claude-fable-5-2 | output | 0.303 (0.268 to 0.318) | measured |
| claude-fable-5-2 | cache_read | 39.9 (31.8 to 64) | measured |
| claude-fable-5-2 | cache_write_5m | 0.921 (0.861 to 1.03) | measured |
| claude-fable-5-2 | cache_write_1h | 1.21 (0.822 to 1.41) | measured |
| claude-opus-5-5 | input | 3 (1.5 to 6) | assumed |
| claude-opus-5-5 | output | 0.567 (0.532 to 0.588) | joint |
| claude-opus-5-5 | cache_read | 94.7 (88 to 140) | measured |
| claude-opus-5-5 | cache_write_5m | 3.95 (3.7 to 4.09) | joint |
| claude-opus-5-5 | cache_write_1h | 3.95 (1.97 to 7.9) | assumed |
| claude-sonnet-5-5 | input | 5 (2.5 to 10) | assumed |
| claude-sonnet-5-5 | output | 0.747 (0.725 to 0.77) | joint |
| claude-sonnet-5-5 | cache_read | 197 (98.5 to 394) | assumed |
| claude-sonnet-5-5 | cache_write_5m | 5.23 (5.08 to 5.39) | joint |
| claude-sonnet-5-5 | cache_write_1h | 5.23 (2.61 to 10.5) | assumed |
| claude-haiku-5-5 | input | 15 (7.5 to 30) | assumed |
| claude-haiku-5-5 | output | 3.3 (1.65 to 6.6) | assumed |
| claude-haiku-5-5 | cache_read | 850 (425 to 1700) | assumed |
| claude-haiku-5-5 | cache_write_5m | 23 (11.5 to 46) | assumed |
| claude-haiku-5-5 | cache_write_1h | 23 (11.5 to 46) | assumed |

Fable weekly meter: millions of tokens per point (range), table `2026-11-20`.

| Model | Token type | M tokens per point | Basis |
| --- | --- | --- | --- |
| claude-fable-5-2 | input | 0.35 (0.175 to 0.7) | assumed |
| claude-fable-5-2 | output | 0.142 (0.14 to 0.144) | measured |
| claude-fable-5-2 | cache_read | 24.6 (23.3 to 47.7) | measured |
| claude-fable-5-2 | cache_write_5m | 0.609 (0.507 to 0.622) | measured |
| claude-fable-5-2 | cache_write_1h | 0.603 (0.444 to 0.871) | measured |

<!-- weights:end -->

## Quick start

```
pip install .
python -m usage_weights estimate --table 2026-11-20 calls.jsonl
```

One record per line: `{"model": "claude-opus-5-5", "usage": {...}}` with a raw
API usage object, or `"tokens": {"output": 1000, ...}`, or the token types as
flat keys. The output is JSON: points with a low and high, and the split by
model, token type and basis.

```python
from usage_weights import estimate, load_table
table = load_table("2026-11-20")
records = [{"model": "claude-fable-5-2", "tokens": {"output": 1_000_000}}]
e = estimate(records, table, meter="all_models")
print(e.points, e.low, e.high)
```

## Pin a table

Always name a table id (`load_table("2026-11-20")`); there is no "latest", so
your numbers do not move under you. Pin the release tag of this repo too.
Tables are never edited after release: a new measurement is a new dated file.

## Run the kit

`kit/blocks.py` drives the workload, `kit/normalize.py` turns its output into a
run folder, `kit/fit.py` and `kit/intervals.py` analyze it, and
`kit/build_table.py` writes the table. Warning: a full table costs real
allowance and API-priced dollars (the second run cost about $96 and moved the
all-models meter about 8 points). Read [docs/method.md](docs/method.md) first.
The fit needs `pip install ".[fit]"`. The committed runs are in `data/`.

## Limits

One account, one week, contexts under 200k tokens, Fable rows measured during
a launch offer, and several rows are stand-ins. See [docs/limits.md](docs/limits.md).

## License

Apache-2.0. See [LICENSE](LICENSE).

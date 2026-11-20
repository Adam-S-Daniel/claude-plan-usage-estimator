#!/usr/bin/env python3
"""Turn the workload driver's raw output into a normalized run folder.

Usage: normalize.py RAW_DIR OUT_DIR [--collector CSV --window START END]

RAW_DIR holds the driver's calls.jsonl and probes.jsonl. OUT_DIR gets
calls.jsonl (one row per model call) and readings.jsonl (one row per meter
reading). Every CLI reading is stamped at the call's start, because the CLI
reads the meter when a call starts. A collector CSV (header
ts_utc,meter_name,percent,resets_at) adds usage-endpoint readings inside the
window.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from usage_weights import TOKEN_TYPES, tokens_from_usage  # noqa: E402
from usage_weights.meter import CLI_METER_KEYS, percent_from_cli_fraction  # noqa: E402

COLLECTOR_METERS = {"seven_day": "all_models", "seven_day_fable": "fable"}


def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def normalize(raw_rows, collector_rows=(), window=None):
    """Return (calls, readings, skipped_count)."""
    calls, readings, skipped = [], [], 0
    for row in raw_rows:
        usage = row.get("usage")
        if not usage:
            skipped += 1
            continue
        tok = tokens_from_usage(usage)
        if "cache_write" in tok:
            raise ValueError(f"call {row.get('tag')!r}: cache writes of unknown lifetime; cannot normalize")
        calls.append({
            "tag": row["tag"],
            "model": row["model"],
            "started": row["started"],
            "ended": row["ended"],
            "tokens": {t: tok[t] for t in TOKEN_TYPES},
            "cost_usd": row.get("cost_usd"),
        })
        meters = (row.get("meters") or [{}])[0]
        for meter, key in CLI_METER_KEYS.items():
            if key in meters and meters[key] is not None:
                readings.append({"at": row["started"], "meter": meter, "reader": "cli",
                                 "percent": percent_from_cli_fraction(meters[key])})
    if window is not None:
        lo, hi = (parse_iso(w) for w in window)
        for r in collector_rows:
            meter = COLLECTOR_METERS.get(r["meter_name"])
            if meter and lo <= parse_iso(r["ts_utc"]) <= hi:
                readings.append({"at": r["ts_utc"], "meter": meter, "reader": "usage_endpoint",
                                 "percent": int(r["percent"])})
    calls.sort(key=lambda c: parse_iso(c["ended"]))
    readings.sort(key=lambda r: (parse_iso(r["at"]), r["meter"], r["reader"]))
    return calls, readings, skipped


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("out")
    ap.add_argument("--collector")
    ap.add_argument("--window", nargs=2, metavar=("START", "END"))
    args = ap.parse_args(argv)
    if bool(args.collector) != bool(args.window):
        ap.error("--collector and --window go together")
    raw = [r for f in ("calls.jsonl", "probes.jsonl") for r in read_jsonl(os.path.join(args.raw, f))]
    coll = []
    if args.collector:
        with open(args.collector, encoding="utf-8", newline="") as fh:
            coll = list(csv.DictReader(fh))
    try:
        calls, readings, skipped = normalize(raw, coll, args.window)
    except ValueError as e:
        sys.exit(f"normalize: {e}")
    os.makedirs(args.out, exist_ok=True)
    write_jsonl(os.path.join(args.out, "calls.jsonl"), calls)
    write_jsonl(os.path.join(args.out, "readings.jsonl"), readings)
    n_cli = sum(r["reader"] == "cli" for r in readings)
    print(f"{len(calls)} calls, {n_cli} cli readings, {len(readings) - n_cli} collector readings, "
          f"{skipped} rows skipped (no usage)")


if __name__ == "__main__":
    main()

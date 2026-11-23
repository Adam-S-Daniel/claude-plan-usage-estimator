#!/usr/bin/env python3
"""Tick-interval factors for a mixed workload (tokens of several types at once).

Usage: intervals.py RUN_DIR --prior atlas-2026-10-29 [--out RUN_DIR/intervals.json]

A tick is the first CLI all-models reading at a new value. Between two
consecutive ticks the meter moved exactly one point, so
factor = 1 / (prior points of the tokens charged in the interval): how many
times the prior the workload moved the meter. Tokens are charged at call end
("end") or spread evenly over each call's run ("linear").

With a guard.json in the run folder (written by blocks.py) each interval also
gets its evidence and a grade (clean, corrected, contaminated); see
kit/guard.py. Without one the run is graded by schedule only and the
intervals carry no grade.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from kit import guard  # noqa: E402
from usage_weights import TOKEN_TYPES, load_table  # noqa: E402
from usage_weights.records import resolve_model  # noqa: E402

MODES = ("end", "linear")


def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def find_ticks(readings):
    cli = sorted((r for r in readings if r["meter"] == "all_models" and r["reader"] == "cli"),
                 key=lambda r: parse_iso(r["at"]))
    ticks, prev = [], None
    for r in cli:
        if prev is not None and r["percent"] != prev["percent"]:
            ticks.append({"value": r["percent"], "at": r["at"], "previous_reading_at": prev["at"]})
        prev = r
    return ticks


def share(call, a, b, mode):
    t0, t1 = parse_iso(call["started"]), parse_iso(call["ended"])
    if mode == "end":
        return 1.0 if a <= t1 < b else 0.0
    span = (t1 - t0).total_seconds()
    if span <= 0:
        return 1.0 if a <= t0 < b else 0.0
    overlap = (min(b, t1) - max(a, t0)).total_seconds()
    return max(0.0, overlap) / span


def block_of(tag):
    """The block a call belongs to, or None: blocks.py tags calls `<block>-warm` or `<block>-w<worker>-<i>`."""
    m = re.fullmatch(r"(.+)-(?:warm|w\d+-\d+)", tag)
    return m.group(1) if m else None


def compute(calls, readings, prior):
    rows = prior["meters"]["all_models"]["models"]
    ticks = find_ticks(readings)
    intervals = []
    for i in range(len(ticks) - 1):
        a, b = parse_iso(ticks[i]["at"]), parse_iso(ticks[i + 1]["at"])
        for mode in MODES:
            by_model, by_type, tokens = {}, dict.fromkeys(TOKEN_TYPES, 0.0), {}
            usd, by_block = 0.0, {}
            for c in calls:
                s = share(c, a, b, mode)
                if not s:
                    continue
                key = resolve_model(c["model"], rows)
                if key is None:
                    raise ValueError(f"model {c['model']!r} is not in the prior table")
                usd += s * (c.get("cost_usd") or 0.0)
                for t in TOKEN_TYPES:
                    n = s * c["tokens"].get(t, 0)
                    if not n:
                        continue
                    p = n / 1e6 * rows[key][t]["point"]
                    by_model[key] = by_model.get(key, 0.0) + p
                    blk = block_of(c.get("tag", ""))
                    if blk:
                        by_block[blk] = by_block.get(blk, 0.0) + p
                    by_type[t] += p
                    tokens.setdefault(key, {}).setdefault(t, 0.0)
                    tokens[key][t] += n
            total = sum(by_type.values())
            intervals.append({
                "from": ticks[i]["value"], "to": ticks[i + 1]["value"],
                "start": ticks[i]["at"], "end": ticks[i + 1]["at"], "mode": mode,
                "prior_points": total, "factor": 1 / total if total else None,
                "api_usd": usd, "prior_points_by_model": by_model,
                "prior_points_by_type": by_type, "tokens": tokens,
            })
            if by_block:  # a run of one unit of work (no blocks.py tags) has none
                intervals[-1]["block"] = max(by_block, key=by_block.get)
    return ticks, intervals


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--prior", required=True)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    calls = read_jsonl(os.path.join(args.run, "calls.jsonl"))
    readings = read_jsonl(os.path.join(args.run, "readings.jsonl"))
    ticks, intervals = compute(calls, readings, load_table(args.prior))
    out = args.out or os.path.join(args.run, "intervals.json")
    doc = {"prior": args.prior, "ticks": ticks, "intervals": intervals}
    g = guard.load(args.run)
    if g is not None:
        doc["guard"], doc["intervals"] = g["guard"], guard.grade_intervals(intervals, g["evidence"])
        intervals = doc["intervals"]
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    for iv in intervals:
        f = iv["factor"]
        print(f"{iv['from']}->{iv['to']} {iv['mode']:6} prior points {iv['prior_points']:.4f} "
              f"factor {f if f is None else round(f, 4)}" + (f" grade {iv['grade']}" if "grade" in iv else ""))


if __name__ == "__main__":
    main()

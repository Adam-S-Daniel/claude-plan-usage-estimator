#!/usr/bin/env python3
"""Build the weight tables from the kit's outputs.

  build_table.py --atlas --out usage_weights/data/weights/atlas-2026-10-29.json
  build_table.py --id 2026-11-20 --night1 INTERVALS_JSON --night2 FIT_JSON --out TABLE_JSON
  build_table.py --readme README.md [--id 2026-11-20]

Deterministic: the same inputs give the same bytes.
"""
import argparse
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from kit import guard  # noqa: E402
from usage_weights import TOKEN_TYPES  # noqa: E402
from usage_weights.table import load_table  # noqa: E402

ATLAS_ID = "atlas-2026-10-29"
NIGHT1 = "2026-11-19-night1"
NIGHT2 = "2026-11-20-night2"
UNMEASURED = [0.5, 2.0]
JOINT_FLOOR = 0.03  # see the table note on joint ranges
OPUS, SONNET, HAIKU, FABLE = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"

# Atlas ADE "Plan usage burn data", model a5v3: millions of tokens that move the
# all-models weekly meter 1 point (cache_write is used for both write lifetimes).
ATLAS_TOKENS_PER_POINT = {
    OPUS: {"input": 3.0, "output": 0.66, "cache_read": 170.0, "cache_write": 4.6},
    SONNET: {"input": 5.0, "output": 1.1, "cache_read": 290.0, "cache_write": 7.7},
    FABLE: {"input": 0.7, "output": 0.16, "cache_read": 41.0, "cache_write": 1.1},
}
HAIKU_TOKENS_FACTOR = 5  # Haiku takes 5x the tokens per point of Opus (weight x 0.2)


def _row_type(t):
    return "cache_write" if t.startswith("cache_write") else t


def _weight(point, basis, source, note=None, low=None, high=None):
    if basis == "published":
        low = high = point
    elif basis == "assumed":
        low, high = (point * f for f in UNMEASURED)
    w = {"point": point, "low": low, "high": high, "basis": basis, "source": source}
    if note:
        w["note"] = note
    return w


def build_atlas():
    all_models, fable_meter = {}, {}
    for model, tpp in ATLAS_TOKENS_PER_POINT.items():
        all_models[model] = {t: _weight(1 / tpp[_row_type(t)], "published", ATLAS_ID) for t in TOKEN_TYPES}
    opus = ATLAS_TOKENS_PER_POINT[OPUS]
    all_models[HAIKU] = {
        t: _weight(1 / (opus[_row_type(t)] * HAIKU_TOKENS_FACTOR), "assumed", ATLAS_ID,
                   "Not in the published table: Opus weight x 0.2.")
        for t in TOKEN_TYPES
    }
    fable_meter[FABLE] = {
        t: _weight(2 * all_models[FABLE][t]["point"], "published", ATLAS_ID, "Twice the all-models weight (Fable is capped at half the week).")
        for t in TOKEN_TYPES
    }
    return {
        "schema": 1,
        "id": ATLAS_ID,
        "plan": "max_20x",
        "unit": "points_per_million_tokens",
        "unmeasured_factor": UNMEASURED,
        "sources": {
            ATLAS_ID: "Atlas ADE, Plan usage burn data, model a5v3, checked 2026-10-29 on one Ultra (formerly Max 20x) "
                      "account: https://atlas-ade.dev/docs/plan-usage-burn-data/",
            "she-llac": "Haiku to Opus ratio of 0.2: https://she-llac.com/claude-limits (2026-01-25)",
        },
        "notes": [
            "Weight = 1 / (millions of tokens that move the all-models weekly meter 1 point).",
            "Atlas has one cache write figure; it is used for both 5-minute and 1-hour writes.",
            "The Fable row is Atlas's Fable 5.1 estimate; Atlas says Fable 5.2 is unmeasured.",
            "Haiku is not in Atlas's table. It is set to Opus x 0.2 by weight (5x the tokens per point), "
            "the Haiku to Opus ratio from she-llac.com/claude-limits (2026-01-25).",
            "The fable meter is twice the all-models weight, because Atlas says Fable is capped at half the week.",
        ],
        "meters": {
            "all_models": {"cli_key": "seven_day", "models": all_models},
            "fable": {"cli_key": "seven_day_overage_included", "models": fable_meter},
        },
    }


def _joint(intervals, model, modes):
    """Factors of the intervals where only `model` had prior points."""
    out = []
    for iv in intervals:
        pm = {m for m, v in iv["prior_points_by_model"].items() if v > 0}
        if guard.usable(iv) and iv["mode"] in modes and pm == {model} and iv["factor"] is not None:
            out.append(iv)
    return out


def _fit_weight(solve, meter, name):
    key = f"{meter}/{name}"
    lo, hi = solve["range"][key]
    return solve["point"][key], lo, hi


def build_table(table_id, night1, night2):
    atlas = load_table(ATLAS_ID)
    P = {m: {t: atlas["meters"]["all_models"]["models"][m][t]["point"] for t in TOKEN_TYPES}
         for m in atlas["meters"]["all_models"]["models"]}
    solve = night2["solves"]["round"]
    if not solve["feasible"]:
        raise ValueError("night 2 'round' solve is infeasible")
    iv = night1["intervals"]

    def measured(meter, name, note):
        point, lo, hi = _fit_weight(solve, meter, name)
        return _weight(point, "measured", NIGHT2, note, lo, hi)

    def assumed(point, note):
        return _weight(point, "assumed", "assumption", note)

    launch = "measured during the Fable 5.2 launch offer"
    types_fit = {"output": "fable.output", "cache_read": "fable.cache_read",
                 "cache_write_5m": "fable.cache_write_5m", "cache_write_1h": "fable.cache_write_1h"}

    # --- all_models
    fable_all = {t: measured("all_models", n, launch) for t, n in types_fit.items()}
    fable_all["input"] = assumed(P[FABLE]["input"], "Not measured: uncached input was under 1,500 tokens all night. Stand-in is the prior's weight.")

    r_pt, r_lo, r_hi = _fit_weight(solve, "all_models", "opus.cache_read")
    r_of = lambda r: r / P[OPUS]["cache_read"]  # noqa: E731

    def joint_f(interval, r):
        k = interval["factor"]
        s = interval["prior_points_by_type"].get("cache_read", 0.0) / interval["prior_points"]
        return (k - s * r) / (1 - s)

    opus_iv = _joint(iv, OPUS, ("end", "linear"))
    opus_end = _joint(iv, OPUS, ("end",))
    if not opus_end:
        raise ValueError("no pure-Opus night-1 intervals")
    f_point = statistics.fmean(joint_f(i, r_of(r_pt)) for i in opus_end)
    f_all = [joint_f(i, r_of(r)) for i in opus_iv for r in (r_lo, r_pt, r_hi)]
    f_low, f_high = min(f_all + [f_point * (1 - JOINT_FLOOR)]), max(f_all + [f_point * (1 + JOINT_FLOOR)])
    joint_note = "Output and cache writes were measured together (about 1 part output to 2 parts cache write by weight)."
    opus = {"cache_read": measured("all_models", "opus.cache_read", "Rests on one meter tick, so the range is wide.")}
    for t in ("output", "cache_write_5m"):
        opus[t] = {"point": f_point * P[OPUS][t], "low": f_low * P[OPUS][t], "high": f_high * P[OPUS][t],
                   "basis": "joint", "source": NIGHT1, "note": joint_note}
    opus["cache_write_1h"] = assumed(opus["cache_write_5m"]["point"],
                                     "On Fable a 1-hour write measured about the same per token as a 5-minute write.")
    opus["input"] = assumed(P[OPUS]["input"], "Not measured. Stand-in is the prior's weight.")

    s_iv = _joint(iv, SONNET, ("end", "linear"))
    s_end = _joint(iv, SONNET, ("end",))
    if not s_end:
        raise ValueError("no pure-Sonnet night-1 intervals")
    k_point = statistics.fmean(i["factor"] for i in s_end)
    k_all = [i["factor"] for i in s_iv]
    k_low, k_high = min(k_all + [k_point * (1 - JOINT_FLOOR)]), max(k_all + [k_point * (1 + JOINT_FLOOR)])
    sonnet = {}
    for t in ("output", "cache_write_5m"):
        sonnet[t] = {"point": k_point * P[SONNET][t], "low": k_low * P[SONNET][t], "high": k_high * P[SONNET][t],
                     "basis": "joint", "source": NIGHT1, "note": joint_note}
    sonnet["cache_read"] = assumed(k_point * P[SONNET]["cache_read"],
                                   "Stand-in is the mix factor times the prior. Cache reads were 7% of the measured mix, "
                                   "and Opus's cache-read weight was not predicted by its mix factor.")
    sonnet["cache_write_1h"] = assumed(sonnet["cache_write_5m"]["point"],
                                       "On Fable a 1-hour write measured about the same per token as a 5-minute write.")
    sonnet["input"] = assumed(P[SONNET]["input"], "Not measured. Stand-in is the prior's weight.")

    haiku = {t: assumed(P[HAIKU][t], "Not measured. Stand-in is the prior's Haiku row.") for t in TOKEN_TYPES}

    # --- fable meter
    fable_meter = {t: measured("fable", n, launch) for t, n in types_fit.items()}
    fable_meter["input"] = assumed(2 * fable_all["input"]["point"], "Not measured. Stand-in is twice the all-models input weight.")

    def order(row):
        return {t: row[t] for t in TOKEN_TYPES}

    return {
        "schema": 1,
        "id": table_id,
        "plan": "max_20x",
        "unit": "points_per_million_tokens",
        "unmeasured_factor": UNMEASURED,
        "sources": {
            NIGHT1: "79 Opus and 106 Sonnet runs of real agent workloads (742 calls, all 5-minute cache writes), "
                    "analyzed by meter ticks with kit/intervals.py",
            NIGHT2: "Synthetic blocks, one dominant token type each (755 calls), fitted by linear program with kit/fit.py",
            ATLAS_ID: "Atlas ADE, Plan usage burn data, model a5v3: https://atlas-ade.dev/docs/plan-usage-burn-data/",
        },
        "notes": [
            "One account, on Max 20x (renamed Ultra).",
            "Contexts under 200k tokens only.",
            "Fable rows were measured during the Fable 5.2 launch offer; re-measure after it ends.",
            "Usage reaches the meter when a call ends.",
            "A joint range is never narrower than 3% either side of its point: two intervals of the same Opus "
            "mix gave factors 2.3% either side of their mean, and the Sonnet factor rests on one interval.",
            "The directly measured weights landed between 0.53 and 1.79 times the Atlas prior, "
            "which is where the unmeasured_factor [0.5, 2.0] comes from.",
            "basis: measured = its own weight was fitted; joint = measured only together with other types in one "
            "workload mix (low and high are the range of the shared factor); assumed = a stand-in, low and high are "
            "point x unmeasured_factor; published = copied from an outside table.",
        ],
        "meters": {
            "all_models": {"cli_key": "seven_day", "models": {
                FABLE: order(fable_all), OPUS: order(opus), SONNET: order(sonnet), HAIKU: order(haiku)}},
            "fable": {"cli_key": "seven_day_overage_included", "models": {FABLE: order(fable_meter)}},
        },
    }


def dumps(obj):
    return json.dumps(obj, indent=2) + "\n"


# --- README block

BEGIN, END = "<!-- weights:begin -->", "<!-- weights:end -->"


def mtok(w):
    """Millions of tokens per point: 1 / weight, formatted to 3 significant digits."""
    s = f"{1 / w:.3g}"
    return f"{float(s):.0f}" if "e" in s else s


def cell(w):
    return f"{mtok(w['point'])} ({mtok(w['high'])} to {mtok(w['low'])})"


def readme_block(table):
    lines = [BEGIN, ""]
    for meter, title in (("all_models", "All-models weekly meter"), ("fable", "Fable weekly meter")):
        lines += [f"{title}: millions of tokens per point (range), table `{table['id']}`.", "",
                  "| Model | Token type | M tokens per point | Basis |", "| --- | --- | --- | --- |"]
        for model, row in table["meters"][meter]["models"].items():
            for t in TOKEN_TYPES:
                lines.append(f"| {model} | {t} | {cell(row[t])} | {row[t]['basis']} |")
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def rewrite_readme(path, table):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    pat = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if not pat.search(text):
        raise ValueError(f"{path} has no {BEGIN} ... {END} block")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(pat.sub(lambda _: readme_block(table), text, count=1))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", action="store_true")
    ap.add_argument("--id")
    ap.add_argument("--night1")
    ap.add_argument("--night2")
    ap.add_argument("--out")
    ap.add_argument("--readme")
    args = ap.parse_args(argv)
    if args.readme:
        rewrite_readme(args.readme, load_table(args.id or "2026-11-20"))
        return
    if not args.out:
        ap.error("--out is required")
    if args.atlas:
        text = dumps(build_atlas())
    else:
        if not (args.id and args.night1 and args.night2):
            ap.error("--id, --night1 and --night2 are required")
        with open(args.night1, encoding="utf-8") as f1, open(args.night2, encoding="utf-8") as f2:
            text = dumps(build_table(args.id, json.load(f1), json.load(f2)))
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fit per-token-type meter weights to one normalized run (linear programs).

Usage: fit.py RUN_DIR [--spec RUN_DIR/fit-spec.json] [--out RUN_DIR/fit.json]

Every meter reading is an inequality (see usage_weights.reading_bounds). The
meter value at time t is
  V(t) = offset + sum(weight x millions of tokens charged by t) + fixed points
and each reading with bounds [lo, hi) gives two rows:
  lo + d <= V   and   V <= hi - 1e-6 - d
d is a margin; it may go down to -tolerance. The point estimate maximizes d;
each variable's range is its min and max with d fixed at -tolerance.
Needs numpy and scipy.
"""
import argparse
import datetime as dt
import json
import os
import sys

import numpy as np
from scipy.optimize import linprog

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from kit import guard  # noqa: E402
from usage_weights import TOKEN_TYPES, load_table, reading_bounds  # noqa: E402
from usage_weights.records import resolve_model  # noqa: E402

METERS = ("all_models", "fable")
ONE_MICRO = 1e-6


def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def prior_weight(prior, meter, model, ttype):
    rows = prior["meters"].get(meter, {}).get("models", {})
    key = resolve_model(model, rows)
    return None if key is None else rows[key][ttype]["point"]


def build_calls(calls, readings, spec, prior):
    """Per call and meter: millions of tokens for each free variable, plus fixed points."""
    meters = [m for m in METERS if any(r["meter"] == m for r in readings)]
    free = [(m, e) for m in meters for e in spec["free"] if m in e["meters"]]  # (meter, entry), meter-major
    names = [f"{m}/{e['name']}" for m, e in free]
    ignore = {(i["model"], t) for i in spec.get("ignore", []) for t in i["types"]}
    out = []
    for c in calls:
        var = np.zeros(len(free))
        fixed = dict.fromkeys(meters, 0.0)
        for t in TOKEN_TYPES:
            n = c["tokens"].get(t, 0)
            if not n or (c["model"], t) in ignore:
                continue
            for m in meters:
                hits = [j for j, (fm, e) in enumerate(free) if fm == m and e["model"] == c["model"] and t in e["types"]]
                fx = [e for e in spec.get("fixed", []) if e["model"] == c["model"] and t in e["types"] and m in e["meters"]]
                if len(hits) + len(fx) > 1:
                    raise ValueError(f"{c['model']}/{t} on {m} is covered more than once by the spec")
                if hits:
                    var[hits[0]] += n / 1e6
                elif fx:
                    e = fx[0]
                    priced = e.get("price_as", {}).get(t, t)
                    w = prior_weight(prior, m, c["model"], priced)
                    if w is None:
                        raise ValueError(f"fixed entry for {c['model']} on {m}: the prior has no row")
                    fixed[m] += e["factor"] * w * n / 1e6
                elif prior_weight(prior, m, c["model"], t) is not None:
                    raise ValueError(f"{c['model']}/{t} on meter {m} has tokens but is neither free, fixed nor ignored")
        out.append({"t0": parse_iso(c["started"]), "t1": parse_iso(c["ended"]), "var": var, "fixed": fixed})
    return meters, free, names, out


def charge_matrix(calls, times, mode):
    """S[i, j]: share of call j charged by reading time i."""
    t0 = np.array([c["t0"].timestamp() for c in calls])
    t1 = np.array([c["t1"].timestamp() for c in calls])
    t = np.array([x.timestamp() for x in times])[:, None]
    if mode == "end":
        return (t1[None, :] <= t).astype(float)
    if mode == "start":
        return (t0[None, :] < t).astype(float)
    if mode == "linear":
        span = t1 - t0
        safe = np.where(span > 0, span, 1.0)
        lin = np.clip((t - t0[None, :]) / safe[None, :], 0.0, 1.0)
        return np.where(span[None, :] > 0, lin, (t1[None, :] <= t).astype(float))
    raise ValueError(f"unknown charge mode {mode!r}")


def solve(meters, names, free, calls, obs, mode, tol):
    """obs: list of (meter, time, lo, hi). Returns the solve result dict."""
    n_off, n_free = len(meters), len(names)
    cols = [f"offset.{m}" for m in meters] + names
    nv = len(cols) + 1  # + margin d
    if not obs:
        return {"feasible": False, "readings": 0}
    S = charge_matrix(calls, [o[1] for o in obs], mode)
    V = np.array([c["var"] for c in calls]).reshape(len(calls), n_free)
    F = {m: np.array([c["fixed"][m] for c in calls]) for m in meters}
    A, b = [], []
    for i, (m, _t, lo, hi) in enumerate(obs):
        row = np.zeros(nv)
        row[meters.index(m)] = 1
        mask = np.array([fm == m for fm, _e in free], dtype=bool)
        x = S[i].dot(V) if n_free else np.zeros(0)
        row[n_off:n_off + n_free] = np.where(mask, x, 0.0)
        fx = float(S[i].dot(F[m]))
        r1 = -row
        r1[-1] = 1
        A.append(r1)
        b.append(fx - lo)
        r2 = row.copy()
        r2[-1] = 1
        A.append(r2)
        b.append(hi - ONE_MICRO - fx)
    A, b = np.array(A), np.array(b)
    bounds = [(None, None)] * n_off + [(0, None)] * n_free + [(-tol, None)]

    def lp(cvec, fix_d=None):
        bd = list(bounds)
        if fix_d is not None:
            bd[-1] = (fix_d, fix_d)
        return linprog(cvec, A_ub=A, b_ub=b, bounds=bd, method="highs")

    c = np.zeros(nv)
    c[-1] = -1
    best = lp(c)
    if best.status != 0:
        return {"feasible": False, "readings": len(obs)}
    res = {"feasible": True, "readings": len(obs), "margin": float(best.x[-1]),
           "point": {n: float(best.x[i]) for i, n in enumerate(cols)}, "range": {}}
    for i, n in enumerate(cols):
        cc = np.zeros(nv)
        cc[i] = 1
        lo_, hi_ = lp(cc, -tol), lp(-cc, -tol)
        res["range"][n] = [float(lo_.x[i]) if lo_.status == 0 else None,
                           float(hi_.x[i]) if hi_.status == 0 else None]
    return res


def fit_run(run_dir, spec=None):
    """Fit one run folder; returns the fit.json dict."""
    if spec is None:
        with open(os.path.join(run_dir, "fit-spec.json"), encoding="utf-8") as fh:
            spec = json.load(fh)
    g = guard.load(run_dir)
    bad = [e for e in (g or {}).get("evidence", []) if not e.get("correction")]
    if bad:
        raise ValueError(f"{len(bad)} uncorrected outside-use evidence item(s) in guard.json (first: {bad[0]['kind']} "
                         f"at {bad[0]['at']}); a contaminated run is not fitted. Record a correction or trim the run")
    calls = read_jsonl(os.path.join(run_dir, "calls.jsonl"))
    readings = read_jsonl(os.path.join(run_dir, "readings.jsonl"))
    prior = load_table(spec["prior"])
    meters, free, names, cc = build_calls(calls, readings, spec, prior)
    tol, mode = spec["tolerance"], spec["charge"]
    cli = [(r["meter"], parse_iso(r["at"]), *reading_bounds(r["percent"], "cli"))
           for r in readings if r["reader"] == "cli"]
    coll = [r for r in readings if r["reader"] == "usage_endpoint"]

    def with_coll(reader):
        return cli + [(r["meter"], parse_iso(r["at"]), *reading_bounds(r["percent"], reader)) for r in coll]

    solves = {
        "round": solve(meters, names, free, cc, with_coll("usage_endpoint"), mode, tol),
        "trunc": solve(meters, names, free, cc, with_coll("cli"), mode, tol),
        "none": solve(meters, names, free, cc, cli, mode, tol),
    }
    return {"run": os.path.basename(os.path.normpath(run_dir)), "spec": spec, "n_calls": len(calls),
            "n_readings": {"cli": len(cli), "usage_endpoint": len(coll)}, "solves": solves}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--spec")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    spec = None
    if args.spec:
        with open(args.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
    result = fit_run(args.run, spec)
    out = args.out or os.path.join(args.run, "fit.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
        fh.write("\n")
    for name, s in result["solves"].items():
        if not s["feasible"]:
            print(f"{name}: infeasible ({s['readings']} readings)")
        else:
            print(f"{name}: feasible, {s['readings']} readings, margin {s['margin']:+.4f}")


if __name__ == "__main__":
    main()

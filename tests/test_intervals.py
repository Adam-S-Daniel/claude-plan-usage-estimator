import json
import math
import os

from kit import intervals as iv
from usage_weights import load_table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NIGHT1 = os.path.join(ROOT, "data", "2026-11-19-night1")


def _run():
    calls = iv.read_jsonl(os.path.join(NIGHT1, "calls.jsonl"))
    readings = iv.read_jsonl(os.path.join(NIGHT1, "readings.jsonl"))
    return iv.compute(calls, readings, load_table("atlas-2026-10-29"))


def _get(intervals, a, b, mode):
    return next(i for i in intervals if (i["from"], i["to"], i["mode"]) == (a, b, mode))


def test_night1_regression():
    _ticks, intervals = _run()
    e, lin = _get(intervals, 38, 39, "end"), _get(intervals, 38, 39, "linear")
    assert math.isclose(e["prior_points"], 0.8438536957219253, rel_tol=1e-9)
    assert math.isclose(e["factor"], 1.1850395454445335, rel_tol=1e-9)
    assert math.isclose(lin["prior_points"], 0.8209210097261087, rel_tol=1e-9)
    assert math.isclose(lin["factor"], 1.2181439969889905, rel_tol=1e-9)
    assert math.isclose(_get(intervals, 41, 42, "end")["factor"], 1.4723, abs_tol=1e-3)
    assert math.isclose(_get(intervals, 41, 42, "linear")["factor"], 1.4766, abs_tol=1e-3)


def test_interval_shape():
    ticks, intervals = _run()
    assert [t["value"] for t in ticks] == [38, 39, 40, 41, 42]
    i = _get(intervals, 38, 39, "end")
    assert set(i) == {"from", "to", "start", "end", "mode", "prior_points", "factor", "api_usd",
                      "prior_points_by_model", "prior_points_by_type", "tokens"}
    assert math.isclose(sum(i["prior_points_by_model"].values()), i["prior_points"])
    assert math.isclose(sum(i["prior_points_by_type"].values()), i["prior_points"])
    assert math.isclose(i["factor"] * i["prior_points"], 1.0)


def test_ticks_from_synthetic_readings():
    r = [{"at": f"2026-01-01T00:00:0{n}Z", "meter": "all_models", "reader": "cli", "percent": p}
         for n, p in enumerate([5, 5, 6, 6, 7])]
    r.append({"at": "2026-01-01T00:00:09Z", "meter": "fable", "reader": "cli", "percent": 50})
    ticks = iv.find_ticks(r)
    assert [(t["value"], t["at"], t["previous_reading_at"]) for t in ticks] == [
        (6, "2026-01-01T00:00:02Z", "2026-01-01T00:00:01Z"), (7, "2026-01-01T00:00:04Z", "2026-01-01T00:00:03Z")]


def test_linear_spreads_a_call_over_its_run():
    prior = load_table("atlas-2026-10-29")
    calls = [{"tag": "x", "model": "claude-opus-5-5", "started": "2026-01-01T00:00:00Z", "ended": "2026-01-01T00:00:10Z",
              "tokens": {"input": 0, "output": 1_000_000, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0},
              "cost_usd": 1.0}]
    readings = [{"at": f"2026-01-01T00:00:{s:02d}Z", "meter": "all_models", "reader": "cli", "percent": p}
                for s, p in [(1, 1), (2, 2), (6, 3)]]
    _t, ivs = iv.compute(calls, readings, prior)
    w = prior["meters"]["all_models"]["models"]["claude-opus-5-5"]["output"]["point"]
    lin = _get(ivs, 2, 3, "linear")
    assert math.isclose(lin["prior_points"], 0.4 * w)
    assert math.isclose(lin["api_usd"], 0.4)
    assert _get(ivs, 2, 3, "end")["prior_points"] == 0.0 and _get(ivs, 2, 3, "end")["factor"] is None


def test_committed_intervals_file_matches():
    committed = json.load(open(os.path.join(NIGHT1, "intervals.json")))
    _t, fresh = _run()
    assert json.loads(json.dumps(fresh)) == committed["intervals"]

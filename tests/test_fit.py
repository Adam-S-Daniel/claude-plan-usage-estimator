import copy
import json
import math
import os

import pytest

pytest.importorskip("scipy")
from kit import fit  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NIGHT2 = os.path.join(ROOT, "data", "2026-11-20-night2")
REL = 1e-6


_CACHE = {}


def _fit_night2():
    if 'r' not in _CACHE:
        _CACHE['r'] = fit.fit_run(NIGHT2)
    return _CACHE['r']


def close(a, b):
    return math.isclose(a, b, rel_tol=REL)


def test_counts():
    result = _fit_night2()
    assert result["n_calls"] == 755
    assert result["n_readings"] == {"cli": 999, "usage_endpoint": 24}
    assert result["solves"]["round"]["feasible"] and result["solves"]["round"]["readings"] == 1023
    assert result["solves"]["none"]["feasible"] and result["solves"]["none"]["readings"] == 999
    assert result["solves"]["trunc"]["feasible"] is False  # the collector truncating fits no weights


def test_regression_point_values():
    result = _fit_night2()
    p = result["solves"]["round"]["point"]
    target = {
        "all_models/fable.output": 3.296613360515793,
        "all_models/fable.cache_read": 0.025066465655035144,
        "all_models/fable.cache_write_5m": 1.0858893106871552,
        "all_models/fable.cache_write_1h": 0.8243968749742353,
        "all_models/opus.cache_read": 0.010554483594133197,
        "offset.all_models": 63.37704638568175,
        "fable/fable.output": 7.033705112896351,
        "fable/fable.cache_read": 0.04068229630487029,
        "fable/fable.cache_write_5m": 1.6432110568557048,
        "fable/fable.cache_write_1h": 1.6574029862733146,
        "offset.fable": 51.75108953487461,
    }
    for k, v in target.items():
        assert close(p[k], v), (k, p[k], v)


def test_regression_ranges():
    result = _fit_night2()
    r = result["solves"]["round"]["range"]
    assert all(close(a, b) for a, b in zip(r["all_models/fable.output"], [3.142556551092912, 3.735025157562099]))
    assert all(close(a, b) for a, b in zip(r["all_models/opus.cache_read"], [0.0071541691971569125, 0.011368229528928757]))
    assert close(result["solves"]["round"]["margin"], -0.009181351962027406)


def test_other_charge_times_are_infeasible():
    for mode in ["start", "linear"]:
        spec = copy.deepcopy(fit.json.load(open(os.path.join(NIGHT2, "fit-spec.json"))))
        spec["charge"] = mode
        assert fit.fit_run(NIGHT2, spec)["solves"]["round"]["feasible"] is False


def test_committed_fit_json_matches_a_fresh_fit():
    result = _fit_night2()
    committed = json.load(open(os.path.join(NIGHT2, "fit.json")))
    assert close(committed["solves"]["round"]["point"]["all_models/fable.output"],
                 result["solves"]["round"]["point"]["all_models/fable.output"])


# ---- synthetic runs

FABLE = "claude-fable-5-2"
SPEC = {"prior": "atlas-2026-10-29", "charge": "end", "tolerance": 0.03,
        "free": [{"name": "fable.output", "model": FABLE, "types": ["output"], "meters": ["all_models"]}],
        "fixed": []}


def _iso(sec):
    return f"2026-01-01T{sec // 3600:02d}:{sec // 60 % 60:02d}:{sec % 60:02d}Z"


def _write(tmp_path, calls, readings):
    (tmp_path / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
    (tmp_path / "readings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in readings))
    return str(tmp_path)


def _synthetic(true_w, offset):
    calls, readings, total = [], [], 0.0
    for i in range(60):
        out = 150_000 + 37_000 * (i % 7)
        t0 = i * 60
        calls.append({"tag": "c", "model": FABLE, "started": _iso(t0), "ended": _iso(t0 + 30),
                      "tokens": {"input": 0, "output": out, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0},
                      "cost_usd": 0.0})
        # the reading is taken at the start: it sees every earlier call, charged at its end
        readings.append({"at": _iso(t0), "meter": "all_models", "reader": "cli",
                         "percent": math.floor(offset + true_w * total)})
        total += out / 1e6
    return calls, readings


def test_synthetic_truth_is_inside_fitted_ranges(tmp_path):
    true_w, offset = 3.1, 10.37
    calls, readings = _synthetic(true_w, offset)
    spec = copy.deepcopy(SPEC)
    (tmp_path / "fit-spec.json").write_text(json.dumps(spec))
    res = fit.fit_run(_write(tmp_path, calls, readings))["solves"]["none"]
    assert res["feasible"]
    lo, hi = res["range"]["all_models/fable.output"]
    assert lo <= true_w <= hi
    lo, hi = res["range"]["offset.all_models"]
    assert lo <= offset <= hi
    assert hi - lo < 1.0


def test_synthetic_contradiction_is_infeasible(tmp_path):
    calls, readings = _synthetic(3.1, 10.37)
    readings.append({"at": _iso(0), "meter": "all_models", "reader": "cli", "percent": 99})
    (tmp_path / "fit-spec.json").write_text(json.dumps(SPEC))
    res = fit.fit_run(_write(tmp_path, calls, readings))["solves"]["none"]
    assert res == {"feasible": False, "readings": len(readings)}


def test_uncovered_tokens_fail_naming_them(tmp_path):
    calls, readings = _synthetic(3.1, 10.37)
    calls[3]["tokens"]["cache_read"] = 1000
    (tmp_path / "fit-spec.json").write_text(json.dumps(SPEC))
    with pytest.raises(ValueError, match=f"{FABLE}/cache_read"):
        fit.fit_run(_write(tmp_path, calls, readings))


def test_ignore_and_fixed_cover_tokens(tmp_path):
    calls, readings = _synthetic(3.1, 10.37)
    calls[3]["tokens"]["cache_read"] = 1000
    spec = copy.deepcopy(SPEC)
    spec["ignore"] = [{"model": FABLE, "types": ["cache_read"]}]
    (tmp_path / "fit-spec.json").write_text(json.dumps(spec))
    assert fit.fit_run(_write(tmp_path, calls, readings))["solves"]["none"]["feasible"]
    spec["ignore"] = []
    spec["fixed"] = [{"model": FABLE, "types": ["cache_read"], "meters": ["all_models"], "factor": 1.0}]
    (tmp_path / "fit-spec.json").write_text(json.dumps(spec))
    assert fit.fit_run(_write(tmp_path, calls, readings))["solves"]["none"]["feasible"]


def test_models_without_a_row_on_the_fable_meter_are_not_counted():
    from usage_weights import load_table
    prior = load_table("atlas-2026-10-29")
    assert fit.prior_weight(prior, "fable", "claude-opus-5-5", "output") is None

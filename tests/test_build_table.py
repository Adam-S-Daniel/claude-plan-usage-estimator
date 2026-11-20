import json
import math
import os

import pytest

from kit import build_table as bt
from usage_weights import load_table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = os.path.join(ROOT, "usage_weights", "data", "weights")


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


def test_atlas_rebuilds_byte_for_byte():
    assert bt.dumps(bt.build_atlas()) == _read("usage_weights", "data", "weights", "atlas-2026-10-29.json")


def test_2026_11_20_rebuilds_byte_for_byte():
    n1 = json.loads(_read("data", "2026-11-19-night1", "intervals.json"))
    n2 = json.loads(_read("data", "2026-11-20-night2", "fit.json"))
    assert bt.dumps(bt.build_table("2026-11-20", n1, n2)) == _read("usage_weights", "data", "weights", "2026-11-20.json")


def test_rules():
    t = load_table("2026-11-20")
    a = load_table("atlas-2026-10-29")["meters"]["all_models"]["models"]
    m = t["meters"]["all_models"]["models"]
    n2 = json.loads(_read("data", "2026-11-20-night2", "fit.json"))["solves"]["round"]
    assert t["plan"] == "max_20x" and t["unmeasured_factor"] == [0.5, 2.0]
    assert set(t["meters"]["fable"]["models"]) == {"claude-fable-5-2"}
    # Fable measured rows are the night-2 round solve
    out = m["claude-fable-5-2"]["output"]
    assert out["basis"] == "measured" and out["source"] == "2026-11-20-night2"
    assert out["point"] == n2["point"]["all_models/fable.output"]
    assert [out["low"], out["high"]] == n2["range"]["all_models/fable.output"]
    assert m["claude-fable-5-2"]["input"]["basis"] == "assumed"
    assert m["claude-fable-5-2"]["input"]["point"] == a["claude-fable-5-2"]["input"]["point"]
    # Opus: joint output and cache write share one factor; the 1h write and input are stand-ins
    o = m["claude-opus-5-5"]
    assert o["cache_read"]["basis"] == "measured"
    assert o["output"]["basis"] == o["cache_write_5m"]["basis"] == "joint"
    f = o["output"]["point"] / a["claude-opus-5-5"]["output"]["point"]
    assert math.isclose(f, o["cache_write_5m"]["point"] / a["claude-opus-5-5"]["cache_write_5m"]["point"])
    assert o["output"]["low"] < o["output"]["point"] < o["output"]["high"]
    assert o["cache_write_1h"]["basis"] == "assumed" and o["cache_write_1h"]["point"] == o["cache_write_5m"]["point"]
    assert o["input"]["point"] == a["claude-opus-5-5"]["input"]["point"]
    # Sonnet: cache read is the mix factor times the prior
    s = m["claude-sonnet-5-5"]
    k = s["output"]["point"] / a["claude-sonnet-5-5"]["output"]["point"]
    assert s["cache_read"]["basis"] == "assumed"
    assert math.isclose(s["cache_read"]["point"], k * a["claude-sonnet-5-5"]["cache_read"]["point"])
    # Haiku equals the prior everywhere
    for ty, w in m["claude-haiku-5-5"].items():
        assert w["basis"] == "assumed" and w["point"] == a["claude-haiku-5-5"][ty]["point"]
    # Fable meter
    fm = t["meters"]["fable"]["models"]["claude-fable-5-2"]
    assert fm["output"]["point"] == n2["point"]["fable/fable.output"]
    assert fm["input"]["point"] == 2 * out_input(m)
    notes = " ".join(t["notes"])
    for needle in ("Max 20x", "200k", "launch offer", "meter when a call ends", "0.53", "1.79"):
        assert needle in notes


def out_input(m):
    return m["claude-fable-5-2"]["input"]["point"]


def _iv(mode, factor, by_model, cr_share):
    return {"mode": mode, "factor": factor, "prior_points": 1 / factor,
            "prior_points_by_model": by_model, "prior_points_by_type": {"cache_read": cr_share / factor}}


def test_joint_factor_formula_on_synthetic_intervals():
    # s_cr = 0.1, r = 2: f = (k - s_cr * r) / (1 - s_cr)
    iv = {"intervals": [
        _iv("end", 1.4, {"claude-opus-5-5": 1.0}, 0.1),
        _iv("end", 1.2, {"claude-opus-5-5": 1.0}, 0.1),
        _iv("linear", 1.6, {"claude-opus-5-5": 1.0}, 0.1),
        _iv("end", 9.0, {"claude-opus-5-5": 0.5, "claude-sonnet-5-5": 0.5}, 0.1),  # mixed: ignored
        _iv("end", 1.5, {"claude-sonnet-5-5": 1.0}, 0.0),
        _iv("linear", 1.7, {"claude-sonnet-5-5": 1.0}, 0.0),
    ]}
    atlas = load_table("atlas-2026-10-29")
    r_point = 2 * atlas["meters"]["all_models"]["models"]["claude-opus-5-5"]["cache_read"]["point"]
    fit = {"solves": {"round": {"feasible": True,
        "point": {"all_models/opus.cache_read": r_point, "fable/fable.output": 7.0},
        "range": {"all_models/opus.cache_read": [r_point * 0.5, r_point * 1.5]}}}}
    for n in ("fable.output", "fable.cache_read", "fable.cache_write_5m", "fable.cache_write_1h"):
        for meter in ("all_models", "fable"):
            fit["solves"]["round"]["point"][f"{meter}/{n}"] = 1.0
            fit["solves"]["round"]["range"][f"{meter}/{n}"] = [0.5, 2.0]
    t = bt.build_table("x", iv, fit)
    P = atlas["meters"]["all_models"]["models"]["claude-opus-5-5"]["output"]["point"]
    o = t["meters"]["all_models"]["models"]["claude-opus-5-5"]["output"]
    f = lambda k, r: (k - 0.1 * r) / 0.9  # noqa: E731
    assert math.isclose(o["point"], P * (f(1.4, 2) + f(1.2, 2)) / 2)
    assert math.isclose(o["low"], P * min(f(k, r) for k in (1.2, 1.4, 1.6) for r in (1, 2, 3)))
    assert math.isclose(o["high"], P * max(f(k, r) for k in (1.2, 1.4, 1.6) for r in (1, 2, 3)))
    s = t["meters"]["all_models"]["models"]["claude-sonnet-5-5"]["output"]
    S = atlas["meters"]["all_models"]["models"]["claude-sonnet-5-5"]["output"]["point"]
    # The data's own low equals the point, so the 3% floor on a joint range sets it; the high is the data's.
    assert math.isclose(s["point"], S * 1.5) and math.isclose(s["low"], S * 1.5 * 0.97) and math.isclose(s["high"], S * 1.7)


def test_readme_rewrite(tmp_path):
    p = tmp_path / "R.md"
    p.write_text(f"intro\n{bt.BEGIN}\nold\n{bt.END}\noutro\n")
    bt.rewrite_readme(str(p), load_table("2026-11-20"))
    text = p.read_text()
    assert text.startswith("intro\n") and text.endswith("outro\n") and "old" not in text
    assert "| claude-fable-5-2 | output |" in text
    p.write_text("no markers")
    with pytest.raises(ValueError):
        bt.rewrite_readme(str(p), load_table("2026-11-20"))

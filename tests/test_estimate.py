import copy
import json
import os
import subprocess
import sys

import pytest

from usage_weights import api_usd, estimate, load_table
from usage_weights.table import load_prices

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = load_table("2026-11-20")
FABLE, OPUS = "claude-fable-5-2", "claude-opus-5-5"


def test_worked_example_one_million_fable_output_tokens():
    w = T["meters"]["all_models"]["models"][FABLE]["output"]
    e = estimate([{"model": FABLE, "tokens": {"output": 1_000_000}}], T)
    assert e.points == w["point"] and e.low == w["low"] and e.high == w["high"]
    assert e.by_type == {"output": w["point"]} and e.by_basis == {"measured": w["point"]}
    assert e.tokens == {"output": 1_000_000} and e.table == "2026-11-20"


def test_hand_arithmetic_two_types():
    t = copy.deepcopy(T)
    row = t["meters"]["all_models"]["models"][OPUS]
    row["output"].update(point=2.0, low=1.0, high=3.0)
    row["cache_read"].update(point=0.01, low=0.005, high=0.02)
    e = estimate([{"model": OPUS, "output": 500_000, "cache_read": 100_000_000}], t)
    assert e.points == pytest.approx(0.5 * 2.0 + 100 * 0.01)
    assert e.low == pytest.approx(0.5 * 1.0 + 100 * 0.005)
    assert e.high == pytest.approx(0.5 * 3.0 + 100 * 0.02)


def test_low_point_high_ordering_and_additivity():
    recs = [{"model": FABLE, "usage": {"input_tokens": 10, "output_tokens": 7_000, "cache_read_input_tokens": 5_000_000,
                                       "cache_creation_input_tokens": 90_000,
                                       "cache_creation": {"ephemeral_5m_input_tokens": 50_000, "ephemeral_1h_input_tokens": 40_000}}},
            {"model": OPUS, "tokens": {"output": 3_000, "cache_write_5m": 400_000}},
            {"model": "claude-sonnet-5-5", "output": 12_345}]
    whole = estimate(recs, T)
    assert whole.low <= whole.points <= whole.high
    parts = [estimate([r], T) for r in recs]
    assert whole.points == pytest.approx(sum(p.points for p in parts))
    assert whole.low == pytest.approx(sum(p.low for p in parts))
    assert whole.high == pytest.approx(sum(p.high for p in parts))
    assert sum(whole.by_model[m]["points"] for m in whole.by_model) == pytest.approx(whole.points)
    assert sum(whole.by_basis.values()) == pytest.approx(whole.points)


def test_opus_counts_zero_on_fable_meter():
    e = estimate([{"model": OPUS, "output": 10**9}], T, meter="fable")
    assert e.points == e.low == e.high == 0.0 and e.by_model == {}
    assert estimate([{"model": OPUS, "output": 10**9}], T).points > 0


def test_fable_meter_uses_its_own_weights():
    w = T["meters"]["fable"]["models"][FABLE]["output"]["point"]
    assert estimate([{"model": FABLE, "output": 1_000_000}], T, meter="fable").points == w


def test_unsplit_cache_write_rules():
    row = T["meters"]["all_models"]["models"][FABLE]
    w5, w1 = row["cache_write_5m"], row["cache_write_1h"]
    e = estimate([{"model": FABLE, "cache_write": 1_000_000}], T)
    assert e.points == w5["point"]
    assert e.low == min(w5["low"], w1["low"]) and e.high == max(w5["high"], w1["high"])
    assert list(e.by_type) == ["cache_write"] and e.by_basis == {w5["basis"]: w5["point"]}


def test_unknown_model():
    with pytest.raises(ValueError) as ei:
        estimate([{"model": "mystery-1", "output": 5}], T)
    msg = str(ei.value)
    assert "mystery-1" in msg and "2026-11-20" in msg and FABLE in msg
    e = estimate([{"model": "mystery-1", "output": 5}, {"model": "mystery-1", "output": 5}], T, on_unknown="skip")
    assert e.skipped == {"mystery-1": 2} and e.points == 0.0
    with pytest.raises(ValueError):
        estimate([], T, on_unknown="ignore")


def test_model_id_with_suffix_resolves():
    e = estimate([{"model": "claude-opus-5-5[1m]", "output": 1_000_000}], T)
    assert list(e.by_model) == [OPUS]


def test_unknown_meter_and_empty():
    with pytest.raises(ValueError, match="all_models"):
        estimate([], T, meter="nope")
    e = estimate([], T)
    assert (e.points, e.low, e.high, e.by_model, e.tokens) == (0.0, 0.0, 0.0, {}, {})


def test_api_usd():
    p = load_prices("2026-11-20")
    recs = [{"model": OPUS, "output": 1_000_000, "cache_read": 10_000_000, "cache_write": 1_000_000}]
    assert api_usd(recs, p) == pytest.approx(20.0 + 10 * 0.20 + 5.0)
    assert api_usd([{"model": FABLE, "cache_write_1h": 1_000_000}], p) == pytest.approx(16.0)
    with pytest.raises(ValueError):
        api_usd([{"model": "x", "output": 1}], p)
    assert api_usd([{"model": "x", "output": 1}], p, on_unknown="skip") == 0.0


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "usage_weights", *args], capture_output=True, text=True, cwd=ROOT)


def test_cli_estimate_and_tables(tmp_path):
    f = tmp_path / "r.jsonl"
    f.write_text(json.dumps({"model": FABLE, "output": 1_000_000}) + "\n\n")
    r = _cli("estimate", "--table", "2026-11-20", str(f))
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["points"] == T["meters"]["all_models"]["models"][FABLE]["output"]["point"]
    r = _cli("tables")
    assert "2026-11-20" in r.stdout.split()


def test_cli_bad_input_exits_2(tmp_path):
    f = tmp_path / "r.jsonl"
    f.write_text("not json\n")
    r = _cli("estimate", "--table", "2026-11-20", str(f))
    assert r.returncode == 2 and len(r.stderr.strip().splitlines()) == 1
    assert _cli("estimate", str(f)).returncode == 2  # --table is required
    assert _cli("estimate", "--table", "nope", str(f)).returncode == 2

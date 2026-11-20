import json

import pytest

from kit import blocks


def test_filler_is_deterministic_and_long_enough():
    a, b, c = blocks.filler("seed", 500), blocks.filler("seed", 500), blocks.filler("other", 500)
    assert a == b and a != c
    assert len(a.split()) >= 500


def test_env_for_sets_exactly_one_caching_variable():
    for kind, var in [("cw1h", "ENABLE_PROMPT_CACHING_1H"), ("in", "DISABLE_PROMPT_CACHING"),
                                      ("cw5", "FORCE_PROMPT_CACHING_5M"), ("cr", "FORCE_PROMPT_CACHING_5M"),
                                      ("out", "FORCE_PROMPT_CACHING_5M")]:
        base = {"FORCE_PROMPT_CACHING_5M": "stale", "ENABLE_PROMPT_CACHING_1H": "stale",
                "DISABLE_PROMPT_CACHING": "stale", "PATH": "/bin"}
        e = blocks.env_for(kind, base)
        present = [v for v in blocks.CACHE_VARS if v in e]
        assert present == [var] and e[var] == "1"
        assert e["PATH"] == "/bin" and e["CLAUDE_CODE_DISABLE_THINKING"] == "1"
        assert base["DISABLE_PROMPT_CACHING"] == "stale"  # input is not modified


def test_parse_cli_output():
    lines = [
        "not json",
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "seven_day": {"utilization": 0.63}, "seven_day_overage_included": {"utilization": 0.51}}}}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn",
                    "duration_ms": 5, "duration_api_ms": 4, "total_cost_usd": 0.25,
                    "usage": {"input_tokens": 2, "output_tokens": 9},
                    "modelUsage": {"claude-fable-5-2": {}}}),
    ]
    meters, usage, cost, models, result = blocks.parse_cli_output("\n".join(lines))
    assert meters == [{"seven_day": 0.63, "seven_day_overage_included": 0.51}]
    assert usage == {"input_tokens": 2, "output_tokens": 9} and cost == 0.25
    assert models == ["claude-fable-5-2"]
    assert result["subtype"] == "success" and result["stop_reason"] == "end_turn" and result["is_error"] is False


def test_parse_cli_output_without_result():
    assert blocks.parse_cli_output("") == ([], None, None, None, {})


def test_prompt_for():
    cr = {"name": "x-cr", "kind": "cr"}
    cw = {"name": "x-cw", "kind": "cw5"}
    assert blocks.prompt_for(cr, 0, words=50) == blocks.prompt_for(cr, 7, words=50)
    assert blocks.prompt_for(cw, 0, words=50) != blocks.prompt_for(cw, 1, words=50)
    out = blocks.prompt_for({"name": "o", "kind": "out"}, 3, words=50)
    assert "4500 words" in out
    assert "reply with the single word ok" in blocks.framed("body")


def test_probe_model_is_required_for_cache_write_blocks(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([{"name": "a", "model": "m", "kind": "cw5", "workers": 1, "ticks": 1, "usd": 1, "minutes": 1}]))
    with pytest.raises(SystemExit) as e:
        blocks.main([str(plan), str(tmp_path / "out"), "--test"])
    assert e.value.code == 2


def test_importing_has_no_side_effects(tmp_path):
    assert not (tmp_path / "calls.jsonl").exists()
    d = blocks.Driver([], str(tmp_path / "o"))
    assert d.state["meter"] == -1

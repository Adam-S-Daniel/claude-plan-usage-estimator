import pytest

from usage_weights.records import TOKEN_TYPES, record_tokens, resolve_model, tokens_from_usage


def test_token_types():
    assert TOKEN_TYPES == ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")


def test_split_cache_creation():
    u = {"input_tokens": 2, "output_tokens": 3, "cache_read_input_tokens": 4, "cache_creation_input_tokens": 30,
         "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20}}
    assert tokens_from_usage(u) == {"input": 2, "output": 3, "cache_read": 4, "cache_write_5m": 10, "cache_write_1h": 20}


def test_remainder_goes_to_unsplit():
    u = {"cache_creation_input_tokens": 35, "cache_creation": {"ephemeral_5m_input_tokens": 10,
                                                              "ephemeral_1h_input_tokens": 20}}
    t = tokens_from_usage(u)
    assert t["cache_write"] == 5 and t["cache_write_5m"] == 10 and t["cache_write_1h"] == 20


def test_no_cache_creation_dict_is_unsplit():
    t = tokens_from_usage({"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 7})
    assert t["cache_write"] == 7 and t["cache_write_5m"] == 0 and t["cache_read"] == 0


def test_missing_fields_are_zero():
    assert tokens_from_usage({}) == {"input": 0, "output": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0}


def test_bad_counts_name_the_field():
    for usage, field in [
    ({"output_tokens": -1}, "output_tokens"),
    ({"input_tokens": "x"}, "input_tokens"),
    ({"cache_read_input_tokens": -5}, "cache_read_input_tokens"),
    ({"cache_creation": {"ephemeral_1h_input_tokens": -1}}, "ephemeral_1h_input_tokens"),
]:
        with pytest.raises(ValueError, match=field):
            tokens_from_usage(usage)


def test_record_shapes():
    assert record_tokens({"model": "m", "tokens": {"output": 5}}) == {"output": 5}
    assert record_tokens({"model": "m", "usage": {"output_tokens": 5}})["output"] == 5
    assert record_tokens({"model": "m", "output": 5, "extra": "ignored"}) == {"output": 5}
    assert record_tokens({"model": "m", "cache_write": 2}) == {"cache_write": 2}


def test_record_without_tokens_is_an_error():
    with pytest.raises(ValueError):
        record_tokens({"model": "m"})
    with pytest.raises(ValueError):
        record_tokens({"tokens": {"output": 1}})


def test_resolve_model():
    keys = ["claude-opus-5-5", "claude-opus-5-5-fast", "claude-haiku-5-5"]
    assert resolve_model("claude-opus-5-5", keys) == "claude-opus-5-5"
    assert resolve_model("claude-opus-5-5[1m]", keys) == "claude-opus-5-5"
    assert resolve_model("claude-haiku-5-5-20261001", keys) == "claude-haiku-5-5"
    assert resolve_model("claude-opus-5-5-fast-x", keys) == "claude-opus-5-5-fast"  # longest key wins
    assert resolve_model("claude-opus-5", keys) is None
    assert resolve_model("CLAUDE-HAIKU-5-5", keys) == "claude-haiku-5-5"
    assert resolve_model("gpt", keys) is None

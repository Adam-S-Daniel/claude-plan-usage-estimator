import copy
import json
import os
import re

import pytest

from usage_weights import TOKEN_TYPES, load_table
from usage_weights.table import list_tables, load_prices, validate_table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _weight(point=1.0, low=0.5, high=2.0, basis="measured"):
    return {"point": point, "low": low, "high": high, "basis": basis}


def _table():
    return {"schema": 1, "id": "t", "meters": {"all_models": {"cli_key": "seven_day", "models": {
        "m": {t: _weight() for t in TOKEN_TYPES}}}}}


def test_packaged_tables_load_by_id():
    assert {"atlas-2026-10-29", "2026-11-20"} <= set(list_tables())
    assert load_table("2026-11-20")["id"] == "2026-11-20"
    assert load_table("atlas-2026-10-29")["id"] == "atlas-2026-10-29"


def test_load_by_path(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps(_table()))
    assert load_table(p)["id"] == "t"
    assert load_table(str(p))["id"] == "t"


def test_no_implicit_latest():
    with pytest.raises(ValueError, match="name a table"):
        load_table()
    with pytest.raises(ValueError, match="name a table"):
        load_table(None)


def test_unknown_id():
    with pytest.raises(ValueError, match="unknown table"):
        load_table("1999-01-01")


def test_validate_rejects():
    for mutate, msg in [
    (lambda t: t.update(schema=2), "schema"),
    (lambda t: t["meters"]["all_models"]["models"]["m"]["output"].update(basis="guess"), "basis"),
    (lambda t: t["meters"]["all_models"]["models"]["m"].pop("cache_read"), "cache_read"),
    (lambda t: t["meters"]["all_models"]["models"]["m"]["output"].update(point=float("nan")), "finite"),
    (lambda t: t["meters"]["all_models"]["models"]["m"]["output"].update(high=float("inf")), "finite"),
    (lambda t: t["meters"]["all_models"]["models"]["m"]["output"].update(low=-1.0), "non-negative"),
    (lambda t: t["meters"]["all_models"]["models"]["m"]["output"].update(low=1.5), "low"),
    (lambda t: t["meters"]["all_models"]["models"]["m"]["output"].update(high=0.9), "above high"),
]:
        t = copy.deepcopy(_table())
        mutate(t)
        with pytest.raises(ValueError, match=msg):
            validate_table(t)


def test_validate_accepts_good():
    validate_table(_table())


def test_prices_load():
    p = load_prices("2026-11-20")
    assert p["models"]["claude-opus-5-5"]["output"] == 20.0
    with pytest.raises(ValueError):
        load_prices()


def test_atlas_weights_are_exact_reciprocals():
    t = load_table("atlas-2026-10-29")
    opus = t["meters"]["all_models"]["models"]["claude-opus-5-5"]
    assert opus["output"]["point"] == 1 / 0.66
    assert opus["cache_write_1h"]["point"] == 1 / 4.6
    haiku = t["meters"]["all_models"]["models"]["claude-haiku-5-5"]
    assert haiku["input"]["basis"] == "assumed"
    assert haiku["input"]["low"] == haiku["input"]["point"] * 0.5
    fable_meter = t["meters"]["fable"]["models"]["claude-fable-5-2"]
    assert fable_meter["output"]["point"] == 2 * t["meters"]["all_models"]["models"]["claude-fable-5-2"]["output"]["point"]


def test_every_basis_rule_in_the_2026_11_20_table():
    t = load_table("2026-11-20")
    f = t["unmeasured_factor"]
    for meter in t["meters"].values():
        for row in meter["models"].values():
            for w in row.values():
                if w["basis"] == "assumed":
                    assert w["low"] == w["point"] * f[0] and w["high"] == w["point"] * f[1]
                if w["basis"] == "published":
                    assert w["low"] == w["point"] == w["high"]


def test_readme_weight_tables_match_table_file():
    from kit import build_table as bt
    table = load_table("2026-11-20")
    text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    m = re.search(re.escape(bt.BEGIN) + r".*?" + re.escape(bt.END), text, re.S)
    assert m, "README has no weights block"
    assert m.group(0) == bt.readme_block(table)
    # Every number in the block, read back independently of the formatter.
    expected = []
    for meter in ("all_models", "fable"):
        for row in table["meters"][meter]["models"].values():
            for t in TOKEN_TYPES:
                w = row[t]
                expected += [float(f"{1 / w[k]:.3g}") for k in ("point", "high", "low")]
    found = []
    for line in m.group(0).splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 4 and cells[1] in TOKEN_TYPES:
            found += [float(x) for x in re.findall(r"[0-9.]+", cells[2])]
    assert found == expected

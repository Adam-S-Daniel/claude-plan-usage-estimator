import datetime as dt
import json
import os

import pytest

from kit import blocks, fit, guard
from kit import intervals as iv

T0 = dt.datetime(2026, 1, 1, 8, 0, 0, tzinfo=dt.timezone.utc)


def at(s):
    return guard.iso(T0 + dt.timedelta(seconds=s))


def test_parse_ps_counts_only_claude():
    text = "  10 claude\n  11 python3\n  12 /usr/bin/claude\n  13 claude-helper\n"
    assert guard.parse_ps(text) == 2


def test_recent_transcripts_uses_mtime(tmp_path):
    new, old = tmp_path / "a" / "new.jsonl", tmp_path / "a" / "old.jsonl"
    new.parent.mkdir()
    new.write_text("{}\n")
    old.write_text("{}\n")
    (tmp_path / "a" / "note.txt").write_text("x")
    os.utime(new, (1000, 1000))
    os.utime(old, (100, 100))
    assert guard.recent_transcripts(str(tmp_path), now_ts=1100, minutes=5) == [str(new)]


def test_preflight_refuses_then_override_marks_unguarded():
    assert guard.preflight(0, []) == {"other_sessions_seen": 0, "recent_transcripts": 0, "unguarded": False}
    with pytest.raises(RuntimeError, match="outside use visible"):
        guard.preflight(1, [])
    with pytest.raises(RuntimeError):
        guard.preflight(0, ["t.jsonl"])
    rec = guard.preflight(2, ["t.jsonl"], override=True)
    assert rec["unguarded"] is True and rec["other_sessions_seen"] == 2


def test_next_quiet_start():
    assert guard.next_quiet_start(T0 - dt.timedelta(hours=1)) == "2026-01-01T08:00:00Z"
    assert guard.next_quiet_start(T0) == "2026-01-02T08:00:00Z"


def test_idle_watch_with_fake_clock():
    clock = [T0]
    seen = iter([5, 5, 6, 6])
    out = guard.idle_watch(3, lambda: next(seen), lambda s: clock.__setitem__(0, clock[0] + dt.timedelta(seconds=s)),
                           lambda: clock[0], every_s=60)
    assert [p for _t, p in out] == [5, 5, 6]
    assert len(guard.rises(out)) == 1


def test_tick_in_idle_window_is_flagged_but_lag_is_not():
    window = [(T0, T0 + dt.timedelta(minutes=10))]
    inside = [(T0 + dt.timedelta(minutes=m), p) for m, p in [(1, 5), (2, 5), (3, 6)]]
    ev = guard.quiet_ticks(inside, window, "idle_tick")
    assert [e["kind"] for e in ev] == ["idle_tick"] and ev[0]["at"] == "2026-01-01T08:03:00Z"
    lag = [(T0 - dt.timedelta(minutes=1), 5), (T0 + dt.timedelta(minutes=1), 6)]  # first reading is before the window
    assert guard.quiet_ticks(lag, window, "idle_tick") == []
    assert guard.quiet_ticks([(T0 + dt.timedelta(minutes=m), 5) for m in (1, 2)], window, "idle_tick") == []


def test_unexpected_meter_only_inside_span():
    r = [(T0 + dt.timedelta(minutes=m), p) for m, p in [(0, 50), (5, 51), (20, 52)]]
    ev = guard.unexpected_meter(r, (T0, T0 + dt.timedelta(minutes=10)))
    assert [e["at"] for e in ev] == ["2026-01-01T08:05:00Z"]


def test_short_intervals():
    assert guard.short_intervals([1.0, 1.1, 0.3, 0.9]) == [2]
    assert guard.short_intervals([1.0, 1.1, 0.9]) == []
    assert guard.short_intervals([1.0, 0.1]) == []


def test_foreign_calls_scan(tmp_path):
    rows = [{"timestamp": at(5)}, {"timestamp": at(500)}, {"nope": 1}]
    (tmp_path / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    ev = guard.foreign_calls(str(tmp_path), T0, T0 + dt.timedelta(seconds=60))
    assert [e["at"] for e in ev] == [at(5)] and ev[0]["kind"] == "foreign_call"


def _ivs(points):
    return [{"from": n, "to": n + 1, "start": at(n * 100), "end": at(n * 100 + 100), "mode": "end",
             "prior_points": p} for n, p in enumerate(points)]


def test_grades():
    ivs = _ivs([1.0, 1.0, 1.0, 1.0])
    ev = [{"kind": "foreign_call", "at": at(150), "detail": "x"},
          {"kind": "idle_tick", "at": at(250), "detail": "y", "correction": "removed 0.2 points"}]
    g = [i["grade"] for i in guard.grade_intervals(ivs, ev)]
    assert g == ["clean", "contaminated", "corrected", "clean"]
    assert guard.grade_intervals(ivs, ev)[1]["evidence"][0]["kind"] == "foreign_call"
    assert [guard.usable(i) for i in guard.grade_intervals(ivs, ev)] == [True, False, True, True]


def test_short_interval_is_contaminated():
    out = guard.grade_intervals(_ivs([1.0, 1.0, 0.2, 1.0]), [])
    assert [i["grade"] for i in out] == ["clean", "clean", "contaminated", "clean"]
    assert out[2]["evidence"][0]["kind"] == "short_interval"


def test_short_interval_is_judged_within_its_own_block():
    # Two blocks whose real factors differ about 2x: neighbors are only same-block intervals.
    ivs = _ivs([2.0, 2.1, 1.9, 0.8, 0.85, 0.75])
    for i, row in enumerate(ivs):
        row["block"] = "cr" if i < 3 else "out"
    assert [i["grade"] for i in guard.grade_intervals(ivs, [])] == ["clean"] * 6
    ivs[4]["prior_points"] = 0.2  # really short inside the low block
    out = guard.grade_intervals(ivs, [])
    assert [i["grade"] for i in out] == ["clean"] * 4 + ["contaminated", "clean"]


def test_block_of_strips_the_call_suffix():
    assert [iv.block_of(t) for t in ("fable-cr-warm", "fable-cr-w3-12", "opus-cr", "probe")] == \
        ["fable-cr", "fable-cr", None, None]


def test_committed_runs_have_no_grade_and_stay_usable():
    night1 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "2026-11-19-night1")
    assert guard.load(night1) is None
    committed = json.load(open(os.path.join(night1, "intervals.json")))
    assert all("grade" not in i and guard.usable(i) for i in committed["intervals"])


def test_intervals_main_grades_from_guard_json(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run = os.path.join(root, "data", "2026-11-19-night1")
    out = tmp_path / "run"
    out.mkdir()
    for n in ("calls.jsonl", "readings.jsonl"):
        (out / n).write_text(open(os.path.join(run, n)).read())
    first = json.load(open(os.path.join(run, "intervals.json")))["ticks"][1]["at"]
    (out / "guard.json").write_text(json.dumps({"guard": {"unguarded": False}, "evidence": [
        {"kind": "foreign_call", "at": first, "detail": "x"}]}))
    iv.main([str(out), "--prior", "atlas-2026-10-29"])
    doc = json.load(open(out / "intervals.json"))
    assert doc["guard"] == {"unguarded": False}
    bad = [i for i in doc["intervals"] if i["grade"] == "contaminated"]
    assert bad and all(i["end"] == first for i in bad)


def test_fit_refuses_a_contaminated_run(tmp_path):
    (tmp_path / "guard.json").write_text(json.dumps({"guard": {}, "evidence": [
        {"kind": "foreign_call", "at": at(1), "detail": "x"}]}))
    with pytest.raises(ValueError, match="uncorrected outside-use"):
        fit.fit_run(str(tmp_path), spec={"prior": "atlas-2026-10-29"})


def test_finish_guard_writes_record(tmp_path):
    out = tmp_path / "out"
    d = blocks.Driver([{"name": "b", "model": "model-x", "kind": "out"}], str(out), probe_model="probe-y")
    row = lambda tag, s, fable: {"tag": tag, "started": at(s), "ended": at(s + 1), "meters": [  # noqa: E731
        {"seven_day": 0.05, "seven_day_overage_included": fable}]}
    with open(d.CALLS, "w") as fh:
        fh.write(json.dumps(row("b-w0-0", 10, 0.50)) + "\n" + json.dumps(row("b-w0-1", 20, 0.51)) + "\n")
    with open(d.PROBES, "w") as fh:
        fh.write(json.dumps({"tag": "probe", "started": at(100), "ended": at(101),
                             "meters": [{"seven_day": 0.05}]}) + "\n")
        fh.write(json.dumps({"tag": "probe", "started": at(160), "ended": at(161),
                             "meters": [{"seven_day": 0.06}]}) + "\n")
    d.quiet = [(T0 + dt.timedelta(seconds=90), T0 + dt.timedelta(seconds=200))]
    roots = tmp_path / "tr"
    roots.mkdir()
    (roots / "s.jsonl").write_text(json.dumps({"timestamp": at(30)}) + "\n")
    d.finish_guard({"other_sessions_seen": 0, "unguarded": False}, T0, str(roots))
    doc = json.load(open(out / "guard.json"))
    assert doc["guard"]["idle_ticks"] == 1 and doc["guard"]["foreign_calls"] == 1
    assert sorted(e["kind"] for e in doc["evidence"]) == ["foreign_call", "idle_tick", "unexpected_meter"]


def test_tick_from_the_block_before_the_window_is_not_an_idle_tick():
    # The last call of a block ends as the idle window opens; its tick shows up to 7 s later (docs/method.md).
    # The first idle probe, 2 s in, still reads the old value; the next probe, a minute on, reads the new one.
    window = [(T0, T0 + dt.timedelta(minutes=10))]
    lagged = [(T0 + dt.timedelta(seconds=2), 5), (T0 + dt.timedelta(seconds=62), 6)]
    assert guard.quiet_ticks(lagged, window, "idle_tick") == []
    # Once the first reading is past the settle period the same rise is outside use.
    settled = [(T0 + dt.timedelta(seconds=guard.SETTLE_S), 5), (T0 + dt.timedelta(seconds=62), 6)]
    assert len(guard.quiet_ticks(settled, window, "idle_tick")) == 1

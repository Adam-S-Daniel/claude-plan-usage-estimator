#!/usr/bin/env python3
"""Keep other use of the account out of a measurement run, and find it afterward.

Any call that is not the kit's moves the meter without the kit's tokens and
biases a fitted weight low. Three stages, all pure functions over data the
caller reads (clocks, process lists and transcript folders are injected, so
none of this needs a network or a wall clock to test):

  before  other `claude` processes and recently changed transcripts; refuse to
          start unless overridden (an override marks the run `unguarded`)
  during  meter ticks in quiet windows (idle watch, gaps between blocks, probed), a
          meter that should not move moving, an interval with clearly fewer
          kit tokens than its neighbors
  after   transcript entries inside the run window (the kit's own calls use
          --no-session-persistence, so they leave none)

Findings are evidence rows {"kind", "at", "detail"[, "correction"]}. Each
tick interval is graded from the evidence that falls in it: `clean`,
`corrected` (every item carries a recorded correction) or `contaminated`. A
run with no guard.json is graded by schedule only (grade "schedule").
"""
import datetime as dt
import json
import os
import statistics

CHECKLIST = (
    "Outside use this kit cannot see: other devices signed in to the account, Claude chat (web, desktop, "
    "phone), and scheduled tasks and routines. Pause or move those before starting."
)
USABLE = ("clean", "corrected", "schedule")  # grades the fit and the table builder may use
SHORT_RATIO = 0.5  # an interval under this share of its neighbors' median kit tokens is flagged
SETTLE_S = 10  # a call's tick shows up to 7 s after it ends (docs/method.md); the first reading in a window waits this long
QUIET_HOUR_UTC = 8  # the committed runs started at 08:00 UTC


def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(t):
    return t.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


# --- before -------------------------------------------------------------

def parse_ps(text):
    """Count `claude` processes in `ps -eo pid=,comm=` output."""
    n = 0
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and os.path.basename(parts[1].strip()) == "claude":
            n += 1
    return n


def recent_transcripts(root, now_ts, minutes):
    """Transcript files (*.jsonl under root) changed in the last `minutes`; now_ts is epoch seconds."""
    found = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            if name.endswith(".jsonl") and os.stat(path).st_mtime >= now_ts - minutes * 60:
                found.append(path)
    return sorted(found)


def preflight(sessions, transcripts, override=False):
    """Return the guard record; raise RuntimeError when outside use is visible and not overridden."""
    seen = {"other_sessions_seen": sessions, "recent_transcripts": len(transcripts), "unguarded": False}
    if sessions or transcripts:
        if not override:
            raise RuntimeError(f"outside use visible ({sessions} other claude process(es), {len(transcripts)} "
                               "recently changed transcript(s)); wait until clear or pass --allow-outside-use")
        seen["unguarded"] = True
    return seen


def next_quiet_start(now, hour=QUIET_HOUR_UTC):
    """The next `hour`:00 UTC strictly after `now`, as an ISO string for --start."""
    t = now.astimezone(dt.timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
    if t <= now:
        t += dt.timedelta(days=1)
    return iso(t)


# --- during -------------------------------------------------------------

def idle_watch(minutes, probe, sleep, clock, every_s=60):
    """Probe across an idle stretch. probe() returns the meter percent; returns [(time, percent)]."""
    start, out = clock(), []
    while (clock() - start).total_seconds() < minutes * 60:
        out.append((clock(), probe()))
        sleep(every_s)
    return out


def meter_series(rows):
    """Per meter key, [(time, whole percent)] from raw driver rows (calls or probes). The CLI reads the
    meter when a call starts, so a reading is stamped at the call's start."""
    out = {}
    for r in rows:
        for m in r.get("meters") or []:
            for key, v in m.items():
                if isinstance(v, (int, float)):
                    out.setdefault(key, []).append((parse_iso(r["started"]), int(round(v * 100, 6))))
    return out


def rises(readings):
    """Rises in a meter: [(previous time, time, value)] for consecutive readings [(time, percent)]."""
    rs = sorted(readings, key=lambda r: r[0])
    return [(a[0], b[0], b[1]) for a, b in zip(rs, rs[1:]) if b[1] > a[1]]


def quiet_ticks(readings, windows, kind, settle=dt.timedelta(seconds=SETTLE_S)):
    """Evidence for each rise whose two readings both lie inside one quiet window (start, end), a stretch
    with no kit call but tiny probes. Both readings must be inside, and the first at least SETTLE_S after
    the window opens, so meter lag from a call just before it is not counted."""
    return [{"kind": kind, "at": iso(t1), "detail": f"meter rose to {v} with no kit call in the window"}
            for t0, t1, v in rises(readings) if any(a + settle <= t0 and t1 <= b for a, b in windows)]


def unexpected_meter(readings, span):
    """Evidence for each rise of a meter that should not move, inside span=(start, end)."""
    return [{"kind": "unexpected_meter", "at": iso(t1), "detail": f"meter rose to {v} in a block that cannot move it"}
            for t0, t1, v in rises(readings) if span[0] <= t1 <= span[1]]


def short_intervals(values, ratio=SHORT_RATIO):
    """Indexes whose value is under `ratio` of the median of the others (needs three values)."""
    if len(values) < 3:
        return []
    return [i for i, v in enumerate(values) if v < ratio * statistics.median(values[:i] + values[i + 1:])]


# --- after --------------------------------------------------------------

def foreign_calls(root, start, end):
    """Evidence for transcript entries stamped inside [start, end] (datetimes) under root."""
    ev = []
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".jsonl"):
                continue
            with open(os.path.join(base, name), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        t = parse_iso(json.loads(line)["timestamp"])
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
                    if start <= t <= end:
                        ev.append({"kind": "foreign_call", "at": iso(t), "detail": f"transcript {name}"})
    return sorted(ev, key=lambda e: e["at"])


# --- grading ------------------------------------------------------------

def grade_intervals(intervals, evidence, ratio=SHORT_RATIO):
    """Copies of `intervals` with `evidence` and `grade`. Evidence belongs to the interval with
    start < at <= end; the short-interval check runs per (block, charge mode), because
    blocks of one run have different real factors and must not be each other's neighbors."""
    out = [dict(iv, evidence=[]) for iv in intervals]
    for group in {(iv.get("block"), iv["mode"]) for iv in out}:
        idx = [i for i, iv in enumerate(out) if (iv.get("block"), iv["mode"]) == group]
        for j in short_intervals([out[i]["prior_points"] for i in idx], ratio):
            iv = out[idx[j]]
            iv["evidence"].append({"kind": "short_interval", "at": iv["end"],
                                   "detail": "clearly fewer kit tokens than its neighbors"})
    for iv in out:
        a, b = parse_iso(iv["start"]), parse_iso(iv["end"])
        iv["evidence"] += [e for e in evidence if a < parse_iso(e["at"]) <= b]
        ev = iv["evidence"]
        iv["grade"] = "clean" if not ev else "corrected" if all(e.get("correction") for e in ev) else "contaminated"
    return out


def usable(interval):
    return interval.get("grade", "schedule") in USABLE


def load(run_dir):
    """A run folder's guard.json ({"guard": {...}, "evidence": [...]}), or None (graded by schedule only)."""
    path = os.path.join(run_dir, "guard.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

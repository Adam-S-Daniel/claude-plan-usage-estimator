#!/usr/bin/env python3
"""Per-token-type meter blocks: the workload driver.

Self-contained: needs only the `claude` CLI signed in to a Claude subscription.
No repository, no personal paths. Each block sends synthetic `claude -p` calls
built so that one token type dominates what the call is charged:

  out    long prose replies (output tokens)
  cr     the same large prompt again and again (cache reads), after one warm call
  cw5    a fresh large prompt every call, 5-minute cache writes
  cw1h   the same with 1-hour cache writes
  in     a fresh large prompt with prompt caching off (uncached input)

Every call is logged to <out>/calls.jsonl as soon as it ends: start and end
time, tokens by type (cache writes split 5m/1h), the CLI's dollar figure, and
every meter reading the CLI reported (seven_day, and seven_day_overage_included
on Fable calls, both as fractions). Small probe calls every PROBE_S seconds
add meter readings between calls (logged to <out>/probes.jsonl); they use
--probe-model, which is required when any block is cw5, cw1h or in.

A block runs until the all-models weekly meter (CLI reading, whole percent) is
`ticks` points above the reading at the block's start; dollars and minutes are
safeties only. The plan is a JSON list of blocks:
  [{"name": "fable-out", "model": "<model id>", "kind": "out",
    "workers": 6, "ticks": 2, "usd": 30, "minutes": 40}, ...]
The first block gets one extra tick (the lead-in, which only fills the part of
a point already used). See plan.example.json.

Usage: blocks.py <plan.json> <out dir> [--probe-model ID] [--start ISO_UTC] [--test]
--test sends one call of each block (two for cr: warm, then read) and stops.

A full run costs real allowance and API-priced dollars.
"""
import argparse
import datetime as dt
import json
import os
import random
import subprocess
import sys
import threading
import time

PROBE_S = 10
PROBE_KINDS = ("cw5", "cw1h", "in")
CACHE_VARS = ("FORCE_PROMPT_CACHING_5M", "ENABLE_PROMPT_CACHING_1H", "DISABLE_PROMPT_CACHING")


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


ADJ = ['quiet', 'old', 'bright', 'narrow', 'green', 'early', 'distant', 'warm', 'heavy', 'patient', 'small', 'tall',
       'cold', 'gentle', 'busy', 'empty', 'careful', 'wooden', 'silver', 'late']
NOUN = ['ferry', 'harbor', 'baker', 'orchard', 'clock', 'river', 'library', 'lantern', 'garden', 'bridge', 'market',
        'teacher', 'window', 'road', 'train', 'village', 'kettle', 'field', 'boat', 'painter', 'map', 'letter',
        'hill', 'station', 'farmer', 'notebook', 'cart', 'tower', 'meadow', 'workshop']
VERB = ['crossed', 'watched', 'carried', 'repaired', 'painted', 'passed', 'opened', 'followed', 'counted', 'cleaned',
        'visited', 'measured', 'described', 'remembered', 'noticed', 'lifted', 'sorted', 'checked', 'found', 'moved']
PLACE = ['before noon', 'after the rain', 'near the water', 'in the evening', 'by the gate', 'at first light',
         'along the shore', 'under the trees', 'during the fair', 'on the third day']
TOPICS = ['a lighthouse keeper', 'a river ferry', 'a mountain bakery', 'a night train', 'a desert observatory',
          'a harbor market', 'a snowed-in library', 'a traveling orchestra', 'an orchard in autumn', 'a canal lock',
          'a radio station at dawn', 'a glassblower', 'a ship in fog', 'a village clock', 'a beekeeper']


def filler(seed, words):
    """Deterministic plain English sentences from a small vocabulary (no real content, nothing private)."""
    r = random.Random(seed)
    out, n = [], 0
    while n < words:
        sent = (f"The {r.choice(ADJ)} {r.choice(NOUN)} {r.choice(VERB)} the {r.choice(ADJ)} "
                f"{r.choice(NOUN)} {r.choice(PLACE)}.")
        out.append(sent)
        n += len(sent.split())
    return ' '.join(out)


def framed(body):
    # Unframed filler drew safety-classifier refusals on some models; framing it does not.
    return ('This message is a load test for prompt caching. The text between the markers is machine-generated '
            'filler sentences with no meaning; you do not need to analyze it.\n\n=== BEGIN FILLER ===\n'
            + body + '\n=== END FILLER ===\n\nTask: reply with the single word ok.')


def prompt_for(block, i, words=100000):
    k = block['kind']
    if k == 'out':
        return (f'Write a plain-prose story of about 4500 words about {TOPICS[i % len(TOPICS)]} (variation {i}). '
                'No headings, no lists, no preamble.')
    if k == 'cr':
        return framed(filler(f"cr-{block['name']}", words))
    # cw5, cw1h, in: unique text every call, so nothing is read from cache
    return f'[{block["name"]} {i} {time.time_ns()}]\n' + framed(filler(f"{block['name']}-{i}-{time.time_ns()}", words))


def env_for(kind, base=None):
    e = dict(os.environ if base is None else base, CLAUDE_CODE_DISABLE_THINKING='1')
    for v in CACHE_VARS:
        e.pop(v, None)
    if kind == 'cw1h':
        e['ENABLE_PROMPT_CACHING_1H'] = '1'
    elif kind == 'in':
        e['DISABLE_PROMPT_CACHING'] = '1'
    else:
        e['FORCE_PROMPT_CACHING_5M'] = '1'
    return e


def parse_cli_output(stdout):
    """Parse `--output-format stream-json` output into (meters, usage, cost, models, result)."""
    meters, usage, cost, models, result = [], None, None, None, {}
    for line in stdout.splitlines():
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if not isinstance(j, dict):
            continue
        if j.get('type') == 'rate_limit_event':
            w = ((j.get('rate_limit_info') or {}).get('unifiedWindows') or {})
            meters.append({k: v.get('utilization') for k, v in w.items() if isinstance(v, dict)})
        elif j.get('type') == 'result':
            usage, cost, models = j.get('usage'), j.get('total_cost_usd'), list((j.get('modelUsage') or {}))
            result = dict(subtype=j.get('subtype'), is_error=j.get('is_error'), stop_reason=j.get('stop_reason'),
                          duration_ms=j.get('duration_ms'), duration_api_ms=j.get('duration_api_ms'))
    return meters, usage, cost, models, result


class Driver:
    """One run: paths, lock, and the meter and dollar state shared by the workers."""

    def __init__(self, plan, out, words=100000, probe_model=None):
        self.plan, self.out, self.words, self.probe_model = plan, out, words, probe_model
        os.makedirs(out, exist_ok=True)
        self.CALLS, self.PROBES, self.LOG = (os.path.join(out, n) for n in ('calls.jsonl', 'probes.jsonl', 'log.txt'))
        self.lock = threading.Lock()
        self.state = dict(meter=-1, meter_fable=-1, spent=0.0)
        self.probe_on, self.stop_all = threading.Event(), threading.Event()

    def log(self, msg):
        line = f'{iso(now())} {msg}'
        print(line, flush=True)
        with self.lock, open(self.LOG, 'a') as fh:
            fh.write(line + '\n')

    def call(self, model, kind, text, tag, sink):
        t0 = now()
        cmd = ['claude', '-p', '--model', model, '--system-prompt', 'You follow instructions exactly.',
               '--tools', '', '--strict-mcp-config', '--setting-sources', '', '--no-session-persistence',
               '--output-format', 'stream-json', '--verbose', '--max-turns', '1']
        try:
            p = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=900, env=env_for(kind),
                               cwd=self.out)
            rc, stdout, stderr = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            rc, stdout = 124, (e.stdout or b'').decode() if isinstance(e.stdout, bytes) else (e.stdout or '')
            stderr = 'timeout'
        t1 = now()
        meters, usage, cost, models, result = parse_cli_output(stdout)
        rec = dict(tag=tag, model=model, kind=kind, started=iso(t0), ended=iso(t1), rc=rc, cost_usd=cost,
                   models=models, meters=meters, usage=usage, result=result,
                   stderr_tail=(stderr or '')[-300:] if rc else '')
        with self.lock, open(sink, 'a') as fh:
            fh.write(json.dumps(rec) + '\n')
        return rec

    def note(self, rec, count_cost=True):
        for m in rec['meters']:
            if isinstance(m.get('seven_day'), (int, float)):
                self.state['meter'] = max(self.state['meter'], int(round(m['seven_day'] * 100, 6)))
            if isinstance(m.get('seven_day_overage_included'), (int, float)):
                self.state['meter_fable'] = max(self.state['meter_fable'],
                                                int(round(m['seven_day_overage_included'] * 100, 6)))
        if count_cost and rec['cost_usd']:
            self.state['spent'] += rec['cost_usd']

    def prober(self):
        while not self.stop_all.is_set():
            if self.probe_on.is_set():
                self.note(self.call(self.probe_model, 'cw5', 'Reply with only the word: ok', 'probe', self.PROBES),
                          count_cost=False)
            self.stop_all.wait(PROBE_S)

    def run_block(self, b, first, hard_end):
        state, lock = self.state, self.lock
        self.log(f"block {b['name']} start model {b['model']} kind {b['kind']} workers {b['workers']} "
                 f"ticks {b['ticks']}{' (+1 lead-in)' if first else ''} usd-safety {b['usd']} "
                 f"minutes-safety {b['minutes']} meter {state['meter']} fable {state['meter_fable']}")
        spent0, t_end = state['spent'], min(time.time() + b['minutes'] * 60, hard_end)
        base = [state['meter']]  # -1 until the block's first reading
        need = b['ticks'] + (1 if first else 0)
        stop, counter, fails = threading.Event(), [0], [0]
        if b['kind'] in PROBE_KINDS:
            self.probe_on.set()
        if b['kind'] == 'cr':  # warm the cache once before fanning out
            rec = self.call(b['model'], 'cr', prompt_for(b, 0, self.words), f"{b['name']}-warm", self.CALLS)
            self.note(rec)
            if base[0] < 0:
                base[0] = state['meter']

        def done():
            if stop.is_set() or self.stop_all.is_set():
                return True
            if time.time() >= t_end:
                self.log(f"block {b['name']}: time safety")
                return True
            if state['spent'] - spent0 >= b['usd']:
                self.log(f"block {b['name']}: dollar safety (${state['spent'] - spent0:.2f})")
                return True
            if base[0] >= 0 and state['meter'] >= base[0] + need:
                self.log(f"block {b['name']}: meter {state['meter']} reached {base[0]} + {need}")
                return True
            return False

        def worker(w):
            while not done():
                with lock:
                    i = counter[0]
                    counter[0] += 1
                rec = self.call(b['model'], b['kind'], prompt_for(b, i, self.words), f"{b['name']}-w{w}-{i}",
                                self.CALLS)
                self.note(rec)
                if base[0] < 0 and state['meter'] >= 0:
                    base[0] = state['meter']
                if rec['rc'] != 0 or not rec['usage']:
                    fails[0] += 1
                    if fails[0] >= 10:
                        self.log(f"block {b['name']}: 10 failures in a row")
                        stop.set()
                    time.sleep(30)
                else:
                    fails[0] = 0
                if done():
                    stop.set()
                elif b.get('gap_s'):
                    time.sleep(b['gap_s'])

        ts = [threading.Thread(target=worker, args=(w,)) for w in range(b['workers'])]
        for t in ts:
            t.start()
            time.sleep(3)
        for t in ts:
            t.join()
        self.probe_on.clear()
        self.log(f"block {b['name']} end spent ${state['spent'] - spent0:.2f} meter {state['meter']} "
                 f"fable {state['meter_fable']}")

    def test(self):
        for b in self.plan:
            n = 2 if b['kind'] == 'cr' else 1
            for i in range(n):
                rec = self.call(b['model'], b['kind'], prompt_for(b, i, self.words), f"test-{b['name']}-{i}", self.CALLS)
                u = rec['usage'] or {}
                secs = (dt.datetime.fromisoformat(rec['ended'][:-1]) - dt.datetime.fromisoformat(rec['started'][:-1])).total_seconds()
                self.log(f"test {b['name']} #{i} rc {rec['rc']} {secs:.0f}s "
                         f"in {u.get('input_tokens')} out {u.get('output_tokens')} "
                         f"think {(u.get('output_tokens_details') or {}).get('thinking_tokens')} "
                         f"cr {u.get('cache_read_input_tokens')} cw {u.get('cache_creation')} "
                         f"${rec['cost_usd']} meters {rec['meters']}")

    def run(self, start=None, hard_minutes=110):
        if start:
            self.log(f'waiting for {start}')
            while now() < dt.datetime.fromisoformat(start.replace('Z', '+00:00')):
                time.sleep(20)
        hard_end = time.time() + hard_minutes * 60
        threading.Thread(target=self.prober, daemon=True).start()
        for n, b in enumerate(self.plan):
            if time.time() >= hard_end:
                self.log('hard time limit: skipping remaining blocks')
                break
            self.run_block(b, n == 0, hard_end)
        self.stop_all.set()
        self.log('driver done')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('plan')
    ap.add_argument('out')
    ap.add_argument('--start')
    ap.add_argument('--test', action='store_true')
    ap.add_argument('--probe-model', help='model for the meter probes; required when a block is cw5, cw1h or in')
    ap.add_argument('--words', type=int, default=int(os.environ.get('FILLER_WORDS', '100000')))
    ap.add_argument('--hard-minutes', type=float, default=110)
    args = ap.parse_args(argv)
    with open(args.plan) as fh:
        plan = json.load(fh)
    if any(b['kind'] in PROBE_KINDS for b in plan) and not args.probe_model:
        ap.error('--probe-model is required when any block is cw5, cw1h or in')
    d = Driver(plan, args.out, args.words, args.probe_model)
    if not args.test and os.path.exists(d.CALLS) and os.path.getsize(d.CALLS):
        sys.exit(f'REFUSED: {d.CALLS} already has rows')
    if args.test:
        d.test()
        return
    d.run(args.start, args.hard_minutes)


if __name__ == '__main__':
    main()

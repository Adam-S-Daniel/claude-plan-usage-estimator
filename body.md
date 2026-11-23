<!-- claude-projects-attribution: {"github_login":"Adam-S-Daniel"} -->
_Requested by **Adam** · [project thread](https://claude.ai/code/project/chan_017VB8JkXZhWtV9XFe5WpQnY?thread=cmsg_017VB8JkXZhWtV9XFe5WpQnY7MzLza8ZVtawMJGhjfy7bM)_

## TLDR
Short-interval grading compared each interval with every other interval of the same charge mode, even across blocks. Blocks have different real factors, so a whole low block could be marked contaminated. It now compares within (block, charge mode).

<details><summary>Details</summary>

Root cause: `grade_intervals` (kit/guard.py) grouped by `mode` only, and intervals carried no block. A run such as night2 mixes fable-out, fable-cr, fable-cw5 and opus-cr. `intervals.compute` now sets `block` (dominant block by prior points, from `blocks.py` tags `<block>-warm` / `<block>-w<k>-<i>`); untagged runs (night1) get no key, so committed intervals.json is unchanged and grading falls back to mode only.

Tests: new regression test with two blocks about 2x apart fails before the guard change (clean block flagged) and passes after; plus `block_of` test. `python3 -m pytest -q`: 83 passed, exit 0.

Refs #3
</details>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01SphsQBFowwE9cdAVhgQWtE

<sub>🤖 Written by Claude Code on behalf of @Adam-S-Daniel</sub>

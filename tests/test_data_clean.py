import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "_source", "__pycache__", ".pytest_cache"}
# Built from pieces so this file does not contain what it forbids.
FORBIDDEN = ["/ho" + "me/", "/m" + "nt/", "bo" + "xy", "ad" + "am", "skills" + "-evals", "pas" + "sp"]
# Files the fleet's sync writes or that name the fleet's own GitHub accounts
# (AGENTS.md, the contributor rules, the agent-checks runner and its workflow,
# the Dependabot config and auto-merge sweep). They are public fleet policy, not
# measurement data, and they must name the owner account to work. Exact paths
# or whole managed directories only: never the package, data, kit or docs.
FLEET_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    ".gitattributes",
    ".github/dependabot.yml",
    ".github/fleet-contributor-rules.md",
    ".github/workflows/agent-checks.yml",
    ".github/workflows/dependabot-auto-merge.yml",
}
FLEET_DIRS = {".githooks", ".claude", ".agent-checks"}
# An email address anywhere is a leak; a bare at-sign (a decorator, a pip URL) is not.
EMAIL = re.compile(r"[a-z0-9._%+-]+" + chr(64) + r"[a-z0-9-]+\.[a-z]{2,}")


def is_fleet_file(rel):
    rel = rel.replace(os.sep, "/")
    return rel in FLEET_FILES or rel.split("/", 1)[0] in FLEET_DIRS


def test_fleet_allowlist_never_covers_measurement_content():
    for rel in sorted(FLEET_FILES | FLEET_DIRS):
        top = rel.split("/", 1)[0]
        assert top not in {"usage_weights", "data", "kit", "docs", "tests", "README.md"}, rel
    assert not is_fleet_file("README.md")
    assert not is_fleet_file("usage_weights/data/weights/x.json")
    assert not is_fleet_file(".github/workflows/ci.yml")
    assert is_fleet_file(".githooks/agent-checks")


def test_no_private_strings_anywhere():
    bad = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in files:
            if name.endswith(".pyc"):
                continue
            path = os.path.join(base, name)
            if is_fleet_file(os.path.relpath(path, ROOT)):
                continue
            text = open(path, encoding="utf-8", errors="replace").read().lower()
            bad += [f"{os.path.relpath(path, ROOT)}: {s!r}" for s in FORBIDDEN if s in text]
            if EMAIL.search(text):
                bad.append(f"{os.path.relpath(path, ROOT)}: an email address")
    assert not bad, bad

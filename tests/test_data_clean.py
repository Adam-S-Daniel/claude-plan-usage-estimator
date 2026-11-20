import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "_source", "__pycache__", ".pytest_cache"}
# Built from pieces so this file does not contain what it forbids.
FORBIDDEN = ["/ho" + "me/", "/m" + "nt/", "bo" + "xy", "ad" + "am", "skills" + "-evals", "pas" + "sp"]
# An email address anywhere is a leak; a bare at-sign (a decorator, a pip URL) is not.
EMAIL = re.compile(r"[a-z0-9._%+-]+" + chr(64) + r"[a-z0-9-]+\.[a-z]{2,}")


def test_no_private_strings_anywhere():
    bad = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in files:
            if name.endswith(".pyc"):
                continue
            path = os.path.join(base, name)
            text = open(path, encoding="utf-8", errors="replace").read().lower()
            bad += [f"{os.path.relpath(path, ROOT)}: {s!r}" for s in FORBIDDEN if s in text]
            if EMAIL.search(text):
                bad.append(f"{os.path.relpath(path, ROOT)}: an email address")
    assert not bad, bad

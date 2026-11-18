#!/usr/bin/env python3
"""The test groups of the `tests (<group>)` matrix: one group per directory under tests/ (the directory tree is the only list).

Prints the groups as a JSON array (the one line on stdout, the result) and, with GITHUB_OUTPUT set, as `groups=<json>`. Fails (reasons on stderr as ::error::) when a test file
(test_*.py or *.sh) sits outside a group, where nothing would run it, or when a group directory holds no test file. tests/fakes is not a group (shared fakes).
Run: python3 -I .github/scripts/plan_tests.py. It runs on the runner's own python3 before uv is set up, so it imports nothing but the standard library (not `ci`): its
stdout and stderr follow the same rule by hand.
"""

import json
import os
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[2] / "tests"
NOT_GROUPS = {"fakes", "__pycache__"}


def is_test(p):
    return p.name.startswith("test_") and p.suffix == ".py" or p.suffix == ".sh"


def problem(stray, groups):
    """The first reason the layout is wrong, or None."""
    empty = [g for g in groups if not any(is_test(p) for p in (TESTS / g).iterdir())]
    if stray:
        return f"tests/{stray[0]} is outside a group: move it into a tests/<group>/ directory ({', '.join(stray)})"
    if empty:
        return f"the test group tests/{empty[0]} has no test file"
    if not groups:
        return "no test groups under tests/"
    return None


def main():
    stray = sorted(p.name for p in TESTS.iterdir() if p.is_file() and is_test(p))
    groups = sorted(
        d.name for d in TESTS.iterdir() if d.is_dir() and d.name not in NOT_GROUPS
    )
    if reason := problem(stray, groups):
        print(f"::error::{reason}", file=sys.stderr)
        return 1
    text = json.dumps(groups)
    print(text)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"groups={text}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

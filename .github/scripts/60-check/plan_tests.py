#!/usr/bin/env python3
"""The test groups of the `tests (<group>)` matrix.

One group per directory under tests/ (the directory tree is the only list).

Prints the groups as a JSON array (the one line on stdout, the result) and, with
GITHUB_OUTPUT set, as `groups=<json>`. Fails (reasons on stderr as ::error::) when a
test file (test_*.py or *.sh) sits outside a group, where nothing would run it, or
when a group directory holds no test file. tests/fakes is not a group (shared fakes).
Run: python3 -I .github/scripts/plan_tests.py. It runs on the runner's own python3
before uv is set up, so it imports nothing but the standard library (not `ci`): its
stdout and stderr follow the same rule by hand.
"""

import json
import os
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[3] / "tests"
NOT_GROUPS = {"fakes", "__pycache__"}


def is_test(p):
    """Is the path `p` a test file: test_*.py (pytest) or *.sh (a shell test)?"""
    return p.name.startswith("test_") and p.suffix == ".py" or p.suffix == ".sh"


def problem(stray, groups):
    """The first reason the tests layout is wrong, or None when it is fine.

    `stray` are test files outside a group directory, `groups` the group names.
    Checks in this order: a stray file, a group without a test file, no group at all.
    """
    # groups whose directory holds nothing that runs
    empty = [g for g in groups if not any(is_test(p) for p in (TESTS / g).iterdir())]

    # a test file outside a group would never run
    if stray:
        return f"tests/{stray[0]} is outside a group: move it into a tests/<group>/ directory ({', '.join(stray)})"

    # an empty group would be a matrix job that tests nothing
    if empty:
        return f"the test group tests/{empty[0]} has no test file"

    # no group at all means no tests
    if not groups:
        return "no test groups under tests/"
    return None


def main():
    """Print the test groups as a JSON array, the one stdout line (the result).

    With GITHUB_OUTPUT set the same is written as `groups=<json>`. Returns 0, or 1
    after an `::error::` line on stderr when the layout is wrong (see `problem`).
    """
    # test files that sit directly under tests/, outside any group
    stray = sorted(p.name for p in TESTS.iterdir() if p.is_file() and is_test(p))

    # every directory under tests/ is a group, except the shared fakes
    groups = sorted(
        d.name for d in TESTS.iterdir() if d.is_dir() and d.name not in NOT_GROUPS
    )

    # a wrong layout fails the plan before any group runs
    if reason := problem(stray, groups):
        print(f"::error::{reason}", file=sys.stderr)
        return 1
    text = json.dumps(groups)
    print(text)

    # outside Actions there is no output file to write
    if os.environ.get("GITHUB_OUTPUT"):
        # append, other steps write to the same file
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"groups={text}\n")
    return 0


# run only as a script, not when a test imports the module
if __name__ == "__main__":
    sys.exit(main())

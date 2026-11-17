#!/usr/bin/env python3
"""The fast sanity check (first step of the required `test` job, about 10 s): every shell script parses, every Python file compiles, and every workflow and action file is valid YAML
(a broken one gives a "workflow file issue" with no job and no useful message). Everything else shows where it matters. Run from the repository root. The .archive/ folder is skipped.
"""
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402

ARCHIVE = ".archive/"  # the kept shell scripts: inert, never checked as live code


def tracked(pattern):
    out = subprocess.run(["git", "ls-files", pattern], capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if not f.startswith(ARCHIVE)]


def problems():
    found = []
    for f in tracked("*.sh"):
        if subprocess.run(["bash", "-n", f]).returncode != 0:
            found.append(f"bash -n {f}")
    for f in tracked("*.py"):
        try:
            compile(Path(f).read_text(), f, "exec")
        except SyntaxError as e:
            found.append(f"{f} does not compile: {e}")
    for f in sorted([*Path(".github/workflows").glob("*.yml"), *Path(".github/actions").glob("*/action.yml")]):
        try:
            yaml.safe_load(f.read_text())
        except yaml.YAMLError as e:
            found.append(f"{f} does not parse: {e}")
    return found


def main(argv=None):
    ci.arg_parser(__doc__).parse_args(argv)
    found = problems()
    for p in found:
        ci.log.error(p)
    if not found:
        ci.log.info("sanity: all checks passed")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

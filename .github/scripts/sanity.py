#!/usr/bin/env python3
"""The fast sanity check (first step of the required `test` job, about 10 s): every shell script parses, every Python file compiles, and every workflow and action file is valid YAML
(a broken one gives a "workflow file issue" with no job and no useful message). Everything else shows where it matters. Run from the repository root; needs PyYAML, which the
GitHub runner image ships (the one non-stdlib import of the scripts; the check says so when it is missing).
"""
import subprocess
import sys
from pathlib import Path


def tracked(pattern):
    out = subprocess.run(["git", "ls-files", pattern], capture_output=True, text=True, check=True).stdout
    return out.splitlines()


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
    try:
        import yaml
    except ImportError:
        return found + ["PyYAML is not installed: the workflow files cannot be parsed"]
    for f in sorted([*Path(".github/workflows").glob("*.yml"), *Path(".github/actions").glob("*/action.yml")]):
        try:
            yaml.safe_load(f.read_text())
        except Exception as e:
            found.append(f"{f} does not parse: {e}")
    return found


def main():
    found = problems()
    for p in found:
        print(f"FAIL: {p}")
    if not found:
        print("sanity: all checks passed")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

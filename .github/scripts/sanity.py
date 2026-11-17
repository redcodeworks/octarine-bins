#!/usr/bin/env python3
"""The fast sanity check (first step of the required `test` job, about 10 s): every shell script parses, every Python file compiles, and every workflow and action file is valid YAML
(a broken one gives a "workflow file issue" with no job and no useful message), and every repo path a Containerfile copies in is listed in that image's `ci.inputs` (a path
missing there would let a change skip the build and `testing` be promoted stale). Everything else shows where it matters. Run from the repository root. The .archive/ folder is skipped.
"""
import re
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


def copied_paths(containerfile, contexts):
    """The repo paths a Containerfile copies in: COPY/ADD sources from the build context (the Containerfile's directory) and from a named `--build-context` (`contexts`: name
    to repo path, read from the Justfile). `--from=<stage>` is another stage, not the repo. Returns (paths, problems); what cannot be resolved is a problem, never ignored."""
    text = Path(containerfile).read_text().replace("\\\n", " ")
    lines = [line.split() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    stages = {w[3] for w in lines if w[0].upper() == "FROM" and len(w) > 3 and w[2].upper() == "AS"}
    root, paths, found = Path(containerfile).parent, [], []
    for words in lines:
        if words[0].upper() == "RUN" and any(w.startswith("--mount=") and "type=bind" in w for w in words):
            found.append(f"{containerfile}: RUN --mount=type=bind is not checked by sanity.py; extend copied_paths: {' '.join(words)}")
        if words[0].upper() not in ("COPY", "ADD"):
            continue
        flags = [w for w in words[1:] if w.startswith("--")]
        args = [w for w in words[1:] if not w.startswith("--")][:-1]  # the last argument is the destination
        source = next((f.removeprefix("--from=") for f in flags if f.startswith("--from=")), None)
        if source in stages:
            continue
        if source is not None and source not in contexts:
            found.append(f"{containerfile}: --from={source} is neither a stage nor a --build-context of the Justfile")
            continue
        base = Path(contexts[source]) if source else root
        for arg in args:
            if "$" in arg or arg.startswith(("http://", "https://", "<<")):
                found.append(f"{containerfile}: cannot check the source '{arg}'")
            else:
                paths.append(str(base / arg))  # Path drops a trailing "/." so `.` is the directory itself
    return paths, found


def input_problems(justfile="Justfile"):
    """Every path a Containerfile copies in must be one of (or inside one of) the image's `ci.inputs`; a new image directory without inputs is a problem too."""
    contexts = dict(re.findall(r"--build-context\s+(\w+)=(\S+)", Path(justfile).read_text()))
    found = []
    for containerfile in tracked("*/Containerfile"):
        directory = containerfile.split("/")[0]
        try:
            listed = ci.inputs(directory)
        except ValueError as e:
            found.append(f"{containerfile}: {e}")
            continue
        paths, errors = copied_paths(containerfile, contexts)
        found += errors
        found += [f"{containerfile} copies '{p}', which is not in ci.inputs('{directory}'): a change to it would not rebuild the image"
                  for p in paths if not any(p == i or p.startswith(i + "/") for i in listed)]
    return found


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
    found = problems() + input_problems()
    for p in found:
        ci.log.error(p)
    if not found:
        ci.log.info("sanity: all checks passed")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

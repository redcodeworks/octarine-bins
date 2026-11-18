#!/usr/bin/env python3
"""The fast sanity check (first step of the required `test` job, about 10 s): every shell script parses, every Python file compiles, and every workflow and action file is valid YAML
(a broken one gives a "workflow file issue" with no job and no useful message), and every repo path a Containerfile copies in is listed in that image's `ci.inputs` (a path
missing there would let a change skip the build and `testing` be promoted stale). Everything else shows where it matters. Run from the repository root. The .archive/ folder is skipped.
"""

import re
import subprocess
import sys
from itertools import chain
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402

ARCHIVE = ".archive/"  # the kept shell scripts: inert, never checked as live code


def tracked(pattern):
    out = subprocess.run(
        ["git", "ls-files", pattern], capture_output=True, text=True, check=True
    ).stdout
    return [f for f in out.splitlines() if not f.startswith(ARCHIVE)]


def line_sources(containerfile, words, stages, contexts):
    """(paths, problems) of ONE Containerfile line: the repo paths a COPY/ADD takes (from the Containerfile's directory, or from a named `--build-context`, `contexts`: name to
    repo path, read from the Justfile) and what cannot be resolved. `--from=<stage>` is another stage, not the repo."""
    match words[0].upper():
        case "RUN" if any(w.startswith("--mount=") and "type=bind" in w for w in words):
            return [], [
                f"{containerfile}: RUN --mount=type=bind is not checked by sanity.py; extend line_sources: {' '.join(words)}"
            ]
        case "COPY" | "ADD":
            flags = [w for w in words[1:] if w.startswith("--")]
            args = [w for w in words[1:] if not w.startswith("--")][
                :-1
            ]  # the last argument is the destination
            source = next(
                (f.removeprefix("--from=") for f in flags if f.startswith("--from=")),
                None,
            )
            if source in stages:
                return [], []
            if source is not None and source not in contexts:
                return [], [
                    f"{containerfile}: --from={source} is neither a stage nor a --build-context of the Justfile"
                ]
            base = Path(contexts[source]) if source else Path(containerfile).parent
            unchecked = [
                a
                for a in args
                if "$" in a or a.startswith(("http://", "https://", "<<"))
            ]
            # Path drops a trailing "/." so `.` is the directory itself
            return [str(base / a) for a in args if a not in unchecked], [
                f"{containerfile}: cannot check the source '{a}'" for a in unchecked
            ]
        case _:
            return [], []


def copied_paths(containerfile, contexts):
    """The repo paths a Containerfile copies in, and the problems of lines that cannot be resolved (never ignored): (paths, problems)."""
    text = Path(containerfile).read_text().replace("\\\n", " ")
    lines = [
        line.split()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    stages = {
        w[3]
        for w in lines
        if w[0].upper() == "FROM" and len(w) > 3 and w[2].upper() == "AS"
    }
    per_line = [line_sources(containerfile, words, stages, contexts) for words in lines]
    return list(chain.from_iterable(p for p, _ in per_line)), list(
        chain.from_iterable(f for _, f in per_line)
    )


def image_problems(containerfile, contexts):
    """The problems of one image: every path its Containerfile copies in must be one of (or inside one of) its `ci.inputs`; a directory without inputs is a problem too."""
    directory = containerfile.split("/")[0]
    try:
        listed = ci.inputs(directory)
    except ValueError as e:
        return [f"{containerfile}: {e}"]
    paths, errors = copied_paths(containerfile, contexts)
    return [
        *errors,
        *(
            f"{containerfile} copies '{p}', which is not in ci.inputs('{directory}'): a change to it would not rebuild the image"
            for p in paths
            if not any(p == i or p.startswith(i + "/") for i in listed)
        ),
    ]


def input_problems(justfile="Justfile"):
    """Every image's `image_problems`, in one list."""
    contexts = dict(
        re.findall(r"--build-context\s+(\w+)=(\S+)", Path(justfile).read_text())
    )
    return list(
        chain.from_iterable(
            image_problems(containerfile, contexts)
            for containerfile in tracked("*/Containerfile")
        )
    )


def bash_problem(path):
    return (
        f"bash -n {path}"
        if subprocess.run(["bash", "-n", path]).returncode != 0
        else None
    )


def python_problem(path):
    try:
        compile(Path(path).read_text(), path, "exec")
    except SyntaxError as e:
        return f"{path} does not compile: {e}"


def yaml_problem(path):
    try:
        yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return f"{path} does not parse: {e}"


def problems():
    workflows = sorted(
        [
            *Path(".github/workflows").glob("*.yml"),
            *Path(".github/actions").glob("*/action.yml"),
        ]
    )
    checked = [
        *map(bash_problem, tracked("*.sh")),
        *map(python_problem, tracked("*.py")),
        *map(yaml_problem, workflows),
    ]
    return [message for message in checked if message]


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

#!/usr/bin/env python3
"""The fast sanity check: the first step of the required `test` job (about 10 s).

Every shell script parses, every Python file compiles, and every workflow and action
file is valid YAML (a broken one gives a "workflow file issue" with no job and no
useful message), and every repo path a Containerfile copies in is listed in that
image's `ci.inputs` (a path missing there would let a change skip the build and
`testing` be promoted stale). Everything else shows where it matters. Run from the
repository root. The .archive/ folder is skipped.
"""

import re
import subprocess
import sys
from itertools import chain
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "00-lib"))
import ci  # noqa: E402

# the kept shell scripts: inert, never checked as live code
ARCHIVE = ".archive/"


def tracked(pattern):
    """The tracked files matching the git pathspec `pattern`, without the archive.

    Asks `git ls-files`, so untracked files are not checked; raises
    CalledProcessError outside a git checkout.
    """
    out = subprocess.run(
        ["git", "ls-files", pattern], capture_output=True, text=True, check=True
    ).stdout

    # the archive is frozen on purpose: leave it out
    return [f for f in out.splitlines() if not f.startswith(ARCHIVE)]


def line_sources(containerfile, words, stages, contexts):
    """The repo paths one Containerfile line takes in, and what cannot be resolved.

    `words` is the split line, `stages` the names of the Containerfile's build
    stages, `contexts` the named `--build-context`s (name to repo path, read from the
    Justfile). Returns (paths, problems): a COPY/ADD gives the paths it takes from the
    Containerfile's directory or from a named context; `--from=<stage>` is another
    stage, not the repo. A bind mount in a RUN line, an unknown `--from` and an
    argument with a variable or URL are problems, never ignored. Other lines give
    ([], []).
    """
    # only COPY, ADD and a bind-mounting RUN can take repo paths in
    match words[0].upper():
        case "RUN" if any(w.startswith("--mount=") and "type=bind" in w for w in words):
            return [], [
                f"{containerfile}: RUN --mount=type=bind is not checked by sanity.py; extend line_sources: {' '.join(words)}"
            ]
        case "COPY" | "ADD":
            # the options of the instruction (--from=..., --chown=...)
            flags = [w for w in words[1:] if w.startswith("--")]

            # the last argument is the destination, the rest are sources
            args = [w for w in words[1:] if not w.startswith("--")][:-1]

            # the --from= flag names another stage or a build context
            source = next(
                (f.removeprefix("--from=") for f in flags if f.startswith("--from=")),
                None,
            )
            # a copy from another stage takes nothing from the repo
            if source in stages:
                return [], []

            # a --from that is neither a stage nor a context cannot be resolved
            if source is not None and source not in contexts:
                return [], [
                    f"{containerfile}: --from={source} is neither a stage nor a --build-context of the Justfile"
                ]
            base = Path(contexts[source]) if source else Path(containerfile).parent

            # variables and URLs cannot be resolved statically: report them
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
    """The repo paths a Containerfile copies in, and the problems found on the way.

    `containerfile` is its path, `contexts` the named build contexts of the Justfile.
    Returns (paths, problems); what cannot be resolved is a problem, never ignored.
    """
    text = Path(containerfile).read_text().replace("\\\n", " ")

    # the instruction lines, split into words; comments and blank lines are not code
    lines = [
        line.split()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    # the names of the stages (`FROM image AS name`), which COPY --from= may name
    stages = {
        w[3]
        for w in lines
        if w[0].upper() == "FROM" and len(w) > 3 and w[2].upper() == "AS"
    }

    # one (paths, problems) pair per line, then joined into two lists
    per_line = [line_sources(containerfile, words, stages, contexts) for words in lines]

    # all paths, then all problems, over every line
    return list(chain.from_iterable(p for p, _ in per_line)), list(
        chain.from_iterable(f for _, f in per_line)
    )


def image_problems(containerfile, contexts):
    """The problems of one image's Containerfile, as a list of messages.

    Every path it copies in must be one of (or inside one of) the image's
    `ci.inputs`, or a change to it would not rebuild the image. A directory without
    inputs is a problem too.
    """
    directory = containerfile.split("/")[0]

    # an unknown image directory is reported instead of crashing the whole check
    try:
        listed = ci.inputs(directory)
    except ValueError as e:
        return [f"{containerfile}: {e}"]
    paths, errors = copied_paths(containerfile, contexts)

    # the unresolved lines, then every copied path that no input covers
    return [
        *errors,
        *(
            f"{containerfile} copies '{p}', which is not in ci.inputs('{directory}'): a change to it would not rebuild the image"
            for p in paths
            if not any(p == i or p.startswith(i + "/") for i in listed)
        ),
    ]


def input_problems(justfile="Justfile"):
    """Every image's `image_problems`, in one list.

    `justfile` is where the named `--build-context`s are read from.
    """
    contexts = dict(
        re.findall(r"--build-context\s+(\w+)=(\S+)", Path(justfile).read_text())
    )

    # the problems of every tracked Containerfile, in one list
    return list(
        chain.from_iterable(
            image_problems(containerfile, contexts)
            for containerfile in tracked("*/Containerfile")
        )
    )


def bash_problem(path):
    """`bash -n <path>` as a problem message, or None when the script parses."""
    return (
        f"bash -n {path}"
        if subprocess.run(["bash", "-n", path]).returncode != 0
        else None
    )


def python_problem(path):
    """A message when the Python file does not compile, else None (implicitly)."""
    # compile only: nothing is run, and an error message names the line
    try:
        compile(Path(path).read_text(), path, "exec")
    except SyntaxError as e:
        return f"{path} does not compile: {e}"


def yaml_problem(path):
    """A message when the workflow or action file (a Path) is not valid YAML."""
    # parse only; a broken file would otherwise give a "workflow file issue"
    try:
        yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return f"{path} does not parse: {e}"


def problems():
    """The problems of every tracked script and every workflow and action file.

    Shell scripts must parse (`bash -n`), Python files must compile, YAML must be
    valid. Returns a list of messages, empty when all is well.
    """
    workflows = sorted(
        [
            *Path(".github/workflows").glob("*.yml"),
            *Path(".github/actions").glob("*/action.yml"),
        ]
    )

    # one message or None per file, then the messages alone
    checked = [
        *map(bash_problem, tracked("*.sh")),
        *map(python_problem, tracked("*.py")),
        *map(yaml_problem, workflows),
    ]

    # keep only the files that had a problem
    return [message for message in checked if message]


def main(argv=None):
    """Run every sanity check from the repository root; log each problem as an error.

    Returns 0 when all checks pass and 1 when there is any problem.
    """
    ci.arg_parser(__doc__).parse_args(argv)
    found = problems() + input_problems()

    # one error line per problem, so a run shows all of them at once
    for p in found:
        ci.log.error(p)

    # a quiet success is easy to miss: say it
    if not found:
        ci.log.info("sanity: all checks passed")
    return 1 if found else 0


# run only as a script, not when a test imports the module
if __name__ == "__main__":
    sys.exit(main())

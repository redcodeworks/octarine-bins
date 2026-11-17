"""Shared helpers of the CI scripts: log lines, step outputs, the retry helper (THE one for network calls), `gh api` reads and the list of an image's build inputs.

Standard library only. Scripts start with `python3 -I`, which keeps the script's own directory off sys.path, so each script adds it back with two lines
(see any script) before it imports this module.
"""
import json
import os
import re
import subprocess
import sys
import time


def error(msg):
    print(f"::error::{msg}", file=sys.stderr)


def warning(msg):
    print(f"::warning::{msg}", file=sys.stderr)


def append_to(env_var, line):
    """Append a line to the file named by an Actions variable (GITHUB_OUTPUT, GITHUB_STEP_SUMMARY), when set."""
    path = os.environ.get(env_var)
    if path:
        with open(path, "a") as f:
            f.write(line + "\n")


def emit(key, value, note=""):
    """Print `key=value` and export it as a step output."""
    print(f"{key}={value}{note}")
    append_to("GITHUB_OUTPUT", f"{key}={value}")


def text(data, limit=300):
    """Bytes or str -> a one-line snippet for a message."""
    if isinstance(data, bytes):
        data = data.decode(errors="replace")
    return data[:limit].replace("\n", " ")


def int_env(name, default):
    value = os.environ.get(name, str(default))
    if not value.isdigit():
        raise SystemExit(f"{name} must be a number, got '{value}'")
    return int(value)


def run(cmd, capture=False, **kw):
    """Run an argument list (never a shell); a missing program is exit status 127."""
    try:
        return subprocess.run(cmd, capture_output=capture, **kw)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(cmd, 127, b"", str(e).encode())


def stderr_of(proc):
    return (proc.stderr or b"").decode(errors="replace")


def retry(cmd, capture=False, ok=None, permanent=None, tries=None, delay=None, **kw):
    """Run `cmd` up to RETRY_TRIES (3) times with a growing pause (RETRY_DELAY * try, default 5 s), for calls to the network that fail for a moment.

    `ok(proc)` says a try succeeded (default: exit status 0); `permanent(proc)` says retrying is pointless (a missing tag, a bad signature). Returns the last
    CompletedProcess; the caller looks at it and decides. Not for commands that must not run twice.
    """
    tries = int_env("RETRY_TRIES", 3) if tries is None else tries
    delay = int_env("RETRY_DELAY", 5) if delay is None else delay
    ok = ok or (lambda p: p.returncode == 0)
    for i in range(1, tries + 1):
        proc = run(cmd, capture, **kw)
        if ok(proc) or (permanent and permanent(proc)):
            return proc
        if i < tries:
            warning(f"try {i}/{tries} failed (exit {proc.returncode}): {' '.join(cmd[:2])} {text(stderr_of(proc), 200)}; retrying in {delay * i} s")
            time.sleep(delay * i)
    return proc


def gh_api(path, paginate=False):
    """A `gh api` GET read, parsed. None when it cannot be read (a 404 is not retried). Paginated reads of lists return the pages joined."""
    cmd = ["gh", "api"] + (["--paginate", "--slurp"] if paginate else []) + [path]
    proc = retry(cmd, capture=True, permanent=lambda p: b"404" in (p.stderr or b"") or b"Not Found" in (p.stderr or b""))
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    return [item for page in data for item in page] if paginate else data


def vkey(version):
    """Natural version order (what `sort -V` gives for plain x.y.z tags): digit runs compare as numbers."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.findall(r"\d+|\D+", version)]


def inputs(directory, trees_only=False):
    """The paths a binaries image is built from, in order: the ONE place that says what rebuilds which image.

    trees_only: only the directories whose content IS the image (binaries-promote compares those). Otherwise also what decides HOW it is built (the Justfile,
    the shared build action, the layout checker, the push script, the image's own caller workflow); binaries_skip hashes all of it. A change to src/lib or the
    builder therefore rebuilds all three images, dx/ only dx, gx/ only gx, installer/ all three. Docs, tests, other workflows and scripts rebuild nothing.
    """
    if directory == "src":
        workflow, trees = "binaries.yml", ["src", "installer"]
    elif directory in ("dx", "gx"):
        workflow, trees = f"binaries-{directory}.yml", [directory, "src/lib", "src/builder.env", "installer"]
    else:
        raise ValueError(f"unknown image directory '{directory}' (src, dx or gx)")
    if trees_only:
        return trees
    return trees + ["Justfile", f".github/workflows/{workflow}", ".github/actions/build-image", ".github/scripts/push_image.py", ".github/scripts/binaries_layout.py"]

"""The ONE shared-helpers file of the CI scripts: log lines, step outputs, the @retry decorator (THE retry for network calls), `gh api` reads and the list of an image's build inputs.

Standard library only. Scripts start with `python3 -I`, which keeps the script's own directory off sys.path, so each script adds it back with two lines
(see any script) before it imports this module.
"""
import functools
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


class NetworkError(Exception):
    """A network command (skopeo copy, podman push, cosign sign) failed; the usual transient error to retry."""


def retry(*, on, attempts=None, delay=None, backoff=True):
    """Decorator for a NETWORK call that may fail for a moment (a registry, cosign's lookups, the GitHub API); put it on nothing else.

    What it retries: only the exception types in `on` (the transient failures). Any other exception, in particular a definitive answer such as `Absent` (the tag is not
    there) or `NotVerified` (the signature is not valid), propagates at once and is never retried: a permanent failure must not be dressed up as a flaky network.
    How long it waits: `attempts` tries (default RETRY_TRIES, 3) with a pause of `delay` seconds (default RETRY_DELAY, 5) times the try number before the next try
    (`backoff=False`: the same pause every time). Both are read when the function is called, so the environment can shorten them for tests.
    What it re-raises: after the last try, the last error itself (same type, so callers catch it as before) with ` (failed N attempts)` added to its message.
    Not for calls that must not run twice. Every failed try prints a ::warning:: line.
    """
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            tries = int_env("RETRY_TRIES", 3) if attempts is None else attempts
            pause = int_env("RETRY_DELAY", 5) if delay is None else delay
            for i in range(1, tries + 1):
                try:
                    return fn(*args, **kwargs)
                except on as e:
                    if i == tries:
                        e.args = (f"{e} (failed {tries} attempts)",)
                        raise
                    wait = pause * i if backoff else pause
                    warning(f"{fn.__name__}: try {i}/{tries} failed ({text(str(e), 200)}); retrying in {wait} s")
                    time.sleep(wait)
        return wrapper
    return decorate


class GhError(Exception):
    pass


class GhNotFound(Exception):
    pass


@retry(on=(GhError,))
def gh_get(path, paginate):
    cmd = ["gh", "api"] + (["--paginate", "--slurp"] if paginate else []) + [path]
    proc = run(cmd, capture=True)
    err = stderr_of(proc)
    if proc.returncode != 0:
        raise (GhNotFound if "404" in err or "Not Found" in err else GhError)(f"gh api {path}: {text(err, 200)}")
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise GhError(f"gh api {path}: the answer is not JSON")
    return [item for page in data for item in page] if paginate else data


def gh_api(path, paginate=False):
    """A `gh api` GET read, parsed; None when it cannot be read (a 404 at once, anything else after the retries). Paginated lists come back joined."""
    try:
        return gh_get(path, paginate)
    except (GhNotFound, GhError):
        return None


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

"""The ONE shared-helpers file of the CI scripts.

It holds logging (`log`, GitHub annotations), the @retry decorator (THE retry for
network calls), the GitHub API through githubkit (`github()`, used directly by the
scripts), version ordering (packaging), step outputs and the list of an image's build
inputs.

The scripts run through uv with the locked dependencies (`uv run --frozen python -I
.github/scripts/<name>.py`). `-I` keeps the script's own directory off sys.path, so
each script adds it back with two lines (see any script) before it imports this
module.
"""

import argparse
import contextlib
import functools
import hashlib
import logging
import os
import re
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from githubkit import GitHub
from githubkit.exception import RequestError, RequestFailed
from githubkit.retry import RETRY_DEFAULT, RetryOption
from packaging.version import InvalidVersion, Version


class Annotations(logging.Handler):
    """Writes log records the way the Actions runner reads them, all to stderr.

    The prefixes are `::error::` and `::warning::`; info is plain. Stdout is reserved
    for a script's result (`result`, `emit`), so a caller that captures stdout
    (`x=$(script.py)`) gets the result and nothing else.
    """

    def emit(self, record):
        """Print one log record to stderr with the annotation prefix of its level."""
        prefix = {logging.ERROR: "::error::", logging.WARNING: "::warning::"}.get(
            record.levelno, ""
        )
        print(f"{prefix}{record.getMessage()}", file=sys.stderr, flush=True)


# every script logs through this one: log.info, log.warning, log.error
log = logging.getLogger("ci")
log.setLevel(logging.INFO)
log.addHandler(Annotations())
log.propagate = False


def arg_parser(doc):
    """An ArgumentParser for a script, described by the first line of its docstring.

    `doc` is the script's `__doc__`; the description is its first line (cut at a
    " Usage:" on that line). The rest of the docstring is not repeated in --help.
    """
    return argparse.ArgumentParser(
        description=doc.strip().splitlines()[0].split(" Usage:")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def append_to(env_var: str, line: str) -> None:
    """Append `line` to the file named by an Actions variable, when it is set.

    `env_var` is GITHUB_OUTPUT or GITHUB_STEP_SUMMARY; outside Actions (and in the
    tests, which set neither) nothing happens.
    """
    # outside Actions the variable is unset and there is nothing to write
    if path := os.environ.get(env_var):
        # append, so earlier steps' lines and this script's several lines all stay
        with Path(path).open("a") as f:
            f.write(f"{line}\n")


def result(line: str) -> None:
    """THE write to stdout: the one result a script hands to its caller.

    The result is a decision line or `key=value`. Everything else (reasons, progress,
    diagnostics) is a log message (`log.info`, ...), which goes to stderr.
    """
    print(line, flush=True)


def emit(key: str, value: str) -> None:
    """Hand out the result `key=value`: on stdout, and as a step output.

    The step output is a line in the file named by GITHUB_OUTPUT, when that is set.
    """
    result(f"{key}={value}")
    append_to("GITHUB_OUTPUT", f"{key}={value}")


def text(data: bytes | str, limit: int = 300) -> str:
    """A one-line snippet of `data` (bytes or str) for a log or error message.

    Cuts at `limit` characters and turns newlines into spaces; undecodable bytes
    become replacement characters.
    """
    decoded = data.decode(errors="replace") if isinstance(data, bytes) else data
    return decoded[:limit].replace("\n", " ")


def int_env(name: str, default: int) -> int:
    """The integer in the environment variable `name`, or `default` when it is unset.

    Raises SystemExit with a message when the variable is set but not a number.
    """
    value = os.environ.get(name, str(default))

    # a typo in a tuning variable should stop the run, not silently use the default
    if not value.isdigit():
        raise SystemExit(f"{name} must be a number, got '{value}'")
    return int(value)


def run(cmd, capture=False, **kw):
    """Run an argument list (never a shell); returns the CompletedProcess.

    `capture` collects stdout and stderr; other keyword arguments go to
    subprocess.run. A missing program is not an exception but exit status 127 with the
    error text on stderr, like a shell would report it.
    """
    # a missing program is an answer (127), so callers handle one kind of failure
    try:
        return subprocess.run(cmd, capture_output=capture, **kw)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(cmd, 127, b"", str(e).encode())


def stderr_of(proc: subprocess.CompletedProcess) -> str:
    """The stderr of a finished process as text ("" when it was not captured)."""
    return (proc.stderr or b"").decode(errors="replace")


def git(*args: str) -> str:
    """The stdout of a git command with `args`, stripped.

    A failing git raises CalledProcessError; callers that expect a failure catch it
    (ask forgiveness, not permission).
    """
    return subprocess.run(
        ["git", *args], check=True, text=True, capture_output=True
    ).stdout.strip()


@contextlib.contextmanager
def group(title: str):
    """A collapsible group in the Actions log: `with ci.group("sign"):`.

    The group markers go to stderr like every log line, and the group is closed even
    when the body raises.
    """
    print(f"::group::{title}", file=sys.stderr, flush=True)

    # close the group however the body ends
    try:
        yield
    finally:
        print("::endgroup::", file=sys.stderr, flush=True)


class NetworkError(Exception):
    """A network command (skopeo copy, podman push, cosign sign) failed.

    This is the usual transient error to retry.
    """


class ServerError(Exception):
    """A web server answered 5xx; transient."""


def retry(*, on, attempts=None, delay=None, backoff=True):
    """Decorator for a NETWORK call that may fail for a moment; put it on nothing else.

    Meant for a registry, cosign's lookups or a download; the GitHub API has its own
    retry decision, see `retry_decision`.

    What it retries: only the exception types in `on` (the transient failures). Any
    other exception, in particular a definitive answer such as `Absent` (the tag is
    not there), `NotVerified` (the signature is not valid) or a 404, propagates at
    once and is never retried: a permanent failure must not be dressed up as a flaky
    network. How long it waits: `attempts` tries (default RETRY_TRIES, 3) with a pause
    of `delay` seconds (default RETRY_DELAY, 5) times the try number before the next
    try (`backoff=False`: the same pause every time). Both are read when the function
    is called, so the environment can shorten them for tests. What it re-raises: after
    the last try, the last error itself (same type, so callers catch it as before)
    with ` (failed N attempts)` added to its message. Not for calls that must not run
    twice. Every failed try logs a warning.
    """

    def decorate(fn):
        """Wrap `fn` with the retry loop (keeps its name and docstring)."""

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            """Call `fn`, trying again after a transient failure; see `retry`."""
            tries = int_env("RETRY_TRIES", 3) if attempts is None else attempts
            pause = int_env("RETRY_DELAY", 5) if delay is None else delay

            # one call per try; a success returns at once
            for i in range(1, tries + 1):
                # only the listed exceptions are transient, the others propagate
                try:
                    return fn(*args, **kwargs)
                except on as e:
                    # the last try gives up: re-raise the error with the count added
                    if i == tries:
                        e.args = (f"{e} (failed {tries} attempts)",)
                        raise
                    wait = pause * i if backoff else pause
                    log.warning(
                        f"{fn.__name__}: try {i}/{tries} failed ({text(str(e), 200)}); retrying in {wait} s"
                    )
                    time.sleep(wait)

        return wrapper

    return decorate


def retry_decision(exc, retry_count):
    """githubkit's retry decision for a failed request (its `auto_retry` hook).

    Returns githubkit's own decision (RETRY_DEFAULT: a rate limit once, a 5xx three
    times) plus transport errors (a dropped connection, a timeout: githubkit raises a
    RequestError that is not a RequestFailed), two more tries with a pause of
    RETRY_DELAY (5) seconds times the try number. An error status such as a 404 is
    never retried.
    """
    # a transport error (not an HTTP answer) is worth two more tries
    if (
        isinstance(exc, RequestError)
        and not isinstance(exc, RequestFailed)
        and retry_count < 2
    ):
        pause = int_env("RETRY_DELAY", 5) * (retry_count + 1)
        # githubkit reads a zero timedelta as "no answer" and waits 60 s, so never zero
        return RetryOption(True, timedelta(seconds=max(pause, 0.001)))
    return RETRY_DEFAULT(exc, retry_count)


def github():
    """The githubkit client of every script.

    The token comes from GITHUB_TOKEN, the server from GITHUB_API_URL (a fake in the
    tests), the retries from `retry_decision`. Its errors are not wrapped: callers see
    githubkit's own exceptions. Keep the returned client in a variable: `gh.rest` only
    holds a weak reference to it.
    """
    return GitHub(
        os.environ.get("GITHUB_TOKEN"),
        base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        timeout=30,
        auto_retry=retry_decision,
    )


def version(tag: str) -> Version:
    """A tag as a comparable version (a leading v is fine).

    Raises ValueError for a tag that is not a version.
    """
    # translate the library's exception into the ValueError callers expect
    try:
        return Version(tag)
    except InvalidVersion as e:
        raise ValueError(f"'{tag}' is not a version") from e


def inputs(directory: str) -> list[str]:
    """The repository paths that make up the image in `directory`.

    A hand-written list, the ONE definition, used by the build skip and by the
    promotion guard alike; the `sanity` copy guard checks it against the
    Containerfiles' COPY lines. Raises ValueError for an unknown directory.

    src: the whole `src/` (Containerfile, components.tsv, builder.env, lib, the
    components). Not inputs: the Justfile, `.github/**`, tests, READMEs and docs (every
    path under an input counts, markdown included); a change to those builds nothing
    (see CLAUDE.md, "Skipping a build").
    """
    # the image directories are a closed list; a new one must be added here
    match directory:
        case "src":
            return ["src"]
        case _:
            raise ValueError(f"unknown image directory '{directory}' (src)")


def has_commit(rev: str) -> bool:
    """Is `rev` a commit that this checkout has?"""
    # git answers a missing object with a failing exit status
    try:
        git("cat-file", "-e", f"{rev}^{{commit}}")
    except subprocess.CalledProcessError:
        return False
    return True


def fetch_commit(rev: str) -> bool:
    """True when commit `rev` is in this checkout, fetching it from origin when missing.

    A shallow CI checkout does not have the commit an older image was built from. A
    failed fetch is not an error here: the answer is just False.
    """
    # try the network only for a commit that is not here yet
    if not has_commit(rev):
        run(["git", "fetch", "-q", "--depth=1", "origin", rev], capture=True)
    return has_commit(rev)


def content_id(commit: str, directory: str) -> str:
    """One value for the content of the image in `directory` at `commit`.

    The sha256 over `git ls-tree -r <commit> -- <inputs>` (modes, blob ids, paths;
    every path under an input counts). Equal values mean the same image content,
    whatever else changed in between. A bad commit raises CalledProcessError, never an
    empty hash.
    """
    out = git("ls-tree", "-r", commit, "--", *inputs(directory))
    return hashlib.sha256(out.encode()).hexdigest()

"""The ONE shared-helpers file of the CI scripts: logging (`log`, GitHub annotations), the @retry decorator (THE retry for network calls), the GitHub API through githubkit
(`github()`, used directly by the scripts), version ordering (packaging), step outputs and the list of an image's build inputs.

The scripts run through uv with the locked dependencies (`uv run --frozen python -I .github/scripts/<name>.py`). `-I` keeps the script's own directory off sys.path, so each
script adds it back with two lines (see any script) before it imports this module.
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
    """Writes log records the way the Actions runner reads them: `::error::` and `::warning::` lines (stderr), plain lines for info (stdout)."""

    def emit(self, record):
        prefix = {logging.ERROR: "::error::", logging.WARNING: "::warning::"}.get(record.levelno, "")
        stream = sys.stderr if record.levelno >= logging.WARNING else sys.stdout
        print(f"{prefix}{record.getMessage()}", file=stream, flush=True)


log = logging.getLogger("ci")  # every script logs through this one: log.info, log.warning, log.error
log.setLevel(logging.INFO)
log.addHandler(Annotations())
log.propagate = False


def arg_parser(doc):
    """An ArgumentParser whose --help description is the first sentence of the script's docstring."""
    return argparse.ArgumentParser(description=doc.strip().splitlines()[0].split(" Usage:")[0], formatter_class=argparse.RawDescriptionHelpFormatter)


def append_to(env_var: str, line: str) -> None:
    """Append a line to the file named by an Actions variable (GITHUB_OUTPUT, GITHUB_STEP_SUMMARY), when set."""
    if path := os.environ.get(env_var):
        with Path(path).open("a") as f:
            print(line, file=f)


def emit(key: str, value: str, note: str = "") -> None:
    """Print `key=value` and export it as a step output."""
    print(f"{key}={value}{note}")
    append_to("GITHUB_OUTPUT", f"{key}={value}")


def text(data: bytes | str, limit: int = 300) -> str:
    """Bytes or str -> a one-line snippet for a message."""
    if isinstance(data, bytes):
        data = data.decode(errors="replace")
    return data[:limit].replace("\n", " ")


def int_env(name: str, default: int) -> int:
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


def stderr_of(proc: subprocess.CompletedProcess) -> str:
    return (proc.stderr or b"").decode(errors="replace")


def git(*args: str) -> str:
    """The stdout of a git command; a failing git raises CalledProcessError (callers that expect a failure catch it: EAFP)."""
    return subprocess.run(["git", *args], check=True, text=True, capture_output=True).stdout.strip()


@contextlib.contextmanager
def group(title: str):
    """A collapsible group in the Actions log: `with ci.group("sign"):`."""
    print(f"::group::{title}", flush=True)
    try:
        yield
    finally:
        print("::endgroup::", flush=True)


class NetworkError(Exception):
    """A network command (skopeo copy, podman push, cosign sign) failed; the usual transient error to retry."""


class ServerError(Exception):
    """A web server answered 5xx; transient."""


def retry(*, on, attempts=None, delay=None, backoff=True):
    """Decorator for a NETWORK call that may fail for a moment (a registry, cosign's lookups, a download; the GitHub API has its own retry decision, see `retry_decision`); put it on nothing else.

    What it retries: only the exception types in `on` (the transient failures). Any other exception, in particular a definitive answer such as `Absent` (the tag is not
    there), `NotVerified` (the signature is not valid) or a 404, propagates at once and is never retried: a permanent failure must not be dressed up as a flaky network.
    How long it waits: `attempts` tries (default RETRY_TRIES, 3) with a pause of `delay` seconds (default RETRY_DELAY, 5) times the try number before the next try
    (`backoff=False`: the same pause every time). Both are read when the function is called, so the environment can shorten them for tests.
    What it re-raises: after the last try, the last error itself (same type, so callers catch it as before) with ` (failed N attempts)` added to its message.
    Not for calls that must not run twice. Every failed try logs a warning.
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
                    log.warning(f"{fn.__name__}: try {i}/{tries} failed ({text(str(e), 200)}); retrying in {wait} s")
                    time.sleep(wait)
        return wrapper
    return decorate


def retry_decision(exc, retry_count):
    """githubkit's own retry decision (RETRY_DEFAULT: a rate limit once, a 5xx three times) plus transport errors (a dropped connection, a timeout: githubkit raises a
    RequestError that is not a RequestFailed), two more tries with a pause of RETRY_DELAY (5) seconds times the try number. An error status such as a 404 is never retried."""
    if isinstance(exc, RequestError) and not isinstance(exc, RequestFailed) and retry_count < 2:
        pause = int_env("RETRY_DELAY", 5) * (retry_count + 1)
        return RetryOption(True, timedelta(seconds=max(pause, 0.001)))  # githubkit reads a zero timedelta as "no answer" and waits 60 s, so never zero
    return RETRY_DEFAULT(exc, retry_count)


def github():
    """The githubkit client of every script: token from GITHUB_TOKEN, server from GITHUB_API_URL (a fake in the tests), the retry decision above. Its errors are not wrapped."""
    return GitHub(os.environ.get("GITHUB_TOKEN"), base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"), timeout=30, auto_retry=retry_decision)


def version(tag: str) -> Version:
    """A tag as a comparable version (a leading v is fine); ValueError for a tag that is not a version."""
    try:
        return Version(tag)
    except InvalidVersion as e:
        raise ValueError(f"'{tag}' is not a version") from e


def inputs(directory: str) -> list[str]:
    """The paths a binaries image is built from, in order: the ONE place that says what rebuilds which image: the directories whose content IS the image, and what decides HOW
    it is built (the Justfile, the shared build action, the layout checker, the push script, the image's own caller workflow). A change to src/lib or the builder therefore
    rebuilds all three images, dx/ only dx, gx/ only gx, installer/ all three. Docs, tests, other workflows and scripts rebuild nothing.
    """
    match directory:
        case "src":
            workflow, trees = "binaries.yml", ["src", "installer"]
        case "dx" | "gx":
            workflow, trees = f"binaries-{directory}.yml", [directory, "src/lib", "src/builder.env", "installer"]
        case _:
            raise ValueError(f"unknown image directory '{directory}' (src, dx or gx)")
    return trees + ["Justfile", f".github/workflows/{workflow}", ".github/actions/build-image", ".github/scripts/push_image.py", ".github/scripts/binaries_layout.py"]


def inputs_tag(directory: str, commit: str = "HEAD") -> str:
    """The registry tag that names the inputs of an image: `inputs-` and 24 hex digits of the sha256 of `git ls-tree -r <commit> -- <inputs>` (file modes, blob ids and paths, so the
    same files give the same tag in any commit). The push step points it at the digest it pushed; binaries_skip and binaries_promote ask the registry which digest these inputs gave.
    A tag, not a label: a label would be part of the digest, and the digest must change only when the image's content does."""
    out = run(["git", "ls-tree", "-r", commit, "--", *inputs(directory)], capture=True).stdout
    return "inputs-" + hashlib.sha256(out).hexdigest()[:24]

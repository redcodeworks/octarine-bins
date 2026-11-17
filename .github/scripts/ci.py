"""The ONE shared-helpers file of the CI scripts: logging (`log`, GitHub annotations), the @retry decorator (THE retry for network calls), the GitHub API through githubkit
(`gh_api`, `gh_request`), version ordering (packaging), step outputs and the list of an image's build inputs.

The scripts run through uv with the locked dependencies (`uv run --frozen python -I .github/scripts/<name>.py`). `-I` keeps the script's own directory off sys.path, so each
script adds it back with two lines (see any script) before it imports this module.
"""
import argparse
import functools
import logging
import os
import re
import subprocess
import sys
import time

from githubkit import GitHub
from githubkit.exception import RequestError, RequestFailed
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel


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


class ServerError(Exception):
    """A web server answered 5xx; transient."""


def retry(*, on, attempts=None, delay=None, backoff=True):
    """Decorator for a NETWORK call that may fail for a moment (a registry, cosign's lookups, the GitHub API, a download); put it on nothing else.

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


class GhTransportError(Exception):
    """The GitHub API could not be reached or the connection broke (githubkit wraps httpx's transport errors in RequestError)."""


class Release(BaseModel):
    """What the scripts read of a GitHub release (untrusted JSON, so validated)."""
    tag_name: str


@retry(on=(GhTransportError,))
def gh_request(method, path, **kwargs):
    """One GitHub REST call through githubkit (token GITHUB_TOKEN, server GITHUB_API_URL, so the tests can use a local fake); returns the githubkit Response.

    githubkit's own auto_retry (the default) already retries a rate limit once and a 5xx three times; it does not retry a transport error, so @retry does that here.
    An error status stays a githubkit `RequestFailed` and is NOT retried (a 404 is a definitive answer).
    """
    client = GitHub(os.environ.get("GITHUB_TOKEN") or None, base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"), timeout=30)
    try:
        return client.request(method, path, **kwargs)
    except RequestFailed:
        raise
    except RequestError as e:
        raise GhTransportError(f"{method} {path}: {e.exc!r}") from e


def gh_api(path, model=None, **params):
    """A GitHub GET read, parsed (and validated as `model`, a pydantic model, when given); None when it cannot be read: a 404 at once, anything else after the retries."""
    try:
        data = gh_request("GET", path, params=params or None).json()
        return model.model_validate(data) if model else data
    except (RequestFailed, RequestError, GhTransportError, ValueError) as e:  # ValueError: not JSON, or pydantic's ValidationError
        if not isinstance(e, RequestFailed):
            log.warning(f"gh api {path}: {text(str(e), 150)}")
        return None


def gh_pages(path, **params):
    """A paginated GET list (100 per page) joined, or None when a page cannot be read."""
    items, page = [], 1
    while True:
        chunk = gh_api(path, per_page=100, page=page, **params)
        if chunk is None:
            return None
        items += chunk
        if len(chunk) < 100:
            return items
        page += 1


def version(tag):
    """A tag as a comparable version (a leading v is fine); ValueError for a tag that is not a version."""
    try:
        return Version(tag)
    except InvalidVersion as e:
        raise ValueError(f"'{tag}' is not a version") from e


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

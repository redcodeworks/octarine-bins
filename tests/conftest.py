"""Fixtures of the tests: the scripts on sys.path and the fake programs on PATH.

The fakes are the skopeo, cosign and gh programs of tests/fakes/ (no network, nothing
is pulled). They are real executable Python files that the scripts under test start as
they would start the real tools, so the tests run the real script into the real
reader. Their behavior comes from the environment: STATE (a directory with one file
per tag, the manifest), MODE, COSIGN_MODE, REV, LOG, COUNT, DB.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "scripts"
FAKES = ROOT / "tests" / "fakes"

# the scripts and their numbered directories (00-lib, ...) are all importable by name
sys.path[:0] = [str(SCRIPTS), *(str(d) for d in sorted(SCRIPTS.glob("[0-9][0-9]-*")))]


def find_script(name):
    """The path of the script `name` (a file name) in its numbered directory.

    Searches .github/scripts and the numbered directories below it; a script that does
    not exist is a mistake in the test, so this raises StopIteration.
    """
    # a script sits in the directory of its pipeline stage
    return next(SCRIPTS.rglob(name))


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """Put tests/fakes in front of PATH and set the fakes' environment.

    Returns the environment as a dict, for subprocesses; it is also applied to this
    process (undone by monkeypatch). STATE is a fresh directory per test (the fake
    registry), LOG and COUNT are files in tmp_path, the retry and read-back waits are
    zero so a failing path does not sleep. GITHUB_OUTPUT is removed, so a script
    writes only stdout unless a test sets it.
    """
    state = tmp_path / "state"
    state.mkdir()
    env = {
        "PATH": f"{FAKES}:{os.environ['PATH']}",
        "RETRY_DELAY": "0",
        "READBACK_WAIT": "0",
        "READBACK_TRIES": "2",
        "STATE": str(state),
        "LOG": str(tmp_path / "log"),
        "COUNT": str(tmp_path / "count"),
    }

    # applied here too, so in-process calls see the same environment as subprocesses
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    return env


def script(name, *args, cwd=None, env=None, extra=None):
    """Run a script of .github/scripts the way a workflow does (`python3 -I`).

    `name` is the script, `args` its arguments, `cwd` the directory to run in. `env`
    and `extra` are added to the inherited environment (`extra` wins). Returns the
    CompletedProcess with text stdout and stderr; the exit status is not checked.
    """
    e = {**os.environ, **(env or {}), **(extra or {})}
    return subprocess.run(
        [sys.executable, "-I", str(find_script(name)), *args],
        cwd=cwd,
        env=e,
        capture_output=True,
        text=True,
    )


def git(repo, *args):
    """Run git in `repo` with a fixed author and committer; returns stdout, stripped.

    A failing git raises CalledProcessError, so a broken test fixture fails loudly.
    """
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    ).stdout.strip()


def load_fake(name):
    """Import a Python file of tests/fakes by path and return the module.

    The directory is not on sys.path: the stub bump_run.py there must not shadow the
    real script of that name.
    """
    spec = importlib.util.spec_from_file_location(name, FAKES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


github_api = load_fake("github_api")

# captured GitHub answers for the tests to adjust
fixture, pull = github_api.fixture, github_api.pull


@pytest.fixture
def github(monkeypatch):
    """Start a fake GitHub API for one test and point the scripts at it.

    The scripts, in this process and in subprocesses, talk to it through
    GITHUB_API_URL; GITHUB_REPOSITORY is o/r and the token is a dummy. Yields the
    FakeGitHub (its `created`, `closed` and `requests` lists are the assertions) and
    stops the server afterwards.
    """
    api = github_api.FakeGitHub()
    monkeypatch.setenv("GITHUB_API_URL", api.url)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("RETRY_DELAY", "0")
    yield api
    api.stop()

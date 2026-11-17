"""Fixtures of the tests: the scripts on sys.path, and the fake skopeo, cosign and gh programs of tests/fakes/ on PATH (no network, nothing is pulled).

The fakes are real executable Python files that the scripts under test start as they would start the real tools, so the tests run the real script into the real
reader. Their behavior comes from the environment: STATE (a directory with one file per tag, the manifest), MODE, COSIGN_MODE, REV, LOG, COUNT, DB.
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
sys.path.insert(0, str(SCRIPTS))

@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """tests/fakes on PATH in front; returns the environment for subprocesses (also applied to this process)."""
    state = tmp_path / "state"
    state.mkdir()
    env = {"PATH": f"{FAKES}:{os.environ['PATH']}", "RETRY_DELAY": "0", "READBACK_WAIT": "0", "READBACK_TRIES": "2", "STATE": str(state),
           "LOG": str(tmp_path / "log"), "COUNT": str(tmp_path / "count")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    return env


def script(name, *args, cwd=None, env=None, extra=None):
    """Run a script of .github/scripts the way a workflow does (`python3 -I`); returns the CompletedProcess with text output."""
    e = {**os.environ, **(env or {}), **(extra or {})}
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / name), *args], cwd=cwd, env=e, capture_output=True, text=True)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


@pytest.fixture
def github(monkeypatch):
    """A fake GitHub API: the scripts (this process and subprocesses) talk to it through GITHUB_API_URL, GITHUB_REPOSITORY is o/r."""
    spec = importlib.util.spec_from_file_location("github_api", FAKES / "github_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    api = module.FakeGitHub()
    monkeypatch.setenv("GITHUB_API_URL", api.url)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("RETRY_DELAY", "0")
    yield api
    api.stop()

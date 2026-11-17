"""Fixtures of the tests: the scripts on sys.path, and fake skopeo, cosign and gh programs on a PATH of their own (no network, nothing is pulled).

The fakes are real executables (Python files) that the scripts under test start as they would start the real tools, so the tests run the real script into the real
reader. Their behavior comes from the environment: STATE (a directory with one file per tag, the manifest), MODE, COSIGN_MODE, REV, LOG, COUNT.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "scripts"
sys.path.insert(0, str(SCRIPTS))

SKOPEO = '''#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path
a = sys.argv[1:]
def log(): open(os.environ["LOG"], "a").write("skopeo " + " ".join(a) + "\\n") if "LOG" in os.environ else None
log()
count = Path(os.environ.get("COUNT", "/nonexistent-count"))
n = (int(count.read_text()) if count.exists() else 0) + 1
if "COUNT" in os.environ: count.write_text(str(n))
mode = os.environ.get("MODE", "ok")
def fail(msg):
    print(msg, file=sys.stderr); sys.exit(1)
state = Path(os.environ["STATE"]) if "STATE" in os.environ else None
if a[0] == "inspect":
    flag, ref = a[1], a[2].removeprefix("docker://")
    if mode == "absent": fail("Error: reading manifest x in ghcr.io/o/i: manifest unknown")
    if mode == "down": fail("Get https://ghcr.io: 503 Service Unavailable")
    if mode == "flaky" and n <= 2: fail("dial tcp: i/o timeout")
    if mode == "empty": sys.exit(0)
    if flag == "--config":
        print(json.dumps({"config": {"Labels": {"org.opencontainers.image.revision": os.environ.get("REV", "")}}}))
    elif state is None:
        sys.stdout.write('{"manifest":1}')
    else:
        f = state / ref.rsplit(":", 1)[-1]
        if not f.exists(): fail("manifest unknown")
        sys.stdout.buffer.write(f.read_bytes())
elif a[0] == "copy" and state is not None:
    shutil.copy(state / "testing", state / "latest")
'''

COSIGN = '''#!/usr/bin/env python3
import os, sys
from pathlib import Path
a = sys.argv[1:]
if "LOG" in os.environ: open(os.environ["LOG"], "a").write("cosign " + " ".join(a) + "\\n")
count = Path(os.environ.get("COUNT", "/nonexistent-count"))
n = (int(count.read_text()) if count.exists() else 0) + 1
if "COUNT" in os.environ: count.write_text(str(n))
mode = os.environ.get("COSIGN_MODE", "ok")
def fail(msg):
    print(msg, file=sys.stderr); sys.exit(1)
if mode == "nosig": fail("Error: no signatures found")
if mode == "badsig": fail("Error: no matching signatures: invalid signature when validating ASN.1 encoded signature")
if mode == "down": fail("Error: context deadline exceeded")
if mode == "flaky" and n <= 1: fail("Error: GET https://ghcr.io/v2/: TLS handshake timeout")
'''


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """A PATH with fake skopeo and cosign in front; returns the environment for subprocesses (also applied to this process)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in (("skopeo", SKOPEO), ("cosign", COSIGN)):
        (bin_dir / name).write_text(source)
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "RETRY_DELAY": "0", "READBACK_WAIT": "0", "READBACK_TRIES": "2", "STATE": str(state),
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

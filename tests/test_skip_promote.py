"""The build skip and the promotion, with real git repositories, a fake registry (a directory of tags) and fake skopeo and cosign.

The registry state is what push_image.py leaves: `testing` is the digest of the last signed build, and the tag ci.inputs_tag(dir) of the inputs of every build points at its digest.
"""
import hashlib
import os
from pathlib import Path

import pytest

import ci
from conftest import git, script

IMG = "ghcr.io/redcodeworks/octarine-bins"


@pytest.fixture
def repo(tmp_path):
    """A repository with the paths the inputs name; returns (path, revision of the first commit)."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    files = {"src/f": "one", "src/lib/x": "l1", "src/builder.env": "b", "dx/f": "d1", "installer/install.sh": "i1", ".github/actions/build-image/action.yml": "ba", "README": "doc",
             ".github/workflows/binaries.yml": "wf", ".github/workflows/binaries-dx.yml": "wf", ".github/workflows/test.yml": "t", ".github/scripts/push_image.py": "p",
             ".github/scripts/binaries_layout.py": "l", "Justfile": "j"}
    for name, content in files.items():
        (r / name).parent.mkdir(parents=True, exist_ok=True)
        (r / name).write_text(content)
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "one")
    return r, git(r, "rev-parse", "HEAD")


def change(r, name, content):
    (r / name).write_text(content)
    git(r, "commit", "-q", "-am", f"change {name}")


def inputs_tag(r, directory, commit="HEAD"):
    cwd = os.getcwd()
    os.chdir(r)
    try:
        return ci.inputs_tag(directory, commit)
    finally:
        os.chdir(cwd)


def built(r, state, manifest="tested", directories=("src", "dx")):
    """What the push step records for the inputs of HEAD: the inputs tag of each image names the manifest."""
    for d in directories:
        (state / inputs_tag(r, d)).write_text(manifest)


def skip(repo_path, rev, directory="src", **extra):
    out = repo_path.parent / "gho"
    out.write_text("")
    p = script("binaries_skip.py", IMG, directory, cwd=repo_path, extra={"GITHUB_OUTPUT": str(out), **extra})
    return p, out.read_text().strip()


@pytest.fixture
def tested(fakes, repo):
    """testing is the build of the first commit's inputs."""
    state = Path(fakes["STATE"])
    (state / "testing").write_text("tested")
    built(repo[0], state)


@pytest.mark.parametrize("edit,directory,expected", [
    (None, "src", "skip=true"),
    ("README", "src", "skip=true"),                      # docs: no rebuild
    (".github/workflows/test.yml", "src", "skip=true"),  # another workflow: no rebuild
    ("src/f", "src", "skip=false"),
    ("Justfile", "src", "skip=false"),                   # the build arguments
    ("src/f", "dx", "skip=true"),                        # only the base image's own files
    ("src/lib/x", "dx", "skip=false"),                   # shared helpers
    ("src/lib/x", "src", "skip=false"),
    ("dx/f", "src", "skip=true"),
    ("dx/f", "dx", "skip=false"),
    ("installer/install.sh", "dx", "skip=false"),        # every image carries the installer
    ("installer/install.sh", "src", "skip=false"),
])
def test_skip_rule_per_image(fakes, tested, repo, edit, directory, expected):
    r, rev = repo
    if edit:
        change(r, edit, "changed")
    p, out = skip(r, rev, directory)
    assert p.returncode == 0 and out == expected, p.stderr


def test_skip_false_when_testing_does_not_verify(fakes, tested, repo):
    r, rev = repo
    p, out = skip(r, rev, COSIGN_MODE="nosig")
    assert p.returncode == 0 and out == "skip=false"


def test_tooling_only_change_builds_once_and_then_skips_although_the_digest_did_not_move(fakes, tested, repo):
    """The loop this guards against: a Justfile change rebuilds to the SAME digest, and every later push of those inputs must skip, not rebuild forever."""
    r, rev = repo
    change(r, "Justfile", "new tooling")
    assert skip(r, rev)[1] == "skip=false"
    built(r, Path(fakes["STATE"]))  # the push step: the new inputs tag names the (unchanged) digest of testing
    assert skip(r, rev)[1] == "skip=true"


def test_skip_false_when_the_inputs_gave_another_digest_than_testing(fakes, tested, repo):
    r, rev = repo
    built(r, Path(fakes["STATE"]), manifest="older build")  # the push finished, signing or the move of testing did not
    assert skip(r, rev)[1] == "skip=false"


def test_skip_false_when_there_is_no_testing_image_yet(fakes, repo):
    r, rev = repo
    p, out = skip(r, rev)
    assert p.returncode == 0 and out == "skip=false"


def test_skip_fails_instead_of_guessing_when_the_registry_is_down(fakes, tested, repo):
    r, rev = repo
    p, _ = skip(r, rev, MODE="down")
    assert p.returncode == 1


@pytest.fixture
def promoted(fakes, repo):
    """testing = the build of the first commit's inputs; HEAD is a merge commit with the same files."""
    r, rev = repo
    git(r, "commit", "-q", "--amend", "-m", "merge commit, same files")
    Path(fakes["STATE"], "testing").write_text("tested")
    built(r, Path(fakes["STATE"]))
    return r, rev, Path(fakes["STATE"]), Path(fakes["LOG"])


def promote(r, rev, directory="src", **extra):
    out = r.parent / "pgho"
    out.write_text("")
    p = script("binaries_promote.py", IMG, directory, cwd=r, extra={"GITHUB_OUTPUT": str(out), **extra})
    return p, out.read_text().split()


def copies(log):
    return sum(1 for line in log.read_text().splitlines() if line.startswith("skopeo copy")) if log.exists() else 0


def test_promote_retags_testing_to_latest_and_verifies_latest(promoted):
    r, rev, state, log = promoted
    p, out = promote(r, rev)
    assert p.returncode == 0 and "changed=true" in out and copies(log) == 1, p.stderr
    assert (state / "latest").read_bytes() == (state / "testing").read_bytes()
    assert f"cosign verify --key cosign.pub {IMG}:latest" in log.read_text()
    assert f"digest=sha256:{hashlib.sha256(b'tested').hexdigest()}" in out


def test_promote_twice_does_nothing_the_second_time(promoted):
    r, rev, _, log = promoted
    promote(r, rev)
    p, out = promote(r, rev)
    assert p.returncode == 0 and "changed=false" in out and copies(log) == 1


def test_promote_moves_an_older_latest(promoted):
    r, rev, state, log = promoted
    (state / "latest").write_text("older")
    p, out = promote(r, rev)
    assert p.returncode == 0 and (state / "latest").read_text() == "tested"


def test_promote_an_unsigned_testing_fails_and_changes_nothing(promoted):
    r, rev, state, log = promoted
    (state / "latest").write_text("older")
    p, _ = promote(r, rev, COSIGN_MODE="nosig")
    assert p.returncode == 1 and copies(log) == 0 and (state / "latest").read_text() == "older"


def test_promote_without_a_testing_image_fails(promoted):
    r, rev, state, log = promoted
    (state / "testing").unlink()
    p, _ = promote(r, rev)
    assert p.returncode == 1 and "build on testing first" in p.stderr and copies(log) == 0


@pytest.mark.parametrize("edit,directory,passes", [
    ("README", "src", True),             # docs only
    ("src/f", "src", False),             # testing was built from other inputs
    ("Justfile", "src", False),          # the tooling is an input too: it must have been built on testing first
    ("src/f", "dx", True),               # dx does not read src/ itself
    ("src/lib/x", "dx", False),          # but it reads the shared helpers
    ("src/lib/x", "src", False),
    ("dx/f", "src", True),
    ("installer/install.sh", "dx", False),
])
def test_promote_compares_the_inputs_of_the_image(promoted, edit, directory, passes):
    r, rev, state, log = promoted
    change(r, edit, "changed")
    p, _ = promote(r, rev, directory)
    if passes:
        assert p.returncode == 0, p.stderr
    else:
        assert p.returncode == 1 and "testing moved on" in p.stderr and copies(log) == 0


def test_promote_survives_registry_lag_on_cosign(promoted, tmp_path):
    r, rev, state, log = promoted
    p, out = promote(r, rev, LAG_FILE=str(tmp_path / "lag"), COSIGN_LAG="3")
    assert p.returncode == 0 and "changed=true" in out, p.stderr

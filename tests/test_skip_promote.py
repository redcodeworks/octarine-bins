"""The build skip and the promotion, with real git repositories, a fake registry (a directory of tags) and fake skopeo and cosign."""
import hashlib
from pathlib import Path

import pytest

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


def skip(repo_path, rev, directory="src", **extra):
    out = repo_path.parent / "gho"
    out.write_text("")
    p = script("binaries_skip.py", IMG, directory, cwd=repo_path, extra={"REV": rev, "GITHUB_OUTPUT": str(out), **extra})
    return p, out.read_text().strip()


@pytest.fixture
def tested(fakes):
    Path(fakes["STATE"], "testing").write_text("tested")


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
    """testing = the first commit's build; HEAD is a merge commit with the same trees."""
    r, rev = repo
    git(r, "commit", "-q", "--amend", "-m", "merge commit, same trees")
    Path(fakes["STATE"], "testing").write_text("tested")
    return r, rev, Path(fakes["STATE"]), Path(fakes["LOG"])


def promote(r, rev, directory="src", **extra):
    out = r.parent / "pgho"
    out.write_text("")
    p = script("binaries_promote.py", IMG, directory, cwd=r, extra={"REV": rev, "GITHUB_OUTPUT": str(out), **extra})
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
    ("src/f", "src", False),             # testing was built from another src/
    ("src/f", "dx", True),               # dx does not read src/ itself
    ("src/lib/x", "dx", False),          # but it reads the shared helpers
    ("src/lib/x", "src", False),
    ("dx/f", "src", True),
    ("installer/install.sh", "dx", False),
])
def test_promote_compares_the_trees_of_the_image(promoted, edit, directory, passes):
    r, rev, state, log = promoted
    change(r, edit, "changed")
    p, _ = promote(r, rev, directory)
    if passes:
        assert p.returncode == 0, p.stderr
    else:
        assert p.returncode == 1 and "testing moved on" in p.stderr and copies(log) == 0

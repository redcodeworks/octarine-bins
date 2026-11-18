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
    files = {
        "src/Containerfile": "c",
        "src/f": "one",
        "src/lib/assemble.sh": "a1",
        "src/lib/stage.sh": "s1",
        "src/NOTES.md": "n",
        "src/builder.env": "b",
        "README.md": "doc",
        "Justfile": "j",
        "tests/t.py": "t",
        ".github/workflows/binaries.yml": "wf",
        ".github/actions/build-image/action.yml": "ba",
        ".github/scripts/push_image.py": "p",
        ".github/scripts/binaries_layout.py": "l",
    }
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
    p = script(
        "binaries_skip.py",
        IMG,
        directory,
        cwd=repo_path,
        extra={"REV": rev, "GITHUB_OUTPUT": str(out), **extra},
    )
    return p, out.read_text().strip()


@pytest.fixture
def tested(fakes):
    Path(fakes["STATE"], "testing").write_text("tested")


# (edit, image directory, the image's content unchanged?): one table for the build skip and the promotion guard
RULES = [
    (None, "src", True),
    # tooling and docs: no image content
    ("Justfile", "src", True),
    (".github/workflows/binaries.yml", "src", True),
    (".github/actions/build-image/action.yml", "src", True),
    (".github/scripts/push_image.py", "src", True),
    (".github/scripts/binaries_layout.py", "src", True),
    ("tests/t.py", "src", True),
    ("README.md", "src", True),
    # content: everything under src/
    ("src/f", "src", False),
    ("src/Containerfile", "src", False),
    ("src/lib/stage.sh", "src", False),
    ("src/lib/assemble.sh", "src", False),
    ("src/builder.env", "src", False),
    ("src/NOTES.md", "src", False),
]


@pytest.mark.parametrize("edit,directory,unchanged", RULES)
def test_skip_rule_per_image(fakes, tested, repo, edit, directory, unchanged):
    r, rev = repo
    if edit:
        change(r, edit, "changed")
    p, out = skip(r, rev, directory)
    assert p.returncode == 0 and out == f"skip={str(unchanged).lower()}", p.stderr


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
    p = script(
        "binaries_promote.py",
        IMG,
        directory,
        cwd=r,
        extra={"REV": rev, "GITHUB_OUTPUT": str(out), **extra},
    )
    return p, out.read_text().split()


def copies(log):
    return (
        sum(
            1 for line in log.read_text().splitlines() if line.startswith("skopeo copy")
        )
        if log.exists()
        else 0
    )


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
    assert (
        p.returncode == 1
        and copies(log) == 0
        and (state / "latest").read_text() == "older"
    )


def test_promote_without_a_testing_image_fails(promoted):
    r, rev, state, log = promoted
    (state / "testing").unlink()
    p, _ = promote(r, rev)
    assert (
        p.returncode == 1 and "build on testing first" in p.stderr and copies(log) == 0
    )


@pytest.mark.parametrize("edit,directory,unchanged", RULES)
def test_promote_compares_the_content_of_the_image(
    promoted, edit, directory, unchanged
):
    r, rev, state, log = promoted
    if edit:
        change(r, edit, "changed")
    p, _ = promote(r, rev, directory)
    if unchanged:
        assert p.returncode == 0, p.stderr
    else:
        assert p.returncode == 1 and "testing moved on" in p.stderr and copies(log) == 0


def test_the_revision_label_follows_the_last_content_change(fakes, tested, repo):
    """A content change builds (label = that commit); tooling changes after it skip and still promote; without the new label a content change never skips or promotes."""
    r, rev = repo
    change(r, "src/f", "new content")
    built = git(r, "rev-parse", "HEAD")
    assert (
        skip(r, rev)[1] == "skip=false"
    )  # the old label names a commit with other content: build
    assert (
        skip(r, built)[1] == "skip=true"
    )  # the build stamps the label with this commit
    change(r, "Justfile", "tooling")
    change(r, "README.md", "docs")
    assert (
        skip(r, built)[1] == "skip=true"
    )  # tooling and docs: the label stays at the content commit and still matches
    assert promote(r, built)[0].returncode == 0
    assert (
        promote(r, rev)[0].returncode == 1
    )  # a stale label (the image of the previous content) is refused


def test_promote_prints_the_real_error_of_a_failed_verification(promoted):
    r, rev, state, log = promoted
    p, _ = promote(r, rev, COSIGN_MODE="down")
    assert (
        p.returncode == 1
        and "read error, not a bad signature" in p.stderr
        and "not signed with cosign.pub" not in p.stderr
        and copies(log) == 0
    )


def test_push_image_does_not_overwrite_an_existing_dated_tag(fakes):
    """The fake registry already has the tag; there is no fake podman, so a push attempt would fail: the script must keep the existing digest and not push."""
    Path(fakes["STATE"], "20261117-abcdef0").write_text("first build")
    out = Path(fakes["STATE"]).parent / "gho"
    out.write_text("")
    p = script(
        "push_image.py",
        "local:tag",
        IMG,
        "20261117-abcdef0",
        extra={"GITHUB_OUTPUT": str(out)},
    )
    assert p.returncode == 0 and "not overwriting" in p.stderr, p.stderr
    assert (
        out.read_text().strip()
        == f"digests=sha256:{hashlib.sha256(b'first build').hexdigest()}"
    )
    assert "podman" not in Path(fakes["LOG"]).read_text()


def test_skip_decides_although_the_registry_lags_on_the_digest_read(
    fakes, tested, repo, tmp_path
):
    r, rev = repo
    p, out = skip(r, rev, DIGEST_LAG="2", DIGEST_LAG_FILE=str(tmp_path / "dlag"))
    assert p.returncode == 0 and out == "skip=true", p.stderr


def test_promote_survives_registry_lag_on_the_digest_read_and_on_cosign(
    promoted, tmp_path
):
    r, rev, state, log = promoted
    p, out = promote(
        r,
        rev,
        DIGEST_LAG="2",
        DIGEST_LAG_FILE=str(tmp_path / "dlag"),
        LAG_FILE=str(tmp_path / "lag"),
        COSIGN_LAG="3",
    )
    assert p.returncode == 0 and "changed=true" in out, p.stderr


def test_check_mode_accepts_what_promote_accepts_and_writes_nothing(promoted):
    r, rev, state, log = promoted
    p = script("binaries_promote.py", "--check", IMG, "src", cwd=r, extra={"REV": rev})
    assert p.returncode == 0 and copies(log) == 0 and not (state / "latest").exists(), (
        p.stderr
    )


def test_check_mode_refuses_a_stale_label_like_promote(promoted):
    r, rev, state, log = promoted
    change(r, "src/f", "changed")
    p = script("binaries_promote.py", "--check", IMG, "src", cwd=r, extra={"REV": rev})
    assert p.returncode == 1 and "testing moved on" in p.stderr and copies(log) == 0

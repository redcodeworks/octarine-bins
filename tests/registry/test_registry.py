"""The readers that keep "could not read" apart from "is not there".

That is registry.py and the scripts around it, tested with a fake skopeo and cosign.
"""

import hashlib

import pytest

import ci
import registry
from conftest import script


def calls(fakes):
    """How many times the fake skopeo or cosign was started (its COUNT file)."""
    return int(open(fakes["COUNT"]).read())


def test_absent_is_not_retried(fakes, monkeypatch):
    """An explicit "does not exist" is definitive: Absent after one call."""
    monkeypatch.setenv("MODE", "absent")

    # the typed answer comes through at once
    with pytest.raises(registry.Absent):
        registry.read("raw", "ghcr.io/o/i:t")
    assert calls(fakes) == 1


def test_transient_failures_are_retried(fakes, monkeypatch):
    """A flaky registry fails twice, then answers: three calls, the bytes returned."""
    monkeypatch.setenv("MODE", "flaky")
    open(fakes["STATE"] + "/t", "w").write('{"manifest":1}')
    assert registry.read("raw", "ghcr.io/o/i:t") == b'{"manifest":1}'
    assert calls(fakes) == 3


@pytest.mark.parametrize("mode", ["down", "empty"])
def test_a_registry_that_stays_down_is_a_read_error_never_absent(
    fakes, monkeypatch, mode
):
    """A registry that is down or answers nothing is a ReadError after three tries."""
    monkeypatch.setenv("MODE", mode)

    # never mistaken for "absent"
    with pytest.raises(registry.ReadError):
        registry.read("raw", "ghcr.io/o/i:t")
    assert calls(fakes) == 3


def test_digest_is_the_sha256_of_the_manifest_bytes(fakes):
    """The digest of a tag is the sha256 of the manifest bytes the registry holds."""
    (open(fakes["STATE"] + "/testing", "w")).write("some manifest")
    assert (
        registry.digest("ghcr.io/o/i", "testing")
        == "sha256:" + hashlib.sha256(b"some manifest").hexdigest()
    )


def test_cosign_not_verified_is_definitive_and_not_retried(fakes, monkeypatch):
    """A missing signature is NotVerified after one call."""
    monkeypatch.setenv("COSIGN_MODE", "nosig")

    # the verdict is final, so no retry
    with pytest.raises(registry.NotVerified):
        registry.cosign_check(["--key", "k", "ghcr.io/o/i@sha256:x"])
    assert calls(fakes) == 1


def test_cosign_that_cannot_complete_is_a_read_error_after_five_tries(
    fakes, monkeypatch
):
    """A cosign that cannot finish is a ReadError after five tries, never a verdict."""
    monkeypatch.setenv("COSIGN_MODE", "down")

    # the message says it was a read error and how often it was tried
    with pytest.raises(
        registry.ReadError, match="NOT a bad signature.*failed 5 attempts"
    ):
        registry.cosign_check(["--key", "k", "x"])
    assert calls(fakes) == 5


def test_cosign_flaky_then_verified(fakes, monkeypatch):
    """A flaky cosign that eventually verifies is simply a success."""
    monkeypatch.setenv("COSIGN_MODE", "flaky")
    registry.cosign_check(["--key", "k", "x"])


def test_settled_fails_naming_unreadable_when_the_registry_stays_down(
    fakes, monkeypatch
):
    """The read-back error names what it saw: "unreadable" for a downed registry."""
    monkeypatch.setenv("MODE", "down")

    # the final error says what the tag showed
    with pytest.raises(registry.ReadError, match="is unreadable"):
        registry.settled("ghcr.io/o/i", "t", "sha256:" + "a" * 64)


def test_settled_passes_when_the_tag_shows_the_digest(fakes):
    """A tag that shows the expected digest passes the read-back."""
    (open(fakes["STATE"] + "/t", "w")).write("m")
    registry.settled("ghcr.io/o/i", "t", "sha256:" + hashlib.sha256(b"m").hexdigest())


def test_a_failing_copy_is_retried_and_then_raised(fakes, monkeypatch):
    """A copy whose write always fails is tried three times, then the error raised."""
    monkeypatch.setenv("MODE", "down")

    # a `write` that always raises NetworkError (a lambda cannot raise, a generator can)
    monkeypatch.setattr(
        registry,
        "write",
        lambda cmd: (_ for _ in ()).throw(ci.NetworkError("copy failed")),
    )

    # the retry decorator re-raises with the attempt count
    with pytest.raises(ci.NetworkError, match="failed 3 attempts"):
        registry.copy("a@sha256:x", "a:testing")


def test_sign_image_signs_verifies_copies_and_verifies_the_tag(fakes, tmp_path):
    """sign_image.py signs, verifies, copies to testing, verifies the tag, in order."""
    digest = "sha256:" + hashlib.sha256(b"tested").hexdigest()
    (open(fakes["STATE"] + "/testing", "w")).write("tested")
    p = script(
        "sign_image.py",
        "ghcr.io/o/i",
        "20261117-abc1234",
        digest,
        cwd=tmp_path,
        extra={"COSIGN_PRIVATE_KEY": "k"},
    )
    log = open(fakes["LOG"]).read().splitlines()
    assert p.returncode == 0, p.stderr

    # the first two words of each fake call: program and subcommand
    order = [l.split()[0] + " " + l.split()[1] for l in log]

    # sign first, verify second, the testing copy, and a last verify through the tag
    assert (
        order[0] == "cosign sign"
        and order[1] == "cosign verify"
        and "skopeo copy" in order
        and order[-1] == "cosign verify"
    )

    # testing moves only after the signature verified
    assert order.index("skopeo copy") > order.index("cosign verify")


def test_sign_image_stops_before_moving_testing_when_the_signature_does_not_verify(
    fakes, tmp_path
):
    """When the signature does not verify, testing is not moved and the exit is 1."""
    p = script(
        "sign_image.py",
        "ghcr.io/o/i",
        "v",
        "sha256:" + "a" * 64,
        cwd=tmp_path,
        extra={"COSIGN_MODE": "nosig"},
    )
    assert p.returncode == 1 and "skopeo copy" not in open(fakes["LOG"]).read()


def test_a_tag_that_cosign_cannot_see_yet_is_a_read_error_never_not_verified(
    fakes, monkeypatch
):
    """A tag cosign cannot see yet is a read error, not a signature verdict.

    A real failure of a sign job: right after the copy, cosign said 'image tag not
    found ... MANIFEST_UNKNOWN' (registry lag).
    """
    monkeypatch.setenv("LAG_FILE", fakes["LOG"] + ".lag")
    monkeypatch.setenv("COSIGN_LAG", "99")

    # the lag is retried, then reported as a read error naming the registry's words
    with pytest.raises(registry.ReadError, match="MANIFEST_UNKNOWN"):
        registry.cosign_check(["--key", "k", "ghcr.io/o/i:testing"])
    assert int(open(fakes["LOG"] + ".lag").read()) == 5


def test_sign_image_waits_out_the_lag_before_it_verifies(fakes, tmp_path, monkeypatch):
    """With two lagged verifies sign_image.py still succeeds: the lag is retried."""
    (open(fakes["STATE"] + "/testing", "w")).write("tested")
    digest = "sha256:" + hashlib.sha256(b"tested").hexdigest()
    p = script(
        "sign_image.py",
        "ghcr.io/o/i",
        "v",
        digest,
        cwd=tmp_path,
        extra={
            "COSIGN_PRIVATE_KEY": "k",
            "LAG_FILE": str(tmp_path / "lag"),
            "COSIGN_LAG": "2",
        },
    )
    assert p.returncode == 0, p.stderr

    # two verifies lagged, the two real ones passed
    assert int((tmp_path / "lag").read_text()) == 4


def test_a_signature_written_a_moment_ago_may_take_a_few_tries_but_a_wrong_one_still_fails(
    fakes, monkeypatch
):
    """A fresh signature may need a few tries, but a wrong one still fails."""
    monkeypatch.setenv("LAG_FILE", fakes["LOG"] + ".lag")
    monkeypatch.setenv("COSIGN_UNSIGNED_FIRST", "2")

    # no signatures found twice, then verified
    registry.cosign_check_fresh(["--key", "k", "x"])
    monkeypatch.setenv("COSIGN_MODE", "nosig")

    # a permanently missing signature ends as NotVerified
    with pytest.raises(registry.NotVerified):
        registry.cosign_check_fresh(["--key", "k", "x"])


def test_a_digest_the_registry_just_named_is_retried_when_it_answers_unknown(
    fakes, monkeypatch
):
    """A digest just taken from a tag is retried when the registry says "unknown".

    A real failure of a build: reading the config of image@digest answered manifest
    unknown for a digest that was just taken from the tag.
    """
    monkeypatch.setenv("DIGEST_LAG_FILE", fakes["LOG"] + ".dlag")
    monkeypatch.setenv("DIGEST_LAG", "2")

    # two lagged answers, then the config: three reads
    assert b"revision" in registry.read_by_digest(
        "config", "ghcr.io/o/i", "sha256:" + "a" * 64
    )
    assert int(open(fakes["LOG"] + ".dlag").read()) == 3
    monkeypatch.setenv("DIGEST_LAG", "99")

    # a lag that never ends is finally reported as it is
    with pytest.raises(registry.Absent, match="manifest unknown"):
        registry.read_by_digest("config", "ghcr.io/o/i", "sha256:" + "a" * 64)

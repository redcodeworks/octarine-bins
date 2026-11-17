"""The readers that keep "could not read" apart from "is not there" (registry.py and the scripts around it), with fake skopeo and cosign."""
import hashlib

import pytest

import ci
import registry
from conftest import script


def calls(fakes):
    return int(open(fakes["COUNT"]).read())


def test_absent_is_not_retried(fakes, monkeypatch):
    monkeypatch.setenv("MODE", "absent")
    with pytest.raises(registry.Absent):
        registry.read("raw", "ghcr.io/o/i:t")
    assert calls(fakes) == 1


def test_transient_failures_are_retried(fakes, monkeypatch):
    monkeypatch.setenv("MODE", "flaky")
    open(fakes["STATE"] + "/t", "w").write('{"manifest":1}')
    assert registry.read("raw", "ghcr.io/o/i:t") == b'{"manifest":1}'
    assert calls(fakes) == 3


@pytest.mark.parametrize("mode", ["down", "empty"])
def test_a_registry_that_stays_down_is_a_read_error_never_absent(fakes, monkeypatch, mode):
    monkeypatch.setenv("MODE", mode)
    with pytest.raises(registry.ReadError):
        registry.read("raw", "ghcr.io/o/i:t")
    assert calls(fakes) == 3


def test_digest_is_the_sha256_of_the_manifest_bytes(fakes):
    (open(fakes["STATE"] + "/testing", "w")).write("some manifest")
    assert registry.digest("ghcr.io/o/i", "testing") == "sha256:" + hashlib.sha256(b"some manifest").hexdigest()


def test_cosign_not_verified_is_definitive_and_not_retried(fakes, monkeypatch):
    monkeypatch.setenv("COSIGN_MODE", "nosig")
    with pytest.raises(registry.NotVerified):
        registry.cosign_check(["--key", "k", "ghcr.io/o/i@sha256:x"])
    assert calls(fakes) == 1


def test_cosign_that_cannot_complete_is_a_read_error_after_three_tries(fakes, monkeypatch):
    monkeypatch.setenv("COSIGN_MODE", "down")
    with pytest.raises(registry.ReadError, match="NOT a bad signature.*failed 3 attempts"):
        registry.cosign_check(["--key", "k", "x"])
    assert calls(fakes) == 3


def test_cosign_flaky_then_verified(fakes, monkeypatch):
    monkeypatch.setenv("COSIGN_MODE", "flaky")
    registry.cosign_check(["--key", "k", "x"])


def test_settled_fails_naming_unreadable_when_the_registry_stays_down(fakes, monkeypatch):
    monkeypatch.setenv("MODE", "down")
    with pytest.raises(registry.ReadError, match="is unreadable"):
        registry.settled("ghcr.io/o/i", "t", "sha256:" + "a" * 64)


def test_settled_passes_when_the_tag_shows_the_digest(fakes):
    (open(fakes["STATE"] + "/t", "w")).write("m")
    registry.settled("ghcr.io/o/i", "t", "sha256:" + hashlib.sha256(b"m").hexdigest())


def test_a_failing_copy_is_retried_and_then_raised(fakes, monkeypatch):
    monkeypatch.setenv("MODE", "down")
    monkeypatch.setattr(registry, "write", lambda cmd: (_ for _ in ()).throw(ci.NetworkError("copy failed")))
    with pytest.raises(ci.NetworkError, match="failed 3 attempts"):
        registry.copy("a@sha256:x", "a:testing")


def test_sign_image_signs_verifies_copies_and_verifies_the_tag(fakes, tmp_path):
    digest = "sha256:" + hashlib.sha256(b"tested").hexdigest()
    (open(fakes["STATE"] + "/testing", "w")).write("tested")
    p = script("sign_image.py", "ghcr.io/o/i", "20261117-abc1234", digest, cwd=tmp_path, extra={"COSIGN_PRIVATE_KEY": "k"})
    log = open(fakes["LOG"]).read().splitlines()
    assert p.returncode == 0, p.stderr
    order = [l.split()[0] + " " + l.split()[1] for l in log]
    assert order[0] == "cosign sign" and order[1] == "cosign verify" and "skopeo copy" in order and order[-1] == "cosign verify"
    assert order.index("skopeo copy") > order.index("cosign verify")  # testing moves only after the signature verified


def test_sign_image_stops_before_moving_testing_when_the_signature_does_not_verify(fakes, tmp_path):
    p = script("sign_image.py", "ghcr.io/o/i", "v", "sha256:" + "a" * 64, cwd=tmp_path, extra={"COSIGN_MODE": "nosig"})
    assert p.returncode == 1 and "skopeo copy" not in open(fakes["LOG"]).read()

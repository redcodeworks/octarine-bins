"""The readers that keep "could not read" apart from "is not there" (registry.py and the scripts around it), with fake skopeo and cosign."""
import hashlib

import pytest

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


def test_cosign_not_verified_is_exit_5_at_once(fakes):
    p = script("cosign_check.py", "--key", "k", "ghcr.io/o/i@sha256:x", extra={"COSIGN_MODE": "nosig"})
    assert p.returncode == 5 and calls(fakes) == 1


def test_cosign_that_cannot_complete_is_exit_4_after_three_tries(fakes):
    p = script("cosign_check.py", "--key", "k", "ghcr.io/o/i@sha256:x", extra={"COSIGN_MODE": "down"})
    assert p.returncode == 4 and calls(fakes) == 3 and "NOT a bad signature" in p.stderr


def test_cosign_flaky_then_verified(fakes):
    assert script("cosign_check.py", "--key", "k", "x", extra={"COSIGN_MODE": "flaky"}).returncode == 0


def test_settled_fails_naming_unreadable_when_the_registry_stays_down(fakes):
    p = script("registry_settled.py", "ghcr.io/o/i", "t", "sha256:" + "a" * 64, extra={"MODE": "down"})
    assert p.returncode == 1 and "is unreadable" in p.stderr


def test_settled_passes_when_the_tag_shows_the_digest(fakes):
    (open(fakes["STATE"] + "/t", "w")).write("m")
    want = "sha256:" + hashlib.sha256(b"m").hexdigest()
    assert script("registry_settled.py", "ghcr.io/o/i", "t", want).returncode == 0


def test_retry_cli_passes_the_exit_status_of_the_last_try(fakes):
    p = script("retry.py", "-n", "2", "--", "python3", "-c", "import sys; sys.exit(7)")
    assert p.returncode == 7 and "failed 2 times" in p.stderr

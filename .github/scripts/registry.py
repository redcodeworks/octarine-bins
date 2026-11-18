"""Every registry and signature call of the pipeline (skopeo, cosign, podman push).

Each network call runs under `@ci.retry`, so "could not be read" is never taken for
"is not there".

Typed answers: `Absent` = skopeo said explicitly that the tag or repository does not
exist; `NotVerified` = cosign said explicitly the signature is missing or invalid.
Both are definitive, so they are NOT in a retry's `on` list and surface at once.
`ReadError` = anything else (network, TLS, 5xx, rate limit): retried, and re-raised
after the last try. `ci.NetworkError` is the same for the write calls (copy, push,
sign). Callers decide what each means: a missing candidate may mean "build instead",
an unreadable registry must never silently mean that.
"""

import hashlib
import re
import subprocess
import tempfile
from pathlib import Path

import ci


class Absent(Exception):
    """skopeo said explicitly that the tag or repository does not exist."""


class ReadError(Exception):
    """Anything else that went wrong with a read: network, TLS, 5xx, rate limit."""


class NotVerified(Exception):
    """cosign said explicitly that the signature is missing or invalid."""


ABSENT = re.compile(
    r"manifest unknown|name unknown|MANIFEST_UNKNOWN|NAME_UNKNOWN|not found|\b404\b|no such manifest",
    re.I,
)
# What cosign says when the signature itself is missing or wrong. A missing IMAGE or
# TAG ("image tag not found", MANIFEST_UNKNOWN) is NOT here: right after a write the
# registry may not show it yet (read-after-write lag seen on GHCR), so that is a read
# error, retried.
UNSIGNED = re.compile(
    r"no signatures found|no matching signatures|invalid signature|signature verification failed|none of the expected identities",
    re.I,
)


def inspect_once(mode, ref, linux_amd64=False):
    """ONE `skopeo inspect --raw|--config` of an image reference; returns the bytes.

    `mode` is "raw" (the manifest) or "config"; `ref` is the reference without the
    docker:// prefix; `linux_amd64` asks for that platform's config of a multi-arch
    tag. No retry here (`read` adds it). Raises Absent when skopeo says the image does
    not exist and ReadError for every other failure, empty output included.
    """
    flag = {"raw": "--raw", "config": "--config"}[mode]
    platform = (
        ["--override-os", "linux", "--override-arch", "amd64"] if linux_amd64 else []
    )

    proc = ci.run(
        ["skopeo", "inspect", *platform, flag, f"docker://{ref}"], capture=True
    )

    # success needs a clean exit and an answer; empty output is a failed read
    if proc.returncode == 0 and proc.stdout:
        return proc.stdout
    err = ci.stderr_of(proc)

    # an explicit "does not exist" is a definitive answer, the rest may be transient
    if ABSENT.search(err):
        raise Absent(f"{ref} does not exist ({ci.text(err, 500)})")
    raise ReadError(
        f"could not read {ref} (a read error, NOT a missing image): {ci.text(err)}"
    )


@ci.retry(on=(ReadError,))
def read(mode, ref, linux_amd64=False):
    """`skopeo inspect` with retries: the raw manifest or the image config as bytes.

    Arguments as `inspect_once`. Raises Absent at once (a definitive answer) and
    ReadError after the last try.
    """
    return inspect_once(mode, ref, linux_amd64)


@ci.retry(on=(ReadError, Absent), attempts=5)
def read_by_digest(mode, image, digest):
    """Read an image the registry itself named a moment ago (a digest from a tag).

    It cannot be absent, so an "absent" answer is registry lag and is retried like a
    read error (5 tries, 5 to 20 s apart), then raised as it is (Absent or
    ReadError). `mode` as in `inspect_once`; returns the bytes.
    """
    return inspect_once(mode, f"{image}@{digest}")


def digest(image, tag):
    """The manifest digest of image:tag, as `sha256:<hex>`.

    It is the sha256 of the raw manifest bytes, so every tag that points at the same
    content has the same digest. Raises Absent when the tag does not exist and
    ReadError when the registry cannot be read.
    """
    return "sha256:" + hashlib.sha256(read("raw", f"{image}:{tag}")).hexdigest()


def settled(image, tag, want):
    """Read image:tag back after a write until it shows the digest `want`.

    The registry may answer right after a push or copy with the PREVIOUS manifest of
    the tag (seen on GHCR 2026-11-14), or fail the read for a moment. Reads
    READBACK_TRIES (6) times, READBACK_WAIT (5) seconds apart; raises ReadError naming
    what it saw (a digest, absent, unreadable) after the last look.
    """

    def shown():
        """What the tag shows now: its digest, or "absent", or "unreadable"."""
        # every answer, including a failure, is a value to compare with `want`
        try:
            return (
                "sha256:"
                + hashlib.sha256(inspect_once("raw", f"{image}:{tag}")).hexdigest()
            )
        except Absent:
            return "absent"
        except ReadError:
            return "unreadable"

    @ci.retry(
        attempts=ci.int_env("READBACK_TRIES", 6),
        delay=ci.int_env("READBACK_WAIT", 5),
        backoff=False,
        on=(ReadError,),
    )
    def read_back():
        """One look at the tag; a different digest is a ReadError, which retries."""
        got = shown()

        # anything but the expected digest means the write is not visible yet
        if got != want:
            raise ReadError(f"{image}:{tag} is {got}, expected {want}")

    read_back()


@ci.retry(on=(ReadError,), attempts=5)
def cosign_check(args):
    """`cosign verify <args>`: returns None when the signature verifies.

    `args` are cosign's arguments (the key and the image reference). Raises
    NotVerified at once when cosign said the signature is missing or wrong, and
    ReadError after the retries when it could not complete or the image or tag is
    not visible yet (5 tries, 5 to 20 s apart).
    """
    proc = ci.run(["cosign", "verify", *args], capture=True)

    # a clean exit is a verified signature
    if proc.returncode == 0:
        return
    err = ci.stderr_of(proc)

    # an explicit "no/invalid signature" is final, anything else may be transient
    if UNSIGNED.search(err):
        raise NotVerified(f"not verified: {ci.text(err)}")
    raise ReadError(
        f"could not complete the verification (a read error, NOT a bad signature): {ci.text(err)}"
    )


def write(cmd):
    """Run one network command that changes something (an argument list).

    Its stdout passes through. A nonzero exit raises ci.NetworkError with the program
    name, the exit code and the start of its stderr. The decorated calls below, which
    add the retries, are the only users.
    """
    proc = ci.run(cmd, stderr=subprocess.PIPE)

    # a failed write is a network error to retry, never ignored
    if proc.returncode != 0:
        raise ci.NetworkError(
            f"{' '.join(cmd[:2])} failed (exit {proc.returncode}): {ci.text(ci.stderr_of(proc), 200)}"
        )


@ci.retry(on=(ci.NetworkError,))
def copy(source, target):
    """Registry-side copy of `source` to `target` (image references, no docker://).

    `skopeo copy --all --preserve-digests`: the same digest and the same signature, no
    rebuild. Retried; raises ci.NetworkError after the last try.
    """
    write(
        [
            "skopeo",
            "copy",
            "--all",
            "--preserve-digests",
            f"docker://{source}",
            f"docker://{target}",
        ]
    )


@ci.retry(on=(ci.NetworkError,))
def push(local_image, target):
    """`podman push` of `local_image` to `target`; returns the manifest digest.

    The digest is the one podman reports through --digestfile (a push is idempotent
    for the same content). Retried; raises ci.NetworkError when podman fails or
    reports no valid digest.
    """
    # the digest file lives in a scratch directory that is removed afterwards
    with tempfile.TemporaryDirectory() as tmp:
        digestfile = Path(tmp) / "digest"
        write(["podman", "push", f"--digestfile={digestfile}", local_image, target])
        value = digestfile.read_text().strip() if digestfile.exists() else ""

    # no digest means the push cannot be signed or checked: treat it as a failure
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ci.NetworkError(f"podman push gave no digest for {target} ('{value}')")
    return value


@ci.retry(on=(ci.NetworkError,), attempts=5)
def sign(reference):
    """`cosign sign` of `reference` (image@digest) with the key in COSIGN_PRIVATE_KEY.

    Uses the old bundle format, see
    https://github.com/containers/container-libs/issues/388.
    Retried (5 tries); raises ci.NetworkError after the last try.
    """
    write(
        [
            "cosign",
            "sign",
            "-y",
            "--new-bundle-format=false",
            "--use-signing-config=false",
            "--key",
            "env://COSIGN_PRIVATE_KEY",
            reference,
        ]
    )


@ci.retry(on=(ReadError, NotVerified), attempts=5)
def cosign_check_fresh(args):
    """`cosign verify` of something written seconds ago (a signature, a copied tag).

    The registry may not show it yet, so "no signatures found" is retried too (5 tries,
    5 to 20 s apart) before it counts as NotVerified. A wrong signature fails the same
    way, only later. Arguments and errors as `cosign_check`.
    """
    return cosign_check.__wrapped__(args)

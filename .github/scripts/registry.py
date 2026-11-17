"""Registry and signature reads that keep "could not be read" apart from "is not there": skopeo and cosign behind the retry helper of ci.py.

Absent (exit 3 in the scripts): skopeo said explicitly that the tag or repository does not exist; nothing is retried. ReadError (exit 4): anything else (network,
TLS, 5xx, rate limit), retried, still failing after the last try. NotVerified (exit 5): cosign said explicitly the signature is not there or not valid. Callers decide
what each means: a missing candidate may mean "build instead", an unreadable registry must never silently mean that.
"""
import hashlib
import re
import time

import ci


class Absent(Exception):
    pass


class ReadError(Exception):
    pass


class NotVerified(Exception):
    pass


ABSENT = re.compile(r"manifest unknown|name unknown|MANIFEST_UNKNOWN|NAME_UNKNOWN|not found|\b404\b|no such manifest", re.I)
UNSIGNED = re.compile(r"no signatures found|no matching signatures|invalid signature|signature verification failed|MANIFEST_UNKNOWN|NAME_UNKNOWN|manifest unknown"
                      r"|not found|none of the expected identities", re.I)


def read(mode, ref, tries=None):
    """`skopeo inspect --raw` (mode raw) or `--config` (mode config) of an image reference without docker://; returns the bytes."""
    flag = {"raw": "--raw", "config": "--config"}[mode]
    proc = ci.retry(["skopeo", "inspect", flag, f"docker://{ref}"], capture=True, tries=tries,
                    ok=lambda p: p.returncode == 0 and bool(p.stdout), permanent=lambda p: ABSENT.search(ci.stderr_of(p)) is not None)
    if proc.returncode == 0 and proc.stdout:
        return proc.stdout
    err = ci.stderr_of(proc)
    if ABSENT.search(err):
        raise Absent(f"{ref} does not exist ({ci.text(err, 200)})")
    raise ReadError(f"could not read {ref} (a read error, NOT a missing image): {ci.text(err)}")


def digest(image, tag, tries=None):
    """The manifest digest of image:tag: the sha256 of the raw manifest bytes, so it is the same for every tag that points at the same content."""
    return "sha256:" + hashlib.sha256(read("raw", f"{image}:{tag}", tries)).hexdigest()


def settled(image, tag, want, tries=None, wait=None):
    """Read a tag back after a write until it shows the expected digest: the registry may answer right after a push or copy with the PREVIOUS manifest of
    the tag (seen on GHCR 2026-11-14), or fail the read for a moment. Raises ReadError after the last read, naming what it saw (the digest, absent, unreadable)."""
    tries = ci.int_env("READBACK_TRIES", 6) if tries is None else tries
    wait = ci.int_env("READBACK_WAIT", 5) if wait is None else wait
    got = ""
    for i in range(1, tries + 1):
        try:
            got = digest(image, tag, tries=1)
        except Absent:
            got = "absent"
        except ReadError:
            got = "unreadable"
        if got == want:
            return
        if i < tries:
            time.sleep(wait)
    raise ReadError(f"{image}:{tag} is {got}, expected {want} (after {tries} reads)")


def cosign_check(args, tries=None):
    """`cosign verify <args>`: returns when verified, raises NotVerified (cosign said no, not retried) or ReadError (could not complete, retried)."""
    proc = ci.retry(["cosign", "verify", *args], capture=True, tries=tries, permanent=lambda p: UNSIGNED.search(ci.stderr_of(p)) is not None)
    if proc.returncode == 0:
        return
    err = ci.stderr_of(proc)
    if UNSIGNED.search(err):
        raise NotVerified(f"not verified: {ci.text(err)}")
    raise ReadError(f"could not complete the verification (a read error, NOT a bad signature): {ci.text(err)}")

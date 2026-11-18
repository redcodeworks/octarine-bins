#!/usr/bin/env python3
"""Promote the binaries: retag the TESTED, signed <image>:testing to :latest.

No rebuild, same digest, same signature.
Usage: binaries_promote.py <registry-image> <dir>

Runs on main only (binaries.yml); nothing else moves `latest`. dir: src; writes
changed=true|false and digest=<digest of latest> to GITHUB_OUTPUT. Fails, and changes
nothing, unless `testing` exists, verifies with cosign.pub and was built from exactly
the binaries being promoted: the image's own content (ci.inputs) at the commit named
by its label org.opencontainers.image.revision must equal that of this checkout (HEAD,
the merge commit of the promotion PR). The same definition of content as
binaries_skip.py: a push that did not change it leaves `testing` at an older revision,
and tooling or docs never make the image stale. A running or failed build on testing,
or a testing that moved on, fails the promotion: re-run it when the tip's build is
green. Afterwards the tag is read back until it shows the digest, and the signature is
verified through `latest`. Run it in a git checkout (the revision is fetched when
missing).

`--check` runs only those checks (the same code) and writes nothing: the promotion
PR's `Promotable:` jobs use it, on the PR's merge result, which is what main will
contain.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "00-lib"))
import ci  # noqa: E402
import registry  # noqa: E402


class Fail(Exception):
    """A promotion that must not happen; the message says why (and how to fix it)."""


def acceptable(image, directory):
    """Decide whether `image:testing` may be promoted; returns its digest.

    One function for the real promote and for `--check`, so the rule exists once.
    `directory` is the image directory (src). Raises Fail, with the reason, when
    testing cannot be read, is not signed with cosign.pub, has no valid revision
    label, or was built from content that differs from this checkout's.
    """
    # a testing tag that cannot be read (or is absent) cannot be promoted
    try:
        tested = registry.digest(image, "testing")
    except (registry.Absent, registry.ReadError) as e:
        raise Fail(f"{image}:testing could not be read ({e}); build on testing first")

    # only a build signed with this repository's key is promoted
    try:
        registry.cosign_check(["--key", "cosign.pub", f"{image}@{tested}"])
    except registry.NotVerified:
        raise Fail(f"{image}@{tested} is not signed with cosign.pub")
    except registry.ReadError as e:
        raise Fail(
            f"{image}@{tested} could not be verified (a read error, not a bad signature): {e}"
        )

    # the image config holds the revision label the content comparison needs
    try:
        config = json.loads(registry.read_by_digest("config", image, tested))
    except registry.Absent, registry.ReadError, ValueError:
        raise Fail(f"the config of {image}@{tested} could not be read")
    rev = ((config.get("config") or {}).get("Labels") or {}).get(
        "org.opencontainers.image.revision"
    ) or ""

    # without a revision label there is no commit to compare with
    if not re.fullmatch(r"[0-9a-f]{40}", rev):
        raise Fail(f"{image}:testing has no valid revision label ('{rev}')")

    # the commit must be here to compare its content with HEAD
    if not ci.fetch_commit(rev):
        raise Fail(f"the revision {rev} of {image}:testing cannot be fetched")
    built, this = ci.content_id(rev, directory), ci.content_id("HEAD", directory)

    # testing must hold exactly the content being promoted (not an older or newer one)
    if built != this:
        raise Fail(
            f"testing was built from {rev}, whose content differs from this "
            f"checkout's ({' '.join(ci.inputs(directory))}): the build on testing is "
            "running, failed, or testing moved on; re-run promote when the tip's "
            "build is green"
        )
    return tested


def latest_digest(image):
    """The digest `latest` shows now, or None when there is no `latest` yet.

    Raises Fail when the registry cannot be read, which is never taken for "absent".
    """
    # "absent" is a normal first-promotion answer, a read error is not
    try:
        return registry.digest(image, "latest")
    except registry.Absent:
        return None
    except registry.ReadError as e:
        raise Fail(f"{image}:latest could not be read ({e})")


def promote(image, directory):
    """Retag the tested `image:testing` to `latest` and verify the result.

    Writes `changed=true|false` and `digest=<digest of latest>` as the result (stdout
    and GITHUB_OUTPUT). Does nothing but report when latest already is the tested
    digest. Raises Fail when the promotion is not acceptable, the copy fails, or the
    tag does not show the digest and verify after the copy.
    """
    tested = acceptable(image, directory)
    current = latest_digest(image)

    # nothing to do when a rerun finds latest already promoted
    if current == tested:
        ci.log.info(f"latest already is the tested image {tested}: nothing to promote")
        ci.emit("changed", "false")
        ci.emit("digest", tested)
        return

    # the retag is a registry-side copy: same digest, same signature, no rebuild
    try:
        registry.copy(f"{image}@{tested}", f"{image}:latest")
    except ci.NetworkError as e:
        raise Fail(str(e))

    # read the tag back and verify through it (the tag was copied seconds ago)
    try:
        registry.settled(image, "latest", tested)
        registry.cosign_check_fresh(["--key", "cosign.pub", f"{image}:latest"])
    except registry.ReadError as e:
        raise Fail(str(e))
    except registry.NotVerified:
        raise Fail(f"{image}:latest does not verify after the retag")
    ci.log.info(f"promoted {image}:latest {current or '<none>'} -> {tested}")
    ci.emit("changed", "true")
    ci.emit("digest", tested)


def main(argv=None):
    """Promote testing to latest, or with --check only decide whether it would be.

    Returns 0 on success and 1 when the promotion is refused (the reason is logged as
    an error).
    """
    ap = ci.arg_parser(__doc__)
    ap.add_argument("image", help="registry image without a tag (registry/owner/name)")
    ap.add_argument("dir", choices=["src"], help="the image directory")
    ap.add_argument(
        "--check",
        action="store_true",
        help="only decide whether a promotion would be accepted; retags and writes nothing",
    )
    args = ap.parse_args(argv)

    # a refusal is a Fail with its reason, shown as the step's error
    try:
        # --check decides and writes nothing; otherwise promote for real
        if args.check:
            ci.log.info(
                f"{args.image}:testing would be promoted ({acceptable(args.image, args.dir)})"
            )
        else:
            promote(args.image, args.dir)
    except Fail as e:
        ci.log.error(str(e))
        return 1
    return 0


# run only as a script, not when a test imports the module
if __name__ == "__main__":
    sys.exit(main())

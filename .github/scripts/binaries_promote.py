#!/usr/bin/env python3
"""Promote the binaries: retag the TESTED, signed <image>:testing to :latest (no rebuild, same digest, same signature). Usage: binaries_promote.py <registry-image> <dir>

Runs on main only (binaries.yml); nothing else moves `latest`. dir: src; writes changed=true|false and digest=<digest of latest>
to GITHUB_OUTPUT. Fails, and changes nothing, unless `testing` exists, verifies with cosign.pub and was built from exactly the binaries being promoted: the image's own
content (ci.inputs) at the commit named by its label org.opencontainers.image.revision must equal that of this checkout (HEAD, the merge commit of the promotion PR).
The same definition of content as binaries_skip.py: a push that did not change it leaves `testing` at an older revision, and tooling or docs never make the image stale. A running or failed build on testing, or a testing that moved on, fails the promotion: re-run it when the tip's build is green.
Afterwards the tag is read back until it shows the digest, and the signature is verified through `latest`. Run it in a git checkout (the revision is fetched when missing).
`--check` runs only those checks (the same code) and writes nothing: the promotion PR's `Promotable:` jobs use it, on the PR's merge result, which is what main will contain.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


class Fail(Exception):
    pass


def acceptable(image, directory):
    """The decision of a promotion, shared by the real promote and `--check` (no second copy of the rule): returns the tested digest or raises Fail."""
    try:
        tested = registry.digest(image, "testing")
    except (registry.Absent, registry.ReadError) as e:
        raise Fail(f"{image}:testing could not be read ({e}); build on testing first")
    try:
        registry.cosign_check(["--key", "cosign.pub", f"{image}@{tested}"])
    except registry.NotVerified:
        raise Fail(f"{image}@{tested} is not signed with cosign.pub")
    except registry.ReadError as e:
        raise Fail(
            f"{image}@{tested} could not be verified (a read error, not a bad signature): {e}"
        )
    try:
        config = json.loads(registry.read_by_digest("config", image, tested))
    except registry.Absent, registry.ReadError, ValueError:
        raise Fail(f"the config of {image}@{tested} could not be read")
    rev = ((config.get("config") or {}).get("Labels") or {}).get(
        "org.opencontainers.image.revision"
    ) or ""
    if not re.fullmatch(r"[0-9a-f]{40}", rev):
        raise Fail(f"{image}:testing has no valid revision label ('{rev}')")
    if not ci.fetch_commit(rev):
        raise Fail(f"the revision {rev} of {image}:testing cannot be fetched")
    built, this = ci.content_id(rev, directory), ci.content_id("HEAD", directory)
    if built != this:
        raise Fail(
            f"testing was built from {rev}, whose content differs from this checkout's ({' '.join(ci.inputs(directory))}): the build on testing is running, failed, "
            "or testing moved on; re-run promote when the tip's build is green"
        )
    return tested


def promote(image, directory):
    tested = acceptable(image, directory)
    try:
        current = registry.digest(image, "latest")
    except registry.Absent:
        current = None
    except registry.ReadError as e:
        raise Fail(f"{image}:latest could not be read ({e})")
    if current == tested:
        ci.log.info(f"latest already is the tested image {tested}: nothing to promote")
        ci.emit("changed", "false")
        ci.emit("digest", tested)
        return
    try:
        registry.copy(f"{image}@{tested}", f"{image}:latest")
    except ci.NetworkError as e:
        raise Fail(str(e))
    try:
        registry.settled(image, "latest", tested)
        registry.cosign_check_fresh(
            ["--key", "cosign.pub", f"{image}:latest"]
        )  # the tag was copied seconds ago
    except registry.ReadError as e:
        raise Fail(str(e))
    except registry.NotVerified:
        raise Fail(f"{image}:latest does not verify after the retag")
    ci.log.info(f"promoted {image}:latest {current or '<none>'} -> {tested}")
    ci.emit("changed", "true")
    ci.emit("digest", tested)


def main(argv=None):
    ap = ci.arg_parser(__doc__)
    ap.add_argument("image", help="registry image without a tag (registry/owner/name)")
    ap.add_argument("dir", choices=["src"], help="the image directory")
    ap.add_argument(
        "--check",
        action="store_true",
        help="only decide whether a promotion would be accepted; retags and writes nothing",
    )
    args = ap.parse_args(argv)
    try:
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


if __name__ == "__main__":
    sys.exit(main())

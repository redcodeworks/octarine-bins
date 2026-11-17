#!/usr/bin/env python3
"""Promote the binaries: retag the TESTED, signed <image>:testing to :latest (no rebuild, same digest, same signature). Usage: binaries_promote.py <registry-image> <dir>

Runs on main only (binaries.yml, binaries-dx.yml, binaries-gx.yml); nothing else moves `latest`. dir: src, dx or gx; writes changed=true|false and digest=<digest of latest>
to GITHUB_OUTPUT. Fails, and changes nothing, unless `testing` exists, verifies with cosign.pub and IS the build of exactly the inputs being promoted: the tag
ci.inputs_tag(dir) of this checkout (HEAD, the merge commit of the promotion PR) names the digest of `testing`. A change to docs or tests changes no inputs, so it does not make the
image stale; a running or failed build on testing, or a testing that moved on, fails the promotion: re-run it when the tip's build is green.
Afterwards the tag is read back until it shows the digest, and the signature is verified through `latest`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


class Fail(Exception):
    pass


def promote(image, directory):
    try:
        tested = registry.digest(image, "testing")
    except (registry.Absent, registry.ReadError) as e:
        raise Fail(f"{image}:testing could not be read ({e}); build on testing first")
    try:
        registry.cosign_check(["--key", "cosign.pub", f"{image}@{tested}"])
    except (registry.NotVerified, registry.ReadError):
        raise Fail(f"{image}@{tested} is not signed with cosign.pub")
    tag = ci.inputs_tag(directory)
    try:
        built = registry.digest(image, tag)
    except registry.Absent:
        raise Fail(f"no build of these inputs ({image}:{tag} does not exist): the build on testing is running, failed, or testing moved on; re-run promote when the tip's build is green")
    except registry.ReadError as e:
        raise Fail(f"{image}:{tag} could not be read ({e})")
    if built != tested:
        raise Fail(f"testing ({tested}) is not the build of these inputs ({tag} is {built}): the build on testing is running, failed, or testing moved on; "
                   "re-run promote when the tip's build is green")
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
        registry.cosign_check_fresh(["--key", "cosign.pub", f"{image}:latest"])  # the tag was copied seconds ago
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
    ap.add_argument("dir", choices=["src", "dx", "gx"], help="the image directory")
    args = ap.parse_args(argv)
    try:
        promote(args.image, args.dir)
    except Fail as e:
        ci.log.error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

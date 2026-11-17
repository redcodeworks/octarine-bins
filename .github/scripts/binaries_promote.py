#!/usr/bin/env python3
"""Promote the binaries: retag the TESTED, signed <image>:testing to :latest (no rebuild, same digest, same signature). Usage: binaries_promote.py <registry-image> <dir>

Runs on main only (binaries.yml, binaries-dx.yml, binaries-gx.yml); nothing else moves `latest`. dir: src, dx or gx; writes changed=true|false and digest=<digest of latest>
to GITHUB_OUTPUT. Fails, and changes nothing, unless `testing` exists, verifies with cosign.pub and was built from exactly the binaries being promoted: the image's own
trees (ci.inputs(dir, trees_only=True)) at the commit named by its label org.opencontainers.image.revision must equal those of this checkout (HEAD, the merge commit of
the promotion PR). Only the trees are compared: a push that did not change the inputs of the build leaves `testing` at an older revision (binaries_skip.py), and a change to
docs or tests does not make the image stale. A running or failed build on testing, or a testing that moved on, fails the promotion: re-run it when the tip's build is green.
Afterwards the tag is read back until it shows the digest, and the signature is verified through `latest`. Run it in a git checkout (the revision is fetched when missing).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


class Fail(Exception):
    pass


def tree_ids(commit, trees):
    """The git tree id of each input at a commit, or None when one is missing."""
    try:
        return [ci.git("rev-parse", "--verify", "-q", f"{commit}:{t}") for t in trees]
    except subprocess.CalledProcessError:
        return None


def promote(image, directory):
    trees = ci.inputs(directory, trees_only=True)
    try:
        tested = registry.digest(image, "testing")
    except (registry.Absent, registry.ReadError) as e:
        raise Fail(f"{image}:testing could not be read ({e}); build on testing first")
    try:
        registry.cosign_check(["--key", "cosign.pub", f"{image}@{tested}"])
    except (registry.NotVerified, registry.ReadError):
        raise Fail(f"{image}@{tested} is not signed with cosign.pub")
    try:
        config = json.loads(registry.read("config", f"{image}@{tested}"))
    except (registry.Absent, registry.ReadError, ValueError):
        raise Fail(f"the config of {image}@{tested} could not be read")
    rev = ((config.get("config") or {}).get("Labels") or {}).get("org.opencontainers.image.revision") or ""
    if not re.fullmatch(r"[0-9a-f]{40}", rev):
        raise Fail(f"{image}:testing has no valid revision label ('{rev}')")
    built = tree_ids(rev, trees)
    if built is None:
        ci.run(["git", "fetch", "-q", "--depth=1", "origin", rev], capture=True)
        built = tree_ids(rev, trees)
    this = tree_ids("HEAD", trees)
    if this is None:
        raise Fail(f"cannot read {' '.join(trees)} of HEAD")
    if built != this:
        raise Fail(f"testing was built from {rev} ({' '.join(trees)}: {built or 'unknown'}), this checkout has {this}: the build on testing is running, failed, "
                   "or testing moved on; re-run promote when the tip's build is green")
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
        registry.cosign_check(["--key", "cosign.pub", f"{image}:latest"])
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

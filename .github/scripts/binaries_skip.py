#!/usr/bin/env python3
"""Decide whether a push to testing needs a build. Usage: binaries_skip.py <registry-image> <dir>   (dir: src, dx or gx; run in a git checkout; env GITHUB_OUTPUT)

NO build when the inputs of the build (ci.inputs: the image's directory, for dx and gx the shared src/lib and builder, the Justfile, the shared build action, the
image's caller workflow, push_image.py, binaries_layout.py) equal those of the commit the current `testing` image was built from (its label
org.opencontainers.image.revision) and that image verifies with cosign.pub. Prints and exports skip=true|false. Absent testing, an unreadable revision, other
inputs, or an image that does not verify (a signature of an old key, a failed signing) all mean skip=false: build. Only a READ ERROR (registry or cosign could not be
asked) fails the script (exit 1): a build must not be skipped or forced on a guess. The job that calls this always succeeds, so the required check names always report.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


def inputs_hash(commit, paths):
    """sha256 of `git ls-tree -r <commit> -- <paths>`: one value for everything the build reads at that commit."""
    out = ci.run(["git", "ls-tree", "-r", commit, "--", *paths], capture=True).stdout
    return hashlib.sha256(out).hexdigest()


def has_commit(rev):
    try:
        ci.git("cat-file", "-e", f"{rev}^{{commit}}")
    except subprocess.CalledProcessError:
        return False
    return True


def decide(image, directory):
    """Returns (skip, reason)."""
    paths = ci.inputs(directory)
    try:
        tested = registry.digest(image, "testing")
    except registry.Absent:
        return False, "no testing image yet"
    try:
        config = json.loads(registry.read_by_digest("config", image, tested))
    except (registry.Absent, ValueError) as e:
        raise registry.ReadError(f"the config of {image}@{tested} could not be read ({e})")
    rev = ((config.get("config") or {}).get("Labels") or {}).get("org.opencontainers.image.revision") or ""
    if not re.fullmatch(r"[0-9a-f]{40}", rev):
        return False, "testing has no revision label"
    if not has_commit(rev):
        ci.run(["git", "fetch", "-q", "--depth=1", "origin", rev], capture=True)
    if not has_commit(rev):
        return False, f"the revision {rev} of testing cannot be fetched"
    if inputs_hash(rev, paths) != inputs_hash("HEAD", paths):
        return False, f"the build inputs differ from those of {rev}"
    try:
        registry.cosign_check(["--key", "cosign.pub", f"{image}@{tested}"])
    except registry.NotVerified:
        return False, "testing does not verify with cosign.pub"
    return True, f"testing is the signed build of {rev}, same inputs"


def main(argv=None):
    ap = ci.arg_parser(__doc__)
    ap.add_argument("image", help="registry image without a tag (registry/owner/name)")
    ap.add_argument("dir", choices=["src", "dx", "gx"], help="the image directory")
    args = ap.parse_args(argv)
    try:
        skip, reason = decide(args.image, args.dir)
    except registry.ReadError as e:
        ci.log.error(f"{e}: not deciding on a guess")
        return 1
    ci.emit("skip", str(skip).lower(), f" ({reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

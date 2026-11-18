#!/usr/bin/env python3
"""Decide whether a push to testing needs a build. Usage: binaries_skip.py <registry-image> <dir>   (dir: src; run in a git checkout; env GITHUB_OUTPUT)

NO build when the CONTENT of the image (ci.inputs: what its Containerfile copies in; the Justfile, workflows, actions, scripts, tests and docs are not content) at HEAD
equals that at the commit the current `testing` image was built from (its label org.opencontainers.image.revision) and that image verifies with cosign.pub. Prints and exports skip=true|false. Absent testing, an unreadable revision, other
inputs, or an image that does not verify (a signature of an old key, a failed signing) all mean skip=false: build. Only a READ ERROR (registry or cosign could not be
asked) fails the script (exit 1): a build must not be skipped or forced on a guess. The job that calls this always succeeds, so the required check names always report.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


def decide(image, directory):
    """Returns (skip, reason)."""
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
    if not ci.fetch_commit(rev):
        return False, f"the revision {rev} of testing cannot be fetched"
    if ci.content_id(rev, directory) != ci.content_id("HEAD", directory):
        return False, f"the build inputs differ from those of {rev}"
    try:
        registry.cosign_check(["--key", "cosign.pub", f"{image}@{tested}"])
    except registry.NotVerified:
        return False, "testing does not verify with cosign.pub"
    return True, f"testing is the signed build of {rev}, same inputs"


def main(argv=None):
    ap = ci.arg_parser(__doc__)
    ap.add_argument("image", help="registry image without a tag (registry/owner/name)")
    ap.add_argument("dir", choices=["src"], help="the image directory")
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

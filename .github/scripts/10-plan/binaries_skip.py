#!/usr/bin/env python3
"""Decide whether a push to testing needs a build.

Usage: binaries_skip.py <registry-image> <dir>
(dir: src; run in a git checkout; env GITHUB_OUTPUT)

NO build when the CONTENT of the image (ci.inputs: what its Containerfile copies in;
the Justfile, workflows, actions, scripts, tests and docs are not content) at HEAD
equals that at the commit the current `testing` image was built from (its label
org.opencontainers.image.revision) and that image verifies with cosign.pub. Prints
and exports skip=true|false. Absent testing, an unreadable revision, other inputs, or
an image that does not verify (a signature of an old key, a failed signing) all mean
skip=false: build. Only a READ ERROR (registry or cosign could not be asked) fails the
script (exit 1): a build must not be skipped or forced on a guess. The job that calls
this always succeeds, so the required check names always report.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "00-lib"))
import ci  # noqa: E402
import registry  # noqa: E402


def decide(image, directory):
    """Decide whether the build can be skipped; returns (skip, reason).

    `image` is the registry image without a tag, `directory` the image directory. The
    answer is True only when `testing` is a signed build of the same content as HEAD.
    Raises registry.ReadError when the registry or cosign could not be asked (a build
    must not be skipped or forced on a guess); an unsigned or absent testing is
    simply (False, reason).
    """
    # no testing image yet means there is nothing to reuse: build
    try:
        tested = registry.digest(image, "testing")
    except registry.Absent:
        return False, "no testing image yet"

    # the config holds the revision label the content comparison needs
    try:
        config = json.loads(registry.read_by_digest("config", image, tested))
    except (registry.Absent, ValueError) as e:
        raise registry.ReadError(
            f"the config of {image}@{tested} could not be read ({e})"
        )
    rev = ((config.get("config") or {}).get("Labels") or {}).get(
        "org.opencontainers.image.revision"
    ) or ""

    # an image without a revision label cannot be compared: build
    if not re.fullmatch(r"[0-9a-f]{40}", rev):
        return False, "testing has no revision label"

    # a revision that cannot be fetched cannot be compared: build
    if not ci.fetch_commit(rev):
        return False, f"the revision {rev} of testing cannot be fetched"

    # different content means the build is needed
    if ci.content_id(rev, directory) != ci.content_id("HEAD", directory):
        return False, f"the build inputs differ from those of {rev}"

    # same content, but only a testing signed with this key may be reused
    try:
        registry.cosign_check(["--key", "cosign.pub", f"{image}@{tested}"])
    except registry.NotVerified:
        return False, "testing does not verify with cosign.pub"
    return True, f"testing is the signed build of {rev}, same inputs"


def main(argv=None):
    """Decide skip=true|false for the image named on the command line.

    The one stdout line is `skip=true|false` (also written to GITHUB_OUTPUT); the
    reason goes to the log. Returns 0 for either answer and 1 only on a read error.
    """
    ap = ci.arg_parser(__doc__)
    ap.add_argument("image", help="registry image without a tag (registry/owner/name)")
    ap.add_argument("dir", choices=["src"], help="the image directory")
    args = ap.parse_args(argv)

    # a read error must fail the step instead of guessing
    try:
        skip, reason = decide(args.image, args.dir)
    except registry.ReadError as e:
        ci.log.error(f"{e}: not deciding on a guess")
        return 1
    ci.log.info(reason)
    ci.emit("skip", str(skip).lower())
    return 0


# run only as a script, not when a test imports the module
if __name__ == "__main__":
    sys.exit(main())

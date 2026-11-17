#!/usr/bin/env python3
"""Decide whether a push to testing needs a build. Usage: binaries_skip.py <registry-image> <dir>   (dir: src, dx or gx; run in a git checkout; env GITHUB_OUTPUT)

NO build when the registry already knows these inputs (ci.inputs(dir): the image's directory, for dx and gx the shared src/lib and builder, the Justfile, the shared build action,
the image's caller workflow, push_image.py, binaries_layout.py) AND they gave the image `testing` is now: the tag ci.inputs_tag(dir) (the push step makes it) points at the same
digest as `testing`, and that image verifies with cosign.pub. Prints and exports skip=true|false. No testing image, no tag for these inputs, a tag that names another digest (testing
moved on, or the push or signing of these inputs did not finish), or an image that does not verify (a signature of an old key, a failed signing) all mean skip=false: build. Only a
READ ERROR (registry or cosign could not be asked) fails the script (exit 1): a build must not be skipped or forced on a guess. The job that calls this always succeeds, so the
required check names always report.

Why a tag and not the image's revision label: the images carry no per-build label (it would change the digest of identical content), and a tooling-only change that rebuilds to
the same digest still gets its own inputs tag, so the next push of those inputs skips instead of rebuilding forever.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


def decide(image, directory):
    """Returns (skip, reason)."""
    tag = ci.inputs_tag(directory)
    try:
        tested = registry.digest(image, "testing")
    except registry.Absent:
        return False, "no testing image yet"
    try:
        built = registry.digest(image, tag)
    except registry.Absent:
        return False, f"no build of these inputs yet ({tag} does not exist)"
    if built != tested:
        return False, f"testing is not the build of these inputs ({tag} is {built}, testing is {tested})"
    try:
        registry.cosign_check(["--key", "cosign.pub", f"{image}@{tested}"])
    except registry.NotVerified:
        return False, "testing does not verify with cosign.pub"
    return True, f"testing is the signed build of these inputs ({tag})"


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

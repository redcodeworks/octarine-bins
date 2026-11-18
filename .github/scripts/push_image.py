#!/usr/bin/env python3
"""Push the built image under ONE tag and print its manifest digest.

The signature belongs to that digest.
Usage: push_image.py <local-image> <registry-image> <tag>

registry.push writes the digest of the manifest it pushed (retried); the registry must
then show that digest for the tag (registry.settled polls, because a read right after
a write may still answer with the previous manifest). A tag that already exists is
never overwritten (a rerun of the same commit on the same day builds a different
digest, because the stamp carries the time): the existing digest is kept and used,
which is the image that passed the layout check and may already be signed. Other tags
(`testing`, `latest`) are never pushed from here: they are registry-side copies of
this digest made after it was signed. Prints (the one stdout line) `digests=<digest>`
and appends it to $GITHUB_OUTPUT. Tools: podman, skopeo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "00-lib"))
import ci  # noqa: E402
import registry  # noqa: E402


def publish(local_image, image, tag):
    """The digest of image:tag, pushing the local image only when the tag is new.

    An existing tag is never overwritten: its digest is returned (with a warning), as
    that is the image that passed the layout check and may already be signed. A new
    tag gets the push, then the registry is polled until the tag shows the digest.
    Raises ci.NetworkError when the push fails and registry.ReadError when the registry
    cannot be read or never shows the digest.
    """
    # "absent" is the normal case: push; an existing tag is kept as it is
    try:
        digest = registry.digest(image, tag)
    except registry.Absent:
        digest = registry.push(local_image, f"{image}:{tag}")
        registry.settled(image, tag, digest)
        ci.log.info(f"pushed {tag} -> {digest}")
        return digest
    ci.log.warning(
        f"{tag} already exists ({digest}); not overwriting it, the existing image is used"
    )
    return digest


def main(argv=None):
    """Push the image named on the command line under one tag.

    The one stdout line is `digests=<digest>` (also written to GITHUB_OUTPUT).
    Returns 0, or 1 with an error when the push or a registry read fails.
    """
    ap = ci.arg_parser(__doc__)
    ap.add_argument("local_image", help="the locally built image")
    ap.add_argument(
        "registry_image", help="registry image without a tag (registry/owner/name)"
    )
    ap.add_argument("tag", help="the one tag to push")
    args = ap.parse_args(argv)

    # a failed push or read is the step's error; nothing is emitted then
    try:
        digest = publish(args.local_image, args.registry_image, args.tag)
    except (ci.NetworkError, registry.ReadError) as e:
        ci.log.error(str(e))
        return 1
    ci.emit("digests", digest)
    return 0


# run only as a script, not when a test imports the module
if __name__ == "__main__":
    sys.exit(main())

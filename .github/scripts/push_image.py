#!/usr/bin/env python3
"""Push the built image under ONE tag and print its manifest digest, which the signature belongs to. Usage: push_image.py <local-image> <registry-image> <tag>

registry.push writes the digest of the manifest it pushed (retried); the registry must then show that digest for the tag (registry.settled polls, because a read right after a write
may still answer with the previous manifest). A tag that already exists is never overwritten (a rerun of the same commit on the same day builds a different digest, because the stamp carries the time): the
existing digest is kept and used, which is the image that passed the layout check and may already be signed. Other tags (`testing`, `latest`) are never pushed from here: they are registry-side copies of this digest made after it was signed.
Prints (the one stdout line) `digests=<digest>` and appends it to $GITHUB_OUTPUT. Tools: podman, skopeo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


def publish(local_image, image, tag):
    """The digest of image:tag: the existing one when the tag exists (never overwritten), else the image pushed under it."""
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
    ap = ci.arg_parser(__doc__)
    ap.add_argument("local_image", help="the locally built image")
    ap.add_argument(
        "registry_image", help="registry image without a tag (registry/owner/name)"
    )
    ap.add_argument("tag", help="the one tag to push")
    args = ap.parse_args(argv)
    try:
        digest = publish(args.local_image, args.registry_image, args.tag)
    except (ci.NetworkError, registry.ReadError) as e:
        ci.log.error(str(e))
        return 1
    ci.emit("digests", digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())

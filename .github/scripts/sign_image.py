#!/usr/bin/env python3
"""Sign a pushed digest, THEN move `testing` to it.

Nobody ever sees an unsigned `testing`.
Usage: sign_image.py <image> <version> <digest>   (env COSIGN_PRIVATE_KEY)

Signs image@digest (old bundle format), verifies it with cosign.pub, copies the same
digest to image:testing registry-side, reads the tag back until it shows the digest
and verifies through the tag. A failure at any step stops there: the version tag stays
unsigned and `testing` has not moved. Run from the repository root (cosign.pub).
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


def main(argv=None):
    """Sign the pushed digest, then move `testing` to it.

    Takes the registry image (no tag), the version tag (for the summary) and the
    pushed manifest digest. Returns 0 on success; 1, with an error, when the digest
    is not a sha256 digest or any step (sign, verify, copy, read back) fails. A
    failure leaves the version tag unsigned and `testing` where it was.
    """
    ap = ci.arg_parser(__doc__)
    ap.add_argument("image", help="registry image without a tag (registry/owner/name)")
    ap.add_argument("version", help="the version tag, for the summary")
    ap.add_argument("digest", help="the pushed manifest digest to sign")
    args = ap.parse_args(argv)
    image, digest = args.image, args.digest

    # signing something that is not a digest would sign nothing useful
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        ci.log.error("No pushed digest to sign")
        return 1

    # every step is retried inside registry; what still fails here ends the run
    try:
        # one log group around the four steps
        with ci.group("sign, verify, then move testing"):
            registry.sign(f"{image}@{digest}")
            # the signature was written seconds ago: verify it before moving testing
            registry.cosign_check_fresh(["--key", "cosign.pub", f"{image}@{digest}"])
            registry.copy(f"{image}@{digest}", f"{image}:testing")
            registry.settled(image, "testing", digest)
            # the tag was copied seconds ago: cosign may not see it yet even after
            # settled()
            registry.cosign_check_fresh(["--key", "cosign.pub", f"{image}:testing"])
    except (ci.NetworkError, registry.ReadError, registry.NotVerified) as e:
        ci.log.error(str(e))
        return 1

    ci.log.info(f"signed: {image}:{args.version} and :testing = {digest}")
    ci.append_to(
        "GITHUB_STEP_SUMMARY",
        f"Binaries image {image.rsplit('/', 1)[-1]} {args.version}, signed and published as `testing` (digest `{digest}`)",
    )
    return 0


# run only as a script, not when a test imports the module
if __name__ == "__main__":
    sys.exit(main())

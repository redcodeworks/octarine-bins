#!/usr/bin/env python3
"""Sign a pushed digest, THEN move `testing` to it, so nobody ever sees an unsigned `testing`. Usage: sign_image.py <image> <version> <digest>   (env COSIGN_PRIVATE_KEY)

Signs image@digest (old bundle format), verifies it with cosign.pub, copies the same digest to image:testing registry-side, reads the tag back until it shows the digest and
verifies through the tag. A failure at any step stops there: the version tag stays unsigned and `testing` has not moved. Run from the repository root (cosign.pub).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


def main(argv):
    if len(argv) != 3:
        print("usage: sign_image.py <image> <version> <digest>", file=sys.stderr)
        return 2
    image, version, digest = argv
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        ci.error("No pushed digest to sign")
        return 1
    try:
        registry.sign(f"{image}@{digest}")
        registry.cosign_check(["--key", "cosign.pub", f"{image}@{digest}"])
        registry.copy(f"{image}@{digest}", f"{image}:testing")
        registry.settled(image, "testing", digest)
        registry.cosign_check(["--key", "cosign.pub", f"{image}:testing"])
    except (ci.NetworkError, registry.ReadError, registry.NotVerified) as e:
        ci.error(str(e))
        return 1
    print(f"signed: {image}:{version} and :testing = {digest}")
    ci.append_to("GITHUB_STEP_SUMMARY", f"Binaries image {image.rsplit('/', 1)[-1]} {version}, signed and published as `testing` (digest `{digest}`)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

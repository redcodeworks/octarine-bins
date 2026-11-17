#!/usr/bin/env python3
"""Push the built image under ONE tag and print its manifest digest, which the signature belongs to. Usage: push_image.py <local-image> <registry-image> <tag>

registry.push writes the digest of the manifest it pushed (retried); the registry must then show that digest for the tag (registry.settled polls, because a read right after a write
may still answer with the previous manifest). Other tags (`testing`, `latest`) are never pushed from here: they are registry-side copies of this digest made after it was signed.
Prints `digests=<digest>` and appends it to $GITHUB_OUTPUT. Tools: podman, skopeo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


def main(argv):
    if len(argv) != 3:
        print("usage: push_image.py <local-image> <registry-image> <tag>", file=sys.stderr)
        return 2
    local, image, tag = argv
    try:
        digest = registry.push(local, f"{image}:{tag}")
        registry.settled(image, tag, digest)
    except (ci.NetworkError, registry.ReadError) as e:
        ci.error(str(e))
        return 1
    print(f"pushed {tag} -> {digest}")
    ci.emit("digests", digest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

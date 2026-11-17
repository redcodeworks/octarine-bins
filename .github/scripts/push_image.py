#!/usr/bin/env python3
"""Push the built image under ONE tag and print its manifest digest, which the signature belongs to. Usage: push_image.py <local-image> <registry-image> <tag>

`podman push --digestfile` writes the digest of the manifest it pushed (retried: a network call); the registry must then show that digest for the tag
(registry.settled polls, because a read right after a write may still answer with the previous manifest). Other tags (`testing`, `latest`) are never pushed from
here: they are registry-side copies of this digest (skopeo copy --preserve-digests) made after it was signed. Prints `digests=<digest>` and appends it to
$GITHUB_OUTPUT. Tools: podman, skopeo.
"""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


def main(argv):
    if len(argv) != 3:
        print("usage: push_image.py <local-image> <registry-image> <tag>", file=sys.stderr)
        return 2
    local, image, tag = argv
    with tempfile.TemporaryDirectory() as tmp:
        digestfile = Path(tmp) / "digest"
        proc = ci.retry(["podman", "push", f"--digestfile={digestfile}", local, f"{image}:{tag}"])
        if proc.returncode != 0:
            ci.error(f"podman push of {tag} failed (exit {proc.returncode})")
            return proc.returncode
        digest = digestfile.read_text().strip() if digestfile.exists() else ""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        ci.error(f"podman push gave no digest for {tag} ('{digest}')")
        return 1
    try:
        registry.settled(image, tag, digest)
    except registry.ReadError as e:
        ci.error(str(e))
        return 1
    print(f"pushed {tag} -> {digest}")
    ci.emit("digests", digest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

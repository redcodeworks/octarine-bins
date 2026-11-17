#!/usr/bin/env python3
"""Read a tag back after a write until it shows the expected digest. Usage: registry_settled.py <image (registry/owner/name)> <tag> <expected digest>

Env READBACK_TRIES (6) and READBACK_WAIT (seconds apart, 5). Exit 0 as soon as one read shows the digest; exit 1 when after the last read the tag shows another digest
(a wrong copy) or could not be read, naming the last answer (the digest, absent or unreadable).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


def main(argv):
    if len(argv) != 3:
        print("usage: registry_settled.py <image> <tag> <expected digest>", file=sys.stderr)
        return 2
    try:
        registry.settled(*argv)
    except registry.ReadError as e:
        ci.error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

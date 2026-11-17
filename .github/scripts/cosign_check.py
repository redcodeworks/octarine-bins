#!/usr/bin/env python3
"""`cosign verify` that tells a transient failure from a real one. Usage: cosign_check.py <cosign verify arguments...>

Exit status: 0 verified; 5 NOT VERIFIED (cosign said so explicitly, nothing retried); 4 READ ERROR (network, registry, timeout: retried, still failing after the last
try). Retries follow RETRY_TRIES (3) and RETRY_DELAY (5). cosign's own message goes to stderr; its stdout is discarded.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import registry  # noqa: E402


def main(argv):
    if not argv:
        print("usage: cosign_check.py <cosign verify arguments...>", file=sys.stderr)
        return 2
    try:
        registry.cosign_check(argv)
    except registry.NotVerified as e:
        print(f"cosign-check: {e}", file=sys.stderr)
        return 5
    except registry.ReadError as e:
        ci.error(f"cosign-check: {e}")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

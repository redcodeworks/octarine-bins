#!/usr/bin/env python3
"""Run a command up to N times with a growing pause: the retry helper of ci.py for workflow steps (cosign sign, skopeo copy). Usage: retry.py [-n TRIES] [-d DELAY] [--] command [args...]

Defaults RETRY_TRIES=3 and RETRY_DELAY=5 seconds (the pause before try k+1 is DELAY*k). stdout and stderr of the command pass through; exit status 0 when a try
succeeded, else that of the last try. Not for commands that must not run twice.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser(prog="retry.py", usage="retry.py [-n TRIES] [-d DELAY] [--] command [args...]")
    ap.add_argument("-n", type=int, dest="tries")
    ap.add_argument("-d", type=int, dest="delay")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not cmd or (args.tries is not None and args.tries < 1):
        ap.error("a command and TRIES >= 1 are needed")
    proc = ci.retry(cmd, tries=args.tries, delay=args.delay)
    if proc.returncode != 0:
        n = args.tries or ci.int_env("RETRY_TRIES", 3)
        ci.error(f"{' '.join(cmd[:2])} failed {n} times (last exit {proc.returncode}); a transient network error would have passed by now, so treat this as a "
                 "real failure (bad signature, missing package or tag, or a longer outage)")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

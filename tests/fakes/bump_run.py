#!/usr/bin/env python3
"""Stub of bump_run.py for the weekly test.

Moves its pin in pins.txt to $UPSTREAM_<PIN> (fail = a failing check) in one commit
that ends with the table row.
"""

import os
import re
import subprocess
import sys

pin = sys.argv[1]
text = open("pins.txt").read()
current = re.search(rf"^{pin}=(.*)$", text, re.M).group(1)

# unset: upstream equals the pin
new = os.environ.get(f"UPSTREAM_{pin.upper()}", current)

# the test asks for a failing check: an error annotation and a nonzero exit
if new == "fail":
    print(f"::error::{pin}: cannot read upstream")
    sys.exit(1)

# a newer value: write it into pins.txt and commit it with the table row
if new != current:
    open("pins.txt", "w").write(
        re.sub(rf"^{pin}=.*$", f"{pin}={new}", text, flags=re.M)
    )
    subprocess.run(
        [
            "git",
            "commit",
            "-q",
            "-a",
            "-m",
            f"Bump {pin} to {new}\n\n{pin} | {current} | {new} | upstream | 3",
        ],
        check=True,
    )

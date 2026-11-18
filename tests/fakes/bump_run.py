#!/usr/bin/env python3
"""Stub of bump_run.py for the weekly test: moves its pin in pins.txt to $UPSTREAM_<PIN> (fail = a failing check) in one commit that ends with the table row."""

import os
import re
import subprocess
import sys

pin = sys.argv[1]
text = open("pins.txt").read()
current = re.search(rf"^{pin}=(.*)$", text, re.M).group(1)
new = os.environ.get(
    f"UPSTREAM_{pin.upper()}", current
)  # unset: upstream equals the pin
if new == "fail":
    print(f"::error::{pin}: cannot read upstream")
    sys.exit(1)
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

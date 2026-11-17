#!/usr/bin/env python3
"""Stub of bump_checks.py for the weekly test: records its arguments (branch and commit) in $DB/checks."""
import os
import sys

with open(os.environ["DB"] + "/checks", "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")

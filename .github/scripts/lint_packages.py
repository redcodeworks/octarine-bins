#!/usr/bin/env python3
"""Lint the package lists of ONE image (<dir>/packages/*.txt): the rules installer/install.sh relies on. Usage: lint_packages.py <list>...   (the lists of one image together)

File names: {required,optional}-{fedora,negativo17,copr}.txt, nothing else. Every list: at least one entry, one token per line, no RPM Fusion. *-copr.txt: owner/project:name-version-release.
The others: `name`, `name.arch`, `@group` or `a|b` alternatives, and every `name.i686` entry needs `name.x86_64` listed explicitly in the same file (a bare name next to name.i686
installs only the i686 package on a host). No entry twice, in one list or across the image's lists (a package in both tiers would be installed strictly AND reported as optional).
Prints every problem on stderr and exits 1 if there is one.
"""
import re
import sys
from collections import Counter
from pathlib import Path

NAME = re.compile(r"(required|optional)-(fedora|negativo17|copr)\.txt")
COPR = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:[A-Za-z0-9_.+]+(-[A-Za-z0-9_.+]+)*-[0-9][A-Za-z0-9_.+~^]*-[0-9][A-Za-z0-9_.+~^]*")
PLAIN = re.compile(r"@[A-Za-z0-9._+-]+|[A-Za-z0-9._+-]+(\|[A-Za-z0-9._+-]+)*")


def entries_of(path):
    """The entries of a list: comments and blank lines removed."""
    lines = (re.sub(r"\s*#.*$", "", line) for line in Path(path).read_text().splitlines())
    return [line for line in lines if line.strip()]


def lint(lists):
    """Problems of one image's lists (paths), as messages."""
    problems = []
    seen = []
    for path in map(Path, lists):
        if not path.is_file():
            problems.append(f"{path}: not a file")
            continue
        if not NAME.fullmatch(path.name):
            problems.append(f"{path}: the name must be {{required,optional}}-{{fedora,negativo17,copr}}.txt")
            continue
        entries = entries_of(path)
        if not entries:
            problems.append(f"{path}: no packages (delete an empty list)")
            continue
        spaced = [f"{n}:{e}" for n, e in enumerate(entries, 1) if re.search(r"\s", e)]
        if spaced:
            problems.append(f"{path}: more than one token per line: {' '.join(spaced)}")
        if path.name.endswith("-copr.txt"):
            bad = [e for e in entries if not COPR.fullmatch(e)]
            if bad:
                problems.append(f"{path}: not owner/project:name-version-release: {' '.join(bad)}")
        else:
            bad = [e for e in entries if not PLAIN.fullmatch(e)]
            if bad:
                problems.append(f"{path}: not 'name', '@group' or 'a|b' alternatives: {' '.join(bad)}")
            for e in entries:
                if e.endswith(".i686") and f"{e[:-5]}.x86_64" not in entries:
                    problems.append(f"{path}: {e} needs {e[:-5]}.x86_64 listed explicitly")
        if re.search(r"rpmfusion|rpm fusion", "\n".join(entries), re.I):
            problems.append(f"{path}: RPM Fusion is not used (negativo17 and COPR only)")
        seen += entries
    dups = sorted(e for e, n in Counter(seen).items() if n > 1)
    if dups:
        problems.append(f"duplicates: {' '.join(dups)}")
    return problems


def main(argv):
    if not argv:
        print("usage: lint_packages.py <list>...", file=sys.stderr)
        return 2
    problems = lint(argv)
    for p in problems:
        print(f"lint-packages: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

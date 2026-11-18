#!/usr/bin/env python3
"""Check a built binaries image against the layout octarine-os reads (its build_files/install-binaries.sh). Usage: binaries_layout.py <local image> <dir>   (dir: src)

Exports the image with podman (env PODMAN, default `podman`; for example `podman --root R --runroot X --storage-driver overlay`), lists its files and calls check().
The components come from <dir>/components.tsv, the schedulers from src/scx/schedulers.tsv; at least one component is required. Per component: scx needs scx/bin/<name> for every scheduler of the manifest and scx/schedulers.tsv; extest both libraries; ghostty its
binary, desktop file and FILES; every component needs <c>/SOURCE, <c>/BUILDINFO and <c>/LICENSES/*. The assembled image also needs the top-level BUILDINFO and COMPONENTS (with
components: SOURCE and LICENSES/ too) and holds no other top-level entry than its components, those files. Nothing else is judged here (the build already ran
the smoke tests and the ELF checks). Exit 0 ok, 1 problems (each on stderr as ::error::).
"""

import os
import shlex
import subprocess
import sys
import tarfile
from itertools import chain
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402


def table_names(path):
    """First column of a tab separated list; comments and blank lines skipped."""
    return [line.split("\t")[0] for line in Path(path).read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]


def required_files(component, schedulers):
    """The files one component must have in the image (the part of the layout that is specific to it)."""
    match component:
        case "scx":
            if not schedulers:
                raise ValueError("no scheduler in the manifest")
            yield from (f"scx/bin/{name}" for name in schedulers)
            yield "scx/schedulers.tsv"
        case "extest":
            yield from ("extest/lib/libextest.so", "extest/lib64/libextest.so")
        case "ghostty":
            yield from ("ghostty/root/usr/bin/ghostty", "ghostty/root/usr/share/applications/com.mitchellh.ghostty.desktop", "ghostty/FILES")
        case _:
            raise ValueError(f"unknown component '{component}'")
    yield from (f"{component}/SOURCE", f"{component}/BUILDINFO")


def check(listing, schedulers, components):
    """Problems of an assembled image: `listing` is the names in it, `schedulers` the scheduler names of the manifest. Returns a list of messages."""
    if not components:
        raise ValueError("name at least one component")
    files = {name.removeprefix("./") for name in listing}

    def has_files_under(prefix):
        return any(f.startswith(prefix) and len(f) > len(prefix) for f in files)

    needed = [*chain.from_iterable(required_files(c, schedulers) for c in components), "BUILDINFO", "COMPONENTS", "SOURCE"]
    problems = [f"the binaries image lacks {name}" for name in needed if name not in files]
    problems += [f"the binaries image has no {c}/LICENSES" for c in components if not has_files_under(f"{c}/LICENSES/")]
    if not has_files_under("LICENSES/"):
        problems.append("the binaries image has no top-level LICENSES")
    allowed = {"SOURCE", "BUILDINFO", "COMPONENTS", "LICENSES", *components}
    if extra := sorted({f.split("/")[0] for f in files} - allowed - {""}):
        problems.append(f"the binaries image holds unexpected top-level entries: {' '.join(extra)}")
    return problems


def export_listing(image):
    """The names in the image, from `podman export`."""
    podman = shlex.split(os.environ.get("PODMAN", "podman"))
    cid = subprocess.run([*podman, "create", image, "/nonexistent"], capture_output=True, text=True, check=True).stdout.strip()
    try:
        with subprocess.Popen([*podman, "export", cid], stdout=subprocess.PIPE) as proc:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                return [m.name for m in tar]
    finally:
        subprocess.run([*podman, "rm", cid], capture_output=True)


def main(argv=None):
    ap = ci.arg_parser(__doc__)
    ap.add_argument("image", help="the locally built image")
    ap.add_argument("dir", choices=["src"], help="the image directory (its components are the expectation)")
    args = ap.parse_args(argv)
    directory = args.dir
    components = table_names(f"{directory}/components.tsv")
    try:
        problems = check(export_listing(args.image), table_names("src/scx/schedulers.tsv"), components)
    except (ValueError, subprocess.CalledProcessError) as e:
        ci.log.error(str(e))
        return 1
    for p in problems:
        ci.log.error(p)
    if problems:
        return 1
    ci.log.info(f"binaries image layout ok: {' '.join(components)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
